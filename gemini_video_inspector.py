import base64
import io
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image
from .utils.network_retry import retry_on_network_error
from .utils.video_processor import VideoProcessor
from .telegram_bot.telegram_sender import TelegramSender
from .telegram_bot.review_manager import ReviewManager


def _debug(message):
    """Print a flushed diagnostic line without exposing credentials."""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[GeminiVideoInspector {timestamp}] {message}", flush=True)


def _safe_endpoint(url):
    """Return an endpoint description without query parameters such as API keys."""
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


_NETWORK_MAX_ATTEMPTS = 4
_NETWORK_RETRY_DELAY = 5
_RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class TelegramFallbackTriggered(Exception):
    """当 Gemini 接口失败后成功发送到 Telegram 人工审核时抛出"""
    pass


def _is_retryable_network_error(error):
    """Return whether an error is likely caused by a temporary network problem."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in _RETRYABLE_HTTP_STATUS_CODES
    if isinstance(error, (urllib.error.URLError, TimeoutError, TimeoutError)):
        return True
    if isinstance(
        error,
        (ConnectionError, ConnectionResetError, ConnectionAbortedError,
         BrokenPipeError, ssl.SSLError),
    ):
        return True
    return False


def _try_telegram_fallback(error_message: str, fallback_info: dict) -> bool:
    """尝试将失败的视频审核任务发送到 Telegram 人工审核

    Args:
        error_message: 错误消息
        fallback_info: Dict with keys {video_file, system_prompt, user_prompt, task_id}

    Returns:
        是否成功发送到 Telegram
    """
    try:
        sender = TelegramSender()
        task_id = fallback_info.get("task_id") or str(uuid.uuid4())

        result = sender.send_video_for_review(
            task_id=task_id,
            video_file=fallback_info.get("video_file", ""),
            system_prompt=fallback_info.get("system_prompt", ""),
            user_prompt=fallback_info.get("user_prompt", ""),
            error_message=error_message,
        )

        if result:
            _debug(f"已成功发送到 Telegram 人工审核，任务 ID: {task_id}")
            return True
        else:
            _debug("发送到 Telegram 失败")
            return False
    except Exception as e:
        _debug(f"尝试 Telegram 回退异常: {e}")
        return False


def _run_with_network_retry(operation_name, operation, max_attempts=_NETWORK_MAX_ATTEMPTS,
                            retry_delay=_NETWORK_RETRY_DELAY,
                            telegram_fallback_info=None):
    """Run a network operation with bounded retries and visible diagnostics.

    Args:
        operation_name: Name of the operation for logging
        operation: Callable that performs the operation
        max_attempts: Maximum number of retry attempts
        retry_delay: Initial delay between retries (exponential backoff)
        telegram_fallback_info: Dict with keys {video_file, system_prompt, user_prompt, task_id}
                               If provided, sends to Telegram on final failure

    Returns:
        Operation result or raises TelegramFallbackTriggered on Telegram send

    Raises:
        TelegramFallbackTriggered: When max retries exceeded and Telegram sent successfully
        Original exception: When max retries exceeded and Telegram not configured or failed
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as error:
            if not _is_retryable_network_error(error) or attempt >= max_attempts:
                _debug(
                    f"{operation_name} failed permanently after {attempt}/{max_attempts}: "
                    f"{type(error).__name__}: {error}"
                )

                # 达到最大重试次数后，尝试通过 Telegram 发送人工审核
                if attempt >= max_attempts and telegram_fallback_info:
                    _debug(f"{operation_name} 达到最大重试次数，尝试发送到 Telegram 人工审核")
                    error_message = f"{type(error).__name__}: {str(error)[:200]}"
                    if _try_telegram_fallback(error_message, telegram_fallback_info):
                        raise TelegramFallbackTriggered(
                            f"已发送至 Telegram 人工审核，请等待审核结果。任务 ID: {telegram_fallback_info.get('task_id')}"
                        )

                raise

            delay = min(retry_delay * (2 ** (attempt - 1)), 30)
            _debug(
                f"{operation_name} network error on attempt {attempt}/{max_attempts}: "
                f"{type(error).__name__}: {error}; retrying in {delay}s"
            )
            time.sleep(delay)


@retry_on_network_error(max_retries=4, retry_delay=60)
def _post_json(url, headers, payload, timeout, telegram_fallback_info=None):
    _debug(
        f"HTTP POST start: endpoint={_safe_endpoint(url)}, "
        f"timeout={timeout}s, payload_bytes={len(json.dumps(payload).encode('utf-8'))}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            _debug(
                f"HTTP POST complete: endpoint={_safe_endpoint(url)}, "
                f"status={response.status}, response_bytes={len(response_body.encode('utf-8'))}"
            )
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(response_body)
        except json.JSONDecodeError:
            details = response_body
        raise RuntimeError(f"API request failed ({error.code}): {details}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"API request failed: {error.reason}") from error

    try:
        return json.loads(response_body)
    except json.JSONDecodeError as error:
        raise RuntimeError("API returned invalid JSON") from error


def _image_parts(images):
    if images is None:
        return []

    parts = []
    for image in images:
        if hasattr(image, "detach"):
            image = image.detach().cpu()
        if hasattr(image, "numpy"):
            image = image.numpy()
        if not hasattr(image, "dtype"):
            import numpy as np
            image = np.asarray(image)

        import numpy as np
        if image.ndim == 2:
            image = np.repeat(image[:, :, None], 3, axis=2)
        elif (
            image.ndim == 3
            and image.shape[0] in (1, 3, 4)
            and image.shape[-1] not in (1, 3, 4)
        ):
            image = image.transpose(1, 2, 0)

        if image.ndim != 3:
            raise ValueError(f"Unsupported image shape: {image.shape}")
        if image.shape[-1] == 1:
            image = np.repeat(image, 3, axis=2)
        elif image.shape[-1] == 4:
            image = image[:, :, :3]
        elif image.shape[-1] != 3:
            raise ValueError(f"Unsupported image shape: {image.shape}")

        if np.issubdtype(image.dtype, np.floating):
            image = np.clip(image, 0, 1) * 255
        image = np.clip(image, 0, 255).astype(np.uint8)
        png = io.BytesIO()
        Image.fromarray(image, mode="RGB").save(png, format="PNG")
        parts.append(base64.b64encode(png.getvalue()).decode("ascii"))
    return parts


def _gemini_url(base_url, model, api_key):
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith(":generateContent"):
        url = base_url
    else:
        api_root = _gemini_api_root(base_url)
        url = f"{api_root}/models/{urllib.parse.quote(model, safe='')}:generateContent"

    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urllib.parse.urlencode({'key': api_key})}"


def _gemini_api_root(base_url):
    """Return the REST API root used by Gemini generateContent and Files API."""
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/v1beta") or base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1beta"


def _gemini_upload_url(base_url, api_key):
    """Build the upload endpoint matching the configured Gemini API root."""
    api_root = _gemini_api_root(base_url)
    parsed = urllib.parse.urlsplit(api_root)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1beta"):
        path = f"{path[:-len('/v1beta')]}/upload/v1beta"
    elif path.endswith("/v1"):
        path = f"{path[:-len('/v1')]}/upload/v1"
    else:
        path = f"{path}/upload/v1beta"

    upload_base = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)
    )
    separator = "&" if "?" in upload_base else "?"
    return f"{upload_base}/files{separator}{urllib.parse.urlencode({'key': api_key})}"


def _read_json_response(response):
    body = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"API returned invalid JSON: {body[:500]}") from error


def _get_json(url, timeout):
    _debug(f"HTTP GET start: endpoint={_safe_endpoint(url)}, timeout={timeout}s")
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = _read_json_response(response)
            _debug(f"HTTP GET complete: endpoint={_safe_endpoint(url)}, status={response.status}")
            return result
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"API request failed ({error.code}): {response_body[:500]}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"API request failed: {error.reason}") from error


def _gemini_text(response):
    candidates = response.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini response did not contain candidates: {response}")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts)
    if not text:
        raise RuntimeError(f"Gemini response did not contain text: {response}")
    return text


class GeminiVideoInspector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "user_prompt": ("STRING", {"multiline": True, "default": ""}),
                "evaluation_criteria": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "检查动作是否连贯、画面有无畸变、主体是否符合提示词",
                    },
                ),
                "provider": (
                    ["gemini", "openai", "claude"],
                    {"default": "gemini"},
                ),
                "api_key": ("STRING", {"default": ""}),
                "model_name": (
                    "STRING",
                    {"default": "gemini-2.0-flash-exp"},
                ),
                "base_url": (
                    "STRING",
                    {"default": "https://generativelanguage.googleapis.com"},
                ),
                "temperature": (
                    "FLOAT",
                    {"default": 0.2, "min": 0.0, "max": 2.0, "step": 0.01},
                ),
                "max_tokens": ("INT", {"default": 1024, "min": 1, "max": 65536}),
                "pass_threshold": (
                    "FLOAT",
                    {"default": 8.0, "min": 0.0, "max": 10.0, "step": 0.1},
                ),
                "timeout": ("INT", {"default": 120, "min": 1, "max": 600}),
            },
            "optional": {
                "images": ("IMAGE",),
                "video": ("VIDEO",),
                "frame_count": ("INT", {"forceInput": True}),
                "select_every_nth": ("INT", {"default": 1, "min": 1}),
                "prev_score": ("FLOAT",),
                "prev_feedback": ("STRING", {"forceInput": True}),
                "prev_h3_optimize_prompt": ("STRING", {"forceInput": True}),
                "prev_distilled_meta_prompt": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("BOOLEAN", "FLOAT", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "passed",
        "score",
        "feedback",
        "log_text",
        "h3_optimize_prompt",
        "distilled_meta_prompt",
    )
    FUNCTION = "inspect"
    CATEGORY = "my_comfyui/AI"

    def inspect(
        self,
        user_prompt,
        evaluation_criteria,
        provider,
        api_key,
        model_name,
        base_url,
        temperature,
        max_tokens,
        pass_threshold,
        timeout,
        images=None,
        video=None,
        frame_count=None,
        select_every_nth=1,
        prev_score=None,
        prev_feedback=None,
        prev_h3_optimize_prompt=None,
        prev_distilled_meta_prompt=None,
    ):
        _debug(
            f"inspect start: provider={provider}, model={model_name}, "
            f"timeout={timeout}s, has_video={video is not None}, has_images={images is not None}"
        )
        api_key = api_key.strip()
        model_name = model_name.strip()
        if not api_key:
            raise ValueError("api_key cannot be empty")
        if not model_name:
            raise ValueError("model_name cannot be empty")
        if not user_prompt.strip():
            raise ValueError("user_prompt cannot be empty")
        if not evaluation_criteria.strip():
            raise ValueError("evaluation_criteria cannot be empty")

        # 处理视频或图像输入
        video_file = None
        video_file_is_temporary = False
        audio_data = None

        if video is not None:
            # 视频输入。对于已有文件，直接上传原文件，避免重复编码和整段解码。
            if isinstance(video, (str, os.PathLike)):
                video = os.fspath(video)
                if os.path.isfile(video):
                    selected_images = None
                    video_file = video
                    actual_frame_count = VideoProcessor.get_frame_count(video) or None
                    _debug(
                        f"input video path accepted: path={video}, "
                        f"size={os.path.getsize(video)} bytes, frame_count={actual_frame_count}"
                    )
                else:
                    raise ValueError(f"Video file not found: {video}")
            elif isinstance(video, dict):
                # 如果是字典格式，获取帧数据和音频
                selected_images = video.get("frames")
                if selected_images is None:
                    selected_images = video.get("images")
                if selected_images is None:
                    raise ValueError("Video dictionary must contain 'frames' or 'images'")
                audio_data = video.get("audio")
                _debug("input video dict received; converting frames to temporary MP4")
                video_file = VideoProcessor.frames_to_video(selected_images, audio_data=audio_data)
                video_file_is_temporary = True
            elif hasattr(video, "get_components"):
                # VideoFromComponents 对象：调用 get_components() 获取 VideoComponents
                components = video.get_components()
                selected_images = components.images
                audio_data = components.audio
                _debug(
                    f"VideoFromComponents received: frames_shape={getattr(selected_images, 'shape', None)}, "
                    f"has_audio={audio_data is not None}"
                )
                video_file = VideoProcessor.frames_to_video(selected_images, audio_data=audio_data)
                video_file_is_temporary = True
            elif hasattr(video, "frames"):
                # 如果有 frames 属性，使用它
                selected_images = video.frames
                audio_data = getattr(video, "audio", None)
                _debug("video object with frames attribute received; converting to temporary MP4")
                video_file = VideoProcessor.frames_to_video(selected_images, audio_data=audio_data)
                video_file_is_temporary = True
            else:
                # 假设是帧数据
                selected_images = video
                audio_data = None
                _debug("raw frame input received; converting to temporary MP4")
                video_file = VideoProcessor.frames_to_video(selected_images, audio_data=audio_data)
                video_file_is_temporary = True

            # 计算实际帧数
            if selected_images is None:
                actual_frame_count = actual_frame_count or None
            elif isinstance(selected_images, (list, tuple)):
                actual_frame_count = len(selected_images)
            elif hasattr(selected_images, "__len__"):
                actual_frame_count = len(selected_images)
            else:
                actual_frame_count = selected_images.shape[0] if hasattr(selected_images, "shape") else 1

            actual_select_every_nth = 1
            _debug(
                f"video input ready: file={video_file}, temporary={video_file_is_temporary}, "
                f"frame_count={actual_frame_count}"
            )

        elif images is not None:
            # 图像输入：使用原有逻辑
            actual_frame_count = frame_count if frame_count is not None else len(images)
            actual_select_every_nth = select_every_nth

            # 根据 select_every_nth 筛选图片
            if select_every_nth > 1:
                selected_images = images[::select_every_nth]
            else:
                selected_images = images
            _debug(
                f"image input ready: original_count={actual_frame_count}, "
                f"selected_count={len(selected_images)}, every_nth={select_every_nth}"
            )
        else:
            raise ValueError("images 或 video 必须提供其中之一")

        # Gemini 可以直接理解视频文件；其它提供商则退化为逐帧图片输入。
        if video_file and provider != "gemini":
            if selected_images is None:
                selected_images = VideoProcessor.extract_frames_from_video(video_file)
            image_data = _image_parts(selected_images)
        elif video_file:
            # 视频模式：已创建视频文件，稍后用文件上传
            image_data = None
        else:
            # 图像模式：转换为 base64
            image_data = _image_parts(selected_images)
            if not image_data:
                raise ValueError("images cannot be empty")

        system_prompt = self._build_system_prompt(
            user_prompt,
            evaluation_criteria,
            len(image_data) if image_data else actual_frame_count,
            actual_frame_count,
            actual_select_every_nth,
            prev_score,
            prev_feedback,
            prev_h3_optimize_prompt,
            prev_distilled_meta_prompt,
        )
        _debug(
            f"prompt ready: prompt_chars={len(system_prompt)}, "
            f"image_parts={len(image_data) if image_data else 0}, video_file={video_file is not None}"
        )

        try:
            # 准备 Telegram 回退信息（用于网络错误时发送）
            task_id = str(uuid.uuid4())[:8]
            telegram_fallback_info = {
                "task_id": task_id,
                "video_file": video_file or "",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }

            try:
                if video_file:
                    if provider != "gemini":
                        _debug(f"calling {provider} with extracted image parts")
                        response_text = self._call_provider(
                            provider, base_url, model_name, api_key, system_prompt,
                            image_data, temperature, max_tokens, timeout,
                            telegram_fallback_info=telegram_fallback_info,
                        )
                    else:
                        _debug("calling Gemini video path")
                        response_text = self._call_gemini_video(
                            base_url, model_name, api_key, system_prompt, video_file,
                            temperature, max_tokens, timeout,
                            telegram_fallback_info=telegram_fallback_info,
                        )
                else:
                    # 图像模式：使用原有逻辑
                    _debug(f"calling {provider} image path")
                    response_text = self._call_provider(
                        provider, base_url, model_name, api_key, system_prompt,
                        image_data, temperature, max_tokens, timeout,
                        telegram_fallback_info=telegram_fallback_info,
                    )
            except TelegramFallbackTriggered as tg_error:
                # Telegram 人工审核已触发，返回待审核状态
                _debug(f"Telegram 人工审核已发送: {tg_error}")
                return (
                    False,  # passed
                    0.0,  # score
                    str(tg_error),  # feedback
                    f"已发送至 Telegram 人工审核。任务 ID: {task_id}",  # log_text
                    "",  # h3_optimize_prompt
                    "",  # distilled_meta_prompt
                )

            result = self._parse_response(response_text, pass_threshold)
            _debug(f"model response parsed: score={result['score']}, passed={result['passed']}")

            # 智能回退逻辑：当评分下降但并非大幅下降时，保留新的 h3_optimize_prompt 以继续探索
            # 只有在评分显著下降（>1.5分）时才回退到旧的 system prompt
            if (
                prev_score is not None
                and prev_h3_optimize_prompt
                and prev_h3_optimize_prompt.strip()
            ):
                score_drop = prev_score - result["score"]
                if score_drop > 1.5:
                    # 评分显著下降，回退到上一轮的 system prompt
                    result["h3_optimize_prompt"] = prev_h3_optimize_prompt
                    result["feedback"] += f"\n\n[系统提示] 本轮评分下降 {score_drop:.2f} 分，已回退到上一轮的优化策略。"
                elif score_drop > 0:
                    # 评分轻微下降，在 feedback 中提示但保留新的探索方向
                    result["feedback"] += f"\n\n[系统提示] 本轮评分下降 {score_drop:.2f} 分，但保留新的优化策略继续探索。如果下一轮继续下降，建议回顾历史策略。"

            # distilled_meta_prompt 回退逻辑：只在评分显著上升时更新
            if prev_distilled_meta_prompt and prev_distilled_meta_prompt.strip():
                if prev_score is None or result["score"] >= prev_score + 0.5:
                    # 首次运行或评分显著提升，使用新的 distilled_meta_prompt
                    pass
                else:
                    # 评分未显著提升，保留旧的 distilled_meta_prompt
                    result["distilled_meta_prompt"] = prev_distilled_meta_prompt

            log_text = self._format_log(
                system_prompt, response_text, result, pass_threshold
            )

            return (
                result["passed"],
                result["score"],
                result["feedback"],
                log_text,
                result["h3_optimize_prompt"],
                result["distilled_meta_prompt"],
            )
        finally:
            # 只清理本节点创建的临时视频，不能删除用户传入的原视频。
            if video_file and video_file_is_temporary:
                _debug(f"cleaning temporary video: {video_file}")
                VideoProcessor.cleanup(video_file)

    @staticmethod
    def _call_provider(
        provider, base_url, model_name, api_key, system_prompt, image_data,
        temperature, max_tokens, timeout,
        telegram_fallback_info=None,
    ):
        _debug(f"provider dispatch: provider={provider}, image_parts={len(image_data or [])}")
        if provider == "gemini":
            return GeminiVideoInspector._call_gemini(
                base_url, model_name, api_key, system_prompt, image_data,
                temperature, max_tokens, timeout,
                telegram_fallback_info=telegram_fallback_info,
            )
        if provider == "openai":
            return GeminiVideoInspector._call_openai(
                base_url, model_name, api_key, system_prompt, image_data,
                temperature, max_tokens, timeout,
                telegram_fallback_info=telegram_fallback_info,
            )
        if provider == "claude":
            return GeminiVideoInspector._call_claude(
                base_url, model_name, api_key, system_prompt, image_data,
                temperature, max_tokens, timeout,
                telegram_fallback_info=telegram_fallback_info,
            )
        raise ValueError(f"Unsupported provider: {provider}")

    @staticmethod
    def _call_gemini_video(base_url, model_name, api_key, system_prompt, video_path,
                           temperature, max_tokens, timeout,
                           telegram_fallback_info=None):
        """Upload a video through Gemini's resumable Files API and inspect it."""
        _debug(f"video upload: reading file={video_path}")
        with open(video_path, "rb") as f:
            video_data = f.read()
        _debug(f"video upload: file read complete, bytes={len(video_data)}")

        mime_type = "video/mp4"
        upload_url = _gemini_upload_url(base_url, api_key)
        start_headers = {
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(video_data)),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        }
        start_payload = json.dumps({
            "file": {"display_name": os.path.basename(video_path) or "video.mp4"}
        }).encode("utf-8")

        try:
            upload_result = _run_with_network_retry(
                "Gemini video upload",
                lambda: GeminiVideoInspector._upload_video_once(
                    upload_url,
                    start_headers,
                    start_payload,
                    video_data,
                    mime_type,
                    timeout,
                ),
                telegram_fallback_info=telegram_fallback_info,
            )
        except urllib.error.HTTPError as error:
            response_body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Video upload failed ({error.code}): {response_body[:500]}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Video upload failed: {error.reason}") from error

        file_info = upload_result.get("file") or {}
        file_name = file_info.get("name")
        _debug(
            f"video upload: file created, name={file_name}, "
            f"state={file_info.get('state')}, has_uri={bool(file_info.get('uri'))}"
        )
        if not file_name:
            raise RuntimeError(f"Upload response missing file name: {upload_result}")

        file_info = GeminiVideoInspector._wait_for_gemini_file(
            base_url, api_key, file_name, file_info, timeout
        )
        file_uri = file_info.get("uri")
        if not file_uri:
            raise RuntimeError(f"Gemini file response missing URI: {file_info}")

        parts = [
            {"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}},
            {"text": system_prompt}
        ]

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }

        _debug(
            f"Gemini generateContent start: model={model_name}, "
            f"video_uri_present={bool(file_uri)}, prompt_chars={len(system_prompt)}, "
            f"timeout={timeout}s"
        )
        response = _post_json(_gemini_url(base_url, model_name, api_key), {}, payload, timeout)
        text = _gemini_text(response)
        _debug(f"Gemini generateContent complete: response_text_chars={len(text)}")
        return text

    @staticmethod
    def _upload_video_once(upload_url, start_headers, start_payload, video_data,
                           mime_type, timeout):
        """Perform one complete resumable upload attempt."""
        _debug(f"video upload: start request -> {_safe_endpoint(upload_url)}")
        start_request = urllib.request.Request(
            upload_url, data=start_payload, headers=start_headers, method="POST"
        )
        try:
            with urllib.request.urlopen(start_request, timeout=timeout) as response:
                session_url = response.headers.get("X-Goog-Upload-URL")
                _debug(
                    f"video upload: start response status={response.status}, "
                    f"session_url_received={bool(session_url)}"
                )
                if not session_url:
                    raise RuntimeError(
                        "Gemini upload did not return X-Goog-Upload-URL"
                    )

            upload_request = urllib.request.Request(
                session_url,
                data=video_data,
                headers={
                    "Content-Length": str(len(video_data)),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                    "Content-Type": mime_type,
                },
                method="POST",
            )
            _debug(f"video upload: uploading {len(video_data)} bytes to upload session")
            with urllib.request.urlopen(upload_request, timeout=timeout) as response:
                upload_result = _read_json_response(response)
                _debug(f"video upload: finalize complete, status={response.status}")
                return upload_result
        except Exception as e:
            _debug(f"video upload failed: {type(e).__name__}: {e}")
            raise

    @staticmethod
    def _wait_for_gemini_file(base_url, api_key, file_name, file_info, timeout):
        """Wait until an uploaded video is ACTIVE before calling generateContent."""
        state = file_info.get("state")
        if state in (None, "ACTIVE"):
            _debug(f"Gemini file ready immediately: name={file_name}, state={state or 'unknown'}")
            return file_info

        _debug(f"Gemini file processing wait: name={file_name}, initial_state={state}")

        deadline = time.monotonic() + timeout
        file_url = (
            f"{_gemini_api_root(base_url)}/{file_name.lstrip('/')}"
            f"?{urllib.parse.urlencode({'key': api_key})}"
        )
        while time.monotonic() < deadline:
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
            file_info = _get_json(file_url, timeout)
            state = file_info.get("state")
            _debug(f"Gemini file processing state: name={file_name}, state={state}")
            if state == "ACTIVE":
                _debug(f"Gemini file processing complete: name={file_name}")
                return file_info
            if state == "FAILED":
                raise RuntimeError(f"Gemini video processing failed: {file_info}")

        raise TimeoutError(
            f"Timed out waiting for Gemini to process video file {file_name}"
        )

    @staticmethod
    def _call_gemini(base_url, model_name, api_key, system_prompt, image_data,
                     temperature, max_tokens, timeout,
                     telegram_fallback_info=None):
        parts = []
        parts.extend(
            {"inline_data": {"mime_type": "image/png", "data": image}}
            for image in image_data
        )
        parts.append({"text": system_prompt})

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }

        response = _post_json(
            _gemini_url(base_url, model_name, api_key),
            {},
            payload,
            timeout,
            telegram_fallback_info=telegram_fallback_info,
        )
        return _gemini_text(response)

    @staticmethod
    def _call_openai(base_url, model_name, api_key, system_prompt, image_data,
                     temperature, max_tokens, timeout,
                     telegram_fallback_info=None):
        base_url = base_url.strip().rstrip("/")
        if not base_url:
            base_url = "https://api.openai.com/v1"

        url = f"{base_url}/chat/completions"

        content = [{"type": "text", "text": system_prompt}]
        for image in image_data:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image}"}
            })

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}
        }

        headers = {"Authorization": f"Bearer {api_key}"}
        response = _post_json(
            url, headers, payload, timeout,
            telegram_fallback_info=telegram_fallback_info,
        )

        choices = response.get("choices")
        if not choices:
            raise RuntimeError(f"OpenAI response did not contain choices: {response}")

        message = choices[0].get("message", {})
        text = message.get("content", "")
        if not text:
            raise RuntimeError(f"OpenAI response did not contain content: {response}")

        return text

    @staticmethod
    def _call_claude(base_url, model_name, api_key, system_prompt, image_data,
                     temperature, max_tokens, timeout,
                     telegram_fallback_info=None):
        base_url = base_url.strip().rstrip("/")
        if not base_url:
            base_url = "https://api.anthropic.com/v1"

        url = f"{base_url}/messages"

        content = []
        for image in image_data:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image
                }
            })
        content.append({"type": "text", "text": system_prompt})

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }
        response = _post_json(
            url, headers, payload, timeout,
            telegram_fallback_info=telegram_fallback_info,
        )

        content_blocks = response.get("content")
        if not content_blocks:
            raise RuntimeError(f"Claude response did not contain content: {response}")

        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        if not text:
            raise RuntimeError(f"Claude response did not contain text: {response}")

        return text

    @staticmethod
    def _build_system_prompt(
        user_prompt,
        evaluation_criteria,
        num_frames,
        total_frame_count,
        select_every_nth,
        prev_score,
        prev_feedback,
        prev_h3_optimize_prompt,
        prev_distilled_meta_prompt,
    ):
        has_prev = prev_score is not None or (
            prev_feedback is not None and prev_feedback.strip()
        ) or (
            prev_h3_optimize_prompt is not None
            and prev_h3_optimize_prompt.strip()
        )

        iteration_info = ""
        distilled_info = ""

        if has_prev:
            iteration_info = "\n\n## 历史迭代信息\n"
            if prev_score is not None:
                score_value = float(prev_score) if isinstance(prev_score, str) else prev_score
                iteration_info += f"- 上一轮评分: {score_value:.2f}\n"
            if prev_feedback and prev_feedback.strip():
                iteration_info += f"- 上一轮修改意见: {prev_feedback}\n"
            if prev_h3_optimize_prompt and prev_h3_optimize_prompt.strip():
                iteration_info += f"- 上一轮针对性 system prompt:\n{prev_h3_optimize_prompt}\n"

            iteration_info += "\n### 迭代蒸馏要求\n"
            iteration_info += "1. **评估上一轮 system prompt 的效果**：分析哪些指导有效、哪些无效\n"
            iteration_info += "2. **检查本轮是否解决了上一轮提出的问题**\n"
            iteration_info += "3. **生成改进的 h3_optimize_prompt**：\n"
            iteration_info += "   - 保留上一轮有效的指导内容\n"
            iteration_info += "   - 针对本轮视频中**新出现的问题**或**未解决的问题**补充新的指导\n"
            iteration_info += "   - 移除或修正上一轮无效的指导\n"
            iteration_info += "   - 确保新的 system prompt 是上一轮的**改进版本**，而不是完全推倒重来\n"
        else:
            iteration_info = "\n\n这是对该视频的首次检验。请基于本轮视频的问题生成初始的 h3_optimize_prompt。"

        # 添加蒸馏元提示词的历史信息
        if prev_distilled_meta_prompt and prev_distilled_meta_prompt.strip():
            distilled_info = f"\n\n## 累积蒸馏的通用规则\n以下是从历史迭代中提炼的通用修改规则：\n{prev_distilled_meta_prompt}\n\n请基于本轮的新发现，更新和补充这些通用规则。"

        # 添加视频帧信息
        frame_info = f"\n\n## 视频帧信息\n"
        frame_info += f"- 当前提供的帧数: {num_frames if num_frames is not None else '未知（视频文件直接上传）'}\n"
        if total_frame_count is not None and total_frame_count > 0:
            frame_info += f"- 原始视频总帧数: {total_frame_count}\n"
            frame_info += f"- 采样间隔: 每 {select_every_nth} 帧提取 1 帧\n"
            coverage_percent = (num_frames * select_every_nth / total_frame_count) * 100
            frame_info += f"- 帧覆盖率: {coverage_percent:.1f}%\n"
        else:
            frame_info += f"- 采样间隔: 每 {select_every_nth} 帧提取 1 帧\n"

        prompt = f"""你是一个专业的视频质量检验 AI。你的任务是基于用户的原始提示词和质检要求，对输入的视频帧序列进行评估。
{frame_info}

## 原始生成提示词
{user_prompt}

## 质检要求
{evaluation_criteria}
{iteration_info}
{distilled_info}

# 视频生成质量评估与提示词迭代优化规则

## 一、输出格式

必须严格输出一个合法的 JSON 对象，不得添加 Markdown 代码围栏、前言、结语或 JSON 之外的任何内容。

输出结构如下：

{{
  "score": 0.0,
  "passed": false,
  "feedback": "基于实际视频证据的详细评价与问题分析",
  "h3_optimize_prompt": "针对本轮视频问题生成的、用于指导另一个 AI 修改原始 user_prompt 的 system prompt",
  "distilled_meta_prompt": "基于历轮评估结果累积、验证和更新的通用提示词优化规则"
}}

字段约束：

- `score`：数值类型，范围为 0.0–10.0，最多保留一位小数。
- `passed`：布尔类型，只允许 `true` 或 `false`。
- `feedback`：字符串，说明评分依据、具体问题、发生位置、严重程度和修改建议。
- `h3_optimize_prompt`：字符串，提供本轮专属的提示词修改指令，不得直接输出修改后的 user_prompt。
- `distilled_meta_prompt`：字符串，提供经过验证、可以跨任务复用的通用修改规则。
- 所有字符串中的换行和引号必须符合 JSON 转义规范。
- 如果缺少必要的审核材料，必须明确说明证据不足，不得伪造视频时间点或评估结果。

## 二、审核目标与证据原则

你是一名资深日本商业 2D 动漫分镜师、作画监督及 AI 视频质量审核专家。

你的任务是对生成视频进行客观审核，并结合原始 `user_prompt` 判断哪些问题可以通过提示词修改改善。

审核材料可能包含：

- `user_prompt`：原始视频生成提示词。
- `video`：本轮生成的视频，或从视频中提取的采样帧。
- `prev_score`：上一轮评估分数。
- `prev_feedback`：上一轮评估反馈。
- `prev_h3_optimize_prompt`：上一轮生成的修改指令。
- `prev_distilled_meta_prompt`：历史累积的通用修改规则。

必须遵循以下原则：

1. **视频证据优先**：以实际可见的画面、可观察的动作及可听见的音频为判断依据，不得将提示词中的预期效果直接当成实际生成结果。
2. **提示词与视频对照**：判断实际画面是否符合角色设定、场景布局、动作意图、镜头安排和视觉风格。
3. **精确定位问题**：尽可能指出实际视频时间点、对应镜头、涉及主体及具体异常。无法确定时间点时，明确说明。
4. **区分问题来源**：
   - 提示词缺陷：动作、空间关系、镜头逻辑或视觉约束描述不足、冲突或含糊。
   - 生成稳定性问题：提示词已足够明确，但输出仍出现随机畸变、闪烁或细节漂移。
   - 参考素材问题：参考图本身缺少必要信息，或多个参考素材之间存在冲突。
   - 无法确定：现有证据不足以可靠判断原因。
5. **避免过度归因**：不能因为画面出现异常，就断言一定是提示词导致的。只有存在合理的提示词改进路径时，才将其转化为修改指令。
6. **不虚构信息**：不得假定未提供的参考图、音轨、历史评分或迭代结果已经经过检查。
7. **修改目标明确**：优先解决影响整体观感、主体一致性、动作可读性和时序连贯性的主要问题，不为追求文字复杂度而增加无必要的描述。

## 三、视频评估维度

根据视频类型和实际可观察证据，对以下维度进行评估。

### 1. 动作连贯性与角色演技

检查：

- 动作是否具有清晰的起始状态、执行过程和结束状态。
- 手部、手指、手臂、腿部及身体姿态是否合理。
- 动作是否存在突然跳变、肢体穿插、关节异常或运动方向错误。
- 角色表情是否与情境及动作一致。
- 对于明显的表情变化，眉毛、眼睛、嘴部是否呈现合理的状态变化。
- 动作速度、停顿、反应延迟是否符合镜头节奏。
- 多角色互动时，注视方向、相对位置及动作回应是否一致。
- 对话场景中，若音频可用，检查口型与语音时序是否大致匹配。

### 2. 画面质量与跨帧稳定性

检查：

- 面部、手部、肢体和服装是否存在畸变。
- 轮廓、头发、衣物褶皱及背景线条是否异常闪烁。
- 是否出现无合理原因的细节突变、纹理游移或局部融化。
- 清晰度、边缘质量、压缩伪影及运动模糊是否影响观看。
- 角色比例、五官、发型、服装及配色是否跨帧稳定。
- 背景透视、物体位置、地面结构及空间关系是否保持一致。
- 光照、阴影和整体色调是否出现不合理变化。

注意：正常的动作变形、合理的运动模糊、表情变化和有意设计的光影变化，不应自动判定为缺陷。

### 3. 主体符合度与参考一致性

检查：

- 角色的发型、发色、服装、体型和辨识性特征是否符合原始设定。
- 场景布局、关键物体和背景结构是否符合参考素材。
- 主体数量、角色身份及其相互关系是否正确。
- 是否出现未被要求的主体替换、服装变化或关键物体消失。
- 是否偏离指定的日本商业 2D 动漫风格，例如无意出现写实摄影感、明显 3D 塑料质感或不一致的渲染方式。

仅在原始提示词或参考素材明确提供相应信息时，才将不一致认定为主体偏差。

### 4. 镜头语言与空间关系

检查：

- 景别、构图、角色站位及视线关系是否符合描述。
- 镜头切换后，人物的屏幕位置、朝向及空间关系是否合理。
- 是否出现不必要的大幅缩放、平移、旋转或连续运镜。
- 镜头运动是否具有明确的叙事目的，是否造成构图失控。
- 是否存在背景漂移、透视突变或角色与环境脱离的现象。

遵循日本商业 2D 动漫常见的分镜逻辑：优先通过硬切、反应镜头、正反打、特写及稳定构图组织叙事；仅在具有明确目的时使用小幅推、拉、摇、移或跟随运动。

不得将固定机位视为所有镜头的强制要求。对于原始提示词明确要求的运镜，应判断其执行质量，而不是直接要求删除运镜。

### 5. 时序与整体协调性

检查：

- 动作是否按照描述的时间顺序发生。
- 镜头之间是否存在突兀的动作重置、人物位置跳变或视线不连续。
- 角色反应、动作停顿及镜头持续时间是否合理。
- 视觉风格、色彩、光照和场景氛围是否统一。
- 画面、动作、对白及音效之间是否存在明显冲突。

只检查能够从现有材料中观察到的内容。没有音频时，不得对口型与语音同步、音效质量或配乐质量作出确定性结论。

## 四、评分规则

采用 10 分制。以下权重为默认参考，可根据视频任务的实际需求调整。

| 评估维度 | 权重 |
|---|---:|
| 动作连贯性与角色演技 | 25% |
| 画面质量与跨帧稳定性 | 25% |
| 主体符合度与参考一致性 | 20% |
| 镜头语言与空间关系 | 15% |
| 时序与整体协调性 | 15% |

每个维度先独立评估，再按权重计算综合分数。

评分等级参考：

- 9.0–10.0：质量优秀，仅存在不影响观感的轻微问题。
- 8.0–8.9：质量良好，主要要求均已满足，存在少量可改进细节。
- 6.5–7.9：基本可用，但存在明显问题，需要有针对性地优化。
- 4.0–6.4：存在多个影响观感的问题，或至少一个严重缺陷。
- 0.0–3.9：存在严重的主体失真、动作异常、结构崩坏或大范围不符合要求的问题。

通过条件：

- `score >= 8.0` 时，`passed = true`。
- `score < 8.0` 时，`passed = false`。

但若存在不可接受的严重缺陷，即使综合分数达到 8.0，也应判定为不通过。此类缺陷包括主体身份严重错误、关键动作完全未实现、严重结构崩坏或其他直接违背任务核心目标的问题。

反之，不得仅因主观偏好或轻微瑕疵而降低评分。

如果输入包含上一轮评分，应使用一致的标准进行比较；不得为了体现迭代效果而人为抬高或压低分数。

## 五、feedback 生成规则

`feedback` 必须说明实际观察结果，而不是只给出笼统评价。

建议采用以下结构：

1. **总体结论**：说明视频的整体质量、是否达到目标，以及主要优点。
2. **主要问题**：按严重程度排序，列出需要优先解决的问题。
3. **证据定位**：尽可能提供时间点、镜头编号、涉及主体及可观察到的异常。
4. **问题归因**：说明问题更可能来自提示词、参考素材、生成稳定性，还是当前证据不足以判断。
5. **优化方向**：提出可以执行的修改方向，并说明预期改善目标。

问题描述应具体，例如：

“00:03.200–00:04.100，角色右手从桌面抬起时，手指轮廓发生明显变化，随后手掌与杯子边缘出现融合。原始提示词未明确手部动作的结束姿态，可以尝试在对应镜头中补充手掌朝向、手指状态和动作终点。”

不得使用“画面不好”“动作不自然”“继续优化”等缺少证据的空泛描述。

如果视频总体表现良好，应如实说明，不得为了生成下一轮优化指令而强行寻找问题。

## 六、h3_optimize_prompt 生成规则

### 6.1 定义与用途

`h3_optimize_prompt` 是一段供另一个 AI 使用的 system prompt。

调用方式：

- `system_message = h3_optimize_prompt`
- `user_message = user_prompt`

它的任务是指导另一个 AI 修改原始提示词，而不是直接输出修改后的提示词。

### 6.2 保持原始结构

必须明确要求下游 AI：

1. 保持原始 `user_prompt` 的主体结构、段落顺序、字段名称及时间轴组织方式。
2. 如果原文包含以下字段，应保留其名称与功能：
   - `subject_definitions`
   - `summary`
   - `retention_analysis`
   - `detailed_description`
   - `overall_soundscape`
   - `non_diegetic_music`
3. 保留原始主体标签、参考素材标签、镜头编号、时间戳及必要的格式标记。
4. 不得擅自删除原有剧情、对白、角色身份、参考素材绑定或关键动作。
5. 不得将完整提示词改写成与原格式不同的摘要、说明文或新的 JSON 结构。
6. 只修改与本轮审核问题相关的内容；没有问题的部分尽量保持不变。
7. 原文不存在的字段不得为了套用模板而强行添加，除非确有必要且不会破坏原有结构。

### 6.3 将问题映射到具体字段

必须根据实际审核结果，指出应修改的具体段落及修改目标。

常见映射如下：

- **动作不连贯**：在 `detailed_description` 对应的 `[Shot X]` 中，补充动作起始姿态、关键过程、动作终点和必要的过渡状态。
- **手部或肢体畸变**：在对应镜头中明确手掌朝向、手指姿态、肢体相对位置和接触关系；避免增加不必要的复杂动作。
- **表情不明确**：在对应镜头中补充眉毛、眼睛、嘴部的可见状态及其变化顺序，不要求每一帧都罗列全部面部细节。
- **主体外观漂移**：在 `subject_definitions` 中强化已有参考素材能够支持的辨识性特征，并在必要的镜头中补充简短的一致性约束。
- **背景结构畸变**：在场景定义或对应的 `detailed_description` 镜头中明确关键物体位置、透视关系、背景布局和需要保持稳定的结构。
- **跨帧闪烁**：针对实际发生异常的主体或背景，补充线条、轮廓、配色和关键细节的连续性约束；不得假定添加文字约束就必然消除模型的随机生成误差。
- **镜头运动失控**：在对应镜头中简化运动路径、减少不必要的运动幅度，或在叙事允许时改用稳定构图与硬切。
- **镜头衔接异常**：明确前后镜头的动作承接、角色朝向、屏幕位置和必要的连续性关系。
- **声音问题**：只有在提供了可审核的音频证据且问题能够通过提示词调整时，才修改 `overall_soundscape`、`non_diegetic_music` 或相关音频描述。

### 6.4 修改优先级

按照以下顺序处理：

1. 核心主体或剧情执行错误。
2. 严重动作异常和结构畸变。
3. 明显的时序、空间和镜头连续性问题。
4. 主体外观、风格及跨帧稳定性问题。
5. 轻微表情、节奏或视觉细节问题。

优先解决少数高影响问题，不要将所有问题同时转化为大量新增约束。

### 6.5 修改指令质量要求

生成的 `h3_optimize_prompt` 必须：

- 明确指出问题发生的段落或镜头。
- 说明应增加、删除、简化或澄清什么内容。
- 与原始剧情、参考素材及镜头意图保持一致。
- 避免无依据地添加新的服装、道具、动作、对白、镜头或视觉效果。
- 避免反复堆叠“绝对稳定”“完全不变”等无法保证的约束。
- 避免将每个动作拆成过多的微小镜头或复杂时间节点。
- 避免为了修复局部问题而重写整个提示词。
- 如果现有提示词已经足够明确，应优先考虑简化冲突描述，而不是继续增加文字。
- 如果问题更像随机生成误差，应只提出有限、合理的提示词改进，并在 `feedback` 中说明限制。

如果本轮无需修改，应明确要求下游 AI 保持原始提示词不变，不得为了触发迭代而强行重写。

## 七、distilled_meta_prompt 生成规则

### 7.1 定义与目标

`distilled_meta_prompt` 是跨任务复用的通用优化规则集合。

它不是本轮问题列表，也不是对本轮 `h3_optimize_prompt` 的简单复制，而是从多轮评估和修改结果中提炼的、能够指导未来任务的稳定规则。

当提供 `prev_distilled_meta_prompt` 时，必须以它为基础进行增量更新。

如果没有历史规则，则根据当前可用证据建立初始版本，但必须区分：

- 已通过多轮结果验证的规则。
- 当前轮次提出、尚未验证的新假设。

不得把一次偶发的生成错误直接升级为普遍适用的强制规则。

### 7.2 规则分类

根据实际需要组织规则，可包含：

1. 角色动作与身体结构。
2. 面部表情与角色演技。
3. 主体外观与参考一致性。
4. 画面稳定性与跨帧一致性。
5. 镜头语言与空间连续性。
6. 时间轴与动作衔接。
7. 音频、对白与口型同步。
8. 提示词复杂度与约束冲突。

没有证据支持的类别可以省略，不必每次输出全部分类。

### 7.3 每条规则的表达方式

每条规则应明确包含：

- **适用问题**：什么情况下使用该规则。
- **修改位置**：在哪个字段或镜头中执行。
- **修改方法**：具体补充、简化或调整什么信息。
- **使用边界**：什么情况下不应强制使用该规则。

例如：

“角色动作与手部结构：当视频出现手部姿态不明确或动作过程中手指结构漂移时，在 `detailed_description` 对应镜头中明确手掌朝向、手指状态、接触关系和动作结束姿态。优先简化不必要的手部操作；对于本身清晰且稳定的动作，不重复增加约束。”

### 7.4 多轮经验的验证与更新

如果输入包含上一轮评估信息，应按照以下原则处理：

**A. 规则有效**

当上一轮采用某项修改策略后，当前轮对应问题明显改善，且没有引入新的显著缺陷时，可以将该策略抽象为通用规则。

**B. 规则部分有效**

当某项策略改善了目标问题，但同时引入了其他问题时，应缩小其适用范围、降低约束强度或增加使用条件。

**C. 规则无效**

当相同问题在采用该策略后持续存在时，不得无条件重复强化该策略。应重新判断问题来源，尝试简化描述、调整字段映射，或承认提示词本身可能无法解决该问题。

**D. 规则产生副作用**

如果新增约束造成动作僵硬、镜头过度静止、提示词冗长、时间轴碎片化或新的主体偏差，应修订或移除相关规则。

**E. 无法比较**

如果缺少上一轮提示词、评估结果或对应的视频证据，不得声称某项规则已经被验证有效。可以暂时保留原有规则，并将新增规则标记为待验证。

### 7.5 保留、合并与淘汰

更新规则时：

- 保留已验证且仍适用的有效规则。
- 将重复或高度相似的规则合并。
- 删除已被后续证据否定的规则。
- 为存在副作用的规则补充适用条件。
- 将新发现的策略作为待验证规则，而不是直接升级为全局强制要求。
- 避免规则数量无上限增长，优先保留对生成质量有明显帮助且可操作的规则。
- 不得因为当前视频未出现某类问题，就直接删除历史上已验证有效的通用规则。

### 7.6 规则与 H3 生成机制相适应

通用规则应遵循以下原则：

- 优先使用清晰、具体、可执行的自然语言描述。
- 通过稳定构图、明确主体位置、合理动作幅度和清楚的镜头切换减少歧义。
- 不要将固定机位应用于所有镜头；根据叙事需要选择静止镜头或有限运镜。
- 不要将所有角色动作都拆分成大量细粒度时间点。
- 对关键动作明确起始状态、过程和结束状态即可，不必逐帧描述。
- 对参考素材已经明确展示的特征，避免无意义地重复堆叠文字。
- 优先消除提示词内部的矛盾和不必要的复杂度，而不是单纯增加约束数量。

## 八、迭代决策规则

综合 `score`、`passed`、`feedback` 和历史迭代记录，确定下一轮策略。

### 情况 A：通过且质量稳定

- `passed = true`。
- `h3_optimize_prompt` 应要求保留当前有效内容，不进行无必要修改。
- `distilled_meta_prompt` 仅在出现有充分证据支持的新经验时更新。
- 不得为了继续迭代而虚构优化目标。

### 情况 B：未通过，但存在明确可修复的问题

- `passed = false`。
- 生成针对关键问题的最小修改指令。
- 优先修改影响最大的少数段落。
- 避免一次引入大量未经验证的规则。

### 情况 C：连续迭代但问题没有改善

- 检查此前修改是否确实针对根本问题。
- 检查是否存在相互冲突的提示词约束。
- 尝试简化动作、镜头运动或复杂的时序描述。
- 不要仅通过不断增加文字强化同一个无效策略。
- 如果证据指向模型生成稳定性限制，应明确指出提示词优化的预期收益可能有限。

### 情况 D：分数提高但出现新问题

- 不应仅根据综合分数上升就判定所有修改有效。
- 识别新出现的缺陷及其可能关联的修改。
- 保留有效部分，回退或调整有明显副作用的约束。
- 仅将净效果明确为正的策略沉淀为通用经验。

## 九、最终检查

输出 JSON 前，必须逐项检查：

1. 是否严格符合指定 JSON 结构及数据类型。
2. 是否根据实际视频证据进行评分。
3. 是否将问题与原始提示词的具体段落对应起来。
4. 是否避免把随机生成误差直接归咎于提示词。
5. `h3_optimize_prompt` 是否为修改指令，而非修改后的完整提示词。
6. 是否要求保留原始提示词的字段结构、主体标签、时间戳和剧情。
7. `distilled_meta_prompt` 是否与本轮专属修改指令明确区分。
8. 是否根据历史结果更新规则，而非机械复制或全部替换。
9. 是否避免把未经验证的假设写成强制性通用规则。
10. 是否根据评分门槛和严重缺陷规则正确设置 `passed`。
11. 是否避免输出证据不足的结论、虚构时间点和无依据的优化要求。
12. 是否确保最终内容为可解析的合法 JSON。

请始终以客观证据、实际改善效果和最小必要修改为优先目标，而不是追求更长的提示词或更多的规则。
"""

        return prompt

    @staticmethod
    def _parse_response(response_text, pass_threshold):
        # 清洗响应文本，提取纯 JSON 内容
        cleaned_text = response_text.strip()

        # 尝试提取 Markdown 代码块中的 JSON
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned_text, re.DOTALL)
        if match:
            cleaned_text = match.group(1)
        else:
            # 尝试直接寻找第一个 { 和最后一个 }
            start_idx = cleaned_text.find('{')
            end_idx = cleaned_text.rfind('}')
            if start_idx != -1 and end_idx != -1:
                cleaned_text = cleaned_text[start_idx : end_idx + 1]

        try:
            data = json.loads(cleaned_text)
            score = float(data.get("score", 0.0))
            passed = bool(data.get("passed", False))
            feedback = str(data.get("feedback", ""))
            h3_optimize_prompt = str(data.get("h3_optimize_prompt") or "")
            distilled_meta_prompt = str(data.get("distilled_meta_prompt") or "")

            if score >= pass_threshold and not passed:
                passed = True
            elif score < pass_threshold and passed:
                passed = False

            return {
                "score": score,
                "passed": passed,
                "feedback": feedback,
                "h3_optimize_prompt": h3_optimize_prompt,
                "distilled_meta_prompt": distilled_meta_prompt,
            }
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            raise RuntimeError(
                f"Failed to parse response as JSON. Original text: {response_text}"
            ) from e

    @staticmethod
    def _format_log(system_prompt, response_text, result, pass_threshold):
        log = f"""=== Gemini Video Inspector Log ===

[System Prompt]
{system_prompt}

[Gemini Response]
{response_text}

[Parsed Result]
- Score: {result['score']:.2f}
- Pass Threshold: {pass_threshold:.2f}
- Passed: {result['passed']}
- Feedback: {result['feedback']}
- H3 Optimize Prompt: {result['h3_optimize_prompt']}

================================"""
        return log


NODE_CLASS_MAPPINGS = {
    "GeminiVideoInspector": GeminiVideoInspector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GeminiVideoInspector": "Gemini Video Inspector",
}

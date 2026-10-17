import base64
import io
import json
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image


def _post_json(url, headers, payload, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
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
        image = image.detach().cpu().clamp(0, 1).mul(255).byte().numpy()
        png = io.BytesIO()
        Image.fromarray(image, mode="RGB").save(png, format="PNG")
        parts.append(base64.b64encode(png.getvalue()).decode("ascii"))
    return parts


def _gemini_url(base_url, model, api_key):
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith(":generateContent"):
        url = base_url
    else:
        if base_url.endswith("/v1beta") or base_url.endswith("/v1"):
            api_root = base_url
        else:
            api_root = f"{base_url}/v1beta"
        url = f"{api_root}/models/{urllib.parse.quote(model, safe='')}:generateContent"

    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urllib.parse.urlencode({'key': api_key})}"


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
                "images": ("IMAGE",),
                "user_prompt": ("STRING", {"multiline": True, "default": ""}),
                "evaluation_criteria": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "检查动作是否连贯、画面有无畸变、主体是否符合提示词",
                    },
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
                "prev_score": ("FLOAT",),
                "prev_feedback": ("STRING", {"forceInput": True}),
                "prev_h3_optimize_prompt": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("BOOLEAN", "FLOAT", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "passed",
        "score",
        "feedback",
        "log_text",
        "h3_optimize_prompt",
    )
    FUNCTION = "inspect"
    CATEGORY = "my_comfyui/AI"

    def inspect(
        self,
        images,
        user_prompt,
        evaluation_criteria,
        api_key,
        model_name,
        base_url,
        temperature,
        max_tokens,
        pass_threshold,
        timeout,
        prev_score=None,
        prev_feedback=None,
        prev_h3_optimize_prompt=None,
    ):
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

        image_data = _image_parts(images)
        if not image_data:
            raise ValueError("images cannot be empty")

        system_prompt = self._build_system_prompt(
            user_prompt,
            evaluation_criteria,
            prev_score,
            prev_feedback,
            prev_h3_optimize_prompt,
        )

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
        )
        response_text = _gemini_text(response)

        result = self._parse_response(response_text, pass_threshold)
        if (
            prev_score is not None
            and prev_score > result["score"]
            and prev_h3_optimize_prompt
            and prev_h3_optimize_prompt.strip()
        ):
            result["h3_optimize_prompt"] = prev_h3_optimize_prompt

        log_text = self._format_log(
            system_prompt, response_text, result, pass_threshold
        )

        return (
            result["passed"],
            result["score"],
            result["feedback"],
            log_text,
            result["h3_optimize_prompt"],
        )

    @staticmethod
    def _build_system_prompt(
        user_prompt,
        evaluation_criteria,
        prev_score,
        prev_feedback,
        prev_h3_optimize_prompt,
    ):
        has_prev = prev_score is not None or (
            prev_feedback is not None and prev_feedback.strip()
        ) or (
            prev_h3_optimize_prompt is not None
            and prev_h3_optimize_prompt.strip()
        )

        iteration_info = ""
        if has_prev:
            iteration_info = "\n\n## 历史迭代信息\n"
            if prev_score is not None:
                iteration_info += f"- 上一轮评分: {prev_score:.2f}\n"
            if prev_feedback and prev_feedback.strip():
                iteration_info += f"- 上一轮修改意见: {prev_feedback}\n"
            if prev_h3_optimize_prompt and prev_h3_optimize_prompt.strip():
                iteration_info += (
                    f"- 上一轮优化提示词: {prev_h3_optimize_prompt}\n"
                )
            iteration_info += "\n请重点检查本轮输入是否解决了上一轮提出的问题。"
        else:
            iteration_info = "\n\n这是对该视频的首次检验。"

        prompt = f"""你是一个专业的视频质量检验 AI。你的任务是基于用户的原始提示词和质检要求，对输入的视频帧序列进行评估。

## 原始生成提示词
{user_prompt}

## 质检要求
{evaluation_criteria}
{iteration_info}

## 输出要求
请严格按照以下 JSON 格式输出评估结果：

{{
  "score": 浮点数（0-10分），
  "passed": 布尔值（true 或 false），
  "feedback": "详细的评价与建议，如果不通过请明确指出问题所在",
  "h3_optimize_prompt": "基于原始提示词和本轮反馈改写的、可直接用于下一轮视频生成的完整提示词"
}}

评估维度包括但不限于：
1. 动作连贯性
2. 画面质量（有无畸变、模糊、闪烁）
3. 主体符合度（是否符合原始提示词）
4. 整体协调性

请客观、严谨地给出评分和反馈。优化提示词应保留原始提示词的核心意图，并针对本轮视频暴露的问题进行具体改进；不要只输出修改建议。
"""

        return prompt

    @staticmethod
    def _parse_response(response_text, pass_threshold):
        try:
            data = json.loads(response_text)
            score = float(data.get("score", 0.0))
            passed = bool(data.get("passed", False))
            feedback = str(data.get("feedback", ""))
            h3_optimize_prompt = str(data.get("h3_optimize_prompt") or "")

            if score >= pass_threshold and not passed:
                passed = True
            elif score < pass_threshold and passed:
                passed = False

            return {
                "score": score,
                "passed": passed,
                "feedback": feedback,
                "h3_optimize_prompt": h3_optimize_prompt,
            }
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            raise RuntimeError(
                f"Failed to parse Gemini response as JSON: {response_text}"
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

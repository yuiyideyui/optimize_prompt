import base64
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image
from .utils.network_retry import retry_on_network_error


@retry_on_network_error(max_retries=4, retry_delay=60)
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
        if video is not None:
            # 视频输入：提取所有帧，忽略 frame_count 和 select_every_nth
            if hasattr(video, "get"):
                # 如果是字典格式，获取帧数据
                selected_images = video.get("frames") if isinstance(video, dict) else video
            else:
                selected_images = video
            actual_frame_count = len(selected_images)
            actual_select_every_nth = 1
        elif images is not None:
            # 图像输入：使用原有逻辑
            actual_frame_count = frame_count if frame_count is not None else len(images)
            actual_select_every_nth = select_every_nth

            # 根据 select_every_nth 筛选图片
            if select_every_nth > 1:
                selected_images = images[::select_every_nth]
            else:
                selected_images = images
        else:
            raise ValueError("images 或 video 必须提供其中之一")

        image_data = _image_parts(selected_images)
        if not image_data:
            raise ValueError("images cannot be empty")

        system_prompt = self._build_system_prompt(
            user_prompt,
            evaluation_criteria,
            len(image_data),
            actual_frame_count,
            actual_select_every_nth,
            prev_score,
            prev_feedback,
            prev_h3_optimize_prompt,
            prev_distilled_meta_prompt,
        )

        if provider == "gemini":
            response_text = self._call_gemini(
                base_url, model_name, api_key, system_prompt, image_data,
                temperature, max_tokens, timeout
            )
        elif provider == "openai":
            response_text = self._call_openai(
                base_url, model_name, api_key, system_prompt, image_data,
                temperature, max_tokens, timeout
            )
        elif provider == "claude":
            response_text = self._call_claude(
                base_url, model_name, api_key, system_prompt, image_data,
                temperature, max_tokens, timeout
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")

        result = self._parse_response(response_text, pass_threshold)

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

    @staticmethod
    def _call_gemini(base_url, model_name, api_key, system_prompt, image_data,
                     temperature, max_tokens, timeout):
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
        return _gemini_text(response)

    @staticmethod
    def _call_openai(base_url, model_name, api_key, system_prompt, image_data,
                     temperature, max_tokens, timeout):
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
        response = _post_json(url, headers, payload, timeout)

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
                     temperature, max_tokens, timeout):
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
        response = _post_json(url, headers, payload, timeout)

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
        frame_info += f"- 当前提供的帧数: {num_frames}\n"
        if total_frame_count is not None:
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

## 输出要求
请严格按照以下 JSON 格式输出评估结果：

{{
  "score": 浮点数（0-10分），
  "passed": 布尔值（true 或 false），
  "feedback": "详细的评价与建议，如果不通过请明确指出问题所在",
  "h3_optimize_prompt": "一段用于指导 AI 如何修改 user_prompt 的 system prompt 指令（针对本轮视频的具体问题）",
  "distilled_meta_prompt": "从多轮迭代中提炼出的通用修改规则（跨任务的元知识）"
}}

评估维度包括但不限于：
1. 动作连贯性
2. 画面质量（有无畸变、模糊、闪烁）
3. 主体符合度（是否符合原始提示词）
4. 整体协调性

## h3_optimize_prompt 生成规则
h3_optimize_prompt 是一段 **system prompt**，它会被用于指导另一个 AI 如何修改用户的 user_prompt。

使用场景：
- system_message = h3_optimize_prompt（你生成的指令）
- user_message = user_prompt（用户的原始提示词）
- AI 根据 system_message 的指导来修改 user_message

内容要求：
1. 这是给 AI 的**修改指令**，不是修改后的结果
2. 必须要求 AI 保持原始 user_prompt 的格式结构（subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music 等段落标题）
3. 针对本轮视频暴露的具体问题，明确指出需要在哪些段落进行什么修改
4. 所有指导必须具体、可操作，例如：
   - 动作问题 → "在 detailed_description 的 [Shot X] 中，增强角色的手部动作描述，明确手指姿态和移动轨迹"
   - 画面畸变 → "在 detailed_description 的所有镜头中，添加'固定机位'、'无镜头移动'等稳定性约束"
   - 主体不符 → "在 subject_definitions 的 <Subject X> 中，补充具体的服装颜色、发型细节"

示例格式：
"你需要修改用户提供的视频生成提示词，保持其原有的格式结构不变。针对以下问题进行针对性修改：1. 在 detailed_description 的 [Shot 2] 段落中，当前角色的面部表情描述过于简略，请添加眉毛、眼睛、嘴唇的具体变化细节。2. 在 subject_definitions 中，<Subject 1> 的光照描述'明亮、柔和'不够精确，请补充光源方向、阴影特征等信息。"

## distilled_meta_prompt 生成规则
distilled_meta_prompt 是从多轮迭代中**提炼出的通用规则**，它不针对某个具体视频的问题，而是总结**普遍适用的修改原则**。

与 h3_optimize_prompt 的区别：
- h3_optimize_prompt：针对本轮视频的具体问题（"在 [Shot 2] 中角色A的手部动作不清晰，请添加..."）
- distilled_meta_prompt：通用的修改原则（"针对角色动作问题：在 detailed_description 对应镜头中，补充具体的肢体姿态、移动轨迹、表情变化细节"）

生成要求：
1. **抽象化具体问题为通用规则**：
   - 具体问题："[Shot 2] 中 Airi 的眉毛动作不清晰" → 通用规则："角色表情描述应包含眉毛、眼睛、嘴唇的具体状态变化"
   - 具体问题："书桌背景出现畸变" → 通用规则："每个镜头描述必须包含'固定机位'、'无镜头移动'等稳定性约束"

2. **累积有效的修改策略**：
   - 如果上一轮某个指导有效（评分提升），将其抽象为通用规则加入 distilled_meta_prompt
   - 如果某个指导无效，不要加入或从 distilled_meta_prompt 中移除

3. **保持结构化和可复用**：
   - 按问题类型分类（角色动作、画面稳定性、主体特征、时序连贯性等）
   - 每条规则明确指出"针对什么问题 → 在哪个段落 → 如何修改"

4. **迭代更新而非替换**：
   - 如果已有 prev_distilled_meta_prompt，基于它进行补充和完善
   - 保留已验证有效的规则，补充新发现的规则，移除无效规则

示例格式：
"你需要修改用户提供的视频生成提示词，保持原有的格式结构（subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music）不变。

通用修改规则：

1. **角色动作精确化**
   - 在 detailed_description 的每个镜头中，补充角色的具体肢体姿态（手部姿势、手指状态、身体角度）
   - 表情描述必须包含眉毛、眼睛、嘴唇的状态变化和时序
   - 动作过渡必须明确起止帧和中间状态

2. **画面稳定性约束**
   - 每个镜头描述必须包含'固定机位'、'无镜头移动'、'无背景漂移'等明确约束
   - 添加'保持角色边缘、头发、衣物、背景线条跨帧稳定'的描述

3. **主体特征强化**
   - 在 subject_definitions 中，补充角色的服装颜色、材质、发型细节、面部特征
   - 补充场景的光照方向、阴影特征、色调信息

4. **时序连贯性保证**
   - 明确每个镜头的起止时间戳
   - 添加镜头间的动作衔接描述"

请客观、严谨地给出评分和反馈。
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

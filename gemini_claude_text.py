import base64
import io
import json
import os
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


def _claude_url(base_url):
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        raise ValueError("base_url cannot be empty")
    if base_url.endswith("/messages"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/messages"
    return f"{base_url}/v1/messages"


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


def _claude_text(response):
    blocks = response.get("content") or []
    text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
    if not text:
        raise RuntimeError(f"Claude response did not contain text: {response}")
    return text


def _openai_url(base_url):
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return "https://api.openai.com/v1/chat/completions"
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def _openai_text(response):
    choices = response.get("choices")
    if not choices:
        raise RuntimeError(f"OpenAI response did not contain choices: {response}")
    message = choices[0].get("message", {})
    text = message.get("content", "")
    if not text:
        raise RuntimeError(f"OpenAI response did not contain content: {response}")
    return text


class GeminiClaudeText:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "provider": (["gemini", "gemma", "claude", "openai"], {"default": "gemini"}),
                "user_prompt": ("STRING", {"multiline": True, "default": ""}),
                "systems_prompt": ("STRING", {"multiline": True, "default": ""}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "base_url": (
                    "STRING",
                    {"default": "https://generativelanguage.googleapis.com"},
                ),
                "temperature": (
                    "FLOAT",
                    {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01},
                ),
                "top_p": (
                    "FLOAT",
                    {"default": 0.95, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
                "max_tokens": ("INT", {"default": 2048, "min": 1, "max": 65536}),
                "timeout": ("INT", {"default": 120, "min": 1, "max": 600}),
            },
            "optional": {
                "images": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "generate"
    CATEGORY = "my_comfyui/AI"

    def generate(
        self,
        provider,
        user_prompt,
        systems_prompt,
        api_key,
        model,
        base_url,
        temperature,
        top_p,
        max_tokens,
        timeout,
        images=None,
    ):
        api_key = api_key.strip()
        model = model.strip()
        if not api_key:
            raise ValueError("api_key cannot be empty")
        if not model:
            raise ValueError("model cannot be empty")

        image_data = _image_parts(images)
        if provider in ("gemini", "gemma"):
            return (self._generate_gemini(
                user_prompt,
                systems_prompt,
                api_key,
                model,
                base_url,
                temperature,
                top_p,
                max_tokens,
                timeout,
                image_data,
            ),)
        elif provider == "claude":
            return (self._generate_claude(
                user_prompt,
                systems_prompt,
                api_key,
                model,
                base_url,
                temperature,
                top_p,
                max_tokens,
                timeout,
                image_data,
            ),)
        elif provider == "openai":
            return (self._generate_openai(
                user_prompt,
                systems_prompt,
                api_key,
                model,
                base_url,
                temperature,
                top_p,
                max_tokens,
                timeout,
                image_data,
            ),)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    @staticmethod
    def _generate_gemini(
        user_prompt,
        systems_prompt,
        api_key,
        model,
        base_url,
        temperature,
        top_p,
        max_tokens,
        timeout,
        image_data,
    ):
        parts = [{"text": user_prompt}]
        parts.extend(
            {"inline_data": {"mime_type": "image/png", "data": image}}
            for image in image_data
        )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "topP": top_p,
                "maxOutputTokens": max_tokens,
            },
        }
        if systems_prompt.strip():
            payload["systemInstruction"] = {"parts": [{"text": systems_prompt}]}

        response = _post_json(
            _gemini_url(base_url, model, api_key),
            {},
            payload,
            timeout,
        )
        return _gemini_text(response)

    @staticmethod
    def _generate_claude(
        user_prompt,
        systems_prompt,
        api_key,
        model,
        base_url,
        temperature,
        top_p,
        max_tokens,
        timeout,
        image_data,
    ):
        content = []
        content.extend(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image,
                },
            }
            for image in image_data
        )
        content.append({"type": "text", "text": user_prompt})
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "messages": [{"role": "user", "content": content}],
        }
        if systems_prompt.strip():
            payload["system"] = systems_prompt

        response = _post_json(
            _claude_url(base_url),
            {
                "x-api-key": api_key,
                "Authorization": f"Bearer {api_key}",
                "anthropic-version": "2023-06-01",
            },
            payload,
            timeout,
        )
        return _claude_text(response)

    @staticmethod
    def _generate_openai(
        user_prompt,
        systems_prompt,
        api_key,
        model,
        base_url,
        temperature,
        top_p,
        max_tokens,
        timeout,
        image_data,
    ):
        content = [{"type": "text", "text": user_prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image}"}
            }
            for image in image_data
        )

        messages = []
        if systems_prompt.strip():
            messages.append({"role": "system", "content": systems_prompt})
        messages.append({"role": "user", "content": content})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }

        response = _post_json(
            _openai_url(base_url),
            {"Authorization": f"Bearer {api_key}"},
            payload,
            timeout,
        )
        return _openai_text(response)


NODE_CLASS_MAPPINGS = {
    "GeminiClaudeText": GeminiClaudeText,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GeminiClaudeText": "Gemini / Claude Text",
}

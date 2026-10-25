"""Small OpenAI-compatible image edit client for reference-guided generation."""

import base64
import json
import mimetypes
import uuid
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from typing import Any


def _images_edit_endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        raise ValueError("Image API base URL cannot be empty.")
    if base.endswith("/images/edits"):
        return base
    if base.endswith("/v1"):
        return base + "/images/edits"
    return base + "/v1/images/edits"


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----Comic2Anime{}".format(uuid.uuid4().hex)
    chunks = []
    for name, value in fields.items():
        chunks.extend([
            "--{}\r\n".format(boundary).encode(),
            ('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(name)).encode(),
            str(value).encode(), b"\r\n",
        ])
    for name, (filename, content, mime) in files.items():
        chunks.extend([
            "--{}\r\n".format(boundary).encode(),
            ('Content-Disposition: form-data; name="{}"; filename="{}"\r\n'.format(name, filename)).encode(),
            ("Content-Type: {}\r\n\r\n".format(mime)).encode(),
            content, b"\r\n",
        ])
    chunks.append("--{}--\r\n".format(boundary).encode())
    return b"".join(chunks), "multipart/form-data; boundary={}".format(boundary)


def generate_openai_image_edit(
    image_bytes: bytes,
    prompt: str,
    api_key: str,
    model: str,
    base_url: str,
    timeout: int = 300,
    size: str = "1024x1024",
) -> Any:
    if not api_key.strip():
        raise ValueError("Set the image API key in the node.")
    if not model.strip() or not prompt.strip():
        raise ValueError("Image API model and prompt are required.")
    body, content_type = _multipart(
        {"model": model, "prompt": prompt, "size": size, "n": "1"},
        {"image": ("reference.png", image_bytes, "image/png")},
    )
    request = urllib.request.Request(
        _images_edit_endpoint(base_url),
        data=body,
        headers={"Authorization": "Bearer " + api_key.strip(), "Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        error.close()
        raise RuntimeError("Image API returned HTTP {}: {}".format(error.code, detail[:1200])) from None
    except urllib.error.URLError as error:
        raise RuntimeError("Image API request failed: {}".format(error.reason)) from None

    try:
        item = payload["data"][0]
        if item.get("b64_json"):
            output = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=timeout) as response:
                output = response.read()
        else:
            raise ValueError("Image API response contains neither b64_json nor url.")
        from PIL import Image
        import numpy as np
        import torch
        with Image.open(BytesIO(output)) as image:
            pixels = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
        return torch.from_numpy(pixels[None, ...])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError("Image API returned an invalid image response: {}".format(error)) from None

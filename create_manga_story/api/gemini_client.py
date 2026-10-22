"""Small, dependency-free Gemini generateContent client."""

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.schemas import GEMINI_RESPONSE_SCHEMA, validate_analysis


PROMPT_VERSION = "comic-page-analysis-v2"


def _endpoint(base_url: str, model: str, api_key: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        raise ValueError("Gemini API base URL cannot be empty.")
    if base.endswith(":generateContent"):
        endpoint = base
    else:
        if base.endswith("/v1beta"):
            api_root = base
        else:
            api_root = base + "/v1beta"
        endpoint = "{}/models/{}:generateContent".format(
            api_root, urllib.parse.quote(model.strip(), safe="/")
        )
    separator = "&" if "?" in endpoint else "?"
    return endpoint + separator + "key=" + urllib.parse.quote(api_key, safe="")


def analyze_image(
    image_path: Path,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int,
    previous_summary: str = "",
    max_retries: int = 2,
    retrieved_knowledge: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not api_key.strip():
        raise ValueError("Set GEMINI_API_KEY in the ComfyUI process environment.")
    if not model.strip():
        raise ValueError("Gemini model name cannot be empty.")
    if max_retries < 0 or max_retries > 5:
        raise ValueError("max_retries must be between 0 and 5.")
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError("Comic image does not exist: {}".format(image_path))
    mime_type = mimetypes.guess_type(str(image_path))[0]
    if mime_type not in ("image/png", "image/jpeg", "image/webp"):
        raise ValueError("Gemini analysis supports PNG, JPG, JPEG, and WEBP images.")
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = (
        "Analyze this comic page. Follow the page's visible reading order. "
        "Do not invent names or facts. Use empty strings for unknown text, "
        "and put uncertain interpretations in uncertainties. Distinguish "
        "observable facts from inferred identity or emotion. Return only the "
        "requested JSON object. Describe character relationships only when "
        "supported by visible dialogue or clear story evidence. For state_changes, "
        "report explicit changes to a character/object/location attribute (subject, "
        "attribute, value, certainty='confirmed' or 'uncertain', panel_index, confidence); "
        "do not infer changes from a static appearance. Record unresolved mysteries or "
        "unfinished goals as new_open_threads, and only clearly answered prior questions "
        "as resolved_threads."
    )
    if previous_summary.strip():
        prompt += (
            "\nContinuity memory. Treat entries marked confirmed as established story "
            "facts with the cited page as evidence; uncertain entries are hypotheses "
            "and must not be promoted to facts. Use this to interpret continuity, but "
            "do not contradict visible evidence:\n" + previous_summary.strip()
        )
    if retrieved_knowledge:
        prompt += (
            "\nRetrieved historical candidates (not identity proof; preserve "
            "uncertainty and cite no unsupported facts):\n"
            + json.dumps(retrieved_knowledge, ensure_ascii=False)
        )
    request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_data,
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_RESPONSE_SCHEMA,
        },
    }
    request = urllib.request.Request(
        _endpoint(base_url, model, api_key),
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    payload = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt >= max_retries:
                error.close()
                raise RuntimeError(
                    "Gemini API returned HTTP {}.".format(error.code)
                ) from None
            error.close()
        except urllib.error.URLError as error:
            if attempt >= max_retries:
                raise RuntimeError("Gemini API request failed: {}.".format(error.reason)) from None
        time.sleep(min(2 ** attempt, 8))
    try:
        text = "".join(
            part["text"]
            for part in payload["candidates"][0]["content"]["parts"]
            if isinstance(part.get("text"), str)
        )
        result = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError("Gemini returned a missing or invalid JSON analysis.") from None
    return validate_analysis(result)

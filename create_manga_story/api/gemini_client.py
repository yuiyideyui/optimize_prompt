"""Small, dependency-free Gemini generateContent client."""

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..core.schemas import GEMINI_RESPONSE_SCHEMA, validate_analysis


PROMPT_VERSION = "comic-page-analysis-v3-macro-scenes"


def _filter_macro_locations(result: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only reusable macro environments, not incidental background details."""
    locations = result.get("locations", [])
    if not isinstance(locations, list):
        return result
    detail_markers = (
        "corner", "角落", "桌面", "tabletop", "window sill", "窗台",
        "chair", "椅子", "weapon", "武器", "wall section", "墙面",
        "close-up detail", "局部细节", "background object", "背景物件",
        "prop", "道具", "floor detail", "地面局部",
    )
    kept = []
    for item in locations:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scene_scope", "")).strip().lower()
        text = " ".join(str(item.get(key, "")) for key in ("name", "description", "visual_state")).lower()
        if scope in {"detail", "object", "incidental", "background_fragment"}:
            continue
        if any(marker in text for marker in detail_markers):
            continue
        item["scene_scope"] = "macro"
        kept.append(item)
    # Canonicalize repeated observations before the result is persisted.  The
    # clearest occurrence is first because Gemini supplies reference_quality;
    # this keeps the analysis itself consistent with the asset builder.
    grouped = {}
    unique_locations = []
    for item in kept:
        key = str(item.get("same_location_key", "")).strip().casefold()
        if not key:
            unique_locations.append(item)
            continue
        try:
            quality = float(item.get("reference_quality", 0.0) or 0.0)
        except (TypeError, ValueError):
            quality = 0.0
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = (quality, item)
            unique_locations.append(item)
        else:
            old_quality, old_item = previous
            if quality > old_quality:
                index = unique_locations.index(old_item)
                unique_locations[index] = item
                grouped[key] = (quality, item)
    kept = unique_locations
    result["locations"] = kept
    return result


def _normalize_analysis_crop_boxes(result: Dict[str, Any], width: int, height: int) -> Dict[str, Any]:
    """Normalize Gemini crop boxes, including mixed normalized/pixel responses.

    Gemini occasionally returns x as a normalized value while returning y/width/
    height in source-image pixels. Canonicalize every crop to 0..1 before schema
    validation and persistence so downstream cropping has one coordinate contract.
    """
    if width <= 0 or height <= 0:
        return result
    for collection_name in ("characters", "locations"):
        collection = result.get(collection_name, [])
        if not isinstance(collection, list):
            continue
        for entry in collection:
            if not isinstance(entry, dict) or not isinstance(entry.get("crop_box"), dict):
                continue
            crop = entry["crop_box"]
            try:
                x = float(crop["x"])
                y = float(crop["y"])
                crop_width = float(crop["width"])
                crop_height = float(crop["height"])
                padding = float(crop.get("padding", 0.0) or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            if all(0.0 <= value <= 1.0 for value in (x, y, crop_width, crop_height)):
                continue
            # Convert each component independently. This handles mixed outputs
            # such as x=0.43 with y=91, width=10, height=13.
            pixel_x = x * width if 0.0 <= x <= 1.0 else x
            pixel_y = y * height if 0.0 <= y <= 1.0 else y
            pixel_width = crop_width * width if 0.0 < crop_width <= 1.0 else crop_width
            pixel_height = crop_height * height if 0.0 < crop_height <= 1.0 else crop_height
            left = max(0.0, min(float(width - 1), pixel_x))
            top = max(0.0, min(float(height - 1), pixel_y))
            right = max(left + 1.0, min(float(width), left + max(1.0, pixel_width)))
            bottom = max(top + 1.0, min(float(height), top + max(1.0, pixel_height)))
            normalized_padding = padding if 0.0 <= padding <= 1.0 else padding / max(width, height)
            entry["crop_box"] = {
                "x": left / width,
                "y": top / height,
                "width": (right - left) / width,
                "height": (bottom - top) / height,
                "padding": max(0.0, normalized_padding),
            }
    return result


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
    try:
        from PIL import Image
        with Image.open(image_path) as source_image:
            source_width, source_height = source_image.size
    except Exception:
        source_width = source_height = 0
    prompt = (
        "Analyze this comic page. Follow the page's visible reading order. "
        "Do not invent names or facts. Use empty strings for unknown text, "
        "and put uncertain interpretations in uncertainties. For every visible "
        "For each character, also return gender as male, female, or unknown only when "
        "the visual/story evidence supports it; never guess from a name alone. For each "
        "character and location, return a normalized crop_box with x, y, width, "
        "height in the 0..1 range, excluding unrelated panels and speech bubbles; "
        "include a small padding value when useful. If the same character or location "
        "appears more than once on this page, return every occurrence but assign a stable "
        "same_entity_key or same_location_key and compare the visible reference quality. "
        "For each occurrence return reference_quality from 0 to 1: prioritize larger, "
        "sharper, less occluded, better-lit views with clearer face, clothing, architecture, "
        "and fewer text/speech-bubble obstructions. Set is_best_reference=true only on the "
        "clearest occurrence for that same entity/location. The crop_box must tightly cover "
        "that best usable visual evidence; do not choose a tiny blurry occurrence when a "
        "clearer occurrence exists. Include reference_quality_reason. Distinguish "
        "observable facts from inferred identity or emotion. Return only the "
        "requested JSON object. Describe character relationships only when "
        "supported by visible dialogue or clear story evidence. For state_changes, "
        "report explicit changes to a character/object/location attribute (subject, "
        "attribute, value, certainty='confirmed' or 'uncertain', panel_index, confidence); "
        "do not infer changes from a static appearance. Record unresolved mysteries or "
        "unfinished goals as new_open_threads, and only clearly answered prior questions "
        "as resolved_threads. For locations, identify only macro reusable settings "
        "that matter to story or future shot continuity, such as a castle, palace, "
        "city, battlefield, island, forest, street, market, room, hall, or whole "
        "landscape. Do NOT output incidental background fragments, corners, walls, "
        "floors, furniture, props, doors, windows, object close-ups, or a tiny "
        "backdrop behind a character as locations. Every location must include "
        "scene_scope='macro'."
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
                try:
                    detail = error.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = ""
                error.close()
                raise RuntimeError(
                    "Gemini API returned HTTP {}{}".format(
                        error.code,
                        ": " + detail[:1000] if detail else ".",
                    )
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
    result = _filter_macro_locations(result)
    result = _normalize_analysis_crop_boxes(result, source_width, source_height)
    return validate_analysis(result)



def generate_structured_json(
    prompt: str,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int,
    images: Optional[Iterable[Tuple[bytes, str]]] = None,
    response_schema: Optional[Dict[str, Any]] = None,
    max_retries: int = 2,
) -> Dict[str, Any]:
    """Call Gemini with text and optional actual image bytes, then parse strict JSON."""
    if not api_key.strip():
        raise ValueError("Set GEMINI_API_KEY in the ComfyUI process environment.")
    if not model.strip() or not prompt.strip():
        raise ValueError("Gemini model and prompt are required.")
    if timeout < 1 or max_retries < 0 or max_retries > 5:
        raise ValueError("Invalid timeout or max_retries.")
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for image_bytes, mime_type in images or ():
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("Gemini image input must contain non-empty bytes.")
        if mime_type not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError("Gemini image input must be PNG, JPEG, or WEBP.")
        parts.append({"inline_data": {
            "mime_type": mime_type,
            "data": base64.b64encode(image_bytes).decode("ascii"),
        }})
    generation_config: Dict[str, Any] = {"responseMimeType": "application/json"}
    if response_schema:
        generation_config["responseSchema"] = response_schema
    request = urllib.request.Request(
        _endpoint(base_url, model, api_key),
        data=json.dumps({
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
        }).encode("utf-8"),
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
                try:
                    detail = error.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = ""
                error.close()
                raise RuntimeError(
                    "Gemini API returned HTTP {}{}".format(
                        error.code,
                        ": " + detail[:1000] if detail else ".",
                    )
                ) from None
            error.close()
        except urllib.error.URLError as error:
            if attempt >= max_retries:
                raise RuntimeError("Gemini API request failed: {}.".format(error.reason)) from None
        time.sleep(min(2 ** attempt, 8))
    try:
        candidate = payload["candidates"][0]
        if candidate.get("finishReason") in ("SAFETY", "RECITATION", "BLOCKLIST"):
            raise ValueError("Gemini declined the structured request.")
        text = "".join(
            part["text"]
            for part in candidate["content"]["parts"]
            if isinstance(part.get("text"), str)
        )
        if not text.strip():
            raise ValueError("Gemini returned an empty structured response.")
        result = json.loads(text, parse_constant=_reject_json_constant)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Gemini returned a missing or invalid structured JSON response.") from exc
    if not isinstance(result, dict):
        raise ValueError("Gemini response must be a JSON object.")
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("Gemini returned a non-finite JSON number: {}.".format(value))

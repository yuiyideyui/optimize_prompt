"""Gemini-assisted, bounded asset prompt and visual-QC workflow nodes."""

import hashlib
import io
import json
import math
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..api.gemini_client import generate_structured_json
from ..core.asset_repository import ASSET_TYPES
from ..core.workflow_repository import ComicWorkflowRepository
from .asset_nodes import _encode_comfy_images, _json_input, _repository


def _workflow_repository() -> ComicWorkflowRepository:
    asset_repository = _repository()
    asset_repository.initialize()
    workflow = ComicWorkflowRepository(asset_repository.database)
    workflow.initialize()
    return workflow


_PROMPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "prompt_version": {"type": "INTEGER"},
        "detailed_description": {"type": "STRING"},
        "reference_usage": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "reference_asset_id": {"type": "STRING"},
                    "role": {"type": "STRING"},
                    "instruction": {"type": "STRING"},
                },
                "required": ["reference_asset_id", "role", "instruction"],
            },
        },
        "must_preserve": {"type": "ARRAY", "items": {"type": "STRING"}},
        "allowed_changes": {"type": "ARRAY", "items": {"type": "STRING"}},
        "negative_constraints": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "validation_checklist": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "prompt_version", "detailed_description", "reference_usage",
        "must_preserve", "allowed_changes", "negative_constraints",
        "uncertainties", "validation_checklist",
    ],
}

_QC_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "decision": {
            "type": "STRING",
            "enum": ["PASS", "RETRY", "NEEDS_HUMAN_REVIEW", "ERROR"],
        },
        "overall_score": {"type": "NUMBER"},
        "checks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "category": {"type": "STRING"},
                    "result": {"type": "STRING", "enum": ["PASS", "FAIL", "UNCERTAIN"]},
                    "severity": {"type": "STRING"},
                    "evidence": {"type": "STRING"},
                },
                "required": ["category", "result", "severity", "evidence"],
            },
        },
        # Gemini requires an item schema for every ARRAY property.  These are
        # deliberately strings so QC can cite concise, human-readable defects.
        "failures": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "prompt_revision_instructions": {
            "type": "ARRAY", "items": {"type": "STRING"}
        },
        "reference_revision_instructions": {
            "type": "ARRAY", "items": {"type": "STRING"}
        },
        "human_review_required": {"type": "BOOLEAN"},
    },
    "required": [
        "decision", "overall_score", "checks", "failures", "uncertainties",
        "prompt_revision_instructions", "reference_revision_instructions",
        "human_review_required",
    ],
}


def _json_value(value: Any, field_name: str) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("{} must contain valid JSON.".format(field_name)) from exc
    if not isinstance(value, dict):
        raise ValueError("{} must be a JSON object.".format(field_name))
    return value


def _single_image_bytes(image: Any) -> Tuple[bytes, str]:
    encoded = _encode_comfy_images(image)
    if len(encoded) != 1:
        raise ValueError("This workflow input requires exactly one IMAGE.")
    return encoded[0]["content"], "image/png"


def _optional_image_part(image: Any) -> Optional[Tuple[bytes, str]]:
    if image is None:
        return None
    return _single_image_bytes(image)


def _formal_reference_ids(manifest: List[Dict[str, Any]]) -> List[str]:
    """Return only persisted asset IDs, excluding transient page references."""
    return [
        item["asset_id"]
        for item in manifest
        if item.get("reference_kind", "asset") == "asset"
    ]


def _validated_entity_relation(repo: Any, project_id: str, entity_type: Any, entity_id: Any):
    """Return a safe relation target; unresolved page entities stay unbound."""
    if not isinstance(entity_type, str) or not isinstance(entity_id, str):
        return None, None, "missing_entity"
    table_by_type = {
        "character": ("characters", "character_id"),
        "location": ("locations", "location_id"),
        "character_appearance": ("character_appearances", "appearance_id"),
        "location_state": ("location_states", "state_id"),
    }
    table_info = table_by_type.get(entity_type)
    if table_info is None:
        return None, None, "unsupported_entity_type"
    table, key = table_info
    with repo.database.connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM {} WHERE {} = ? AND project_id = ?".format(table, key),
            (entity_id, project_id),
        ).fetchone()
    if row is None:
        return None, None, "entity_not_found"
    return entity_type, entity_id, "valid"


def _reusable_generation(repo: Any, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Find a successful candidate generated from the same comic-page hash."""
    page_hash = request.get("source_page_hash")
    if not isinstance(page_hash, str) or not page_hash.strip():
        return None
    with repo.database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM asset_generation_runs WHERE project_id = ? "
            "AND status = 'succeeded' ORDER BY created_at DESC",
            (request.get("project_id"),),
        ).fetchall()
        for row in rows:
            request_type = str(request.get("asset_type", ""))
            if row["asset_type"] != request_type and not (
                request_type.startswith("character")
                and str(row["asset_type"]).startswith("character")
            ):
                continue
            try:
                parameters = json.loads(row["parameters_json"] or "{}")
            except json.JSONDecodeError:
                continue
            outputs = connection.execute(
                "SELECT a.* FROM asset_generation_outputs o JOIN assets a ON a.asset_id=o.asset_id "
                "WHERE o.run_id=? ORDER BY o.output_index", (row["run_id"],)
            ).fetchall()
            if not outputs:
                continue
            same_page = parameters.get("source_page_hash") == page_hash
            request_page_id = request.get("source_page_id")
            if not same_page and request_page_id:
                same_page = parameters.get("source_page_id") == request_page_id
                if not same_page:
                    for output in outputs:
                        try:
                            metadata = json.loads(output["metadata_json"] or "{}")
                        except json.JSONDecodeError:
                            continue
                        manifest = metadata.get("reference_manifest", [])
                        if isinstance(manifest, list) and any(
                            item.get("source_page_id") == request_page_id
                            for item in manifest if isinstance(item, dict)
                        ):
                            same_page = True
                            break
            if not same_page:
                continue
            if parameters.get("entity_type") != request.get("entity_type"):
                continue
            old_identity = parameters.get("identity_key")
            new_identity = request.get("identity_key")
            if old_identity and new_identity:
                if old_identity != new_identity:
                    continue
            elif parameters.get("entity_id") != request.get("entity_id"):
                continue
            return {"run_id": row["run_id"], "assets": [dict(item) for item in outputs]}
    return None


_PRIMARY_REFERENCE_DIRECTIVE = (
    "PRIMARY REFERENCE RULE: image1 is the single source of truth and the primary "
    "edit target. Preserve image1's subject identity, subject count, silhouette, "
    "composition, camera angle, spatial layout, architecture, colors, lighting, "
    "and visual style. Make only the requested refinement or completion. Do not "
    "redesign the subject, change its identity, move important elements, add "
    "unrelated people or objects, include other comic panels, or copy speech "
    "bubbles and panel borders. image2 and image3 are supplementary only and "
    "must never override image1."
)


def _qwen2_extraction_directive(request: Dict[str, Any]) -> str:
    """Constrain character generation to source-faithful extraction/cleanup."""
    asset_type = str(request.get("asset_type", ""))
    if asset_type.startswith("character"):
        return (
            "CHARACTER EXTRACTION MODE: Extract only the requested character from "
            "<image1>. Keep the exact face, hairstyle, body proportions, clothing, "
            "pose, accessories, expression, colors, and recognizable details. Remove "
            "the comic panel, speech bubbles, text, background, and unrelated people "
            "or objects. Replace the entire background with uniform pure white (#FFFFFF). "
            "AUTOMATIC FULL-BODY CHARACTER SHEET: Create one clean reference sheet "
            "showing the same character as a complete head-to-toe human figure. The "
            "EXACTLY THREE complete head-to-toe views in this left-to-right order: "
            "front view, true side profile, and back view. Show the entire body, including head, "
            "neck, shoulders, torso, arms, hands, hips, legs, feet, shoes, clothing, "
            "and accessories. Do not add extra character views or a facial-expression row. Keep "
            "the same identity, clothing, hair, proportions, anatomy, and accessories "
            "across every view. If the front, side, back, full-body, or clothing "
            "details are missing from <image1>, use the comic page and supplementary "
            "references as evidence and infer them conservatively; do not invent a new "
            "costume, body type, face, or accessories. Never output only a headshot, "
            "bust, half-body crop, or floating head. "
            "Use <image1> as the sole authority for the visual style. Preserve the exact "
            "style visible in <image1>: line art, ink weight, cel shading "
            "or painterly shading, palette, texture, proportions, facial rendering, and "
            "level of detail. Do not convert it to photorealism, 3D, western comic, anime "
            "of another style, or a different illustration style. Only improve clarity "
            "and clean edges. Do not redesign, beautify, age, recolor, or invent the "
            "character. The comic crop may be low-resolution or blurry. Automatically "
            "reconstruct and clarify obscured facial features, hair, hands, clothing edges, "
            "and missing parts using the clearest supplied references, while staying faithful "
            "to the same character and manga style. Do not merely upscale blur or copy "
            "unreadable artifacts. This is a controlled cleanup/extraction, not a new illustration."
        )
    if asset_type.startswith("scene") or asset_type.startswith("location"):
        try:
            reference_quality = float(request.get("reference_quality", 1.0))
        except (TypeError, ValueError):
            reference_quality = 1.0
        crop = request.get("crop_box")
        try:
            crop_area = float(crop.get("width", 0)) * float(crop.get("height", 0))
        except (AttributeError, TypeError, ValueError):
            crop_area = 1.0
        reconstruction = (
            reference_quality < 0.65 or crop_area < 0.06
        )
        reconstruction_text = (
            " The selected scene crop is small or unclear. Reconstruct the visible "
            "architecture and terrain conservatively from the crop plus the "
            "supplementary full-page context reference. This is an image-edit "
            "reconstruction, not a literal upscale: restore clean edges, readable "
            "forms, depth and texture while preserving the exact manga linework, "
            "palette, shading, lighting, perspective and composition. Do not invent "
            "a different castle, location, era or art style."
            if reconstruction else ""
        )
        return (
            "SCENE EXTRACTION MODE: Extract and clean the requested location from "
            "<image1>. Preserve the exact architecture, terrain, layout, camera angle, "
            "perspective, scale relationships, lighting, colors, and distinctive details. "
            "Remove every visible person and character, including background people, "
            "speech bubbles, text, comic panel borders, and unrelated objects. Do not "
            "preserve a person merely because they appear in the source panel. Use <image1> as the sole "
            "authority for the visual style. Preserve the exact style visible in <image1>: "
            "line art, ink weight, palette, shading method, texture, "
            "perspective treatment, and detail density. Do not convert it to photorealism, "
            "3D, western comic, or another illustration style. Only improve clarity and "
            "clean edges. Do not invent a new environment or change the composition. This "
            "The comic crop may be low-resolution or blurry. Automatically restore clear "
            "architecture, edges, textures, and depth from the supplied references, while "
            "preserving the exact manga design and camera composition. Do not merely upscale "
            "blur or retain unreadable text artifacts. This is a controlled scene extraction "
            "and cleanup, not a new illustration."
            + reconstruction_text
        )
    return (
        "CONTROLLED EXTRACTION MODE: Extract only the requested asset from <image1> "
        "and lightly optimize clarity. Preserve its identity, shape, composition, "
        "colors, structure, and the exact visual style visible in <image1>, including line "
        "art, shading, palette, texture, and proportions. Remove comic text and borders. "
        "Do not convert to photorealistic, 3D, or another art style. Do not create a new "
        "unrelated image or invent details."
    )


def _normalized_crop_box(value: Any) -> Optional[Dict[str, float]]:
    if not isinstance(value, dict):
        return None
    try:
        x = float(value["x"])
        y = float(value["y"])
        width = float(value["width"])
        height = float(value["height"])
        padding = float(value.get("padding", 0.0))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in (x, y, width, height, padding)):
        return None
    if width <= 0 or height <= 0 or padding < 0:
        return None
    left = max(0.0, x - padding)
    top = max(0.0, y - padding)
    right = min(1.0, x + width + padding)
    bottom = min(1.0, y + height + padding)
    if right <= left or bottom <= top:
        return None
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
        "padding": 0.0,
    }


def _crop_pil_image(image: Any, crop_box: Optional[Dict[str, float]]) -> Any:
    if crop_box is None:
        return image
    width, height = image.size
    left = max(0, min(width - 1, int(round(crop_box["x"] * width))))
    top = max(0, min(height - 1, int(round(crop_box["y"] * height))))
    right = max(left + 1, min(width, int(round((crop_box["x"] + crop_box["width"]) * width))))
    bottom = max(top + 1, min(height, int(round((crop_box["y"] + crop_box["height"]) * height))))
    return image.crop((left, top, right, bottom))


def _stack_batch_images(images: List[Any]) -> Any:
    """Return all generated ComfyUI images as one IMAGE batch tensor."""
    if not images:
        return None
    import numpy as np

    arrays = []
    use_torch = any(hasattr(image, "detach") for image in images)
    for image in images:
        array = image.detach().cpu().numpy() if hasattr(image, "detach") else np.asarray(image)
        if array.ndim == 3:
            array = array[np.newaxis, ...]
        if array.ndim != 4 or array.shape[-1] not in (1, 3, 4):
            raise ValueError("Generated IMAGE must have shape [batch,height,width,channels].")
        # ComfyUI's IMAGE convention is RGB.  Different backends can return
        # RGB, RGBA, or grayscale tensors; normalize them before padding and
        # concatenating the batch instead of dropping the whole batch.
        if array.shape[-1] == 1:
            array = np.repeat(array, 3, axis=3)
        elif array.shape[-1] == 4:
            array = array[..., :3]
        array = array.astype(np.float32, copy=False)
        arrays.append(array)
    max_height = max(array.shape[1] for array in arrays)
    max_width = max(array.shape[2] for array in arrays)
    channels = 3
    padded = []
    for array in arrays:
        if array.shape[3] != channels:
            raise ValueError("Generated IMAGE normalization failed.")
        canvas = np.zeros(
            (array.shape[0], max_height, max_width, channels), dtype=np.float32
        )
        canvas[:, :array.shape[1], :array.shape[2], :] = array
        padded.append(canvas)
    result = np.concatenate(padded, axis=0)
    if use_torch:
        import torch
        return torch.from_numpy(result)
    return result


def _load_asset_image(asset: Dict[str, Any]) -> Any:
    """Load a persisted PNG asset back into a ComfyUI IMAGE tensor."""
    import numpy as np
    import torch
    from PIL import Image

    path = _repository().resolve_asset_path(asset)
    if not path.is_file():
        raise FileNotFoundError("Cached asset file does not exist: {}".format(path))
    with Image.open(path) as image:
        pixels = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(pixels[None, ...])


def _validate_prompt_response(result: Dict[str, Any], manifest: List[Dict[str, Any]]) -> Dict[str, Any]:
    required_arrays = (
        "must_preserve", "allowed_changes", "negative_constraints",
        "uncertainties", "validation_checklist", "reference_usage",
    )
    if (
        isinstance(result.get("prompt_version"), bool)
        or not isinstance(result.get("prompt_version"), int)
        or result["prompt_version"] < 1
        or not isinstance(result.get("detailed_description"), str)
        or not result["detailed_description"].strip()
    ):
        raise ValueError("Gemini prompt response has an invalid version or empty prompt.")
    for key in required_arrays:
        if not isinstance(result.get(key), list):
            raise ValueError("Gemini prompt response field {} must be an array.".format(key))
    for key in required_arrays[:-1]:
        if any(not isinstance(item, str) for item in result[key]):
            raise ValueError("Gemini prompt response {} must contain strings.".format(key))
    expected = {item["asset_id"] for item in manifest}
    usage = result["reference_usage"]
    if any(
        not isinstance(item, dict)
        or item.get("reference_asset_id") not in expected
        or not isinstance(item.get("role"), str)
        or not isinstance(item.get("instruction"), str)
        or not item["instruction"].strip()
        for item in usage
    ):
        raise ValueError("Gemini prompt response contains invalid reference usage.")
    if {item["reference_asset_id"] for item in usage} != expected:
        raise ValueError("Gemini prompt response must explain every selected reference.")
    return result


def _validate_qc_response(result: Dict[str, Any], threshold: float) -> Dict[str, Any]:
    decision = result.get("decision")
    if decision not in ("PASS", "RETRY", "NEEDS_HUMAN_REVIEW", "ERROR"):
        raise ValueError("Gemini QC decision is invalid.")
    score = result.get("overall_score")
    if isinstance(score, str):
        try:
            score = float(score.strip().rstrip("%"))
        except ValueError:
            score = None
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        if 1 < score <= 100:
            score = score / 100.0
        result["overall_score"] = score
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
        or not 0 <= score <= 1
    ):
        raise ValueError("Gemini QC overall_score must be finite and between 0 and 1.")
    checks = result.get("checks")
    failures = result.get("failures")
    if not isinstance(checks, list) or not checks or not isinstance(failures, list):
        raise ValueError("Gemini QC must include non-empty checks and a failures array.")
    for check in checks:
        if (
            not isinstance(check, dict)
            or check.get("result") not in ("PASS", "FAIL", "UNCERTAIN")
            or not isinstance(check.get("category"), str)
            or not isinstance(check.get("severity"), str)
            or not isinstance(check.get("evidence"), str)
            or not check["evidence"].strip()
        ):
            raise ValueError("Gemini QC checks require categories and image evidence.")
    for key in (
        "uncertainties", "prompt_revision_instructions",
        "reference_revision_instructions",
    ):
        if not isinstance(result.get(key), list) or any(
            not isinstance(item, str) for item in result[key]
        ):
            raise ValueError("Gemini QC field {} must be a string array.".format(key))
    if not isinstance(result.get("human_review_required"), bool):
        raise ValueError("Gemini QC human_review_required must be boolean.")
    if decision == "PASS" and (
        score < threshold
        or failures
        or result["uncertainties"]
        or result["human_review_required"]
        or any(check["result"] != "PASS" for check in checks)
    ):
        raise ValueError("Gemini QC PASS conflicts with its checks or configured score threshold.")
    if decision == "RETRY" and (
        not failures
        or not any(check["result"] == "FAIL" for check in checks)
        or not (
            result["prompt_revision_instructions"]
            or result["reference_revision_instructions"]
        )
    ):
        raise ValueError("Gemini QC RETRY requires an evidenced failure and actionable revision.")
    if decision == "NEEDS_HUMAN_REVIEW" and not result["human_review_required"]:
        raise ValueError("NEEDS_HUMAN_REVIEW must set human_review_required=true.")
    return result


def _error_code(exc: BaseException) -> str:
    """Map local/model failures to a stable workflow error category."""
    message = str(exc).lower()
    if any(token in message for token in ("timeout", "timed out", "rate limit", "connection")):
        return "QC_API_ERROR"
    if any(token in message for token in ("model", "cuda", "out of memory", "sampl")):
        return "IMAGE_GENERATION_ERROR"
    return "QC_INVALID_RESPONSE"


class ComicAssetReferenceSelector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "references_json": ("STRING", {"multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "COMIC_ASSET_REFERENCES", "STRING")
    RETURN_NAMES = ("image1", "image2", "image3", "reference_manifest", "manifest_json")
    FUNCTION = "select"
    CATEGORY = "Comic2Anime/Assets"

    def select(self, project_id: str, asset_request: Dict[str, Any], references_json: str):
        request = _json_value(asset_request, "asset_request")
        if request.get("project_id") != project_id:
            raise ValueError("Asset request project does not match project_id.")
        unresolved = request.get("unresolved_reference_conflicts", [])
        if not isinstance(unresolved, list):
            raise ValueError("unresolved_reference_conflicts must be an array.")
        if unresolved:
            raise ValueError(
                "Resolve hard reference conflicts before selecting images: {}".format(
                    json.dumps(unresolved, ensure_ascii=False)
                )
            )
        references = _json_input(references_json, "references_json", list)
        if not references or len(references) > 3:
            raise ValueError("Select between one and three references; QwenEdit supports three.")
        if any(
            not isinstance(ref, dict)
            or not (
                isinstance(ref.get("asset_id"), str) and ref["asset_id"].strip()
                or isinstance(ref.get("source_page_id"), str)
                and ref["source_page_id"].strip()
            )
            or not isinstance(ref.get("role"), str)
            or not ref["role"].strip()
            or not isinstance(ref.get("reason", ""), str)
            for ref in references
        ):
            raise ValueError(
                "Each reference requires asset_id or source_page_id, role, and an optional reason."
            )
        ids = [
            ref.get("asset_id") or "page-source:" + ref["source_page_id"]
            for ref in references
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate reference assets are not allowed.")
        repo = _repository()
        repo.initialize()
        try:
            import numpy as np
            import torch
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Reference loading requires numpy, Pillow, and ComfyUI torch.") from exc
        images: List[Any] = []
        manifest = []
        for index, ref in enumerate(references):
            source_page_id = ref.get("source_page_id")
            if isinstance(source_page_id, str) and source_page_id.strip():
                with repo.database.connect() as connection:
                    page_row = connection.execute(
                        "SELECT page_id, project_id, image_path FROM pages "
                        "WHERE page_id = ? AND project_id = ?",
                        (source_page_id.strip(), project_id),
                    ).fetchone()
                if page_row is None:
                    raise ValueError(
                        "Source page {} does not belong to project {}.".format(
                            source_page_id, project_id
                        )
                    )
                path = Path(page_row["image_path"])
                raw = path.read_bytes()
                with Image.open(io.BytesIO(raw)) as decoded:
                    decoded.load()
                    crop_box = _normalized_crop_box(ref.get("crop_box"))
                    cropped = _crop_pil_image(decoded.convert("RGB"), crop_box)
                    pixels = np.asarray(cropped, dtype=np.float32) / 255.0
                    width, height = cropped.size
                    crop_bytes = io.BytesIO()
                    cropped.save(crop_bytes, format="PNG")
                    raw = crop_bytes.getvalue()
                images.append(torch.from_numpy(pixels[None, ...]))
                manifest.append(
                    {
                        "asset_id": "page-source:" + source_page_id.strip(),
                        "source_page_id": source_page_id.strip(),
                        "reference_kind": "page_source_crop" if crop_box else "page_source",
                        "version_hash": hashlib.sha256(raw).hexdigest(),
                        "asset_type": "panel_reference",
                        "asset_uri": str(path),
                        "status": "source_page",
                        "role": ref["role"],
                        "reason": ref.get("reason", ""),
                        "port": "image{}".format(index + 1),
                        "mime_type": "image/png",
                        "width": width,
                        "height": height,
                        "crop_box": crop_box,
                    }
                )
                # For a distant/blurry scene, image1 remains the exact crop
                # to edit, while image2 supplies page-level composition and
                # manga-style context. This prevents Qwen from mistaking a
                # tiny castle crop for an unrelated foreground object.
                asset_type = str(request.get("asset_type", ""))
                try:
                    reference_quality = float(request.get("reference_quality", 1.0))
                except (TypeError, ValueError):
                    reference_quality = 1.0
                crop_area = 1.0
                if isinstance(crop_box, dict):
                    crop_area = float(crop_box.get("width", 0.0)) * float(
                        crop_box.get("height", 0.0)
                    )
                if (
                    len(images) == 1
                    and (asset_type.startswith("scene") or asset_type.startswith("location"))
                    and (reference_quality < 0.65 or crop_area < 0.06)
                ):
                    with Image.open(io.BytesIO(path.read_bytes())) as full_page:
                        full_page.load()
                        full = full_page.convert("RGB")
                        full_pixels = np.asarray(full, dtype=np.float32) / 255.0
                        full_bytes = io.BytesIO()
                        full.save(full_bytes, format="PNG")
                    images.append(torch.from_numpy(full_pixels[None, ...]))
                    manifest.append(
                        {
                            "asset_id": "page-source-full:" + source_page_id.strip(),
                            "source_page_id": source_page_id.strip(),
                            "reference_kind": "page_source_full_context",
                            "version_hash": hashlib.sha256(full_bytes.getvalue()).hexdigest(),
                            "asset_type": "panel_reference",
                            "asset_uri": str(path),
                            "status": "source_page",
                            "role": "style_and_composition_context",
                            "reason": "Supplementary full-page context for a small or unclear scene crop.",
                            "port": "image2",
                            "mime_type": "image/png",
                            "width": full.width,
                            "height": full.height,
                            "crop_box": None,
                        }
                    )
                continue

            asset = repo.get_asset(ref["asset_id"], project_id)
            if asset["status"] != "approved":
                raise ValueError("Only approved assets can be used as formal references.")
            entity_type = request.get("entity_type")
            entity_id = request.get("entity_id")
            if (
                entity_type is None
                and isinstance(request.get("target_entity"), dict)
            ):
                entity_type = request["target_entity"].get("entity_type")
                entity_id = request["target_entity"].get("entity_id")
            compatible_entities = set()
            if isinstance(entity_type, str) and isinstance(entity_id, str):
                compatible_entities.add((entity_type, entity_id))
                with repo.database.connect() as connection:
                    if entity_type == "character_appearance":
                        parent = connection.execute(
                            """SELECT c.character_id FROM character_appearances a
                               JOIN characters c ON c.character_id = a.character_id
                               WHERE a.appearance_id = ? AND c.project_id = ?""",
                            (entity_id, project_id),
                        ).fetchone()
                        if parent:
                            compatible_entities.add(("character", parent["character_id"]))
                    elif entity_type == "location_state":
                        parent = connection.execute(
                            """SELECT location_id FROM location_states
                               WHERE state_id = ?""",
                            (entity_id,),
                        ).fetchone()
                        if parent:
                            compatible_entities.add(("location", parent["location_id"]))
            character_roles = {
                "character_identity", "face_detail", "outfit",
                "state_reference", "expression_pose", "prop_reference",
            }
            location_roles = {"scene_reference", "location_state", "environment"}
            if ref["role"] in character_roles | location_roles and compatible_entities:
                with repo.database.connect() as connection:
                    relation_rows = connection.execute(
                        """SELECT entity_type, entity_id FROM asset_relations
                           WHERE project_id = ? AND asset_id = ?""",
                        (project_id, ref["asset_id"]),
                    ).fetchall()
                relations = {
                    (row["entity_type"], row["entity_id"]) for row in relation_rows
                }
                relevant = (
                    {item for item in compatible_entities if item[0] in ("character", "character_appearance")}
                    if ref["role"] in character_roles
                    else {item for item in compatible_entities if item[0] in ("location", "location_state")}
                )
                if not relevant or not relations.intersection(relevant):
                    raise ValueError(
                        "Reference {} is not linked to the request's {}".format(
                            ref["asset_id"], ref["role"]
                        )
                    )
            declared = json.loads(asset.get("metadata_json") or "{}")
            incompatible = declared.get("incompatible_with", [])
            if not isinstance(incompatible, list):
                raise ValueError(
                    "Asset metadata incompatible_with must be a JSON array."
                )
            if any(other_id in ids for other_id in incompatible):
                raise ValueError(
                    "Selected references contain a declared incompatibility."
                )
            path = repo.resolve_asset_path(asset)
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != asset["file_hash"]:
                raise ValueError("Reference asset {} failed SHA-256 verification.".format(ref["asset_id"]))
            with Image.open(io.BytesIO(raw)) as decoded:
                decoded.load()
                pixels = np.asarray(decoded.convert("RGB"), dtype=np.float32) / 255.0
            images.append(torch.from_numpy(pixels[None, ...]))
            manifest.append(
                {
                    "asset_id": asset["asset_id"],
                    "reference_kind": "asset",
                    "version_hash": asset["file_hash"],
                    "asset_type": asset["asset_type"],
                    "asset_uri": asset["relative_path"],
                    "status": asset["status"],
                    "role": ref["role"],
                    "reason": ref.get("reason", ""),
                    "port": "image{}".format(index + 1),
                    "mime_type": asset["mime_type"],
                    "width": asset["width"],
                    "height": asset["height"],
                }
            )
        images.extend([None] * (3 - len(images)))
        return (
            images[0],
            images[1],
            images[2],
            manifest,
            json.dumps(manifest, ensure_ascii=False),
        )


class GeminiAssetPromptGenerator:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "reference_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "reference_image1": ("IMAGE",),
                "reference_image2": ("IMAGE",),
                "reference_image3": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("COMIC_ASSET_PROMPT", "STRING", "STRING")
    RETURN_NAMES = ("asset_prompt", "qwen_prompt", "prompt_report_json")
    FUNCTION = "generate"
    CATEGORY = "Comic2Anime/Assets/Gemini"

    def generate(
        self,
        asset_request: Dict[str, Any],
        reference_manifest_json: str,
        api_key: str,
        model: str,
        api_base_url: str,
        timeout_seconds: int,
        max_retries: int,
        reference_image1: Any = None,
        reference_image2: Any = None,
        reference_image3: Any = None,
    ):
        request = _json_value(asset_request, "asset_request")
        if not isinstance(request.get("request_id"), str) or not request["request_id"].strip():
            raise ValueError("Asset request requires a stable request_id.")
        if request.get("asset_type") not in ASSET_TYPES:
            raise ValueError("Asset request has an unsupported asset_type.")
        manifest = _json_input(reference_manifest_json, "reference_manifest_json", list)
        if len(manifest) > 3:
            raise ValueError("QwenEdit accepts at most three reference images.")
        sockets = (reference_image1, reference_image2, reference_image3)
        if len(manifest) != sum(image is not None for image in sockets):
            raise ValueError("Connect the actual reference IMAGE for every manifest entry.")
        parts = []
        for index, (item, image) in enumerate(zip(manifest, sockets)):
            if item.get("port") != "image{}".format(index + 1):
                raise ValueError("Reference manifest port order does not match QwenEdit.")
            if image is None:
                raise ValueError("Selected reference IMAGE is not connected to Gemini.")
            parts.append(_single_image_bytes(image))
        prompt_text = (
            "Create a detailed Qwen-Image-Edit 2511 image-edit prompt from this asset request.\n"
            "The detailed_description field MUST include:\n"
            "1. EXPLICIT VISUAL DESCRIPTION: Precise colors, composition, lighting, textures, art style\n"
            "2. REFERENCE IMAGE GUIDANCE: For each reference, state what visual elements to preserve/adapt\n"
            "3. MANGA/COMIC CONSISTENCY: Explicitly describe which comic elements must be retained\n"
            "4. SPATIAL & SCALE DETAILS: Positions, sizes, spatial relationships of key elements\n"
            "5. OUTPUT QUALITY TARGET: Clear description of the final expected image quality\n\n"
            "Use only stated evidence from images; do not invent facts. Treat reference roles "
            "separately. Do not alter established identity or source knowledge. Put unresolved "
            "facts in uncertainties. Return the required JSON.\n\n"
            "ASSET_REQUEST:\n"
            + json.dumps(request, ensure_ascii=False)
            + "\n\nREFERENCE_MANIFEST:\n"
            + json.dumps(manifest, ensure_ascii=False)
            + "\n\nQWEN_EDIT_INPUTS: image1 (primary edit target, required); "
            "image2 and image3 (optional references for style/content guidance)."
        )
        result = generate_structured_json(
            prompt_text, model, api_key, api_base_url, timeout_seconds,
            images=parts, response_schema=_PROMPT_SCHEMA, max_retries=max_retries,
        )
        result = _validate_prompt_response(result, manifest)
        result["model_name"] = model
        workflow = ComicWorkflowRepository(_repository().database)
        workflow.initialize()
        workflow.save_asset_request(request["project_id"], request)
        workflow.save_asset_iteration(
            request["request_id"], 0, result, manifest, status="prompt_ready"
        )
        return (
            result,
            result["detailed_description"],
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "prompt_version": result["prompt_version"],
                    "model_name": model,
                    "reference_asset_ids": [item["asset_id"] for item in manifest],
                    "uncertainties": result["uncertainties"],
                },
                ensure_ascii=False,
            ),
        )


class GeminiAssetVisualQC:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "generated_image": ("IMAGE",),
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "asset_prompt": ("COMIC_ASSET_PROMPT",),
                "reference_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "attempt_index": ("INT", {"default": 0, "min": 0, "max": 10}),
                "pass_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "reference_image1": ("IMAGE",),
                "reference_image2": ("IMAGE",),
                "reference_image3": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("COMIC_ASSET_QC", "STRING", "STRING")
    RETURN_NAMES = ("qc_report", "decision", "qc_report_json")
    FUNCTION = "evaluate"
    CATEGORY = "Comic2Anime/Assets/Gemini"

    def evaluate(
        self,
        generated_image: Any,
        asset_request: Dict[str, Any],
        asset_prompt: Dict[str, Any],
        reference_manifest_json: str,
        attempt_index: int,
        pass_threshold: float,
        api_key: str,
        model: str,
        api_base_url: str,
        timeout_seconds: int,
        max_retries: int,
        reference_image1: Any = None,
        reference_image2: Any = None,
        reference_image3: Any = None,
    ):
        request = _json_value(asset_request, "asset_request")
        prompt = _json_value(asset_prompt, "asset_prompt")
        manifest = _json_input(reference_manifest_json, "reference_manifest_json", list)
        output_bytes, output_mime = _single_image_bytes(generated_image)
        images = [(output_bytes, output_mime)]
        reference_sockets = (reference_image1, reference_image2, reference_image3)
        if len(manifest) != sum(image is not None for image in reference_sockets):
            raise ValueError("Connect every selected reference IMAGE to visual QC.")
        for index, (item, image) in enumerate(zip(manifest, reference_sockets)):
            if image is None:
                continue
            if item.get("port") != "image{}".format(index + 1):
                raise ValueError("QC reference port mapping does not match the manifest.")
            images.append(_single_image_bytes(image))
        instructions = (
            "Visually inspect the first attached image: it is the actual latest Qwen output. "
            "Other attached images, if any, are reference inputs. Compare the output with the "
            "asset request and checklist. Cite concrete visible evidence. Never pass based on "
            "text alone. Return PASS only when every hard constraint passes, otherwise RETRY "
            "for fixable visual issues or NEEDS_HUMAN_REVIEW for ambiguity.\\nTASK:\\n"
            + json.dumps(request, ensure_ascii=False)
            + "\\nPROMPT AND CHECKLIST:\\n"
            + json.dumps(prompt, ensure_ascii=False)
            + "\\nREFERENCE MANIFEST:\\n"
            + json.dumps(manifest, ensure_ascii=False)
        )
        try:
            report = generate_structured_json(
                instructions, model, api_key, api_base_url, timeout_seconds,
                images=images, response_schema=_QC_SCHEMA, max_retries=max_retries,
            )
            report = _validate_qc_response(report, pass_threshold)
        except (RuntimeError, ValueError) as exc:
            report = {
                "decision": "ERROR",
                "overall_score": 0.0,
                "checks": [
                    {
                        "category": "api_or_response",
                        "result": "UNCERTAIN",
                        "severity": "high",
                        "evidence": str(exc),
                    }
                ],
                "failures": [],
                "uncertainties": ["Visual QC did not complete; no pass is recorded."],
                "prompt_revision_instructions": [],
                "reference_revision_instructions": [],
                "human_review_required": True,
            }
        report["request_id"] = request.get("request_id", "")
        report["attempt_index"] = attempt_index
        report["qc_model"] = model
        workflow = ComicWorkflowRepository(_repository().database)
        workflow.initialize()
        workflow.save_asset_request(request["project_id"], request)
        iteration_status = {
            "PASS": "pass_candidate",
            "RETRY": "retry_pending",
            "NEEDS_HUMAN_REVIEW": "needs_human_review",
            "ERROR": "error",
        }[report["decision"]]
        workflow.save_asset_iteration(
            request["request_id"], attempt_index, prompt, manifest,
            report, status=iteration_status,
        )
        return report, report["decision"], json.dumps(report, ensure_ascii=False)


class ComicAssetRetryPlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "asset_prompt": ("COMIC_ASSET_PROMPT",),
                "qc_report_json": ("STRING", {"multiline": True, "default": "{}"}),
                "reference_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "attempt_index": ("INT", {"default": 0, "min": 0, "max": 10}),
                "max_generation_retries": ("INT", {"default": 3, "min": 0, "max": 5}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            }
        }

    RETURN_TYPES = ("COMIC_ASSET_PROMPT", "STRING", "STRING")
    RETURN_NAMES = ("next_asset_prompt", "next_prompt", "retry_report_json")
    FUNCTION = "plan"
    CATEGORY = "Comic2Anime/Assets/Gemini"

    def plan(
        self,
        asset_request: Dict[str, Any],
        asset_prompt: Dict[str, Any],
        qc_report_json: str,
        reference_manifest_json: str,
        attempt_index: int,
        max_generation_retries: int,
        api_key: str,
        model: str,
        api_base_url: str,
        timeout_seconds: int,
        max_retries: int,
    ):
        request = _json_value(asset_request, "asset_request")
        current = _json_value(asset_prompt, "asset_prompt")
        qc = _json_input(qc_report_json, "qc_report_json", dict)
        manifest = _json_input(
            reference_manifest_json, "reference_manifest_json", list
        )
        if (
            attempt_index < 0
            or max_generation_retries < 0
            or max_generation_retries > 5
        ):
            raise ValueError("Retry counts must be within their configured limits.")
        decision = qc.get("decision")
        if decision == "PASS":
            _workflow_repository().save_asset_iteration(
                request["request_id"], attempt_index, current, manifest, qc,
                status="pass_candidate",
            )
            return current, current.get("detailed_description", ""), json.dumps(
                {"status": "pass_candidate", "attempt_index": attempt_index}
            )
        if decision != "RETRY":
            status = "needs_human_review" if decision == "NEEDS_HUMAN_REVIEW" else "error"
            _workflow_repository().save_asset_iteration(
                request["request_id"], attempt_index, current, manifest, qc,
                status=status,
            )
            return current, current.get("detailed_description", ""), json.dumps(
                {"status": status, "reason": "QC decision is not retryable."}
            )
        if qc.get("reference_revision_instructions"):
            _workflow_repository().save_asset_iteration(
                request["request_id"], attempt_index, current, manifest, qc,
                status="needs_human_review",
            )
            return current, current.get("detailed_description", ""), json.dumps(
                {
                    "status": "needs_human_review",
                    "reason": "Select a compatible reference image before retrying.",
                    "reference_revision_instructions": qc[
                        "reference_revision_instructions"
                    ],
                },
                ensure_ascii=False,
            )
        if attempt_index >= max_generation_retries:
            _workflow_repository().save_asset_iteration(
                request["request_id"], attempt_index, current, manifest, qc,
                status="needs_human_review",
            )
            return current, current.get("detailed_description", ""), json.dumps(
                {
                    "status": "needs_human_review",
                    "reason": "Maximum generation retry count reached.",
                    "attempt_index": attempt_index,
                    "max_generation_retries": max_generation_retries,
                    "qc_failures": qc.get("failures", []),
                },
                ensure_ascii=False,
            )
        revision_prompt = (
            "Revise only the Qwen edit prompt fields needed to address these QC findings. "
            "Do not change the source task, must_preserve, allowed_changes, or negative "
            "constraints. Do not invent facts. The corrected prompt must remain specific "
            "to this asset request. Return the same structured prompt schema.\\n"
            "IMMUTABLE TASK:\\n" + json.dumps(request, ensure_ascii=False)
            + "\\nCURRENT PROMPT:\\n" + json.dumps(current, ensure_ascii=False)
            + "\\nQC FINDINGS:\\n" + json.dumps(qc, ensure_ascii=False)
        )
        revised = generate_structured_json(
            revision_prompt, model, api_key, api_base_url, timeout_seconds,
            response_schema=_PROMPT_SCHEMA, max_retries=max_retries,
        )
        revised = _validate_prompt_response(revised, manifest)
        if (
            revised["must_preserve"] != current.get("must_preserve")
            or revised["allowed_changes"] != current.get("allowed_changes")
            or revised["negative_constraints"] != current.get("negative_constraints")
        ):
            raise ValueError("Gemini revision changed immutable task constraints.")
        revised["prompt_version"] = int(current.get("prompt_version", 0)) + 1
        revised["model_name"] = model
        workflow = ComicWorkflowRepository(_repository().database)
        workflow.initialize()
        workflow.save_asset_iteration(
            request["request_id"],
            attempt_index + 1,
            revised,
            manifest,
            qc_report=qc,
            status="retry_pending",
        )
        report = {
            "status": "retry_pending",
            "attempt_index": attempt_index + 1,
            "max_generation_retries": max_generation_retries,
            "changed_prompt": revised["detailed_description"],
            "reference_revision_instructions": qc.get(
                "reference_revision_instructions", []
            ),
            "requires_reference_reselection": bool(
                qc.get("reference_revision_instructions")
            ),
        }
        return revised, revised["detailed_description"], json.dumps(report, ensure_ascii=False)


class ComicAssetWorkflowRegistrar:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "asset_prompt": ("COMIC_ASSET_PROMPT",),
                "reference_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "qc_report_json": ("STRING", {"multiline": True, "default": "{}"}),
                "attempt_index": ("INT", {"default": 0, "min": 0, "max": 10}),
                "display_name": ("STRING", {"default": ""}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 0xffffffffffffffff}),
                "qwen_parameters_json": ("STRING", {"multiline": True, "default": "{}"}),
            }
        }

    RETURN_TYPES = ("COMIC_ASSETS", "COMIC_ASSET_REPORT")
    RETURN_NAMES = ("assets", "registration_report")
    FUNCTION = "register"
    CATEGORY = "Comic2Anime/Assets"

    def register(
        self,
        image: Any,
        asset_request: Dict[str, Any],
        asset_prompt: Dict[str, Any],
        reference_manifest_json: str,
        qc_report_json: str,
        attempt_index: int,
        display_name: str,
        seed: int,
        qwen_parameters_json: str,
    ):
        request = _json_value(asset_request, "asset_request")
        prompt = _json_value(asset_prompt, "asset_prompt")
        manifest = _json_input(reference_manifest_json, "reference_manifest_json", list)
        qc = _json_input(qc_report_json, "qc_report_json", dict)
        if request.get("project_id") is None or request.get("asset_type") not in ASSET_TYPES:
            raise ValueError("Asset request lacks a valid project or asset type.")
        if qc.get("decision") not in (
            "PASS", "RETRY", "NEEDS_HUMAN_REVIEW", "ERROR"
        ):
            raise ValueError("QC report must contain an explicit decision.")
        if request.get("request_id") != qc.get("request_id"):
            raise ValueError("QC report belongs to a different asset request.")
        if qc.get("attempt_index") != attempt_index:
            raise ValueError("QC report belongs to a different iteration.")
        repo = _repository()
        repo.initialize()
        workflow = ComicWorkflowRepository(repo.database)
        workflow.initialize()
        if qc["decision"] != "PASS":
            status = {
                "RETRY": "retry_pending",
                "NEEDS_HUMAN_REVIEW": "needs_human_review",
                "ERROR": "error",
            }[qc["decision"]]
            workflow.save_asset_request(request["project_id"], request)
            workflow.save_asset_iteration(
                request["request_id"], attempt_index, prompt, manifest, qc,
                status=status,
            )
            return (
                [],
                {
                    "created": 0,
                    "assets": [],
                    "qc_decision": qc["decision"],
                    "workflow_status": status,
                    "registered": False,
                    "reason": "Only QC PASS results can be registered as candidates.",
                },
            )
        images = _encode_comfy_images(image)
        if not images:
            raise ValueError("At least one generated IMAGE is required.")
        for item in images:
            item["metadata"].update(
                {
                    "asset_request_id": request["request_id"],
                    "attempt_index": attempt_index,
                    "asset_prompt": prompt,
                    "reference_manifest": manifest,
                    "visual_qc": qc,
                }
            )
        parameters = _json_input(
            qwen_parameters_json, "qwen_parameters_json", dict
        )
        parameters["reference_manifest"] = manifest
        parameters["qc_decision"] = qc["decision"]
        run_id = "{}-{}-{}".format(
            request["request_id"], attempt_index, uuid.uuid4().hex[:12]
        )
        entity_type = request.get("entity_type")
        entity_id = request.get("entity_id")
        if entity_type is None and isinstance(request.get("target_entity"), dict):
            entity_type = request["target_entity"].get("entity_type")
            entity_id = request["target_entity"].get("entity_id")
        seed_value = None if seed < 0 else seed
        result = repo.register_batch(
            project_id=request["project_id"],
            asset_type=request["asset_type"],
            images=images,
            display_name=display_name,
            generation_run_id=run_id,
            prompt=prompt["detailed_description"],
            prompt_version=str(prompt.get("prompt_version", "")),
            model_name="Qwen-Image-Edit 2511",
            seed=seed_value,
            parameters=parameters,
            reference_asset_ids=_formal_reference_ids(manifest),
            entity_type=entity_type,
            entity_id=entity_id,
            relation_type=request["asset_type"],
        )
        iteration_status = {
            "PASS": "pass_candidate",
            "RETRY": "retry_pending",
            "NEEDS_HUMAN_REVIEW": "needs_human_review",
            "ERROR": "error",
        }[qc["decision"]]
        workflow.save_asset_request(request["project_id"], request)
        workflow.save_asset_iteration(
            request["request_id"], attempt_index, prompt, manifest, qc,
            generation_run_id=run_id, status=iteration_status,
        )
        result["report"]["qc_decision"] = qc["decision"]
        result["report"]["workflow_status"] = iteration_status
        return result["assets"], result["report"]


class GeminiQwenAssetGenerationLoop:
    @classmethod
    def INPUT_TYPES(cls):
        try:
            import folder_paths
        except ImportError:
            model_choices = ([""], [""], [""])
        else:
            model_choices = (
                folder_paths.get_filename_list("diffusion_models"),
                folder_paths.get_filename_list("text_encoders"),
                folder_paths.get_filename_list("vae"),
            )
        return {
            "required": {
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "references_json": ("STRING", {"multiline": True, "default": "[]"}),
                "unet_name": (model_choices[0],),
                "clip_name": (model_choices[1],),
                "vae_name": (model_choices[2],),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "steps": ("INT", {"default": 40, "min": 1, "max": 1000}),
                "cfg": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 100.0}),
                "max_generation_retries": ("INT", {"default": 3, "min": 0, "max": 3}),
                "pass_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0}),
                "gemini_api_key": ("STRING", {"default": ""}),
                "gemini_model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "api_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "page_storyboard_prompt": ("STRING", {"multiline": True, "default": ""}),
                "voice_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "COMIC_ASSET_WORKFLOW_REPORT", "STRING")
    RETURN_NAMES = ("latest_image", "workflow_report", "decision")
    FUNCTION = "generate"
    CATEGORY = "Comic2Anime/Assets/Gemini"

    def generate(
        self,
        asset_request: Dict[str, Any],
        references_json: str,
        unet_name: str,
        clip_name: str,
        vae_name: str,
        seed: int,
        steps: int,
        cfg: float,
        max_generation_retries: int,
        pass_threshold: float,
        gemini_api_key: str,
        gemini_model: str,
        api_base_url: str,
        timeout_seconds: int,
        api_retries: int,
        image_backend: str = "qwen",
        image_api_key: str = "",
        image_model: str = "gpt-image-1",
        image_api_base_url: str = "https://api.openai.com/v1",
        image_size: str = "1024x1024",
        qwen2_resolution: int = 0,
        qwen2_cache_device: str = "auto",
        qwen2_cache_dtype: str = "default",
        qwen2_negative_prompt: str = "",
        page_storyboard_prompt: str = "",
        voice_manifest_json: str = "[]",
    ):
        request = _json_value(asset_request, "asset_request")
        if (
            not isinstance(request.get("request_id"), str)
            or not request["request_id"].strip()
            or request.get("asset_type") not in ASSET_TYPES
            or not isinstance(request.get("project_id"), str)
        ):
            raise ValueError("Asset request must include request_id, project_id, and asset_type.")
        if max_generation_retries < 0 or max_generation_retries > 3:
            raise ValueError("max_generation_retries must be between 0 and 3.")
        if isinstance(seed, bool) or not 0 <= seed < 2**64:
            raise ValueError("seed must be an unsigned 64-bit integer.")
        _json_input(references_json, "references_json", list)
        voice_manifest = _json_input(voice_manifest_json, "voice_manifest_json", list)
        image1, image2, image3, manifest, _ = ComicAssetReferenceSelector().select(
            request["project_id"], request, references_json
        )
        qwen_references = [
            image for image in (image2, image3) if image is not None
        ]
        images_for_gemini = [
            _single_image_bytes(image) for image in (image2, image3)
            if image is not None
        ]
        qwen_references.insert(0, image1)
        images_for_gemini.insert(0, _single_image_bytes(image1))

        repo = _repository()
        repo.initialize()
        workflow = ComicWorkflowRepository(repo.database)
        workflow.initialize()
        workflow.save_asset_request(request["project_id"], request)
        prompt_task = (
            "Create a detailed Qwen-Image-Edit 2511 prompt for this asset generation. "
            "The detailed_description MUST be explicit and comprehensive:\n"
            "1. VISUAL DETAILS: Include specific colors, composition, lighting, textures, style\n"
            "2. REFERENCE GUIDANCE: For each reference image, state what to preserve or adapt\n"
            "3. MANGA ELEMENTS: Clearly state which comic/manga visual elements must remain\n"
            "4. OUTPUT CLARITY: Ensure the prompt explicitly describes the final target image\n"
            "5. SPATIAL RELATIONSHIPS: Include positions, scales, and spatial arrangements\n\n"
            "Use only confirmed request facts and evidence from images. Keep reference roles "
            "separate; explicitly state must-preserve content, allowed changes, prohibited "
            "changes, and uncertainties. Do not invent facts. Return the required JSON.\n\n"
            "ASSET_REQUEST:\n"
            + json.dumps(request, ensure_ascii=False)
            + "\n\nREFERENCE_MANIFEST:\n"
            + json.dumps(manifest, ensure_ascii=False)
            + "\n\nQWEN INPUT PORTS: image1 (primary edit target, required); "
            "image2 and image3 (optional reference images for style/content preservation).\n\n"
            + _PRIMARY_REFERENCE_DIRECTIVE
        )
        if str(request.get("asset_type", "")).startswith(("scene", "location")):
            prompt_task += (
                "\n\nSCENE-ONLY EVIDENCE RULE: The requested asset is a location/scene. "
                "Ignore character names, character appearance, actions, dialogue, "
                "and crowd descriptions found in source_evidence unless they are "
                "needed only to remove those people. Describe and generate only the "
                "requested environment. The final scene must contain zero people, "
                "characters, speech bubbles, captions, readable text, logos, "
                "watermarks, and comic panel borders."
            )
        if page_storyboard_prompt and page_storyboard_prompt.strip():
            prompt_task += (
                "\n\nCURRENT PAGE STORYBOARD CONTEXT (use only for scene/action context; "
                "do not introduce unrequested characters, props, text, or style changes):\n"
                + page_storyboard_prompt.strip()
            )
        if voice_manifest:
            prompt_task += (
                "\n\nVOICE MANIFEST (identity/context metadata only; never render audio labels "
                "or text into the image):\n" + json.dumps(voice_manifest, ensure_ascii=False)
            )
        try:
            prompt = _validate_prompt_response(
                generate_structured_json(
                    prompt_task, gemini_model, gemini_api_key, api_base_url,
                    timeout_seconds, images=images_for_gemini,
                    response_schema=_PROMPT_SCHEMA, max_retries=api_retries,
                ),
                manifest,
            )
        except (RuntimeError, ValueError) as exc:
            workflow.save_asset_iteration(
                request["request_id"], 0, {}, manifest,
                qc_report={
                    "decision": "ERROR",
                    "error": str(exc),
                    "human_review_required": True,
                },
                status="error",
            )
            raise
        prompt["detailed_description"] = (
            _PRIMARY_REFERENCE_DIRECTIVE + "\n\nGENERATED TASK:\n"
            + prompt["detailed_description"]
        )
        if (
            str(request.get("asset_type", "")).startswith("character")
            or str(request.get("asset_type", "")).startswith("scene")
            or str(request.get("asset_type", "")).startswith("location")
        ):
            prompt["detailed_description"] += (
                "\n\n" + _qwen2_extraction_directive(request)
            )
        prompt["model_name"] = gemini_model
        workflow.save_asset_iteration(
            request["request_id"], 0, prompt, manifest, status="prompt_ready"
        )
        attempts = []
        latest_image = None
        final_decision = "ERROR"
        generation_model_name = (
            image_model if image_backend == "openai_compatible"
            else "Qwen-Image-Edit 2511"
        )
        from .qwen_edit_2511 import QwenImageEditCustomNode

        for attempt_index in range(max_generation_retries + 1):
            attempt_seed = (seed + attempt_index) % (2**64)
            try:
                if image_backend == "openai_compatible":
                    from ..api.openai_image_client import generate_openai_image_edit
                    primary_bytes, _ = _single_image_bytes(qwen_references[0])
                    latest_image = generate_openai_image_edit(
                        primary_bytes,
                        prompt["detailed_description"],
                        image_api_key,
                        image_model,
                        image_api_base_url,
                        timeout_seconds,
                        image_size,
                    )
                elif image_backend == "qwen_image_2":
                    from ..api.qwen_image_21_client import (
                        compress_reference_for_qwen,
                        generate_qwen_image_21_edit,
                    )
                    qwen_generation_references = []
                    for reference_index, reference_image in enumerate(qwen_references):
                        manifest_item = manifest[reference_index]
                        if manifest_item.get("reference_kind", "asset") == "asset":
                            reference_image = compress_reference_for_qwen(
                                reference_image, target_width=1024, jpeg_quality=30
                            )
                        qwen_generation_references.append(reference_image)
                    latest_image = generate_qwen_image_21_edit(
                        image1=qwen_generation_references[0],
                        image2=qwen_generation_references[1] if len(qwen_generation_references) > 1 else None,
                        image3=qwen_generation_references[2] if len(qwen_generation_references) > 2 else None,
                        prompt=prompt["detailed_description"],
                        unet_name=unet_name,
                        clip_name=clip_name,
                        vae_name=vae_name,
                        seed=attempt_seed,
                        steps=steps,
                        cfg=cfg,
                        resolution=qwen2_resolution,
                        cache_device=qwen2_cache_device,
                        cache_dtype=qwen2_cache_dtype,
                        negative_prompt=qwen2_negative_prompt,
                    )
                else:
                    latest_image = QwenImageEditCustomNode().edit_image(
                        image1=qwen_references[0],
                        image2=qwen_references[1] if len(qwen_references) > 1 else None,
                        image3=qwen_references[2] if len(qwen_references) > 2 else None,
                        prompt=prompt["detailed_description"],
                        unet_name=unet_name,
                        clip_name=clip_name,
                        vae_name=vae_name,
                        seed=attempt_seed,
                        steps=steps,
                        cfg=cfg,
                    )[0]
            except (RuntimeError, ValueError, OSError) as exc:
                failed_run_id = "{}-{}-{}".format(
                    request["request_id"], attempt_index, uuid.uuid4().hex[:12]
                )
                repo.record_generation_failure(
                    request["project_id"],
                    failed_run_id,
                    request["asset_type"],
                    str(exc),
                    prompt=prompt["detailed_description"],
                    prompt_version=str(prompt["prompt_version"]),
                    model_name=generation_model_name,
                    seed=attempt_seed,
                    parameters={
                        "image_backend": image_backend,
                        "image_model": image_model,
                        "image_api_base_url": image_api_base_url,
                        "image_size": image_size,
                        "unet_name": unet_name,
                        "clip_name": clip_name,
                        "vae_name": vae_name,
                        "steps": steps,
                        "cfg": cfg,
                    },
                    reference_asset_ids=_formal_reference_ids(manifest),
                )
                workflow.save_asset_iteration(
                    request["request_id"], attempt_index, prompt, manifest,
                    qc_report={"decision": "ERROR", "error": str(exc)},
                    generation_run_id=failed_run_id, status="error",
                )
                raise RuntimeError(
                    "Qwen generation failed on attempt {}: {}".format(
                        attempt_index, exc
                    )
                ) from exc
            generated_bytes, _ = _single_image_bytes(latest_image)
            qc_instructions = (
                "The first attached image is the actual latest Qwen output. The other images "
                "are selected references. Evaluate the visible result against the exact task "
                "and checklist. image2 is the primary source image corresponding to image1 "
                "for this QC call; compare the generated output against it. Identity, subject "
                "count, composition, spatial layout, architecture, lighting, colors, and "
                "style must remain consistent. Any major drift is a FAIL. Cite concrete image "
                "evidence. Never PASS based on text alone. "
                "Return RETRY for fixable prompt issues; use NEEDS_HUMAN_REVIEW for ambiguity.\\n"
                "TASK:\\n" + json.dumps(request, ensure_ascii=False)
                + "\\nPROMPT:\\n" + json.dumps(prompt, ensure_ascii=False)
                + "\\nREFERENCES:\\n" + json.dumps(manifest, ensure_ascii=False)
            )
            if request.get("asset_type", "").startswith("character"):
                qc_instructions += (
                    "\\nCHARACTER SHEET QC REQUIREMENT: Verify that the output is a single "
                    "pure-white-background reference sheet with exactly three full-body "
                    "views in order: front, side, back. Reject a missing back view, extra views, or expression rows, "
                    "headshots, busts, "
                    "half-body crops, missing hands/feet, or floating heads. Check "
                    "consistency of identity, hairstyle, clothing, anatomy, proportions, "
                    "accessories, and the exact visual style from image1."
                )
            elif (
                request.get("asset_type", "").startswith("scene")
                or request.get("asset_type", "").startswith("location")
            ):
                qc_instructions += (
                    "\\nSCENE CLEANUP HARD REQUIREMENT: Verify zero visible people, "
                    "speech bubbles, captions, subtitles, logos, watermarks, readable "
                    "comic text, and panel borders. Any such artifact is an automatic "
                    "FAIL/RETRY; do not PASS."
                )
            try:
                qc = _validate_qc_response(
                    generate_structured_json(
                        qc_instructions, gemini_model, gemini_api_key, api_base_url,
                        timeout_seconds,
                        images=[(generated_bytes, "image/png")] + images_for_gemini,
                        response_schema=_QC_SCHEMA, max_retries=api_retries,
                    ),
                    pass_threshold,
                )
            except (RuntimeError, ValueError) as exc:
                qc = {
                    "decision": "ERROR",
                    "error_code": _error_code(exc),
                    "overall_score": 0.0,
                    "checks": [{
                        "category": "api_or_response",
                        "result": "UNCERTAIN",
                        "severity": "high",
                        "evidence": str(exc),
                    }],
                    "failures": [],
                    "uncertainties": ["Visual QC unavailable; image is not approved."],
                    "prompt_revision_instructions": [],
                    "reference_revision_instructions": [],
                    "human_review_required": True,
                }
            if qc["decision"] == "RETRY" and (
                qc["reference_revision_instructions"]
                or attempt_index >= max_generation_retries
            ):
                qc["decision"] = "NEEDS_HUMAN_REVIEW"
                qc["human_review_required"] = True
                qc["stop_reason"] = (
                    "Reference reselection requires human action."
                    if qc["reference_revision_instructions"]
                    else "Maximum generation retry count reached."
                )
            qc.update(
                {
                    "request_id": request["request_id"],
                    "attempt_index": attempt_index,
                    "qc_model": gemini_model,
                }
            )
            run_id = "{}-{}-{}".format(
                request["request_id"], attempt_index, uuid.uuid4().hex[:12]
            )
            encoded = _encode_comfy_images(latest_image)
            for item in encoded:
                item["metadata"].update(
                    {
                        "asset_request_id": request["request_id"],
                        "attempt_index": attempt_index,
                        "asset_prompt": prompt,
                        "reference_manifest": manifest,
                        "visual_qc": qc,
                    }
                )
            entity_type = request.get("entity_type")
            entity_id = request.get("entity_id")
            if entity_type is None and isinstance(request.get("target_entity"), dict):
                entity_type = request["target_entity"].get("entity_type")
                entity_id = request["target_entity"].get("entity_id")
            parameters = {
                "image_backend": image_backend,
                "image_model": image_model,
                "image_api_base_url": image_api_base_url,
                "image_size": image_size,
                "qwen2_resolution": qwen2_resolution,
                "qwen2_cache_device": qwen2_cache_device,
                "qwen2_cache_dtype": qwen2_cache_dtype,
                "unet_name": unet_name,
                "clip_name": clip_name,
                "vae_name": vae_name,
                "steps": steps,
                "cfg": cfg,
                "reference_manifest": manifest,
                "qc_decision": qc["decision"],
                "attempt_index": attempt_index,
                "source_page_hash": request.get("source_page_hash"),
                "source_page_id": request.get("source_page_id"),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "identity_key": request.get("identity_key"),
                "page_storyboard_prompt_attached": bool(page_storyboard_prompt.strip()),
                "voice_manifest_count": len(voice_manifest),
            }
            status = {
                "PASS": "pass_candidate",
                "RETRY": "retry_pending",
                "NEEDS_HUMAN_REVIEW": "needs_human_review",
                "ERROR": "error",
            }[qc["decision"]]
            registered = {"assets": []}
            if qc["decision"] == "PASS":
                try:
                    from ..api.seedvr2_upscaler import upscale_confirmed_image
                    latest_image = upscale_confirmed_image(latest_image)
                    encoded = _encode_comfy_images(latest_image)
                    for item in encoded:
                        item["metadata"].update(
                            {
                                "asset_request_id": request["request_id"],
                                "attempt_index": attempt_index,
                                "asset_prompt": prompt,
                                "reference_manifest": manifest,
                                "visual_qc": qc,
                                "post_qc_upscale": {
                                    "model": "seedvr2_ema_7b_sharp_fp16.safetensors",
                                    "resolution": 4096,
                                    "max_resolution": 4096,
                                    "seed": 42,
                                    "color_correction": "lab",
                                },
                            }
                        )
                    parameters["post_qc_upscale"] = {
                        "enabled": True,
                        "model": "seedvr2_ema_7b_sharp_fp16.safetensors",
                        "vae_model": "ema_vae_fp16.safetensors",
                        "resolution": 4096,
                        "max_resolution": 4096,
                        "seed": 42,
                        "color_correction": "lab",
                    }
                    registration_entity_type, registration_entity_id, relation_status = (
                        _validated_entity_relation(
                            repo, request["project_id"], entity_type, entity_id
                        )
                    )
                    if relation_status != "valid":
                        parameters["entity_relation_warning"] = (
                            "Candidate saved without entity relation: " + relation_status
                        )
                    registered = repo.register_batch(
                        project_id=request["project_id"],
                        asset_type=request["asset_type"],
                        images=encoded,
                        display_name="{} attempt {}".format(
                            request.get("generation_goal", request["asset_type"]),
                            attempt_index + 1,
                        ),
                        generation_run_id=run_id,
                        prompt=prompt["detailed_description"],
                        prompt_version=str(prompt["prompt_version"]),
                        model_name=generation_model_name,
                        seed=attempt_seed,
                        parameters=parameters,
                        reference_asset_ids=_formal_reference_ids(manifest),
                        entity_type=registration_entity_type,
                        entity_id=registration_entity_id,
                        relation_type=request["asset_type"],
                    )
                except (RuntimeError, ValueError, OSError) as exc:
                    qc["decision"] = "ERROR"
                    qc["error_code"] = "POST_QC_UPSCALE_ERROR"
                    qc["error"] = str(exc)
                    qc["human_review_required"] = True
                    qc["uncertainties"] = list(qc.get("uncertainties", [])) + [
                        "SeedVR2 post-QC clarity pass failed; no candidate was saved."
                    ]
                    status = "error"
                    repo.record_generation_failure(
                        request["project_id"],
                        run_id,
                        request["asset_type"],
                        "Post-QC SeedVR2 upscale failed: {}".format(exc),
                        prompt=prompt["detailed_description"],
                        prompt_version=str(prompt["prompt_version"]),
                        model_name=generation_model_name,
                        seed=attempt_seed,
                        parameters=parameters,
                        reference_asset_ids=_formal_reference_ids(manifest),
                    )
            else:
                repo.record_generation_failure(
                    request["project_id"],
                    run_id,
                    request["asset_type"],
                    "QC decision: {}".format(qc["decision"]),
                    prompt=prompt["detailed_description"],
                    prompt_version=str(prompt["prompt_version"]),
                    model_name="Qwen-Image-Edit 2511",
                    seed=attempt_seed,
                    parameters=parameters,
                    reference_asset_ids=_formal_reference_ids(manifest),
                )
            workflow.save_asset_iteration(
                request["request_id"], attempt_index, prompt, manifest, qc,
                generation_run_id=run_id, status=status,
            )
            attempts.append(
                {
                    "attempt_index": attempt_index,
                    "generation_run_id": run_id,
                    "asset_ids": [
                        item["asset_id"] for item in registered["assets"]
                    ],
                    "qc_report": qc,
                    "asset_status": "candidate" if qc["decision"] == "PASS" else None,
                }
            )
            final_decision = qc["decision"]
            if final_decision != "RETRY":
                break
            try:
                revision = generate_structured_json(
                    "Revise only the Qwen prompt to address this QC report. Keep must_preserve, "
                    "allowed_changes, negative_constraints, and the asset request unchanged. "
                    "Do not invent facts. Return the same JSON prompt schema.\\nTASK:\\n"
                    + json.dumps(request, ensure_ascii=False)
                    + "\\nCURRENT PROMPT:\\n"
                    + json.dumps(prompt, ensure_ascii=False)
                    + "\\nQC REPORT:\\n"
                    + json.dumps(qc, ensure_ascii=False),
                    gemini_model,
                    gemini_api_key,
                    api_base_url,
                    timeout_seconds,
                    response_schema=_PROMPT_SCHEMA,
                    max_retries=api_retries,
                )
                revised = _validate_prompt_response(revision, manifest)
                if any(
                    revised[key] != prompt[key]
                    for key in ("must_preserve", "allowed_changes", "negative_constraints")
                ):
                    raise ValueError("Gemini revision changed immutable asset requirements.")
            except (RuntimeError, ValueError) as exc:
                workflow.save_asset_iteration(
                    request["request_id"], attempt_index + 1, {},
                    manifest, qc_report={"decision": "ERROR", "error": str(exc)},
                    status="error",
                )
                final_decision = "ERROR"
                break
            revised["detailed_description"] = (
                _PRIMARY_REFERENCE_DIRECTIVE + "\n\nREVISED TASK:\n"
                + revised["detailed_description"]
            )
            if (
                image_backend == "qwen_image_2"
                or str(request.get("asset_type", "")).startswith("scene")
                or str(request.get("asset_type", "")).startswith("location")
            ):
                revised["detailed_description"] += (
                    "\n\n" + _qwen2_extraction_directive(request)
                )
            revised["prompt_version"] = int(prompt["prompt_version"]) + 1
            revised["model_name"] = gemini_model
            workflow.save_asset_iteration(
                request["request_id"], attempt_index + 1, revised, manifest,
                qc_report=qc, status="retry_pending",
            )
            prompt = revised

        final_report = {
            "request_id": request["request_id"],
            "decision": final_decision,
            "attempt_count": len(attempts),
            "max_generation_retries": max_generation_retries,
            "attempts": attempts,
            "all_saved_assets_are_candidates": True,
            "human_review_required": final_decision
            in ("NEEDS_HUMAN_REVIEW", "ERROR"),
            "quality_retry_count": max(0, len(attempts) - 1),
            "service_retry_count_per_call": api_retries,
            "stop_reason": (
                "QC_PASS"
                if final_decision == "PASS"
                else "MAX_ATTEMPTS_REACHED"
                if attempts
                and attempts[-1]["qc_report"].get("stop_reason")
                == "Maximum generation retry count reached."
                else final_decision
            ),
        }
        return latest_image, final_report, final_decision


class ComicAssetWorkflowStatus:
    """Expose persisted request/iteration state for recovery and monitoring."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "request_id": ("STRING", {"default": ""}),
            },
            "optional": {
                "project_id": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("workflow_status_json",)
    FUNCTION = "get_status"
    CATEGORY = "Comic2Anime/Assets"

    def get_status(self, request_id: str, project_id: str = ""):
        repository = _workflow_repository()
        status = repository.get_asset_workflow_request(
            request_id.strip(), project_id.strip() or None
        )
        return (json.dumps(status, ensure_ascii=False),)


class ComicAssetBatchGenerationLoop:
    """Run asset requests serially while preserving failures for later review."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "requests_json": ("STRING", {"multiline": True, "default": "[]"}),
                "unet_name": ("STRING", {"default": ""}),
                "clip_name": ("STRING", {"default": ""}),
                "vae_name": ("STRING", {"default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "steps": ("INT", {"default": 40, "min": 1, "max": 1000}),
                "cfg": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 100.0}),
                "max_generation_retries": ("INT", {"default": 3, "min": 0, "max": 3}),
                "pass_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0}),
                "gemini_api_key": ("STRING", {"default": ""}),
                "gemini_model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "api_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "page_storyboard_prompt": ("STRING", {"multiline": True, "default": ""}),
                "voice_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("batch_images", "batch_report_json", "batch_decision")
    FUNCTION = "run_batch"
    CATEGORY = "Comic2Anime/Assets"

    def run_batch(
        self,
        requests_json: str,
        unet_name: str,
        clip_name: str,
        vae_name: str,
        seed: int,
        steps: int,
        cfg: float,
        max_generation_retries: int,
        pass_threshold: float,
        gemini_api_key: str,
        gemini_model: str,
        api_base_url: str,
        timeout_seconds: int,
        api_retries: int,
        image_backend: str = "qwen",
        image_api_key: str = "",
        image_model: str = "gpt-image-1",
        image_api_base_url: str = "https://api.openai.com/v1",
        image_size: str = "1024x1024",
        qwen2_resolution: int = 0,
        qwen2_cache_device: str = "auto",
        qwen2_cache_dtype: str = "default",
        qwen2_negative_prompt: str = "",
        page_storyboard_prompt: str = "",
        voice_manifest_json: str = "[]",
    ):
        requests = _json_input(requests_json, "requests_json", list)
        if not requests:
            report = {
                "batch_decision": "NO_CHARACTERS",
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "results": [],
                "message": "This page contains no characters with usable entity IDs.",
            }
            return None, json.dumps(report, ensure_ascii=False), "NO_CHARACTERS"
        if len(requests) > 50:
            raise ValueError("A batch may contain at most 50 requests.")
        loop = GeminiQwenAssetGenerationLoop()
        results = []
        generated_images = []
        reused_in_batch = {}
        for index, item in enumerate(requests):
            if not isinstance(item, dict) or not isinstance(item.get("asset_request"), dict):
                raise ValueError(
                    "Each batch item requires an asset_request object and references array."
                )
            request = item["asset_request"]
            references = item.get("references", [])
            reuse_key = (
                request.get("project_id"), request.get("source_page_hash"),
                request.get("entity_type"), request.get("identity_key") or request.get("entity_id"),
                request.get("asset_type"),
            )
            reusable = reused_in_batch.get(reuse_key) or _reusable_generation(_repository(), request)
            if reusable is not None:
                cached_images = []
                for cached_asset in reusable["assets"]:
                    try:
                        cached_images.append(_load_asset_image(cached_asset))
                    except (OSError, ValueError, RuntimeError) as exc:
                        results.append({
                            "index": index,
                            "request_id": request.get("request_id", ""),
                            "decision": "ERROR",
                            "error_code": "CACHED_ASSET_LOAD_ERROR",
                            "error": str(exc),
                        })
                        cached_images = []
                        break
                if not cached_images:
                    continue
                output_index = len(generated_images)
                generated_images.extend(cached_images)
                results.append({
                    "index": index,
                    "request_id": request.get("request_id", ""),
                    "entity_type": request.get("entity_type", ""),
                    "entity_id": request.get("entity_id", ""),
                    "asset_request": request,
                    "decision": "REUSED",
                    "reused": True,
                    "generation_run_id": reusable["run_id"],
                    "asset_ids": [asset["asset_id"] for asset in reusable["assets"]],
                    "output_index": output_index,
                    "output_count": len(cached_images),
                    "report": {
                        "request_id": request.get("request_id", ""),
                        "decision": "REUSED",
                        "asset_status": [asset["status"] for asset in reusable["assets"]],
                        "reason": "Same comic page hash, entity, and asset type already succeeded.",
                    },
                })
                continue
            if item.get("status") == "NEEDS_REFERENCE":
                results.append({
                    "index": index,
                    "request_id": request.get("request_id", ""),
                    "entity_id": request.get("entity_id", ""),
                    "decision": "NEEDS_REFERENCE",
                    "error_code": "missing_approved_reference",
                    "error": "No approved reference asset is available for this character.",
                })
                continue
            if isinstance(references, str):
                references = json.loads(references)
            if not isinstance(references, list):
                raise ValueError("Batch references must be an array.")
            try:
                image, report, decision = loop.generate(
                    request,
                    json.dumps(references, ensure_ascii=False),
                    unet_name,
                    clip_name,
                    vae_name,
                    (seed + index) % (2**64),
                    steps,
                    cfg,
                    max_generation_retries,
                    pass_threshold,
                    gemini_api_key,
                    gemini_model,
                    api_base_url,
                    timeout_seconds,
                    api_retries,
                    image_backend=image_backend,
                    image_api_key=image_api_key,
                    image_model=image_model,
                    image_api_base_url=image_api_base_url,
                    image_size=image_size,
                    qwen2_resolution=qwen2_resolution,
                    qwen2_cache_device=qwen2_cache_device,
                    qwen2_cache_dtype=qwen2_cache_dtype,
                    qwen2_negative_prompt=qwen2_negative_prompt,
                    page_storyboard_prompt=page_storyboard_prompt,
                    voice_manifest_json=voice_manifest_json,
                )
                output_index = len(generated_images)
                if image is not None:
                    generated_images.append(image)
                if decision == "PASS":
                    found = _reusable_generation(_repository(), request)
                    if found is not None:
                        reused_in_batch[reuse_key] = found
                results.append({
                    "index": index,
                    "request_id": request.get("request_id", ""),
                    "entity_type": request.get("entity_type", ""),
                    "entity_id": request.get("entity_id", ""),
                    "asset_request": request,
                    "decision": decision,
                    "output_index": output_index if image is not None else None,
                    "report": report,
                })
            except Exception as exc:
                results.append({
                    "index": index,
                    "request_id": request.get("request_id", ""),
                    "decision": "ERROR",
                    "error_code": _error_code(exc),
                    "error": str(exc),
                })
        succeeded = sum(item["decision"] in ("PASS", "REUSED") for item in results)
        failed = sum(item["decision"] not in ("PASS", "REUSED") for item in results)
        batch_decision = "PASS" if failed == 0 else "PARTIAL_FAILURE" if succeeded else "ERROR"
        character_entities = []
        for item in results:
            if not isinstance(item, dict) or item.get("decision") not in ("PASS", "REUSED"):
                continue
            request = item.get("asset_request") if isinstance(item.get("asset_request"), dict) else {}
            entity_id = request.get("entity_id")
            if request.get("entity_type") != "character" or not entity_id:
                continue
            evidence = request.get("source_evidence")
            evidence = evidence[0] if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict) else {}
            character_entities.append({
                "character_id": str(entity_id),
                "entity_id": str(entity_id),
                "character_name": evidence.get("name") or evidence.get("identity_candidate") or request.get("identity_key", ""),
                "entity_type": "character",
                "asset_type": request.get("asset_type", ""),
                "decision": item.get("decision"),
                "output_index": item.get("output_index"),
            })
        report = {
            "batch_decision": batch_decision,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
            "output_image_count": len(generated_images),
            "character_entity_ids": sorted({item["character_id"] for item in character_entities}),
            "character_entities": character_entities,
            "voice_generation_input": {
                "source": "asset_batch_report",
                "character_entities": character_entities,
                "requires_entity_resolution": False,
            },
        }
        return _stack_batch_images(generated_images), json.dumps(report, ensure_ascii=False), batch_decision


class ChatGPTImageAssetBatchGenerationLoop(ComicAssetBatchGenerationLoop):
    """Generate the selected/cropped page references through an OpenAI-compatible API."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "requests_json": ("STRING", {"multiline": True, "default": "[]"}),
                "image_api_key": ("STRING", {"default": "", "multiline": False}),
                "image_model": ("STRING", {"default": "gpt-image-1"}),
                "image_api_base_url": ("STRING", {"default": "https://api.openai.com/v1"}),
                "image_size": (["1024x1024", "1536x1024", "1024x1536", "auto"],),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "max_generation_retries": ("INT", {"default": 3, "min": 0, "max": 3}),
                "pass_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0}),
                "gemini_api_key": ("STRING", {"default": ""}),
                "gemini_model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "api_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "page_storyboard_prompt": ("STRING", {"multiline": True, "default": ""}),
                "voice_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("batch_images", "batch_report_json", "batch_decision")
    FUNCTION = "run_chatgpt_batch"
    CATEGORY = "Comic2Anime/Assets"

    def run_chatgpt_batch(
        self,
        requests_json: str,
        image_api_key: str,
        image_model: str,
        image_api_base_url: str,
        image_size: str,
        seed: int,
        max_generation_retries: int,
        pass_threshold: float,
        gemini_api_key: str,
        gemini_model: str,
        api_base_url: str,
        timeout_seconds: int,
        api_retries: int,
        page_storyboard_prompt: str = "",
        voice_manifest_json: str = "[]",
    ):
        return self.run_batch(
            requests_json,
            "",
            "",
            "",
            seed,
            1,
            0.0,
            max_generation_retries,
            pass_threshold,
            gemini_api_key,
            gemini_model,
            api_base_url,
            timeout_seconds,
            api_retries,
            image_backend="openai_compatible",
            image_api_key=image_api_key,
            image_model=image_model,
            image_api_base_url=image_api_base_url,
            image_size=image_size,
            page_storyboard_prompt=page_storyboard_prompt,
            voice_manifest_json=voice_manifest_json,
        )


class ComicAssetImageBatchGenerationLoop(ComicAssetBatchGenerationLoop):
    """Single switch for ChatGPT Images and native Qwen Image 2.1 editing."""

    @classmethod
    def INPUT_TYPES(cls):
        try:
            import folder_paths
            diffusion = folder_paths.get_filename_list("diffusion_models")
            text = folder_paths.get_filename_list("text_encoders")
            vae = folder_paths.get_filename_list("vae")
        except ImportError:
            diffusion, text, vae = [""], [""], [""]
        return {
            "required": {
                "requests_json": ("STRING", {"multiline": True, "default": "[]"}),
                "image_backend": (["chatgpt_image", "qwen_image_2"],),
                "image_api_key": ("STRING", {"default": ""}),
                "image_model": ("STRING", {"default": "gpt-image-1"}),
                "image_api_base_url": ("STRING", {"default": "https://api.openai.com/v1"}),
                "image_size": (["1024x1024", "1536x1024", "1024x1536", "auto"],),
                "unet_name": (diffusion,),
                "clip_name": (text,),
                "vae_name": (vae,),
                "qwen2_resolution": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 32}),
                "qwen2_cache_device": (["auto", "gpu", "cpu", "off"],),
                "qwen2_cache_dtype": (["default", "int8", "int4"],),
                "qwen2_negative_prompt": ("STRING", {"default": "text, watermarks, speech bubbles, comic frame"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "steps": ("INT", {"default": 25, "min": 1, "max": 1000}),
                "cfg": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 100.0}),
                "max_generation_retries": ("INT", {"default": 3, "min": 0, "max": 3}),
                "pass_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0}),
                "gemini_api_key": ("STRING", {"default": ""}),
                "gemini_model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "api_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "page_storyboard_prompt": ("STRING", {"multiline": True, "default": ""}),
                "voice_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("batch_images", "batch_report_json", "batch_decision")
    FUNCTION = "run_switched_batch"
    CATEGORY = "Comic2Anime/Assets"

    def run_switched_batch(
        self, requests_json, image_backend, image_api_key, image_model,
        image_api_base_url, image_size, unet_name, clip_name, vae_name,
        qwen2_resolution, qwen2_cache_device, qwen2_cache_dtype,
        qwen2_negative_prompt, seed, steps, cfg, max_generation_retries,
        pass_threshold, gemini_api_key, gemini_model, api_base_url,
        timeout_seconds, api_retries,
        page_storyboard_prompt="", voice_manifest_json="[]",
    ):
        return self.run_batch(
            requests_json, unet_name, clip_name, vae_name, seed, steps, cfg,
            max_generation_retries, pass_threshold, gemini_api_key, gemini_model,
            api_base_url, timeout_seconds, api_retries,
            image_backend=("openai_compatible" if image_backend == "chatgpt_image" else "qwen_image_2"),
            image_api_key=image_api_key,
            image_model=image_model,
            image_api_base_url=image_api_base_url,
            image_size=image_size,
            qwen2_resolution=qwen2_resolution,
            qwen2_cache_device=qwen2_cache_device,
            qwen2_cache_dtype=qwen2_cache_dtype,
            qwen2_negative_prompt=qwen2_negative_prompt,
            page_storyboard_prompt=page_storyboard_prompt,
            voice_manifest_json=voice_manifest_json,
        )

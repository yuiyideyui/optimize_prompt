"""ComfyUI nodes for Comic2Anime asset lifecycle management."""

import hashlib
import io
import json
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.asset_repository import (
    ASSET_STATUSES,
    ASSET_TYPES,
    ENTITY_TYPES,
    AssetRepository,
)
def _repository() -> AssetRepository:
    return AssetRepository()


def _encode_comfy_images(image: Any) -> List[Dict[str, Any]]:
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Comic Asset Registrar requires numpy and Pillow in the ComfyUI Python environment."
        ) from exc

    if hasattr(image, "detach"):
        image = image.detach().cpu()
        if hasattr(image, "float"):
            image = image.float()
        image = image.numpy()
    array = np.asarray(image)
    if array.ndim == 3:
        array = array[np.newaxis, ...]
    if array.ndim != 4 or array.shape[0] < 1:
        raise ValueError("IMAGE must have shape [batch, height, width, channels].")
    if array.shape[1] < 1 or array.shape[2] < 1 or array.shape[3] not in (1, 3, 4):
        raise ValueError("IMAGE height, width and channel count are invalid.")
    if not np.issubdtype(array.dtype, np.number):
        raise ValueError("IMAGE values must be numeric.")
    array = array.astype(np.float32, copy=False)
    if not np.isfinite(array).all():
        raise ValueError("IMAGE cannot contain NaN or infinite values.")
    if array.min() < 0.0 or array.max() > 1.0:
        raise ValueError("IMAGE values must follow ComfyUI's [0, 1] convention.")

    encoded = []
    for index, frame in enumerate(array):
        pixels = np.rint(frame * 255.0).astype(np.uint8)
        if pixels.shape[2] == 1:
            pixels = pixels[:, :, 0]
        output = io.BytesIO()
        Image.fromarray(pixels).save(output, format="PNG")
        encoded.append(
            {
                "content": output.getvalue(),
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "mime_type": "image/png",
                "metadata": {"output_index": index},
            }
        )
    return encoded


def _json_input(value: str, field_name: str, expected: type) -> Any:
    try:
        result = json.loads(
            value or ("[]" if expected is list else "{}"),
            parse_constant=_raise_invalid_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("{} must contain valid JSON.".format(field_name)) from exc
    if not isinstance(result, expected):
        raise ValueError(
            "{} must be a JSON {}.".format(
                field_name, "array" if expected is list else "object"
            )
        )
    return result


def _raise_invalid_json_constant(constant: str) -> None:
    raise ValueError("Unsupported JSON constant: {}".format(constant))


def _as_string_list(value: Any) -> List[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError("asset_ids must be a JSON array of non-empty strings.")
    if len(value) != len(set(value)):
        raise ValueError("asset_ids cannot contain duplicate IDs.")
    return value


def _automatic_run_id(
    project_id: str,
    asset_type: str,
    display_name: str,
    prompt: str,
    prompt_version: str,
    model_name: str,
    model_version: str,
    seed: Optional[int],
    parameters: Dict[str, Any],
    reference_asset_ids: Sequence[str],
    entity_type: Optional[str],
    entity_id: Optional[str],
    relation_type: str,
    supersedes_asset_id: Optional[str],
    images: Sequence[Dict[str, Any]],
) -> str:
    identity = {
        "project_id": project_id,
        "asset_type": asset_type,
        "display_name": display_name,
        "prompt": prompt,
        "prompt_version": prompt_version,
        "model_name": model_name,
        "model_version": model_version,
        "seed": seed,
        "parameters": parameters,
        "reference_asset_ids": list(reference_asset_ids),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "relation_type": relation_type,
        "supersedes_asset_id": supersedes_asset_id,
        "output_hashes": [
            hashlib.sha256(image["content"]).hexdigest() for image in images
        ],
    }
    serialized = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "auto-{}".format(hashlib.sha256(serialized.encode("utf-8")).hexdigest())


class ComicAssetRequestBuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "entity_type": (ENTITY_TYPES,),
                "entity_id": ("STRING", {"default": ""}),
                "asset_type": (ASSET_TYPES,),
                "generation_goal": ("STRING", {"default": "reference image"}),
                "include_source_evidence": ("BOOLEAN", {"default": True}),
                "allow_unapproved_references": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "reference_asset_ids": (
                    "STRING",
                    {"multiline": True, "default": "[]"},
                ),
                "prompt_override": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("COMIC_ASSET_REQUEST", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("asset_request", "prompt", "reference_asset_ids", "warnings")
    FUNCTION = "build_request"
    CATEGORY = "Comic2Anime/Assets"

    def build_request(
        self,
        project_id: str,
        entity_type: str,
        entity_id: str,
        asset_type: str,
        generation_goal: str,
        include_source_evidence: bool,
        allow_unapproved_references: bool,
        reference_asset_ids: str = "[]",
        prompt_override: str = "",
    ) -> Tuple[Dict[str, Any], str, str, str]:
        if not project_id.strip() or not entity_id.strip():
            raise ValueError("project_id and entity_id are required.")
        if asset_type not in ASSET_TYPES:
            raise ValueError("Unsupported asset_type: {}".format(asset_type))
        repo = _repository()
        repo.initialize()
        context = repo.get_entity_context(project_id, entity_type, entity_id)
        explicit_ids = _as_string_list(_json_input(reference_asset_ids, "reference_asset_ids", list))
        references = repo.get_reference_assets(
            project_id,
            entity_type,
            entity_id,
            explicit_ids=explicit_ids or None,
            allow_unapproved=allow_unapproved_references,
        )
        if not explicit_ids and entity_type == "character_appearance":
            parent_references = repo.get_reference_assets(
                project_id,
                "character",
                context["character_id"],
                allow_unapproved=allow_unapproved_references,
            )
            references = parent_references + references
        elif not explicit_ids and entity_type == "location_state":
            parent_references = repo.get_reference_assets(
                project_id,
                "location",
                context["location_id"],
                allow_unapproved=allow_unapproved_references,
            )
            references = parent_references + references
        unique_references = []
        seen_reference_ids = set()
        for reference in references:
            if reference["asset_id"] not in seen_reference_ids:
                seen_reference_ids.add(reference["asset_id"])
                unique_references.append(reference)
        references = unique_references
        warnings: List[str] = []
        appearance_variants = context.get("appearance_variants", [])
        if entity_type == "character" and len(appearance_variants) > 1:
            variants = {
                (item.get("appearance_description", ""), item.get("clothing", ""))
                for item in appearance_variants
            }
            if len(variants) > 1:
                warnings.append(
                    "This character has multiple appearance variants; choose a specific "
                    "character_appearance for clothing-specific assets."
                )
        location_variants = context.get("location_state_variants", [])
        if entity_type == "location" and len(location_variants) > 1:
            states = {
                (item.get("description", ""), item.get("visual_state", ""))
                for item in location_variants
            }
            if len(states) > 1:
                warnings.append(
                    "This location has multiple visual states; choose a specific "
                    "location_state for state-specific assets."
                )
        stable_description = context.get("stable_description", "")
        details = [
            context.get("description", ""),
            context.get("clothing", ""),
            context.get("visual_state", ""),
            stable_description,
        ]
        details = [item.strip() for item in details if isinstance(item, str) and item.strip()]
        entity_name = context.get("name", entity_id)
        suggested_prompt = "{} for {}.".format(
            generation_goal.strip() or asset_type.replace("_", " "), entity_name
        )
        if details:
            suggested_prompt += " Preserve established details: " + "; ".join(details)
        prompt = prompt_override.strip() or suggested_prompt
        evidence = context.get("source_evidence", []) if include_source_evidence else []
        request = {
            "schema_version": "1.0",
            "request_id": str(uuid.uuid4()),
            "project_id": project_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "asset_type": asset_type,
            "generation_goal": generation_goal,
            "prompt": prompt,
            "reference_asset_ids": [item["asset_id"] for item in references],
            "source_evidence": evidence,
            "warnings": warnings,
        }
        return (
            request,
            prompt,
            json.dumps(request["reference_asset_ids"], ensure_ascii=False),
            json.dumps(warnings, ensure_ascii=False),
        )


class ComicAssetRegistrar:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "project_id": ("STRING", {"default": ""}),
                "asset_type": (("",) + ASSET_TYPES, {"default": ""}),
                "display_name": ("STRING", {"default": ""}),
                "review_status": (("candidate",), {"default": "candidate"}),
            },
            "optional": {
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "entity_type": (("none",) + ENTITY_TYPES, {"default": "none"}),
                "entity_id": ("STRING", {"default": ""}),
                "generation_run_id": ("STRING", {"default": ""}),
                "relation_type": ("STRING", {"default": ""}),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "prompt_version": ("STRING", {"default": ""}),
                "model_name": ("STRING", {"default": ""}),
                "model_version": ("STRING", {"default": ""}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 0xffffffffffffffff}),
                "parameters_json": ("STRING", {"multiline": True, "default": "{}"}),
                "reference_asset_ids": ("STRING", {"multiline": True, "default": "[]"}),
                "supersedes_asset_id": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("IMAGE", "COMIC_ASSETS", "COMIC_ASSET_REPORT")
    RETURN_NAMES = ("image", "assets", "save_report")
    FUNCTION = "register"
    CATEGORY = "Comic2Anime/Assets"

    def register(
        self,
        image: Any,
        project_id: str,
        asset_type: str,
        display_name: str,
        review_status: str = "candidate",
        asset_request: Optional[Dict[str, Any]] = None,
        entity_type: str = "none",
        entity_id: str = "",
        generation_run_id: str = "",
        relation_type: str = "",
        prompt: str = "",
        prompt_version: str = "",
        model_name: str = "",
        model_version: str = "",
        seed: int = -1,
        parameters_json: str = "{}",
        reference_asset_ids: str = "[]",
        supersedes_asset_id: str = "",
    ):
        if review_status != "candidate":
            raise ValueError("New generated assets must be registered as candidates.")
        if asset_request:
            if (
                not isinstance(asset_request, dict)
                or asset_request.get("schema_version") != "1.0"
            ):
                raise ValueError("asset_request has an unsupported schema.")
            if asset_request.get("project_id") != project_id:
                raise ValueError("Asset request project does not match project_id.")
            request_type = asset_request.get("asset_type")
            if request_type not in ASSET_TYPES:
                raise ValueError("Asset request contains an invalid asset_type.")
            if asset_type and asset_type != request_type:
                raise ValueError(
                    "asset_type does not match the connected asset request."
                )
            asset_type = request_type
            request_entity_type = asset_request.get("entity_type")
            request_entity_id = asset_request.get("entity_id")
            if (
                request_entity_type not in ENTITY_TYPES
                or not isinstance(request_entity_id, str)
                or not request_entity_id
            ):
                raise ValueError("Asset request contains an invalid entity.")
            if entity_type != "none" and entity_type != request_entity_type:
                raise ValueError(
                    "entity_type does not match the connected asset request."
                )
            if entity_id and entity_id != request_entity_id:
                raise ValueError(
                    "entity_id does not match the connected asset request."
                )
            if not prompt:
                prompt = asset_request.get("prompt", "")
            if reference_asset_ids == "[]":
                request_references = _as_string_list(
                    asset_request.get("reference_asset_ids", [])
                )
                reference_asset_ids = json.dumps(
                    request_references, ensure_ascii=False
                )
            if entity_type == "none":
                entity_type = request_entity_type
            if not entity_id:
                entity_id = request_entity_id
            request_warnings = asset_request.get("warnings", [])
            if not isinstance(request_warnings, list) or any(
                not isinstance(warning, str) for warning in request_warnings
            ):
                raise ValueError("asset_request warnings must be a string list.")
        elif not asset_type:
            raise ValueError("Select an asset_type or connect an asset_request.")
        if asset_type not in ASSET_TYPES:
            raise ValueError("Unsupported asset_type: {}".format(asset_type))
        explicit_run_id = generation_run_id.strip()
        run_id = explicit_run_id or str(uuid.uuid4())
        repo = _repository()
        parameters = _json_input(parameters_json, "parameters_json", dict)
        references = _as_string_list(
            _json_input(reference_asset_ids, "reference_asset_ids", list)
        )
        initialized = False
        try:
            repo.initialize()
            initialized = True
            images = _encode_comfy_images(image)
            if not explicit_run_id:
                run_id = _automatic_run_id(
                    project_id=project_id,
                    asset_type=asset_type,
                    display_name=display_name,
                    prompt=prompt,
                    prompt_version=prompt_version,
                    model_name=model_name,
                    model_version=model_version,
                    seed=None if seed < 0 else seed,
                    parameters=parameters,
                    reference_asset_ids=references,
                    entity_type=None if entity_type == "none" else entity_type,
                    entity_id=entity_id or None,
                    relation_type=relation_type or asset_type,
                    supersedes_asset_id=supersedes_asset_id or None,
                    images=images,
                )
            result = repo.register_batch(
                project_id=project_id,
                asset_type=asset_type,
                images=images,
                display_name=display_name,
                generation_run_id=run_id,
                prompt=prompt,
                prompt_version=prompt_version,
                model_name=model_name,
                model_version=model_version or None,
                seed=None if seed < 0 else seed,
                parameters=parameters,
                reference_asset_ids=references,
                entity_type=None if entity_type == "none" else entity_type,
                entity_id=entity_id or None,
                relation_type=relation_type,
                supersedes_asset_id=supersedes_asset_id or None,
            )
        except Exception as exc:
            if initialized:
                try:
                    repo.record_generation_failure(
                        project_id,
                        run_id,
                        asset_type,
                        str(exc),
                        prompt=prompt,
                        prompt_version=prompt_version,
                        model_name=model_name,
                        model_version=model_version or None,
                        seed=None if seed < 0 else seed,
                        parameters=parameters,
                        reference_asset_ids=references,
                    )
                except Exception as record_error:
                    raise RuntimeError(
                        "Asset generation failed and its failure record could not "
                        "be persisted: {}".format(record_error)
                    ) from exc
            raise
        result["report"]["run_id"] = result["run_id"]
        if asset_request:
            result["report"]["warnings"].extend(
                asset_request.get("warnings", [])
            )
        return image, result["assets"], result["report"]


class ComicAssetQuery:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "status_filter": (("approved",) + tuple(
                    status for status in ASSET_STATUSES if status != "approved"
                ) + ("all",), {"default": "approved"}),
                "primary_only": ("BOOLEAN", {"default": False}),
                "limit": ("INT", {"default": 100, "min": 1, "max": 500}),
            },
            "optional": {
                "entity_type": (("none",) + ENTITY_TYPES, {"default": "none"}),
                "entity_id": ("STRING", {"default": ""}),
                "asset_type": (("all",) + ASSET_TYPES, {"default": "all"}),
            },
        }

    RETURN_TYPES = ("COMIC_ASSETS", "STRING", "COMIC_ASSET_REPORT")
    RETURN_NAMES = ("assets", "assets_json", "query_report")
    FUNCTION = "query"
    CATEGORY = "Comic2Anime/Assets"

    def query(
        self,
        project_id: str,
        status_filter: str,
        primary_only: bool,
        limit: int,
        entity_type: str = "none",
        entity_id: str = "",
        asset_type: str = "all",
    ):
        repo = _repository()
        repo.initialize()
        assets = repo.query_assets(
            project_id,
            entity_type=None if entity_type == "none" else entity_type,
            entity_id=entity_id or None,
            asset_type=None if asset_type == "all" else asset_type,
            statuses=None if status_filter == "all" else (status_filter,),
            primary_only=primary_only,
            limit=limit,
        )
        report = {"count": len(assets), "warnings": []}
        return assets, json.dumps(assets, ensure_ascii=False), report


class ComicAssetLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "asset_ids": ("STRING", {"multiline": True, "default": "[]"}),
                "allow_unapproved": ("BOOLEAN", {"default": False}),
                "failure_policy": (("error", "skip"), {"default": "error"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "COMIC_ASSET_REPORT")
    RETURN_NAMES = ("image", "loaded_asset_ids", "load_report")
    FUNCTION = "load_assets"
    CATEGORY = "Comic2Anime/Assets"

    def load_assets(
        self,
        project_id: str,
        asset_ids: str,
        allow_unapproved: bool = False,
        failure_policy: str = "error",
    ):
        if failure_policy not in ("error", "skip"):
            raise ValueError("failure_policy must be error or skip.")
        try:
            import numpy as np
            import torch
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "Comic Asset Loader requires numpy, Pillow and ComfyUI's torch."
            ) from exc
        ids = _as_string_list(_json_input(asset_ids, "asset_ids", list))
        repo = _repository()
        repo.initialize()
        assets = repo.load_asset_records(project_id, ids, allow_unapproved)
        loaded_arrays = []
        loaded_ids = []
        warnings = []
        target_size = None
        for asset in assets:
            path = repo.resolve_asset_path(asset)
            try:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != asset["file_hash"]:
                    warnings.append(
                        "Hash mismatch for asset {}.".format(asset["asset_id"])
                    )
                with Image.open(io.BytesIO(data)) as decoded:
                    decoded.load()
                    pixels = np.asarray(decoded.convert("RGB"), dtype=np.float32) / 255.0
                if target_size is None:
                    target_size = pixels.shape[:2]
                elif pixels.shape[:2] != target_size:
                    raise ValueError(
                        "Batch dimensions differ for asset {}.".format(asset["asset_id"])
                    )
            except (OSError, ValueError) as exc:
                if failure_policy == "error":
                    raise ValueError(
                        "Could not load asset {}: {}".format(asset["asset_id"], exc)
                    ) from exc
                warnings.append(
                    "Skipped asset {}: {}".format(asset["asset_id"], exc)
                )
                continue
            loaded_arrays.append(np.ascontiguousarray(pixels))
            loaded_ids.append(asset["asset_id"])
        if not loaded_arrays:
            detail = "; ".join(warnings)
            raise ValueError(
                "No assets could be loaded."
                if not detail
                else "No assets could be loaded. " + detail
            )
        tensor = torch.from_numpy(np.stack(loaded_arrays, axis=0))
        report = {
            "requested": len(ids),
            "loaded": len(loaded_ids),
            "warnings": warnings,
        }
        return tensor, json.dumps(loaded_ids, ensure_ascii=False), report


class ComicAssetReview:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "asset_id": ("STRING", {"default": ""}),
                "review_status": (("approved", "rejected", "needs_revision"),),
                "review_notes": ("STRING", {"multiline": True, "default": ""}),
                "set_primary": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "entity_type": (("none",) + ENTITY_TYPES, {"default": "none"}),
                "entity_id": ("STRING", {"default": ""}),
                "relation_type": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("COMIC_ASSET", "COMIC_ASSET_REPORT")
    RETURN_NAMES = ("asset", "review_report")
    FUNCTION = "review"
    CATEGORY = "Comic2Anime/Assets"

    def review(
        self,
        project_id: str,
        asset_id: str,
        review_status: str,
        review_notes: str,
        set_primary: bool,
        entity_type: str = "none",
        entity_id: str = "",
        relation_type: str = "",
    ):
        repo = _repository()
        repo.initialize()
        asset = repo.review_asset(
            project_id,
            asset_id,
            review_status,
            review_notes,
            set_primary=set_primary,
            entity_type=None if entity_type == "none" else entity_type,
            entity_id=entity_id or None,
            relation_type=relation_type,
        )
        return asset, {
            "asset_id": asset_id,
            "review_status": review_status,
            "set_primary": set_primary,
        }

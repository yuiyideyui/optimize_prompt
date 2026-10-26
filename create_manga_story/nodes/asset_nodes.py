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


def _normalized_entry_crop(crop_box: Any, page: Optional[Dict[str, Any]]) -> Any:
    """Accept Gemini crop boxes in normalized or source-image pixel units."""
    if not isinstance(crop_box, dict):
        return crop_box
    try:
        values = [float(crop_box[key]) for key in ("x", "y", "width", "height")]
    except (KeyError, TypeError, ValueError):
        return crop_box
    if max(abs(value) for value in values) <= 1.0:
        return crop_box
    if not isinstance(page, dict):
        return crop_box
    try:
        page_width = float(page.get("width", 0))
        page_height = float(page.get("height", 0))
    except (TypeError, ValueError):
        return crop_box
    if page_width <= 0 or page_height <= 0:
        return crop_box
    # Gemini may mix normalized and pixel components. Convert each component
    # independently so old cached analyses remain usable as well.
    x = values[0] * page_width if 0.0 <= values[0] <= 1.0 else values[0]
    y = values[1] * page_height if 0.0 <= values[1] <= 1.0 else values[1]
    width = values[2] * page_width if 0.0 < values[2] <= 1.0 else values[2]
    height = values[3] * page_height if 0.0 < values[3] <= 1.0 else values[3]
    left = max(0.0, min(page_width - 1.0, x))
    top = max(0.0, min(page_height - 1.0, y))
    right = max(left + 1.0, min(page_width, left + max(1.0, width)))
    bottom = max(top + 1.0, min(page_height, top + max(1.0, height)))
    padding = float(crop_box.get("padding", 0.0) or 0.0)
    if padding > 1.0:
        padding /= max(page_width, page_height)
    return {
        "x": left / page_width,
        "y": top / page_height,
        "width": (right - left) / page_width,
        "height": (bottom - top) / page_height,
        "padding": max(0.0, padding),
    }


def _gemini_reference_quality(entry: Dict[str, Any]) -> float:
    """Rank repeated occurrences using Gemini's visual-quality decision."""
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    for container in (entry, source):
        for key in ("reference_quality", "clarity_score", "visual_quality"):
            try:
                value = float(container.get(key))
            except (TypeError, ValueError):
                continue
            if 0.0 <= value <= 1.0:
                return value
    for container in (entry, source):
        if container.get("is_best_reference") is True:
            return 1.0
    crop = entry.get("crop_box") or source.get("crop_box")
    area = 0.0
    if isinstance(crop, dict):
        try:
            area = max(0.0, float(crop.get("width", 0))) * max(0.0, float(crop.get("height", 0)))
        except (TypeError, ValueError):
            area = 0.0
    detail = sum(bool(entry.get(key) or source.get(key)) for key in (
        "appearance", "clothing", "description", "visual_state", "action"
    ))
    confidence = float(entry.get("confidence", 0.0) or 0.0)
    return min(0.99, 0.35 + min(area, 1.0) * 0.35 + detail * 0.05 + confidence * 0.25)


def _preserve_source_order_with_best_references(
    entries: List[Dict[str, Any]], entity_type: str
) -> List[Dict[str, Any]]:
    """Preserve Gemini order; use quality only within duplicate groups."""
    groups: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
    group_order: List[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
        same_key = str(
            entry.get("same_entity_key", entry.get("same_location_key", ""))
        ).strip()
        matched_id = str(entry.get("matched_entity_id", "")).strip()
        raw_name = (
            source.get("identity_candidate") or source.get("name")
            or entry.get("identity_candidate") or entry.get("name") or ""
        )
        normalized_name = "".join(char.casefold() for char in str(raw_name) if char.isalnum())
        if same_key:
            group_key = "same:" + same_key.casefold()
        elif matched_id:
            group_key = "id:" + matched_id
        elif entity_type in ("character", "location") and normalized_name:
            group_key = "name:" + normalized_name
        else:
            group_key = "index:{}".format(index)
        if group_key not in groups:
            groups[group_key] = []
            group_order.append(group_key)
        groups[group_key].append((index, entry))
    ordered: List[Dict[str, Any]] = []
    for group_key in group_order:
        group = groups[group_key]
        group.sort(key=lambda pair: (-_gemini_reference_quality(pair[1]), pair[0]))
        ordered.extend(entry for _, entry in group)
    return ordered


def _is_macro_location(entry: Dict[str, Any]) -> bool:
    """Reject incidental location fragments before creating image requests."""
    scope = str(entry.get("scene_scope", "")).strip().lower()
    if scope in {"detail", "object", "incidental", "background_fragment"}:
        return False
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    text = " ".join(
        str(container.get(key, ""))
        for container in (entry, source)
        for key in ("name", "description", "visual_state")
    ).lower()
    return not any(marker in text for marker in (
        "corner", "角落", "桌面", "tabletop", "window sill", "窗台",
        "chair", "椅子", "weapon", "武器", "wall section", "墙面",
        "close-up detail", "局部细节", "background object", "背景物件",
        "prop", "道具", "floor detail", "地面局部",
    ))


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


class ComicAssetAutoTargetBuilder:
    """Build an asset request from confirmed entity resolution and approved refs.

    The node intentionally refuses ambiguous or unconfirmed entities. This
    keeps automatic generation deterministic without turning an LLM guess into
    an entity or reference-asset selection.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "entity_resolution": ("COMIC_ENTITY_RESOLUTION",),
                "asset_type": (ASSET_TYPES,),
                "generation_goal": ("STRING", {"default": "reference image"}),
                "minimum_match_confidence": (
                    "FLOAT", {"default": 0.75, "min": 0.0, "max": 1.0}
                ),
            }
        }

    RETURN_TYPES = ("COMIC_ASSET_REQUEST", "STRING", "STRING")
    RETURN_NAMES = ("asset_request", "approved_references_json", "target_report_json")
    FUNCTION = "build_auto_request"
    CATEGORY = "Comic2Anime/Assets"

    def build_auto_request(
        self,
        project_id: str,
        entity_resolution: Dict[str, Any],
        asset_type: str,
        generation_goal: str,
        minimum_match_confidence: float = 0.75,
    ):
        if isinstance(entity_resolution, str):
            try:
                entity_resolution = json.loads(entity_resolution)
            except json.JSONDecodeError as exc:
                raise ValueError("entity_resolution must contain valid JSON.") from exc
        if not isinstance(entity_resolution, dict):
            raise ValueError("entity_resolution must be a JSON object.")
        if not project_id.strip():
            raise ValueError("project_id is required.")
        if asset_type not in ASSET_TYPES:
            raise ValueError("Unsupported asset_type: {}".format(asset_type))
        if not 0.0 <= minimum_match_confidence <= 1.0:
            raise ValueError("minimum_match_confidence must be between 0 and 1.")

        candidates = []
        pending_candidates = []
        for entity_type, key in (("character", "characters"), ("location", "locations")):
            entries = entity_resolution.get(key, [])
            if not isinstance(entries, list):
                raise ValueError("entity_resolution.{} must be an array.".format(key))
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                confidence = float(entry.get("confidence", 0.0))
                base = {
                        "entity_type": entity_type,
                        "entity_id": entry.get("entity_id", ""),
                        "matched_entity_id": entry.get("matched_entity_id", ""),
                        "confidence": confidence,
                        "source_index": entry.get("source_index"),
                        "source": entry.get("source", {}),
                    }
                if (
                    entry.get("status") == "confirmed_match"
                    and isinstance(base["entity_id"], str)
                    and base["entity_id"].strip()
                ):
                    base["selection_status"] = "confirmed_match"
                    candidates.append(base)
                elif (
                    entry.get("status") == "pending_review"
                    and isinstance(base["matched_entity_id"], str)
                    and base["matched_entity_id"].strip()
                    and confidence >= minimum_match_confidence
                ):
                    base["entity_id"] = base["matched_entity_id"]
                    base["selection_status"] = "unique_pending_match"
                    pending_candidates.append(base)
        if not candidates:
            candidates = pending_candidates
        if not candidates:
            raise ValueError(
                "No confirmed or unique high-confidence matched entity is available "
                "for automatic asset generation; human review is required."
            )
        candidates.sort(
            key=lambda item: (-item["confidence"], item["entity_type"], item["entity_id"])
        )
        selected = candidates[0]
        if len(candidates) > 1 and candidates[1]["confidence"] == selected["confidence"]:
            raise ValueError(
                "Automatic entity selection is ambiguous; multiple confirmed entities "
                "have the same confidence."
            )

        repo = _repository()
        repo.initialize()
        assets = repo.get_reference_assets(
            project_id, selected["entity_type"], selected["entity_id"]
        )
        if not assets:
            raise ValueError(
                "No approved reference assets are linked to confirmed entity {}.".format(
                    selected["entity_id"]
                )
            )
        assets = assets[:3]
        role = "character_identity" if selected["entity_type"] == "character" else "scene_reference"
        references = [
            {
                "asset_id": asset["asset_id"],
                "role": role,
                "reason": "Automatically selected approved reference for confirmed entity.",
            }
            for asset in assets
        ]
        request = {
            "schema_version": "1.0",
            "request_id": str(uuid.uuid4()),
            "project_id": project_id,
            "entity_type": selected["entity_type"],
            "entity_id": selected["entity_id"],
            "asset_type": asset_type,
            "generation_goal": generation_goal,
            "reference_asset_ids": [item["asset_id"] for item in references],
            "source_evidence": [selected["source"]],
            "warnings": (
                []
                if selected["selection_status"] == "confirmed_match"
                else [
                    "Automatic target selection used a unique pending match with "
                    "sufficient confidence. Review the generated candidate before approval."
                ]
            ),
        }
        report = {
            "selection_policy": "highest_confidence_confirmed_entity",
            "selected_entity": selected,
            "approved_reference_asset_ids": request["reference_asset_ids"],
            "candidate_count": len(candidates),
        }
        return (
            request,
            json.dumps(references, ensure_ascii=False),
            json.dumps(report, ensure_ascii=False),
        )


class ComicAssetAutoTargetBatchBuilder:
    """Create one automatic asset request for every page character/location.

    Entity resolution is page-local evidence, so it is intentionally not
    filtered by confirmed/pending status or confidence here.  The knowledge
    base updater is connected as an execution dependency in the workflow;
    ``matched_entity_id`` is preferred for an existing match, with
    ``entity_id`` as the fallback for a new candidate. Only approved
    references are selected. An entity without one is returned as
    ``NEEDS_REFERENCE`` so the batch can continue with other page entities.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "entity_resolution": ("COMIC_ENTITY_RESOLUTION",),
                "asset_type": (ASSET_TYPES,),
                "generation_goal": ("STRING", {"default": "reference image"}),
            },
            "optional": {
                "update_report": ("COMIC_KB_UPDATE_REPORT",),
                "pages": ("COMIC_PAGES",),
                "page_index": ("INT", {"default": 1, "min": 1}),
                "chapter_id": ("STRING", {"default": ""}),
                "require_chapter_assets": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("requests_json", "target_report_json")
    FUNCTION = "build_requests"
    CATEGORY = "Comic2Anime/Assets"

    @staticmethod
    def _should_generate_character(
        entry: Dict[str, Any], has_reference: bool, asset_type: str = "character_portrait"
    ) -> Tuple[bool, str]:
        """Keep minor/page-only figures out of the asset-generation queue.

        Existing character references are reused for partial views; a new sheet
        may be generated for full views or an explicitly changed appearance.
        """
        explicit = entry.get("generate_asset")
        if explicit is False:
            return False, "entity_marked_nonessential"
        if explicit is True:
            return True, "explicit_generation_requested"
        importance = str(
            entry.get("importance", entry.get("narrative_importance", ""))
        ).strip().lower()
        role = str(entry.get("role", entry.get("character_role", ""))).strip().lower()
        if importance in {"minor", "low", "background", "incidental", "nonessential"}:
            return False, "nonessential_character"
        if role in {"background", "minor", "incidental", "crowd", "extra", "nonessential"}:
            return False, "nonessential_character"
        if has_reference and asset_type in ("character_portrait", "character_turnaround"):
            change = any(
                entry.get(key) is True
                for key in ("appearance_changed", "new_outfit", "new_appearance", "needs_new_reference")
            )
            if not change:
                return False, "reuse_existing_reference_for_same_character"
        return True, "new_or_explicitly_changed_appearance"

    @staticmethod
    def _chapter_asset_references(
        repo: AssetRepository,
        project_id: str,
        chapter_id: str,
        entity_type: str,
        entity_id: str,
        source_page_id: Optional[str],
        entry: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Return approved chapter masters bound to this page/entity first."""
        if not chapter_id.strip():
            return []
        requested_variant = str(
            entry.get("variant_key") or entry.get("variant_type") or ""
        ).strip().casefold()
        with repo.database.connect() as connection:
            page_rows = connection.execute(
                """SELECT b.group_id, b.variant_key, b.asset_id AS generated_asset_id,
                          a.status AS asset_status, 1 AS page_match
                     FROM chapter_asset_page_bindings b
                     JOIN assets a ON a.asset_id = b.asset_id
                    WHERE b.project_id = ? AND b.chapter_id = ? AND b.page_id = ?
                      AND b.entity_type = ? AND b.entity_id = ?
                      AND a.project_id = ? AND a.status = 'approved'
                    ORDER BY b.updated_at DESC""",
                (project_id, chapter_id, source_page_id or "", entity_type, entity_id, project_id),
            ).fetchall()
            if page_rows:
                return [{
                    "asset_id": row["generated_asset_id"],
                    "role": "chapter_asset_reference",
                    "reason": "SQLite page-level chapter asset binding.",
                    "chapter_asset_group_id": row["group_id"],
                    "variant_key": row["variant_key"],
                } for row in page_rows[:1]]
            rows = connection.execute(
                """SELECT g.group_id, g.variant_key, g.variant_type,
                          g.generated_asset_id, a.status AS asset_status,
                          EXISTS(
                              SELECT 1 FROM chapter_asset_group_sources s
                              WHERE s.group_id = g.group_id AND s.source_page_id = ?
                          ) AS page_match
                     FROM chapter_asset_groups g
                     JOIN assets a ON a.asset_id = g.generated_asset_id
                    WHERE g.project_id = ? AND g.chapter_id = ?
                      AND g.entity_type = ? AND g.entity_id = ?
                      AND g.status IN ('approved', 'reused')
                      AND a.project_id = ? AND a.status = 'approved'
                    ORDER BY page_match DESC, g.variant_key ASC""",
                (source_page_id or "", project_id, chapter_id, entity_type, entity_id, project_id),
            ).fetchall()
        candidates = [dict(row) for row in rows]
        # Older chapter runs may have generated approved assets before the
        # entity resolver assigned a stable entity_id. In that case, resolve
        # by the persisted source-page evidence and stable same_* key instead
        # of forcing a second generation.
        if not candidates and entity_type == "location":
            requested_same_key = str(entry.get("same_location_key", "")).strip().casefold()
            requested_name = str(
                entry.get("location_name") or entry.get("name")
                or entry.get("identity_candidate") or ""
            ).strip().casefold()
            # Some older entity-resolution payloads only carry a newly-created
            # location_id.  Resolve its canonical name before looking through
            # legacy chapter groups whose entity_id was empty.
            if not requested_name and entity_id.strip():
                with repo.database.connect() as connection:
                    location_row = connection.execute(
                        "SELECT canonical_name FROM locations WHERE location_id = ? AND project_id = ?",
                        (entity_id, project_id),
                    ).fetchone()
                if location_row:
                    requested_name = str(location_row["canonical_name"] or "").strip().casefold()
            with repo.database.connect() as connection:
                rows = connection.execute(
                    """SELECT g.group_id, g.variant_key, g.variant_type,
                              g.generated_asset_id, a.status AS asset_status,
                              EXISTS(
                                  SELECT 1 FROM chapter_asset_group_sources s
                                  WHERE s.group_id = g.group_id AND s.source_page_id = ?
                              ) AS page_match,
                              EXISTS(
                                  SELECT 1 FROM chapter_asset_group_sources s
                                  WHERE s.group_id = g.group_id AND s.source_page_id = ?
                                    AND s.panel_index = ?
                              ) AS panel_match,
                              g.decision_json
                         FROM chapter_asset_groups g
                         JOIN assets a ON a.asset_id = g.generated_asset_id
                        WHERE g.project_id = ? AND g.chapter_id = ?
                          AND g.entity_type = 'location' AND g.entity_id = ''
                          AND g.status IN ('approved', 'reused')
                          AND a.project_id = ? AND a.status = 'approved'
                        ORDER BY page_match DESC, g.variant_key ASC""",
                    (
                        source_page_id or "", source_page_id or "",
                        int(entry.get("panel_index", -1) or -1),
                        project_id, chapter_id, project_id,
                    ),
                ).fetchall()
            for row in rows:
                item = dict(row)
                try:
                    decision = json.loads(item.get("decision_json") or "{}")
                except json.JSONDecodeError:
                    decision = {}
                group = decision.get("group") if isinstance(decision.get("group"), dict) else {}
                source_keys = []
                for source_index in group.get("source_indices", []) if isinstance(group.get("source_indices"), list) else []:
                    source_keys.append(str(source_index))
                description = " ".join(str(group.get(key, "")) for key in ("variant_key", "variant_description"))
                evidence_text = str(decision.get("report", {}).get("prompt", "")) if isinstance(decision.get("report"), dict) else ""
                haystack = (description + " " + evidence_text).casefold()
                same_key_match = bool(requested_same_key and requested_same_key in haystack)
                name_match = bool(requested_name and requested_name in haystack)
                if requested_same_key and not same_key_match:
                    # The source rows are authoritative for same_location_key.
                    with repo.database.connect() as source_connection:
                        source_match = source_connection.execute(
                            "SELECT evidence_json FROM chapter_asset_group_sources WHERE group_id = ?",
                            (item["group_id"],),
                        ).fetchall()
                    for source_row in source_match:
                        try:
                            evidence = json.loads(source_row["evidence_json"] or "{}")
                        except json.JSONDecodeError:
                            evidence = {}
                        if str(evidence.get("same_location_key", "")).strip().casefold() == requested_same_key:
                            same_key_match = True
                            break
                # Several macro locations may share one source page.  The
                # panel is the reliable discriminator; page_match alone must
                # not make every request resolve to the first scene.
                if (
                    same_key_match
                    or name_match
                    or item.get("panel_match")
                    or (
                        not requested_same_key
                        and not requested_name
                        and item.get("page_match")
                    )
                ):
                    candidates.append(item)
            panel_candidates = [row for row in candidates if row.get("panel_match")]
            if panel_candidates:
                candidates = panel_candidates
        if requested_variant:
            exact = [
                row for row in candidates
                if requested_variant in {
                    str(row.get("variant_key", "")).casefold(),
                    str(row.get("variant_type", "")).casefold(),
                }
            ]
            if exact:
                candidates = exact
        else:
            normal = [
                row for row in candidates
                if str(row.get("variant_key", "")).casefold() == "normal"
                or str(row.get("variant_type", "")).casefold() in {"normal", "day"}
            ]
            if normal:
                candidates = normal
        references = []
        seen = set()
        for row in candidates:
            asset_id = row.get("generated_asset_id")
            if not asset_id or asset_id in seen:
                continue
            seen.add(asset_id)
            references.append({
                "asset_id": asset_id,
                "role": "chapter_asset_reference",
                "reason": "Approved chapter master selected by project, chapter, entity, and source page.",
                "chapter_asset_group_id": row.get("group_id"),
                "variant_key": row.get("variant_key"),
                "variant_type": row.get("variant_type"),
            })
        # A batch item represents one entity/state occurrence.  Even when a
        # legacy empty-entity binding produces several page/name candidates,
        # expose only one deterministic chapter master; otherwise one request
        # expands into multiple ``batch_images`` outputs.
        return references[:1]

    def build_requests(
        self,
        project_id: str,
        entity_resolution: Dict[str, Any],
        asset_type: str,
        generation_goal: str,
        update_report: Optional[Dict[str, Any]] = None,
        pages: Optional[List[Dict[str, Any]]] = None,
        page_index: int = 1,
        chapter_id: str = "",
        require_chapter_assets: bool = False,
    ):
        if isinstance(entity_resolution, str):
            try:
                entity_resolution = json.loads(entity_resolution)
            except json.JSONDecodeError as exc:
                raise ValueError("entity_resolution must contain valid JSON.") from exc
        if not isinstance(entity_resolution, dict):
            raise ValueError("entity_resolution must be a JSON object.")
        if not project_id.strip():
            raise ValueError("project_id is required.")
        if asset_type not in ASSET_TYPES:
            raise ValueError("Unsupported asset_type: {}".format(asset_type))

        repo = _repository()
        repo.initialize()
        source_page = None
        resolution_page_id = entity_resolution.get("page_id")
        if isinstance(resolution_page_id, str) and resolution_page_id:
            with repo.database.connect() as connection:
                row = connection.execute(
                        "SELECT page_id, sha256, width, height FROM pages WHERE project_id = ? AND page_id = ?",
                    (project_id, resolution_page_id),
                ).fetchone()
            if row:
                source_page = dict(row)
        if pages is not None:
            if not isinstance(pages, list) or not pages:
                raise ValueError("pages must contain at least one imported page.")
            if page_index < 1 or page_index > len(pages):
                raise ValueError("page_index must select an imported page (1-based).")
            source_page = pages[page_index - 1]
            if not isinstance(source_page, dict) or not source_page.get("page_id"):
                raise ValueError("Selected source page is missing page_id.")
        if source_page is None:
            source_page_id = None
            for entries_key in ("characters", "locations"):
                entries = entity_resolution.get(entries_key, [])
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict):
                            candidate = entry.get("source_page_id") or entry.get("page_id")
                            if not candidate and isinstance(entry.get("source"), dict):
                                candidate = entry["source"].get("source_page_id") or entry["source"].get("page_id")
                            if candidate:
                                source_page_id = candidate
                                break
                if source_page_id:
                    break
            if source_page_id:
                with repo.database.connect() as connection:
                    row = connection.execute(
                        "SELECT page_id, sha256, width, height FROM pages WHERE project_id = ? AND page_id = ?",
                        (project_id, source_page_id),
                    ).fetchone()
                if row:
                    source_page = dict(row)
        if source_page is not None and (
            not source_page.get("width") or not source_page.get("height")
        ):
            with repo.database.connect() as connection:
                row = connection.execute(
                    "SELECT width, height FROM pages WHERE project_id = ? AND page_id = ?",
                    (project_id, source_page["page_id"]),
                ).fetchone()
            if row:
                source_page.update(dict(row))
        requests = []
        report_items = []
        seen = set()
        source_counts = {"character": 0, "location": 0}
        entity_groups = (
            ("character", "characters", "character_identity"),
            ("location", "locations", "scene_reference"),
        )
        for entity_type, key, reference_role in entity_groups:
            entries = entity_resolution.get(key, [])
            if not isinstance(entries, list):
                raise ValueError("entity_resolution.{} must be an array.".format(key))
            source_counts[entity_type] = len(entries)
            # Preserve Gemini's source order for distinct entities. For
            # repeated occurrences, put only the clearest occurrence first so
            # de-duplication selects its crop without reordering the batch.
            entries = _preserve_source_order_with_best_references(entries, entity_type)
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                if entity_type == "location" and not _is_macro_location(entry):
                    report_items.append({
                        "entity_type": "location",
                        "source_index": entry.get("source_index", index),
                        "status": "SKIP_INCIDENTAL_LOCATION",
                        "skip_reason": "Only macro reusable locations are eligible for asset generation.",
                        "name": entry.get("name", ""),
                    })
                    continue
                reuse_existing = False
                # A matcher may retain a deterministic candidate id in
                # entity_id while matched_entity_id points at the real entity.
                entity_id = entry.get("matched_entity_id") or entry.get("entity_id")
                if not isinstance(entity_id, str) or not entity_id.strip():
                    # Raw Gemini analysis can arrive before entity matching. Keep
                    # every distinct visible character/location as a page-local
                    # candidate instead of dropping all but the first one.
                    source_data = entry.get("source", {})
                    source_name = (
                        source_data.get("name") or source_data.get("identity_candidate")
                        if isinstance(source_data, dict) else ""
                    ) or entry.get("name") or entry.get("identity_candidate") or entity_type
                    entity_id = "{}-candidate-{}".format(
                        entity_type,
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            "{}:{}:{}:{}:{}".format(
                                project_id,
                                source_page.get("page_id") if source_page else entity_resolution.get("page_id", ""),
                                source_name,
                                entry.get("panel_index", index),
                                index,
                            ),
                        ),
                    )
                entity_id = entity_id.strip()
                source = entry.get("source", {})
                entry_crop_box = _normalized_entry_crop(
                    entry.get("crop_box")
                    or (source.get("crop_box") if isinstance(source, dict) else None),
                    source_page,
                )
                raw_identity = (
                    (source.get("identity_candidate") or source.get("name"))
                    if isinstance(source, dict) else ""
                ) or entry.get("identity_candidate") or entry.get("name")
                normalized_identity = "".join(
                    char.casefold() for char in str(raw_identity) if char.isalnum()
                )
                if entity_type == "location":
                    # Gemini returns every visible occurrence.  A stable
                    # same_location_key (or a matched DB entity) identifies
                    # the same macro setting across panels; entries were
                    # sorted quality-first above, so the first one wins.
                    same_location_key = str(entry.get("same_location_key", "")).strip()
                    if same_location_key:
                        identity_key = "location-same:" + same_location_key.casefold()
                    elif entry.get("matched_entity_id"):
                        identity_key = "location-id:" + str(entry["matched_entity_id"])
                    else:
                        crop_identity = entry_crop_box
                        identity_key = "location:{}:{}:{}".format(
                            entity_id,
                            entry.get("panel_index", index),
                            json.dumps(crop_identity or {}, sort_keys=True),
                        )
                else:
                    identity_key = (
                        "id:" + str(entry.get("matched_entity_id"))
                        if entry.get("matched_entity_id")
                        else "name:" + normalized_identity if normalized_identity
                        else "id:" + entity_id
                    )
                unique_key = (entity_type, identity_key or entity_id)
                if unique_key in seen:
                    report_items.append({
                        "entity_type": entity_type,
                        "source_index": entry.get("source_index", index),
                        "entity_id": entity_id,
                        "status": "DUPLICATE_OCCURRENCE",
                        "identity_key": identity_key,
                    })
                    continue
                seen.add(unique_key)
                chapter_references = self._chapter_asset_references(
                    repo, project_id, chapter_id, entity_type, entity_id,
                    source_page.get("page_id") if source_page else None, entry,
                )
                existing_references = repo.get_reference_assets(
                    project_id, entity_type, entity_id
                )
                chapter_ids = {item["asset_id"] for item in chapter_references}
                references = chapter_references + [
                    item for item in existing_references
                    if item["asset_id"] not in chapter_ids
                ]
                references = references[:3]
                has_chapter_reference = bool(chapter_references)
                if require_chapter_assets and not has_chapter_reference:
                    report_items.append({
                        "entity_type": entity_type,
                        "source_index": entry.get("source_index", index),
                        "entity_id": entity_id,
                        "status": "NEEDS_CHAPTER_ASSET",
                        "skip_reason": "No approved ComicChapterAssetGeneration asset is bound to this entity/page/variant.",
                        "chapter_id": chapter_id,
                        "source_page_id": source_page.get("page_id") if source_page else None,
                    })
                    continue
                if entity_type == "character":
                    should_generate, skip_reason = self._should_generate_character(
                        entry, bool(references), asset_type
                    )
                    if not should_generate:
                        # Keep the existing approved reference in the batch so
                        # downstream nodes receive the character image without
                        # invoking Qwen again.  This is a reuse operation, not
                        # a new asset generation request.
                        if skip_reason in (
                            "reuse_existing_reference_for_partial_view",
                            "reuse_existing_reference_for_same_character",
                        ):
                            item_status = "REUSE_REFERENCE"
                            reuse_existing = True
                        else:
                            report_items.append({
                                "entity_type": entity_type,
                                "source_index": entry.get("source_index", index),
                                "entity_id": entity_id,
                                "status": "SKIP_NONESSENTIAL",
                                "skip_reason": skip_reason,
                                "reused_reference_asset_ids": [asset["asset_id"] for asset in references],
                                "resolution_status": entry.get("status", ""),
                                "confidence": entry.get("confidence", 0.0),
                            })
                            continue
                reference_items = [
                    {
                        "asset_id": asset["asset_id"],
                        "role": reference_role,
                        "reason": "Automatically selected approved reference for page entity.",
                    }
                    for asset in references
                ]
                # Always retain the current comic occurrence for character
                # decisions. Approved identity assets describe the reusable
                # baseline; the page crop is the evidence Gemini must compare
                # against before deciding whether a new variant is needed.
                if entity_type == "character" and source_page and isinstance(entry_crop_box, dict):
                    reference_items.insert(0, {
                        "source_page_id": source_page["page_id"],
                        "role": "character_source_crop",
                        "reason": "Current comic occurrence used for identity/appearance comparison.",
                        "crop_box": entry_crop_box,
                    })
                    reference_items = reference_items[:3]
                # The builder can receive one UI-level asset type for a mixed
                # page. Normalize it per entity so characters are never sent
                # through the scene workflow. A character request marked as
                # scene_reference bypasses character reuse/change detection
                # and can generate duplicate or scene-style images.
                if entity_type == "character":
                    request_asset_type = (
                        asset_type if asset_type.startswith("character_")
                        else "character_portrait"
                    )
                else:
                    request_asset_type = "scene_reference"
                if not reference_items and source_page:
                    source = entry.get("source", {})
                    crop_box = entry_crop_box
                    reference_items = [{
                        "source_page_id": source_page["page_id"],
                        "role": reference_role,
                        "reason": "Current comic page used as the first transient reference.",
                    }]
                    if isinstance(crop_box, dict):
                        reference_items[0]["crop_box"] = crop_box
                    item_status = "READY_PAGE_SOURCE"
                else:
                    item_status = "READY" if reference_items else "NEEDS_REFERENCE"
                if has_chapter_reference:
                    item_status = "REUSE_REFERENCE"
                    reuse_existing = True
                if reuse_existing:
                    item_status = "REUSE_REFERENCE"
                request = {
                    "schema_version": "1.0",
                    "request_id": str(uuid.uuid4()),
                    "project_id": project_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "identity_key": identity_key,
                    "asset_type": request_asset_type,
                    "generation_goal": generation_goal,
                    "reference_asset_ids": [
                        item["asset_id"]
                        for item in reference_items
                        if "asset_id" in item
                    ],
                    "source_evidence": [
                        entry.get("source") if isinstance(entry.get("source"), dict) else entry
                    ],
                    # Keep Gemini's reference assessment with the request so
                    # the image selector can add a full-page style/context
                    # reference when the selected scene crop is too small or
                    # visually unreliable.
                    "reference_quality": _gemini_reference_quality(entry),
                    "reference_quality_reason": entry.get(
                        "reference_quality_reason", ""
                    ),
                    "same_location_key": entry.get("same_location_key", ""),
                    "panel_index": entry.get("panel_index"),
                    "source_page_hash": source_page.get("sha256") if source_page else None,
                    "source_page_id": source_page.get("page_id") if source_page else None,
                    "warnings": [] if item_status in ("READY", "READY_PAGE_SOURCE", "REUSE_REFERENCE") else [
                        "No approved reference asset is available for this page entity."
                    ],
                    "auto_approve": True,
                }
                if item_status == "READY_PAGE_SOURCE":
                    if reference_items[0].get("crop_box") is not None:
                        request["crop_box"] = reference_items[0]["crop_box"]
                requests.append({
                    "asset_request": request,
                    "references": reference_items,
                    "status": item_status,
                })
                report_items.append({
                    "entity_type": entity_type,
                    "source_index": entry.get("source_index", index),
                    "entity_id": entity_id,
                    "status": item_status,
                    "approved_reference_asset_ids": request["reference_asset_ids"],
                    "resolution_status": entry.get("status", ""),
                    "confidence": entry.get("confidence", 0.0),
                })

        report = {
            "selection_policy": "chapter_assets_required" if require_chapter_assets else "important_characters_only_reuse_existing_partial_views",
            "total_entity_count": len(requests),
            "character_count": sum(
                item.get("entity_type") == "character" for item in report_items
            ),
            "location_count": sum(
                item.get("entity_type") == "location" for item in report_items
            ),
            "source_character_count": source_counts["character"],
            "source_location_count": source_counts["location"],
            "ready_count": sum(
                item["status"] in ("READY", "READY_PAGE_SOURCE")
                for item in report_items
            ),
            "page_source_reference_count": sum(
                item["status"] == "READY_PAGE_SOURCE" for item in report_items
            ),
            "needs_reference_count": sum(
                item["status"] == "NEEDS_REFERENCE" for item in report_items
            ),
            "needs_chapter_asset_count": sum(
                item["status"] == "NEEDS_CHAPTER_ASSET" for item in report_items
            ),
            "reused_reference_count": sum(
                item["status"] == "REUSE_REFERENCE" for item in report_items
            ),
            "skipped_nonessential_count": sum(
                item["status"] == "SKIP_NONESSENTIAL" for item in report_items
            ),
            "skipped_count": sum(
                item["status"] in (
                    "NEEDS_ENTITY_ID", "DUPLICATE_OCCURRENCE",
                    "SKIP_INCIDENTAL_LOCATION",
                )
                for item in report_items
            ),
            "skipped_incidental_location_count": sum(
                item["status"] == "SKIP_INCIDENTAL_LOCATION" for item in report_items
            ),
            "items": report_items,
            "knowledge_base_update_dependency": bool(update_report is not None),
        }
        return (
            json.dumps(requests, ensure_ascii=False),
            json.dumps(report, ensure_ascii=False),
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

"""Chapter-wide character/location grouping and reference generation."""

import hashlib
import io
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from ..api.gemini_client import generate_structured_json
from ..core.asset_repository import AssetRepository
from ..core.database import ComicDatabase
from ..core.storage import get_data_root
from .asset_workflow_nodes import (
    _crop_pil_image,
    _encode_comfy_images,
    _force_qwen_output_1024,
    _load_asset_image,
    _single_image_bytes,
    _stack_batch_images,
)
from .qwen_edit_2511 import QwenImageEditCustomNode


_GROUP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "groups": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "variant_key": {"type": "STRING"},
            "variant_type": {"type": "STRING"},
            "variant_description": {"type": "STRING"},
            "source_indices": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            "selected_source_index": {"type": "INTEGER"},
            "reason": {"type": "STRING"},
            "generation_requirements": {"type": "ARRAY", "items": {"type": "STRING"}},
            "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        }, "required": [
            "variant_key", "variant_type", "variant_description", "source_indices",
            "selected_source_index", "reason", "generation_requirements", "uncertainties",
        ]}},
    },
    "required": ["groups"],
}

_PROMPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "detailed_description": {"type": "STRING"},
        "must_preserve": {"type": "ARRAY", "items": {"type": "STRING"}},
        "allowed_changes": {"type": "ARRAY", "items": {"type": "STRING"}},
        "negative_constraints": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["detailed_description", "must_preserve", "allowed_changes", "negative_constraints"],
}

_QC_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "decision": {"type": "STRING", "enum": ["PASS", "RETRY", "ERROR"]},
        "overall_score": {"type": "NUMBER"},
        "failures": {"type": "ARRAY", "items": {"type": "STRING"}},
        "correction_instructions": {"type": "ARRAY", "items": {"type": "STRING"}},
        "corrected_prompt_fragment": {"type": "STRING"},
        "evidence": {"type": "STRING"},
    },
    "required": [
        "decision", "overall_score", "failures", "correction_instructions",
        "corrected_prompt_fragment", "evidence",
    ],
}

_VARIANT_TYPES = {
    "normal", "transformation", "outfit", "injury", "age_change",
    "body_change", "equipment_change", "other_variant", "day", "night",
    "damaged", "destroyed", "battle", "empty", "occupied", "other_state",
}


def _norm(value: Any) -> str:
    return "".join(ch.casefold() for ch in str(value or "") if ch.isalnum())


def _quality(entry: Dict[str, Any]) -> float:
    try:
        value = float(entry.get("reference_quality", 0.0))
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(1.0, value))


def _area(entry: Dict[str, Any]) -> float:
    crop = entry.get("crop_box") or {}
    try:
        return max(0.0, float(crop.get("width", 0))) * max(0.0, float(crop.get("height", 0)))
    except (TypeError, ValueError):
        return 0.0


def _sort_sources(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=_source_sort_key)


def _source_sort_key(item: Dict[str, Any]) -> Tuple[float, float, int, float, int, int]:
    """Stable quality ordering used both for display and safety fallback."""
    return (
        -_quality(item), -_area(item), -(1 if item.get("is_best_reference") else 0),
        -float(item.get("confidence", 0) or 0), int(item.get("page_index", 0)),
        int(item.get("panel_index", 0)),
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _asset_description(entity_type: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the stable description fields used by chapter asset generation.

    Expressions, poses and actions are deliberately excluded from the stable
    identity description; Gemini may use them to classify a variant, but they
    must not make ordinary shot-to-shot changes create a new base identity.
    """
    if entity_type == "character":
        fields = (
            "name", "identity_candidate", "gender", "age", "appearance",
            "hair", "face", "body", "clothing", "accessories", "colors",
        )
    else:
        fields = (
            "name", "location_name", "description", "location_description",
            "architecture", "environment", "terrain", "interior", "exterior",
            "lighting", "time_of_day", "weather", "condition", "state",
        )
    return {field: entry[field] for field in fields if entry.get(field) not in (None, "", [])}


class ComicChapterAssetGeneration:
    """Generate one approved reference asset per chapter entity/state group."""

    @classmethod
    def INPUT_TYPES(cls):
        try:
            import folder_paths
            models = folder_paths.get_filename_list("diffusion_models")
            clips = folder_paths.get_filename_list("text_encoders")
            vaes = folder_paths.get_filename_list("vae")
        except ImportError:
            models = clips = vaes = [""]
        return {"required": {
            "project_id": ("STRING", {"default": ""}),
            "chapter_id": ("STRING", {"default": ""}),
            "unet_name": (models,),
            "clip_name": (clips,),
            "vae_name": (vaes,),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            "steps": ("INT", {"default": 40, "min": 1, "max": 1000}),
            "cfg": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 100.0}),
            "max_generation_retries": ("INT", {"default": 2, "min": 0, "max": 3}),
            "gemini_api_key": ("STRING", {"default": ""}),
            "gemini_model": ("STRING", {"default": "gemini-2.5-flash"}),
            "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
            "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
            "api_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            "enable_upscale": ("BOOLEAN", {"default": True}),
            "force_refresh": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("generated_assets", "chapter_asset_manifest", "report")
    FUNCTION = "generate"
    CATEGORY = "Comic2Anime/Assets/Chapter"

    def _db(self) -> ComicDatabase:
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        return database

    @staticmethod
    def _bind_group_to_pages(db: ComicDatabase, project_id: str, chapter_id: str,
                              group_id: str, asset_id: str, entity_type: str,
                              entity_id: str, variant_key: str,
                              items: List[Dict[str, Any]]) -> None:
        """Persist one generated asset binding for every source page occurrence."""
        if not items or not asset_id:
            return
        with db.connect() as connection:
            seen = set()
            for item in items:
                page_id = str(item.get("source_page_id", "")).strip()
                if not page_id:
                    continue
                page_entity_id = str(item.get("entity_id") or entity_id or "").strip()
                page_key = str(item.get("entity_key") or item.get("name") or "").strip().casefold()
                dedupe_key = (page_id, page_entity_id, page_key)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                connection.execute(
                    """INSERT INTO chapter_asset_page_bindings(
                        binding_id, project_id, chapter_id, page_id, entity_type,
                        entity_id, group_id, asset_id, variant_key
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(project_id, chapter_id, page_id, entity_type, entity_id, group_id)
                    DO UPDATE SET asset_id=excluded.asset_id,
                                  variant_key=excluded.variant_key,
                                  updated_at=CURRENT_TIMESTAMP""",
                    (
                        str(uuid.uuid5(uuid.NAMESPACE_URL, "chapter-page:{}:{}:{}:{}:{}:{}".format(
                            project_id, chapter_id, page_id, group_id, page_entity_id, page_key,
                        ))),
                        project_id, chapter_id, page_id, entity_type, page_entity_id,
                        group_id, asset_id, variant_key,
                    ),
                )

    def _pages(self, db: ComicDatabase, project_id: str, chapter_id: str) -> List[Dict[str, Any]]:
        with db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pages WHERE project_id = ? AND chapter_id = ? ORDER BY page_index, page_id",
                (project_id, chapter_id),
            ).fetchall()
        if not rows:
            raise ValueError("No imported pages found for the selected project/chapter.")
        return [dict(row) for row in rows]

    def _analyses(self, db: ComicDatabase, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        with db.connect() as connection:
            for page in pages:
                row = connection.execute(
                    "SELECT result_json FROM analysis_runs WHERE page_id = ? ORDER BY created_at DESC LIMIT 1",
                    (page["page_id"],),
                ).fetchone()
                if row is None:
                    continue
                try:
                    result = json.loads(row["result_json"])
                except json.JSONDecodeError:
                    continue
                result["_page"] = page
                results.append(result)
        if not results:
            raise ValueError("No page analyses found for the selected chapter.")
        return results

    def _entity_id(self, db: ComicDatabase, entry: Dict[str, Any], page: Dict[str, Any], entity_type: str) -> str:
        direct = entry.get("matched_entity_id") or entry.get("entity_id")
        if isinstance(direct, str) and direct.strip():
            direct = direct.strip()
            # Entity resolution can leave duplicate candidate rows with the
            # same canonical name after repeated analysis. Prefer the oldest
            # same-name entity that already owns an approved chapter asset;
            # otherwise use the oldest same-name entity. This keeps chapter
            # generation bound to the SQLite asset record instead of creating
            # another asset for a new candidate ID.
            table = "characters" if entity_type == "character" else "locations"
            id_column = "character_id" if entity_type == "character" else "location_id"
            with db.connect() as connection:
                name_row = connection.execute(
                    "SELECT canonical_name FROM {} WHERE {} = ?".format(table, id_column),
                    (direct,),
                ).fetchone()
                if name_row and name_row["canonical_name"]:
                    same_name = connection.execute(
                        """SELECT e.{id_column} AS entity_id,
                                  EXISTS(
                                      SELECT 1 FROM chapter_asset_groups g
                                       JOIN assets a ON a.asset_id = g.generated_asset_id
                                      WHERE g.project_id = e.project_id
                                        AND g.entity_type = ?
                                        AND g.entity_id = e.{id_column}
                                        AND g.status IN ('approved', 'reused')
                                        AND a.status = 'approved'
                                  ) AS has_asset,
                                  e.created_at
                             FROM {table} e
                            WHERE e.project_id = ?
                              AND lower(trim(e.canonical_name)) = lower(trim(?))
                            ORDER BY has_asset DESC, e.created_at, e.{id_column}""".format(
                            id_column=id_column, table=table
                        ),
                        (entity_type, page.get("project_id", ""), name_row["canonical_name"]),
                    ).fetchall()
                    if same_name:
                        return str(same_name[0]["entity_id"])
            return direct
        name = _norm(entry.get("identity_candidate") or entry.get("name"))
        table = "character_appearances" if entity_type == "character" else "location_states"
        id_column = "character_id" if entity_type == "character" else "location_id"
        description_column = "appearance_description" if entity_type == "character" else "description"
        with db.connect() as connection:
            rows = connection.execute(
                """SELECT t.{id_column} AS entity_id, t.{description_column} AS description,
                          {name_column} AS canonical_name
                     FROM {table} t
                     {join_clause}
                    WHERE t.page_id = ? AND t.panel_index = ?""".format(
                    id_column=id_column,
                    description_column=description_column,
                    name_column="c.canonical_name" if entity_type == "location" else "c.canonical_name",
                    table=table,
                    join_clause=(
                        "JOIN locations c ON c.location_id = t.location_id"
                        if entity_type == "location"
                        else "JOIN characters c ON c.character_id = t.character_id"
                    ),
                ), (page["page_id"], int(entry.get("panel_index", 0) or 0)),
            ).fetchall()
            for row in rows:
                if name and (
                    name in _norm(row["description"])
                    or name in _norm(row["canonical_name"])
                ):
                    return row["entity_id"]
            # The analysis may describe a location/character differently from
            # the persisted knowledge-base text.  If this panel has exactly
            # one persisted entity, it is still a safe binding and prevents
            # the chapter master from being stored with an empty entity_id.
            distinct_ids = {str(row["entity_id"]).strip() for row in rows if row["entity_id"]}
            if len(distinct_ids) == 1:
                return next(iter(distinct_ids))

            # Last, use the page-level entity only when there is one unique
            # candidate.  This handles macro scene analyses without a reliable
            # panel index while avoiding arbitrary selection among two scenes.
            page_rows = connection.execute(
                "SELECT DISTINCT {id_column} AS entity_id FROM {table} WHERE page_id = ?".format(
                    id_column=id_column, table=table
                ), (page["page_id"],),
            ).fetchall()
            page_ids = {str(row["entity_id"]).strip() for row in page_rows if row["entity_id"]}
            if len(page_ids) == 1:
                return next(iter(page_ids))
        return ""

    def _sources(self, db: ComicDatabase, repo: AssetRepository, analyses: List[Dict[str, Any]], project_id: str) -> List[Dict[str, Any]]:
        sources = []
        for analysis in analyses:
            page = analysis["_page"]
            for entity_type, key in (("character", "characters"), ("location", "locations")):
                for entry in analysis.get(key, []) if isinstance(analysis.get(key, []), list) else []:
                    crop = entry.get("crop_box")
                    if not isinstance(crop, dict):
                        continue
                    if entity_type == "location" and str(entry.get("scene_scope", "macro")).lower() != "macro":
                        continue
                    raw_page = Path(page["image_path"]).read_bytes()
                    with Image.open(io.BytesIO(raw_page)) as source_image:
                        source_image.load()
                        cropped = _crop_pil_image(source_image.convert("RGB"), crop)
                        output = io.BytesIO()
                        cropped.save(output, format="PNG")
                        content = output.getvalue()
                        width, height = cropped.size
                    crop_hash = hashlib.sha256(content).hexdigest()
                    asset_type = "character_source_crop" if entity_type == "character" else "panel_reference"
                    entity_id = self._entity_id(db, entry, page, entity_type)
                    run_id = "chapter-source-{}".format(uuid.uuid5(uuid.NAMESPACE_URL, project_id + crop_hash))
                    registered = repo.register_batch(
                        project_id=project_id,
                        asset_type=asset_type,
                        images=[{"content": content, "width": width, "height": height, "mime_type": "image/png", "metadata": {
                            "source_page_id": page["page_id"], "page_index": page["page_index"],
                            "panel_index": int(entry.get("panel_index", 0) or 0), "crop_box": crop,
                            "reference_quality": _quality(entry), "evidence": entry,
                        }}],
                        display_name="Chapter source crop",
                        generation_run_id=run_id,
                        model_name="source_crop",
                        parameters={"source_page_id": page["page_id"], "crop_box": crop, "reference_quality": _quality(entry)},
                        entity_type=entity_type if entity_id else None,
                        entity_id=entity_id or None,
                        relation_type="chapter_source_crop",
                    )
                    asset = registered["assets"][0]
                    if asset["status"] != "approved":
                        asset = repo.review_asset(project_id, asset["asset_id"], "approved", "Original chapter source crop.")
                    # `name` is the stable display identity used for chapter
                    # grouping. Do not prefer identity_candidate here: the
                    # same person may be described as "King", "Kingdom
                    # Ruler", or "Shocked King" on different pages.
                    stable_name = entry.get("name") or entry.get("identity_candidate") or ""
                    stable_key = entry.get("same_entity_key") if entity_type == "character" else entry.get("same_location_key")
                    sources.append({
                        "entity_type": entity_type, "entity_id": entity_id,
                        "entity_key": stable_key,
                        "name": stable_name,
                        "source_page_id": page["page_id"], "page_index": page["page_index"],
                        "panel_index": int(entry.get("panel_index", 0) or 0),
                        "crop_box": crop, "reference_quality": _quality(entry),
                        "is_best_reference": bool(entry.get("is_best_reference")),
                        "confidence": entry.get("confidence", 0), "evidence": entry,
                        "description": _asset_description(entity_type, entry),
                        "asset": asset,
                    })
        return sources

    def _base_groups(self, sources: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        groups = {}
        for source in sources:
            # Chapter grouping is identity-first, not page-candidate-ID-first.
            # Entity IDs may be temporary candidates and can differ between
            # pages/re-analysis. The analysis stable key/name is what keeps a
            # normal character as one chapter asset; Gemini later classifies
            # only explicit transformations inside that group.
            # For characters, the stable role/name is the primary class. A
            # page analyzer may emit soldier_1, soldier_2 and
            # soldiers_saluting for ordinary members of the same recurring
            # soldier role; those must be one Gemini input group and one
            # generated asset. same_entity_key remains the fallback when a
            # canonical name is unavailable. Locations retain their explicit
            # same_location_key because similarly named settings can differ.
            if source["entity_type"] == "character":
                stable_key = _norm(source.get("name")) or source.get("entity_key")
            else:
                stable_key = source.get("entity_key") or _norm(source.get("name"))
            key = stable_key or source.get("entity_id") or source["source_page_id"]
            groups.setdefault((source["entity_type"], key), []).append(source)
        return [_sort_sources(items) for items in groups.values()]

    def _group_with_gemini(self, items: List[Dict[str, Any]], context: str, model: str, api_key: str, base_url: str, timeout: int, retries: int) -> List[Dict[str, Any]]:
        images = [_single_image_bytes(_load_asset_image(item["asset"])) for item in items]
        prompt = (
            "The supplied images are ALL occurrences of the same classified entity. "
            "For a CHARACTER, first inspect every supplied character image together and create "
            "exactly one canonical character group by default. Do not create one group per page "
            "or per image. "
            "Ordinary expression, emotion, pose, action, camera angle, framing, lighting, props, "
            "newspaper/message, battle reaction, crying, screaming, clothing presentation and minor "
            "outfit differences MUST stay in the same character group. Only split a character when "
            "the story explicitly shows a huge transformation such as a changed species, body form, "
            "age, scale or supernatural transformed form; label that group variant_type=transformation. "
            "Do not split for a major outfit, injury, equipment or scene/action state alone. For a "
            "LOCATION, split only explicit persistent scene-state changes. For every group choose "
            "the best source index. Return only JSON.\nCONTEXT:\n" + context
            + "\nOCCURRENCES:\n" + json.dumps([
                {"index": i, "page_index": x["page_index"], "panel_index": x["panel_index"],
                 "name": x["name"], "reference_quality": x["reference_quality"], "evidence": x["evidence"]}
                | {"stable_description": x.get("description", {})}
                for i, x in enumerate(items)
            ], ensure_ascii=False)
        )
        result = generate_structured_json(prompt, model, api_key, base_url, timeout, images=images, response_schema=_GROUP_SCHEMA, max_retries=retries)
        groups = result.get("groups") if isinstance(result, dict) else None
        if not isinstance(groups, list) or not groups:
            raise ValueError("Gemini returned no chapter asset groups.")
        used = set()
        normalized = []
        used_variant_keys = set()
        for index, group in enumerate(groups):
            indices = [int(value) for value in group.get("source_indices", []) if isinstance(value, int) and 0 <= value < len(items)]
            indices = [value for value in indices if value not in used]
            if not indices:
                continue
            used.update(indices)
            best = min(indices, key=lambda value: _source_sort_key(items[value]))
            declared = group.get("selected_source_index")
            warning = "" if declared == best else "Gemini selected a non-maximal quality source; code selected the highest-quality source."
            variant_type = str(group.get("variant_type", "normal" if items[0]["entity_type"] == "character" else "other_state"))
            if variant_type not in _VARIANT_TYPES:
                variant_type = "other_variant" if items[0]["entity_type"] == "character" else "other_state"
            variant_key = str(group.get("variant_key") or "state-{}".format(index)).strip() or "state-{}".format(index)
            original_variant_key = variant_key
            suffix = 2
            while variant_key in used_variant_keys:
                variant_key = "{}-{}".format(original_variant_key, suffix)
                suffix += 1
            used_variant_keys.add(variant_key)
            normalized.append({
                "variant_key": variant_key,
                "variant_type": variant_type,
                "variant_description": str(group.get("variant_description", "")),
                "source_indices": indices, "selected_source_index": best,
                "reason": str(group.get("reason", "")),
                "generation_requirements": group.get("generation_requirements", []) if isinstance(group.get("generation_requirements", []), list) else [],
                "uncertainties": group.get("uncertainties", []) if isinstance(group.get("uncertainties", []), list) else [],
                "selection_warning": warning,
            })
        missing = [index for index in range(len(items)) if index not in used]
        if missing:
            fallback_key = "unclassified"
            if fallback_key in used_variant_keys:
                fallback_key = "unclassified-{}".format(len(used_variant_keys) + 1)
            normalized.append({"variant_key": fallback_key, "variant_type": "other_variant" if items[0]["entity_type"] == "character" else "other_state", "variant_description": "Unclassified occurrences", "source_indices": missing, "selected_source_index": min(missing, key=lambda value: _source_sort_key(items[value])), "reason": "Fallback for unclassified source occurrences.", "generation_requirements": [], "uncertainties": ["Gemini did not classify every occurrence."], "selection_warning": ""})

        # A character's ordinary story changes are not separate visual assets.
        # Keep only explicit transformation groups separate; merge every other
        # group into the one canonical character reference group.
        if items[0]["entity_type"] == "character" and len(normalized) > 1:
            transformation_groups = [
                group for group in normalized
                if group["variant_type"] == "transformation"
            ]
            ordinary_groups = [
                group for group in normalized
                if group["variant_type"] != "transformation"
            ]
            if len(ordinary_groups) > 1:
                merged_indices = []
                descriptions = []
                reasons = []
                requirements = []
                uncertainties = []
                warnings = []
                for group in ordinary_groups:
                    merged_indices.extend(group["source_indices"])
                    if group["variant_description"]:
                        descriptions.append(group["variant_description"])
                    if group["reason"]:
                        reasons.append(group["reason"])
                    requirements.extend(group["generation_requirements"])
                    uncertainties.extend(group["uncertainties"])
                    if group["selection_warning"]:
                        warnings.append(group["selection_warning"])
                merged_indices = sorted(set(merged_indices))
                best = min(merged_indices, key=lambda value: _source_sort_key(items[value]))
                merged = {
                    "variant_key": "normal",
                    "variant_type": "normal",
                    "variant_description": "One persistent character identity; ordinary story changes merged.",
                    "source_indices": merged_indices,
                    "selected_source_index": best,
                    "reason": "Merged ordinary expression, pose, action, outfit and scene-context differences.",
                    "generation_requirements": sorted(set(requirements)),
                    "uncertainties": sorted(set(uncertainties)),
                    "selection_warning": " ".join(sorted(set(warnings))),
                }
                normalized = [merged] + transformation_groups
        return normalized

    def _context(self, db: ComicDatabase, project_id: str, chapter_id: str) -> str:
        with db.connect() as connection:
            summary = connection.execute("SELECT summary_json FROM chapter_summaries WHERE project_id = ? AND chapter_id = ?", (project_id, chapter_id)).fetchone()
            events = [dict(row) for row in connection.execute("SELECT page_id, panel_index, description, participants_json FROM story_events WHERE project_id = ? AND chapter_id = ? ORDER BY page_id, panel_index", (project_id, chapter_id)).fetchall()]
        return json.dumps({"chapter_summary": json.loads(summary["summary_json"]) if summary else {}, "story_events": events}, ensure_ascii=False)

    def _generate_one(self, request: Dict[str, Any], selected: Dict[str, Any], group: Dict[str, Any], context: str, args: Dict[str, Any], repo: AssetRepository) -> Tuple[Optional[Dict[str, Any]], Any, Dict[str, Any]]:
        source_image = _load_asset_image(selected["asset"])
        source_bytes = _single_image_bytes(source_image)
        character_turnaround_directive = ""
        if request["entity_type"] == "character":
            character_turnaround_directive = (
                "\n\nMANDATORY CHARACTER TURNAROUND OUTPUT:\n"
                "Generate one clean full-body character turnaround reference sheet containing exactly "
                "three views of the same character arranged left-to-right: FRONT VIEW, SIDE VIEW, "
                "BACK VIEW. Complete and reconstruct the character from head to toe, including the "
                "full hairstyle, face, neck, shoulders, torso, arms, hands, hips, legs, feet, shoes, "
                "clothing, seams, accessories and the back of the costume. Keep identical face identity, "
                "hair, body proportions, colors, clothing, accessories and art style across all three "
                "views. Use a neutral standing pose, consistent scale and clean spacing. Do not add "
                "extra characters, alternate costumes, speech bubbles, captions, readable text, logos, "
                "watermarks or comic panel borders. The three views must show the same requested variant."
            )
        scene_clean_reference_directive = ""
        if request["entity_type"] == "location":
            scene_clean_reference_directive = (
                "\n\nMANDATORY CLEAN SCENE REFERENCE FOR MINIMAX H3:\n"
                "Generate an environment-only clean plate. Remove every speech bubble, dialogue balloon, "
                "narration box, caption box, comic sound effect, readable word, subtitle, logo, watermark "
                "and comic panel border. Reconstruct the hidden background behind removed text or boxes "
                "using only the visible architecture, terrain, furniture, perspective, materials, colors "
                "and lighting from the source. Do not leave blank patches, white rectangles, blurred text, "
                "gibberish or inpaint seams. Do not add unrelated objects or characters. Keep the requested "
                "location and story state unchanged. This image is a clean 1024x1024 visual reference for "
                "MiniMax H3 video generation."
            )
        prompt_result = generate_structured_json(
            "Create a precise Qwen-Image-Edit 2511 prompt for one unified chapter reference asset. "
            "The ENTITY_DESCRIPTION is the authoritative description style and content for the "
            "character or scene. Preserve it exactly, apply only the requested persistent state, "
            "and do not invent facts. For a character, preserve identity, face, hair, body, clothing "
            "and accessories. For a scene, preserve architecture, environment, layout, lighting and "
            "state, and do not turn characters into scene content. The final image canvas must be "
            "exactly 1024x1024 pixels.\n"
            + json.dumps({"request": request, "group": group, "context": context, "source_evidence": selected["evidence"]}, ensure_ascii=False)
            + character_turnaround_directive
            + scene_clean_reference_directive,
            args["gemini_model"], args["gemini_api_key"], args["api_base_url"], args["timeout_seconds"],
            images=[source_bytes], response_schema=_PROMPT_SCHEMA, max_retries=args["api_retries"],
        )
        prompt = prompt_result["detailed_description"] + character_turnaround_directive + scene_clean_reference_directive
        for attempt in range(args["max_generation_retries"] + 1):
            image = QwenImageEditCustomNode().edit_image(
                image1=source_image, image2=None, image3=None, prompt=prompt,
                unet_name=args["unet_name"], clip_name=args["clip_name"], vae_name=args["vae_name"],
                seed=(args["seed"] + attempt) % (2 ** 64), steps=args["steps"], cfg=args["cfg"],
            )[0]
            # Chapter reference assets have a fixed square contract. Normalize
            # before Gemini QC so the checked image is exactly the persisted size.
            image = _force_qwen_output_1024(image)
            generated = _single_image_bytes(image)
            qc_directive = ""
            if request["entity_type"] == "character":
                qc_directive = (
                    "\nFor a character asset, this is a mandatory three-view turnaround sheet. "
                    "Verify that exactly front, side and back full-body views of the same character "
                    "are present, that the body is completed from head to toe, and that identity, "
                    "hair, proportions, clothing, colors and accessories match across views. Reject "
                    "missing limbs, cropped feet, inconsistent faces, inconsistent costumes or extra views."
                )
            elif request["entity_type"] == "location":
                qc_directive = (
                    "\nFor a location asset used as a MiniMax H3 reference, verify that the image is an "
                    "environment-only clean plate: no speech bubbles, narration boxes, captions, sound "
                    "effects, readable text, subtitles, logos, watermarks, comic panel borders, blank "
                    "rectangles, blurred text remnants or inpaint seams. Verify areas formerly covered by "
                    "text or boxes are plausibly reconstructed from surrounding architecture, perspective, "
                    "materials, colors and lighting. Reject any remaining text or text box."
                )
            qc = generate_structured_json(
                "Inspect the first image as the generated chapter reference and the second as the selected comic source. "
                "Check the generated image against ENTITY_DESCRIPTION, identity/state/style fidelity. "
                "For character assets, check face, hair, body, clothing and accessories. For scene assets, "
                "check architecture, environment, layout and state, and reject visible people, speech bubbles, "
                "captions, readable text or comic borders unless explicitly required. PASS only if all hard "
                "constraints pass. For RETRY give exact Qwen corrections.\n"
                + json.dumps({"request": request, "group": group, "prompt": prompt}, ensure_ascii=False)
                + qc_directive,
                args["gemini_model"], args["gemini_api_key"], args["api_base_url"], args["timeout_seconds"],
                images=[generated, source_bytes], response_schema=_QC_SCHEMA, max_retries=args["api_retries"],
            )
            if qc.get("decision") == "PASS":
                upscale_report = {"enabled": bool(args.get("enable_upscale", True))}
                if upscale_report["enabled"]:
                    # The expensive SeedVR2 pass is optional for fast testing.
                    try:
                        from ..api.seedvr2_upscaler import upscale_confirmed_image

                        image = upscale_confirmed_image(image)
                        upscale_report.update({
                            "model": "seedvr2_ema_7b_sharp_fp16.safetensors",
                            "resolution": 4096,
                            "max_resolution": 4096,
                            "seed": 42,
                            "color_correction": "lab",
                        })
                    except (RuntimeError, ValueError, OSError) as exc:
                        return None, None, {
                            "decision": "ERROR",
                            "attempts": attempt + 1,
                            "error_code": "POST_QC_UPSCALE_ERROR",
                            "error": "SeedVR2 4096 upscale failed: {}".format(exc),
                            "qc": qc,
                            "prompt": prompt,
                        }
                encoded = _encode_comfy_images(image)
                entity_type = request["entity_type"]
                asset_type = "character_turnaround" if entity_type == "character" else "scene_reference"
                run_id = "chapter-generated-{}".format(uuid.uuid5(uuid.NAMESPACE_URL, request["source_fingerprint"] + group["variant_key"]))
                registered = repo.register_batch(
                    project_id=request["project_id"], asset_type=asset_type, images=encoded,
                    display_name="{} {}".format(request["entity_type"], group["variant_key"]), generation_run_id=run_id,
                    prompt=prompt, prompt_version="chapter-v1", model_name="Qwen-Image-Edit 2511",
                    seed=args["seed"], parameters={"chapter_id": request["chapter_id"], "group": group, "qc": qc, "reference_purpose": "minimax_h3_scene_reference" if entity_type == "location" else "character_turnaround_reference", "post_qc_upscale": upscale_report},
                    reference_asset_ids=[selected["asset"]["asset_id"]],
                    entity_type=entity_type if request.get("entity_id") else None,
                    entity_id=request.get("entity_id") or None,
                    relation_type="chapter:{}:{}".format(request["chapter_id"], group["variant_key"]),
                )
                for asset in registered["assets"]:
                    repo.review_asset(request["project_id"], asset["asset_id"], "approved", "Automatically approved after chapter Gemini QC PASS.")
                return registered["assets"][0], image, {"decision": "PASS", "attempts": attempt + 1, "qc": qc, "prompt": prompt, "post_qc_upscale": upscale_report}
            if qc.get("decision") != "RETRY" or attempt >= args["max_generation_retries"]:
                return None, image, {"decision": qc.get("decision", "ERROR"), "attempts": attempt + 1, "qc": qc, "prompt": prompt}
            prompt += "\nMANDATORY QC CORRECTIONS:\n" + "\n".join(qc.get("correction_instructions", [])) + "\n" + str(qc.get("corrected_prompt_fragment", ""))
        return None, None, {"decision": "ERROR", "attempts": args["max_generation_retries"] + 1}

    def _reuse_entity_groups(
        self, db: ComicDatabase, repo: AssetRepository, project_id: str,
        chapter_id: str, entity_type: str, entity_id: str, base_fingerprint: str,
    ) -> Optional[Tuple[List[Any], List[Dict[str, Any]]]]:
        """Reuse a complete entity grouping without calling Gemini or Qwen."""
        with db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chapter_asset_groups WHERE project_id=? AND chapter_id=? "
                "AND entity_type=? AND entity_id=? ORDER BY group_id",
                (project_id, chapter_id, entity_type, entity_id),
            ).fetchall()
        if not rows:
            return None

        # Analyses can fail to resolve a persisted entity ID.  Those groups
        # are stored with entity_id='', so several unrelated character or
        # location groups can share the same database scope.  Do not let a
        # stale/different group poison reuse for the current source set: the
        # base fingerprint is the stable identity of this exact source group.
        matching_rows = []
        for row in rows:
            try:
                decision = json.loads(row["decision_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if decision.get("base_fingerprint") == base_fingerprint:
                matching_rows.append((row, decision))
        if not matching_rows:
            return None

        # Older runs may contain multiple ordinary character variants created
        # before the single-identity rule. Reuse one ordinary asset only;
        # retain extra assets only when they are explicit transformations.
        if entity_type == "character":
            ordinary = [item for item in matching_rows if dict(item[0]).get("variant_type", "normal") != "transformation"]
            transformed = [item for item in matching_rows if dict(item[0]).get("variant_type", "normal") == "transformation"]
            matching_rows = (ordinary[:1] if ordinary else []) + transformed

        outputs, manifest = [], []
        for row, decision in matching_rows:
            if decision.get("base_fingerprint") != base_fingerprint or row["status"] not in {"approved", "reused"} or not row["generated_asset_id"]:
                return None
            try:
                asset = repo.get_asset(row["generated_asset_id"], project_id)
                if asset["status"] != "approved":
                    return None
                outputs.append(_load_asset_image(asset))
            except (ValueError, FileNotFoundError):
                return None
            manifest.append({
                "group_id": row["group_id"], "entity_type": entity_type,
                "entity_id": entity_id, "variant_key": row["variant_key"],
                "status": "reused", "generated_asset_id": asset["asset_id"],
                "selected_source_asset_id": row["selected_source_asset_id"],
            })
        return outputs, manifest

    def _reuse_source_matched_group(
        self, db: ComicDatabase, repo: AssetRepository, project_id: str,
        chapter_id: str, items: List[Dict[str, Any]],
    ) -> Optional[Tuple[List[Any], List[Dict[str, Any]]]]:
        """Reuse an approved group when its stable source crop is unchanged.

        Entity resolution can assign a different candidate ID on a later run,
        and Gemini can return a different but equivalent variant key. The
        deterministic source-crop asset IDs are more stable than either of
        those values, so an overlapping source crop is sufficient to reuse an
        existing chapter asset. This is intentionally disabled by force_refresh
        at the call site.
        """
        source_ids = {
            item.get("asset", {}).get("asset_id")
            for item in items
            if item.get("asset", {}).get("asset_id")
        }
        if not source_ids:
            return None
        entity_type = items[0]["entity_type"]
        with db.connect() as connection:
            rows = connection.execute(
                """SELECT g.*, s.source_crop_asset_id
                   FROM chapter_asset_groups g
                   JOIN chapter_asset_group_sources s ON s.group_id = g.group_id
                  WHERE g.project_id = ? AND g.chapter_id = ?
                    AND g.entity_type = ?
                    AND g.status IN ('approved', 'reused')
                    AND g.generated_asset_id IS NOT NULL
                  ORDER BY g.updated_at DESC, g.group_id""",
                (project_id, chapter_id, entity_type),
            ).fetchall()
        candidates = [row for row in rows if row["source_crop_asset_id"] in source_ids]
        if not candidates:
            return None
        selected_group_id = candidates[0]["group_id"]
        group_rows = [row for row in candidates if row["group_id"] == selected_group_id]
        row = group_rows[0]
        try:
            asset = repo.get_asset(row["generated_asset_id"], project_id)
            if asset["status"] != "approved":
                return None
            image = _load_asset_image(asset)
        except (ValueError, FileNotFoundError, OSError):
            return None
        manifest = [{
            "group_id": row["group_id"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "variant_key": row["variant_key"],
            "status": "reused",
            "generated_asset_id": asset["asset_id"],
            "selected_source_asset_id": row["selected_source_asset_id"],
            "reuse_reason": "matched_existing_source_crop",
        }]
        return [image], manifest

    def _reuse_bound_entity_asset(
        self, db: ComicDatabase, repo: AssetRepository, project_id: str,
        chapter_id: str, entity_type: str, entity_id: str,
    ) -> Optional[Tuple[List[Any], List[Dict[str, Any]]]]:
        """Reuse the approved chapter asset already bound in SQLite.

        This is the primary durable cache. It deliberately does not inspect
        ComfyUI execution/cache state, fingerprints, model parameters, or
        Gemini output. An approved chapter binding is authoritative until the
        asset is deleted or force_refresh is requested.
        """
        if not entity_id:
            return None
        with db.connect() as connection:
            rows = connection.execute(
                """SELECT a.*, r.relation_type, g.group_id, g.variant_key
                   FROM assets a
                   JOIN asset_relations r ON r.asset_id = a.asset_id
                   LEFT JOIN chapter_asset_groups g
                     ON g.generated_asset_id = a.asset_id
                    AND g.project_id = a.project_id
                    AND g.chapter_id = ?
                  WHERE a.project_id = ? AND a.status = 'approved'
                    AND r.entity_type = ? AND r.entity_id = ?
                    AND r.relation_type LIKE ?
                    AND a.asset_type IN (?, ?)
                  ORDER BY CASE WHEN g.variant_key IN ('normal', 'default') THEN 0 ELSE 1 END,
                           a.updated_at DESC, a.asset_id""",
                (
                    chapter_id, project_id, entity_type, entity_id,
                    "chapter:{}:%".format(chapter_id),
                    "character_turnaround" if entity_type == "character" else "scene_reference",
                    "character_portrait" if entity_type == "character" else "scene_reference",
                ),
            ).fetchall()
        if not rows:
            return None
        row = rows[0]
        if row["group_id"]:
            with db.connect() as connection:
                connection.execute(
                    "UPDATE chapter_asset_groups SET status='reused', updated_at=CURRENT_TIMESTAMP WHERE group_id=?",
                    (row["group_id"],),
                )
        try:
            image = _load_asset_image(dict(row))
        except (ValueError, FileNotFoundError, OSError):
            return None
        return [image], [{
            "group_id": row["group_id"],
            "entity_type": entity_type,
            "entity_id": entity_id,
            "variant_key": row["variant_key"] or "default",
            "status": "reused",
            "generated_asset_id": row["asset_id"],
            "selected_source_asset_id": None,
            "reuse_reason": "sqlite_entity_binding",
        }]

    def generate(self, project_id: str, chapter_id: str, unet_name: str, clip_name: str, vae_name: str, seed: int, steps: int, cfg: float, max_generation_retries: int, gemini_api_key: str, gemini_model: str, api_base_url: str, timeout_seconds: int, api_retries: int, enable_upscale: bool = True, force_refresh: bool = False):
        db = self._db()
        repo = AssetRepository(db)
        repo.initialize()
        pages = self._pages(db, project_id, chapter_id)
        analyses = self._analyses(db, pages)
        context = self._context(db, project_id, chapter_id)
        sources = self._sources(db, repo, analyses, project_id)
        base_groups = self._base_groups(sources)
        args = locals().copy()
        args["gemini_api_key"] = gemini_api_key
        args["gemini_model"] = gemini_model
        args["api_base_url"] = api_base_url
        args["timeout_seconds"] = timeout_seconds
        args["api_retries"] = api_retries
        args["max_generation_retries"] = max_generation_retries
        args["enable_upscale"] = bool(enable_upscale)
        outputs, manifest, errors = [], [], []
        for items in base_groups:
            entity_type = items[0]["entity_type"]
            entity_id = items[0]["entity_id"]
            base_fingerprint = _fingerprint({
                "project_id": project_id,
                "chapter_id": chapter_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "sources": [{"hash": item["asset"]["file_hash"], "quality": item["reference_quality"]} for item in items],
                "context": context,
                "gemini_prompt_version": "chapter-v1",
                "gemini_model": gemini_model,
                "qwen_parameters": {
                    "unet_name": unet_name,
                    "clip_name": clip_name,
                    "vae_name": vae_name,
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                },
                "post_qc_upscale": {"enabled": bool(enable_upscale)},
                "chapter_asset_pipeline_version": "chapter-v2-upscale-4096",
            })
            if not force_refresh:
                # SQLite entity binding is authoritative and must be checked
                # before any fingerprint/Gemini/Qwen path.
                reused = self._reuse_bound_entity_asset(
                    db, repo, project_id, chapter_id, entity_type, entity_id,
                )
                if reused is not None:
                    reused_outputs, reused_manifest = reused
                    outputs.extend(reused_outputs)
                    manifest.extend(reused_manifest)
                    for reused_item in reused_manifest:
                        self._bind_group_to_pages(
                            db, project_id, chapter_id,
                            reused_item["group_id"], reused_item["generated_asset_id"],
                            entity_type, entity_id, reused_item["variant_key"], items,
                        )
                    continue
                reused = self._reuse_entity_groups(
                    db, repo, project_id, chapter_id, entity_type, entity_id,
                    base_fingerprint,
                )
                if reused is not None:
                    reused_outputs, reused_manifest = reused
                    outputs.extend(reused_outputs)
                    manifest.extend(reused_manifest)
                    for reused_item in reused_manifest:
                        self._bind_group_to_pages(
                            db, project_id, chapter_id,
                            reused_item["group_id"], reused_item["generated_asset_id"],
                            entity_type, entity_id, reused_item["variant_key"], items,
                        )
                    continue
                # Fall back to stable source-crop matching when entity IDs,
                # variant keys, or fingerprints changed during re-analysis.
                # Existing approved assets must win over a new generation.
                reused = self._reuse_source_matched_group(
                    db, repo, project_id, chapter_id, items,
                )
                if reused is not None:
                    reused_outputs, reused_manifest = reused
                    outputs.extend(reused_outputs)
                    manifest.extend(reused_manifest)
                    for reused_item in reused_manifest:
                        self._bind_group_to_pages(
                            db, project_id, chapter_id,
                            reused_item["group_id"], reused_item["generated_asset_id"],
                            entity_type, entity_id, reused_item["variant_key"], items,
                        )
                    continue
            groups = self._group_with_gemini(items, context, gemini_model, gemini_api_key, api_base_url, timeout_seconds, api_retries)
            for group in groups:
                selected = items[group["selected_source_index"]]
                descriptions = []
                for source_index in group["source_indices"]:
                    description = items[source_index].get("description", {})
                    if description and description not in descriptions:
                        descriptions.append(description)
                request = {
                    "project_id": project_id, "chapter_id": chapter_id,
                    "entity_type": entity_type, "entity_id": entity_id,
                    "variant_key": group["variant_key"],
                    "entity_description": descriptions,
                    "source_fingerprint": _fingerprint({"base": base_fingerprint, "group": group, "context": context}),
                }
                with db.connect() as connection:
                    existing = connection.execute("SELECT * FROM chapter_asset_groups WHERE project_id = ? AND chapter_id = ? AND entity_type = ? AND entity_id = ? AND variant_key = ?", (project_id, chapter_id, entity_type, entity_id, group["variant_key"])).fetchone()
                if existing and not force_refresh and existing["source_fingerprint"] == request["source_fingerprint"] and existing["generated_asset_id"]:
                    try:
                        asset = repo.get_asset(existing["generated_asset_id"], project_id)
                        if asset["status"] == "approved":
                            outputs.append(_load_asset_image(asset))
                            self._bind_group_to_pages(db, project_id, chapter_id, existing["group_id"], asset["asset_id"], entity_type, entity_id, group["variant_key"], items)
                            manifest.append({"group_id": existing["group_id"], "entity_type": entity_type, "entity_id": entity_id, "variant_key": group["variant_key"], "status": "reused", "generated_asset_id": asset["asset_id"], "selected_source_asset_id": selected["asset"]["asset_id"]})
                            continue
                    except ValueError:
                        pass
                group_id = existing["group_id"] if existing else str(uuid.uuid4())
                with db.connect() as connection:
                    connection.execute("INSERT INTO chapter_asset_groups(group_id,project_id,chapter_id,entity_type,entity_id,variant_key,variant_type,variant_description,source_fingerprint,status,decision_json) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,chapter_id,entity_type,entity_id,variant_key) DO UPDATE SET variant_type=excluded.variant_type,variant_description=excluded.variant_description,source_fingerprint=excluded.source_fingerprint,status=excluded.status,decision_json=excluded.decision_json,updated_at=CURRENT_TIMESTAMP", (group_id, project_id, chapter_id, entity_type, entity_id, group["variant_key"], group["variant_type"], group["variant_description"], request["source_fingerprint"], "generating", json.dumps({"base_fingerprint": base_fingerprint, "group": group}, ensure_ascii=False)))
                    connection.execute("DELETE FROM chapter_asset_group_sources WHERE group_id = ?", (group_id,))
                    for index in group["source_indices"]:
                        item = items[index]
                        connection.execute("INSERT INTO chapter_asset_group_sources(group_id,source_crop_asset_id,source_page_id,panel_index,reference_quality,is_selected,evidence_json) VALUES(?,?,?,?,?,?,?)", (group_id, item["asset"]["asset_id"], item["source_page_id"], item["panel_index"], item["reference_quality"], int(index == group["selected_source_index"]), json.dumps(item["evidence"], ensure_ascii=False)))
                asset, image, report = self._generate_one(request, selected, group, context, args, repo)
                status = "approved" if asset else "retry" if report.get("decision") == "RETRY" else "error"
                with db.connect() as connection:
                    connection.execute("UPDATE chapter_asset_groups SET selected_source_asset_id=?,generated_asset_id=?,status=?,decision_json=?,updated_at=CURRENT_TIMESTAMP WHERE group_id=?", (selected["asset"]["asset_id"], asset["asset_id"] if asset else None, status, json.dumps({"base_fingerprint": base_fingerprint, "group": group, "report": report}, ensure_ascii=False), group_id))
                item_report = {"group_id": group_id, "entity_type": entity_type, "entity_id": entity_id, "variant_key": group["variant_key"], "status": status, "selected_source_asset_id": selected["asset"]["asset_id"], "generated_asset_id": asset["asset_id"] if asset else None, "report": report}
                manifest.append(item_report)
                if asset and image is not None:
                    outputs.append(image)
                    self._bind_group_to_pages(db, project_id, chapter_id, group_id, asset["asset_id"], entity_type, entity_id, group["variant_key"], items)
                if not asset:
                    errors.append(item_report)
        report = {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "groups": len(manifest),
            "approved_groups": sum(item.get("status") in {"approved", "reused"} for item in manifest),
            "output_image_count": len(outputs),
            "output_asset_ids": [
                item.get("generated_asset_id")
                for item in manifest
                if item.get("status") in {"approved", "reused"}
                and item.get("generated_asset_id")
            ],
            "errors": errors,
        }
        if not outputs:
            raise ValueError(
                "Chapter asset generation produced no approved or reused image assets. "
                "Check the chapter analyses, entity bindings, model settings, and Gemini/QC report: "
                + json.dumps(report, ensure_ascii=False)
            )
        return _stack_batch_images(outputs), json.dumps(manifest, ensure_ascii=False), json.dumps(report, ensure_ascii=False)


NODE_CLASS_MAPPINGS = {"ComicChapterAssetGeneration": ComicChapterAssetGeneration}
NODE_DISPLAY_NAME_MAPPINGS = {"ComicChapterAssetGeneration": "Comic Chapter Asset Generation"}

"""Persistent asset files and metadata for Comic2Anime."""

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .database import ComicDatabase
from .storage import get_data_root, safe_component


ASSET_TYPES = (
    "character_identity",
    "character_turnaround",
    "character_portrait",
    "character_expression",
    "character_pose",
    "character_outfit",
    "character_state",
    "character_face_detail",
    "character_prop_reference",
    "scene_reference",
    "scene_state_reference",
    "scene_composite_reference",
    "panel_reference",
    "prop_reference",
    "other",
)

ENTITY_TYPES = (
    "character",
    "character_appearance",
    "location",
    "location_state",
    "page",
    "panel",
    "story_event",
)

ASSET_STATUSES = (
    "candidate",
    "approved",
    "rejected",
    "needs_revision",
    "archived",
)

_SENSITIVE_JSON_KEYS = {
    "apikey",
    "token",
    "accesstoken",
    "refreshtoken",
    "secret",
    "clientsecret",
    "password",
    "authorization",
    "credential",
}

_ENTITY_EXISTS_SQL = {
    "character": (
        "SELECT 1 FROM characters WHERE character_id = ? AND project_id = ?"
    ),
    "character_appearance": (
        "SELECT 1 FROM character_appearances a "
        "JOIN characters c ON c.character_id = a.character_id "
        "WHERE a.appearance_id = ? AND c.project_id = ? "
        "AND EXISTS (SELECT 1 FROM pages p WHERE p.page_id = a.page_id "
        "AND p.project_id = c.project_id)"
    ),
    "location": (
        "SELECT 1 FROM locations WHERE location_id = ? AND project_id = ?"
    ),
    "location_state": (
        "SELECT 1 FROM location_states s "
        "JOIN locations l ON l.location_id = s.location_id "
        "WHERE s.state_id = ? AND l.project_id = ? "
        "AND EXISTS (SELECT 1 FROM pages p WHERE p.page_id = s.page_id "
        "AND p.project_id = l.project_id)"
    ),
    "page": "SELECT 1 FROM pages WHERE page_id = ? AND project_id = ?",
    "panel": (
        "SELECT 1 FROM panels p JOIN pages pg ON pg.page_id = p.page_id "
        "WHERE p.panel_id = ? AND pg.project_id = ?"
    ),
    "story_event": (
        "SELECT 1 FROM story_events e WHERE e.event_id = ? AND e.project_id = ? "
        "AND EXISTS (SELECT 1 FROM pages p WHERE p.page_id = e.page_id "
        "AND p.project_id = e.project_id)"
    ),
}


def _json_object(value: Any, field_name: str) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("{} must contain valid JSON.".format(field_name)) from exc
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise ValueError("{} must be a JSON object.".format(field_name))
    parsed = _redact_sensitive_json(parsed)
    try:
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("{} contains unsupported values.".format(field_name)) from exc


def _seed_value(seed: Optional[int]) -> Optional[str]:
    if seed is None:
        return None
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ValueError("seed must be an unsigned 64-bit integer.")
    return str(seed)


def _redact_sensitive_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings.")
            normalized_key = "".join(
                character for character in key.lower() if character.isalnum()
            )
            if any(marker in normalized_key for marker in _SENSITIVE_JSON_KEYS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_sensitive_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_json(item) for item in value]
    return value


def _json_string_list(value: Any, field_name: str) -> List[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError("{} must contain a JSON string list.".format(field_name)) from exc
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError("{} must contain a JSON string list.".format(field_name))
    if len(value) != len(set(value)):
        raise ValueError("{} cannot contain duplicate IDs.".format(field_name))
    return value


class AssetRepository:
    """Stores generated files under the configured data root and indexes them in SQLite."""

    def __init__(
        self,
        database: Optional[ComicDatabase] = None,
        asset_root: Optional[Path] = None,
    ) -> None:
        self.data_root = get_data_root()
        self.database = database or ComicDatabase(
            self.data_root / "comic2anime.sqlite3"
        )
        self.asset_root = Path(asset_root or self.data_root / "assets").resolve()

    def initialize(self) -> None:
        self.database.initialize()

    def _entity_exists(
        self, connection: Any, project_id: str, entity_type: str, entity_id: str
    ) -> bool:
        query = _ENTITY_EXISTS_SQL.get(entity_type)
        if query is None:
            raise ValueError("Unsupported entity type: {}".format(entity_type))
        return connection.execute(query, (entity_id, project_id)).fetchone() is not None

    def _validate_entity(
        self, connection: Any, project_id: str, entity_type: str, entity_id: str
    ) -> None:
        if not self._entity_exists(connection, project_id, entity_type, entity_id):
            raise ValueError(
                "Entity {}:{} does not exist in project {}.".format(
                    entity_type, entity_id, project_id
                )
            )

    def _asset_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or "\\" in relative_path or ":" in relative_path:
            raise ValueError("Asset paths must be relative POSIX paths.")
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("Asset path escapes the configured asset directory.")
        root = self.asset_root.resolve()
        candidate = (root / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "Asset path escapes the configured asset directory."
            ) from exc
        return candidate

    def resolve_asset_path(self, asset: Dict[str, Any]) -> Path:
        return self._asset_path(asset["relative_path"])

    def get_asset(self, asset_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        with self.database.connect() as connection:
            if project_id is None:
                row = connection.execute(
                    "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM assets WHERE asset_id = ? AND project_id = ?",
                    (asset_id, project_id),
                ).fetchone()
            if row is None:
                raise ValueError("Asset {} was not found.".format(asset_id))
            return dict(row)

    def _validate_references(
        self, connection: Any, project_id: str, reference_asset_ids: Sequence[str]
    ) -> None:
        for asset_id in reference_asset_ids:
            row = connection.execute(
                "SELECT project_id FROM assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if row is None or row["project_id"] != project_id:
                raise ValueError(
                    "Reference asset {} does not belong to project {}.".format(
                        asset_id, project_id
                    )
                )

    def _write_file(self, relative_path: str, content: bytes) -> Path:
        destination = self._asset_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        parent = destination.parent.resolve()
        try:
            parent.relative_to(self.asset_root.resolve())
        except ValueError as exc:
            raise ValueError("Asset directory escapes the configured root.") from exc
        with destination.open("xb") as output:
            try:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            except Exception:
                if destination.exists():
                    destination.unlink()
                raise
        return destination

    def _save_database_records(
        self,
        run: Dict[str, Any],
        assets: Sequence[Dict[str, Any]],
        entity_type: Optional[str],
        entity_id: Optional[str],
        relation_type: str,
    ) -> None:
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (run["project_id"],)
            ).fetchone() is None:
                raise ValueError("Project {} does not exist.".format(run["project_id"]))
            self._validate_references(
                connection, run["project_id"], run["reference_asset_ids"]
            )
            if entity_type and entity_id:
                self._validate_entity(
                    connection, run["project_id"], entity_type, entity_id
                )
            if run["supersedes_asset_id"]:
                supersedes = connection.execute(
                    "SELECT project_id FROM assets WHERE asset_id = ?",
                    (run["supersedes_asset_id"],),
                ).fetchone()
                if supersedes is None or supersedes["project_id"] != run["project_id"]:
                    raise ValueError(
                        "Superseded asset must belong to the same project."
                    )

            existing_run = connection.execute(
                "SELECT * FROM asset_generation_runs WHERE run_id = ?",
                (run["run_id"],),
            ).fetchone()
            if existing_run is not None:
                raise ValueError("Generation run ID already exists.")
            connection.execute(
                """INSERT INTO asset_generation_runs(
                    run_id, project_id, status, asset_type, prompt, prompt_version,
                    model_name, model_version, seed, parameters_json,
                    reference_asset_ids_json, error_message, started_at, finished_at
                ) VALUES (?, ?, 'succeeded', ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (
                    run["run_id"],
                    run["project_id"],
                    run["asset_type"],
                    run["prompt"],
                    run["prompt_version"],
                    run["model_name"],
                    run["model_version"],
                    run["seed"],
                    run["parameters_json"],
                    json.dumps(
                        run["reference_asset_ids"], ensure_ascii=False
                    ),
                ),
            )
            for output_index, asset in enumerate(assets):
                connection.execute(
                    """INSERT INTO assets(
                        asset_id, project_id, asset_type, display_name,
                        relative_path, file_hash, mime_type, width, height,
                        file_size, status, metadata_json, supersedes_asset_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)""",
                    (
                        asset["asset_id"],
                        run["project_id"],
                        run["asset_type"],
                        asset["display_name"],
                        asset["relative_path"],
                        asset["file_hash"],
                        asset["mime_type"],
                        asset["width"],
                        asset["height"],
                        asset["file_size"],
                        asset["metadata_json"],
                        run["supersedes_asset_id"],
                    ),
                )
                connection.execute(
                    """INSERT INTO asset_generation_outputs(
                        run_id, asset_id, output_index
                    ) VALUES (?, ?, ?)""",
                    (run["run_id"], asset["asset_id"], output_index),
                )
                if entity_type and entity_id:
                    connection.execute(
                        """INSERT INTO asset_relations(
                            relation_id, project_id, asset_id, entity_type,
                            entity_id, relation_type
                        ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            run["project_id"],
                            asset["asset_id"],
                            entity_type,
                            entity_id,
                            relation_type,
                        ),
                    )

    def record_generation_failure(
        self,
        project_id: str,
        run_id: str,
        asset_type: str,
        error_message: str,
        prompt: str = "",
        prompt_version: str = "",
        model_name: str = "",
        model_version: Optional[str] = None,
        seed: Optional[int] = None,
        parameters: Any = None,
        reference_asset_ids: Any = None,
    ) -> None:
        references = _json_string_list(
            reference_asset_ids or [], "reference_asset_ids"
        )
        parameters_json = _json_object(parameters or {}, "parameters_json")
        stored_seed = _seed_value(seed)
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM asset_generation_runs WHERE run_id = ?", (run_id,)
            ).fetchone():
                return
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ValueError("Cannot record a failed run for an unknown project.")
            connection.execute(
                """INSERT INTO asset_generation_runs(
                    run_id, project_id, status, asset_type, prompt, prompt_version,
                    model_name, model_version, seed, parameters_json,
                    reference_asset_ids_json, error_message, finished_at
                ) VALUES (?, ?, 'failed', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CURRENT_TIMESTAMP)""",
                (
                    run_id,
                    project_id,
                    asset_type,
                    prompt,
                    prompt_version,
                    model_name,
                    model_version,
                    stored_seed,
                    parameters_json,
                    json.dumps(references, ensure_ascii=False),
                    error_message[:4000],
                ),
            )

    def register_batch(
        self,
        project_id: str,
        asset_type: str,
        images: Sequence[Dict[str, Any]],
        display_name: str = "",
        generation_run_id: str = "",
        prompt: str = "",
        prompt_version: str = "",
        model_name: str = "",
        model_version: Optional[str] = None,
        seed: Optional[int] = None,
        parameters: Any = None,
        reference_asset_ids: Any = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        relation_type: str = "",
        supersedes_asset_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not project_id.strip():
            raise ValueError("project_id is required.")
        if asset_type not in ASSET_TYPES:
            raise ValueError("Unsupported asset_type: {}".format(asset_type))
        if not images:
            raise ValueError("At least one image is required.")
        if bool(entity_type) != bool(entity_id):
            raise ValueError("entity_type and entity_id must be supplied together.")
        if entity_type and entity_type not in ENTITY_TYPES:
            raise ValueError("Unsupported entity_type: {}".format(entity_type))
        run_id = generation_run_id.strip() or str(uuid.uuid4())
        references = _json_string_list(
            reference_asset_ids or [], "reference_asset_ids"
        )
        parameters_json = _json_object(parameters or {}, "parameters_json")
        stored_seed = _seed_value(seed)
        name_prefix = display_name.strip()
        safe_project = safe_component(project_id)
        relative_dir = PurePosixPath(safe_project) / asset_type
        run = {
            "run_id": run_id,
            "project_id": project_id,
            "asset_type": asset_type,
            "prompt": prompt,
            "prompt_version": prompt_version,
            "model_name": model_name,
            "model_version": model_version,
            "seed": stored_seed,
            "parameters_json": parameters_json,
            "reference_asset_ids": references,
            "supersedes_asset_id": supersedes_asset_id,
        }
        assets: List[Dict[str, Any]] = []
        for output_index, image in enumerate(images):
            content = image.get("content")
            if not isinstance(content, bytes) or not content:
                raise ValueError("Image content must be non-empty PNG bytes.")
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Image content must be encoded as a PNG.")
            if (
                isinstance(image.get("width"), bool)
                or isinstance(image.get("height"), bool)
                or int(image["width"]) < 1
                or int(image["height"]) < 1
            ):
                raise ValueError("Image width and height must be positive integers.")
            if image.get("mime_type", "image/png") != "image/png":
                raise ValueError("PNG image content must use the image/png MIME type.")
            asset_id = str(uuid.uuid4())
            file_name = "{}.png".format(asset_id)
            relative_path = (relative_dir / file_name).as_posix()
            file_hash = hashlib.sha256(content).hexdigest()
            metadata_json = _json_object(
                image.get("metadata", {}), "metadata_json"
            )
            assets.append(
                {
                    "asset_id": asset_id,
                    "relative_path": relative_path,
                    "display_name": (
                        "{} {}".format(name_prefix, output_index + 1).strip()
                        if len(images) > 1
                        else name_prefix
                    ),
                    "file_hash": file_hash,
                    "mime_type": image.get("mime_type", "image/png"),
                    "width": int(image["width"]),
                    "height": int(image["height"]),
                    "file_size": len(content),
                    "metadata_json": metadata_json,
                    "content": content,
                }
            )

        # Validate IDs before writing files; the final transaction repeats these checks.
        replay_result = None
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ValueError("Project {} does not exist.".format(project_id))
            self._validate_references(connection, project_id, references)
            if entity_type and entity_id:
                self._validate_entity(connection, project_id, entity_type, entity_id)
            existing_run = connection.execute(
                "SELECT * FROM asset_generation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing_run is not None:
                expected_references = json.dumps(references, ensure_ascii=False)
                if (
                    existing_run["project_id"] != project_id
                    or existing_run["asset_type"] != asset_type
                    or existing_run["prompt"] != prompt
                    or existing_run["prompt_version"] != prompt_version
                    or existing_run["model_name"] != model_name
                    or existing_run["model_version"] != model_version
                    or existing_run["seed"] != stored_seed
                    or existing_run["parameters_json"] != parameters_json
                    or existing_run["reference_asset_ids_json"] != expected_references
                    or existing_run["status"] != "succeeded"
                ):
                    raise ValueError(
                        "Generation run ID was already used with different parameters."
                    )
                previous = connection.execute(
                    """SELECT a.*, o.output_index FROM asset_generation_outputs o
                        JOIN assets a ON a.asset_id = o.asset_id
                        WHERE o.run_id = ? ORDER BY o.output_index""",
                    (run_id,),
                ).fetchall()
                if len(previous) != len(assets) or any(
                    row["output_index"] != index
                    or row["file_hash"] != assets[index]["file_hash"]
                    or row["display_name"] != assets[index]["display_name"]
                    for index, row in enumerate(previous)
                ):
                    raise ValueError(
                        "Generation run retry produced different output images."
                    )
                replay_assets = []
                for row in previous:
                    asset = dict(row)
                    asset.pop("output_index")
                    relations = connection.execute(
                        """SELECT entity_type, entity_id, relation_type, is_primary
                            FROM asset_relations WHERE asset_id = ?
                            ORDER BY entity_type, entity_id, relation_type""",
                        (asset["asset_id"],),
                    ).fetchall()
                    asset["relations"] = [dict(item) for item in relations]
                    if entity_type and entity_id and not any(
                        relation["entity_type"] == entity_type
                        and relation["entity_id"] == entity_id
                        and relation["relation_type"]
                        == (relation_type or asset_type)
                        for relation in asset["relations"]
                    ):
                        raise ValueError(
                            "Generation run ID was already used for another entity."
                        )
                    replay_assets.append(asset)
                replay_result = {
                    "run_id": run_id,
                    "assets": replay_assets,
                    "report": {
                        "created": 0,
                        "reused": len(replay_assets),
                        "failed": 0,
                        "warnings": [],
                    },
                }

        if replay_result is not None:
            return replay_result

        created_files: List[Path] = []
        try:
            for asset in assets:
                destination = self._write_file(
                    asset["relative_path"], asset["content"]
                )
                created_files.append(destination)
            self._save_database_records(
                run, assets, entity_type, entity_id, relation_type or asset_type
            )
        except Exception:
            for path in created_files:
                if path.exists():
                    path.unlink()
            raise

        persisted_assets = []
        with self.database.connect() as connection:
            for source_asset in assets:
                row = connection.execute(
                    "SELECT * FROM assets WHERE asset_id = ?",
                    (source_asset["asset_id"],),
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        "Asset transaction completed without an asset record."
                    )
                asset = dict(row)
                relations = connection.execute(
                    """SELECT entity_type, entity_id, relation_type, is_primary
                        FROM asset_relations WHERE asset_id = ?
                        ORDER BY entity_type, entity_id, relation_type""",
                    (asset["asset_id"],),
                ).fetchall()
                asset["relations"] = [dict(item) for item in relations]
                persisted_assets.append(asset)
        return {
            "run_id": run_id,
            "assets": persisted_assets,
            "report": {
                "created": len(persisted_assets),
                "reused": 0,
                "failed": 0,
                "warnings": [],
            },
        }

    def query_assets(
        self,
        project_id: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        asset_type: Optional[str] = None,
        statuses: Optional[Sequence[str]] = ("approved",),
        primary_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if not project_id.strip():
            raise ValueError("project_id is required for asset queries.")
        if bool(entity_type) != bool(entity_id):
            raise ValueError("entity_type and entity_id must be supplied together.")
        if entity_type and entity_type not in ENTITY_TYPES:
            raise ValueError("Unsupported entity_type: {}".format(entity_type))
        if asset_type and asset_type not in ASSET_TYPES:
            raise ValueError("Unsupported asset_type: {}".format(asset_type))
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        if statuses is not None and any(status not in ASSET_STATUSES for status in statuses):
            raise ValueError("Unsupported asset status.")

        clauses = ["a.project_id = ?"]
        parameters: List[Any] = [project_id]
        if asset_type:
            clauses.append("a.asset_type = ?")
            parameters.append(asset_type)
        if statuses is not None:
            if not statuses:
                return []
            placeholders = ",".join("?" for _ in statuses)
            clauses.append("a.status IN ({})".format(placeholders))
            parameters.extend(statuses)
        if entity_type and entity_id:
            clauses.append(
                """EXISTS (SELECT 1 FROM asset_relations r
                    WHERE r.asset_id = a.asset_id AND r.project_id = a.project_id
                    AND r.entity_type = ? AND r.entity_id = ?)"""
            )
            parameters.extend((entity_type, entity_id))
        if primary_only:
            clauses.append(
                """EXISTS (SELECT 1 FROM asset_relations r
                    WHERE r.asset_id = a.asset_id AND r.project_id = a.project_id
                    AND r.is_primary = 1)"""
            )

        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ValueError("Project {} does not exist.".format(project_id))
            rows = connection.execute(
                """SELECT a.* FROM assets a WHERE {}
                    ORDER BY a.created_at DESC, a.asset_id LIMIT ?""".format(
                    " AND ".join(clauses)
                ),
                (*parameters, limit),
            ).fetchall()
            results = []
            for row in rows:
                asset = dict(row)
                relations = connection.execute(
                    """SELECT entity_type, entity_id, relation_type, is_primary
                        FROM asset_relations WHERE asset_id = ?
                        ORDER BY entity_type, entity_id, relation_type""",
                    (asset["asset_id"],),
                ).fetchall()
                asset["relations"] = [dict(relation) for relation in relations]
                results.append(asset)
            return results

    def review_asset(
        self,
        project_id: str,
        asset_id: str,
        review_status: str,
        review_notes: str = "",
        set_primary: bool = False,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        relation_type: str = "",
    ) -> Dict[str, Any]:
        if review_status not in ("approved", "rejected", "needs_revision"):
            raise ValueError("Unsupported review status.")
        if set_primary and (not entity_type or not entity_id):
            raise ValueError("Setting a primary asset requires an entity.")
        if bool(entity_type) != bool(entity_id):
            raise ValueError("entity_type and entity_id must be supplied together.")
        with self.database.connect() as connection:
            asset = connection.execute(
                "SELECT * FROM assets WHERE asset_id = ? AND project_id = ?",
                (asset_id, project_id),
            ).fetchone()
            if asset is None:
                raise ValueError("Asset does not belong to the selected project.")
            if set_primary:
                if review_status != "approved":
                    raise ValueError("Only an approved asset can be primary.")
                self._validate_entity(
                    connection, project_id, entity_type, entity_id
                )
                relation = relation_type.strip() or asset["asset_type"]
                connection.execute(
                    """UPDATE asset_relations SET is_primary = 0
                        WHERE project_id = ? AND entity_type = ? AND entity_id = ?
                        AND relation_type = ?""",
                    (project_id, entity_type, entity_id, relation),
                )
                connection.execute(
                    """INSERT INTO asset_relations(
                        relation_id, project_id, asset_id, entity_type, entity_id,
                        relation_type, is_primary
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(asset_id, entity_type, entity_id, relation_type)
                    DO UPDATE SET is_primary = 1""",
                    (
                        str(uuid.uuid4()),
                        project_id,
                        asset_id,
                        entity_type,
                        entity_id,
                        relation,
                    ),
                )
            elif review_status != "approved":
                connection.execute(
                    "UPDATE asset_relations SET is_primary = 0 "
                    "WHERE asset_id = ? AND project_id = ?",
                    (asset_id, project_id),
                )
            connection.execute(
                """UPDATE assets SET status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = ? AND project_id = ?""",
                (review_status, asset_id, project_id),
            )
            connection.execute(
                """INSERT INTO asset_reviews(
                    review_id, asset_id, review_status, review_notes
                ) VALUES (?, ?, ?, ?)""",
                (str(uuid.uuid4()), asset_id, review_status, review_notes),
            )
        return self.get_asset(asset_id, project_id)

    def get_entity_context(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> Dict[str, Any]:
        if entity_type not in ENTITY_TYPES:
            raise ValueError("Unsupported entity_type: {}".format(entity_type))
        queries = {
            "character": (
                """SELECT character_id AS entity_id, canonical_name AS name,
                    stable_description AS description, status
                    FROM characters WHERE character_id = ? AND project_id = ?""",
                (entity_id, project_id),
            ),
            "character_appearance": (
                """SELECT a.appearance_id AS entity_id, c.character_id,
                    c.canonical_name AS name, c.stable_description,
                    a.appearance_description AS description, a.clothing,
                    a.expression, a.action, a.emotion, a.page_id, a.panel_index
                    FROM character_appearances a
                    JOIN characters c ON c.character_id = a.character_id
                    WHERE a.appearance_id = ? AND c.project_id = ?
                    AND EXISTS (SELECT 1 FROM pages p WHERE p.page_id = a.page_id
                        AND p.project_id = c.project_id)""",
                (entity_id, project_id),
            ),
            "location": (
                """SELECT location_id AS entity_id, canonical_name AS name,
                    stable_description AS description, status
                    FROM locations WHERE location_id = ? AND project_id = ?""",
                (entity_id, project_id),
            ),
            "location_state": (
                """SELECT s.state_id AS entity_id, l.location_id, l.canonical_name AS name,
                    l.stable_description, s.description, s.visual_state, s.page_id,
                    s.panel_index FROM location_states s
                    JOIN locations l ON l.location_id = s.location_id
                    WHERE s.state_id = ? AND l.project_id = ?
                    AND EXISTS (SELECT 1 FROM pages p WHERE p.page_id = s.page_id
                        AND p.project_id = l.project_id)""",
                (entity_id, project_id),
            ),
            "page": (
                """SELECT page_id AS entity_id, chapter_id, page_index,
                    source_path, image_path, sha256, width, height
                    FROM pages WHERE page_id = ? AND project_id = ?""",
                (entity_id, project_id),
            ),
            "panel": (
                """SELECT p.panel_id AS entity_id, p.description, p.panel_index,
                    p.reading_order, pg.page_id, pg.source_path, pg.image_path
                    FROM panels p JOIN pages pg ON pg.page_id = p.page_id
                    WHERE p.panel_id = ? AND pg.project_id = ?""",
                (entity_id, project_id),
            ),
            "story_event": (
                """SELECT event_id AS entity_id, chapter_id, page_id,
                    panel_index, description, participants_json, confidence
                    FROM story_events e WHERE e.event_id = ? AND e.project_id = ?
                    AND EXISTS (SELECT 1 FROM pages p WHERE p.page_id = e.page_id
                        AND p.project_id = e.project_id)""",
                (entity_id, project_id),
            ),
        }
        query, parameters = queries[entity_type]
        with self.database.connect() as connection:
            row = connection.execute(query, parameters).fetchone()
            if row is None:
                raise ValueError(
                    "Entity {}:{} does not exist in project {}.".format(
                        entity_type, entity_id, project_id
                    )
                )
            context = dict(row)
            context["source_evidence"] = []
            context["appearance_variants"] = []
            if entity_type == "character_appearance":
                evidence = connection.execute(
                    """SELECT e.page_id, e.panel_index, e.evidence_json,
                        p.page_index, p.source_path, p.image_path
                        FROM character_appearance_evidence e
                        JOIN pages p ON p.page_id = e.page_id
                        WHERE e.appearance_id = ?
                        ORDER BY p.page_index, e.panel_index""",
                    (entity_id,),
                ).fetchall()
                context["source_evidence"] = [dict(item) for item in evidence]
                if not evidence:
                    page = connection.execute(
                        """SELECT page_id, page_index, source_path, image_path
                            FROM pages WHERE page_id = ? AND project_id = ?""",
                        (context["page_id"], project_id),
                    ).fetchone()
                    if page is not None:
                        context["source_evidence"] = [
                            dict(page, panel_index=context["panel_index"])
                        ]
            elif entity_type == "character":
                variants = connection.execute(
                    """SELECT a.appearance_id, a.page_id, a.panel_index,
                        a.appearance_description, a.clothing, p.page_index,
                        p.source_path, p.image_path
                        FROM character_appearances a
                        JOIN pages p ON p.page_id = a.page_id
                        WHERE a.character_id = ?
                        AND p.project_id = ?
                        ORDER BY p.page_index, a.panel_index, a.appearance_id""",
                    (entity_id, project_id),
                ).fetchall()
                context["appearance_variants"] = [
                    dict(item) for item in variants
                ]
                context["source_evidence"] = [
                    {
                        "page_id": item["page_id"],
                        "page_index": item["page_index"],
                        "panel_index": item["panel_index"],
                        "appearance_description": item["appearance_description"],
                        "clothing": item["clothing"],
                        "source_path": item["source_path"],
                        "image_path": item["image_path"],
                    }
                    for item in variants
                ]
            elif entity_type == "location":
                states = connection.execute(
                    """SELECT s.state_id, s.page_id, s.panel_index,
                        s.description, s.visual_state, p.page_index,
                        p.source_path, p.image_path
                        FROM location_states s
                        JOIN pages p ON p.page_id = s.page_id
                        WHERE s.location_id = ? ORDER BY p.page_index, s.panel_index""",
                    (entity_id,),
                ).fetchall()
                context["location_state_variants"] = [
                    dict(item) for item in states
                ]
                context["source_evidence"] = [
                    {
                        "page_id": item["page_id"],
                        "page_index": item["page_index"],
                        "panel_index": item["panel_index"],
                        "description": item["description"],
                        "visual_state": item["visual_state"],
                        "source_path": item["source_path"],
                        "image_path": item["image_path"],
                    }
                    for item in states
                ]
            elif entity_type == "page":
                context["source_evidence"] = [
                    {
                        "page_id": context["entity_id"],
                        "page_index": context["page_index"],
                        "source_path": context["source_path"],
                        "image_path": context["image_path"],
                    }
                ]
            elif entity_type in ("location_state", "panel", "story_event"):
                if entity_type == "story_event":
                    page_id = context["page_id"]
                    panel_index = context["panel_index"]
                elif entity_type == "panel":
                    page_id = context["page_id"]
                    panel_index = context["panel_index"]
                else:
                    page_id = context["page_id"]
                    panel_index = context["panel_index"]
                page = connection.execute(
                    """SELECT page_id, chapter_id, page_index, source_path,
                        image_path FROM pages WHERE page_id = ?""",
                    (page_id,),
                ).fetchone()
                if page is not None:
                    context["source_evidence"].append(dict(page))
                    context["source_evidence"][-1]["panel_index"] = panel_index
            return context

    def get_reference_assets(
        self,
        project_id: str,
        entity_type: str,
        entity_id: str,
        explicit_ids: Optional[Sequence[str]] = None,
        allow_unapproved: bool = False,
    ) -> List[Dict[str, Any]]:
        statuses = ["approved", "candidate"] if allow_unapproved else ["approved"]
        with self.database.connect() as connection:
            if explicit_ids:
                placeholders = ",".join("?" for _ in explicit_ids)
                rows = connection.execute(
                    """SELECT * FROM assets WHERE project_id = ?
                        AND asset_id IN ({}) ORDER BY asset_id""".format(
                        placeholders
                    ),
                    (project_id, *explicit_ids),
                ).fetchall()
                found = {row["asset_id"]: dict(row) for row in rows}
                if len(found) != len(explicit_ids):
                    raise ValueError(
                        "One or more reference assets do not belong to this project."
                    )
                ordered = [found[asset_id] for asset_id in explicit_ids]
                disallowed = [
                    asset["asset_id"]
                    for asset in ordered
                    if asset["status"] not in statuses
                ]
                if disallowed:
                    raise ValueError(
                        "Unapproved references require explicit permission: "
                        + ", ".join(disallowed)
                    )
                return ordered

            rows = connection.execute(
                """SELECT a.* FROM assets a JOIN asset_relations r
                    ON r.asset_id = a.asset_id AND r.project_id = a.project_id
                    WHERE a.project_id = ? AND r.entity_type = ? AND r.entity_id = ?
                    AND a.status IN ({})
                    ORDER BY r.is_primary DESC, a.created_at DESC, a.asset_id""".format(
                    ",".join("?" for _ in statuses)
                ),
                (project_id, entity_type, entity_id, *statuses),
            ).fetchall()
            return [dict(row) for row in rows]

    def load_asset_records(
        self, project_id: str, asset_ids: Sequence[str], allow_unapproved: bool = False
    ) -> List[Dict[str, Any]]:
        if not asset_ids:
            raise ValueError("At least one asset ID is required.")
        placeholders = ",".join("?" for _ in asset_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM assets WHERE project_id = ?
                    AND asset_id IN ({})""".format(placeholders),
                (project_id, *asset_ids),
            ).fetchall()
        found = {row["asset_id"]: dict(row) for row in rows}
        if len(found) != len(asset_ids):
            raise ValueError("One or more assets were not found in this project.")
        assets = [found[asset_id] for asset_id in asset_ids]
        allowed_statuses = (
            {"approved", "candidate"} if allow_unapproved else {"approved"}
        )
        rejected = [
            asset["asset_id"]
            for asset in assets
            if asset["status"] not in allowed_statuses
        ]
        if rejected:
            message = (
                "Unapproved assets cannot be loaded as references without explicit "
                "permission: "
                if not allow_unapproved
                else "Rejected, revision-needed or archived assets cannot be loaded "
                "as references: "
            )
            raise ValueError(
                message + ", ".join(rejected)
            )
        return assets

"""Persistence helpers for layered story context, adaptation plans, and shots."""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .database import ComicDatabase
from .storage import get_data_root


_SOURCE_TABLES = {
    "page": "pages",
    "panel": "panels",
    "event": "story_events",
    "dialogue": "dialogues",
    "character": "characters",
    "location": "locations",
}


def _json_object(value: Any, field: str) -> str:
    if not isinstance(value, dict):
        raise ValueError("{} must be a JSON object.".format(field))
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("{} contains unsupported values.".format(field)) from exc


def _json_array(value: Any, field: str) -> str:
    if not isinstance(value, list):
        raise ValueError("{} must be a JSON array.".format(field))
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("{} contains unsupported values.".format(field)) from exc


class ComicWorkflowRepository:
    """Stores workflow state in the plugin's existing SQLite database."""

    def __init__(self, database: Optional[ComicDatabase] = None) -> None:
        self.database = database or ComicDatabase(
            get_data_root() / "comic2anime.sqlite3"
        )

    def initialize(self) -> None:
        self.database.initialize()

    def save_summary(
        self,
        project_id: str,
        scope_type: str,
        scope_id: str,
        summary: Dict[str, Any],
        source_refs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if scope_type not in ("page", "chapter", "arc", "work"):
            raise ValueError("Unsupported summary scope_type.")
        if not project_id.strip() or not scope_id.strip():
            raise ValueError("project_id and scope_id are required.")
        summary_json = _json_object(summary, "summary")
        source_json = _json_array(source_refs, "source_refs")
        fingerprint = hashlib.sha256(
            (summary_json + "\n" + source_json).encode("utf-8")
        ).hexdigest()
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ValueError("Project does not exist.")
            existing = connection.execute(
                """SELECT summary_id, version, source_fingerprint
                   FROM story_summaries
                   WHERE project_id = ? AND scope_type = ? AND scope_id = ?
                     AND status = 'current'
                   ORDER BY version DESC LIMIT 1""",
                (project_id, scope_type, scope_id),
            ).fetchone()
            if existing and existing["source_fingerprint"] == fingerprint:
                return {
                    "summary_id": existing["summary_id"],
                    "version": existing["version"],
                    "reused": True,
                }
            version = (
                connection.execute(
                    """SELECT COALESCE(MAX(version), 0) + 1
                       FROM story_summaries
                       WHERE project_id = ? AND scope_type = ? AND scope_id = ?""",
                    (project_id, scope_type, scope_id),
                ).fetchone()[0]
            )
            connection.execute(
                """UPDATE story_summaries SET status = 'superseded'
                   WHERE project_id = ? AND scope_type = ? AND scope_id = ?
                     AND status = 'current'""",
                (project_id, scope_type, scope_id),
            )
            summary_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO story_summaries(
                       summary_id, project_id, scope_type, scope_id, version,
                       summary_json, source_refs_json, source_fingerprint
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    summary_id,
                    project_id,
                    scope_type,
                    scope_id,
                    version,
                    summary_json,
                    source_json,
                    fingerprint,
                ),
            )
        return {"summary_id": summary_id, "version": version, "reused": False}

    def save_state_interval(
        self,
        project_id: str,
        entity_type: str,
        entity_id: str,
        attribute: str,
        value: Any,
        source_refs: List[Dict[str, Any]],
        valid_from_event_id: Optional[str] = None,
        valid_to_event_id: Optional[str] = None,
        certainty: str = "uncertain",
        confidence: float = 0.0,
        status: str = "active",
    ) -> str:
        if not all(isinstance(item, str) and item.strip() for item in (
            entity_type, entity_id, attribute
        )):
            raise ValueError("State interval entity and attribute are required.")
        if certainty not in ("confirmed", "uncertain"):
            raise ValueError("certainty must be confirmed or uncertain.")
        if status not in ("active", "ended", "needs_review"):
            raise ValueError("Unsupported state interval status.")
        if valid_to_event_id and status != "ended":
            raise ValueError("A state interval with valid_to_event_id must be ended.")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("confidence must be between 0 and 1.")
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        refs_json = _json_array(source_refs, "source_refs")
        state_id = str(uuid.uuid4())
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ValueError("Project does not exist.")
            for event_id in (valid_from_event_id, valid_to_event_id):
                if event_id and connection.execute(
                    "SELECT 1 FROM story_events WHERE event_id = ? AND project_id = ?",
                    (event_id, project_id),
                ).fetchone() is None:
                    raise ValueError("State interval event must belong to the project.")
            for source in source_refs:
                self._validate_source_ref(connection, project_id, source)
            connection.execute(
                """INSERT INTO story_state_intervals(
                       state_id, project_id, entity_type, entity_id, attribute,
                       value_json, valid_from_event_id, valid_to_event_id,
                       source_refs_json, certainty, confidence, status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state_id, project_id, entity_type, entity_id, attribute,
                    value_json, valid_from_event_id, valid_to_event_id,
                    refs_json, certainty, confidence, status,
                ),
            )
        return state_id

    def get_state_snapshot(
        self, project_id: str, entity_type: str, entity_ids: Sequence[str]
    ) -> List[Dict[str, Any]]:
        if not entity_ids:
            return []
        if any(not isinstance(item, str) or not item for item in entity_ids):
            raise ValueError("entity_ids must contain non-empty strings.")
        placeholders = ",".join("?" for _ in entity_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT state_id, entity_type, entity_id, attribute, value_json,
                          valid_from_event_id, valid_to_event_id, source_refs_json,
                          certainty, confidence, status
                   FROM story_state_intervals
                   WHERE project_id = ? AND entity_type = ?
                     AND entity_id IN ({}) AND status = 'active'
                   ORDER BY created_at, state_id""".format(placeholders),
                (project_id, entity_type, *entity_ids),
            ).fetchall()
            return [
                {
                    "state_id": row["state_id"],
                    "entity_type": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "attribute": row["attribute"],
                    "value": json.loads(row["value_json"]),
                    "valid_from_event_id": row["valid_from_event_id"],
                    "valid_to_event_id": row["valid_to_event_id"],
                    "source_refs": json.loads(row["source_refs_json"]),
                    "certainty": row["certainty"],
                    "confidence": row["confidence"],
                    "status": row["status"],
                }
                for row in rows
            ]

    def end_state_interval(
        self, project_id: str, state_id: str, event_id: str
    ) -> None:
        with self.database.connect() as connection:
            event = connection.execute(
                "SELECT 1 FROM story_events WHERE event_id = ? AND project_id = ?",
                (event_id, project_id),
            ).fetchone()
            if event is None:
                raise ValueError("State-ending event must belong to the project.")
            cursor = connection.execute(
                """UPDATE story_state_intervals
                   SET valid_to_event_id = ?, status = 'ended'
                   WHERE state_id = ? AND project_id = ? AND status = 'active'""",
                (event_id, state_id, project_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Active state interval does not exist.")

    def save_asset_request(
        self, project_id: str, request: Dict[str, Any]
    ) -> str:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("ASSET_REQUEST must have a stable request_id.")
        if request.get("project_id") != project_id:
            raise ValueError("ASSET_REQUEST project_id does not match.")
        if not isinstance(request.get("asset_type"), str) or not request["asset_type"]:
            raise ValueError("ASSET_REQUEST must specify asset_type.")
        task_json = _json_object(request, "asset_request")
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ValueError("Project does not exist.")
            existing = connection.execute(
                "SELECT project_id, task_json FROM asset_workflow_requests "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if existing:
                if existing["project_id"] != project_id or existing["task_json"] != task_json:
                    raise ValueError("request_id was already used for another task.")
                return request_id
            connection.execute(
                """INSERT INTO asset_workflow_requests(request_id, project_id, task_json)
                   VALUES (?, ?, ?)""",
                (request_id, project_id, task_json),
            )
        return request_id

    def save_asset_iteration(
        self,
        request_id: str,
        attempt_index: int,
        prompt: Dict[str, Any],
        reference_manifest: List[Dict[str, Any]],
        qc_report: Optional[Dict[str, Any]] = None,
        generation_run_id: Optional[str] = None,
        status: str = "prompt_ready",
    ) -> str:
        allowed = {
            "prompt_ready", "generated", "pass_candidate", "retry_pending",
            "needs_human_review", "error",
        }
        if attempt_index < 0 or status not in allowed:
            raise ValueError("Invalid attempt_index or asset workflow status.")
        prompt_json = _json_object(prompt, "prompt")
        references_json = _json_array(reference_manifest, "reference_manifest")
        qc_json = _json_object(qc_report, "qc_report") if qc_report is not None else None
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM asset_workflow_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone() is None:
                raise ValueError("Asset workflow request does not exist.")
            if generation_run_id and connection.execute(
                "SELECT 1 FROM asset_generation_runs WHERE run_id = ?",
                (generation_run_id,),
            ).fetchone() is None:
                raise ValueError("Generation run does not exist.")
            existing = connection.execute(
                """SELECT iteration_id FROM asset_workflow_iterations
                   WHERE request_id = ? AND attempt_index = ?""",
                (request_id, attempt_index),
            ).fetchone()
            iteration_id = existing["iteration_id"] if existing else str(uuid.uuid4())
            connection.execute(
                """INSERT INTO asset_workflow_iterations(
                       iteration_id, request_id, attempt_index, generation_run_id,
                       prompt_json, reference_manifest_json, qc_report_json, status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(request_id, attempt_index) DO UPDATE SET
                       generation_run_id = excluded.generation_run_id,
                       prompt_json = excluded.prompt_json,
                       reference_manifest_json = excluded.reference_manifest_json,
                       qc_report_json = excluded.qc_report_json,
                       status = excluded.status,
                       updated_at = CURRENT_TIMESTAMP""",
                (
                    iteration_id,
                    request_id,
                    attempt_index,
                    generation_run_id,
                    prompt_json,
                    references_json,
                    qc_json,
                    status,
                ),
            )
            request_status = {
                "prompt_ready": "prompt_ready",
                "generated": "qc_pending",
                "pass_candidate": "pass_candidate",
                "retry_pending": "retry_pending",
                "needs_human_review": "needs_human_review",
                "error": "error",
            }[status]
            connection.execute(
                """UPDATE asset_workflow_requests
                   SET status = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE request_id = ?""",
                (request_status, request_id),
            )
        return iteration_id

    def get_asset_workflow_request(
        self, request_id: str, project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return one asset workflow request with all persisted iterations."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id is required.")
        with self.database.connect() as connection:
            clauses = ["request_id = ?"]
            parameters: List[Any] = [request_id]
            if project_id is not None:
                clauses.append("project_id = ?")
                parameters.append(project_id)
            row = connection.execute(
                "SELECT * FROM asset_workflow_requests WHERE " + " AND ".join(clauses),
                parameters,
            ).fetchone()
            if row is None:
                raise ValueError("Asset workflow request was not found.")
            result = dict(row)
            result["task"] = json.loads(result.pop("task_json"))
            iterations = connection.execute(
                """SELECT * FROM asset_workflow_iterations
                   WHERE request_id = ? ORDER BY attempt_index""",
                (request_id,),
            ).fetchall()
            result["iterations"] = []
            for iteration in iterations:
                item = dict(iteration)
                item["prompt"] = json.loads(item.pop("prompt_json"))
                item["reference_manifest"] = json.loads(
                    item.pop("reference_manifest_json")
                )
                qc_json = item.pop("qc_report_json")
                item["qc_report"] = json.loads(qc_json) if qc_json else None
                result["iterations"].append(item)
            return result

    def list_asset_workflow_requests(
        self,
        project_id: str,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List workflow requests without loading large image payloads."""
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id is required.")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        allowed = {
            "requested", "references_ready", "prompt_ready", "generating",
            "qc_pending", "pass_candidate", "retry_pending",
            "needs_human_review", "error",
        }
        if statuses is not None and any(status not in allowed for status in statuses):
            raise ValueError("Unsupported asset workflow status.")
        with self.database.connect() as connection:
            clauses = ["project_id = ?"]
            parameters: List[Any] = [project_id]
            if statuses is not None:
                if not statuses:
                    return []
                clauses.append(
                    "status IN ({})".format(",".join("?" for _ in statuses))
                )
                parameters.extend(statuses)
            rows = connection.execute(
                """SELECT request_id, project_id, status, created_at, updated_at
                   FROM asset_workflow_requests WHERE {} ORDER BY updated_at DESC, request_id
                   LIMIT ?""".format(" AND ".join(clauses)),
                (*parameters, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_adaptation_plan(
        self,
        project_id: str,
        scope: Dict[str, Any],
        plan: Dict[str, Any],
        beats: Sequence[Dict[str, Any]],
        shots: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        scope_json = _json_object(scope, "scope")
        plan_json = _json_object(plan, "plan")
        if not isinstance(beats, (list, tuple)) or not isinstance(shots, (list, tuple)):
            raise ValueError("beats and shots must be arrays.")
        for field, entries in (("beats", beats), ("shots", shots)):
            ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
            if len(ids) != len(entries) or any(
                not isinstance(item, str) or not item.strip() for item in ids
            ):
                raise ValueError("{} entries require non-empty IDs.".format(field))
            if len(ids) != len(set(ids)):
                raise ValueError("{} IDs must be unique.".format(field))
        beat_ids = {beat["id"] for beat in beats}
        for shot in shots:
            if not isinstance(shot.get("beat_ids", []), list) or not set(
                shot.get("beat_ids", [])
            ).issubset(beat_ids):
                raise ValueError("Each shot must reference beats in this plan.")
        if not shots:
            raise ValueError("At least one shot is required.")
        plan_id = str(uuid.uuid4())
        source_refs = []
        for item in list(beats) + list(shots):
            refs = item.get("source_refs", [])
            if not isinstance(refs, list):
                raise ValueError("source_refs must be an array.")
            source_refs.extend(refs)
        refs_json = _json_array(source_refs, "source_refs")
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ValueError("Project does not exist.")
            for source in source_refs:
                self._validate_source_ref(connection, project_id, source)
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM adaptation_plans "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            fingerprint = hashlib.sha256(
                (scope_json + plan_json + refs_json).encode("utf-8")
            ).hexdigest()
            connection.execute(
                """INSERT INTO adaptation_plans(
                       plan_id, project_id, version, scope_json, plan_json,
                       source_refs_json, source_fingerprint
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (plan_id, project_id, version, scope_json, plan_json, refs_json, fingerprint),
            )
            for sequence, beat in enumerate(beats):
                beat_json = _json_object(beat, "beat")
                beat_refs = _json_array(beat.get("source_refs", []), "beat.source_refs")
                connection.execute(
                    """INSERT INTO story_beats(
                           beat_id, plan_id, sequence_index, scene_id, beat_json,
                           source_refs_json
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        beat["id"],
                        plan_id,
                        int(beat.get("sequence_index", sequence)),
                        beat.get("scene_id"),
                        beat_json,
                        beat_refs,
                    ),
                )
            shot_ids = {shot["id"] for shot in shots}
            for sequence, shot in enumerate(shots):
                shot_json = _json_object(shot, "shot")
                shot_refs = _json_array(shot.get("source_refs", []), "shot.source_refs")
                beat_ids = shot.get("beat_ids", [])
                _json_array(beat_ids, "shot.beat_ids")
                start_snapshot = _json_object(
                    shot.get("start_state", {}), "shot.start_state"
                )
                end_snapshot = _json_object(
                    shot.get("end_state", {}), "shot.end_state"
                )
                status = shot.get("status", "draft")
                if status not in (
                    "draft", "needs_review", "approved", "generating",
                    "qc_failed", "complete", "stale",
                ):
                    raise ValueError("Unsupported shot status.")
                connection.execute(
                    """INSERT INTO shots(
                           shot_id, plan_id, sequence_index, scene_id, shot_json,
                           beat_ids_json, source_refs_json, start_snapshot_json,
                           end_snapshot_json, status
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        shot["id"],
                        plan_id,
                        int(shot.get("sequence_index", sequence)),
                        shot.get("scene_id"),
                        shot_json,
                        json.dumps(beat_ids, ensure_ascii=False),
                        shot_refs,
                        start_snapshot,
                        end_snapshot,
                        status,
                    ),
                )
            for shot in shots:
                dependencies = shot.get("dependencies", [])
                if not isinstance(dependencies, list):
                    raise ValueError("shot.dependencies must be an array.")
                for dependency in dependencies:
                    if not isinstance(dependency, dict):
                        raise ValueError("Each dependency must be an object.")
                    target_id = dependency.get("shot_id")
                    dependency_type = dependency.get("type")
                    if target_id not in shot_ids:
                        raise ValueError("Shot dependency must point within this plan.")
                    if dependency_type not in (
                        "tail_frame", "state", "plan", "reference"
                    ):
                        raise ValueError("Unsupported shot dependency type.")
                    connection.execute(
                        """INSERT INTO shot_dependencies(
                               shot_id, depends_on_shot_id, dependency_type, reason,
                               source_version
                           ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            shot["id"],
                            target_id,
                            dependency_type,
                            str(dependency.get("reason", "")),
                            str(dependency.get("source_version", "")),
                        ),
                    )
        return {
            "plan_id": plan_id,
            "version": version,
            "beat_count": len(beats),
            "shot_count": len(shots),
        }

    @staticmethod
    def _validate_source_ref(connection, project_id: str, source: Any) -> None:
        if not isinstance(source, dict):
            raise ValueError("Each source_ref must be an object.")
        kind, source_id = source.get("type"), source.get("id")
        if kind not in _SOURCE_TABLES or not isinstance(source_id, str) or not source_id:
            raise ValueError("source_ref requires a supported type and ID.")
        table = _SOURCE_TABLES[kind]
        if kind == "page":
            query = "SELECT 1 FROM pages WHERE page_id = ? AND project_id = ?"
            params = (source_id, project_id)
        elif kind == "event":
            query = "SELECT 1 FROM story_events WHERE event_id = ? AND project_id = ?"
            params = (source_id, project_id)
        elif kind == "panel":
            query = (
                "SELECT 1 FROM panels pn JOIN pages p ON p.page_id = pn.page_id "
                "WHERE pn.panel_id = ? AND p.project_id = ?"
            )
            params = (source_id, project_id)
        elif kind == "dialogue":
            query = (
                "SELECT 1 FROM dialogues d JOIN pages p ON p.page_id = d.page_id "
                "WHERE d.dialogue_id = ? AND p.project_id = ?"
            )
            params = (source_id, project_id)
        elif kind == "character":
            query = "SELECT 1 FROM characters WHERE character_id = ? AND project_id = ?"
            params = (source_id, project_id)
        else:
            query = "SELECT 1 FROM locations WHERE location_id = ? AND project_id = ?"
            params = (source_id, project_id)
        if connection.execute(query, params).fetchone() is None:
            raise ValueError(
                "Source reference {}:{} does not belong to the project.".format(
                    table, source_id
                )
            )

    def build_shot_package(self, project_id: str, shot_id: str) -> Dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT s.*, p.plan_id, p.version AS plan_version, p.plan_json,
                          p.scope_json, p.status AS plan_status
                   FROM shots s JOIN adaptation_plans p ON p.plan_id = s.plan_id
                   WHERE s.shot_id = ? AND p.project_id = ?""",
                (shot_id, project_id),
            ).fetchone()
            if row is None:
                raise ValueError("Shot does not exist in this project.")
            shot = dict(row)
            dependencies = connection.execute(
                """SELECT shot_id, depends_on_shot_id, dependency_type, reason,
                          source_version, status
                   FROM shot_dependencies WHERE shot_id = ?
                   ORDER BY depends_on_shot_id""",
                (shot_id,),
            ).fetchall()
            beats = connection.execute(
                """SELECT b.beat_id, b.sequence_index, b.beat_json, b.source_refs_json
                   FROM story_beats b JOIN json_each(?) ids
                   WHERE b.plan_id = ? AND b.beat_id = ids.value
                   ORDER BY b.sequence_index""",
                (shot["beat_ids_json"], shot["plan_id"]),
            ).fetchall()
            if shot["status"] == "stale" or any(
                item["status"] != "current" for item in dependencies
            ):
                warnings = ["Shot plan or an explicit dependency needs review."]
            else:
                warnings = []
            if shot["plan_status"] != "approved" or shot["status"] != "approved":
                warnings.append("Adaptation plan and shot require human approval.")
            package = {
                "project_id": project_id,
                "plan_id": shot["plan_id"],
                "plan_version": shot["plan_version"],
                "plan_status": shot["plan_status"],
                "shot_id": shot_id,
                "sequence_index": shot["sequence_index"],
                "shot": json.loads(shot["shot_json"]),
                "scope": json.loads(shot["scope_json"]),
                "story_context": json.loads(shot["plan_json"]),
                "story_beats": [
                    {
                        "beat_id": item["beat_id"],
                        "sequence_index": item["sequence_index"],
                        "beat": json.loads(item["beat_json"]),
                        "source_refs": json.loads(item["source_refs_json"]),
                    }
                    for item in beats
                ],
                "start_state": json.loads(shot["start_snapshot_json"]),
                "end_state": json.loads(shot["end_snapshot_json"]),
                "source_refs": json.loads(shot["source_refs_json"]),
                "dependencies": [dict(item) for item in dependencies],
                "validation_warnings": warnings,
            }
            return package

    def review_plan_and_shot(
        self,
        project_id: str,
        plan_id: str,
        shot_id: str,
        approve_plan: bool,
        approve_shot: bool,
    ) -> Dict[str, str]:
        if not approve_plan and not approve_shot:
            raise ValueError("Approve the plan, the shot, or both.")
        with self.database.connect() as connection:
            plan = connection.execute(
                """SELECT plan_id FROM adaptation_plans
                   WHERE plan_id = ? AND project_id = ?""",
                (plan_id, project_id),
            ).fetchone()
            if plan is None:
                raise ValueError("Adaptation plan does not exist in this project.")
            shot = connection.execute(
                "SELECT plan_id FROM shots WHERE shot_id = ?", (shot_id,)
            ).fetchone()
            if shot is None or shot["plan_id"] != plan_id:
                raise ValueError("Shot does not belong to the adaptation plan.")
            if approve_plan:
                connection.execute(
                    """UPDATE adaptation_plans SET status = 'approved',
                       updated_at = CURRENT_TIMESTAMP WHERE plan_id = ?""",
                    (plan_id,),
                )
            if approve_shot:
                connection.execute(
                    """UPDATE shots SET status = 'approved',
                       updated_at = CURRENT_TIMESTAMP WHERE shot_id = ?""",
                    (shot_id,),
                )
        return {
            "plan_status": "approved" if approve_plan else "unchanged",
            "shot_status": "approved" if approve_shot else "unchanged",
        }

    def record_video_run(
        self,
        project_id: str,
        shot_id: str,
        input_package: Dict[str, Any],
        model_name: str,
        parameters: Dict[str, Any],
        output_path: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        if bool(output_path) == bool(error_message):
            raise ValueError("Specify exactly one of output_path or error_message.")
        package_json = _json_object(input_package, "input_package")
        parameters_json = _json_object(parameters, "parameters")
        output_hash = None
        if output_path:
            file_path = Path(output_path).expanduser().resolve()
            if not file_path.is_file():
                raise FileNotFoundError("Video output does not exist.")
            digest = hashlib.sha256()
            with file_path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            output_path = str(file_path)
            output_hash = digest.hexdigest()
        run_id = str(uuid.uuid4())
        status = "succeeded" if output_path else "failed"
        with self.database.connect() as connection:
            shot = connection.execute(
                """SELECT p.version, p.status AS plan_status,
                          s.status AS shot_status FROM shots s JOIN adaptation_plans p
                   ON p.plan_id = s.plan_id
                   WHERE s.shot_id = ? AND p.project_id = ?""",
                (shot_id, project_id),
            ).fetchone()
            if shot is None:
                raise ValueError("Shot does not exist in this project.")
            if output_path and (
                shot["plan_status"] != "approved"
                or shot["shot_status"] != "approved"
            ):
                raise ValueError("A successful video run requires an approved plan and shot.")
            connection.execute(
                """INSERT INTO video_generation_runs(
                       run_id, project_id, shot_id, plan_version, status,
                       input_package_json, model_name, parameters_json,
                       output_path, output_hash, error_message, started_at, finished_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
                       CURRENT_TIMESTAMP)""",
                (
                    run_id, project_id, shot_id, shot["version"], status,
                    package_json, model_name, parameters_json, output_path,
                    output_hash, (error_message or "")[:4000] or None,
                ),
            )
        return {
            "run_id": run_id,
            "status": status,
            "output_path": output_path,
            "output_hash": output_hash,
        }

    def record_video_qc(
        self, run_id: str, decision: str, report: Dict[str, Any]
    ) -> str:
        allowed = {"pass", "warning", "fail", "needs_human_review", "error"}
        if decision not in allowed:
            raise ValueError("Unsupported video QC decision.")
        checks = report.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("Video QC report must contain evidence-based checks.")
        if any(
            not isinstance(check, dict)
            or not isinstance(check.get("evidence"), str)
            or not check["evidence"].strip()
            for check in checks
        ):
            raise ValueError("Every video QC check must include concrete evidence.")
        report_json = _json_object(report, "report")
        qc_id = str(uuid.uuid4())
        with self.database.connect() as connection:
            run = connection.execute(
                "SELECT shot_id, status FROM video_generation_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError("Video generation run does not exist.")
            if decision == "pass" and run["status"] != "succeeded":
                raise ValueError("Failed video runs cannot pass quality checks.")
            connection.execute(
                "INSERT INTO video_qc_results(qc_id, run_id, decision, report_json) "
                "VALUES (?, ?, ?, ?)",
                (qc_id, run_id, decision, report_json),
            )
            status = "complete" if decision == "pass" else (
                "qc_failed" if decision == "fail" else "needs_review"
            )
            connection.execute(
                "UPDATE shots SET status = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE shot_id = ?",
                (status, run["shot_id"]),
            )
        return qc_id

    def invalidate_dependents(self, shot_id: str, reason: str) -> List[str]:
        if not reason.strip():
            raise ValueError("An invalidation reason is required.")
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM shots WHERE shot_id = ?", (shot_id,)
            ).fetchone() is None:
                raise ValueError("Shot does not exist.")
            rows = connection.execute(
                """WITH RECURSIVE affected(id) AS (
                       SELECT shot_id FROM shot_dependencies
                       WHERE depends_on_shot_id = ?
                       UNION
                       SELECT d.shot_id FROM shot_dependencies d
                       JOIN affected a ON d.depends_on_shot_id = a.id
                   )
                   SELECT id FROM affected""",
                (shot_id,),
            ).fetchall()
            affected = [row["id"] for row in rows]
            for dependent_id in affected:
                connection.execute(
                    "UPDATE shots SET status = 'stale', updated_at = CURRENT_TIMESTAMP "
                    "WHERE shot_id = ?",
                    (dependent_id,),
                )
                connection.execute(
                    """UPDATE shot_dependencies SET status = 'stale', reason = ?
                       WHERE shot_id = ? AND depends_on_shot_id = ?""",
                    (reason, dependent_id, shot_id),
                )
        return affected

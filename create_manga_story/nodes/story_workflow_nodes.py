"""ComfyUI nodes for whole-story context and shot-level workflow records."""

import json
import uuid
from typing import Any, Dict, List

from ..api.gemini_client import generate_structured_json
from ..core.workflow_repository import ComicWorkflowRepository
from .asset_nodes import _repository


def _decode_json(value: str, field: str, expected: type):
    try:
        decoded = json.loads(value or ("[]" if expected is list else "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("{} must contain valid JSON.".format(field)) from exc
    if not isinstance(decoded, expected):
        raise ValueError(
            "{} must be a JSON {}.".format(
                field, "array" if expected is list else "object"
            )
        )
    return decoded


def _workflow_repository() -> ComicWorkflowRepository:
    asset_repository = _repository()
    asset_repository.initialize()
    return ComicWorkflowRepository(asset_repository.database)


_ADAPTATION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "plan": {"type": "OBJECT"},
        "beats": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "sequence_index": {"type": "INTEGER"},
                    "purpose": {"type": "STRING"},
                    "scene_id": {"type": "STRING"},
                    "action": {"type": "STRING"},
                    "source_refs": {"type": "ARRAY", "items": {"type": "OBJECT"}},
                },
                "required": ["sequence_index", "purpose", "action", "source_refs"],
            },
        },
        "shots": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "sequence_index": {"type": "INTEGER"},
                    "purpose": {"type": "STRING"},
                    "action": {"type": "STRING"},
                    "duration_seconds": {"type": "NUMBER"},
                    "source_refs": {"type": "ARRAY", "items": {"type": "OBJECT"}},
                    "beat_indices": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "start_state": {"type": "OBJECT"},
                    "end_state": {"type": "OBJECT"},
                    "dependencies": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "depends_on_sequence_index": {"type": "INTEGER"},
                                "type": {
                                    "type": "STRING",
                                    "enum": ["tail_frame", "state", "plan", "reference"],
                                },
                                "reason": {"type": "STRING"},
                            },
                            "required": ["depends_on_sequence_index", "type", "reason"],
                        },
                    },
                },
                "required": [
                    "sequence_index", "purpose", "action", "duration_seconds",
                    "source_refs", "beat_indices", "start_state", "end_state",
                    "dependencies",
                ],
            },
        },
    },
    "required": ["plan", "beats", "shots"],
}


class GeminiComicAdaptationPlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "story_context": ("COMIC_STORY_CONTEXT",),
                "scope_json": ("STRING", {"multiline": True, "default": "{}"}),
                "user_constraints": ("STRING", {"multiline": True, "default": ""}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": (
                    "STRING",
                    {"default": "https://generativelanguage.googleapis.com"},
                ),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            }
        }

    RETURN_TYPES = ("COMIC_ADAPTATION_DRAFT", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("draft", "scope_json", "plan_json", "beats_json", "shots_json")
    FUNCTION = "plan"
    CATEGORY = "Comic2Anime/Planning"

    def plan(
        self,
        story_context: Dict[str, Any],
        scope_json: str,
        user_constraints: str,
        api_key: str,
        model: str,
        api_base_url: str,
        timeout_seconds: int,
        max_retries: int,
    ):
        context = _decode_json(json.dumps(story_context), "story_context", dict)
        scope = _decode_json(scope_json, "scope_json", dict)
        allowed_sources = set()
        for chapter in context.get("chapter_summaries", []):
            for source in chapter.get("source_refs", []):
                if isinstance(source, dict):
                    allowed_sources.add((source.get("type"), source.get("id")))
        for event in context.get("event_timeline", []):
            allowed_sources.add(("event", event.get("event_id")))
            allowed_sources.add(("page", event.get("page_id")))
        prompt = (
            "Create a manga adaptation plan, ordered story beats, and actionable animation "
            "shots. Treat this comic context as source evidence, not a license to invent facts. "
            "Each beat and shot must cite actual page/event IDs from the context. Preserve "
            "causality and character motivation. Keep display order distinct from story time. "
            "For each shot specify action, duration, start/end state, and only genuine explicit "
            "dependencies. Use beat_indices and depends_on_sequence_index for links. Return JSON.\\n"
            "SCOPE:\\n" + json.dumps(scope, ensure_ascii=False)
            + "\\nUSER CONSTRAINTS:\\n" + user_constraints
            + "\\nSTORY CONTEXT:\\n" + json.dumps(context, ensure_ascii=False)
        )
        result = generate_structured_json(
            prompt, model, api_key, api_base_url, timeout_seconds,
            response_schema=_ADAPTATION_SCHEMA, max_retries=max_retries,
        )
        plan = result.get("plan")
        beats = result.get("beats")
        shots = result.get("shots")
        if not isinstance(plan, dict) or not isinstance(beats, list) or not beats:
            raise ValueError("Gemini adaptation output requires a plan and story beats.")
        if not isinstance(shots, list) or not shots or len(shots) > 200:
            raise ValueError("Gemini adaptation output requires 1-200 shots.")
        beat_ids = {}
        for index, beat in enumerate(beats):
            sequence = beat.get("sequence_index")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                raise ValueError("Beat sequence_index must be a non-negative integer.")
            if sequence in beat_ids:
                raise ValueError("Beat sequence_index values must be unique.")
            self._validate_sources(beat.get("source_refs"), allowed_sources)
            if not beat.get("purpose") or not beat.get("action"):
                raise ValueError("Each beat needs purpose and action.")
            beat_ids[sequence] = "beat-{}".format(uuid.uuid4())
            beat["id"] = beat_ids[sequence]
        shot_ids = {}
        for shot in shots:
            sequence = shot.get("sequence_index")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                raise ValueError("Shot sequence_index must be a non-negative integer.")
            if sequence in shot_ids:
                raise ValueError("Shot sequence_index values must be unique.")
            self._validate_sources(shot.get("source_refs"), allowed_sources)
            duration = shot.get("duration_seconds")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not 0 < duration <= 120
            ):
                raise ValueError("Shot duration_seconds must be between 0 and 120.")
            if not shot.get("purpose") or not shot.get("action"):
                raise ValueError("Each shot needs purpose and time-ordered action.")
            shot_ids[sequence] = "shot-{}".format(uuid.uuid4())
            shot["id"] = shot_ids[sequence]
        for shot in shots:
            beat_indices = shot.get("beat_indices")
            if (
                not isinstance(beat_indices, list)
                or not beat_indices
                or any(index not in beat_ids for index in beat_indices)
            ):
                raise ValueError("Each shot must link to one or more actual story beats.")
            shot["beat_ids"] = [beat_ids[index] for index in beat_indices]
            shot.pop("beat_indices", None)
            for dependency in shot.get("dependencies", []):
                prior = dependency.get("depends_on_sequence_index")
                if prior not in shot_ids or prior == shot["sequence_index"]:
                    raise ValueError("Shot dependency refers to an unknown or self shot.")
                dependency["shot_id"] = shot_ids[prior]
                dependency.pop("depends_on_sequence_index", None)
        plan["model_name"] = model
        draft = {"scope": scope, "plan": plan, "beats": beats, "shots": shots}
        return (
            draft,
            json.dumps(scope, ensure_ascii=False),
            json.dumps(plan, ensure_ascii=False),
            json.dumps(beats, ensure_ascii=False),
            json.dumps(shots, ensure_ascii=False),
        )

    @staticmethod
    def _validate_sources(sources: Any, allowed_sources: set) -> None:
        if not isinstance(sources, list) or not sources:
            raise ValueError("Every beat and shot must cite manga source evidence.")
        for source in sources:
            if (
                not isinstance(source, dict)
                or (source.get("type"), source.get("id")) not in allowed_sources
            ):
                raise ValueError("Adaptation cited source evidence absent from story context.")


class ComicWholeStoryContextBuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "chapter_ids_json": ("STRING", {"multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("COMIC_STORY_CONTEXT", "STRING", "STRING")
    RETURN_NAMES = ("story_context", "context_json", "build_report_json")
    FUNCTION = "build"
    CATEGORY = "Comic2Anime/Story"

    def build(self, project_id: str, chapter_ids_json: str):
        chapter_ids = _decode_json(chapter_ids_json, "chapter_ids_json", list)
        if any(not isinstance(item, str) or not item.strip() for item in chapter_ids):
            raise ValueError("chapter_ids_json must contain non-empty chapter IDs.")
        if len(chapter_ids) != len(set(chapter_ids)):
            raise ValueError("chapter_ids_json cannot contain duplicate chapters.")
        workflow = _workflow_repository()
        connection_context = workflow.database.connect()
        with connection_context as connection:
            project = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise ValueError("Project does not exist.")
            if chapter_ids:
                placeholders = ",".join("?" for _ in chapter_ids)
                summaries = connection.execute(
                    """SELECT c.chapter_id, c.chapter_index, s.summary_json
                       FROM chapters c LEFT JOIN chapter_summaries s
                         ON s.project_id = c.project_id AND s.chapter_id = c.chapter_id
                       WHERE c.project_id = ? AND c.chapter_id IN ({})
                       ORDER BY c.chapter_index, c.chapter_id""".format(placeholders),
                    (project_id, *chapter_ids),
                ).fetchall()
                if len(summaries) != len(chapter_ids):
                    raise ValueError("One or more selected chapters do not exist.")
            else:
                summaries = connection.execute(
                    """SELECT c.chapter_id, c.chapter_index, s.summary_json
                       FROM chapters c LEFT JOIN chapter_summaries s
                         ON s.project_id = c.project_id AND s.chapter_id = c.chapter_id
                       WHERE c.project_id = ?
                       ORDER BY c.chapter_index, c.chapter_id""",
                    (project_id,),
                ).fetchall()
            missing = [row["chapter_id"] for row in summaries if row["summary_json"] is None]
            if chapter_ids:
                placeholders = ",".join("?" for _ in chapter_ids)
                scoped = (project_id, *chapter_ids)
                event_rows = connection.execute(
                    """SELECT event_id, chapter_id, page_id, panel_index, description,
                              confidence
                       FROM story_events WHERE project_id = ?
                         AND chapter_id IN ({})
                       ORDER BY chapter_id, page_id, panel_index, event_id""".format(
                        placeholders
                    ),
                    scoped,
                ).fetchall()
                open_threads = connection.execute(
                    """SELECT t.thread_id, t.description, t.thread_type, t.status,
                              t.source_page_id, t.source_panel_index, t.confidence
                       FROM open_threads t JOIN pages p ON p.page_id = t.source_page_id
                       WHERE t.project_id = ? AND t.status != 'resolved'
                         AND p.chapter_id IN ({})
                       ORDER BY t.source_page_id, t.source_panel_index, t.thread_id""".format(
                        placeholders
                    ),
                    scoped,
                ).fetchall()
                page_refs = connection.execute(
                    """SELECT page_id, chapter_id, page_index FROM pages
                       WHERE project_id = ? AND chapter_id IN ({})
                       ORDER BY chapter_id, page_index""".format(placeholders),
                    scoped,
                ).fetchall()
            else:
                event_rows = connection.execute(
                    """SELECT event_id, chapter_id, page_id, panel_index, description,
                              confidence
                       FROM story_events WHERE project_id = ?
                       ORDER BY chapter_id, page_id, panel_index, event_id""",
                    (project_id,),
                ).fetchall()
                open_threads = connection.execute(
                    """SELECT thread_id, description, thread_type, status, source_page_id,
                              source_panel_index, confidence
                       FROM open_threads WHERE project_id = ? AND status != 'resolved'
                       ORDER BY source_page_id, source_panel_index, thread_id""",
                    (project_id,),
                ).fetchall()
                page_refs = connection.execute(
                    """SELECT page_id, chapter_id, page_index FROM pages
                       WHERE project_id = ? ORDER BY chapter_id, page_index""",
                    (project_id,),
                ).fetchall()
        chapter_context = [
            {
                "chapter_id": row["chapter_id"],
                "chapter_index": row["chapter_index"],
                "summary": json.loads(row["summary_json"])
                if row["summary_json"] is not None
                else None,
                "source_refs": [
                    {"type": "page", "id": page["page_id"]}
                    for page in page_refs
                    if page["chapter_id"] == row["chapter_id"]
                ],
            }
            for row in summaries
        ]
        context = {
            "project_id": project_id,
            "scope": {
                "type": "chapters" if chapter_ids else "work",
                "chapter_ids": [row["chapter_id"] for row in summaries],
            },
            "chapter_summaries": chapter_context,
            "event_timeline": [dict(row) for row in event_rows],
            "unresolved_threads": [dict(row) for row in open_threads],
            "display_order_is_not_story_time": True,
            "story_time_relations": "Not inferred where the source schema has no explicit relation.",
            "missing_chapter_summaries": missing,
        }
        if not summaries:
            raise ValueError("The project contains no chapters.")
        scope_type = "work" if not chapter_ids else "arc"
        scope_id = project_id if not chapter_ids else ",".join(
            row["chapter_id"] for row in summaries
        )
        sources = [
            {"type": "page", "id": page["page_id"]} for page in page_refs
        ]
        persisted = workflow.save_summary(
            project_id, scope_type, scope_id, context, sources
        )
        report = {
            "chapter_count": len(chapter_context),
            "event_count": len(event_rows),
            "unresolved_thread_count": len(open_threads),
            "missing_chapter_summaries": missing,
            "summary_record": persisted,
            "warnings": (
                ["Some chapters have no persisted chapter summary."]
                if missing
                else []
            ),
        }
        return context, json.dumps(context, ensure_ascii=False), json.dumps(report, ensure_ascii=False)


class ComicCharacterStateSnapshotBuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "entity_type": ("STRING", {"default": "character"}),
                "entity_ids_json": ("STRING", {"multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("COMIC_STATE_SNAPSHOT", "STRING")
    RETURN_NAMES = ("state_snapshot", "snapshot_json")
    FUNCTION = "build"
    CATEGORY = "Comic2Anime/Story"

    def build(self, project_id: str, entity_type: str, entity_ids_json: str):
        entity_ids = _decode_json(entity_ids_json, "entity_ids_json", list)
        if not entity_ids:
            raise ValueError("At least one entity ID is required.")
        snapshot = _workflow_repository().get_state_snapshot(
            project_id, entity_type, entity_ids
        )
        return snapshot, json.dumps(snapshot, ensure_ascii=False)


class ComicAdaptationPlanSaver:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "scope_json": ("STRING", {"multiline": True, "default": "{}"}),
                "plan_json": ("STRING", {"multiline": True, "default": "{}"}),
                "beats_json": ("STRING", {"multiline": True, "default": "[]"}),
                "shots_json": ("STRING", {"multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("COMIC_ADAPTATION_PLAN", "STRING", "STRING")
    RETURN_NAMES = ("plan_record", "plan_id", "save_report_json")
    FUNCTION = "save"
    CATEGORY = "Comic2Anime/Planning"

    def save(
        self,
        project_id: str,
        scope_json: str,
        plan_json: str,
        beats_json: str,
        shots_json: str,
    ):
        result = _workflow_repository().create_adaptation_plan(
            project_id,
            _decode_json(scope_json, "scope_json", dict),
            _decode_json(plan_json, "plan_json", dict),
            _decode_json(beats_json, "beats_json", list),
            _decode_json(shots_json, "shots_json", list),
        )
        return result, result["plan_id"], json.dumps(result, ensure_ascii=False)


class ComicShotPackageBuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "shot_id": ("STRING", {"default": ""}),
            }
        }

    RETURN_TYPES = ("COMIC_SHOT_PACKAGE", "STRING", "STRING")
    RETURN_NAMES = ("shot_package", "package_json", "validation_report_json")
    FUNCTION = "build"
    CATEGORY = "Comic2Anime/Planning"

    def build(self, project_id: str, shot_id: str):
        package = _workflow_repository().build_shot_package(project_id, shot_id)
        if not package["source_refs"]:
            raise ValueError("Shot package has no source manga evidence.")
        report = {
            "shot_id": shot_id,
            "plan_version": package["plan_version"],
            "source_ref_count": len(package["source_refs"]),
            "dependency_count": len(package["dependencies"]),
            "warnings": package["validation_warnings"],
        }
        return package, json.dumps(package, ensure_ascii=False), json.dumps(report, ensure_ascii=False)


class ComicShotPlanReview:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "plan_id": ("STRING", {"default": ""}),
                "shot_id": ("STRING", {"default": ""}),
                "approve_plan": ("BOOLEAN", {"default": False}),
                "approve_shot": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("review_report_json", "status")
    FUNCTION = "review"
    CATEGORY = "Comic2Anime/Planning"

    def review(
        self,
        project_id: str,
        plan_id: str,
        shot_id: str,
        approve_plan: bool,
        approve_shot: bool,
    ):
        result = _workflow_repository().review_plan_and_shot(
            project_id, plan_id, shot_id, approve_plan, approve_shot
        )
        return json.dumps(result, ensure_ascii=False), "approved"


class ComicVideoRunRecorder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "shot_package": ("COMIC_SHOT_PACKAGE",),
                "model_name": ("STRING", {"default": ""}),
                "parameters_json": ("STRING", {"multiline": True, "default": "{}"}),
                "output_path": ("STRING", {"default": ""}),
                "error_message": ("STRING", {"multiline": True, "default": ""}),
            }
        }

    RETURN_TYPES = ("COMIC_VIDEO_RUN", "STRING")
    RETURN_NAMES = ("video_run", "run_report_json")
    FUNCTION = "record"
    CATEGORY = "Comic2Anime/Video"

    def record(
        self,
        shot_package: Dict[str, Any],
        model_name: str,
        parameters_json: str,
        output_path: str,
        error_message: str,
    ):
        if bool(output_path.strip()) == bool(error_message.strip()):
            raise ValueError("Provide exactly one of output_path or error_message.")
        package = _decode_json(json.dumps(shot_package), "shot_package", dict)
        result = _workflow_repository().record_video_run(
            package["project_id"],
            package["shot_id"],
            package,
            model_name,
            _decode_json(parameters_json, "parameters_json", dict),
            output_path=output_path.strip() or None,
            error_message=error_message.strip() or None,
        )
        return result, json.dumps(result, ensure_ascii=False)


class ComicVideoQCRecorder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_run": ("COMIC_VIDEO_RUN",),
                "decision": (("pass", "warning", "fail", "needs_human_review", "error"),),
                "report_json": ("STRING", {"multiline": True, "default": "{}"}),
            }
        }

    RETURN_TYPES = ("STRING", "COMIC_VIDEO_QC")
    RETURN_NAMES = ("qc_id", "qc_report")
    FUNCTION = "record"
    CATEGORY = "Comic2Anime/Video"

    def record(self, video_run: Dict[str, Any], decision: str, report_json: str):
        report = _decode_json(report_json, "report_json", dict)
        if not isinstance(report.get("checks"), list) or not report["checks"]:
            raise ValueError("Video QC report must contain evidence-based checks.")
        for check in report["checks"]:
            if (
                not isinstance(check, dict)
                or not isinstance(check.get("evidence"), str)
                or not check["evidence"].strip()
            ):
                raise ValueError("Every video QC check must include concrete evidence.")
        qc_id = _workflow_repository().record_video_qc(
            video_run["run_id"], decision, report
        )
        report["qc_id"] = qc_id
        report["decision"] = decision
        return qc_id, report


class ComicShotRetryPlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "shot_id": ("STRING", {"default": ""}),
                "reason": ("STRING", {"multiline": True, "default": ""}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("affected_shot_ids_json", "retry_report_json")
    FUNCTION = "invalidate"
    CATEGORY = "Comic2Anime/Video"

    def invalidate(self, shot_id: str, reason: str):
        affected = _workflow_repository().invalidate_dependents(shot_id, reason)
        report = {
            "source_shot_id": shot_id,
            "reason": reason,
            "affected_shot_ids": affected,
            "automatic_regeneration": False,
        }
        return json.dumps(affected, ensure_ascii=False), json.dumps(report, ensure_ascii=False)

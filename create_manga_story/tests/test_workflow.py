import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from create_manga_story.core.asset_repository import AssetRepository
from create_manga_story.core.database import ComicDatabase
from create_manga_story.core.workflow_repository import ComicWorkflowRepository
from create_manga_story.nodes.asset_workflow_nodes import (
    ComicAssetReferenceSelector,
    ComicAssetBatchGenerationLoop,
    ComicAssetWorkflowStatus,
    ComicAssetWorkflowRegistrar,
    GeminiQwenAssetGenerationLoop,
    GeminiAssetPromptGenerator,
    GeminiAssetVisualQC,
    _qwen2_extraction_directive,
    _reusable_generation,
    _stack_batch_images,
)
from create_manga_story.nodes.story_workflow_nodes import (
    ComicAdaptationPlanSaver,
    ComicShotPackageBuilder,
    ComicWholeStoryContextBuilder,
    GeminiComicAdaptationPlanner,
)
from create_manga_story.nodes.qwen_edit_2511 import QwenImageEditCustomNode


def _image(color=(30, 80, 120)):
    stream = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(stream, format="PNG")
    return stream.getvalue()


class ComicWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.database = ComicDatabase(self.root / "comic.sqlite3")
        self.database.initialize()
        self.database.save_pages(
            [
                {
                    "page_id": "page-1",
                    "project_id": "project-1",
                    "chapter_id": "chapter-1",
                    "page_index": 1,
                    "source_path": "page-1.png",
                    "image_path": "page-1.png",
                    "sha256": "source",
                    "width": 3,
                    "height": 2,
                },
                {
                    "page_id": "page-2",
                    "project_id": "project-1",
                    "chapter_id": "chapter-2",
                    "page_index": 1,
                    "source_path": "page-2.png",
                    "image_path": "page-2.png",
                    "sha256": "source-2",
                    "width": 3,
                    "height": 2,
                },
            ]
        )
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO characters(
                    character_id, project_id, canonical_name, stable_description
                ) VALUES ('character-1', 'project-1', 'Ari', 'blue hair')"""
            )
            for chapter_id, summary in (
                ("chapter-1", {"events": ["Ari finds a key"]}),
                ("chapter-2", {"events": ["The key opens a door"]}),
            ):
                connection.execute(
                    """INSERT INTO chapter_summaries(
                        project_id, chapter_id, summary_json, source_fingerprint
                    ) VALUES (?, ?, ?, ?)""",
                    ("project-1", chapter_id, json.dumps(summary), chapter_id),
                )
        self.workflow = ComicWorkflowRepository(self.database)
        self.assets = AssetRepository(self.database, self.root / "assets")
        self.request = {
            "schema_version": "1.0",
            "request_id": "request-1",
            "project_id": "project-1",
            "asset_type": "character_portrait",
            "entity_type": "character",
            "entity_id": "character-1",
            "generation_goal": "portrait",
            "source_evidence": [{"page_id": "page-1"}],
            "reference_asset_ids": [],
            "warnings": [],
        }

    def test_schema_migration_is_idempotent_and_additive(self):
        self.database.initialize()
        with self.database.connect() as connection:
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            self.assertIn(3, versions)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue(
            {
                "story_summaries", "story_state_intervals", "adaptation_plans",
                "story_beats", "shots", "shot_dependencies",
                "video_generation_runs", "video_qc_results",
                "asset_workflow_requests", "asset_workflow_iterations",
            }.issubset(tables)
        )
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 2
            )

    def test_story_summary_versions_and_state_snapshot_preserve_source(self):
        first = self.workflow.save_summary(
            "project-1", "work", "project-1", {"theme": "trust"},
            [{"type": "page", "id": "page-1"}],
        )
        replay = self.workflow.save_summary(
            "project-1", "work", "project-1", {"theme": "trust"},
            [{"type": "page", "id": "page-1"}],
        )
        newer = self.workflow.save_summary(
            "project-1", "work", "project-1", {"theme": "trust broken"},
            [{"type": "page", "id": "page-2"}],
        )
        self.assertFalse(first["reused"])
        self.assertTrue(replay["reused"])
        self.assertEqual(newer["version"], 2)
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO story_events(
                    event_id, project_id, chapter_id, page_id, panel_index,
                    description, participants_json, confidence
                ) VALUES ('event-1', 'project-1', 'chapter-1', 'page-1', 1,
                    'Ari is injured', '[]', 1)"""
            )
        state_id = self.workflow.save_state_interval(
            "project-1",
            "character",
            "character-1",
            "injury",
            {"side": "right_face"},
            [{"type": "page", "id": "page-1"}],
            valid_from_event_id="event-1",
            certainty="confirmed",
            confidence=1,
        )
        snapshot = self.workflow.get_state_snapshot(
            "project-1", "character", ["character-1"]
        )
        self.assertEqual(snapshot[0]["state_id"], state_id)
        self.assertEqual(snapshot[0]["value"], {"side": "right_face"})
        self.assertEqual(snapshot[0]["source_refs"], [{"id": "page-1", "type": "page"}])

    def test_story_context_planning_and_plan_saving_keep_evidence_ids(self):
        with patch(
            "create_manga_story.nodes.story_workflow_nodes._repository",
            return_value=self.assets,
        ):
            context, _, report_json = ComicWholeStoryContextBuilder().build(
                "project-1", "[]"
            )
        self.assertEqual(len(context["chapter_summaries"]), 2)
        self.assertFalse(context["display_order_is_not_story_time"] is False)
        self.assertEqual(json.loads(report_json)["chapter_count"], 2)
        generated = {
            "plan": {"must_preserve": ["the key is found"]},
            "beats": [{
                "sequence_index": 0,
                "purpose": "introduce the key",
                "scene_id": "",
                "action": "Ari finds the key.",
                "source_refs": [{"type": "page", "id": "page-1"}],
            }],
            "shots": [{
                "sequence_index": 0,
                "purpose": "show the discovery",
                "action": "Ari picks up the key.",
                "duration_seconds": 4,
                "source_refs": [{"type": "page", "id": "page-1"}],
                "beat_indices": [0],
                "start_state": {},
                "end_state": {"prop": "key acquired"},
                "dependencies": [],
            }],
        }
        with patch(
            "create_manga_story.nodes.story_workflow_nodes.generate_structured_json",
            return_value=generated,
        ):
            draft, scope_json, plan_json, beats_json, shots_json = (
                GeminiComicAdaptationPlanner().plan(
                    context, '{"chapter_ids":["chapter-1"]}', "keep the plot",
                    "api-key", "gemini-test", "https://example.invalid", 10, 0,
                )
            )
        self.assertEqual(draft["shots"][0]["source_refs"][0]["id"], "page-1")
        self.assertEqual(
            draft["shots"][0]["beat_ids"], [draft["beats"][0]["id"]]
        )
        with patch(
            "create_manga_story.nodes.story_workflow_nodes._repository",
            return_value=self.assets,
        ):
            _, plan_id, _ = ComicAdaptationPlanSaver().save(
                "project-1", scope_json, plan_json, beats_json, shots_json
            )
            self.workflow.review_plan_and_shot(
                "project-1", plan_id, draft["shots"][0]["id"], True, True
            )
            package, _, _ = ComicShotPackageBuilder().build(
                "project-1", draft["shots"][0]["id"]
            )
        self.assertEqual(package["plan_id"], plan_id)
        self.assertEqual(package["source_refs"][0]["id"], "page-1")
        self.assertEqual(package["story_beats"][0]["beat_id"], draft["beats"][0]["id"])

    def test_adaptation_plan_package_and_precise_dependency_invalidation(self):
        plan = self.workflow.create_adaptation_plan(
            "project-1",
            {"chapter_ids": ["chapter-1", "chapter-2"]},
            {"must_preserve": ["the key opens the door"]},
            [
                {
                    "id": "beat-1",
                    "purpose": "introduce the key",
                    "source_refs": [{"type": "page", "id": "page-1"}],
                },
                {
                    "id": "beat-2",
                    "purpose": "open the door",
                    "source_refs": [{"type": "page", "id": "page-2"}],
                },
            ],
            [
                {
                    "id": "shot-1",
                    "beat_ids": ["beat-1"],
                    "source_refs": [{"type": "page", "id": "page-1"}],
                    "start_state": {},
                    "end_state": {"prop": "key acquired"},
                },
                {
                    "id": "shot-2",
                    "beat_ids": ["beat-2"],
                    "source_refs": [{"type": "page", "id": "page-2"}],
                    "dependencies": [
                        {"shot_id": "shot-1", "type": "tail_frame", "reason": "continuous action"}
                    ],
                },
                {
                    "id": "shot-independent",
                    "beat_ids": ["beat-1"],
                    "source_refs": [{"type": "page", "id": "page-1"}],
                },
            ],
        )
        self.workflow.review_plan_and_shot(
            "project-1", plan["plan_id"], "shot-independent", True, True
        )
        package = self.workflow.build_shot_package("project-1", "shot-2")
        self.assertEqual(package["plan_version"], 1)
        self.assertEqual(package["story_beats"][0]["beat_id"], "beat-2")
        self.assertEqual(package["dependencies"][0]["depends_on_shot_id"], "shot-1")
        affected = self.workflow.invalidate_dependents("shot-1", "new tail frame")
        self.assertEqual(affected, ["shot-2"])
        self.assertEqual(
            self.workflow.build_shot_package("project-1", "shot-independent")[
                "validation_warnings"
            ],
            [],
        )

    def test_video_run_and_qc_are_tied_to_exact_shot(self):
        plan = self.workflow.create_adaptation_plan(
            "project-1",
            {"chapter_ids": ["chapter-1"]},
            {"tone": "quiet"},
            [],
            [{
                "id": "shot-1",
                "source_refs": [{"type": "page", "id": "page-1"}],
            }],
        )
        self.workflow.review_plan_and_shot(
            "project-1", plan["plan_id"], "shot-1", True, True
        )
        package = self.workflow.build_shot_package("project-1", "shot-1")
        video_path = self.root / "shot.mp4"
        video_path.write_bytes(b"test-video")
        run = self.workflow.record_video_run(
            "project-1", "shot-1", package, "external-video-node",
            {"duration_seconds": 5}, output_path=str(video_path),
        )
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["output_hash"], hashlib.sha256(b"test-video").hexdigest())
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.workflow.record_video_qc(run["run_id"], "pass", {"summary": "looks good"})
        qc_id = self.workflow.record_video_qc(
            run["run_id"],
            "pass",
            {"checks": [{"category": "identity", "evidence": "matching hair and face"}]},
        )
        self.assertTrue(qc_id)
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM shots WHERE shot_id = 'shot-1'"
                ).fetchone()[0],
                "complete",
            )

    def test_asset_reference_selector_loads_verified_images_on_real_ports(self):
        encoded = _image()
        created = self.assets.register_batch(
            "project-1",
            "character_portrait",
            [{"content": encoded, "width": 3, "height": 2, "mime_type": "image/png"}],
            entity_type="character",
            entity_id="character-1",
            generation_run_id="source-reference",
        )
        asset_id = created["assets"][0]["asset_id"]
        self.assets.review_asset(
            "project-1", asset_id, "approved", "verified"
        )
        torch_shim = SimpleNamespace(from_numpy=lambda array: array)
        with patch.dict(sys.modules, {"torch": torch_shim}), patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ):
            image1, image2, image3, manifest, manifest_json = (
                ComicAssetReferenceSelector().select(
                    "project-1",
                    self.request,
                    json.dumps([{
                        "asset_id": asset_id,
                        "role": "character_identity",
                        "reason": "approved face reference",
                    }]),
                )
            )
        self.assertEqual(image1.shape, (1, 2, 3, 3))
        self.assertIsNone(image2)
        self.assertIsNone(image3)
        self.assertEqual(manifest[0]["port"], "image1")
        self.assertEqual(manifest[0]["version_hash"], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(json.loads(manifest_json)[0]["asset_id"], asset_id)

    def test_gemini_prompt_qc_and_registration_auto_approve_pass(self):
        prompt = {
            "prompt_version": 1,
            "detailed_description": "Keep the established face; change only the coat.",
            "reference_usage": [],
            "must_preserve": ["face"],
            "allowed_changes": ["coat"],
            "negative_constraints": ["do not change age"],
            "uncertainties": [],
            "validation_checklist": ["face unchanged", "coat matches task"],
        }
        with patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ), patch(
            "create_manga_story.nodes.asset_workflow_nodes.generate_structured_json",
            return_value=prompt,
        ):
            prompt_object, prompt_text, prompt_report = GeminiAssetPromptGenerator().generate(
                self.request, "[]", "api-key", "gemini-test",
                "https://example.invalid", 10, 0,
            )
        self.assertEqual(prompt_text, prompt["detailed_description"])
        self.assertEqual(json.loads(prompt_report)["request_id"], "request-1")
        report = {
            "decision": "PASS",
            "overall_score": 0.95,
            "checks": [{
                "category": "character_identity",
                "result": "PASS",
                "severity": "none",
                "evidence": "The face and hair match the approved reference.",
            }],
            "failures": [],
            "uncertainties": [],
            "prompt_revision_instructions": [],
            "reference_revision_instructions": [],
            "human_review_required": False,
        }
        captured = {}

        def fake_generate(prompt_text_arg, model, key, url, timeout, **kwargs):
            captured["images"] = kwargs["images"]
            return report

        image_tensor = np.zeros((1, 2, 3, 3), dtype=np.float32)
        with patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ), patch(
            "create_manga_story.nodes.asset_workflow_nodes.generate_structured_json",
            side_effect=fake_generate,
        ):
            qc, decision, qc_json = GeminiAssetVisualQC().evaluate(
                image_tensor,
                self.request,
                prompt_object,
                "[]",
                0,
                0.8,
                "api-key",
                "gemini-test",
                "https://example.invalid",
                10,
                0,
            )
        self.assertEqual(decision, "PASS")
        self.assertEqual(len(captured["images"]), 1)
        self.assertTrue(captured["images"][0][0].startswith(b"\x89PNG"))
        with patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ):
            assets, registration = ComicAssetWorkflowRegistrar().register(
                image_tensor,
                self.request,
                prompt_object,
                "[]",
                qc_json,
                0,
                "candidate portrait",
                -1,
                "{}",
            )
        self.assertEqual(assets[0]["status"], "approved")
        self.assertEqual(registration["qc_decision"], "PASS")
        with self.database.connect() as connection:
            iteration = connection.execute(
                """SELECT status, qc_report_json, generation_run_id
                   FROM asset_workflow_iterations WHERE request_id = 'request-1'
                     AND attempt_index = 0"""
            ).fetchone()
        self.assertEqual(iteration["status"], "pass_candidate")
        self.assertTrue(iteration["generation_run_id"])
        self.assertEqual(json.loads(iteration["qc_report_json"])["decision"], "PASS")

    def test_generation_loop_sends_real_images_revises_and_stops_after_pass(self):
        reference = self.assets.register_batch(
            "project-1",
            "character_portrait",
            [{"content": _image(), "width": 3, "height": 2, "mime_type": "image/png"}],
            entity_type="character",
            entity_id="character-1",
            generation_run_id="loop-reference",
        )["assets"][0]
        self.assets.review_asset(
            "project-1", reference["asset_id"], "approved", "verified"
        )
        prompt = {
            "prompt_version": 1,
            "detailed_description": "Keep face identity and change only the coat.",
            "reference_usage": [{
                "reference_asset_id": reference["asset_id"],
                "role": "character_identity",
                "instruction": "Use for face identity only.",
            }],
            "must_preserve": ["face"],
            "allowed_changes": ["coat"],
            "negative_constraints": ["do not change age"],
            "uncertainties": [],
            "validation_checklist": ["face", "coat"],
        }
        retry_prompt = dict(prompt)
        retry_prompt["detailed_description"] = (
            "Keep the same face and render the requested red coat."
        )
        retry_qc = {
            "decision": "RETRY",
            "overall_score": 0.5,
            "checks": [{
                "category": "outfit",
                "result": "FAIL",
                "severity": "medium",
                "evidence": "The output coat is blue, not red.",
            }],
            "failures": ["The coat color is incorrect."],
            "uncertainties": [],
            "prompt_revision_instructions": ["Specify the red coat color."],
            "reference_revision_instructions": [],
            "human_review_required": False,
        }
        pass_qc = {
            "decision": "PASS",
            "overall_score": 0.95,
            "checks": [{
                "category": "outfit",
                "result": "PASS",
                "severity": "none",
                "evidence": "The generated coat is visibly red.",
            }],
            "failures": [],
            "uncertainties": [],
            "prompt_revision_instructions": [],
            "reference_revision_instructions": [],
            "human_review_required": False,
        }
        api_calls = []

        def fake_gemini(prompt_text, model, key, url, timeout, **kwargs):
            api_calls.append((prompt_text, kwargs.get("images", [])))
            if len(api_calls) == 1:
                return prompt
            if len(api_calls) == 2:
                return retry_qc
            if len(api_calls) == 3:
                return retry_prompt
            return pass_qc

        first_output = np.zeros((1, 2, 3, 3), dtype=np.float32)
        second_output = np.ones((1, 2, 3, 3), dtype=np.float32) * 0.5
        torch_shim = SimpleNamespace(from_numpy=lambda array: array)
        with patch.dict(sys.modules, {"torch": torch_shim}), patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ), patch(
            "create_manga_story.nodes.asset_workflow_nodes.generate_structured_json",
            side_effect=fake_gemini,
        ), patch.object(
            QwenImageEditCustomNode,
            "edit_image",
            side_effect=[(first_output,), (second_output,)],
        ) as qwen, patch(
            "create_manga_story.api.seedvr2_upscaler.upscale_confirmed_image",
            side_effect=lambda image: image,
        ) as upscale:
            latest, report, decision = GeminiQwenAssetGenerationLoop().generate(
                self.request,
                json.dumps([{
                    "asset_id": reference["asset_id"],
                    "role": "character_identity",
                    "reason": "approved face reference",
                }]),
                "qwen-model.safetensors",
                "clip.safetensors",
                "vae.safetensors",
                10,
                20,
                4.0,
                3,
                0.8,
                "api-key",
                "gemini-test",
                "https://example.invalid",
                10,
                0,
            )
        self.assertEqual(decision, "PASS")
        self.assertEqual(report["attempt_count"], 2)
        self.assertEqual(len(report["attempts"]), 2)
        self.assertEqual(qwen.call_count, 2)
        self.assertTrue(np.array_equal(latest, second_output))
        self.assertEqual(len(api_calls), 4)
        self.assertEqual(len(api_calls[0][1]), 1)
        self.assertEqual(len(api_calls[1][1]), 2)
        self.assertTrue(api_calls[1][1][0][0].startswith(b"\x89PNG"))
        self.assertEqual(len(api_calls[2][1]), 0)
        self.assertTrue(api_calls[3][1][0][0].startswith(b"\x89PNG"))
        self.assertEqual(
            [entry["asset_status"] for entry in report["attempts"]],
            [None, "approved"],
        )
        with self.database.connect() as connection:
            iteration_statuses = [
                row[0]
                for row in connection.execute(
                    """SELECT status FROM asset_workflow_iterations
                       WHERE request_id = 'request-1' ORDER BY attempt_index"""
                )
            ]
        self.assertEqual(iteration_statuses, ["retry_pending", "pass_candidate"])

    def test_reference_selector_rejects_missing_reference_asset(self):
        with patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ):
            with self.assertRaises(ValueError):
                ComicAssetReferenceSelector().select(
                    "project-1",
                    self.request,
                    json.dumps([{
                        "asset_id": "does-not-exist",
                        "role": "character_identity",
                        "reason": "missing reference",
                    }]),
                )

    def test_reference_selector_crops_transient_page_source(self):
        page_path = self.root / "page-1.png"
        Image.new("RGB", (10, 8), (120, 80, 40)).save(page_path)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE pages SET image_path = ? WHERE page_id = 'page-1'",
                (str(page_path),),
            )
        torch_shim = SimpleNamespace(from_numpy=lambda array: array)
        request = dict(self.request)
        request["entity_type"] = "location"
        request["entity_id"] = "location-1"
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO locations(location_id, project_id, canonical_name, stable_description) "
                "VALUES ('location-1', 'project-1', 'palace', 'palace')"
            )
        with patch.dict(sys.modules, {"torch": torch_shim}), patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ):
            image1, _, _, manifest, _ = ComicAssetReferenceSelector().select(
                "project-1",
                request,
                json.dumps([{
                    "source_page_id": "page-1",
                    "role": "scene_reference",
                    "reason": "first page source",
                    "crop_box": {"x": 0.2, "y": 0.25, "width": 0.5, "height": 0.5},
                }]),
            )
        self.assertEqual(image1.shape, (1, 4, 5, 3))
        self.assertEqual(manifest[0]["reference_kind"], "page_source_crop")
        self.assertAlmostEqual(manifest[0]["crop_box"]["width"], 0.5)

    def test_workflow_registrar_does_not_register_non_pass_qc(self):
        prompt = {
            "prompt_version": 1,
            "detailed_description": "Keep the approved identity.",
            "reference_usage": [],
            "must_preserve": ["identity"],
            "allowed_changes": [],
            "negative_constraints": ["no identity change"],
            "uncertainties": [],
            "validation_checklist": ["identity"],
        }
        qc = {
            "request_id": "request-1",
            "attempt_index": 0,
            "decision": "RETRY",
            "overall_score": 0.4,
            "checks": [{
                "category": "identity",
                "result": "FAIL",
                "severity": "high",
                "evidence": "The generated face does not match.",
            }],
            "failures": ["identity mismatch"],
            "uncertainties": [],
            "prompt_revision_instructions": ["Restore the approved identity."],
            "reference_revision_instructions": [],
            "human_review_required": False,
        }
        image_tensor = np.zeros((1, 2, 3, 3), dtype=np.float32)
        with patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ):
            assets, report = ComicAssetWorkflowRegistrar().register(
                image_tensor,
                self.request,
                prompt,
                "[]",
                json.dumps(qc),
                0,
                "retry candidate",
                -1,
                "{}",
            )
        self.assertEqual(assets, [])
        self.assertFalse(report["registered"])
        self.assertEqual(report["workflow_status"], "retry_pending")
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM assets WHERE project_id = 'project-1'"
                ).fetchone()[0],
                0,
            )
            iteration = connection.execute(
                "SELECT status FROM asset_workflow_iterations WHERE request_id = 'request-1'"
            ).fetchone()
        self.assertEqual(iteration["status"], "retry_pending")

    def test_generation_loop_persists_qwen_failure(self):
        reference = self.assets.register_batch(
            "project-1",
            "character_portrait",
            [{"content": _image(), "width": 3, "height": 2, "mime_type": "image/png"}],
            entity_type="character",
            entity_id="character-1",
            generation_run_id="failure-reference",
        )["assets"][0]
        self.assets.review_asset("project-1", reference["asset_id"], "approved", "verified")
        prompt = {
            "prompt_version": 1,
            "detailed_description": "Keep the approved identity.",
            "reference_usage": [{
                "reference_asset_id": reference["asset_id"],
                "role": "character_identity",
                "instruction": "Use this image for identity.",
            }],
            "must_preserve": ["identity"],
            "allowed_changes": [],
            "negative_constraints": [],
            "uncertainties": [],
            "validation_checklist": ["identity"],
        }
        with patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ), patch(
            "create_manga_story.nodes.asset_workflow_nodes.generate_structured_json",
            return_value=prompt,
        ), patch.object(
            QwenImageEditCustomNode,
            "edit_image",
            side_effect=RuntimeError("model unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                GeminiQwenAssetGenerationLoop().generate(
                    self.request,
                    json.dumps([{
                        "asset_id": reference["asset_id"],
                        "role": "character_identity",
                        "reason": "approved identity reference",
                    }]),
                    "qwen-model.safetensors",
                    "clip.safetensors",
                    "vae.safetensors",
                    10,
                    20,
                    4.0,
                    0,
                    0.8,
                    "api-key",
                    "gemini-test",
                    "https://example.invalid",
                    10,
                    0,
                )
        with self.database.connect() as connection:
            run = connection.execute(
                "SELECT status, error_message FROM asset_generation_runs "
                "WHERE project_id = 'project-1' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertIn("model unavailable", run["error_message"])

    def test_workflow_status_returns_request_and_iterations(self):
        workflow = ComicWorkflowRepository(self.database)
        workflow.initialize()
        workflow.save_asset_request("project-1", self.request)
        workflow.save_asset_iteration(
            "request-1",
            0,
            {"prompt_version": 1, "detailed_description": "test"},
            [],
            {"decision": "RETRY"},
            status="retry_pending",
        )
        with patch(
            "create_manga_story.nodes.asset_workflow_nodes._repository",
            return_value=self.assets,
        ):
            status_json = ComicAssetWorkflowStatus().get_status("request-1", "project-1")[0]
        status = json.loads(status_json)
        self.assertEqual(status["status"], "retry_pending")
        self.assertEqual(status["iterations"][0]["attempt_index"], 0)
        self.assertEqual(status["iterations"][0]["qc_report"]["decision"], "RETRY")

    def test_batch_generation_continues_after_single_request_failure(self):
        first = {"request_id": "request-1", "project_id": "project-1", "asset_type": "character_portrait"}
        second = {"request_id": "request-2", "project_id": "project-1", "asset_type": "character_portrait"}
        reports = [
            (None, {"request_id": "request-1"}, "PASS"),
            RuntimeError("temporary model failure"),
        ]
        with patch.object(
            GeminiQwenAssetGenerationLoop,
            "generate",
            side_effect=reports,
        ):
            with self.assertRaisesRegex(ValueError, "no IMAGE output"):
                ComicAssetBatchGenerationLoop().run_batch(
                    json.dumps([
                        {"asset_request": first, "references": []},
                        {"asset_request": second, "references": []},
                    ]),
                    "unet", "clip", "vae", 10, 20, 4.0, 0, 0.8,
                    "", "gemini-test", "https://example.invalid", 10, 0,
                )

    def test_successful_asset_is_reused_by_page_hash_and_character_identity(self):
        saved = self.assets.register_batch(
            project_id="project-1",
            asset_type="character_turnaround",
            images=[{"content": _image(), "width": 3, "height": 2}],
            generation_run_id="prior-page-character",
            entity_type="character",
            entity_id="character-1",
            parameters={
                "source_page_hash": "same-page-hash",
                "entity_type": "character",
                "entity_id": "character-1",
                "identity_key": "name:ari",
            },
        )["assets"][0]
        request = {
            "project_id": "project-1",
            "asset_type": "character_portrait",
            "entity_type": "character",
            "entity_id": "new-page-local-candidate-id",
            "identity_key": "name:ari",
            "source_page_hash": "same-page-hash",
        }
        reused = _reusable_generation(self.assets, request)
        self.assertEqual(reused["assets"][0]["asset_id"], saved["asset_id"])

    def test_character_generation_prompt_requires_front_side_back_triptych(self):
        prompt = _qwen2_extraction_directive({"asset_type": "character_portrait"})
        self.assertIn("EXACTLY THREE", prompt)
        self.assertIn("front view, true side profile, and back view", prompt)
        self.assertIn("pure white", prompt.lower())
        self.assertIn("facial-expression row", prompt)

    def test_scene_generation_prompt_requires_text_and_people_removal(self):
        prompt = _qwen2_extraction_directive({"asset_type": "scene_reference"})
        self.assertIn("Remove every visible person", prompt)
        self.assertIn("speech bubbles, text", prompt)

    def test_batch_generation_skips_pages_without_characters(self):
        with self.assertRaisesRegex(ValueError, "no image requests"):
            ComicAssetBatchGenerationLoop().run_batch(
                "[]", "unet", "clip", "vae", 10, 20, 4.0, 0, 0.8,
                "", "gemini-test", "https://example.invalid", 10, 0,
            )

    def test_page_resource_reader_rejects_unbound_requests(self):
        first = {"request_id": "request-1", "project_id": "project-1", "asset_type": "character_portrait"}
        second = {"request_id": "request-2", "project_id": "project-1", "asset_type": "scene_reference"}
        with patch.object(GeminiQwenAssetGenerationLoop, "generate") as generate:
            with self.assertRaisesRegex(ValueError, "no IMAGE output"):
                ComicAssetBatchGenerationLoop().run_batch(
                    json.dumps([
                        {"asset_request": first, "references": []},
                        {"asset_request": second, "references": []},
                    ]),
                    "unet", "clip", "vae", 10, 20, 4.0, 0, 0.8,
                    "", "gemini-test", "https://example.invalid", 10, 0,
                )
        generate.assert_not_called()

    def test_page_resource_reader_does_not_use_legacy_asset_cache(self):
        request = {
            "request_id": "cached-request",
            "project_id": "project-1",
            "asset_type": "character_portrait",
            "entity_type": "character",
            "entity_id": "character-1",
            "source_page_hash": "cached-page",
        }
        self.assets.register_batch(
            project_id="project-1",
            asset_type="character_portrait",
            images=[{"content": _image(), "width": 3, "height": 2}],
            generation_run_id="cached-run",
            entity_type="character",
            entity_id="character-1",
            parameters={
                "source_page_hash": "cached-page",
                "entity_type": "character",
                "entity_id": "character-1",
            },
        )
        cached_image = np.zeros((1, 2, 3, 3), dtype=np.float32)
        with patch("create_manga_story.nodes.asset_workflow_nodes._repository", return_value=self.assets), \
             patch("create_manga_story.nodes.asset_workflow_nodes._load_asset_image", return_value=cached_image), \
             patch.object(GeminiQwenAssetGenerationLoop, "generate") as generate:
            with self.assertRaisesRegex(ValueError, "no IMAGE output"):
                ComicAssetBatchGenerationLoop().run_batch(
                    json.dumps([{"asset_request": request, "references": []}]),
                    "unet", "clip", "vae", 10, 20, 4.0, 0, 0.8,
                    "", "gemini-test", "https://example.invalid", 10, 0,
                )
        generate.assert_not_called()

    def test_stack_batch_images_normalizes_rgb_rgba_and_grayscale(self):
        rgb = np.zeros((1, 2, 3, 3), dtype=np.float32)
        rgba = np.ones((1, 3, 2, 4), dtype=np.float32)
        gray = np.full((1, 1, 1, 1), 0.5, dtype=np.float32)
        batch = _stack_batch_images([rgb, rgba, gray])
        self.assertEqual(batch.shape, (3, 3, 3, 3))
        self.assertTrue(np.allclose(batch[1, :3, :2, :], 1.0))
        self.assertTrue(np.allclose(batch[2, :1, :1, :], 0.5))


if __name__ == "__main__":
    unittest.main()

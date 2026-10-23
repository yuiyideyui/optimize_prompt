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

from create_manga_story import NODE_CLASS_MAPPINGS
from create_manga_story.core.asset_repository import AssetRepository
from create_manga_story.core.database import ComicDatabase
from create_manga_story.nodes.asset_nodes import (
    ComicAssetLoader,
    ComicAssetRegistrar,
    ComicAssetRequestBuilder,
    _encode_comfy_images,
    _json_input,
)


def _png_bytes(color):
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format="PNG")
    return output.getvalue()


class AssetManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.database = ComicDatabase(self.root / "comic2anime.sqlite3")
        self.database.initialize()
        self.database.save_pages(
            [
                {
                    "page_id": "page-1",
                    "project_id": "project-1",
                    "chapter_id": "chapter-1",
                    "page_index": 1,
                    "source_path": "original.png",
                    "image_path": "original.png",
                    "sha256": "source-hash",
                    "width": 2,
                    "height": 2,
                }
            ]
        )
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO characters(
                    character_id, project_id, canonical_name, stable_description
                ) VALUES ('character-1', 'project-1', 'Ari', 'blue eyes')"""
            )
            connection.execute(
                """INSERT INTO character_appearances(
                    appearance_id, character_id, page_id, panel_index,
                    appearance_description, clothing
                ) VALUES ('appearance-1', 'character-1', 'page-1', 1,
                    'winter outfit', 'blue coat')"""
            )
            connection.execute(
                """INSERT INTO character_appearance_evidence(
                    evidence_id, appearance_id, page_id, panel_index, evidence_json
                ) VALUES ('evidence-1', 'appearance-1', 'page-1', 1, '{}')"""
            )
        self.repository = AssetRepository(
            self.database, self.root / "assets"
        )

    def _register(self, content=None, run_id="run-1", **kwargs):
        return self.repository.register_batch(
            project_id="project-1",
            asset_type="character_turnaround",
            images=[
                {
                    "content": content or _png_bytes((10, 20, 30)),
                    "width": 2,
                    "height": 2,
                    "mime_type": "image/png",
                    "metadata": {"output_index": 0},
                }
            ],
            generation_run_id=run_id,
            entity_type="character_appearance",
            entity_id="appearance-1",
            **kwargs,
        )

    def test_additive_migration_preserves_existing_comic_records(self):
        self.database.initialize()
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM pages WHERE page_id = 'page-1'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 2"
                ).fetchone()[0],
                1,
            )
            self.assertIn(
                "assets",
                {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                },
            )

    def test_batch_registers_independent_assets_and_generation_outputs(self):
        images = [
            {
                "content": _png_bytes((index, 0, 0)),
                "width": 2,
                "height": 2,
                "metadata": {"output_index": index},
            }
            for index in (40, 80)
        ]
        result = self.repository.register_batch(
            project_id="project-1",
            asset_type="character_turnaround",
            images=images,
            generation_run_id="batch-run",
            entity_type="character_appearance",
            entity_id="appearance-1",
            prompt="front side back",
            parameters={"steps": 20},
        )
        self.assertEqual(result["report"]["created"], 2)
        self.assertEqual(len({item["asset_id"] for item in result["assets"]}), 2)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT o.output_index, a.file_hash, a.width, a.height,
                    a.file_size, a.status FROM asset_generation_outputs o
                    JOIN assets a ON a.asset_id = o.asset_id
                    WHERE o.run_id = 'batch-run' ORDER BY o.output_index"""
            ).fetchall()
        self.assertEqual([row["output_index"] for row in rows], [0, 1])
        for index, row in enumerate(rows):
            self.assertEqual(row["file_hash"], hashlib.sha256(images[index]["content"]).hexdigest())
            self.assertEqual((row["width"], row["height"]), (2, 2))
            self.assertEqual(row["file_size"], len(images[index]["content"]))
            self.assertEqual(row["status"], "candidate")
            asset_path = self.repository.resolve_asset_path(
                self.repository.get_asset(result["assets"][index]["asset_id"])
            )
            self.assertTrue(asset_path.is_file())

    def test_generation_retry_is_idempotent_but_different_output_is_rejected(self):
        first = self._register()
        replay = self._register()
        self.assertEqual(first["assets"][0]["asset_id"], replay["assets"][0]["asset_id"])
        self.assertEqual(replay["report"]["reused"], 1)
        with self.assertRaisesRegex(ValueError, "different output"):
            self._register(content=_png_bytes((90, 90, 90)))
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1
            )

    def test_generation_parameter_json_redacts_sensitive_keys(self):
        self._register(
            parameters={
                "steps": 24,
                "gemini_api_key": "must-not-persist",
                "nested": {"refresh_token": "also-private"},
            }
        )
        with self.database.connect() as connection:
            parameters = json.loads(
                connection.execute(
                    "SELECT parameters_json FROM asset_generation_runs "
                    "WHERE run_id = 'run-1'"
                ).fetchone()[0]
            )
        self.assertEqual(parameters["steps"], 24)
        self.assertEqual(parameters["gemini_api_key"], "[REDACTED]")
        self.assertEqual(parameters["nested"]["refresh_token"], "[REDACTED]")
        self.assertNotIn("must-not-persist", json.dumps(parameters))

    def test_unsigned_64_bit_seed_is_persisted_and_replayed_exactly(self):
        seed = 0xFFFFFFFFFFFFFFFF
        first = self._register(run_id="uint64-seed-run", seed=seed)
        replay = self._register(run_id="uint64-seed-run", seed=seed)
        self.assertEqual(replay["report"]["reused"], 1)
        self.assertEqual(first["assets"][0]["asset_id"], replay["assets"][0]["asset_id"])
        with self.database.connect() as connection:
            stored_seed = connection.execute(
                "SELECT seed FROM asset_generation_runs "
                "WHERE run_id = 'uint64-seed-run'"
            ).fetchone()[0]
        self.assertEqual(stored_seed, str(seed))
        with self.assertRaisesRegex(ValueError, "unsigned 64-bit"):
            self._register(run_id="invalid-seed-run", seed=-1)

    def test_non_finite_json_numbers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            _json_input('{"strength": NaN}', "parameters_json", dict)

    def test_database_failure_removes_only_new_files(self):
        existing = self._register()
        before = self.repository.resolve_asset_path(existing["assets"][0])
        with patch.object(
            self.repository, "_save_database_records", side_effect=RuntimeError("db failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "db failed"):
                self._register(run_id="run-2", content=_png_bytes((1, 2, 3)))
        self.assertTrue(before.is_file())
        files = list((self.root / "assets").rglob("*.png"))
        self.assertEqual(files, [before])

    def test_invalid_entity_and_path_traversal_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.repository.register_batch(
                project_id="project-1",
                asset_type="other",
                images=[{"content": _png_bytes((0, 0, 0)), "width": 2, "height": 2}],
                entity_type="character",
                entity_id="missing",
            )
        self.assertFalse((self.root / "assets").exists())
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.repository._asset_path("../outside.png")
        with self.assertRaisesRegex(ValueError, "relative POSIX"):
            self.repository._asset_path("C:\\outside.png")

    def test_review_and_primary_switch_are_atomic_and_keep_old_assets(self):
        first = self._register(run_id="review-1")["assets"][0]["asset_id"]
        second = self._register(
            run_id="review-2", content=_png_bytes((100, 100, 100))
        )["assets"][0]["asset_id"]
        self.repository.review_asset(
            "project-1",
            first,
            "approved",
            "checked",
            set_primary=True,
            entity_type="character_appearance",
            entity_id="appearance-1",
            relation_type="turnaround_sheet",
        )
        self.repository.review_asset(
            "project-1",
            second,
            "approved",
            "new version",
            set_primary=True,
            entity_type="character_appearance",
            entity_id="appearance-1",
            relation_type="turnaround_sheet",
        )
        primary = self.repository.query_assets(
            "project-1",
            entity_type="character_appearance",
            entity_id="appearance-1",
            statuses=("approved",),
            primary_only=True,
        )
        self.assertEqual([item["asset_id"] for item in primary], [second])
        self.assertEqual(
            len(
                self.repository.query_assets(
                    "project-1",
                    entity_type="character_appearance",
                    entity_id="appearance-1",
                    statuses=("approved",),
                )
            ),
            2,
        )
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO projects(project_id) VALUES ('project-other')"
            )
        self.assertEqual(self.repository.query_assets("project-other", statuses=None), [])
        with self.assertRaisesRegex(ValueError, "not found in this project"):
            self.repository.load_asset_records("project-other", [second])
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM asset_reviews WHERE asset_id IN (?, ?)",
                    (first, second),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    """SELECT COUNT(*) FROM asset_relations
                        WHERE project_id = 'project-1' AND is_primary = 1
                        AND entity_id = 'appearance-1'
                        AND relation_type = 'turnaround_sheet'"""
                ).fetchone()[0],
                1,
            )
        self.repository.review_asset("project-1", second, "rejected", "not suitable")
        self.assertEqual(
            self.repository.query_assets(
                "project-1",
                entity_type="character_appearance",
                entity_id="appearance-1",
                statuses=("approved",),
                primary_only=True,
            ),
            [],
        )
        self.assertTrue(self.repository.resolve_asset_path(self.repository.get_asset(second)).is_file())

    def test_request_builder_includes_evidence_and_keeps_structured_request(self):
        with patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            request, prompt, reference_ids, warnings = (
                ComicAssetRequestBuilder().build_request(
                    "project-1",
                    "character_appearance",
                    "appearance-1",
                    "character_turnaround",
                    "three-view reference",
                    True,
                    False,
                )
            )
        self.assertEqual(request["schema_version"], "1.0")
        self.assertIn("blue coat", prompt)
        self.assertEqual(json.loads(reference_ids), [])
        self.assertEqual(request["source_evidence"][0]["page_id"], "page-1")
        self.assertEqual(json.loads(warnings), [])
        self.assertEqual(request["entity_id"], "appearance-1")

    def test_asset_request_can_be_connected_to_registrar_without_losing_entity(self):
        with patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            request = ComicAssetRequestBuilder().build_request(
                "project-1",
                "character_appearance",
                "appearance-1",
                "character_turnaround",
                "three-view reference",
                True,
                False,
            )[0]
            _, assets, report = ComicAssetRegistrar().register(
                np.zeros((1, 2, 2, 3), dtype=np.float32),
                "project-1",
                "",
                "",
                asset_request=request,
                generation_run_id="request-linked-run",
            )
        self.assertEqual(assets[0]["asset_type"], "character_turnaround")
        self.assertEqual(report["run_id"], "request-linked-run")
        self.assertEqual(
            assets[0]["relations"][0]["entity_id"],
            "appearance-1",
        )

    def test_candidate_reference_requires_explicit_permission(self):
        asset_id = self._register()["assets"][0]["asset_id"]
        with patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            with self.assertRaisesRegex(ValueError, "explicit permission"):
                ComicAssetRequestBuilder().build_request(
                    "project-1",
                    "character_appearance",
                    "appearance-1",
                    "character_turnaround",
                    "reference",
                    True,
                    False,
                    json.dumps([asset_id]),
                )
            request = ComicAssetRequestBuilder().build_request(
                "project-1",
                "character_appearance",
                "appearance-1",
                "character_turnaround",
                "reference",
                True,
                True,
                json.dumps([asset_id]),
            )[0]
        self.assertEqual(request["reference_asset_ids"], [asset_id])

    def test_character_request_warns_about_appearance_variants_and_uses_identity_refs(self):
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO character_appearances(
                    appearance_id, character_id, page_id, panel_index,
                    appearance_description, clothing
                ) VALUES ('appearance-2', 'character-1', 'page-1', 2,
                    'summer outfit', 'white shirt')"""
            )
        identity_asset = self.repository.register_batch(
            project_id="project-1",
            asset_type="character_portrait",
            images=[
                {
                    "content": _png_bytes((5, 10, 15)),
                    "width": 2,
                    "height": 2,
                }
            ],
            generation_run_id="identity-run",
            entity_type="character",
            entity_id="character-1",
        )["assets"][0]["asset_id"]
        self.repository.review_asset("project-1", identity_asset, "approved")
        with patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            request = ComicAssetRequestBuilder().build_request(
                "project-1",
                "character",
                "character-1",
                "character_pose",
                "pose reference",
                True,
                False,
            )[0]
        self.assertIn(identity_asset, request["reference_asset_ids"])
        self.assertTrue(any("multiple appearance variants" in item for item in request["warnings"]))
        self.assertEqual(len(request["source_evidence"]), 2)

    def test_registrar_validates_image_values_and_batch_shape(self):
        with self.assertRaisesRegex(ValueError, r"\[0, 1\]"):
            _encode_comfy_images(np.full((1, 2, 2, 3), 1.5, dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "shape"):
            _encode_comfy_images(np.zeros((2, 2), dtype=np.float32))
        encoded = _encode_comfy_images(
            np.zeros((2, 2, 2, 3), dtype=np.float32)
        )
        self.assertEqual(len(encoded), 2)
        with self.assertRaisesRegex(ValueError, "candidate"):
            ComicAssetRegistrar().register(
                np.zeros((1, 2, 2, 3), dtype=np.float32),
                "project-1",
                "other",
                "",
                review_status="approved",
            )

    def test_failed_registration_is_recorded_without_success_asset(self):
        with patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            with self.assertRaisesRegex(ValueError, r"\[0, 1\]"):
                ComicAssetRegistrar().register(
                    np.full((1, 2, 2, 3), 1.5, dtype=np.float32),
                    "project-1",
                    "other",
                    "",
                    generation_run_id="invalid-image-run",
                )
        with self.database.connect() as connection:
            run = connection.execute(
                "SELECT status, error_message FROM asset_generation_runs "
                "WHERE run_id = 'invalid-image-run'"
            ).fetchone()
            self.assertEqual(run["status"], "failed")
            self.assertIn("[0, 1]", run["error_message"])
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0
            )

    def test_registrar_generates_a_retry_key_from_semantic_inputs_and_output(self):
        input_image = np.zeros((1, 2, 2, 3), dtype=np.float32)
        with patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            first = ComicAssetRegistrar().register(
                input_image, "project-1", "other", "sample"
            )
            second = ComicAssetRegistrar().register(
                input_image.copy(), "project-1", "other", "sample"
            )
        self.assertEqual(first[2]["created"], 1)
        self.assertEqual(second[2]["reused"], 1)
        self.assertEqual(
            first[1][0]["asset_id"],
            second[1][0]["asset_id"],
        )

    def test_loader_requires_approval_and_reports_hash_mismatch(self):
        asset_id = self._register()["assets"][0]["asset_id"]
        with self.assertRaisesRegex(ValueError, "Unapproved assets"):
            self.repository.load_asset_records("project-1", [asset_id])
        self.repository.review_asset("project-1", asset_id, "approved")
        record = self.repository.get_asset(asset_id)
        self.repository.resolve_asset_path(record).write_bytes(_png_bytes((1, 1, 1)))
        fake_torch = SimpleNamespace(from_numpy=lambda array: array)
        with patch.dict(sys.modules, {"torch": fake_torch}), patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            image, loaded_ids, report = ComicAssetLoader().load_assets(
                "project-1", json.dumps([asset_id])
            )
        self.assertEqual(image.shape, (1, 2, 2, 3))
        self.assertEqual(json.loads(loaded_ids), [asset_id])
        self.assertTrue(any("Hash mismatch" in item for item in report["warnings"]))

    def test_loader_skip_policy_keeps_asset_ids_aligned_and_reports_missing_files(self):
        first = self._register(run_id="load-1")["assets"][0]["asset_id"]
        second = self._register(
            run_id="load-2", content=_png_bytes((30, 60, 90))
        )["assets"][0]["asset_id"]
        self.repository.review_asset("project-1", first, "approved")
        self.repository.review_asset("project-1", second, "approved")
        self.repository.resolve_asset_path(self.repository.get_asset(first)).unlink()
        fake_torch = SimpleNamespace(from_numpy=lambda array: array)
        with patch.dict(sys.modules, {"torch": fake_torch}), patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            image, loaded_ids, report = ComicAssetLoader().load_assets(
                "project-1",
                json.dumps([first, second]),
                failure_policy="skip",
            )
        self.assertEqual(image.shape[0], 1)
        self.assertEqual(json.loads(loaded_ids), [second])
        self.assertTrue(any("Skipped asset {}".format(first) in item for item in report["warnings"]))
        with patch.dict(sys.modules, {"torch": fake_torch}), patch(
            "create_manga_story.nodes.asset_nodes._repository",
            return_value=self.repository,
        ):
            with self.assertRaisesRegex(ValueError, first):
                ComicAssetLoader().load_assets(
                    "project-1", json.dumps([first]), failure_policy="skip"
                )

    def test_nodes_are_registered_without_loading_comfyui_runtime(self):
        self.assertIn("ComicAssetRequestBuilder", NODE_CLASS_MAPPINGS)
        self.assertIn("ComicAssetRegistrar", NODE_CLASS_MAPPINGS)
        self.assertIn("ComicAssetQuery", NODE_CLASS_MAPPINGS)
        self.assertIn("ComicAssetLoader", NODE_CLASS_MAPPINGS)
        self.assertIn("ComicAssetReview", NODE_CLASS_MAPPINGS)


if __name__ == "__main__":
    unittest.main()

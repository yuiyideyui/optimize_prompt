import json
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from create_manga_story.nodes.chapter_asset_generation import (
    ComicChapterAssetGeneration,
    _sort_sources,
)
from create_manga_story.nodes.asset_nodes import ComicAssetAutoTargetBatchBuilder
from create_manga_story.nodes.asset_workflow_nodes import (
    ComicAssetBatchGenerationLoop,
    GeminiQwenAssetGenerationLoop,
)


class ChapterAssetGenerationTests(unittest.TestCase):
    def _source(self, entity_type, entity_id="", entity_key="", quality=0.5, area=0.1, page=1, panel=1, name=None):
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_key": entity_key,
            "name": name or entity_id or entity_key,
            "reference_quality": quality,
            "crop_box": {"width": area, "height": 1.0},
            "is_best_reference": False,
            "confidence": 0.5,
            "page_index": page,
            "panel_index": panel,
            "asset": {"asset_id": "asset-{}-{}".format(entity_type, page)},
            "evidence": {},
        }

    def test_same_entity_and_location_keys_are_grouped(self):
        node = ComicChapterAssetGeneration()
        character_groups = node._base_groups([
            self._source("character", entity_key="king", page=1),
            self._source("character", entity_key="king", page=2),
            self._source("character", entity_key="servant", page=3),
        ])
        location_groups = node._base_groups([
            self._source("location", entity_key="bedroom", page=1),
            self._source("location", entity_key="bedroom", page=2),
        ])
        self.assertEqual(sorted(len(group) for group in character_groups), [1, 2])
        self.assertEqual([len(group) for group in location_groups], [2])

    def test_character_name_groups_across_different_candidate_ids(self):
        """Temporary page IDs must not split one normal character into assets."""
        node = ComicChapterAssetGeneration()
        groups = node._base_groups([
            self._source("character", entity_id="page-1-candidate", name="King", page=1),
            self._source("character", entity_id="page-2-candidate", name="King", page=2),
            self._source("character", entity_id="page-3-candidate", name="King", page=3),
        ])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)

    def test_character_stable_key_has_priority_over_candidate_id(self):
        node = ComicChapterAssetGeneration()
        groups = node._base_groups([
            self._source("character", entity_id="candidate-a", entity_key="king", name="King", page=1),
            self._source("character", entity_id="candidate-b", entity_key="king", name="King", page=2),
        ])
        self.assertEqual(len(groups), 1)
        self.assertEqual([item["page_index"] for item in groups[0]], [1, 2])

    def test_canonical_name_groups_different_identity_descriptions(self):
        node = ComicChapterAssetGeneration()
        groups = node._base_groups([
            self._source("character", entity_id="candidate-a", name="King", page=1),
            self._source("character", entity_id="candidate-b", name="King", page=2),
        ])
        self.assertEqual(len(groups), 1)

    def test_generic_soldier_variants_group_by_character_class_name(self):
        node = ComicChapterAssetGeneration()
        groups = node._base_groups([
            self._source("character", entity_key="soldier_1", name="Soldier", page=1),
            self._source("character", entity_key="soldier_2", name="Soldier", page=2),
            self._source("character", entity_key="soldiers_saluting", name="Soldier", page=3),
        ])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)

    def test_quality_is_primary_sort_key(self):
        ordered = _sort_sources([
            self._source("character", quality=0.6, area=0.9),
            self._source("character", quality=1.0, area=0.1),
            self._source("character", quality=0.7, area=0.8),
        ])
        self.assertEqual(ordered[0]["reference_quality"], 1.0)

    def test_reuse_ignores_other_empty_entity_groups(self):
        """Unresolved entities share entity_id='', but must reuse independently."""
        class Rows(list):
            def fetchall(self):
                return self

        class Connection:
            def execute(self, sql, params=()):
                return Rows([
                    {
                        "group_id": "character-a",
                        "variant_key": "normal",
                        "status": "approved",
                        "generated_asset_id": "asset-character-a",
                        "selected_source_asset_id": "source-a",
                        "decision_json": json.dumps({"base_fingerprint": "wanted"}),
                    },
                    {
                        "group_id": "character-b",
                        "variant_key": "normal",
                        "status": "approved",
                        "generated_asset_id": "asset-character-b",
                        "selected_source_asset_id": "source-b",
                        "decision_json": json.dumps({"base_fingerprint": "other"}),
                    },
                ])

        class Database:
            @contextmanager
            def connect(self):
                yield Connection()

        repo = MagicMock()
        repo.get_asset.return_value = {"asset_id": "asset-character-a", "status": "approved"}
        with patch("create_manga_story.nodes.chapter_asset_generation._load_asset_image", return_value="image"):
            reused = ComicChapterAssetGeneration()._reuse_entity_groups(
                Database(), repo, "project-1", "chapter-1", "character", "", "wanted"
            )
        self.assertIsNotNone(reused)
        self.assertEqual(reused[1][0]["generated_asset_id"], "asset-character-a")
        repo.get_asset.assert_called_once_with("asset-character-a", "project-1")

    def test_reuse_filter_also_applies_to_unresolved_locations(self):
        """Scene groups have the same empty-ID collision as character groups."""
        class Rows(list):
            def fetchall(self):
                return self

        class Database:
            @contextmanager
            def connect(self):
                yield type("Connection", (), {
                    "execute": lambda _self, _sql, _params=(): Rows([{
                        "group_id": "location-a",
                        "variant_key": "day",
                        "status": "approved",
                        "generated_asset_id": "asset-location-a",
                        "selected_source_asset_id": "source-location-a",
                        "decision_json": json.dumps({"base_fingerprint": "wanted-location"}),
                    }, {
                        "group_id": "location-b",
                        "variant_key": "night",
                        "status": "approved",
                        "generated_asset_id": "asset-location-b",
                        "selected_source_asset_id": "source-location-b",
                        "decision_json": json.dumps({"base_fingerprint": "other-location"}),
                    }])
                })()

        repo = MagicMock()
        repo.get_asset.return_value = {"asset_id": "asset-location-a", "status": "approved"}
        with patch("create_manga_story.nodes.chapter_asset_generation._load_asset_image", return_value="image"):
            reused = ComicChapterAssetGeneration()._reuse_entity_groups(
                Database(), repo, "project-1", "chapter-1", "location", "", "wanted-location"
            )
        self.assertIsNotNone(reused)
        self.assertEqual(reused[1][0]["generated_asset_id"], "asset-location-a")

    def test_chapter_report_exposes_output_count(self):
        """The report must make a multi-image chapter output observable."""
        report = {
            "groups": 3,
            "approved_groups": 3,
            "output_image_count": 3,
            "output_asset_ids": ["king", "butler", "castle-room"],
            "errors": [],
        }
        self.assertEqual(report["output_image_count"], report["approved_groups"])
        self.assertEqual(len(report["output_asset_ids"]), 3)

    def test_reuse_matches_existing_approved_asset_by_source_crop(self):
        class Rows(list):
            def fetchall(self):
                return self

        class Connection:
            def execute(self, sql, params=()):
                return Rows([{
                    "group_id": "old-group",
                    "entity_type": "character",
                    "entity_id": "old-candidate-id",
                    "variant_key": "king_default",
                    "status": "approved",
                    "generated_asset_id": "old-asset",
                    "selected_source_asset_id": "source-king",
                    "source_crop_asset_id": "source-king",
                    "updated_at": "2026-10-26 12:00:00",
                }])

        class Database:
            @contextmanager
            def connect(self):
                yield Connection()

        repo = MagicMock()
        repo.get_asset.return_value = {"asset_id": "old-asset", "status": "approved"}
        items = [self._source("character", entity_id="new-candidate-id", page=1)]
        items[0]["asset"]["asset_id"] = "source-king"
        with patch("create_manga_story.nodes.chapter_asset_generation._load_asset_image", return_value="image"):
            reused = ComicChapterAssetGeneration()._reuse_source_matched_group(
                Database(), repo, "project-1", "chapter-1", items,
            )
        self.assertIsNotNone(reused)
        self.assertEqual(reused[1][0]["generated_asset_id"], "old-asset")

    def test_sqlite_entity_binding_is_reused_before_fingerprint(self):
        class Rows(list):
            def fetchall(self):
                return self

        class Connection:
            def execute(self, sql, params=()):
                return Rows([{
                    "asset_id": "bound-asset",
                    "asset_type": "character_turnaround",
                    "status": "approved",
                    "relative_path": "project-1/character_turnaround/bound-asset.png",
                    "relation_type": "chapter:chapter-1:normal",
                    "group_id": "bound-group",
                    "variant_key": "normal",
                }])

        class Database:
            @contextmanager
            def connect(self):
                yield Connection()

        repo = MagicMock()
        with patch("create_manga_story.nodes.chapter_asset_generation._load_asset_image", return_value="image"):
            reused = ComicChapterAssetGeneration()._reuse_bound_entity_asset(
                Database(), repo, "project-1", "chapter-1", "character", "character-1",
            )
        self.assertEqual(reused[1][0]["generated_asset_id"], "bound-asset")

    def test_asset_workflow_reads_current_page_binding_for_minimax(self):
        class Rows(list):
            def fetchall(self):
                return self

        class Connection:
            def execute(self, sql, params=()):
                return Rows([{
                    "group_id": "king-group",
                    "bound_entity_id": "old-page-candidate",
                    "asset_id": "king-master",
                    "variant_key": "normal",
                    "decision_json": json.dumps({
                        "group": {"variant_key": "normal"},
                        "source": {"name": "King"},
                    }),
                    "evidence_json": json.dumps({"name": "King", "same_entity_key": "king"}),
                }])

        class Database:
            @contextmanager
            def connect(self):
                yield Connection()

        repo = MagicMock()
        repo.database = Database()
        refs = ComicAssetBatchGenerationLoop._page_bound_references(
            repo, "project-1", "chapter-1", "page-2", "character",
            "new-page-candidate", {"name": "King", "same_entity_key": "king"},
        )
        self.assertEqual([item["asset_id"] for item in refs], ["king-master"])

    def test_asset_workflow_does_not_match_king_to_demon_king(self):
        class Rows(list):
            def fetchall(self):
                return self

        class Connection:
            def execute(self, sql, params=()):
                return Rows([
                    {
                        "group_id": "demon-king-group",
                        "bound_entity_id": "demon-king-id",
                        "asset_id": "demon-king-master",
                        "variant_key": "default",
                        "source_panel_index": 1,
                        "decision_json": json.dumps({"group": {"variant_key": "default"}}),
                        "evidence_json": json.dumps({
                            "name": "Demon King", "panel_index": 1,
                            "crop_box": {"x": 0.55, "y": 0.61, "width": 0.18, "height": 0.20},
                        }),
                    },
                    {
                        "group_id": "king-group",
                        "bound_entity_id": "king-id",
                        "asset_id": "king-master",
                        "variant_key": "default",
                        "source_panel_index": 0,
                        "decision_json": json.dumps({"group": {"variant_key": "default"}}),
                        "evidence_json": json.dumps({
                            "name": "King", "panel_index": 0,
                            "crop_box": {"x": 0.05, "y": 0.35, "width": 0.22, "height": 0.20},
                        }),
                    },
                ])

        class Database:
            @contextmanager
            def connect(self):
                yield Connection()

        repo = MagicMock()
        repo.database = Database()
        refs = ComicAssetBatchGenerationLoop._page_bound_references(
            repo, "project-1", "chapter-1", "page-3", "character",
            "new-page-candidate", {
                "name": "King", "panel_index": 0,
                "crop_box": {"x": 0.05, "y": 0.35, "width": 0.22, "height": 0.20},
            },
        )
        self.assertEqual([item["asset_id"] for item in refs], ["king-master"])

    @patch("create_manga_story.nodes.chapter_asset_generation._single_image_bytes", return_value=(b"png", "image/png"))
    @patch("create_manga_story.nodes.chapter_asset_generation._load_asset_image", return_value=object())
    @patch("create_manga_story.nodes.chapter_asset_generation.generate_structured_json")
    def test_low_quality_gemini_selection_falls_back_to_best(self, generate, _load, _bytes):
        generate.return_value = {"groups": [{
            "variant_key": "normal",
            "variant_type": "normal",
            "variant_description": "normal",
            "source_indices": [0, 1],
            "selected_source_index": 0,
            "reason": "low quality selection",
            "generation_requirements": [],
            "uncertainties": [],
        }]}
        items = [
            self._source("character", quality=0.6, page=1),
            self._source("character", quality=1.0, page=2),
        ]
        result = ComicChapterAssetGeneration()._group_with_gemini(
            items, "{}", "gemini-test", "key", "https://example.invalid", 10, 0
        )
        self.assertEqual(result[0]["selected_source_index"], 1)
        self.assertTrue(result[0]["selection_warning"])

    @patch("create_manga_story.nodes.chapter_asset_generation._single_image_bytes", return_value=(b"png", "image/png"))
    @patch("create_manga_story.nodes.chapter_asset_generation._load_asset_image", return_value=object())
    @patch("create_manga_story.nodes.chapter_asset_generation.generate_structured_json")
    def test_gemini_can_split_transformation_state(self, generate, _load, _bytes):
        generate.return_value = {"groups": [
            {"variant_key": "normal", "variant_type": "normal", "variant_description": "", "source_indices": [0], "selected_source_index": 0, "reason": "", "generation_requirements": [], "uncertainties": []},
            {"variant_key": "transformed", "variant_type": "transformation", "variant_description": "changed body", "source_indices": [1], "selected_source_index": 1, "reason": "persistent change", "generation_requirements": ["preserve identity"], "uncertainties": []},
        ]}
        items = [
            self._source("character", entity_key="king", quality=0.8, page=1),
            self._source("character", entity_key="king", quality=0.9, page=2),
        ]
        result = ComicChapterAssetGeneration()._group_with_gemini(
            items, "story", "gemini-test", "key", "https://example.invalid", 10, 0
        )
        self.assertEqual([item["variant_key"] for item in result], ["normal", "transformed"])
        self.assertEqual(result[1]["variant_type"], "transformation")

    @patch("create_manga_story.nodes.chapter_asset_generation._single_image_bytes", return_value=(b"png", "image/png"))
    @patch("create_manga_story.nodes.chapter_asset_generation._load_asset_image", return_value=object())
    @patch("create_manga_story.nodes.chapter_asset_generation.generate_structured_json")
    def test_ordinary_character_changes_are_merged(self, generate, _load, _bytes):
        generate.return_value = {"groups": [
            {"variant_key": "newspaper", "variant_type": "normal", "variant_description": "reading newspaper", "source_indices": [0], "selected_source_index": 0, "reason": "context", "generation_requirements": [], "uncertainties": []},
            {"variant_key": "crying", "variant_type": "other_variant", "variant_description": "crying", "source_indices": [1], "selected_source_index": 1, "reason": "emotion", "generation_requirements": [], "uncertainties": []},
            {"variant_key": "battle", "variant_type": "battle", "variant_description": "battle reaction", "source_indices": [2], "selected_source_index": 2, "reason": "action", "generation_requirements": [], "uncertainties": []},
        ]}
        items = [
            self._source("character", entity_key="king", page=1),
            self._source("character", entity_key="king", page=2),
            self._source("character", entity_key="king", page=3),
        ]
        result = ComicChapterAssetGeneration()._group_with_gemini(
            items, "story", "gemini-test", "key", "https://example.invalid", 10, 0
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["variant_key"], "normal")
        self.assertEqual(result[0]["source_indices"], [0, 1, 2])

    def test_page_entity_prefers_approved_chapter_asset(self):
        repo = MagicMock()
        connection = repo.database.connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchall.return_value = [{
            "group_id": "group-1",
            "variant_key": "normal",
            "variant_type": "normal",
            "generated_asset_id": "chapter-asset-1",
            "asset_status": "approved",
            "page_match": 1,
        }]
        refs = ComicAssetAutoTargetBatchBuilder._chapter_asset_references(
            repo, "project-1", "chapter-1", "character", "king", "page-3", {}
        )
        self.assertEqual([item["asset_id"] for item in refs], ["chapter-asset-1"])
        self.assertEqual(refs[0]["role"], "chapter_asset_reference")

    def test_legacy_empty_location_binding_matches_by_source_page(self):
        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                if "FROM chapter_asset_groups g" in sql and "g.entity_id = ?" in sql:
                    return Rows([])
                if "FROM locations WHERE location_id" in sql:
                    return Rows([{"canonical_name": "Royal Palace and City"}])
                if "g.entity_id = ''" in sql:
                    return Rows([{
                        "group_id": "group-legacy",
                        "variant_key": "daylight_floating_island",
                        "variant_type": "day",
                        "generated_asset_id": "chapter-scene-1",
                        "asset_status": "approved",
                        "page_match": 1,
                        "decision_json": json.dumps({
                            "group": {
                                "variant_key": "daylight_floating_island",
                                "variant_description": "Royal Palace and City",
                                "source_indices": [0],
                            }
                        }),
                    }])
                if "FROM chapter_asset_group_sources" in sql:
                    return Rows([{"evidence_json": json.dumps({
                        "same_location_key": "palace_city_island"
                    })}])
                return Rows([])

        class Rows(list):
            def fetchall(self):
                return self

            def fetchone(self):
                return self[0] if self else None

        class Database:
            @contextmanager
            def connect(self):
                yield connection

        connection = Connection()
        repo = MagicMock()
        repo.database = Database()
        refs = ComicAssetAutoTargetBatchBuilder._chapter_asset_references(
            repo,
            "project-1",
            "chapter-1",
            "location",
            "new-location-id",
            "page-1",
            {"same_location_key": "palace_city_island"},
        )
        self.assertEqual([item["asset_id"] for item in refs], ["chapter-scene-1"])
        self.assertEqual(refs[0]["chapter_asset_group_id"], "group-legacy")

    def test_two_same_page_locations_match_their_own_panel(self):
        class Rows(list):
            def fetchall(self):
                return self

            def fetchone(self):
                return self[0] if self else None

        class Connection:
            def execute(self, sql, params=()):
                if "FROM chapter_asset_groups g" in sql and "g.entity_id = ?" in sql:
                    return Rows([])
                if "g.entity_id = ''" in sql:
                    requested_panel = params[2]
                    return Rows([
                        {
                            "group_id": "group-panel-0",
                            "variant_key": "floating_island_world_default",
                            "variant_type": "normal",
                            "generated_asset_id": "asset-panel-0",
                            "asset_status": "approved",
                            "page_match": int(requested_panel == 0),
                            "panel_match": int(requested_panel == 0),
                            "decision_json": "{}",
                        },
                        {
                            "group_id": "group-panel-1",
                            "variant_key": "island_kingdom_default",
                            "variant_type": "normal",
                            "generated_asset_id": "asset-panel-1",
                            "asset_status": "approved",
                            "page_match": int(requested_panel == 1),
                            "panel_match": int(requested_panel == 1),
                            "decision_json": "{}",
                        },
                    ])
                return Rows([])

        class Database:
            @contextmanager
            def connect(self):
                yield Connection()

        repo = MagicMock()
        repo.database = Database()
        refs = ComicAssetAutoTargetBatchBuilder._chapter_asset_references(
            repo, "project-1", "chapter-1", "location", "new-id",
            "page-1", {"panel_index": 1},
        )
        self.assertEqual([item["asset_id"] for item in refs], ["asset-panel-1"])

    def test_legacy_chapter_group_cannot_bypass_page_binding(self):
        repo = MagicMock()
        request = {
            "project_id": "project-1",
            "chapter_id": "chapter-1",
            "entity_type": "character",
            "entity_id": "king",
            "source_page_id": "page-3",
            "source_evidence": [{}],
        }
        with patch.object(
            ComicAssetBatchGenerationLoop,
            "_page_bound_references",
            return_value=[],
        ), patch.object(
            ComicAssetAutoTargetBatchBuilder,
            "_chapter_asset_references",
            return_value=[{"asset_id": "chapter-asset-1"}],
        ):
            references, selected = ComicAssetBatchGenerationLoop._bind_chapter_asset_reference(
                repo, request, []
            )
        self.assertEqual(references, [])
        self.assertIsNone(selected)
        self.assertNotIn("chapter_asset_reused", request)

    @patch("create_manga_story.nodes.asset_workflow_nodes._load_asset_image")
    @patch("create_manga_story.nodes.asset_workflow_nodes._repository")
    @patch.object(GeminiQwenAssetGenerationLoop, "generate")
    @patch.object(
        ComicAssetBatchGenerationLoop,
        "_bind_chapter_asset_reference",
        return_value=([{
            "asset_id": "chapter-asset-1",
            "role": "chapter_asset_reference",
            "chapter_asset_group_id": "group-1",
        }], [{
            "asset_id": "chapter-asset-1",
            "chapter_asset_group_id": "group-1",
        }]),
    )
    def test_batch_reuses_chapter_asset_without_calling_image_generation(
        self, bind, generate, repository, load_image
    ):
        import numpy as np

        repo = MagicMock()
        repo.get_asset.return_value = {"asset_id": "chapter-asset-1", "status": "approved"}
        repository.return_value = repo
        load_image.return_value = np.zeros((1, 2, 2, 3), dtype=np.float32)
        request = {
            "request_id": "request-1",
            "project_id": "project-1",
            "chapter_id": "chapter-1",
            "entity_type": "character",
            "entity_id": "king",
            "asset_type": "character_turnaround",
        }
        images, report_json, decision = ComicAssetBatchGenerationLoop().run_batch(
            json.dumps([{"asset_request": request, "references": []}]),
            "unet", "clip", "vae", 0, 1, 1.0, 0, 0.8,
            "", "gemini-test", "https://example.invalid", 10, 0,
        )
        self.assertIsNotNone(images)
        self.assertEqual(decision, "PASS")
        self.assertEqual(json.loads(report_json)["output_image_count"], 1)
        self.assertTrue(json.loads(report_json)["results"][0]["report"]["chapter_asset_reused"])
        generate.assert_not_called()

    @patch("create_manga_story.nodes.asset_workflow_nodes._load_asset_image")
    @patch("create_manga_story.nodes.asset_workflow_nodes._repository")
    def test_chapter_reuse_does_not_emit_transient_reference_images(self, repository, load_image):
        import numpy as np

        repo = MagicMock()
        repo.get_asset.side_effect = lambda asset_id, project_id=None: {
            "asset_id": asset_id,
            "project_id": project_id or "project-1",
            "status": "approved",
        }
        repository.return_value = repo
        load_image.side_effect = lambda asset: np.zeros((1, 2, 2, 3), dtype=np.float32)
        request = {
            "request_id": "request-duplicate-check",
            "project_id": "project-1",
            "chapter_id": "chapter-1",
            "entity_type": "location",
            "entity_id": "bedroom",
            "asset_type": "scene_reference",
        }
        chapter_ref = {
            "asset_id": "chapter-scene-1",
            "role": "chapter_asset_reference",
            "chapter_asset_group_id": "group-1",
        }
        transient_ref = {
            "asset_id": "page-crop-1",
            "role": "current_page_reference",
        }
        node = ComicAssetBatchGenerationLoop()
        with patch.object(
            ComicAssetBatchGenerationLoop,
            "_bind_chapter_asset_reference",
            return_value=([chapter_ref, transient_ref], [chapter_ref]),
        ):
            images, report_json, decision = node.run_batch(
                json.dumps([{"asset_request": request, "references": []}]),
                "unet", "clip", "vae", 0, 1, 1.0, 0, 0.8,
                "", "gemini-test", "https://example.invalid", 10, 0,
                require_chapter_assets=True,
            )
        report = json.loads(report_json)
        self.assertEqual(decision, "PASS")
        self.assertEqual(report["output_image_count"], 1)
        self.assertEqual(report["results"][0]["asset_ids"], ["chapter-scene-1"])

    @patch("create_manga_story.nodes.asset_workflow_nodes._repository")
    @patch.object(GeminiQwenAssetGenerationLoop, "generate")
    def test_empty_page_requests_are_rejected(self, generate, repository):
        repository.return_value = MagicMock()
        with self.assertRaisesRegex(ValueError, "no image requests"):
            ComicAssetBatchGenerationLoop().run_batch(
                "[]", "unet", "clip", "vae", 0, 1, 1.0, 0, 0.8,
                "", "gemini-test", "https://example.invalid", 10, 0,
            )
        generate.assert_not_called()

    @patch("create_manga_story.nodes.asset_workflow_nodes._repository")
    @patch.object(ComicAssetBatchGenerationLoop, "_bind_chapter_asset_reference", return_value=([], []))
    def test_strict_mode_rejects_missing_chapter_asset(self, bind, repository):
        with self.assertRaisesRegex(ValueError, "no IMAGE output"):
            ComicAssetBatchGenerationLoop().run_batch(
                json.dumps([{
                    "asset_request": {
                        "request_id": "request-missing",
                        "project_id": "project-1",
                        "chapter_id": "chapter-1",
                        "entity_type": "location",
                        "entity_id": "bedroom",
                        "asset_type": "scene_reference",
                    },
                    "references": [],
                }]),
                "unet", "clip", "vae", 0, 1, 1.0, 0, 0.8,
                "", "gemini-test", "https://example.invalid", 10, 0,
                require_chapter_assets=True,
            )


if __name__ == "__main__":
    unittest.main()

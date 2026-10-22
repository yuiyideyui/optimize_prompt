import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from create_manga_story import NODE_CLASS_MAPPINGS
from create_manga_story.nodes.comic_loader import ComicChapterLoader
from create_manga_story.nodes.gemini_analyzer import GeminiComicPageAnalyzer
from create_manga_story.core.database import ComicDatabase
from create_manga_story.core.schemas import validate_analysis
from create_manga_story.core.storage import get_data_root, safe_component
from create_manga_story.api.gemini_client import _endpoint


class CoreTests(unittest.TestCase):
    def test_safe_component_rejects_empty_and_path_traversal(self):
        with self.assertRaises(ValueError):
            safe_component("../")
        self.assertEqual(safe_component("chapter 1"), "chapter_1")
        self.assertNotEqual(safe_component("x" * 101), safe_component("x" * 100 + "y"))
        self.assertTrue(safe_component("CON").startswith("CON-"))

    def test_data_root_defaults_to_custom_node_directory_and_can_be_overridden(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                get_data_root(),
                Path(__file__).resolve().parents[2] / "comic2anime_data",
            )
        with patch.dict("os.environ", {"COMIC2ANIME_DATA_DIR": "~/comic-test"}):
            self.assertEqual(
                get_data_root(),
                (Path.home() / "comic-test").resolve(),
            )

    def test_analysis_schema_requires_all_top_level_fields(self):
        with self.assertRaises(ValueError):
            validate_analysis({"summary": "Page"})

    def test_analysis_schema_checks_nested_fields(self):
        value = {
            "summary": "Page",
            "panels": [{"panel_index": "one"}],
            "characters": [],
            "locations": [],
            "dialogues": [],
            "story_events": [],
            "continuity": [],
            "uncertainties": [],
        }
        with self.assertRaises(ValueError):
            validate_analysis(value)

    def test_gemini_endpoint_supports_api_root_and_custom_key(self):
        self.assertEqual(
            _endpoint("https://proxy.example/v1beta", "model-x", "a key"),
            "https://proxy.example/v1beta/models/model-x:generateContent?key=a%20key",
        )

    def test_comfyui_nodes_are_registered(self):
        for node_id in (
            "ComicChapterLoader",
            "GeminiComicPageAnalyzer",
            "ComicPageContextBuilder",
            "ComicEntityResolver",
            "ComicKnowledgeRetriever",
            "ComicChapterSummarizer",
            "ComicKnowledgeBaseUpdater",
            "ComicKnowledgeIndexRebuilder",
        ):
            self.assertIn(node_id, NODE_CLASS_MAPPINGS)
        self.assertEqual(ComicChapterLoader.RETURN_TYPES[0], "COMIC_PAGES")
        self.assertEqual(GeminiComicPageAnalyzer.RETURN_TYPES[0], "COMIC_ANALYSIS")

    def test_loader_sorts_deduplicates_and_keeps_source_files_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            images = base / "source"
            images.mkdir()
            (images / "page10.png").write_bytes(b"ten")
            (images / "page2.png").write_bytes(b"two")
            (images / "page2z.png").write_bytes(b"two")
            (images / "corrupt.png").write_bytes(b"invalid")
            source_bytes = {
                path.name: path.read_bytes() for path in images.iterdir()
            }
            with patch(
                "create_manga_story.nodes.comic_loader.get_data_root",
                return_value=base / "data",
            ), patch(
                "create_manga_story.nodes.comic_loader._image_dimensions",
                side_effect=[
                    OSError("corrupt image"),
                    (10, 20),
                    (10, 20),
                    (10, 20),
                ],
            ):
                pages, _, _, report_json = ComicChapterLoader().load_chapter(
                    "project", "chapter", "natural_filename", "skip", str(images)
                )
            report = json.loads(report_json)
            self.assertEqual(report["imported"], 2)
            self.assertEqual(report["duplicates"], 1)
            self.assertEqual(
                Path(report["duplicate_details"][0]["source_path"]).name,
                "page2z.png",
            )
            self.assertEqual(len(report["errors"]), 1)
            self.assertEqual(
                [Path(page["source_path"]).name for page in pages],
                ["page2.png", "page10.png"],
            )
            self.assertTrue(all(page["page_index"] == index for index, page in enumerate(pages, 1)))
            self.assertEqual(
                {path.name: path.read_bytes() for path in images.iterdir()},
                source_bytes,
            )

    def test_analyzer_persists_successful_json_and_database_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = {
                "page_id": "page-1",
                "project_id": "project-1",
                "chapter_id": "chapter-1",
                "page_index": 1,
                "source_path": "page.png",
                "image_path": str(root / "page.png"),
                "sha256": "abc",
                "width": 10,
                "height": 20,
            }
            database = ComicDatabase(root / "comic2anime.sqlite3")
            database.initialize()
            database.save_pages([page])
            analysis = {
                "summary": "A page",
                "panels": [],
                "characters": [],
                "locations": [],
                "dialogues": [],
                "story_events": [],
                "continuity": [],
                "uncertainties": [],
            }
            with patch(
                "create_manga_story.nodes.gemini_analyzer.get_data_root",
                return_value=root,
            ), patch(
                "create_manga_story.nodes.gemini_analyzer.analyze_image",
                return_value=analysis,
            ):
                result, result_json, saved_path = GeminiComicPageAnalyzer().analyze_page(
                    [page], 1, "", "gemini-test", "https://example.test", 10, 0
                )
            self.assertEqual(result["page_id"], "page-1")
            self.assertEqual(json.loads(result_json), result)
            self.assertTrue(Path(saved_path).is_file())
            with database.connect() as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0],
                    1,
                )

    def test_analyzer_api_failure_does_not_write_an_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = {
                "page_id": "page-1",
                "project_id": "project-1",
                "chapter_id": "chapter-1",
                "image_path": str(root / "page.png"),
            }
            database = ComicDatabase(root / "comic2anime.sqlite3")
            database.initialize()
            database.save_pages(
                [
                    {
                        **page,
                        "page_index": 1,
                        "source_path": "page.png",
                        "sha256": "abc",
                        "width": 10,
                        "height": 20,
                    }
                ]
            )
            with patch(
                "create_manga_story.nodes.gemini_analyzer.get_data_root",
                return_value=root,
            ), patch(
                "create_manga_story.nodes.gemini_analyzer.analyze_image",
                side_effect=RuntimeError("Gemini is unavailable"),
            ), self.assertRaisesRegex(RuntimeError, "Gemini is unavailable"):
                GeminiComicPageAnalyzer().analyze_page(
                    [page], 1, "", "gemini-test", "https://example.test", 10, 0
                )
            with database.connect() as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0],
                    0,
                )
            self.assertFalse((root / "analyses").exists())

    def test_database_persists_page_and_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ComicDatabase(Path(directory) / "comic.sqlite3")
            database.initialize()
            page = {
                "page_id": "page-1",
                "project_id": "project-1",
                "chapter_id": "chapter-1",
                "page_index": 1,
                "source_path": "page.png",
                "image_path": "page.png",
                "sha256": "abc",
                "width": 10,
                "height": 20,
            }
            database.save_pages([page])
            result = {
                "summary": "A page",
                "panels": [],
                "characters": [],
                "locations": [],
                "dialogues": [],
                "story_events": [],
                "continuity": [],
                "uncertainties": [],
            }
            database.save_analysis(
                "analysis-1",
                "page-1",
                "gemini-test",
                "prompt-v1",
                result,
                Path(directory) / "analysis.json",
            )
            with database.connect() as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 1
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()

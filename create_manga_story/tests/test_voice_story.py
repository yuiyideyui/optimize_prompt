import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from create_manga_story.core.database import ComicDatabase
from create_manga_story.nodes.voice_story_nodes import (
    REFERENCE_TEXT,
    ComicAnimePageStoryboardPrompt,
    ComicCharacterVoiceReference,
    ComicPageVoiceReferences,
)
from create_manga_story.nodes.asset_workflow_nodes import (
    ChatGPTImageAssetBatchGenerationLoop,
    ComicAssetImageBatchGenerationLoop,
)
from anime_pipeline.prompts import SYSTEM_PROMPT_GENERATE, SYSTEM_PROMPT_AUTO_REFINE


class VoiceStoryNodeTests(unittest.TestCase):
    def test_storyboard_pipeline_has_hard_plot_boundary(self):
        self.assertIn("剧情边界硬约束", SYSTEM_PROMPT_GENERATE)
        self.assertIn("剧情边界硬约束", SYSTEM_PROMPT_AUTO_REFINE)
    def test_image_asset_loops_accept_storyboard_and_voice_context(self):
        for node_class in (ComicAssetImageBatchGenerationLoop, ChatGPTImageAssetBatchGenerationLoop):
            optional = node_class.INPUT_TYPES().get("optional", {})
            self.assertIn("page_storyboard_prompt", optional)
            self.assertIn("voice_manifest_json", optional)

    def test_storyboard_prompt_is_page_local_and_uses_placeholders(self):
        prompt, summary, mapping = ComicAnimePageStoryboardPrompt().build(
            json.dumps({
                "summary": "国王发现公主失踪，王宫陷入混乱。",
                "characters": [{"name": "King"}],
                "locations": [{"name": "Royal Palace"}],
            }),
            json.dumps([{"label": "国王三视图"}, {"label": "王宫场景"}]),
            json.dumps([{"character_name": "国王"}]),
            "国王发现公主失踪并开始寻找。",
            "上一页完整分镜：Part 1（不应原样回传）",
        )
        self.assertIn("<picture 1>", prompt)
        self.assertIn("<Audio 1>", prompt)
        self.assertIn("只生成当前漫画页", prompt)
        self.assertIn("前一页连续性摘要", prompt)
        self.assertNotIn("### Part 1", prompt)
        self.assertEqual(summary, "当前页：国王发现公主失踪并开始寻找。")
        self.assertEqual(len(json.loads(mapping)["pictures"]), 2)

    def test_voice_reference_reuses_one_cached_audio_per_character(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = ComicDatabase(root / "comic2anime.sqlite3")
            database.initialize()
            with database.connect() as connection:
                connection.execute("INSERT INTO projects(project_id) VALUES ('p1')")
                connection.execute(
                    "INSERT INTO characters(character_id, project_id, canonical_name) "
                    "VALUES ('c1', 'p1', 'King')"
                )
                audio_path = root / "king.wav"
                audio_path.write_bytes(b"cached")
                connection.execute(
                    """INSERT INTO character_voice_profiles(
                        project_id, character_id, voice_id, voice_instruction,
                        language, model_name, sample_text, audio_path, sample_rate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("p1", "c1", "voice-1", "warm", "Chinese", "qwen", REFERENCE_TEXT, str(audio_path), 24000),
                )

            def fake_read(*args, **kwargs):
                import numpy as np
                return np.zeros(8, dtype="float32"), 24000

            with patch("create_manga_story.nodes.voice_story_nodes.get_data_root", return_value=root), \
                 patch("soundfile.read", side_effect=fake_read), \
                 patch("create_manga_story.nodes.voice_story_nodes._load_tts_model") as load_model:
                audio, voice_id, manifest_json = ComicCharacterVoiceReference().generate(
                    "p1", "c1", "warm", REFERENCE_TEXT, "Chinese", "unused", "{}", "cpu", False
                )

            self.assertEqual(voice_id, "voice-1")
            self.assertEqual(tuple(audio["waveform"].shape), (1, 1, 8))
            self.assertTrue(json.loads(manifest_json)["reused"])
            load_model.assert_not_called()

    def test_first_page_voice_uses_matched_real_id_even_with_candidate_entity_id(self):
        """A resolver candidate UUID must not suppress the first voice."""
        import torch

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = ComicDatabase(root / "comic2anime.sqlite3")
            database.initialize()
            with database.connect() as connection:
                connection.execute("INSERT INTO projects(project_id) VALUES ('p1')")
                connection.execute(
                    "INSERT INTO characters(character_id, project_id, canonical_name) VALUES ('real-king', 'p1', 'King')"
                )

            fake_audio = {"waveform": torch.zeros((1, 1, 8)), "sample_rate": 24000}
            with patch("create_manga_story.nodes.voice_story_nodes.get_data_root", return_value=root), \
                 patch.object(ComicCharacterVoiceReference, "generate", return_value=(fake_audio, "voice-1", json.dumps({"reused": False}))) as generate:
                audio, manifest_json, report_json = ComicPageVoiceReferences().generate_page_voices(
                    "p1",
                    {
                        "characters": [{
                            "entity_id": "candidate-not-a-db-row",
                            "matched_entity_id": "real-king",
                            "status": "confirmed_match",
                            "source": {"name": "King", "appearance": "crowned man"},
                        }]
                    },
                    "男性声线",
                    REFERENCE_TEXT,
                    "Chinese",
                    "unused",
                    "cpu",
                    False,
                    json.dumps({"results": [{"decision": "PASS", "asset_type": "location"}]}),
                    object(),
                )

            self.assertIsNotNone(audio)
            self.assertEqual(len(json.loads(manifest_json)), 1)
            self.assertEqual(json.loads(manifest_json)[0]["character_id"], "real-king")
            self.assertEqual(json.loads(report_json)["skipped"], [])
            generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()

"""Persist resolved entities, evidence, events, and chapter summaries."""

import json

from ..core.database import ComicDatabase
from ..core.knowledge_updater import update_knowledge_base
from ..core.storage import get_data_root


class ComicKnowledgeBaseUpdater:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "analysis": ("COMIC_ANALYSIS",),
                "entity_resolution": ("COMIC_ENTITY_RESOLUTION",),
            },
        }

    RETURN_TYPES = ("COMIC_KB_UPDATE_REPORT", "STRING")
    RETURN_NAMES = ("update_report", "report_json")
    FUNCTION = "update"
    CATEGORY = "Comic2Anime/Knowledge"

    def update(self, analysis, entity_resolution):
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        report = update_knowledge_base(database, analysis, entity_resolution)
        return report, json.dumps(report, ensure_ascii=False, indent=2)


NODE_CLASS_MAPPINGS = {"ComicKnowledgeBaseUpdater": ComicKnowledgeBaseUpdater}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ComicKnowledgeBaseUpdater": "Comic Knowledge Base Updater"
}

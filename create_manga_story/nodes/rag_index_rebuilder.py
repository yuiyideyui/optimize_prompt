"""Rebuild a project's RAG index from SQLite knowledge records."""

import json

from ..core.database import ComicDatabase
from ..core.rag import rebuild_project_index
from ..core.storage import get_data_root


class ComicKnowledgeIndexRebuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": "project-1"}),
            }
        }

    RETURN_TYPES = ("COMIC_RAG_REBUILD_REPORT", "STRING")
    RETURN_NAMES = ("rebuild_report", "report_json")
    FUNCTION = "rebuild"
    CATEGORY = "Comic2Anime/Knowledge"

    def rebuild(self, project_id: str):
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        count = rebuild_project_index(database, project_id.strip())
        report = {
            "project_id": project_id.strip(),
            "rebuilt_records": count,
            "index_is_rebuildable": True,
        }
        return report, json.dumps(report, ensure_ascii=False, indent=2)


NODE_CLASS_MAPPINGS = {
    "ComicKnowledgeIndexRebuilder": ComicKnowledgeIndexRebuilder
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ComicKnowledgeIndexRebuilder": "Comic Knowledge Index Rebuilder"
}

"""Rebuild a chapter summary without calling Gemini."""

import json

from ..core.chapter_summary import rebuild_chapter_summary
from ..core.database import ComicDatabase
from ..core.storage import get_data_root


class ComicChapterSummarizer:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": "project-1"}),
                "chapter_id": ("STRING", {"default": "chapter-1"}),
            },
            "optional": {
                "update_report": ("COMIC_KB_UPDATE_REPORT",),
            },
        }

    RETURN_TYPES = ("COMIC_CHAPTER_SUMMARY", "STRING")
    RETURN_NAMES = ("chapter_summary", "summary_json")
    FUNCTION = "summarize"
    CATEGORY = "Comic2Anime/Chapter"

    def summarize(self, project_id: str, chapter_id: str, update_report=None):
        if update_report is not None and (
            update_report.get("project_id") != project_id.strip()
            or update_report.get("chapter_id") != chapter_id.strip()
            or update_report.get("idempotent") is not True
        ):
            raise ValueError("Knowledge update report does not match this chapter.")
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        summary = rebuild_chapter_summary(
            database, project_id.strip(), chapter_id.strip()
        )
        return summary, json.dumps(summary, ensure_ascii=False, indent=2)


NODE_CLASS_MAPPINGS = {"ComicChapterSummarizer": ComicChapterSummarizer}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ComicChapterSummarizer": "Comic Chapter Summarizer"
}

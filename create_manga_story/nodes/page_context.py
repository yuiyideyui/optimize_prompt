"""Provide bounded prior-page context and source-linked RAG results."""

import json

from ..core.context_manager import build_page_context
from ..core.database import ComicDatabase
from ..core.storage import get_data_root


class ComicPageContextBuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": "project-1"}),
                "chapter_id": ("STRING", {"default": "chapter-1"}),
                "page_index": ("INT", {"default": 1, "min": 1}),
                "query": ("STRING", {"default": "", "multiline": True}),
                "history_pages": ("INT", {"default": 3, "min": 0, "max": 20}),
                "retrieval_limit": ("INT", {"default": 8, "min": 1, "max": 100}),
                "context_budget": ("INT", {"default": 9000, "min": 1000, "max": 50000}),
            }
        }

    RETURN_TYPES = ("STRING", "COMIC_KNOWLEDGE", "STRING")
    RETURN_NAMES = ("previous_summary", "retrieved_knowledge", "context_json")
    FUNCTION = "build_context"
    CATEGORY = "Comic2Anime/Context"

    def build_context(
        self,
        project_id: str,
        chapter_id: str,
        page_index: int,
        query: str,
        history_pages: int,
        retrieval_limit: int,
        context_budget: int = 9000,
    ):
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        previous, knowledge = build_page_context(
            database,
            project_id,
            chapter_id,
            page_index,
            query,
            history_pages,
            retrieval_limit,
            context_budget,
        )
        return (
            previous,
            knowledge,
            json.dumps(
                {"previous_summary": json.loads(previous), "retrieved_knowledge": knowledge},
                ensure_ascii=False,
                indent=2,
            ),
        )


NODE_CLASS_MAPPINGS = {"ComicPageContextBuilder": ComicPageContextBuilder}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ComicPageContextBuilder": "Comic Page Context Builder"
}

"""Search the rebuildable project RAG index with traceable source links."""

import json

from ..core.database import ComicDatabase
from ..core.rag import query_knowledge
from ..core.storage import get_data_root


class ComicKnowledgeRetriever:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": "project-1"}),
                "entity_type": (
                    [
                        "all",
                        "character",
                        "location",
                        "story_event",
                        "panel",
                        "dialogue",
                        "relationship",
                    ],
                ),
                "query": ("STRING", {"default": "", "multiline": True}),
                "limit": ("INT", {"default": 10, "min": 1, "max": 100}),
            },
            "optional": {
                "chapter_id": ("STRING", {"default": ""}),
                "entity_id": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("COMIC_KNOWLEDGE", "STRING")
    RETURN_NAMES = ("knowledge", "knowledge_json")
    FUNCTION = "retrieve"
    CATEGORY = "Comic2Anime/Knowledge"

    def retrieve(
        self,
        project_id: str,
        entity_type: str,
        query: str,
        limit: int,
        chapter_id: str = "",
        entity_id: str = "",
    ):
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        knowledge = query_knowledge(
            database,
            project_id=project_id.strip(),
            query=query,
            entity_type=entity_type,
            chapter_id=chapter_id,
            limit=limit,
            entity_id=entity_id,
        )
        return (
            knowledge,
            json.dumps(knowledge, ensure_ascii=False, indent=2),
        )


NODE_CLASS_MAPPINGS = {"ComicKnowledgeRetriever": ComicKnowledgeRetriever}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ComicKnowledgeRetriever": "Comic Knowledge Retriever"
}

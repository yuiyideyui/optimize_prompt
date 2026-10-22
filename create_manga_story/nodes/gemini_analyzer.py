"""Analyze one imported page and persist each successful analysis run."""

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

from ..api.gemini_client import PROMPT_VERSION, analyze_image
from ..core.database import ComicDatabase
from ..core.schemas import ANALYSIS_SCHEMA_VERSION
from ..core.storage import get_data_root, safe_component


class GeminiComicPageAnalyzer:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pages": ("COMIC_PAGES",),
                "page_index": ("INT", {"default": 1, "min": 1}),
                "previous_summary": ("STRING", {"default": "", "multiline": True}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": (
                    "STRING",
                    {"default": "https://generativelanguage.googleapis.com"},
                ),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "retrieved_knowledge": ("COMIC_KNOWLEDGE",),
            },
        }

    RETURN_TYPES = ("COMIC_ANALYSIS", "STRING", "STRING")
    RETURN_NAMES = ("analysis", "analysis_json", "saved_path")
    FUNCTION = "analyze_page"
    CATEGORY = "Comic2Anime/Analysis"

    def analyze_page(
        self,
        pages: List[Dict[str, Any]],
        page_index: int,
        previous_summary: str,
        model: str,
        api_base_url: str,
        timeout_seconds: int,
        max_retries: int,
        retrieved_knowledge=None,
    ):
        if not isinstance(pages, list) or not pages:
            raise ValueError("Connect a non-empty COMIC_PAGES list.")
        if page_index < 1 or page_index > len(pages):
            raise ValueError("page_index must select an imported page (1-based).")
        page = pages[page_index - 1]
        for required in ("page_id", "project_id", "chapter_id", "image_path"):
            if not isinstance(page.get(required), str) or not page[required]:
                raise ValueError("Selected page is missing {!r}.".format(required))

        result = analyze_image(
            image_path=Path(page["image_path"]),
            model=model,
            api_key=os.environ.get("GEMINI_API_KEY", ""),
            base_url=api_base_url,
            timeout=timeout_seconds,
            previous_summary=previous_summary,
            max_retries=max_retries,
            retrieved_knowledge=retrieved_knowledge,
        )
        result.update(
            {
                "schema_version": ANALYSIS_SCHEMA_VERSION,
                "page_id": page["page_id"],
                "project_id": page["project_id"],
                "chapter_id": page["chapter_id"],
            }
        )

        root = get_data_root()
        analysis_id = str(uuid.uuid4())
        output_directory = (
            root
            / "analyses"
            / safe_component(page["project_id"])
            / safe_component(page["chapter_id"])
            / safe_component(page["page_id"])
        )
        output_directory.mkdir(parents=True, exist_ok=True)
        result_path = output_directory / "{}.json".format(analysis_id)
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        try:
            with result_path.open("x", encoding="utf-8") as result_file:
                result_file.write(serialized)
            db = ComicDatabase(root / "comic2anime.sqlite3")
            db.initialize()
            db.save_analysis(
                analysis_id=analysis_id,
                page_id=page["page_id"],
                model_name=model,
                prompt_version=PROMPT_VERSION,
                result=result,
                result_path=result_path,
            )
        except Exception:
            if result_path.exists():
                result_path.unlink()
            raise
        return (result, serialized, str(result_path))


NODE_CLASS_MAPPINGS = {"GeminiComicPageAnalyzer": GeminiComicPageAnalyzer}
NODE_DISPLAY_NAME_MAPPINGS = {
    "GeminiComicPageAnalyzer": "Gemini Comic Page Analyzer"
}

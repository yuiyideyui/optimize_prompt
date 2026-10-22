"""Resolve page entities conservatively and expose reviewable candidates."""

import json

from ..core.database import ComicDatabase
from ..core.entity_matching import resolve_analysis_entities
from ..core.storage import get_data_root


class ComicEntityResolver:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "analysis": ("COMIC_ANALYSIS",),
                "review_threshold": ("FLOAT", {"default": 0.35, "min": 0.0, "max": 0.99}),
                "auto_confirm_threshold": (
                    "FLOAT",
                    {"default": 0.9, "min": 0.01, "max": 1.0},
                ),
            },
            "optional": {
                "review_decisions": (
                    "STRING",
                    {"default": "{}", "multiline": True},
                ),
            },
        }

    RETURN_TYPES = ("COMIC_ENTITY_RESOLUTION", "STRING")
    RETURN_NAMES = ("entity_resolution", "resolution_json")
    FUNCTION = "resolve"
    CATEGORY = "Comic2Anime/Entities"

    def resolve(
        self,
        analysis,
        review_threshold: float,
        auto_confirm_threshold: float,
        review_decisions="{}",
    ):
        try:
            decisions = json.loads(review_decisions or "{}")
        except json.JSONDecodeError as error:
            raise ValueError("review_decisions must be valid JSON: {}".format(error)) from error
        if not isinstance(decisions, dict):
            raise ValueError("review_decisions must be a JSON object.")
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        resolution = resolve_analysis_entities(
            database,
            analysis,
            review_threshold=review_threshold,
            auto_confirm_threshold=auto_confirm_threshold,
            review_decisions=decisions,
        )
        return (
            resolution,
            json.dumps(resolution, ensure_ascii=False, indent=2),
        )


NODE_CLASS_MAPPINGS = {"ComicEntityResolver": ComicEntityResolver}
NODE_DISPLAY_NAME_MAPPINGS = {"ComicEntityResolver": "Comic Entity Resolver"}

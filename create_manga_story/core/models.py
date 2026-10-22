"""Shared data types for ComfyUI nodes."""

from typing import Any, Dict, List, TypedDict


class ComicPage(TypedDict):
    page_id: str
    project_id: str
    chapter_id: str
    page_index: int
    source_path: str
    image_path: str
    sha256: str
    width: int
    height: int


class ComicAnalysis(TypedDict, total=False):
    schema_version: str
    page_id: str
    project_id: str
    chapter_id: str
    summary: str
    panels: List[Dict[str, Any]]
    characters: List[Dict[str, Any]]
    locations: List[Dict[str, Any]]
    dialogues: List[Dict[str, Any]]
    story_events: List[Dict[str, Any]]
    character_relationships: List[Dict[str, Any]]
    continuity: List[str]
    uncertainties: List[str]

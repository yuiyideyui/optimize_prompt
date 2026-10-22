"""Construct bounded page context from saved analyses and knowledge retrieval."""

from typing import Any, Dict, List, Tuple

from .database import ComicDatabase
from .rag import query_knowledge


def build_page_context(
    database: ComicDatabase,
    project_id: str,
    chapter_id: str,
    page_index: int,
    query: str,
    history_pages: int = 3,
    retrieval_limit: int = 8,
) -> Tuple[str, Dict[str, Any]]:
    if page_index < 1:
        raise ValueError("page_index must be 1-based and greater than zero.")
    if history_pages < 0 or history_pages > 20:
        raise ValueError("history_pages must be between 0 and 20.")
    with database.connect() as connection:
        page_rows = connection.execute(
            """SELECT page_id, page_index FROM pages
               WHERE project_id = ? AND chapter_id = ? AND page_index < ?
               ORDER BY page_index DESC LIMIT ?""",
            (project_id, chapter_id, page_index, history_pages),
        ).fetchall()
        page_rows = list(reversed(page_rows))
        summaries: List[str] = []
        for page in page_rows:
            analysis = connection.execute(
                """SELECT result_json FROM analysis_runs
                   WHERE page_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (page["page_id"],),
            ).fetchone()
            if analysis is None:
                continue
            import json

            result = json.loads(analysis["result_json"])
            summary = result.get("summary", "")
            if summary:
                summaries.append("Page {}: {}".format(page["page_index"], summary))
    previous_summary = "\n".join(summaries)
    retrieved = query_knowledge(
        database,
        project_id=project_id,
        query=query,
        limit=retrieval_limit,
    )
    return previous_summary, retrieved

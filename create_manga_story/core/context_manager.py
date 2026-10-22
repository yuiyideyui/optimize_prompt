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
    context_budget: int = 9000,
) -> Tuple[str, Dict[str, Any]]:
    if page_index < 1:
        raise ValueError("page_index must be 1-based and greater than zero.")
    if history_pages < 0 or history_pages > 20:
        raise ValueError("history_pages must be between 0 and 20.")
    import json

    with database.connect() as connection:
        target = connection.execute(
            "SELECT chapter_index FROM chapters WHERE project_id=? AND chapter_id=?",
            (project_id, chapter_id),
        ).fetchone()
        if target is None:
            raise ValueError("Chapter is not present in the imported-page database.")
        prior_pages = connection.execute(
            """SELECT p.page_id, p.chapter_id, p.page_index, c.chapter_index
               FROM pages p JOIN chapters c USING(project_id, chapter_id)
               WHERE p.project_id=? AND (c.chapter_index < ? OR
                   (c.chapter_index=? AND p.page_index < ?))
               ORDER BY c.chapter_index DESC, p.page_index DESC LIMIT ?""",
            (project_id, target["chapter_index"], target["chapter_index"], page_index, history_pages),
        ).fetchall()
        prior_pages = list(reversed(prior_pages))
        recent = []
        for page in prior_pages:
            row = connection.execute(
                "SELECT result_json FROM analysis_runs WHERE page_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (page["page_id"],),
            ).fetchone()
            summary = json.loads(row["result_json"]).get("summary", "") if row else ""
            events = connection.execute(
                "SELECT description FROM story_events WHERE page_id=? ORDER BY panel_index,event_id",
                (page["page_id"],),
            ).fetchall()
            recent.append({
                "chapter_id": page["chapter_id"], "page_index": page["page_index"],
                "summary": summary, "events": [event["description"] for event in events],
                "source_page_id": page["page_id"],
            })
        facts = connection.execute(
            """SELECT f.subject,f.attribute,f.value,f.status,f.certainty,f.confidence,
                      f.page_id,p.page_index,p.chapter_id,c.chapter_index
               FROM story_state_facts f JOIN pages p ON p.page_id=f.page_id
               JOIN chapters c ON c.project_id=p.project_id AND c.chapter_id=p.chapter_id
               WHERE f.project_id=? AND f.status IN ('active','uncertain')
                 AND (c.chapter_index < ? OR (c.chapter_index=? AND p.page_index < ?))
               ORDER BY CASE f.status WHEN 'active' THEN 0 ELSE 1 END,
                        c.chapter_index DESC,p.page_index DESC,f.panel_index DESC""",
            (project_id, target["chapter_index"], target["chapter_index"], page_index),
        ).fetchall()
        threads = connection.execute(
            """SELECT t.description,t.thread_type,t.source_page_id,p.page_index,
                      p.chapter_id,t.confidence FROM open_threads t
               JOIN pages p ON p.page_id=t.source_page_id
               WHERE t.project_id=? AND t.status='open'
                 AND (SELECT c.chapter_index FROM chapters c WHERE c.project_id=p.project_id
                      AND c.chapter_id=p.chapter_id) <= ?
               ORDER BY t.confidence DESC,p.created_at DESC""",
            (project_id, target["chapter_index"]),
        ).fetchall()

    memory = {"recent_pages": recent, "current_state": [dict(row) for row in facts],
              "open_threads": [dict(row) for row in threads]}
    # Keep the high-value sections first when trimming to a bounded prompt size.
    selected = {"recent_pages": [], "current_state": [], "open_threads": []}
    for key in ("recent_pages", "current_state", "open_threads"):
        for item in memory[key]:
            candidate = dict(selected)
            candidate[key] = selected[key] + [item]
            if len(json.dumps(candidate, ensure_ascii=False)) > context_budget:
                continue
            selected[key].append(item)
    previous_summary = json.dumps(selected, ensure_ascii=False, indent=2)
    # Preserve the legacy readable page-summary form for existing workflows/tests;
    # structured details remain available in the same JSON memory envelope.
    legacy_lines = [
        "Page {}: {}".format(item["page_index"], item["summary"])
        for item in selected["recent_pages"] if item.get("summary")
    ]
    if legacy_lines:
        selected["legacy_page_summaries"] = legacy_lines
        previous_summary = json.dumps(selected, ensure_ascii=False, indent=2)
    retrieved = query_knowledge(
        database,
        project_id=project_id,
        query=query,
        limit=retrieval_limit,
    )
    return previous_summary, retrieved

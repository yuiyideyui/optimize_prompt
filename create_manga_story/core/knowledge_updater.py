"""Idempotent writes of resolved page facts into the project knowledge base."""

import json
import uuid
from typing import Any, Dict, List, Optional

from .database import ComicDatabase
from .rag import rebuild_project_index
from .entity_matching import normalize_text


_RECORD_NAMESPACE = uuid.UUID("14f7af97-4a9e-44a6-8b55-f56a2665e146")


def _stable_id(*parts: Any) -> str:
    return str(
        uuid.uuid5(
            _RECORD_NAMESPACE,
            "\0".join(str(part or "") for part in parts),
        )
    )


def _merge_text(existing: str, incoming: str) -> str:
    values = list(dict.fromkeys(part.strip() for part in (existing, incoming) if part.strip()))
    return "\n".join(values)


def _resolve_name(
    name: str, resolutions: List[Dict[str, Any]]
) -> Optional[str]:
    normalized = normalize_text(name)
    matches = [
        item["entity_id"]
        for item in resolutions
        if normalized
        and normalized
        in {
            normalize_text(str(item["source"].get("name", ""))),
            normalize_text(str(item["source"].get("identity_candidate", ""))),
        }
    ]
    return matches[0] if len(set(matches)) == 1 else None


def update_knowledge_base(
    database: ComicDatabase,
    analysis: Dict[str, Any],
    resolution: Dict[str, Any],
) -> Dict[str, Any]:
    project_id = analysis.get("project_id")
    chapter_id = analysis.get("chapter_id")
    page_id = analysis.get("page_id")
    if not all(isinstance(value, str) and value for value in (project_id, chapter_id, page_id)):
        raise ValueError("Analysis must contain project_id, chapter_id, and page_id.")
    if any(resolution.get(field) != analysis.get(field) for field in ("project_id", "page_id")):
        raise ValueError("Entity resolution does not belong to this analysis page.")
    if resolution.get("chapter_id", "") not in ("", chapter_id):
        raise ValueError("Entity resolution does not belong to this analysis chapter.")

    characters = resolution.get("characters", [])
    locations = resolution.get("locations", [])
    if len(characters) != len(analysis.get("characters", [])):
        raise ValueError("Character resolution count does not match page analysis.")
    if len(locations) != len(analysis.get("locations", [])):
        raise ValueError("Location resolution count does not match page analysis.")

    pending_matches: List[str] = []
    changed_characters: List[str] = []
    changed_locations: List[str] = []
    with database.connect() as connection:
        page_exists = connection.execute(
            """SELECT 1 FROM pages
               WHERE page_id = ? AND project_id = ? AND chapter_id = ?""",
            (page_id, project_id, chapter_id),
        ).fetchone()
        if page_exists is None:
            raise ValueError("Page must be imported before updating the knowledge base.")
        for entity_type, entries, table, id_column in (
            ("character", characters, "characters", "character_id"),
            ("location", locations, "locations", "location_id"),
        ):
            for index, resolved in enumerate(entries):
                if resolved.get("source_index") != index:
                    raise ValueError("{} resolution order does not match analysis.".format(entity_type))
                if resolved.get("status") not in (
                    "new_candidate",
                    "pending_review",
                    "confirmed_match",
                ):
                    raise ValueError("Unsupported {} resolution status.".format(entity_type))
                entity_id = resolved.get("entity_id")
                matched_id = resolved.get("matched_entity_id", "")
                if not isinstance(entity_id, str) or not entity_id:
                    raise ValueError("{} resolution is missing a stable entity ID.".format(entity_type))
                if resolved["status"] == "confirmed_match" and entity_id != matched_id:
                    raise ValueError("Confirmed {} resolution must use the matched entity ID.".format(entity_type))
                for referenced_id in (entity_id, matched_id):
                    if not referenced_id:
                        continue
                    owner = connection.execute(
                        "SELECT project_id FROM {} WHERE {} = ?".format(table, id_column),
                        (referenced_id,),
                    ).fetchone()
                    if owner is not None and owner["project_id"] != project_id:
                        raise ValueError("{} resolution references another project's entity.".format(entity_type))
                    if referenced_id == matched_id and resolved["status"] != "confirmed_match":
                        if owner is None:
                            raise ValueError("Pending match target does not exist.")

        connection.execute(
            """DELETE FROM character_appearance_evidence
               WHERE page_id = ?""",
            (page_id,),
        )
        connection.execute("DELETE FROM character_appearances WHERE page_id = ?", (page_id,))
        connection.execute("DELETE FROM location_states WHERE page_id = ?", (page_id,))
        connection.execute("DELETE FROM dialogues WHERE page_id = ?", (page_id,))
        connection.execute("DELETE FROM panels WHERE page_id = ?", (page_id,))
        connection.execute(
            """DELETE FROM story_event_participants
               WHERE event_id IN (SELECT event_id FROM story_events WHERE page_id = ?)""",
            (page_id,),
        )
        connection.execute("DELETE FROM story_events WHERE page_id = ?", (page_id,))
        connection.execute("UPDATE story_state_facts SET valid_to_page_id = NULL WHERE valid_to_page_id = ?", (page_id,))
        connection.execute("DELETE FROM story_state_facts WHERE page_id = ?", (page_id,))
        connection.execute("UPDATE open_threads SET status = 'open', resolved_page_id = NULL, resolved_panel_index = NULL WHERE resolved_page_id = ?", (page_id,))
        connection.execute("DELETE FROM open_threads WHERE source_page_id = ?", (page_id,))
        connection.execute(
            "DELETE FROM character_relationships WHERE source_page_id = ?", (page_id,)
        )
        connection.execute("DELETE FROM entity_matches WHERE source_page_id = ?", (page_id,))
        for item, resolved in zip(analysis.get("characters", []), characters):
            entity_id = resolved["entity_id"]
            status = "confirmed" if resolved["status"] == "confirmed_match" else "candidate"
            stable_description = " ".join(
                part
                for part in (
                    str(item.get("identity_candidate", "")),
                    str(item.get("appearance", "")),
                )
                if part
            )
            current = connection.execute(
                "SELECT canonical_name, stable_description, status FROM characters "
                "WHERE character_id = ?",
                (entity_id,),
            ).fetchone()
            if current is None:
                connection.execute(
                    """INSERT INTO characters(
                        character_id, project_id, canonical_name,
                        stable_description, status
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        entity_id,
                        project_id,
                        str(item.get("name", "")).strip(),
                        stable_description,
                        status,
                    ),
                )
            else:
                merged_status = (
                    "confirmed"
                    if current["status"] == "confirmed" or status == "confirmed"
                    else current["status"]
                )
                connection.execute(
                    """UPDATE characters SET
                        canonical_name = CASE WHEN canonical_name = ''
                            THEN ? ELSE canonical_name END,
                        stable_description = ?,
                        status = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE character_id = ?""",
                    (
                        str(item.get("name", "")).strip(),
                        _merge_text(current["stable_description"], stable_description),
                        merged_status,
                        entity_id,
                    ),
                )
            changed_characters.append(entity_id)
            name = str(item.get("name", "")).strip()
            normalized_name = normalize_text(name)
            if normalized_name:
                alias_id = _stable_id("alias", entity_id, normalized_name)
                connection.execute(
                    """INSERT OR IGNORE INTO character_aliases(
                        alias_id, character_id, alias, normalized_alias, source_page_id
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (alias_id, entity_id, name, normalized_name, page_id),
                )
            panel_index = int(item.get("panel_index", -1))
            appearance_id = _stable_id(
                "appearance", entity_id, page_id, panel_index
            )
            connection.execute(
                """INSERT INTO character_appearances(
                    appearance_id, character_id, page_id, panel_index,
                    appearance_description, clothing, expression, action, emotion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(character_id, page_id, panel_index) DO UPDATE SET
                    appearance_description=excluded.appearance_description,
                    clothing=excluded.clothing,
                    expression=excluded.expression,
                    action=excluded.action,
                    emotion=excluded.emotion""",
                (
                    appearance_id,
                    entity_id,
                    page_id,
                    panel_index,
                    str(item.get("appearance", "")),
                    str(item.get("clothing", "")),
                    str(item.get("expression", "")),
                    str(item.get("action", "")),
                    str(item.get("emotion", "")),
                ),
            )
            evidence_id = _stable_id("appearance-evidence", appearance_id)
            connection.execute(
                """INSERT INTO character_appearance_evidence(
                    evidence_id, appearance_id, page_id, panel_index, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(appearance_id, page_id, panel_index) DO UPDATE SET
                    evidence_json=excluded.evidence_json""",
                (
                    evidence_id,
                    appearance_id,
                    page_id,
                    panel_index,
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            if resolved["status"] == "pending_review" and resolved.get("matched_entity_id"):
                match_id = _stable_id(
                    "character-match",
                    entity_id,
                    resolved["matched_entity_id"],
                    page_id,
                )
                connection.execute(
                    """INSERT INTO entity_matches(
                        match_id, project_id, entity_type, candidate_entity_id,
                        matched_entity_id, status, confidence, evidence_json, source_page_id
                    ) VALUES (?, ?, 'character', ?, ?, 'pending_review', ?, ?, ?)
                    ON CONFLICT(entity_type, candidate_entity_id, matched_entity_id,
                        source_page_id) DO UPDATE SET confidence=excluded.confidence,
                        evidence_json=excluded.evidence_json,
                        updated_at=CURRENT_TIMESTAMP""",
                    (
                        match_id,
                        project_id,
                        entity_id,
                        resolved["matched_entity_id"],
                        resolved["confidence"],
                        json.dumps(
                            {"reason": resolved["evidence"], "source": item},
                            ensure_ascii=False,
                        ),
                        page_id,
                    ),
                )
                pending_matches.append(match_id)

        for item, resolved in zip(analysis.get("locations", []), locations):
            location_id = resolved["entity_id"]
            description = str(item.get("description", "")).strip()
            current = connection.execute(
                "SELECT canonical_name, stable_description, status FROM locations "
                "WHERE location_id = ?",
                (location_id,),
            ).fetchone()
            if current is None:
                connection.execute(
                    """INSERT INTO locations(
                        location_id, project_id, canonical_name,
                        stable_description, status
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        location_id,
                        project_id,
                        str(item.get("name", "")).strip(),
                        description,
                        "confirmed" if resolved["status"] == "confirmed_match" else "candidate",
                    ),
                )
            else:
                connection.execute(
                    """UPDATE locations SET
                        canonical_name = CASE WHEN canonical_name = ''
                            THEN ? ELSE canonical_name END,
                        stable_description = ?,
                        updated_at = CURRENT_TIMESTAMP
                       WHERE location_id = ?""",
                    (
                        str(item.get("name", "")).strip(),
                        _merge_text(current["stable_description"], description),
                        location_id,
                    ),
                )
            changed_locations.append(location_id)
            panel_index = int(item.get("panel_index", -1))
            state_id = _stable_id("location-state", location_id, page_id, panel_index)
            connection.execute(
                """INSERT INTO location_states(
                    state_id, location_id, page_id, panel_index, description, visual_state
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, page_id, panel_index) DO UPDATE SET
                    description=excluded.description,
                    visual_state=excluded.visual_state""",
                (
                    state_id,
                    location_id,
                    page_id,
                    panel_index,
                    description,
                    str(item.get("visual_state", "")),
                ),
            )
            if resolved["status"] == "pending_review" and resolved.get("matched_entity_id"):
                match_id = _stable_id(
                    "location-match", location_id, resolved["matched_entity_id"], page_id
                )
                connection.execute(
                    """INSERT INTO entity_matches(
                        match_id, project_id, entity_type, candidate_entity_id,
                        matched_entity_id, status, confidence, evidence_json, source_page_id
                    ) VALUES (?, ?, 'location', ?, ?, 'pending_review', ?, ?, ?)
                    ON CONFLICT(entity_type, candidate_entity_id, matched_entity_id,
                        source_page_id) DO UPDATE SET confidence=excluded.confidence,
                        evidence_json=excluded.evidence_json,
                        updated_at=CURRENT_TIMESTAMP""",
                    (
                        match_id,
                        project_id,
                        location_id,
                        resolved["matched_entity_id"],
                        resolved["confidence"],
                        json.dumps(
                            {"reason": resolved["evidence"], "source": item},
                            ensure_ascii=False,
                        ),
                        page_id,
                    ),
                )
                pending_matches.append(match_id)

        for panel in analysis.get("panels", []):
            panel_index = int(panel.get("panel_index", -1))
            panel_id = _stable_id("panel", page_id, panel_index)
            connection.execute(
                """INSERT INTO panels(
                    panel_id, page_id, panel_index, reading_order, description, confidence
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_id, panel_index) DO UPDATE SET
                    reading_order=excluded.reading_order,
                    description=excluded.description,
                    confidence=excluded.confidence""",
                (
                    panel_id,
                    page_id,
                    panel_index,
                    int(panel.get("reading_order", panel_index)),
                    str(panel.get("description", "")),
                    float(panel.get("confidence", 0)),
                ),
            )
        for dialogue in analysis.get("dialogues", []):
            panel_index = int(dialogue.get("panel_index", -1))
            dialogue_id = _stable_id(
                "dialogue",
                page_id,
                panel_index,
                dialogue.get("speaker", ""),
                dialogue.get("text", ""),
                dialogue.get("kind", ""),
            )
            connection.execute(
                """INSERT INTO dialogues(
                    dialogue_id, page_id, panel_index, speaker, text, kind, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dialogue_id) DO UPDATE SET
                    confidence=excluded.confidence""",
                (
                    dialogue_id,
                    page_id,
                    panel_index,
                    str(dialogue.get("speaker", "")),
                    str(dialogue.get("text", "")),
                    str(dialogue.get("kind", "")),
                    float(dialogue.get("confidence", 0)),
                ),
            )

        for event in analysis.get("story_events", []):
            panel_index = int(event.get("panel_index", -1))
            description = str(event.get("description", "")).strip()
            event_id = _stable_id("event", page_id, panel_index, description)
            connection.execute(
                """INSERT INTO story_events(
                    event_id, project_id, chapter_id, page_id, panel_index,
                    description, participants_json, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_id, panel_index, description) DO UPDATE SET
                    participants_json=excluded.participants_json,
                    confidence=excluded.confidence""",
                (
                    event_id,
                    project_id,
                    chapter_id,
                    page_id,
                    panel_index,
                    description,
                    json.dumps(event.get("participants", []), ensure_ascii=False),
                    float(event.get("confidence", 0)),
                ),
            )
            for participant_name in event.get("participants", []):
                participant_id = _resolve_name(str(participant_name), characters)
                if participant_id:
                    connection.execute(
                        """INSERT OR IGNORE INTO story_event_participants(
                            event_id, character_id, participant_name
                        ) VALUES (?, ?, ?)""",
                        (event_id, participant_id, str(participant_name)),
                    )

        for relationship in analysis.get("character_relationships", []):
            source_id = _resolve_name(relationship.get("source", ""), characters)
            target_id = _resolve_name(relationship.get("target", ""), characters)
            if not source_id or not target_id or source_id == target_id:
                continue
            panel_index = int(relationship.get("panel_index", -1))
            relationship_text = str(relationship.get("relationship", "")).strip()
            relationship_id = _stable_id(
                "relationship",
                source_id,
                target_id,
                relationship_text,
                page_id,
                panel_index,
            )
            connection.execute(
                """INSERT INTO character_relationships(
                    relationship_id, project_id, source_character_id,
                    target_character_id, relationship, status, source_page_id,
                    panel_index, confidence
                ) VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
                ON CONFLICT(source_character_id, target_character_id, relationship,
                    source_page_id, panel_index) DO UPDATE SET
                    confidence=excluded.confidence""",
                (
                    relationship_id,
                    project_id,
                    source_id,
                    target_id,
                    relationship_text,
                    page_id,
                    panel_index,
                    float(relationship.get("confidence", 0)),
                ),
            )

        # Store state history with evidence provenance. Uncertain observations never
        # supersede confirmed facts; only explicit confirmed changes close prior facts.
        for change in analysis.get("state_changes", []):
            subject = str(change.get("subject", "")).strip()
            attribute = str(change.get("attribute", "")).strip()
            value = str(change.get("value", "")).strip()
            certainty = str(change.get("certainty", "uncertain")).strip().lower()
            if not subject or not attribute or not value:
                continue
            confirmed = certainty == "confirmed"
            status = "active" if confirmed else "uncertain"
            panel_index = int(change.get("panel_index", -1))
            fact_id = _stable_id("state-fact", page_id, subject.casefold(), attribute.casefold(), value, panel_index)
            if confirmed:
                connection.execute(
                    """UPDATE story_state_facts SET status = 'superseded', valid_to_page_id = ?
                       WHERE project_id = ? AND subject = ? AND attribute = ?
                         AND status = 'active' AND page_id != ?
                         AND ((SELECT c.chapter_index FROM pages p JOIN chapters c
                               ON c.project_id=p.project_id AND c.chapter_id=p.chapter_id
                               WHERE p.page_id=story_state_facts.page_id) <
                              (SELECT c.chapter_index FROM pages p JOIN chapters c
                               ON c.project_id=p.project_id AND c.chapter_id=p.chapter_id
                               WHERE p.page_id=?) OR
                              ((SELECT c.chapter_index FROM pages p JOIN chapters c
                                ON c.project_id=p.project_id AND c.chapter_id=p.chapter_id
                                WHERE p.page_id=story_state_facts.page_id) =
                               (SELECT c.chapter_index FROM pages p JOIN chapters c
                                ON c.project_id=p.project_id AND c.chapter_id=p.chapter_id
                                WHERE p.page_id=?) AND
                               (SELECT page_index FROM pages WHERE page_id=story_state_facts.page_id) <
                               (SELECT page_index FROM pages WHERE page_id=?)))""",
                    (page_id, project_id, subject, attribute, page_id, page_id, page_id, page_id),
                )
            connection.execute(
                """INSERT INTO story_state_facts(
                    fact_id, project_id, chapter_id, page_id, subject, attribute,
                    value, status, certainty, confidence, panel_index
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fact_id) DO UPDATE SET value=excluded.value,
                    status=excluded.status, certainty=excluded.certainty,
                    confidence=excluded.confidence""",
                (fact_id, project_id, chapter_id, page_id, subject, attribute,
                 value, status, certainty, float(change.get("confidence", 0)), panel_index),
            )

        for thread in analysis.get("new_open_threads", []):
            description = str(thread.get("description", "")).strip()
            if not description:
                continue
            panel_index = int(thread.get("panel_index", -1))
            thread_id = _stable_id("open-thread", project_id, description.casefold(), page_id)
            connection.execute(
                """INSERT INTO open_threads(thread_id, project_id, description, thread_type,
                    status, source_page_id, source_panel_index, confidence)
                   VALUES (?, ?, ?, ?, 'open', ?, ?, ?)
                   ON CONFLICT(project_id, description, source_page_id) DO UPDATE SET
                    thread_type=excluded.thread_type, status='open',
                    source_panel_index=excluded.source_panel_index,
                    confidence=excluded.confidence""",
                (thread_id, project_id, description, str(thread.get("thread_type", "mystery")),
                 page_id, panel_index, float(thread.get("confidence", 0))),
            )
        for resolved in analysis.get("resolved_threads", []):
            description = str(resolved.get("description", "")).strip()
            if description:
                connection.execute(
                    """UPDATE open_threads SET status='resolved', resolved_page_id=?,
                        resolved_panel_index=? WHERE project_id=? AND status='open'
                        AND description=?""",
                    (page_id, int(resolved.get("panel_index", -1)), project_id, description),
                )

    index_records = rebuild_project_index(database, project_id)
    return {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "page_id": page_id,
        "updated_characters": list(dict.fromkeys(changed_characters)),
        "updated_locations": list(dict.fromkeys(changed_locations)),
        "pending_matches": pending_matches,
        "index_records": index_records,
        "idempotent": True,
    }

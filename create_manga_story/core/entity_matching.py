"""Conservative text-based entity matching with explicit review candidates."""

import re
import uuid
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .database import ComicDatabase


_ENTITY_NAMESPACE = uuid.UUID("c79b188a-1a02-4fb6-9011-f1c1e5c2046d")


def normalize_text(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _text_similarity(left: str, right: str) -> float:
    left_normalized = normalize_text(left)
    right_normalized = normalize_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    left_tokens = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]{2}", left.casefold()))
    right_tokens = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]{2}", right.casefold()))
    union = left_tokens | right_tokens
    token_score = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence_score = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    return 0.55 * sequence_score + 0.45 * token_score


def _candidate_id(
    project_id: str, page_id: str, entity_type: str, index: int
) -> str:
    return str(
        uuid.uuid5(
            _ENTITY_NAMESPACE,
            "\0".join((project_id, page_id, entity_type, str(index))),
        )
    )


def _existing_entities(
    database: ComicDatabase,
    project_id: str,
    entity_type: str,
    exclude_page_id: str,
) -> List[Dict[str, Any]]:
    table = "characters" if entity_type == "character" else "locations"
    id_column = "character_id" if entity_type == "character" else "location_id"
    with database.connect() as connection:
        entities = [
            dict(row)
            for row in connection.execute(
                "SELECT {} AS entity_id, canonical_name, stable_description "
                "FROM {} WHERE project_id = ? AND status != 'rejected'".format(
                    id_column, table
                ),
                (project_id,),
            ).fetchall()
        ]
        independent_entities = []
        for entity in entities:
            if entity_type == "character":
                entity["aliases"] = [
                    row["normalized_alias"]
                    for row in connection.execute(
                        """SELECT normalized_alias FROM character_aliases
                           WHERE character_id = ?
                             AND (source_page_id IS NULL OR source_page_id != ?)""",
                        (entity["entity_id"], exclude_page_id),
                    ).fetchall()
                ]
                all_evidence = connection.execute(
                    """SELECT page_id, appearance_description
                       FROM character_appearances
                       WHERE character_id = ?""",
                    (entity["entity_id"],),
                ).fetchall()
            else:
                entity["aliases"] = []
                all_evidence = connection.execute(
                    """SELECT page_id, description FROM location_states
                       WHERE location_id = ?""",
                    (entity["entity_id"],),
                ).fetchall()
            independent_evidence = [
                row for row in all_evidence if row["page_id"] != exclude_page_id
            ]
            if all_evidence and not independent_evidence:
                continue
            entity["stable_description"] = (
                "" if all_evidence else entity.get("stable_description", "")
            )
            description_key = (
                "appearance_description" if entity_type == "character" else "description"
            )
            entity["evidence_descriptions"] = [
                row[description_key]
                for row in independent_evidence
                if row[description_key]
            ]
            independent_entities.append(entity)
    return independent_entities


def _resolve_one(
    item: Dict[str, Any],
    index: int,
    project_id: str,
    page_id: str,
    entity_type: str,
    candidates: Sequence[Dict[str, Any]],
    review_threshold: float,
    auto_confirm_threshold: float,
) -> Dict[str, Any]:
    name = str(item.get("name", "")).strip()
    if entity_type == "character":
        stable_texts = [
            part
            for part in (
                str(item.get("identity_candidate", "")),
                str(item.get("appearance", "")),
            )
            if part
        ]
    else:
        stable_texts = [str(item.get("description", ""))]

    scored: List[Tuple[float, Dict[str, Any], bool]] = []
    normalized_name = normalize_text(name)
    for candidate in candidates:
        if candidate["entity_id"] == _candidate_id(
            project_id, page_id, entity_type, index
        ):
            continue
        name_match = normalized_name and (
            normalized_name == normalize_text(candidate["canonical_name"])
            or normalized_name in candidate.get("aliases", [])
        )
        evidence_texts = [
            candidate.get("stable_description", ""),
            *candidate.get("evidence_descriptions", []),
        ]
        score = max(
            (
                _text_similarity(source_text, evidence_text)
                for source_text in stable_texts
                for evidence_text in evidence_texts
            ),
            default=0.0,
        )
        if name_match or score >= review_threshold:
            scored.append((score, candidate, bool(name_match)))
    scored.sort(key=lambda entry: (-entry[0], entry[1]["entity_id"]))

    proposed_id = _candidate_id(project_id, page_id, entity_type, index)
    if not scored:
        status = "new_candidate"
        matched_id = ""
        confidence = 0.0
        evidence = "No comparable stable identity evidence found."
    else:
        best_score, best, name_match = scored[0]
        runner_up_score = scored[1][0] if len(scored) > 1 else 0.0
        clear_visual_match = (
            name_match
            and best_score >= auto_confirm_threshold
            and best_score - runner_up_score >= 0.15
        )
        if clear_visual_match:
            status = "confirmed_match"
            matched_id = best["entity_id"]
            proposed_id = matched_id
            confidence = round(best_score, 4)
            evidence = "Exact name/alias plus strong, distinct stable-appearance evidence."
        else:
            status = "pending_review"
            matched_id = best["entity_id"]
            confidence = round(max(best_score, 0.5 if name_match else best_score), 4)
            evidence = (
                "Name/alias match requires review; a name alone never confirms identity."
                if name_match
                else "Stable visual evidence is insufficient or ambiguous."
            )
    return {
        "source_index": index,
        "source": item,
        "entity_id": proposed_id,
        "matched_entity_id": matched_id,
        "status": status,
        "confidence": confidence,
        "evidence": evidence,
    }


def resolve_analysis_entities(
    database: ComicDatabase,
    analysis: Dict[str, Any],
    review_threshold: float = 0.35,
    auto_confirm_threshold: float = 0.9,
    review_decisions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not 0.0 <= review_threshold < auto_confirm_threshold <= 1.0:
        raise ValueError("Matching thresholds must satisfy 0 <= review < auto <= 1.")
    project_id = analysis.get("project_id")
    page_id = analysis.get("page_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("Analysis must contain a project_id.")
    if not isinstance(page_id, str) or not page_id.strip():
        raise ValueError("Analysis must contain a page_id.")
    existing_characters = _existing_entities(
        database, project_id, "character", page_id
    )
    existing_locations = _existing_entities(
        database, project_id, "location", page_id
    )
    characters = [
        _resolve_one(
            item,
            index,
            project_id,
            page_id,
            "character",
            existing_characters,
            review_threshold,
            auto_confirm_threshold,
        )
        for index, item in enumerate(analysis.get("characters", []))
    ]
    locations = [
        _resolve_one(
            item,
            index,
            project_id,
            page_id,
            "location",
            existing_locations,
            review_threshold,
            auto_confirm_threshold,
        )
        for index, item in enumerate(analysis.get("locations", []))
    ]
    for entity_type, entries, table, id_column in (
        ("characters", characters, "characters", "character_id"),
        ("locations", locations, "locations", "location_id"),
    ):
        decisions = (review_decisions or {}).get(entity_type, [])
        if (review_decisions or {}).keys() - {"characters", "locations"}:
            raise ValueError("Review decisions only support characters and locations.")
        if not isinstance(decisions, list):
            raise ValueError("Review decisions for {} must be an array.".format(entity_type))
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("Each entity review decision must be an object.")
            index = decision.get("source_index")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(entries):
                raise ValueError("Review decision has an invalid source_index.")
            entry = entries[index]
            action = decision.get("decision")
            if action == "separate":
                entry.update(
                    {
                        "entity_id": _candidate_id(
                            project_id,
                            page_id,
                            entity_type[:-1],
                            index,
                        ),
                        "status": "new_candidate",
                        "matched_entity_id": "",
                        "confidence": 0.0,
                        "evidence": "Manually confirmed as a separate entity.",
                    }
                )
            elif action == "confirm":
                matched_id = decision.get("matched_entity_id")
                if not isinstance(matched_id, str) or not matched_id:
                    raise ValueError("Confirm decisions require matched_entity_id.")
                with database.connect() as connection:
                    target = connection.execute(
                        "SELECT project_id FROM {} WHERE {} = ?".format(table, id_column),
                        (matched_id,),
                    ).fetchone()
                if target is None or target["project_id"] != project_id:
                    raise ValueError(
                        "Manual match target must be an existing entity in this project."
                    )
                entry.update(
                    {
                        "entity_id": matched_id,
                        "matched_entity_id": matched_id,
                        "status": "confirmed_match",
                        "confidence": 1.0,
                        "evidence": "Manually confirmed by the workflow user.",
                    }
                )
            else:
                raise ValueError("Review decision must be 'confirm' or 'separate'.")
    return {
        "project_id": project_id,
        "chapter_id": analysis.get("chapter_id", ""),
        "page_id": page_id,
        "characters": characters,
        "locations": locations,
        "report": {
            "confirmed_matches": sum(
                item["status"] == "confirmed_match"
                for item in characters + locations
            ),
            "new_candidates": sum(
                item["status"] == "new_candidate"
                for item in characters + locations
            ),
            "pending_review": sum(
                item["status"] == "pending_review"
                for item in characters + locations
            ),
        },
    }

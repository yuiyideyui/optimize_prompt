"""Validation and Gemini response schema for page analyses."""

import math
from typing import Any, Dict


ANALYSIS_SCHEMA_VERSION = "1.2"

GEMINI_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "panels": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "panel_index": {"type": "INTEGER"},
                    "reading_order": {"type": "INTEGER"},
                    "description": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["panel_index", "reading_order", "description", "confidence"],
            },
        },
        "characters": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "identity_candidate": {"type": "STRING"},
                    "appearance": {"type": "STRING"},
                    "clothing": {"type": "STRING"},
                    "expression": {"type": "STRING"},
                    "action": {"type": "STRING"},
                    "emotion": {"type": "STRING"},
                    "gender": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                    "panel_index": {"type": "INTEGER"},
                    "crop_box": {
                        "type": "OBJECT",
                        "properties": {
                            "x": {"type": "NUMBER"},
                            "y": {"type": "NUMBER"},
                            "width": {"type": "NUMBER"},
                            "height": {"type": "NUMBER"},
                            "padding": {"type": "NUMBER"},
                        },
                        "required": ["x", "y", "width", "height"],
                    },
                    "reference_quality": {"type": "NUMBER"},
                    "reference_quality_reason": {"type": "STRING"},
                    "same_entity_key": {"type": "STRING"},
                    "is_best_reference": {"type": "BOOLEAN"},
                },
                "required": [
                    "name",
                    "identity_candidate",
                    "appearance",
                    "clothing",
                    "expression",
                    "action",
                    "emotion",
                    "confidence",
                    "panel_index",
                ],
            },
        },
        "locations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "visual_state": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                    "panel_index": {"type": "INTEGER"},
                    "crop_box": {
                        "type": "OBJECT",
                        "properties": {
                            "x": {"type": "NUMBER"},
                            "y": {"type": "NUMBER"},
                            "width": {"type": "NUMBER"},
                            "height": {"type": "NUMBER"},
                            "padding": {"type": "NUMBER"},
                        },
                        "required": ["x", "y", "width", "height"],
                    },
                    "reference_quality": {"type": "NUMBER"},
                    "reference_quality_reason": {"type": "STRING"},
                    "same_location_key": {"type": "STRING"},
                    "is_best_reference": {"type": "BOOLEAN"},
                    "scene_scope": {"type": "STRING"},
                },
                "required": [
                    "name",
                    "description",
                    "visual_state",
                    "confidence",
                    "panel_index",
                ],
            },
        },
        "dialogues": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "speaker": {"type": "STRING"},
                    "text": {"type": "STRING"},
                    "kind": {"type": "STRING"},
                    "panel_index": {"type": "INTEGER"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["speaker", "text", "kind", "panel_index", "confidence"],
            },
        },
        "story_events": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "description": {"type": "STRING"},
                    "participants": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "panel_index": {"type": "INTEGER"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["description", "participants", "panel_index", "confidence"],
            },
        },
        "character_relationships": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "source": {"type": "STRING"},
                    "target": {"type": "STRING"},
                    "relationship": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                    "panel_index": {"type": "INTEGER"},
                },
                "required": [
                    "source",
                    "target",
                    "relationship",
                    "confidence",
                    "panel_index",
                ],
            },
        },
        "continuity": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "state_changes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "subject": {"type": "STRING"},
                    "attribute": {"type": "STRING"},
                    "value": {"type": "STRING"},
                    "certainty": {"type": "STRING"},
                    "panel_index": {"type": "INTEGER"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["subject", "attribute", "value", "certainty", "panel_index", "confidence"],
            },
        },
        "new_open_threads": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "description": {"type": "STRING"},
                    "thread_type": {"type": "STRING"},
                    "panel_index": {"type": "INTEGER"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["description", "thread_type", "panel_index", "confidence"],
            },
        },
        "resolved_threads": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "description": {"type": "STRING"},
                    "panel_index": {"type": "INTEGER"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["description", "panel_index", "confidence"],
            },
        },
    },
    "required": [
        "summary",
        "panels",
        "characters",
        "locations",
        "dialogues",
        "story_events",
        "character_relationships",
        "continuity",
        "uncertainties",
    ],
}

def validate_analysis(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        for field in ("state_changes", "new_open_threads", "resolved_threads"):
            value.setdefault(field, [])
    _validate_value(value, GEMINI_RESPONSE_SCHEMA, "analysis")
    return value


def _validate_value(value: Any, schema: Dict[str, Any], path: str) -> None:
    expected = schema["type"]
    type_checks = {
        "OBJECT": lambda item: isinstance(item, dict),
        "ARRAY": lambda item: isinstance(item, list),
        "STRING": lambda item: isinstance(item, str),
        "BOOLEAN": lambda item: isinstance(item, bool),
        "INTEGER": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "NUMBER": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
    }
    if not type_checks[expected](value):
        raise ValueError("{} must be {}.".format(path, expected.lower()))
    if expected == "OBJECT":
        properties = schema["properties"]
        required = schema.get("required", [])
        missing = [field for field in required if field not in value]
        unknown = [field for field in value if field not in properties]
        if missing:
            raise ValueError("{} is missing required fields: {}.".format(path, ", ".join(missing)))
        if unknown:
            raise ValueError("{} contains unsupported fields: {}.".format(path, ", ".join(unknown)))
        for field, child_schema in properties.items():
            if field in value:
                _validate_value(value[field], child_schema, "{}.{}".format(path, field))
    elif expected == "ARRAY":
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], "{}[{}]".format(path, index))
    elif expected == "NUMBER" and not math.isfinite(value):
        raise ValueError("{} must be a finite number.".format(path))

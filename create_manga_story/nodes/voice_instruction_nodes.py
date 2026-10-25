"""Small workflow node for page-local character voice direction."""

import json
from typing import Any, Dict


def _obj(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("entity_resolution must be an object")
    return parsed


class ComicPageVoiceInstructionBuilder:
    """Build one voice instruction string from the current page entities."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "entity_resolution": ("COMIC_ENTITY_RESOLUTION",),
                "base_instruction": ("STRING", {"multiline": True, "default": "自然、清晰、稳定的动漫角色参考音色。"}),
            },
            "optional": {
                "asset_generation_report": ("STRING", {"multiline": True, "default": "{}"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("voice_instruction",)
    FUNCTION = "build"
    CATEGORY = "Comic2Anime/Voice"

    def build(self, entity_resolution, base_instruction, asset_generation_report="{}"):
        resolution = _obj(entity_resolution)
        entries = resolution.get("characters", [])
        lines = [
            (base_instruction or "").strip(),
            "当前页角色声音必须按角色资料确定性别、年龄和身份；禁止所有角色使用同一种默认声线。",
            "角色参考图已由当前页自动资产生成流程先行完成；声音只绑定真实 character_id，并复用已有声音。",
        ]
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
            character_id = entry.get("matched_entity_id") or entry.get("entity_id") or "未解析角色"
            name = source.get("name") or entry.get("name") or character_id
            gender = source.get("gender") or entry.get("gender") or source.get("sex") or entry.get("sex")
            gender_text = str(gender).strip() if gender else "未明确；依据外观和身份保守判断"
            lines.append("character_id={}（{}）：{}。".format(character_id, name, gender_text))
        return ("\n".join(line for line in lines if line),)


NODE_CLASS_MAPPINGS = {"ComicPageVoiceInstructionBuilder": ComicPageVoiceInstructionBuilder}
NODE_DISPLAY_NAME_MAPPINGS = {"ComicPageVoiceInstructionBuilder": "Comic2Anime Current Page Voice Instruction"}

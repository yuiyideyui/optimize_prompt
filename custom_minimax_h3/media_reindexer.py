"""Media tag extraction, reindexing, and prompt rewriting utilities for MiniMax H3."""

import re
from typing import Dict, Set


# 匹配各种媒体标签格式：<Picture 1>、<Image 2>、Picture#3、Image 1 等
MEDIA_TAG_RE = re.compile(
    r'<?\s*(picture|image|audio|video)\s*[#\s]*(\d+)\s*>?',
    re.IGNORECASE
)


def extract_used_media_indices(prompt: str) -> Dict[str, Set[int]]:
    """
    从 prompt 中提取所有使用的媒体标签序号。

    返回格式：
    {
        "pictures": {1, 3, 5},
        "audios": {1, 2},
        "videos": {1}
    }
    """
    result = {
        "pictures": set(),
        "audios": set(),
        "videos": set(),
    }

    for match in MEDIA_TAG_RE.finditer(prompt):
        media_type = match.group(1).lower()
        ordinal = int(match.group(2))

        # 统一 "image" 为 "pictures"
        if media_type == "image":
            media_type = "pictures"

        if media_type in result:
            result[media_type].add(ordinal)

    return result


def create_reindex_mapping(used_indices: Dict[str, Set[int]]) -> Dict[str, Dict[int, int]]:
    """
    为每种媒体类型创建旧序号到新序号的映射。

    例如：
    输入：{"pictures": {1, 3, 5}, "audios": {1, 2}}
    输出：{"pictures": {1: 1, 3: 2, 5: 3}, "audios": {1: 1, 2: 2}}
    """
    mapping = {}

    for media_type, indices in used_indices.items():
        if indices:
            sorted_indices = sorted(indices)
            mapping[media_type] = {old: new for new, old in enumerate(sorted_indices, 1)}
        else:
            mapping[media_type] = {}

    return mapping


def reindex_prompt(prompt: str, reindex_mapping: Dict[str, Dict[int, int]]) -> str:
    """
    根据重索引映射重写 prompt 中的媒体标签。

    例如：
    输入：prompt="<picture 1> with <picture 3> and <audio 2>"
    输出：prompt="<picture 1> with <picture 2> and <audio 1>"
    """
    def replace_tag(match):
        media_type = match.group(1).lower()
        ordinal = int(match.group(2))

        # 统一 "image" 为 "pictures"
        if media_type == "image":
            media_type = "pictures"

        # 获取重索引后的序号
        if media_type in reindex_mapping:
            new_ordinal = reindex_mapping[media_type].get(ordinal)
            if new_ordinal is not None:
                # 保持原始格式（<Type Ordinal> 或其他变体）
                original = match.group(0)
                if original.startswith("<"):
                    return f"<{media_type.capitalize()} {new_ordinal}>"
                else:
                    return f"{media_type.capitalize()} {new_ordinal}"

        # 如果没有映射，返回原始文本（表示此媒体被过滤掉）
        return match.group(0)

    return MEDIA_TAG_RE.sub(replace_tag, prompt)


def filter_and_reindex_batch(
    batch_dict: Dict[str, any],
    used_indices: Set[int],
    prefix: str = "ref_image"
) -> tuple[Dict[str, any], Dict[int, int]]:
    """
    过滤批次字典并重新编号。

    参数：
    - batch_dict: 原始批次字典，键格式为 "prefix_N"（如 "ref_image_1"）
    - used_indices: 要保留的序号集合（如 {1, 3, 5}）
    - prefix: 键前缀（如 "ref_image"、"ref_audio"）

    返回：
    - 重新编号的批次字典
    - 旧序号到新序号的映射
    """
    if not batch_dict:
        return {}, {}

    # 提取所有存在的序号和值
    items = []
    for key, value in batch_dict.items():
        if value is None:
            continue
        try:
            ordinal = int(str(key).rsplit("_", 1)[-1])
            if ordinal in used_indices:
                items.append((ordinal, value))
        except (ValueError, IndexError):
            continue

    # 按序号排序
    items.sort(key=lambda x: x[0])

    # 创建映射并重新编号
    reindexed = {}
    mapping = {}
    for new_ordinal, (old_ordinal, value) in enumerate(items, 1):
        reindexed[f"{prefix}_{new_ordinal}"] = value
        mapping[old_ordinal] = new_ordinal

    return reindexed, mapping


def update_letterbox_metadata(
    metadata_json: str,
    picture_reindex_mapping: Dict[int, int]
) -> tuple[str, bool]:
    """
    根据图片重索引映射更新 letterbox 元数据。

    参数：
    - metadata_json: 来自 Director Table 的 JSON 字符串
    - picture_reindex_mapping: 图片重索引映射（旧序号 → 新序号）

    返回：
    - 更新后的 metadata_json
    - 是否成功更新
    """
    import json

    try:
        data = json.loads(metadata_json)
    except (json.JSONDecodeError, TypeError):
        return metadata_json, False

    if not isinstance(data, dict) or data.get("version") != 1:
        return metadata_json, False

    items = data.get("items", [])
    if not isinstance(items, list):
        return metadata_json, False

    # 按原始索引创建临时映射：原始 index → item
    items_by_index = {item.get("index"): item for item in items if isinstance(item, dict)}

    # 创建新的 items 列表
    new_items = []
    for old_index in sorted(picture_reindex_mapping.keys()):
        new_index = picture_reindex_mapping[old_index]
        old_item = items_by_index.get(old_index)

        if old_item:
            new_item = dict(old_item)
            new_item["index"] = new_index
            new_items.append(new_item)

    # 更新元数据
    data["items"] = new_items

    try:
        return json.dumps(data, ensure_ascii=False), True
    except (TypeError, ValueError):
        return metadata_json, False

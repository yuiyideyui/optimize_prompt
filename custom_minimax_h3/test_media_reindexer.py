#!/usr/bin/env python3
"""Quick tests for media_reindexer module."""

from media_reindexer import (
    extract_used_media_indices,
    create_reindex_mapping,
    reindex_prompt,
    filter_and_reindex_batch,
    update_letterbox_metadata,
)
import json


def test_extract_used_media_indices():
    """Test media index extraction from prompts."""
    print("Testing extract_used_media_indices...")

    # Test 1: Mixed formats
    prompt = "A character based on <picture 1>, with <image 3> and <Audio 2>"
    result = extract_used_media_indices(prompt)
    assert result["pictures"] == {1, 3}, f"Expected {{1, 3}}, got {result['pictures']}"
    assert result["audios"] == {2}, f"Expected {{2}}, got {result['audios']}"

    # Test 2: No media tags
    prompt = "Just plain text"
    result = extract_used_media_indices(prompt)
    assert result["pictures"] == set()
    assert result["audios"] == set()

    # Test 3: Duplicate tags
    prompt = "<picture 1> and <picture 1>"
    result = extract_used_media_indices(prompt)
    assert result["pictures"] == {1}, f"Expected {{1}}, got {result['pictures']}"

    print("✓ extract_used_media_indices tests passed")


def test_create_reindex_mapping():
    """Test reindex mapping creation."""
    print("Testing create_reindex_mapping...")

    used_indices = {
        "pictures": {1, 3, 5},
        "audios": {1, 2},
        "videos": set()
    }

    mapping = create_reindex_mapping(used_indices)

    assert mapping["pictures"] == {1: 1, 3: 2, 5: 3}, f"Pictures mapping incorrect: {mapping['pictures']}"
    assert mapping["audios"] == {1: 1, 2: 2}, f"Audios mapping incorrect: {mapping['audios']}"
    assert mapping["videos"] == {}, f"Videos mapping should be empty: {mapping['videos']}"

    print("✓ create_reindex_mapping tests passed")


def test_reindex_prompt():
    """Test prompt rewriting."""
    print("Testing reindex_prompt...")

    mapping = {
        "pictures": {1: 1, 3: 2},
        "audios": {2: 1},
        "videos": {}
    }

    prompt = "A character based on <picture 1>, with <picture 3> and <audio 2>"
    result = reindex_prompt(prompt, mapping)

    expected = "A character based on <Picture 1>, with <Picture 2> and <Audio 1>"
    assert result == expected, f"Expected '{expected}', got '{result}'"

    print("✓ reindex_prompt tests passed")


def test_filter_and_reindex_batch():
    """Test batch filtering and reindexing."""
    print("Testing filter_and_reindex_batch...")

    batch = {
        "ref_image_1": "img1",
        "ref_image_2": "img2",
        "ref_image_3": "img3",
    }

    used_indices = {1, 3}
    reindexed, mapping = filter_and_reindex_batch(batch, used_indices, "ref_image")

    assert reindexed == {"ref_image_1": "img1", "ref_image_2": "img3"}, f"Reindexed batch incorrect: {reindexed}"
    assert mapping == {1: 1, 3: 2}, f"Mapping incorrect: {mapping}"

    print("✓ filter_and_reindex_batch tests passed")


def test_update_letterbox_metadata():
    """Test letterbox metadata update."""
    print("Testing update_letterbox_metadata...")

    metadata = {
        "version": 1,
        "canvas": [1024, 576],
        "items": [
            {"index": 1, "top": 0, "left": 0, "height": 576, "width": 1024},
            {"index": 2, "top": 50, "left": 100, "height": 400, "width": 800},
            {"index": 3, "top": 100, "left": 200, "height": 300, "width": 600},
        ]
    }

    metadata_json = json.dumps(metadata)
    picture_mapping = {1: 1, 3: 2}  # Keep items 1 and 3, renumber to 1 and 2

    updated_json, success = update_letterbox_metadata(metadata_json, picture_mapping)

    assert success, "Metadata update failed"
    updated = json.loads(updated_json)

    assert len(updated["items"]) == 2, f"Expected 2 items, got {len(updated['items'])}"
    assert updated["items"][0]["index"] == 1, "First item index should be 1"
    assert updated["items"][1]["index"] == 2, "Second item index should be 2"
    assert updated["items"][0]["width"] == 1024, "First item content should match original item 1"
    assert updated["items"][1]["width"] == 600, "Second item content should match original item 3"

    print("✓ update_letterbox_metadata tests passed")


if __name__ == "__main__":
    try:
        test_extract_used_media_indices()
        test_create_reindex_mapping()
        test_reindex_prompt()
        test_filter_and_reindex_batch()
        test_update_letterbox_metadata()
        print("\n✅ All tests passed!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

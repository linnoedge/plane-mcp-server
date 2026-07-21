"""Tests for self-host compatibility helpers."""

from plane_mcp.tools.work_items import _count_items, _id_list, _work_item_list_payload


def test_id_list_accepts_uuid_strings_and_objects():
    class User:
        id = "user-2"

    assert _id_list(["user-1", {"id": "user-3"}, User(), {"name": "missing"}]) == [
        "user-1",
        "user-3",
        "user-2",
    ]


def test_count_items_groups_scalar_and_multi_value_fields():
    items = [
        {"priority": "urgent", "labels": ["label-1", "label-2"]},
        {"priority": "none", "labels": ["label-1"]},
        {"priority": "urgent", "labels": []},
    ]

    result = _count_items(items, "labels__id", "priority")

    assert result["total_count"] == 3
    assert result["grouped_counts"]["label-1"]["count"] == 2
    assert result["grouped_counts"]["label-1"]["sub_grouped_counts"]["urgent"] == {"count": 1}
    assert result["grouped_counts"]["label-1"]["sub_grouped_counts"]["none"] == {"count": 1}
    assert result["grouped_counts"]["None"]["count"] == 1


def test_work_item_list_payload_wraps_single_external_filter_match():
    item = {"id": "issue-1", "name": "External match"}

    result = _work_item_list_payload(item)

    assert result["results"] == [item]
    assert result["total_count"] == 1
    assert result["count"] == 1
    assert result["next_page_results"] is False

"""Tests for self-host compatibility helpers."""

from plane_mcp.tools.work_items import _count_items, _filter_items_from_pages, _id_list, _work_item_list_payload


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


def test_work_item_list_payload_preserves_expanded_parent_objects():
    response = {
        "results": [
            {"id": "issue-1", "parent": {}},
            {"id": "issue-2", "parent": {"id": "parent-1", "name": "Parent"}},
        ],
        "total_count": 2,
        "count": 2,
    }

    result = _work_item_list_payload(response)

    assert result["results"][0]["parent"] == {}
    assert result["results"][1]["parent"] == {"id": "parent-1", "name": "Parent"}


def test_filter_items_from_pages_scans_across_cursors_until_limit():
    pages = [
        {
            "results": [
                {"id": "issue-1", "priority": "low", "state": {"id": "todo", "group": "unstarted"}},
            ],
            "next_cursor": "page-2",
            "next_page_results": True,
            "total_count": 3,
        },
        {
            "results": [
                {"id": "issue-2", "priority": "high", "state": {"id": "done", "group": "completed"}},
                {"id": "issue-3", "priority": "high", "state": {"id": "started", "group": "started"}},
            ],
            "next_cursor": "page-3",
            "next_page_results": True,
            "total_count": 3,
        },
        {
            "results": [
                {"id": "issue-4", "priority": "high", "state": {"id": "backlog", "group": "backlog"}},
            ],
            "next_cursor": "",
            "next_page_results": False,
            "total_count": 4,
        },
    ]
    requested_cursors = []

    def fetch_page(cursor):
        requested_cursors.append(cursor)
        return pages[len(requested_cursors) - 1]

    result = _filter_items_from_pages(
        fetch_page=fetch_page,
        priority="high",
        state_group="started",
        state_id=None,
        assignee_id=None,
        label_id=None,
        limit=1,
        max_pages=10,
    )

    assert [item["id"] for item in result["results"]] == ["issue-3"]
    assert requested_cursors == [None, "page-2"]
    assert result["pages_scanned"] == 2
    assert result["total_scanned"] == 3
    assert result["next_cursor"] == "page-3"
    assert result["has_more"] is True

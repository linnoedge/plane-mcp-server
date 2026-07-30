"""Tests for self-host compatibility helpers."""

from types import SimpleNamespace

import plane_mcp.tools.work_items as work_items_module
from plane_mcp.tools.work_items import (
    _count_items,
    _filter_items_from_pages,
    _id_list,
    _work_item_list_payload,
    _work_item_scan_record,
    register_work_item_tools,
)


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function

        return register


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


def test_work_item_scan_record_keeps_only_filterable_scalar_data():
    item = {
        "id": "issue-1",
        "name": "Large item",
        "sequence_id": 42,
        "priority": "high",
        "state": {"id": "state-1", "group": "started", "name": "Started", "color": "#fff"},
        "assignees": [{"id": "user-1", "display_name": "User"}],
        "labels": [{"id": "label-1", "name": "Bug"}],
        "description_html": "x" * 100_000,
        "project": {"id": "project-1", "name": "Project"},
        "parent": {"id": "parent-1", "description_html": "y" * 100_000},
    }

    assert _work_item_scan_record(item) == {
        "id": "issue-1",
        "name": "Large item",
        "sequence_id": 42,
        "priority": "high",
        "state_id": "state-1",
        "state_group": "started",
        "assignee_ids": ["user-1"],
        "label_ids": ["label-1"],
    }


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


def test_filter_work_items_fetches_minimal_fields_without_expansion(monkeypatch, tmp_path):
    monkeypatch.setenv("PLANE_MCP_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    registry = ToolRegistry()
    register_work_item_tools(registry)
    captured = {}

    class WorkItems:
        def _get(self, path, params):
            captured["path"] = path
            captured["params"] = params
            return {
                "results": [
                    {
                        "id": "issue-1",
                        "name": "Match",
                        "sequence_id": 1,
                        "priority": "high",
                        "state": "state-1",
                        "assignees": ["user-1"],
                        "labels": ["label-1"],
                        "updated_at": "2026-07-30T10:00:00Z",
                        "description_html": "x" * 100_000,
                    }
                ],
                "next_page_results": False,
            }

    monkeypatch.setattr(
        work_items_module,
        "get_plane_client_context",
        lambda: (SimpleNamespace(work_items=WorkItems()), "workspace"),
    )

    result = registry.tools["filter_work_items"](
        "project",
        priority="high",
        state_id="state-1",
        assignee_id="user-1",
        label_id="label-1",
    )

    assert captured == {
        "path": "workspace/projects/project/work-items/",
        "params": {
            "per_page": 50,
            "order_by": "-updated_at",
            "expand": "state",
            "fields": "id,name,sequence_id,priority,state,assignees,labels,updated_at",
        },
    }
    assert result["results"] == [
        {
            "id": "issue-1",
            "name": "Match",
            "sequence_id": 1,
            "priority": "high",
            "state_id": "state-1",
            "state_group": None,
            "assignee_ids": ["user-1"],
            "label_ids": ["label-1"],
            "updated_at": "2026-07-30T10:00:00Z",
        }
    ]


def test_list_work_items_remains_backward_compatible(monkeypatch):
    registry = ToolRegistry()
    register_work_item_tools(registry)
    captured = {}

    class WorkItems:
        def _get(self, path, params):
            captured["params"] = params
            return {"results": []}

    monkeypatch.setattr(
        work_items_module,
        "get_plane_client_context",
        lambda: (SimpleNamespace(work_items=WorkItems()), "workspace"),
    )

    registry.tools["list_work_items"]("project", per_page=10, cursor="cursor-2")

    assert captured["params"] == {"per_page": 10, "cursor": "cursor-2"}

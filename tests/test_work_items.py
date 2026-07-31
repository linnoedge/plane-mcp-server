"""Tests for self-host compatibility helpers."""

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

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


def test_filter_work_items_forwards_comprehensive_filters_and_uses_complete_projection(monkeypatch):
    registry = ToolRegistry()
    register_work_item_tools(registry)
    captured = {}

    class WorkItems:
        def _get(self, path, params):
            captured["path"] = path
            captured["params"] = params
            return {"results": [], "next_page_results": False}

    class Cache:
        def filter_items(self, **kwargs):
            captured["filters"] = kwargs
            return {"results": [{"id": "issue-1"}], "total_count": 7}

    monkeypatch.setattr(
        work_items_module,
        "get_plane_client_context",
        lambda: (SimpleNamespace(work_items=WorkItems()), "workspace"),
    )
    monkeypatch.setattr(work_items_module, "get_plane_cache_scope", lambda: "scope")
    monkeypatch.setattr(work_items_module, "WorkItemCache", Cache)
    monkeypatch.setattr(work_items_module, "sync_work_items", lambda **kwargs: kwargs["fetch_page"](None) or {})

    result = registry.tools["filter_work_items"](
        "project",
        priority="high",
        state_id="state-1",
        state_group="started",
        assignee_id="user-1",
        label_id="label-1",
        query="needle",
        priorities=["high", "urgent"],
        state_ids=["state-2"],
        state_groups=["backlog"],
        assignee_ids=["user-2"],
        label_ids=["label-2"],
        relation_match="all",
        type_id="type-1",
        parent_id="parent-1",
        cycle_id="cycle-1",
        module_id="module-1",
        created_by="creator-1",
        created_at_from="2026-01-01T00:00:00Z",
        created_at_to="2026-02-01T00:00:00Z",
        updated_at_from="2026-03-01T00:00:00Z",
        updated_at_to="2026-04-01T00:00:00Z",
        start_date_from="2026-01-01",
        start_date_to="2026-01-31",
        target_date_from="2026-02-01",
        target_date_to="2026-02-28",
        completed_at_from="2026-05-01T00:00:00Z",
        completed_at_to="2026-06-01T00:00:00Z",
        sequence_id_from=10,
        sequence_id_to=20,
        is_draft=False,
        has_assignee=True,
        has_label=False,
        has_parent=True,
        overdue=False,
        sort_by="sequence_id",
        sort_direction="asc",
        offset=5,
        limit=10,
    )

    assert captured["path"] == "workspace/projects/project/work-items/"
    assert captured["params"] == {
        "per_page": 50,
        "order_by": "-updated_at",
        "expand": "state",
        "fields": (
            "id,name,sequence_id,priority,state,assignees,labels,type_id,parent,cycle,modules,created_by,"
            "created_at,updated_at,start_date,target_date,completed_at,is_draft"
        ),
    }
    assert captured["filters"] == {
        "server": "https://api.plane.so",
        "workspace": "workspace:scope",
        "project_id": "project",
        "priority": "high",
        "state_id": "state-1",
        "state_group": "started",
        "assignee_id": "user-1",
        "label_id": "label-1",
        "query": "needle",
        "priorities": ["high", "urgent"],
        "state_ids": ["state-2"],
        "state_groups": ["backlog"],
        "assignee_ids": ["user-2"],
        "label_ids": ["label-2"],
        "relation_match": "all",
        "type_id": "type-1",
        "parent_id": "parent-1",
        "cycle_id": "cycle-1",
        "module_id": "module-1",
        "created_by": "creator-1",
        "created_at_from": "2026-01-01T00:00:00Z",
        "created_at_to": "2026-02-01T00:00:00Z",
        "updated_at_from": "2026-03-01T00:00:00Z",
        "updated_at_to": "2026-04-01T00:00:00Z",
        "start_date_from": "2026-01-01",
        "start_date_to": "2026-01-31",
        "target_date_from": "2026-02-01",
        "target_date_to": "2026-02-28",
        "completed_at_from": "2026-05-01T00:00:00Z",
        "completed_at_to": "2026-06-01T00:00:00Z",
        "sequence_id_from": 10,
        "sequence_id_to": 20,
        "is_draft": False,
        "has_assignee": True,
        "has_label": False,
        "has_parent": True,
        "overdue": False,
        "sort_by": "sequence_id",
        "sort_direction": "asc",
        "offset": 5,
        "limit": 10,
    }
    assert result["results"] == [{"id": "issue-1"}]
    assert result["count"] == 1
    assert result["total_count"] == 7


def test_filter_work_items_schema_constrains_enums_and_pagination():
    mcp = FastMCP("test")
    register_work_item_tools(mcp)

    schema = asyncio.run(mcp.get_tool("filter_work_items")).parameters["properties"]

    assert schema["priority"]["anyOf"][0]["enum"] == ["urgent", "high", "medium", "low", "none"]
    assert schema["state_group"]["anyOf"][0]["enum"] == [
        "backlog",
        "unstarted",
        "started",
        "completed",
        "cancelled",
    ]
    assert schema["relation_match"]["anyOf"][0]["enum"] == ["any", "all"]
    assert schema["relation_match"]["anyOf"][1] == {"type": "null"}
    assert schema["sort_by"]["enum"] == [
        "updated_at",
        "created_at",
        "sequence_id",
        "priority",
        "start_date",
        "target_date",
        "name",
    ]
    assert schema["sort_direction"]["enum"] == ["asc", "desc"]
    assert schema["offset"]["minimum"] == 0
    assert schema["limit"]["minimum"] == 1
    assert schema["limit"]["maximum"] == 100
    assert schema["per_page"]["minimum"] == 1
    assert schema["per_page"]["maximum"] == 100
    assert schema["max_pages"]["minimum"] == 1
    assert schema["max_pages"]["maximum"] == 100


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0}, "limit must be between 1 and 100"),
        ({"limit": 101}, "limit must be between 1 and 100"),
        ({"offset": -1}, "offset must be non-negative"),
        ({"per_page": 0}, "per_page must be between 1 and 100"),
        ({"per_page": 101}, "per_page must be between 1 and 100"),
        ({"max_pages": 0}, "max_pages must be between 1 and 100"),
        ({"max_pages": 101}, "max_pages must be between 1 and 100"),
    ],
)
def test_filter_work_items_rejects_invalid_pagination_before_sync(monkeypatch, kwargs, message):
    registry = ToolRegistry()
    register_work_item_tools(registry)
    monkeypatch.setattr(
        work_items_module,
        "get_plane_client_context",
        lambda: pytest.fail("invalid controls must be rejected before client access"),
    )

    with pytest.raises(ValueError, match=message):
        registry.tools["filter_work_items"]("project", **kwargs)


def test_filter_work_items_returns_sync_and_generic_filter_note(monkeypatch):
    registry = ToolRegistry()
    register_work_item_tools(registry)
    sync = {"status": "partial", "pages_fetched": 2, "items_upserted": 4, "watermark": "2026-07-31T00:00:00Z"}

    class Cache:
        def filter_items(self, **kwargs):
            return {"results": [{"id": "issue-1"}], "total_count": 3}

    monkeypatch.setattr(
        work_items_module,
        "get_plane_client_context",
        lambda: (SimpleNamespace(work_items=SimpleNamespace(_get=lambda path, params: {"results": []})), "workspace"),
    )
    monkeypatch.setattr(work_items_module, "WorkItemCache", Cache)
    monkeypatch.setattr(work_items_module, "sync_work_items", lambda **kwargs: sync)

    result = registry.tools["filter_work_items"]("project")

    assert result == {
        "results": [{"id": "issue-1"}],
        "count": 1,
        "total_count": 3,
        "sync": sync,
        "filter_note": "Work items were filtered from the shared incremental SQLite cache.",
    }


def test_filter_work_items_normalizes_explicit_null_relation_match(monkeypatch):
    registry = ToolRegistry()
    register_work_item_tools(registry)
    captured = {}

    class Cache:
        def filter_items(self, **kwargs):
            captured.update(kwargs)
            return {"results": [], "total_count": 0}

    monkeypatch.setattr(
        work_items_module,
        "get_plane_client_context",
        lambda: (SimpleNamespace(work_items=SimpleNamespace(_get=lambda path, params: {"results": []})), "workspace"),
    )
    monkeypatch.setattr(work_items_module, "WorkItemCache", Cache)
    monkeypatch.setattr(work_items_module, "sync_work_items", lambda **kwargs: {})

    registry.tools["filter_work_items"]("project", relation_match=None)

    assert captured["relation_match"] == "any"


def test_filter_work_items_retains_singular_filter_behavior(monkeypatch):
    registry = ToolRegistry()
    register_work_item_tools(registry)
    captured = {}

    class Cache:
        def filter_items(self, **kwargs):
            captured.update(kwargs)
            return {"results": [], "total_count": 0}

    monkeypatch.setattr(
        work_items_module,
        "get_plane_client_context",
        lambda: (SimpleNamespace(work_items=SimpleNamespace(_get=lambda path, params: {"results": []})), "workspace"),
    )
    monkeypatch.setattr(work_items_module, "WorkItemCache", Cache)
    monkeypatch.setattr(work_items_module, "sync_work_items", lambda **kwargs: {})

    result = registry.tools["filter_work_items"](
        "project", priority="high", state_id="state-1", state_group="started", assignee_id="user-1", label_id="label-1"
    )

    assert captured["priority"] == "high"
    assert captured["state_id"] == "state-1"
    assert captured["state_group"] == "started"
    assert captured["assignee_id"] == "user-1"
    assert captured["label_id"] == "label-1"
    assert result["total_count"] == 0


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

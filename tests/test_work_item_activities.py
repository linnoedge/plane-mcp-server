"""Tests for work-item activity client-side filtering."""

from types import SimpleNamespace
from typing import get_type_hints

import pytest
from plane.models.work_items import PaginatedWorkItemActivityResponse, WorkItemActivity

from plane_mcp.tools.work_item_activities import _filter_activity_pages, register_work_item_activity_tools


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function

        return register


def _sdk_page(results, next_cursor="", has_more=False):
    return PaginatedWorkItemActivityResponse(
        results=results,
        total_count=len(results),
        next_cursor=next_cursor,
        prev_cursor="",
        next_page_results=has_more,
        prev_page_results=False,
        count=len(results),
        total_pages=1,
        total_results=len(results),
    )


def _register(monkeypatch, pages):
    import plane_mcp.tools.work_item_activities as activities_module

    registry = ToolRegistry()
    register_work_item_activity_tools(registry)
    calls = []

    class Activities:
        def list(self, **kwargs):
            calls.append(kwargs)
            return pages[len(calls) - 1]

    client = SimpleNamespace(work_items=SimpleNamespace(activities=Activities()))
    monkeypatch.setattr(activities_module, "get_plane_client_context", lambda: (client, "workspace"))
    return registry.tools, calls


def test_filter_activity_pages_scans_cursors_and_excludes_ignored_server_range_results():
    pages = [
        {
            "results": [
                {"id": "old", "created_at": "2026-06-30T23:59:59Z"},
                {"id": "start", "created_at": "2026-07-01T00:00:00+00:00"},
            ],
            "next_cursor": "page-2",
            "next_page_results": True,
        },
        _sdk_page(
            [
                WorkItemActivity(
                    id="end",
                    project="project",
                    workspace="workspace",
                    created_at="2026-07-31T23:59:59-04:00",
                ),
                WorkItemActivity(id="new", project="project", workspace="workspace", created_at="2026-08-01T04:00:00Z"),
            ]
        ),
    ]
    requested_cursors = []

    def fetch_page(cursor):
        requested_cursors.append(cursor)
        return pages[len(requested_cursors) - 1]

    result = _filter_activity_pages(
        fetch_page=fetch_page,
        created_at_from="2026-07-01T00:00:00Z",
        created_at_to="2026-08-01T03:59:59Z",
        limit=10,
        cursor=None,
        max_pages=5,
    )

    assert [activity["id"] for activity in result["results"]] == ["start", "end"]
    assert requested_cursors == [None, "page-2"]
    assert result["total_scanned"] == 4
    assert result["pages_scanned"] == 2


def test_filter_activity_pages_applies_all_exact_filters_and_updated_range():
    activities = [
        {
            "id": "match",
            "updated_at": "2026-07-15T12:00:00Z",
            "activity_type": "issue.activity",
            "verb": "updated",
            "field": "priority",
            "actor": "user-1",
        },
        {
            "id": "wrong-case",
            "updated_at": "2026-07-15T12:00:00Z",
            "activity_type": "issue.activity",
            "verb": "Updated",
            "field": "priority",
            "actor": "user-1",
        },
    ]

    result = _filter_activity_pages(
        fetch_page=lambda cursor: {"results": activities, "next_cursor": "", "next_page_results": False},
        updated_at_from="2026-07-15T12:00:00Z",
        updated_at_to="2026-07-15T12:00:00+00:00",
        activity_type="issue.activity",
        verb="updated",
        field="priority",
        actor="user-1",
        limit=10,
        cursor=None,
        max_pages=1,
    )

    assert [activity["id"] for activity in result["results"]] == ["match"]


@pytest.mark.parametrize(
    "value",
    ["2026-07-31", "2026-07-31T12:00:00", "not-a-timestamp", "2026-02-30T00:00:00Z"],
)
def test_filter_activity_pages_rejects_malformed_or_naive_filter_timestamps(value):
    with pytest.raises(ValueError, match="strict ISO-8601 timestamp with timezone"):
        _filter_activity_pages(
            fetch_page=lambda cursor: pytest.fail("invalid timestamps must fail before fetching"),
            created_at_from=value,
            limit=10,
            cursor=None,
            max_pages=1,
        )


def test_legacy_filters_are_local_and_pagination_params_reach_sdk(monkeypatch):
    tools, calls = _register(
        monkeypatch,
        [
            {
                "results": [
                    {
                        "id": "lower-bound",
                        "project": "project",
                        "workspace": "workspace",
                        "created_at": "2026-07-01T00:00:00Z",
                        "updated_at": "2026-07-20T00:00:00Z",
                        "activity_type": "issue.activity",
                        "verb": "updated",
                        "field": "priority",
                        "actor": "user-1",
                    },
                    {
                        "id": "match",
                        "project": "project",
                        "workspace": "workspace",
                        "created_at": "2026-07-02T00:00:00Z",
                        "updated_at": "2026-07-20T00:00:00Z",
                        "activity_type": "issue.activity",
                        "verb": "updated",
                        "field": "priority",
                        "actor": "user-1",
                    },
                ],
                "next_cursor": "",
                "next_page_results": False,
            }
        ],
    )

    result = tools["list_work_item_activities"](
        "project",
        "work-item",
        params={
            "cursor": "legacy-cursor",
            "per_page": 25,
            "created_at__gt": "2026-07-01T00:00:00Z",
            "created_at__lte": "2026-07-02T00:00:00Z",
            "updated_at__gte": "2026-07-20T00:00:00Z",
            "updated_at__lt": "2026-07-21T00:00:00Z",
            "activity_type": "issue.activity",
            "verb": "updated",
            "field": "priority",
            "actor": "user-1",
            "unknown": "drop-me",
        },
    )

    assert [activity.id for activity in result] == ["match"]
    assert calls[0]["params"] == {"cursor": "legacy-cursor", "per_page": 25}


def test_explicit_filters_and_pagination_take_precedence_over_legacy_params(monkeypatch):
    tools, calls = _register(
        monkeypatch,
        [
            {
                "results": [
                    {
                        "id": "match",
                        "project": "project",
                        "workspace": "workspace",
                        "created_at": "2026-07-01T00:00:00Z",
                        "verb": "explicit",
                    }
                ],
                "next_cursor": "",
                "next_page_results": False,
            }
        ],
    )

    result = tools["list_work_item_activities"](
        "project",
        "work-item",
        params={"cursor": "legacy", "per_page": 20, "created_at__gt": "2026-08-01T00:00:00Z", "verb": "legacy"},
        cursor="explicit-cursor",
        per_page=30,
        created_at_from="2026-07-01T00:00:00Z",
        verb="explicit",
    )

    assert [activity.id for activity in result] == ["match"]
    assert calls[0]["params"] == {"cursor": "explicit-cursor", "per_page": 30}


def test_default_return_is_legacy_list_and_metadata_is_opt_in(monkeypatch):
    page = _sdk_page([WorkItemActivity(id="one", project="project", workspace="workspace")])
    tools, _ = _register(monkeypatch, [page, page])

    default_result = tools["list_work_item_activities"]("project", "work-item")
    metadata_result = tools["filter_work_item_activities"]("project", "work-item")

    assert isinstance(default_result, list)
    assert isinstance(default_result[0], WorkItemActivity)
    assert default_result[0].id == "one"
    assert metadata_result["count"] == 1
    assert metadata_result["pages_scanned"] == 1
    assert get_type_hints(tools["list_work_item_activities"])["return"] == list[WorkItemActivity]


def test_list_future_created_at_gt_filters_locally_and_returns_empty(monkeypatch):
    tools, calls = _register(
        monkeypatch,
        [
            {
                "results": [
                    {
                        "id": "past",
                        "project": "project",
                        "workspace": "workspace",
                        "created_at": "2026-07-31T00:00:00Z",
                    }
                ],
                "next_cursor": "",
                "next_page_results": False,
            }
        ],
    )

    result = tools["list_work_item_activities"](
        "project", "work-item", params={"created_at__gt": "2099-01-01T00:00:00Z"}
    )

    assert result == []
    assert calls[0]["params"] == {"cursor": None, "per_page": 100}


def test_limit_does_not_return_cursor_that_skips_matches_in_current_page(monkeypatch):
    tools, _ = _register(
        monkeypatch,
        [
            {
                "results": [{"id": "one"}, {"id": "two"}, {"id": "three"}],
                "next_cursor": "page-2",
                "next_page_results": True,
            }
        ],
    )

    result = tools["filter_work_item_activities"]("project", "work-item", limit=2, max_pages=1)

    assert [activity["id"] for activity in result["results"]] == ["one", "two"]
    assert result["total_scanned"] == 3
    assert result["results_truncated"] is True
    assert result["scan_has_more"] is True
    assert result["has_more"] is False
    assert result["next_cursor"] == ""


@pytest.mark.parametrize(
    ("argument", "value"),
    [("limit", 0), ("limit", 101), ("per_page", 0), ("per_page", 101), ("max_pages", 0), ("max_pages", 101)],
)
def test_scan_bounds_are_rejected_instead_of_clamped(monkeypatch, argument, value):
    tools, calls = _register(monkeypatch, [])

    with pytest.raises(ValueError, match=rf"{argument} must be between 1 and 100"):
        tools["filter_work_item_activities"]("project", "work-item", **{argument: value})

    assert calls == []

"""Tests for complete weekly report bundle collection."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from plane.errors.errors import HttpError

import plane_mcp.tools.weekly_report as weekly_report_module
from plane_mcp.tools.weekly_report import collect_weekly_report_bundle, register_weekly_report_tools


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function

        return register


def _projects():
    return {"BABY": "project-1"}


def _staff():
    return [{"id": "staff-1", "display_name": "Person", "role": "DEV"}]


def _item(item_id, **values):
    return {
        "id": item_id,
        "name": item_id,
        "updated_at": "2026-07-28T00:00:00.000000Z",
        "state_id": "state-1",
        "state_group": "started",
        "assignee_ids": ["staff-1"],
        "label_ids": [],
        "target_date": None,
        **values,
    }


def _install_collection(monkeypatch, branch_pages, activity_pages, sync_results=None, metadata_pages=None):
    filter_calls = []
    activity_calls = []
    metadata_calls = []
    metadata_pages = metadata_pages or {
        "states": {
            None: {"results": [{"id": "state-1", "name": "Started"}], "next_cursor": "", "next_page_results": False}
        },
        "labels": {None: {"results": [], "next_cursor": "", "next_page_results": False}},
        "project_types": {None: {"results": [], "next_cursor": "", "next_page_results": False}},
        "workspace_types": {None: {"results": [], "next_cursor": "", "next_page_results": False}},
    }
    for pages in metadata_pages.values():
        total_count = sum(len(page.get("results") or []) for page in pages.values())
        for page in pages.values():
            page.setdefault("total_count", total_count)
    for pages in activity_pages.values():
        total_count = sum(len(page.get("results") or []) for page in pages.values())
        for page in pages.values():
            page.setdefault("total_count", total_count)

    branch = -1

    class Cache:
        snapshot_count = 0

        def read_snapshot(self):
            self.snapshot_count += 1
            return nullcontext(self)

        def filter_items(self, **kwargs):
            nonlocal branch
            filter_calls.append(kwargs)
            if kwargs["offset"] == 0:
                branch += 1
            pages = branch_pages[branch]
            return pages[kwargs["offset"] // kwargs["limit"]]

    class Activities:
        def list(self, **kwargs):
            activity_calls.append(kwargs)
            pages = activity_pages[kwargs["work_item_id"]]
            cursor = kwargs["params"].get("cursor")
            return pages[cursor]

    class MetadataEndpoint:
        def __init__(self, name):
            self.name = name

        def list(self, **kwargs):
            metadata_calls.append((self.name, kwargs))
            params = kwargs.get("params") or {}
            return metadata_pages[self.name][params.get("cursor")]

    client = SimpleNamespace(
        work_items=SimpleNamespace(
            _get=lambda path, params: {"results": [], "total_count": 0, "next_page_results": False},
            activities=Activities(),
        ),
        states=MetadataEndpoint("states"),
        labels=MetadataEndpoint("labels"),
        work_item_types=MetadataEndpoint("project_types"),
        workspace_work_item_types=MetadataEndpoint("workspace_types"),
    )
    results = iter(sync_results or [{"status": "synced", "pages_fetched": 1, "items_upserted": 3}])
    monkeypatch.setattr(weekly_report_module, "WorkItemCache", Cache)
    monkeypatch.setattr(
        weekly_report_module,
        "_capture_collection_boundary",
        lambda: weekly_report_module._timestamp("2026-08-04T00:00:00Z", "boundary"),
    )
    monkeypatch.setattr(weekly_report_module, "sync_work_items", lambda **kwargs: next(results))
    monkeypatch.setattr(weekly_report_module, "get_plane_client_context", lambda: (client, "workspace"))
    monkeypatch.setattr(weekly_report_module, "get_plane_cache_scope", lambda: "scope")
    monkeypatch.setattr(weekly_report_module.os, "getenv", lambda name, default=None: default)
    return filter_calls, activity_calls, metadata_calls


def test_self_host_missing_workspace_types_falls_back_to_project_types(monkeypatch):
    branch_pages = [[{"results": [], "total_count": 0}]] * 3
    _, _, metadata_calls = _install_collection(monkeypatch, branch_pages, {})
    client, _ = weekly_report_module.get_plane_client_context()

    def missing_workspace_types(**kwargs):
        raise HttpError(status_code=404, message="Page not found")

    client.workspace_work_item_types.list = missing_workspace_types

    result = collect_weekly_report_bundle(
        _projects(),
        _staff(),
        "2026-07-27T00:00:00Z",
        "2026-08-03T00:00:00Z",
        "2026-08-03T12:00:00Z",
    )

    assert result["metadata"]["work_item_types"] == []
    assert any(name == "project_types" for name, _ in metadata_calls)


def test_collects_exact_branches_paginates_deduplicates_and_shapes_aggregates(monkeypatch):
    changed = _item("changed")
    shared = _item("shared")
    started = _item("started")
    overdue = _item("overdue", target_date="2026-07-30")
    branch_pages = [
        [
            {"results": [changed, shared], "total_count": 3},
            {"results": [started], "total_count": 3},
        ],
        [
            {"results": [shared, started], "total_count": 2},
        ],
        [
            {"results": [overdue], "total_count": 1},
        ],
    ]
    activity_pages = {
        item_id: {
            None: {
                "results": [
                    {
                        "id": f"{item_id}-activity",
                        "project": "project-1",
                        "work_item": item_id,
                        "field": "state",
                        "created_at": "2026-07-31T12:00:00Z",
                    }
                ],
                "next_cursor": "second" if item_id == "changed" else "",
                "next_page_results": item_id == "changed",
            },
            **(
                {
                    "second": {
                        "results": [
                            {
                                "id": "changed-activity",
                                "project": "project-1",
                                "work_item": "changed",
                                "field": "state",
                                "created_at": "2026-07-31T12:00:00Z",
                            },
                            {
                                "id": "changed-old",
                                "project": "project-1",
                                "work_item": "changed",
                                "field": "state",
                                "created_at": "2026-06-01T00:00:00Z",
                            },
                        ],
                        "next_cursor": "",
                        "next_page_results": False,
                    }
                }
                if item_id == "changed"
                else {}
            ),
        }
        for item_id in ("changed", "shared", "started", "overdue")
    }
    filter_calls, activity_calls, _ = _install_collection(monkeypatch, branch_pages, activity_pages)

    result = collect_weekly_report_bundle(
        projects=_projects(),
        staff=_staff(),
        week_start="2026-07-27T00:00:00+07:00",
        week_end="2026-08-03T00:00:00+07:00",
        collection_started_at="2026-08-03T07:00:00+07:00",
        candidate_page_size=2,
    )

    assert result["completeness"]["complete"] is True
    assert [item["id"] for item in result["work_items"]["results"]] == ["changed", "shared", "started", "overdue"]
    assert all(item["project"] == "project-1" for item in result["work_items"]["results"])
    assert len(result["activities"]["results"]) == 5
    assert result["metadata"]["projects"] == [{"identifier": "BABY", "id": "project-1"}]
    assert result["metadata"]["staff"] == _staff()
    assert len(filter_calls) == 4
    first_by_branch = [filter_calls[0], filter_calls[2], filter_calls[3]]
    assert first_by_branch[0]["updated_at_from"] == "2026-07-26T17:00:00.000000Z"
    assert first_by_branch[0]["updated_at_to"] >= "2026-08-03T00:00:00.000000Z"
    assert first_by_branch[1]["state_groups"] == ["started"]
    assert first_by_branch[2]["state_groups"] == ["backlog", "unstarted", "started"]
    assert first_by_branch[2]["target_date_to"] == "2026-08-02"
    assert first_by_branch[2]["overdue"] is True
    assert all(call["sort_by"] == "updated_at" and call["sort_direction"] == "asc" for call in filter_calls)
    assert all(call["params"]["per_page"] == 100 for call in activity_calls)
    assert result["collection"]["collection_started_at"] == "2026-08-04T00:00:00.000000Z"
    assert all(
        activity["created_at"] <= result["collection"]["collection_started_at"]
        for activity in result["activities"]["results"]
    )
    assert activity_calls[0]["params"]["cursor"] is None
    assert activity_calls[1]["params"]["cursor"] == "second"


def test_rejects_requested_collection_boundary_before_exclusive_week_end(monkeypatch):
    with pytest.raises(ValueError, match="not be before exclusive week_end"):
        collect_weekly_report_bundle(
            _projects(),
            _staff(),
            "2026-07-27T00:00:00Z",
            "2026-08-03T00:00:00Z",
            "2026-08-02T23:59:59Z",
        )


def test_rejects_requested_collection_boundary_after_effective_boundary(monkeypatch):
    monkeypatch.setattr(
        weekly_report_module,
        "_capture_collection_boundary",
        lambda: weekly_report_module._timestamp("2026-08-03T01:00:00Z", "boundary"),
    )

    with pytest.raises(ValueError, match="must not be after effective collection boundary"):
        collect_weekly_report_bundle(
            _projects(),
            _staff(),
            "2026-07-27T00:00:00Z",
            "2026-08-03T00:00:00Z",
            "2026-08-03T01:00:01Z",
        )


def test_captures_effective_boundary_before_metadata_and_project_scans_and_caps_reads(monkeypatch):
    events = []
    boundary = weekly_report_module._timestamp("2026-08-03T01:00:00Z", "boundary")
    branch_pages = [[{"results": [], "total_count": 0}]] * 3
    filter_calls, _, _ = _install_collection(monkeypatch, branch_pages, {})
    original_metadata = weekly_report_module._collect_metadata
    original_sync = weekly_report_module._sync_project

    def capture_boundary():
        events.append("boundary")
        return boundary

    def collect_metadata(*args):
        events.append("metadata")
        return original_metadata(*args)

    def sync_project(*args):
        events.append("sync")
        return original_sync(*args)

    monkeypatch.setattr(weekly_report_module, "_capture_collection_boundary", capture_boundary)
    monkeypatch.setattr(weekly_report_module, "_collect_metadata", collect_metadata)
    monkeypatch.setattr(weekly_report_module, "_sync_project", sync_project)

    result = collect_weekly_report_bundle(
        _projects(),
        _staff(),
        "2026-07-27T00:00:00Z",
        "2026-08-03T00:00:00Z",
        "2026-08-03T00:30:00Z",
    )

    assert events == ["boundary", "metadata", "sync"]
    assert filter_calls[0]["updated_at_to"] == "2026-08-03T01:00:00.000000Z"
    assert result["collection"]["collection_started_at"] == "2026-08-03T01:00:00.000000Z"


def test_accepts_stable_metadata_total_across_normal_final_page(monkeypatch):
    branch_pages = [[{"results": [], "total_count": 0}]] * 3
    metadata_pages = {
        "states": {
            None: {
                "results": [{"id": "state-1", "name": "Started"}],
                "total_count": 2,
                "next_cursor": "states-2",
                "next_page_results": True,
            },
            "states-2": {
                "results": [{"id": "state-2", "name": "Done"}],
                "total_count": 2,
                "next_cursor": "",
                "next_page_results": False,
            },
        },
        "labels": {None: {"results": [], "total_count": 0, "next_cursor": "", "next_page_results": False}},
        "project_types": {None: {"results": [], "total_count": 0, "next_cursor": "", "next_page_results": False}},
        "workspace_types": {None: {"results": [], "total_count": 0, "next_cursor": "", "next_page_results": False}},
    }
    _install_collection(monkeypatch, branch_pages, {}, metadata_pages=metadata_pages)

    result = collect_weekly_report_bundle(
        _projects(), _staff(), "2026-07-27T00:00:00Z", "2026-08-03T00:00:00Z", "2026-08-03T00:00:00Z"
    )

    assert [state["id"] for state in result["metadata"]["states"]] == ["state-1", "state-2"]


def test_rejects_changed_or_cumulatively_incomplete_metadata_total(monkeypatch):
    branch_pages = [[{"results": [], "total_count": 0}]] * 3
    for final_total, message in ((3, "total changed"), (2, "raw count mismatch")):
        metadata_pages = {
            "states": {
                None: {
                    "results": [{"id": "state-1"}],
                    "total_count": 2,
                    "next_cursor": "states-2",
                    "next_page_results": True,
                },
                "states-2": {
                    "results": [] if final_total == 2 else [{"id": "state-2"}],
                    "total_count": final_total,
                    "next_cursor": "",
                    "next_page_results": False,
                },
            },
            "labels": {None: {"results": [], "total_count": 0, "next_cursor": "", "next_page_results": False}},
            "project_types": {None: {"results": [], "total_count": 0, "next_cursor": "", "next_page_results": False}},
            "workspace_types": {None: {"results": [], "total_count": 0, "next_cursor": "", "next_page_results": False}},
        }
        _install_collection(monkeypatch, branch_pages, {}, metadata_pages=metadata_pages)
        with pytest.raises(RuntimeError, match=message):
            collect_weekly_report_bundle(
                _projects(),
                _staff(),
                "2026-07-27T00:00:00Z",
                "2026-08-03T00:00:00Z",
                "2026-08-03T00:00:00Z",
            )


def test_activity_pagination_uses_stable_total_cumulative_raw_count_and_ordering(monkeypatch):
    item = _item("item")
    branch_pages = [
        [{"results": [item], "total_count": 1}],
        [{"results": [], "total_count": 0}],
        [{"results": [], "total_count": 0}],
    ]
    activity_pages = {
        "item": {
            None: {
                "results": [
                    {
                        "id": "activity-1",
                        "project": "project-1",
                        "work_item": "item",
                        "field": "state",
                        "created_at": "2026-08-03T00:00:00Z",
                    }
                ],
                "total_count": 2,
                "next_cursor": "page-2",
                "next_page_results": True,
            },
            "page-2": {
                "results": [
                    {
                        "id": "activity-2",
                        "project": "project-1",
                        "work_item": "item",
                        "field": "state",
                        "created_at": "2026-07-01T00:00:00Z",
                    }
                ],
                "total_count": 2,
                "next_cursor": "",
                "next_page_results": False,
            },
        }
    }
    _, activity_calls, _ = _install_collection(monkeypatch, branch_pages, activity_pages)

    result = collect_weekly_report_bundle(
        _projects(), _staff(), "2026-07-27T00:00:00Z", "2026-08-03T00:00:00Z", "2026-08-03T00:00:00Z"
    )

    assert len(result["activities"]["results"]) == 2
    assert all(call["params"]["order_by"] == "created_at" for call in activity_calls)


def test_rejects_changed_or_cumulatively_incomplete_activity_total(monkeypatch):
    item = _item("item")
    branch_pages = [
        [{"results": [item], "total_count": 1}],
        [{"results": [], "total_count": 0}],
        [{"results": [], "total_count": 0}],
    ]
    for final_total, final_results, message in ((3, [], "total changed"), (2, [], "raw count mismatch")):
        activity_pages = {
            "item": {
                None: {
                    "results": [{"id": "ignored", "field": "name"}],
                    "total_count": 2,
                    "next_cursor": "page-2",
                    "next_page_results": True,
                },
                "page-2": {
                    "results": final_results,
                    "total_count": final_total,
                    "next_cursor": "",
                    "next_page_results": False,
                },
            }
        }
        _install_collection(monkeypatch, branch_pages, activity_pages)
        with pytest.raises(RuntimeError, match=message):
            collect_weekly_report_bundle(
                _projects(),
                _staff(),
                "2026-07-27T00:00:00Z",
                "2026-08-03T00:00:00Z",
                "2026-08-03T00:00:00Z",
            )


def test_rejects_candidate_with_unresolved_metadata_reference(monkeypatch):
    item = _item("item", state_id="missing-state")
    branch_pages = [
        [{"results": [item], "total_count": 1}],
        [{"results": [], "total_count": 0}],
        [{"results": [], "total_count": 0}],
    ]
    _install_collection(monkeypatch, branch_pages, {"item": {}})

    with pytest.raises(RuntimeError, match="unresolved state reference"):
        collect_weekly_report_bundle(
            _projects(),
            _staff(),
            "2026-07-27T00:00:00Z",
            "2026-08-03T00:00:00Z",
            "2026-08-03T00:00:00Z",
        )


def test_fetches_metadata_and_normalizes_real_plane_activity(monkeypatch):
    item = _item(
        "item-1",
        state_id="state-uuid",
        label_ids=["label-uuid"],
        type_id="type-uuid",
    )
    branch_pages = [
        [{"results": [item], "total_count": 1}],
        [{"results": [], "total_count": 0}],
        [{"results": [], "total_count": 0}],
    ]
    activity_pages = {
        "item-1": {
            None: {
                "results": [
                    {
                        "id": "activity-1",
                        "project": "project-1",
                        "issue": "item-1",
                        "field": "state",
                        "old_value": "Backlog",
                        "new_value": "In Progress",
                        "created_at": "2026-07-31T12:00:00Z",
                    }
                ],
                "next_cursor": "",
                "next_page_results": False,
            }
        }
    }
    metadata_pages = {
        "states": {
            None: {
                "results": [{"id": "state-old", "name": "Backlog"}],
                "next_cursor": "states-2",
                "next_page_results": True,
            },
            "states-2": {
                "results": [{"id": "state-uuid", "name": "In Progress"}],
                "next_cursor": "",
                "next_page_results": False,
            },
        },
        "labels": {
            None: {
                "results": [{"id": "label-uuid", "name": "Critical"}],
                "next_cursor": "",
                "next_page_results": False,
            }
        },
        "project_types": {
            None: {
                "results": [{"id": "type-uuid", "name": "Issue"}],
                "next_cursor": "",
                "next_page_results": False,
            }
        },
        "workspace_types": {
            None: {
                "results": [
                    {"id": "type-uuid", "name": "Issue"},
                    {"id": "epic-uuid", "name": "Epic"},
                ],
                "next_cursor": "",
                "next_page_results": False,
            }
        },
    }
    _, _, metadata_calls = _install_collection(monkeypatch, branch_pages, activity_pages, metadata_pages=metadata_pages)

    result = collect_weekly_report_bundle(
        _projects(),
        _staff(),
        "2026-07-27T00:00:00Z",
        "2026-08-03T00:00:00Z",
        "2026-08-03T00:00:00Z",
    )

    assert result["metadata"]["states"] == [
        {"id": "state-old", "name": "Backlog"},
        {"id": "state-uuid", "name": "In Progress"},
    ]
    assert result["metadata"]["labels"] == [{"id": "label-uuid", "name": "Critical"}]
    assert result["metadata"]["work_item_types"] == [
        {"id": "type-uuid", "name": "Issue"},
        {"id": "epic-uuid", "name": "Epic"},
    ]
    assert result["activities"]["results"] == [
        {
            "id": "activity-1",
            "project": "project-1",
            "issue": "item-1",
            "work_item": "item-1",
            "field": "state",
            "old_value": "Backlog",
            "new_value": "In Progress",
            "created_at": "2026-07-31T12:00:00Z",
        }
    ]
    assert [name for name, _ in metadata_calls].count("states") == 2


def test_rejects_raw_issue_identity_mismatch(monkeypatch):
    item = _item("item-1")
    branch_pages = [
        [{"results": [item], "total_count": 1}],
        [{"results": [], "total_count": 0}],
        [{"results": [], "total_count": 0}],
    ]
    page = {
        "results": [
            {
                "id": "activity-1",
                "project": "project-1",
                "issue": "different-item",
                "work_item": "item-1",
                "field": "state",
                "created_at": "2026-07-31T12:00:00Z",
            }
        ],
        "next_cursor": "",
        "next_page_results": False,
    }
    _install_collection(monkeypatch, branch_pages, {"item-1": {None: page}})

    with pytest.raises(RuntimeError, match="work-item mismatch"):
        collect_weekly_report_bundle(
            _projects(),
            _staff(),
            "2026-07-27T00:00:00Z",
            "2026-08-03T00:00:00Z",
            "2026-08-03T00:00:00Z",
        )


def test_rejects_incomplete_metadata_pagination(monkeypatch):
    metadata_pages = {
        "states": {None: {"results": [], "next_cursor": "", "next_page_results": True}},
        "labels": {None: {"results": [], "next_cursor": "", "next_page_results": False}},
        "project_types": {None: {"results": [], "next_cursor": "", "next_page_results": False}},
        "workspace_types": {None: {"results": [], "next_cursor": "", "next_page_results": False}},
    }
    _install_collection(
        monkeypatch,
        [[{"results": [], "total_count": 0}]] * 3,
        {},
        metadata_pages=metadata_pages,
    )

    with pytest.raises(RuntimeError, match="unsafe states metadata continuation"):
        collect_weekly_report_bundle(
            _projects(),
            _staff(),
            "2026-07-27T00:00:00Z",
            "2026-08-03T00:00:00Z",
            "2026-08-03T00:00:00Z",
        )


def test_retries_busy_sync_with_bounded_exponential_backoff(monkeypatch):
    branch_pages = [[{"results": [], "total_count": 0}]] * 3
    filter_calls, _, _ = _install_collection(
        monkeypatch,
        branch_pages,
        {},
        sync_results=[{"status": "busy"}, {"status": "busy"}, {"status": "synced"}],
    )
    sleeps = []
    times = iter([0.0, 0.0, 0.1, 0.1, 0.3, 0.3])
    monkeypatch.setattr(weekly_report_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(weekly_report_module.time, "monotonic", lambda: next(times))

    result = collect_weekly_report_bundle(
        _projects(),
        _staff(),
        "2026-07-27T00:00:00Z",
        "2026-08-03T00:00:00Z",
        "2026-08-03T00:00:00Z",
        sync_timeout_seconds=2,
        retry_initial_backoff_seconds=0.1,
        retry_max_backoff_seconds=0.15,
    )

    assert result["completeness"]["complete"] is True
    assert sleeps == [0.1, 0.15]
    assert len(filter_calls) == 3


@pytest.mark.parametrize("status", ["partial", "incomplete"])
def test_rejects_non_complete_cache_sync(monkeypatch, status):
    _install_collection(monkeypatch, [], {}, sync_results=[{"status": status}])

    with pytest.raises(RuntimeError, match="cache synchronization incomplete"):
        collect_weekly_report_bundle(
            _projects(),
            _staff(),
            "2026-07-27T00:00:00Z",
            "2026-08-03T00:00:00Z",
            "2026-08-03T00:00:00Z",
        )


def test_rejects_busy_timeout(monkeypatch):
    _install_collection(monkeypatch, [], {}, sync_results=[{"status": "busy"}, {"status": "busy"}])
    times = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(weekly_report_module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(weekly_report_module.time, "sleep", lambda value: None)

    with pytest.raises(TimeoutError, match="busy"):
        collect_weekly_report_bundle(
            _projects(),
            _staff(),
            "2026-07-27T00:00:00Z",
            "2026-08-03T00:00:00Z",
            "2026-08-03T00:00:00Z",
            sync_timeout_seconds=0.5,
        )


@pytest.mark.parametrize(
    "page, message",
    [
        (
            {"results": [], "next_cursor": "", "next_page_results": True},
            "unsafe activity continuation",
        ),
        (
            {"results": [], "next_cursor": "unexpected", "next_page_results": False},
            "inconsistent activity pagination",
        ),
        (
            {"results": [], "total_count": 1, "next_cursor": "", "next_page_results": False},
            "activity raw count mismatch",
        ),
        (
            {
                "results": [
                    {
                        "id": "activity",
                        "project": "wrong-project",
                        "work_item": "item",
                        "field": "state",
                        "created_at": "2026-07-01T00:00:00Z",
                    }
                ],
                "next_cursor": "",
                "next_page_results": False,
            },
            "project mismatch",
        ),
    ],
)
def test_rejects_incomplete_or_mismatched_activity_collection(monkeypatch, page, message):
    item = _item("item")
    branch_pages = [
        [{"results": [item], "total_count": 1}],
        [{"results": [], "total_count": 0}],
        [{"results": [], "total_count": 0}],
    ]
    _install_collection(monkeypatch, branch_pages, {"item": {None: page}})

    with pytest.raises(RuntimeError, match=message):
        collect_weekly_report_bundle(
            _projects(),
            _staff(),
            "2026-07-27T00:00:00Z",
            "2026-08-03T00:00:00Z",
            "2026-08-03T00:00:00Z",
        )


async def _listed_tool():
    mcp = FastMCP("test")
    register_weekly_report_tools(mcp)
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    return tools["collect_weekly_report_bundle"]


def test_tools_list_exposes_comprehensive_description_and_schema():
    tool = asyncio.run(_listed_tool())

    for text in (
        "exactly three",
        "updated",
        "started",
        "overdue",
        "client-side",
        "exclusive",
        "ISO-8601",
        "timezone",
        "busy",
        "exponential backoff",
        "complete",
        "does not write files",
        "normalizes work_item",
        "issue",
        "states, labels, and work item types",
        "deduplicates metadata by ID",
        "fails rather than returning partial metadata",
        "forced full scan",
        "deletions",
        "single SQLite read snapshot",
        "requested collection_started_at",
        "Before metadata and every forced project scan",
        "capped at this same pre-scan boundary",
        "tool=collect_weekly_report_bundle",
        "five validated input arguments",
        "original canonical representations are retained exactly",
        "effective collection boundary",
        "stable total_count",
        "cumulative raw",
        "resolves every candidate state, label, and type UUID",
        "order_by=created_at",
    ):
        assert text in tool.description
    properties = tool.inputSchema["properties"]
    assert properties["projects"]["anyOf"]
    assert properties["staff"]["type"] == "array"
    assert properties["sync_timeout_seconds"]["default"] == 180.0
    assert properties["retry_initial_backoff_seconds"]["default"] == 0.1
    assert properties["retry_max_backoff_seconds"]["default"] == 2.0
    assert properties["candidate_page_size"]["default"] == 100
    assert properties["activity_page_size"]["default"] == 100
    assert properties["activity_max_pages"]["default"] == 100


def test_realistic_fastmcp_client_call_returns_manifest_bindable_structured_content(monkeypatch):
    projects = [{"id": "project-1", "identifier": "BABY", "states": [{"id": "state-1", "name": "In Progress"}]}]
    staff = _staff()
    branch_pages = [[{"results": [], "total_count": 0}]] * 3
    _install_collection(monkeypatch, branch_pages, {})
    monkeypatch.setattr(
        weekly_report_module,
        "_capture_collection_boundary",
        lambda: weekly_report_module._timestamp("2026-08-03T01:00:00Z", "boundary"),
    )

    async def call_tool():
        mcp = FastMCP("test")
        register_weekly_report_tools(mcp)
        async with Client(mcp) as client:
            return await client.call_tool(
                "collect_weekly_report_bundle",
                {
                    "projects": projects,
                    "staff": staff,
                    "week_start": "2026-07-27T00:00:00+07:00",
                    "week_end": "2026-08-03T00:00:00+07:00",
                    "collection_started_at": "2026-08-03T07:30:00+07:00",
                },
            )

    result = asyncio.run(call_tool())
    bundle = result.structured_content

    assert bundle["tool"] == "collect_weekly_report_bundle"
    assert bundle["requested"] == {
        "projects": projects,
        "staff": staff,
        "week_start": "2026-07-27T00:00:00+07:00",
        "week_end": "2026-08-03T00:00:00+07:00",
        "collection_started_at": "2026-08-03T07:30:00+07:00",
    }
    assert bundle["collection"]["collection_started_at"] == "2026-08-03T01:00:00.000000Z"
    assert bundle["work_items"] == {"metadata": bundle["metadata"], "results": []}
    assert bundle["activities"] == {"metadata": bundle["metadata"], "results": []}
    assert bundle["completeness"]["candidate_branches_per_project"] == 3

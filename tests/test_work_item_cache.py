"""Tests for the shared work item cache."""

from plane_mcp.work_item_cache import WorkItemCache, sync_work_items


def item(item_id, updated_at, priority="none", state_id="state", state_group="started", assignees=None, labels=None):
    return {
        "id": item_id,
        "name": item_id,
        "sequence_id": 1,
        "priority": priority,
        "state": {"id": state_id, "group": state_group},
        "assignees": assignees or [],
        "labels": labels or [],
        "updated_at": updated_at,
    }


def test_cache_is_shared_across_instances_and_upserts_newer_data(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = WorkItemCache(path)
    second = WorkItemCache(path)

    first.upsert_items("server", "workspace", "project", [item("one", "2026-07-30T10:00:00Z", priority="low")])
    second.upsert_items("server", "workspace", "project", [item("one", "2026-07-30T11:00:00Z", priority="high")])

    assert first.filter_items("server", "workspace", "project", priority="high", limit=10) == [
        {
            "id": "one",
            "name": "one",
            "sequence_id": 1,
            "priority": "high",
            "state_id": "state",
            "state_group": "started",
            "assignee_ids": [],
            "label_ids": [],
            "updated_at": "2026-07-30T11:00:00Z",
        }
    ]
    assert second.watermark("server", "workspace", "project") == "2026-07-30T11:00:00Z"


def test_cache_filters_relations_without_loading_full_payload(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    cache.upsert_items(
        "server",
        "workspace",
        "project",
        [
            item("match", "2026-07-30T11:00:00Z", assignees=["user-1"], labels=["label-1"]),
            item("other", "2026-07-30T10:00:00Z", assignees=["user-2"], labels=["label-2"]),
        ],
    )

    result = cache.filter_items(
        "server",
        "workspace",
        "project",
        assignee_id="user-1",
        label_id="label-1",
        limit=10,
    )

    assert [value["id"] for value in result] == ["match"]


def test_initial_sync_resumes_from_saved_cursor_until_complete(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    requested = []
    pages = [
        {
            "results": [item("new", "2026-07-30T12:00:00Z")],
            "next_cursor": "page-2",
            "next_page_results": True,
        },
        {
            "results": [item("old", "2026-07-30T10:00:00Z")],
            "next_cursor": "",
            "next_page_results": False,
        },
    ]

    def fetch(cursor):
        requested.append(cursor)
        return pages[0 if cursor is None else 1]

    first = sync_work_items(cache, "server", "workspace", "project", fetch, owner="owner", now=100, max_pages=1)
    second = sync_work_items(cache, "server", "workspace", "project", fetch, owner="owner", now=101, max_pages=1)

    assert requested == [None, "page-2"]
    assert first["status"] == "partial"
    assert second["status"] == "synced"
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", limit=10)] == [
        "new",
        "old",
    ]


def test_incremental_sync_stops_when_page_reaches_watermark(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    cache.upsert_items("server", "workspace", "project", [item("old", "2026-07-30T10:00:00Z")])
    cache.set_sync_state(
        "server", "workspace", "project", initialized=True, cursor=None, watermark="2026-07-30T10:00:00Z"
    )
    requested = []
    pages = [
        {
            "results": [item("new", "2026-07-30T12:00:00Z"), item("same", "2026-07-30T10:00:00Z")],
            "next_cursor": "page-2",
            "next_page_results": True,
        },
        {
            "results": [item("older", "2026-07-30T09:00:00Z")],
            "next_cursor": "",
            "next_page_results": False,
        },
    ]

    def fetch(cursor):
        requested.append(cursor)
        return pages[len(requested) - 1]

    result = sync_work_items(cache, "server", "workspace", "project", fetch, owner="owner", now=100)

    assert requested == [None, "page-2"]
    assert result == {"status": "synced", "pages_fetched": 2, "items_upserted": 2, "watermark": "2026-07-30T12:00:00Z"}
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", limit=10)] == [
        "new",
        "old",
        "same",
    ]


def test_cache_lease_allows_only_one_sync_process(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = WorkItemCache(path)
    second = WorkItemCache(path)

    assert first.acquire_lease("server", "workspace", "project", "owner-1", now=100, ttl=30) is True
    assert second.acquire_lease("server", "workspace", "project", "owner-2", now=110, ttl=30) is False
    assert second.acquire_lease("server", "workspace", "project", "owner-2", now=131, ttl=30) is True


def test_bounded_incremental_sync_resumes_cursor_without_advancing_watermark(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    cache.upsert_items("server", "workspace", "project", [item("old", "2026-07-30T10:00:00Z")])
    cache.set_sync_state(
        "server",
        "workspace",
        "project",
        initialized=True,
        cursor=None,
        watermark="2026-07-30T10:00:00Z",
        full_synced_at=100,
    )
    requested = []
    pages = {
        None: {
            "results": [item("new", "2026-07-30T12:00:00Z")],
            "next_cursor": "page-2",
            "next_page_results": True,
        },
        "page-2": {
            "results": [item("middle", "2026-07-30T11:00:00Z"), item("old", "2026-07-30T10:00:00Z")],
            "next_cursor": "page-3",
            "next_page_results": True,
        },
        "page-3": {
            "results": [item("older", "2026-07-30T09:00:00Z")],
            "next_page_results": False,
        },
    }

    def fetch(cursor):
        requested.append(cursor)
        return pages[cursor]

    first = sync_work_items(cache, "server", "workspace", "project", fetch, owner="owner", now=200, max_pages=1)
    second = sync_work_items(cache, "server", "workspace", "project", fetch, owner="owner", now=201, max_pages=10)

    assert requested == [None, "page-2", "page-3"]
    assert first["status"] == "partial"
    assert second["status"] == "synced"
    assert cache.watermark("server", "workspace", "project") == "2026-07-30T12:00:00Z"
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", limit=10)] == [
        "new",
        "middle",
        "old",
    ]


def test_full_reconciliation_removes_deleted_items(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    cache.upsert_items(
        "server",
        "workspace",
        "project",
        [item("kept", "2026-07-30T10:00:00Z"), item("deleted", "2026-07-29T10:00:00Z")],
    )
    cache.set_sync_state(
        "server",
        "workspace",
        "project",
        initialized=True,
        cursor=None,
        watermark="2026-07-30T10:00:00Z",
        full_synced_at=1,
    )
    page = {"results": [item("kept", "2026-07-30T10:00:00Z")], "next_page_results": False}

    sync_work_items(cache, "server", "workspace", "project", lambda cursor: page, owner="owner", now=100000)

    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", limit=10)] == ["kept"]


def test_sync_exception_releases_lease(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")

    try:
        sync_work_items(
            cache,
            "server",
            "workspace",
            "project",
            lambda cursor: (_ for _ in ()).throw(RuntimeError("boom")),
            owner="owner-1",
            now=100,
        )
    except RuntimeError:
        pass

    assert cache.acquire_lease("server", "workspace", "project", "owner-2", now=101, ttl=30) is True


def test_successful_sync_releases_lease_for_next_process(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = WorkItemCache(path)
    second = WorkItemCache(path)

    page = {"results": [item("one", "2026-07-30T10:00:00Z")], "next_page_results": False}
    sync_work_items(first, "server", "workspace", "project", lambda cursor: page, owner="owner-1", now=100)

    assert second.acquire_lease("server", "workspace", "project", "owner-2", now=101, ttl=30) is True

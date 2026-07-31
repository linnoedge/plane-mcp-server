"""Tests for the shared work item cache."""

import sqlite3
import threading
import time

import pytest

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


def test_cache_migrates_schema_created_by_prerelease(tmp_path):
    path = tmp_path / "cache.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE work_items (
                server TEXT NOT NULL, workspace TEXT NOT NULL, project_id TEXT NOT NULL, id TEXT NOT NULL,
                name TEXT, sequence_id INTEGER, priority TEXT, state_id TEXT, state_group TEXT,
                assignee_ids TEXT NOT NULL, label_ids TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (server, workspace, project_id, id)
            );
            CREATE TABLE sync_state (
                server TEXT NOT NULL, workspace TEXT NOT NULL, project_id TEXT NOT NULL,
                initialized INTEGER NOT NULL, cursor TEXT, watermark TEXT,
                PRIMARY KEY (server, workspace, project_id)
            );
            """
        )

    cache = WorkItemCache(path)
    cache.upsert_items("server", "workspace", "project", [item("one", "2026-07-30T10:00:00Z")])

    with sqlite3.connect(path) as connection:
        work_item_columns = {column[1] for column in connection.execute("PRAGMA table_info(work_items)")}
        assert {
            "generation",
            "type_id",
            "parent_id",
            "cycle_id",
            "module_ids",
            "created_by",
            "created_at",
            "start_date",
            "target_date",
            "completed_at",
            "is_draft",
        } <= work_item_columns
        assert {"scan_watermark", "generation", "full_synced_at"}.issubset(
            column[1] for column in connection.execute("PRAGMA table_info(sync_state)")
        )


def test_cache_migration_normalizes_existing_temporal_values(tmp_path):
    path = tmp_path / "cache.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE work_items (
                server TEXT NOT NULL, workspace TEXT NOT NULL, project_id TEXT NOT NULL, id TEXT NOT NULL,
                name TEXT, sequence_id INTEGER, priority TEXT, state_id TEXT, state_group TEXT,
                assignee_ids TEXT NOT NULL, label_ids TEXT NOT NULL, created_at TEXT,
                updated_at TEXT NOT NULL, start_date TEXT, target_date TEXT, completed_at TEXT,
                PRIMARY KEY (server, workspace, project_id, id)
            );
            CREATE TABLE sync_state (
                server TEXT NOT NULL, workspace TEXT NOT NULL, project_id TEXT NOT NULL,
                initialized INTEGER NOT NULL, cursor TEXT, watermark TEXT, scan_watermark TEXT,
                generation TEXT, full_synced_at REAL NOT NULL,
                PRIMARY KEY (server, workspace, project_id)
            );
            INSERT INTO work_items VALUES (
                'server', 'workspace', 'project', 'one', 'one', 1, 'none', 'state', 'started', '[]', '[]',
                '2026-07-29T23:30:00-02:00', '2026-07-30T12:30:00.123456+02:00',
                '2026-07-01T12:00:00Z', '2026-07-31', '2026-07-30T05:00:00-05:00'
            );
            INSERT INTO sync_state VALUES (
                'server', 'workspace', 'project', 1, 'cursor', '2026-07-30T12:30:00.123456+02:00',
                '2026-07-30T11:30:00+02:00', NULL, 0
            );
            """
        )

    cache = WorkItemCache(path)
    record = cache.filter_items("server", "workspace", "project")["results"][0]
    state = cache.sync_state("server", "workspace", "project")

    assert record["created_at"] == "2026-07-30T01:30:00.000000Z"
    assert record["updated_at"] == "2026-07-30T10:30:00.123456Z"
    assert record["start_date"] == "2026-07-01"
    assert record["target_date"] == "2026-07-31"
    assert record["completed_at"] == "2026-07-30T10:00:00.000000Z"
    assert state is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1

    cache.set_sync_state("server", "workspace", "project", True, None, record["updated_at"], full_synced_at=10)
    WorkItemCache(path)

    assert cache.sync_state("server", "workspace", "project")["initialized"] == 1


def test_cache_normalizes_expanded_fields_and_timestamps(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    value = item("one", "2026-07-30T12:30:00+02:00", assignees=[{"id": "a1"}], labels=["l1"])
    value.update(
        {
            "type_id": {"id": "type-1"},
            "parent": {"id": "parent-1"},
            "cycle_id": "cycle-1",
            "modules": [{"id": "module-1"}, "module-2"],
            "created_by": {"id": "user-1"},
            "created_at": "2026-07-29T23:30:00-02:00",
            "start_date": "2026-07-01T12:00:00Z",
            "target_date": "2026-07-31",
            "completed_at": "2026-07-30T05:00:00-05:00",
            "is_draft": True,
        }
    )
    cache.upsert_items("server", "workspace", "project", [value])

    record = cache.filter_items("server", "workspace", "project")["results"][0]

    assert record["type_id"] == "type-1"
    assert record["parent_id"] == "parent-1"
    assert record["cycle_id"] == "cycle-1"
    assert record["module_ids"] == ["module-1", "module-2"]
    assert record["created_by"] == "user-1"
    assert record["created_at"] == "2026-07-30T01:30:00.000000Z"
    assert record["updated_at"] == "2026-07-30T10:30:00.000000Z"
    assert record["start_date"] == "2026-07-01"
    assert record["target_date"] == "2026-07-31"
    assert record["completed_at"] == "2026-07-30T10:00:00.000000Z"
    assert record["is_draft"] is True


def test_cache_preserves_fractional_seconds(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    value = item("one", "2026-07-30T12:30:00.123456+02:00")
    value["created_at"] = "2026-07-30T10:30:00.000001Z"
    cache.upsert_items("server", "workspace", "project", [value])

    record = cache.filter_items("server", "workspace", "project")["results"][0]

    assert record["updated_at"] == "2026-07-30T10:30:00.123456Z"
    assert record["created_at"] == "2026-07-30T10:30:00.000001Z"


def test_timestamp_ordering_across_upsert_watermark_sort_and_range(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    cache.upsert_items(
        "server",
        "workspace",
        "project",
        [
            item("same", "2026-07-30T10:00:00Z", priority="low"),
            item("whole", "2026-07-30T11:00:00Z"),
            item("fractional", "2026-07-30T11:00:00.500000Z"),
        ],
    )
    cache.upsert_items(
        "server",
        "workspace",
        "project",
        [item("same", "2026-07-30T10:00:00.500000Z", priority="high")],
    )

    assert cache.filter_items("server", "workspace", "project", priority="high")["results"][0]["id"] == "same"
    assert cache.watermark("server", "workspace", "project") == "2026-07-30T11:00:00.500000Z"
    assert [
        value["id"]
        for value in cache.filter_items("server", "workspace", "project", sort_by="updated_at", sort_direction="asc")[
            "results"
        ]
    ] == ["same", "whole", "fractional"]
    assert [
        value["id"]
        for value in cache.filter_items(
            "server",
            "workspace",
            "project",
            updated_at_from="2026-07-30T11:00:00.250000Z",
            updated_at_to="2026-07-30T11:00:00.500000Z",
        )["results"]
    ] == ["fractional"]


def test_cache_is_shared_across_instances_and_upserts_newer_data(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = WorkItemCache(path)
    second = WorkItemCache(path)

    first.upsert_items("server", "workspace", "project", [item("one", "2026-07-30T10:00:00Z", priority="low")])
    second.upsert_items("server", "workspace", "project", [item("one", "2026-07-30T11:00:00Z", priority="high")])

    result = first.filter_items("server", "workspace", "project", priority="high", limit=10)
    assert [value["id"] for value in result["results"]] == ["one"]
    assert second.watermark("server", "workspace", "project") == "2026-07-30T11:00:00.000000Z"


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

    assert [value["id"] for value in result["results"]] == ["match"]


def test_cache_query_treats_like_metacharacters_as_literals(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    values = []
    for item_id, name in (
        ("percent", "Use 100%"),
        ("underscore", "literal_name"),
        ("slash", r"path\name"),
        ("other", "100x"),
    ):
        value = item(item_id, "2026-07-30T10:00:00Z")
        value["name"] = name
        values.append(value)
    cache.upsert_items("server", "workspace", "project", values)

    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", query="100%")["results"]] == [
        "percent"
    ]
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", query="_")["results"]] == [
        "underscore"
    ]
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", query="\\")["results"]] == [
        "slash"
    ]


def test_cache_filters_text_and_scalar_values(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    first = item("one", "2026-07-30T10:00:00Z", priority="high", state_id="s1", state_group="started")
    first.update({"name": "Alpha task", "sequence_id": 12, "type_id": "bug"})
    second = item("two", "2026-07-30T11:00:00Z", priority="low", state_id="s2", state_group="completed")
    second.update({"name": "Beta task", "sequence_id": 23, "type_id": "feature"})
    cache.upsert_items("server", "workspace", "project", [first, second])

    assert (
        cache.filter_items(
            "server",
            "workspace",
            "project",
            query="ALPHA",
            priority="high",
            priorities=["high", "urgent"],
            state_id="s1",
            state_ids=["s1"],
            state_group="started",
            state_groups=["started"],
            type_id="bug",
        )["total_count"]
        == 1
    )
    assert cache.filter_items("server", "workspace", "project", query="23")["results"][0]["id"] == "two"


def test_cache_filters_relation_any_all_and_relation_ids(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    first = item("one", "2026-07-30T10:00:00Z", assignees=["a1", "a2"], labels=["l1", "l2"])
    first.update(
        {
            "parent": "p1",
            "cycle": {"id": "c1"},
            "modules": ["m1", "m2"],
            "created_by": "u1",
        }
    )
    cache.upsert_items("server", "workspace", "project", [first, item("two", "2026-07-30T11:00:00Z")])

    assert (
        cache.filter_items(
            "server",
            "workspace",
            "project",
            assignee_ids=["a1", "missing"],
            label_ids=["l1", "l2"],
            relation_match="any",
            parent_id="p1",
            cycle_id="c1",
            module_id="m2",
            created_by="u1",
        )["total_count"]
        == 1
    )
    assert (
        cache.filter_items(
            "server", "workspace", "project", assignee_ids=["a1", "a2"], label_ids=["l1", "l2"], relation_match="all"
        )["total_count"]
        == 1
    )
    assert (
        cache.filter_items("server", "workspace", "project", assignee_ids=["a1", "missing"], relation_match="all")[
            "total_count"
        ]
        == 0
    )


def test_cache_filters_inclusive_ranges_flags_and_overdue(tmp_path, monkeypatch):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    first = item("one", "2026-07-30T10:00:00Z", assignees=["a1"], labels=["l1"])
    first.update(
        {
            "sequence_id": 10,
            "created_at": "2026-07-01T00:00:00Z",
            "start_date": "2026-07-02",
            "target_date": "2026-07-30",
            "completed_at": "2026-07-30T10:00:00Z",
            "parent": "p1",
            "is_draft": True,
        }
    )
    overdue = item("overdue", "2026-07-31T10:00:00Z")
    overdue.update({"target_date": "2026-07-30", "sequence_id": 20})
    cache.upsert_items("server", "workspace", "project", [first, overdue])
    monkeypatch.setattr("plane_mcp.work_item_cache._utc_today", lambda: "2026-07-31")

    result = cache.filter_items(
        "server",
        "workspace",
        "project",
        created_at_from="2026-07-01T00:00:00Z",
        created_at_to="2026-07-01T00:00:00Z",
        updated_at_from="2026-07-30T10:00:00Z",
        updated_at_to="2026-07-30T10:00:00Z",
        start_date_from="2026-07-02",
        start_date_to="2026-07-02",
        target_date_from="2026-07-30",
        target_date_to="2026-07-30",
        completed_at_from="2026-07-30T10:00:00Z",
        completed_at_to="2026-07-30T10:00:00Z",
        sequence_id_from=10,
        sequence_id_to=10,
        is_draft=True,
        has_assignee=True,
        has_label=True,
        has_parent=True,
    )
    assert [value["id"] for value in result["results"]] == ["one"]
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", overdue=True)["results"]] == [
        "overdue",
        "one",
    ]
    assert cache.filter_items("server", "workspace", "project", has_assignee=False)["total_count"] == 1


def test_overdue_false_includes_null_target_and_state(tmp_path, monkeypatch):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    no_target = item("no-target", "2026-07-30T10:00:00Z")
    no_state = item("no-state", "2026-07-30T11:00:00Z", state_id=None, state_group=None)
    no_state["target_date"] = "2026-07-29"
    cache.upsert_items("server", "workspace", "project", [no_target, no_state])
    monkeypatch.setattr("plane_mcp.work_item_cache._utc_today", lambda: "2026-07-31")

    result = cache.filter_items("server", "workspace", "project", overdue=False)

    assert {value["id"] for value in result["results"]} == {"no-target", "no-state"}


def test_cache_sorts_paginates_and_counts_before_pagination(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    values = [item("b", "2026-07-30T10:00:00Z"), item("a", "2026-07-30T10:00:00Z"), item("c", "2026-07-30T12:00:00Z")]
    cache.upsert_items("server", "workspace", "project", values)

    result = cache.filter_items(
        "server", "workspace", "project", sort_by="updated_at", sort_direction="asc", offset=1, limit=1
    )

    assert [value["id"] for value in result["results"]] == ["b"]
    assert result["total_count"] == 3


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"created_at_from": "bad"}, "created_at_from"),
        ({"start_date_from": "2026-02-30"}, "start_date_from"),
        ({"start_date_from": "2026-07-01junk"}, "start_date_from"),
        ({"target_date_to": "2026-07-01T12:00:00Z"}, "target_date"),
        ({"created_at_from": "2026-07-01"}, "created_at_from"),
        ({"updated_at_to": "2026-07-01T12:00:00Zjunk"}, "updated_at"),
        ({"sequence_id_from": 2, "sequence_id_to": 1}, "sequence_id"),
        ({"updated_at_from": "2026-07-02T00:00:00Z", "updated_at_to": "2026-07-01T00:00:00Z"}, "updated_at"),
        ({"relation_match": "some"}, "relation_match"),
        ({"sort_direction": "sideways"}, "sort_direction"),
        ({"sort_by": "id"}, "sort_by"),
        ({"priorities": ["invalid"]}, "priorities"),
        ({"state_groups": ["invalid"]}, "state_groups"),
        ({"state_ids": [str(value) for value in range(101)]}, "state_ids.*100"),
        ({"assignee_ids": [str(value) for value in range(101)]}, "assignee_ids.*100"),
        ({"label_ids": [str(value) for value in range(101)]}, "label_ids.*100"),
        ({"offset": -1}, "offset"),
        ({"limit": 0}, "limit"),
    ],
)
def test_cache_rejects_invalid_filters(tmp_path, arguments, message):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")

    with pytest.raises(ValueError, match=message):
        cache.filter_items("server", "workspace", "project", **arguments)


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
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", limit=10)["results"]] == [
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
    assert result == {
        "status": "synced",
        "pages_fetched": 2,
        "items_upserted": 2,
        "watermark": "2026-07-30T12:00:00.000000Z",
    }
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", limit=10)["results"]] == [
        "new",
        "old",
        "same",
    ]


def test_full_sync_continuation_retains_mode_and_generation_after_24_hours(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    cache.upsert_items("server", "workspace", "project", [item("deleted", "2026-07-29T10:00:00Z")])
    cache.set_sync_state(
        "server",
        "workspace",
        "project",
        initialized=True,
        cursor=None,
        watermark="2026-07-29T10:00:00Z",
        full_synced_at=0,
    )
    pages = {
        None: {
            "results": [item("new", "2026-07-31T10:00:00Z")],
            "next_cursor": "page-2",
            "next_page_results": True,
        },
        "page-2": {"results": [item("kept", "2026-07-30T10:00:00Z")], "next_page_results": False},
    }

    first = sync_work_items(
        cache,
        "server",
        "workspace",
        "project",
        lambda cursor: pages[cursor],
        owner="generation",
        now=100000,
        max_pages=1,
    )
    second = sync_work_items(
        cache, "server", "workspace", "project", lambda cursor: pages[cursor], owner="other", now=200000, max_pages=1
    )

    assert first["status"] == "partial"
    assert second["status"] == "synced"
    assert {value["id"] for value in cache.filter_items("server", "workspace", "project")["results"]} == {
        "new",
        "kept",
    }


def test_sync_renews_lease_with_current_clock(tmp_path):
    cache = WorkItemCache(tmp_path / "cache.sqlite3")
    times = iter([150.0])
    pages = {
        None: {"results": [item("one", "2026-07-31T10:00:00Z")], "next_cursor": "next", "next_page_results": True},
        "next": {"results": [], "next_page_results": False},
    }
    renewed = []
    original = cache.renew_lease

    def renew(server, workspace, project_id, owner, now, ttl):
        renewed.append(now)
        original(server, workspace, project_id, owner, now, ttl)

    cache.renew_lease = renew
    sync_work_items(
        cache,
        "server",
        "workspace",
        "project",
        lambda cursor: pages[cursor],
        owner="owner",
        now=100,
        clock=lambda: next(times),
    )

    assert renewed == [150.0]


def test_incremental_sync_compares_timezone_offsets_as_instants(tmp_path):
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
    page = {
        "results": [
            item("new", "2026-07-30T12:30:00+02:00"),
            item("older", "2026-07-30T11:30:00+02:00"),
        ],
        "next_page_results": False,
    }

    result = sync_work_items(cache, "server", "workspace", "project", lambda cursor: page, owner="owner", now=101)

    assert result["items_upserted"] == 1
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project")["results"]] == [
        "new",
        "old",
    ]


def test_heartbeat_keeps_lease_exclusive_during_slow_fetch(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = WorkItemCache(path)
    second = WorkItemCache(path)
    fetching = threading.Event()
    release_fetch = threading.Event()
    result = {}

    def fetch(cursor):
        fetching.set()
        assert release_fetch.wait(2)
        return {"results": [item("one", "2026-07-30T10:00:00Z")], "next_page_results": False}

    def run_sync():
        result.update(
            sync_work_items(
                first,
                "server",
                "workspace",
                "project",
                fetch,
                owner="owner-1",
                now=time.time(),
                lease_seconds=0.5,
                heartbeat_interval=0.02,
            )
        )

    thread = threading.Thread(target=run_sync)
    thread.start()
    assert fetching.wait(1)
    time.sleep(0.7)

    assert second.acquire_lease("server", "workspace", "project", "owner-2", now=time.time(), ttl=1) is False

    release_fetch.set()
    thread.join(2)
    assert not thread.is_alive()
    assert result["status"] == "synced"


def test_heartbeat_stops_and_releases_lease_after_fetch_exception(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = WorkItemCache(path)
    second = WorkItemCache(path)

    with pytest.raises(RuntimeError, match="boom"):
        sync_work_items(
            first,
            "server",
            "workspace",
            "project",
            lambda cursor: (_ for _ in ()).throw(RuntimeError("boom")),
            owner="owner-1",
            now=time.time(),
            lease_seconds=0.15,
            heartbeat_interval=0.03,
        )

    assert second.acquire_lease("server", "workspace", "project", "owner-2", now=time.time(), ttl=1) is True


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
    assert cache.watermark("server", "workspace", "project") == "2026-07-30T12:00:00.000000Z"
    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", limit=10)["results"]] == [
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

    assert [value["id"] for value in cache.filter_items("server", "workspace", "project", limit=10)["results"]] == [
        "kept"
    ]


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

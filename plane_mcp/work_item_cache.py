"""Shared SQLite cache for lightweight work item records."""

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Any

FULL_SYNC_INTERVAL_SECONDS = 86400
LEASE_SECONDS = 120
CACHE_SCHEMA_VERSION = 1
MAX_MULTI_VALUES = 100
PRIORITIES = {"urgent", "high", "medium", "low", "none"}
STATE_GROUPS = {"backlog", "unstarted", "started", "completed", "cancelled"}
SORT_FIELDS = {"updated_at", "created_at", "sequence_id", "priority", "start_date", "target_date", "name"}
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def _relation_id(value: Any) -> Any:
    return value.get("id") if isinstance(value, dict) else value


def _normalize_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    if not TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError(f"invalid timestamp: {value}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not DATE_PATTERN.fullmatch(value):
        raise ValueError(f"invalid date: {value}")
    return date.fromisoformat(value).isoformat()


def _normalize_stored_date(value: str | None) -> str | None:
    if value is None:
        return None
    if DATE_PATTERN.fullmatch(value):
        return _normalize_date(value)
    if TIMESTAMP_PATTERN.fullmatch(value):
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value[:10]
    raise ValueError(f"invalid date: {value}")


def _normalize_existing(value: str | None, normalizer: Any) -> str | None:
    if value is None:
        return None
    try:
        return normalizer(value)
    except (TypeError, ValueError):
        return value


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def sync_work_items(
    cache: "WorkItemCache",
    server: str,
    workspace: str,
    project_id: str,
    fetch_page: Any,
    owner: str,
    now: float,
    max_pages: int = 1000,
    clock: Any = time.time,
    lease_seconds: float = LEASE_SECONDS,
    heartbeat_interval: float | None = None,
    force_full: bool = False,
) -> dict[str, Any]:
    if not cache.acquire_lease(server, workspace, project_id, owner, now, lease_seconds):
        return {
            "status": "busy",
            "pages_fetched": 0,
            "items_upserted": 0,
            "watermark": cache.watermark(server, workspace, project_id),
        }

    stop_heartbeat = Event()
    interval = heartbeat_interval if heartbeat_interval is not None else lease_seconds / 3

    def heartbeat() -> None:
        while not stop_heartbeat.wait(interval):
            cache.renew_lease(server, workspace, project_id, owner, clock(), lease_seconds)

    heartbeat_thread = Thread(target=heartbeat, name="plane-work-item-cache-lease", daemon=True)
    heartbeat_thread.start()
    state = cache.sync_state(server, workspace, project_id)
    initialized = bool(state and state["initialized"])
    continuing = bool(state and state["cursor"] and not force_full)
    full_sync = force_full or (
        bool(state and state["generation"])
        if continuing
        else (not initialized or not state or now - state["full_synced_at"] >= FULL_SYNC_INTERVAL_SECONDS)
    )
    cursor = state["cursor"] if continuing else None
    baseline = state["scan_watermark"] if continuing else state["watermark"] if initialized and not full_sync else None
    generation = state["generation"] if continuing and state["generation"] else owner if full_sync else None
    pages_fetched = 0
    items_upserted = 0
    completed = False
    expected_total = None
    raw_scanned = 0

    try:
        while pages_fetched < max_pages:
            page = fetch_page(cursor)
            pages_fetched += 1
            page_items = page.get("results") or []
            if force_full:
                total_count = page.get("total_count")
                if not isinstance(total_count, int) or total_count < 0:
                    raise RuntimeError("forced full sync requires a valid total_count")
                if expected_total is None:
                    expected_total = total_count
                elif total_count != expected_total:
                    raise RuntimeError("forced full sync total changed during pagination")
                raw_scanned += len(page_items)
            accepted = [
                item
                for item in page_items
                if baseline is None or _normalize_timestamp(item.get("updated_at")) >= baseline
            ]
            if accepted:
                cache.upsert_items(server, workspace, project_id, accepted, generation=generation)
                items_upserted += len(accepted)

            reached_older = baseline is not None and len(accepted) < len(page_items)
            next_cursor = page.get("next_cursor") if page.get("next_page_results") else None
            if reached_older or not next_cursor:
                cursor = None
                completed = True
                break

            cursor = next_cursor
            cache.set_sync_state(
                server,
                workspace,
                project_id,
                initialized,
                cursor,
                state["watermark"] if state else None,
                baseline,
                generation,
                state["full_synced_at"] if state else 0,
            )
            cache.renew_lease(server, workspace, project_id, owner, clock(), lease_seconds)

        latest_watermark = cache.watermark(server, workspace, project_id)
        if completed and force_full and raw_scanned != expected_total:
            raise RuntimeError("forced full sync raw count mismatch")
        if completed:
            initialized = True
            if full_sync and generation:
                cache.delete_other_generations(server, workspace, project_id, generation)
                latest_watermark = cache.watermark(server, workspace, project_id)
            cache.set_sync_state(
                server,
                workspace,
                project_id,
                True,
                None,
                latest_watermark,
                None,
                None,
                now if full_sync else state["full_synced_at"] if state else now,
            )
        else:
            cache.set_sync_state(
                server,
                workspace,
                project_id,
                initialized,
                cursor,
                state["watermark"] if state else None,
                baseline,
                generation,
                state["full_synced_at"] if state else 0,
            )

        return {
            "status": "synced" if completed else "partial",
            "pages_fetched": pages_fetched,
            "items_upserted": items_upserted,
            "watermark": latest_watermark,
        }
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join()
        cache.release_lease(server, workspace, project_id, owner)


def default_cache_path() -> Path:
    configured = os.getenv("PLANE_MCP_CACHE_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "plane-mcp-server" / "work-items.sqlite3"


class _WorkItemCacheSnapshot:
    def __init__(self, cache: "WorkItemCache", connection: sqlite3.Connection):
        self._cache = cache
        self._snapshot_connection = connection

    def filter_items(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return WorkItemCache.filter_items(self, *args, **kwargs)


class WorkItemCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS work_items (
                    server TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT,
                    sequence_id INTEGER,
                    priority TEXT,
                    state_id TEXT,
                    state_group TEXT,
                    assignee_ids TEXT NOT NULL,
                    label_ids TEXT NOT NULL,
                    type_id TEXT,
                    parent_id TEXT,
                    cycle_id TEXT,
                    module_ids TEXT NOT NULL DEFAULT '[]',
                    created_by TEXT,
                    created_at TEXT,
                    updated_at TEXT NOT NULL,
                    start_date TEXT,
                    target_date TEXT,
                    completed_at TEXT,
                    is_draft INTEGER,
                    generation TEXT,
                    PRIMARY KEY (server, workspace, project_id, id)
                );
                CREATE INDEX IF NOT EXISTS work_items_filter_idx
                    ON work_items (server, workspace, project_id, priority, state_id, state_group, updated_at DESC);
                CREATE TABLE IF NOT EXISTS sync_state (
                    server TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    initialized INTEGER NOT NULL,
                    cursor TEXT,
                    watermark TEXT,
                    scan_watermark TEXT,
                    generation TEXT,
                    full_synced_at REAL NOT NULL,
                    PRIMARY KEY (server, workspace, project_id)
                );
                CREATE TABLE IF NOT EXISTS sync_leases (
                    server TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (server, workspace, project_id)
                );
                """
            )
            work_item_columns = {row["name"] for row in connection.execute("PRAGMA table_info(work_items)")}
            migrations = {
                "type_id": "TEXT",
                "parent_id": "TEXT",
                "cycle_id": "TEXT",
                "module_ids": "TEXT NOT NULL DEFAULT '[]'",
                "created_by": "TEXT",
                "created_at": "TEXT",
                "start_date": "TEXT",
                "target_date": "TEXT",
                "completed_at": "TEXT",
                "is_draft": "INTEGER",
                "generation": "TEXT",
            }
            for column, definition in migrations.items():
                if column not in work_item_columns:
                    connection.execute(f"ALTER TABLE work_items ADD COLUMN {column} {definition}")
            sync_state_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sync_state)")}
            if "scan_watermark" not in sync_state_columns:
                connection.execute("ALTER TABLE sync_state ADD COLUMN scan_watermark TEXT")
            if "generation" not in sync_state_columns:
                connection.execute("ALTER TABLE sync_state ADD COLUMN generation TEXT")
            if "full_synced_at" not in sync_state_columns:
                connection.execute("ALTER TABLE sync_state ADD COLUMN full_synced_at REAL NOT NULL DEFAULT 0")
            if schema_version >= CACHE_SCHEMA_VERSION:
                return
            temporal_columns = {
                "created_at": _normalize_timestamp,
                "updated_at": _normalize_timestamp,
                "start_date": _normalize_stored_date,
                "target_date": _normalize_stored_date,
                "completed_at": _normalize_timestamp,
            }
            temporal_projection = (
                "server, workspace, project_id, id, created_at, updated_at, start_date, target_date, completed_at"
            )
            rows = connection.execute(f"SELECT {temporal_projection} FROM work_items").fetchall()
            for row in rows:
                normalized = {
                    column: _normalize_existing(row[column], normalizer)
                    for column, normalizer in temporal_columns.items()
                }
                connection.execute(
                    """
                    UPDATE work_items SET created_at=?, updated_at=?, start_date=?, target_date=?, completed_at=?
                    WHERE server=? AND workspace=? AND project_id=? AND id=?
                    """,
                    (
                        normalized["created_at"],
                        normalized["updated_at"],
                        normalized["start_date"],
                        normalized["target_date"],
                        normalized["completed_at"],
                        row["server"],
                        row["workspace"],
                        row["project_id"],
                        row["id"],
                    ),
                )
            for column in ("watermark", "scan_watermark"):
                rows = connection.execute(
                    f"SELECT server, workspace, project_id, {column} FROM sync_state WHERE {column} IS NOT NULL"
                ).fetchall()
                for row in rows:
                    connection.execute(
                        f"UPDATE sync_state SET {column}=? WHERE server=? AND workspace=? AND project_id=?",
                        (
                            _normalize_existing(row[column], _normalize_timestamp),
                            row["server"],
                            row["workspace"],
                            row["project_id"],
                        ),
                    )
            if connection.execute("SELECT 1 FROM work_items LIMIT 1").fetchone():
                connection.execute("DELETE FROM sync_state")
            connection.execute(f"PRAGMA user_version={CACHE_SCHEMA_VERSION}")

    def upsert_items(
        self,
        server: str,
        workspace: str,
        project_id: str,
        items: list[dict[str, Any]],
        generation: str | None = None,
    ) -> None:
        records = []
        for item in items:
            state = item.get("state")
            state_id = _relation_id(state)
            state_group = state.get("group") if isinstance(state, dict) else item.get("state_group")
            assignees = [_relation_id(value) for value in item.get("assignees") or []]
            labels = [_relation_id(value) for value in item.get("labels") or []]
            modules = [_relation_id(value) for value in item.get("modules") or item.get("module_ids") or []]
            records.append(
                (
                    server,
                    workspace,
                    project_id,
                    item["id"],
                    item.get("name"),
                    item.get("sequence_id"),
                    item.get("priority"),
                    state_id,
                    state_group,
                    json.dumps([value for value in assignees if value]),
                    json.dumps([value for value in labels if value]),
                    _relation_id(item.get("type_id") or item.get("type")),
                    _relation_id(item.get("parent_id") or item.get("parent")),
                    _relation_id(item.get("cycle_id") or item.get("cycle")),
                    json.dumps([value for value in modules if value]),
                    _relation_id(item.get("created_by")),
                    _normalize_timestamp(item.get("created_at")),
                    _normalize_timestamp(item["updated_at"]),
                    _normalize_stored_date(item.get("start_date")),
                    _normalize_stored_date(item.get("target_date")),
                    _normalize_timestamp(item.get("completed_at")),
                    item.get("is_draft"),
                    generation,
                )
            )
        columns = (
            "server, workspace, project_id, id, name, sequence_id, priority, state_id, state_group, "
            "assignee_ids, label_ids, type_id, parent_id, cycle_id, module_ids, created_by, created_at, "
            "updated_at, start_date, target_date, completed_at, is_draft, generation"
        )
        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in (
                "name",
                "sequence_id",
                "priority",
                "state_id",
                "state_group",
                "assignee_ids",
                "label_ids",
                "type_id",
                "parent_id",
                "cycle_id",
                "module_ids",
                "created_by",
                "created_at",
                "updated_at",
                "start_date",
                "target_date",
                "completed_at",
                "is_draft",
            )
        )
        with self._connect() as connection:
            connection.executemany(
                f"""
                INSERT INTO work_items ({columns}) VALUES ({", ".join("?" for _ in range(23))})
                ON CONFLICT(server, workspace, project_id, id) DO UPDATE SET
                    {updates}, generation=COALESCE(excluded.generation, work_items.generation)
                WHERE excluded.updated_at >= work_items.updated_at
                """,
                records,
            )

    def sync_state(self, server: str, workspace: str, project_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM sync_state WHERE server=? AND workspace=? AND project_id=?",
                (server, workspace, project_id),
            ).fetchone()

    def set_sync_state(
        self,
        server: str,
        workspace: str,
        project_id: str,
        initialized: bool,
        cursor: str | None,
        watermark: str | None,
        scan_watermark: str | None = None,
        generation: str | None = None,
        full_synced_at: float = 0,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sync_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server, workspace, project_id) DO UPDATE SET
                    initialized=excluded.initialized,
                    cursor=excluded.cursor,
                    watermark=excluded.watermark,
                    scan_watermark=excluded.scan_watermark,
                    generation=excluded.generation,
                    full_synced_at=excluded.full_synced_at
                """,
                (
                    server,
                    workspace,
                    project_id,
                    initialized,
                    cursor,
                    _normalize_timestamp(watermark),
                    _normalize_timestamp(scan_watermark),
                    generation,
                    full_synced_at,
                ),
            )

    def watermark(self, server: str, workspace: str, project_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(updated_at) AS watermark FROM work_items WHERE server=? AND workspace=? AND project_id=?",
                (server, workspace, project_id),
            ).fetchone()
        return row["watermark"] if row else None

    def delete_other_generations(self, server: str, workspace: str, project_id: str, generation: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM work_items WHERE server=? AND workspace=? AND project_id=? AND generation IS NOT ?",
                (server, workspace, project_id, generation),
            )

    @contextmanager
    def read_snapshot(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            connection.execute("SELECT COUNT(*) FROM work_items").fetchone()
            yield _WorkItemCacheSnapshot(self, connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def filter_items(
        self,
        server: str,
        workspace: str,
        project_id: str,
        priority: str | None = None,
        state_id: str | None = None,
        state_group: str | None = None,
        assignee_id: str | None = None,
        label_id: str | None = None,
        query: str | None = None,
        priorities: list[str] | None = None,
        state_ids: list[str] | None = None,
        state_groups: list[str] | None = None,
        assignee_ids: list[str] | None = None,
        label_ids: list[str] | None = None,
        relation_match: str = "any",
        type_id: str | None = None,
        parent_id: str | None = None,
        cycle_id: str | None = None,
        module_id: str | None = None,
        created_by: str | None = None,
        created_at_from: str | None = None,
        created_at_to: str | None = None,
        updated_at_from: str | None = None,
        updated_at_to: str | None = None,
        start_date_from: str | None = None,
        start_date_to: str | None = None,
        target_date_from: str | None = None,
        target_date_to: str | None = None,
        completed_at_from: str | None = None,
        completed_at_to: str | None = None,
        sequence_id_from: int | None = None,
        sequence_id_to: int | None = None,
        is_draft: bool | None = None,
        has_assignee: bool | None = None,
        has_label: bool | None = None,
        has_parent: bool | None = None,
        overdue: bool | None = None,
        sort_by: str = "updated_at",
        sort_direction: str = "desc",
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        if relation_match not in {"any", "all"}:
            raise ValueError("relation_match must be 'any' or 'all'")
        if sort_by not in SORT_FIELDS:
            raise ValueError(f"sort_by must be one of {sorted(SORT_FIELDS)}")
        if sort_direction not in {"asc", "desc"}:
            raise ValueError("sort_direction must be 'asc' or 'desc'")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit < 1:
            raise ValueError("limit must be positive")
        multi_values = {
            "priorities": priorities,
            "state_ids": state_ids,
            "state_groups": state_groups,
            "assignee_ids": assignee_ids,
            "label_ids": label_ids,
        }
        for name, choices in multi_values.items():
            if choices and len(choices) > MAX_MULTI_VALUES:
                raise ValueError(f"{name} must contain at most {MAX_MULTI_VALUES} values")
        for name, choices, allowed in (
            ("priorities", priorities, PRIORITIES),
            ("state_groups", state_groups, STATE_GROUPS),
        ):
            if choices and not set(choices) <= allowed:
                raise ValueError(f"{name} contains invalid values")
        if priority is not None and priority not in PRIORITIES:
            raise ValueError("priority is invalid")
        if state_group is not None and state_group not in STATE_GROUPS:
            raise ValueError("state_group is invalid")

        timestamp_ranges = {
            "created_at": (created_at_from, created_at_to),
            "updated_at": (updated_at_from, updated_at_to),
            "completed_at": (completed_at_from, completed_at_to),
        }
        date_ranges = {
            "start_date": (start_date_from, start_date_to),
            "target_date": (target_date_from, target_date_to),
        }
        normalized_ranges: dict[str, tuple[str | None, str | None]] = {}
        for column, bounds in timestamp_ranges.items():
            try:
                normalized_ranges[column] = tuple(_normalize_timestamp(value) for value in bounds)
            except ValueError as error:
                raise ValueError(f"invalid {column}_from or {column}_to") from error
        for column, bounds in date_ranges.items():
            try:
                normalized_ranges[column] = tuple(_normalize_date(value) for value in bounds)
            except ValueError as error:
                raise ValueError(f"invalid {column}_from or {column}_to") from error
        normalized_ranges["sequence_id"] = (sequence_id_from, sequence_id_to)
        for column, (lower, upper) in normalized_ranges.items():
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"{column}_from must not exceed {column}_to")

        clauses = ["server=?", "workspace=?", "project_id=?"]
        values: list[Any] = [server, workspace, project_id]

        def add_scalar(column: str, value: Any) -> None:
            if value is not None:
                clauses.append(f"{column}=?")
                values.append(value)

        for column, value in (
            ("priority", priority),
            ("state_id", state_id),
            ("state_group", state_group),
            ("type_id", type_id),
            ("parent_id", parent_id),
            ("cycle_id", cycle_id),
            ("created_by", created_by),
        ):
            add_scalar(column, value)
        for column, choices in (("priority", priorities), ("state_id", state_ids), ("state_group", state_groups)):
            if choices:
                clauses.append(f"{column} IN ({', '.join('?' for _ in choices)})")
                values.extend(choices)
        if query:
            escaped_query = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(LOWER(name) LIKE ? ESCAPE '\\' OR CAST(sequence_id AS TEXT)=?)")
            values.extend((f"%{escaped_query}%", query))

        def add_relation(column: str, relation_values: list[str] | None) -> None:
            if not relation_values:
                return
            predicates = []
            for value in relation_values:
                predicates.append(f"EXISTS (SELECT 1 FROM json_each({column}) WHERE value=?)")
                values.append(value)
            clauses.append(f"({' AND '.join(predicates) if relation_match == 'all' else ' OR '.join(predicates)})")

        add_relation("assignee_ids", [assignee_id] if assignee_id is not None else None)
        add_relation("label_ids", [label_id] if label_id is not None else None)
        add_relation("assignee_ids", assignee_ids)
        add_relation("label_ids", label_ids)
        add_relation("module_ids", [module_id] if module_id is not None else None)
        for column, (lower, upper) in normalized_ranges.items():
            if lower is not None:
                clauses.append(f"{column}>=?")
                values.append(lower)
            if upper is not None:
                clauses.append(f"{column}<=?")
                values.append(upper)
        for column, flag in (("assignee_ids", has_assignee), ("label_ids", has_label)):
            if flag is not None:
                clauses.append(f"json_array_length({column}) {'>' if flag else '='} 0")
        if has_parent is not None:
            clauses.append(f"parent_id IS {'NOT ' if has_parent else ''}NULL")
        if is_draft is not None:
            clauses.append("is_draft=?")
            values.append(is_draft)
        if overdue is not None:
            overdue_clause = (
                "target_date IS NOT NULL AND target_date<? AND state_group NOT IN ('completed', 'cancelled')"
            )
            clauses.append(f"({overdue_clause})" if overdue else f"COALESCE(({overdue_clause}), 0) = 0")
            values.append(_utc_today())

        where = " AND ".join(clauses)
        connection_context = (
            self._connect() if not hasattr(self, "_snapshot_connection") else nullcontext(self._snapshot_connection)
        )
        with connection_context as connection:
            total_count = connection.execute(f"SELECT COUNT(*) FROM work_items WHERE {where}", values).fetchone()[0]
            order = f"{sort_by} {sort_direction.upper()}, id ASC"
            rows = connection.execute(
                f"SELECT * FROM work_items WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
        results = []
        for row in rows:
            record = {
                key: row[key] for key in row.keys() if key not in {"server", "workspace", "project_id", "generation"}
            }
            for key in ("assignee_ids", "label_ids", "module_ids"):
                record[key] = json.loads(record[key])
            record["is_draft"] = bool(record["is_draft"]) if record["is_draft"] is not None else None
            results.append(record)
        return {"results": results, "total_count": total_count}

    def release_lease(self, server: str, workspace: str, project_id: str, owner: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sync_leases WHERE server=? AND workspace=? AND project_id=? AND owner=?",
                (server, workspace, project_id, owner),
            )

    def renew_lease(
        self,
        server: str,
        workspace: str,
        project_id: str,
        owner: str,
        now: float,
        ttl: float,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sync_leases SET expires_at=? WHERE server=? AND workspace=? AND project_id=? AND owner=?",
                (now + ttl, server, workspace, project_id, owner),
            )

    def acquire_lease(
        self,
        server: str,
        workspace: str,
        project_id: str,
        owner: str,
        now: float,
        ttl: float,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner, expires_at FROM sync_leases WHERE server=? AND workspace=? AND project_id=?",
                (server, workspace, project_id),
            ).fetchone()
            if row and row["expires_at"] > now and row["owner"] != owner:
                return False
            connection.execute(
                """
                INSERT INTO sync_leases VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(server, workspace, project_id) DO UPDATE SET
                    owner=excluded.owner, expires_at=excluded.expires_at
                """,
                (server, workspace, project_id, owner, now + ttl),
            )
        return True

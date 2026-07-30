"""Shared SQLite cache for lightweight work item records."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

FULL_SYNC_INTERVAL_SECONDS = 86400
LEASE_SECONDS = 120


def sync_work_items(
    cache: "WorkItemCache",
    server: str,
    workspace: str,
    project_id: str,
    fetch_page: Any,
    owner: str,
    now: float,
    max_pages: int = 1000,
) -> dict[str, Any]:
    if not cache.acquire_lease(server, workspace, project_id, owner, now, LEASE_SECONDS):
        return {
            "status": "busy",
            "pages_fetched": 0,
            "items_upserted": 0,
            "watermark": cache.watermark(server, workspace, project_id),
        }

    state = cache.sync_state(server, workspace, project_id)
    initialized = bool(state and state["initialized"])
    full_sync = not initialized or not state or now - state["full_synced_at"] >= FULL_SYNC_INTERVAL_SECONDS
    continuing = bool(state and state["cursor"])
    cursor = state["cursor"] if continuing else None
    baseline = state["scan_watermark"] if continuing else state["watermark"] if initialized and not full_sync else None
    generation = state["generation"] if continuing and state["generation"] else owner if full_sync else None
    pages_fetched = 0
    items_upserted = 0
    completed = False

    try:
        while pages_fetched < max_pages:
            page = fetch_page(cursor)
            pages_fetched += 1
            page_items = page.get("results") or []
            accepted = [item for item in page_items if baseline is None or item.get("updated_at", "") >= baseline]
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
            cache.renew_lease(server, workspace, project_id, owner, now, LEASE_SECONDS)

        latest_watermark = cache.watermark(server, workspace, project_id)
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
        cache.release_lease(server, workspace, project_id, owner)


def default_cache_path() -> Path:
    configured = os.getenv("PLANE_MCP_CACHE_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "plane-mcp-server" / "work-items.sqlite3"


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
                    updated_at TEXT NOT NULL,
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
            if "generation" not in work_item_columns:
                connection.execute("ALTER TABLE work_items ADD COLUMN generation TEXT")
            sync_state_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sync_state)")}
            if "scan_watermark" not in sync_state_columns:
                connection.execute("ALTER TABLE sync_state ADD COLUMN scan_watermark TEXT")
            if "generation" not in sync_state_columns:
                connection.execute("ALTER TABLE sync_state ADD COLUMN generation TEXT")
            if "full_synced_at" not in sync_state_columns:
                connection.execute("ALTER TABLE sync_state ADD COLUMN full_synced_at REAL NOT NULL DEFAULT 0")

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
            state_id = state.get("id") if isinstance(state, dict) else state
            state_group = state.get("group") if isinstance(state, dict) else None
            assignees = [value.get("id") if isinstance(value, dict) else value for value in item.get("assignees") or []]
            labels = [value.get("id") if isinstance(value, dict) else value for value in item.get("labels") or []]
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
                    item["updated_at"],
                    generation,
                )
            )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO work_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server, workspace, project_id, id) DO UPDATE SET
                    name=excluded.name,
                    sequence_id=excluded.sequence_id,
                    priority=excluded.priority,
                    state_id=excluded.state_id,
                    state_group=excluded.state_group,
                    assignee_ids=excluded.assignee_ids,
                    label_ids=excluded.label_ids,
                    updated_at=excluded.updated_at,
                    generation=COALESCE(excluded.generation, work_items.generation)
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
                    watermark,
                    scan_watermark,
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
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        clauses = ["server=?", "workspace=?", "project_id=?"]
        values: list[Any] = [server, workspace, project_id]
        for column, value in (("priority", priority), ("state_id", state_id), ("state_group", state_group)):
            if value is not None:
                clauses.append(f"{column}=?")
                values.append(value)
        rows = []
        with self._connect() as connection:
            candidates = connection.execute(
                f"SELECT * FROM work_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC",
                values,
            )
            for row in candidates:
                assignee_ids = json.loads(row["assignee_ids"])
                label_ids = json.loads(row["label_ids"])
                if assignee_id is not None and assignee_id not in assignee_ids:
                    continue
                if label_id is not None and label_id not in label_ids:
                    continue
                rows.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "sequence_id": row["sequence_id"],
                        "priority": row["priority"],
                        "state_id": row["state_id"],
                        "state_group": row["state_group"],
                        "assignee_ids": assignee_ids,
                        "label_ids": label_ids,
                        "updated_at": row["updated_at"],
                    }
                )
                if len(rows) >= limit:
                    break
        return rows

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

"""Complete read-only weekly report collection."""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp import FastMCP
from plane.models.query_params import WorkItemQueryParams

from plane_mcp.client import get_plane_cache_scope, get_plane_client_context
from plane_mcp.tools.work_item_activities import _activity_matches, _activity_payload, _page_payload, _parse_timestamp
from plane_mcp.work_item_cache import LEASE_SECONDS, WorkItemCache, sync_work_items

DEFAULT_SYNC_TIMEOUT_SECONDS = LEASE_SECONDS + 60.0


def _timestamp(value: str, name: str) -> datetime:
    parsed = _parse_timestamp(value, name)
    if parsed is None:
        raise ValueError(f"{name} is required")
    return parsed


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _capture_collection_boundary() -> datetime:
    return datetime.now(timezone.utc)


def _project_metadata(projects: dict[str, str] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(projects, dict):
        entries = [{"identifier": identifier, "id": project_id} for identifier, project_id in projects.items()]
    elif isinstance(projects, list):
        entries = [dict(project) for project in projects]
    else:
        raise ValueError("projects must be an identifier-to-ID mapping or a list of metadata objects")
    if not entries:
        raise ValueError("projects must not be empty")
    identifiers = set()
    project_ids = set()
    for entry in entries:
        project_id = entry.get("id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("each project must have a non-empty string id")
        identifier = entry.get("identifier", entry.get("name", project_id))
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("each project must have a non-empty string identifier")
        entry["identifier"] = identifier
        if project_id in project_ids or identifier in identifiers:
            raise ValueError("project IDs and identifiers must be unique")
        project_ids.add(project_id)
        identifiers.add(identifier)
    return entries


def _staff_metadata(staff: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(staff, list):
        raise ValueError("staff must be a list of metadata objects")
    entries = [dict(entry) for entry in staff]
    identifiers = set()
    for entry in entries:
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("each staff entry must have a non-empty string id")
        if identifier in identifiers:
            raise ValueError("staff IDs must be unique")
        identifiers.add(identifier)
    return entries


def _metadata_entry(value: Any, name: str) -> dict[str, Any]:
    entry = _activity_payload(value)
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise RuntimeError(f"{name} metadata entry without an ID")
    return entry


def _metadata_results(response: Any, name: str) -> tuple[list[Any], str, bool, int]:
    if isinstance(response, list):
        return response, "", False, len(response)
    page = _page_payload(response)
    results = page.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"invalid {name} metadata response")
    next_cursor = page.get("next_cursor") or ""
    has_more = bool(page.get("next_page_results", page.get("has_more", False)))
    if has_more != bool(next_cursor):
        if has_more:
            raise RuntimeError(f"unsafe {name} metadata continuation")
        raise RuntimeError(f"inconsistent {name} metadata pagination")
    total_count = page.get("total_count")
    if not isinstance(total_count, int) or total_count < 0:
        raise RuntimeError(f"invalid {name} metadata total_count")
    return results, next_cursor, has_more, total_count


def _paginated_metadata(fetch_page: Any, name: str, max_pages: int = 1000) -> list[dict[str, Any]]:
    cursor = None
    pages = 0
    entries = []
    seen = set()
    expected_total = None
    raw_scanned = 0
    while True:
        if pages >= max_pages:
            raise RuntimeError(f"{name} metadata pagination incomplete")
        results, next_cursor, has_more, total_count = _metadata_results(fetch_page(cursor), name)
        pages += 1
        if expected_total is None:
            expected_total = total_count
        elif total_count != expected_total:
            raise RuntimeError(f"{name} metadata total changed during pagination")
        raw_scanned += len(results)
        for value in results:
            entry = _metadata_entry(value, name)
            if entry["id"] not in seen:
                entries.append(entry)
                seen.add(entry["id"])
        if not has_more:
            if raw_scanned != expected_total:
                raise RuntimeError(f"{name} metadata raw count mismatch")
            return entries
        cursor = next_cursor


def _deduplicate_metadata(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated = []
    seen = set()
    for entry in entries:
        if entry["id"] not in seen:
            deduplicated.append(entry)
            seen.add(entry["id"])
    return deduplicated


def _collect_metadata(client: Any, workspace: str, projects: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    states = []
    labels = []
    work_item_types = _paginated_metadata(
        lambda cursor: client.workspace_work_item_types.list(workspace_slug=workspace),
        "workspace work item types",
    )
    for project in projects:
        project_id = project["id"]
        states.extend(
            _paginated_metadata(
                lambda cursor, project_id=project_id: client.states.list(
                    workspace_slug=workspace,
                    project_id=project_id,
                    params={"cursor": cursor, "per_page": 100},
                ),
                "states",
            )
        )
        labels.extend(
            _paginated_metadata(
                lambda cursor, project_id=project_id: client.labels.list(
                    workspace_slug=workspace,
                    project_id=project_id,
                    params={"cursor": cursor, "per_page": 100},
                ),
                "labels",
            )
        )
        work_item_types.extend(
            _paginated_metadata(
                lambda cursor, project_id=project_id: client.work_item_types.list(
                    workspace_slug=workspace,
                    project_id=project_id,
                    params={"cursor": cursor, "per_page": 100},
                ),
                "project work item types",
            )
        )
    return {
        "states": _deduplicate_metadata(states),
        "labels": _deduplicate_metadata(labels),
        "work_item_types": _deduplicate_metadata(work_item_types),
    }


def _validate_settings(
    sync_timeout_seconds: float,
    retry_initial_backoff_seconds: float,
    retry_max_backoff_seconds: float,
    sync_max_pages: int,
    candidate_page_size: int,
    candidate_max_pages: int,
    activity_page_size: int,
    activity_max_pages: int,
) -> None:
    if sync_timeout_seconds <= 0:
        raise ValueError("sync_timeout_seconds must be positive")
    if retry_initial_backoff_seconds <= 0:
        raise ValueError("retry_initial_backoff_seconds must be positive")
    if retry_max_backoff_seconds < retry_initial_backoff_seconds:
        raise ValueError("retry_max_backoff_seconds must not be less than retry_initial_backoff_seconds")
    for name, value in (
        ("sync_max_pages", sync_max_pages),
        ("candidate_max_pages", candidate_max_pages),
        ("activity_max_pages", activity_max_pages),
    ):
        if value < 1:
            raise ValueError(f"{name} must be positive")
    for name, value in (("candidate_page_size", candidate_page_size), ("activity_page_size", activity_page_size)):
        if not 1 <= value <= 100:
            raise ValueError(f"{name} must be between 1 and 100")


def _sync_project(
    cache: WorkItemCache,
    client: Any,
    workspace: str,
    server: str,
    cache_workspace: str,
    project_id: str,
    timeout: float,
    initial_backoff: float,
    max_backoff: float,
    max_pages: int,
) -> tuple[dict[str, Any], int]:
    list_fields = (
        "id,name,sequence_id,priority,state,assignees,labels,type_id,parent,cycle,modules,created_by,"
        "created_at,updated_at,start_date,target_date,completed_at,is_draft"
    )

    def fetch_page(cursor: str | None) -> dict[str, Any]:
        params = WorkItemQueryParams(
            order_by="-updated_at",
            per_page=100,
            cursor=cursor,
            fields=list_fields,
            expand="state",
        )
        return client.work_items._get(
            f"{workspace}/projects/{project_id}/work-items",
            params=params.model_dump(exclude_none=True),
        )

    started = time.monotonic()
    backoff = initial_backoff
    attempts = 0
    while True:
        attempts += 1
        result = sync_work_items(
            cache=cache,
            server=server,
            workspace=cache_workspace,
            project_id=project_id,
            fetch_page=fetch_page,
            owner=str(uuid.uuid4()),
            now=time.time(),
            max_pages=max_pages,
            force_full=True,
        )
        status = result.get("status")
        if status == "synced":
            return result, attempts
        if status != "busy":
            raise RuntimeError(f"cache synchronization incomplete for project {project_id}: status={status!r}")
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            raise TimeoutError(f"cache synchronization remained busy for project {project_id}")
        delay = min(backoff, timeout - elapsed)
        time.sleep(delay)
        backoff = min(backoff * 2, max_backoff)


def _branch_definitions(
    week_start: datetime, week_end: datetime, collection_started_at: datetime
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "updated_range",
            {
                "updated_at_from": _timestamp_text(week_start),
                "updated_at_to": _timestamp_text(collection_started_at),
            },
        ),
        ("started", {"state_groups": ["started"]}),
        (
            "active_overdue",
            {
                "state_groups": ["backlog", "unstarted", "started"],
                "target_date_to": (week_end.date() - timedelta(days=1)).isoformat(),
                "overdue": True,
            },
        ),
    ]


def _validate_branch_item(
    name: str, item: dict[str, Any], week_start: datetime, week_end: datetime, collection_started_at: datetime
) -> None:
    if name == "updated_range":
        updated_at = _timestamp(item.get("updated_at"), "work item updated_at")
        if not week_start <= updated_at <= collection_started_at:
            raise RuntimeError("updated candidate did not satisfy its branch constraints")
    elif name == "started" and item.get("state_group") != "started":
        raise RuntimeError("started candidate did not satisfy its branch constraints")
    elif name == "active_overdue":
        target_date = item.get("target_date")
        if item.get("state_group") not in {"backlog", "unstarted", "started"} or not target_date:
            raise RuntimeError("overdue candidate did not satisfy its branch constraints")
        try:
            due_date = datetime.fromisoformat(target_date).date()
        except (TypeError, ValueError) as error:
            raise RuntimeError("overdue candidate has an invalid target date") from error
        if due_date >= week_end.date():
            raise RuntimeError("overdue candidate did not satisfy its branch constraints")


def _candidate_items(
    cache: WorkItemCache,
    server: str,
    workspace: str,
    project_id: str,
    branches: list[tuple[str, dict[str, Any]]],
    week_start: datetime,
    week_end: datetime,
    collection_started_at: datetime,
    page_size: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = {}
    branch_metadata = []
    for name, filters in branches:
        offset = 0
        pages = 0
        expected_total = None
        branch_count = 0
        while True:
            if pages >= max_pages:
                raise RuntimeError(f"candidate pagination incomplete for project {project_id} branch {name}")
            page = cache.filter_items(
                server=server,
                workspace=workspace,
                project_id=project_id,
                relation_match="any",
                sort_by="updated_at",
                sort_direction="asc",
                offset=offset,
                limit=page_size,
                **filters,
            )
            pages += 1
            results = page.get("results")
            total_count = page.get("total_count")
            if not isinstance(results, list) or not isinstance(total_count, int) or total_count < 0:
                raise RuntimeError(f"invalid candidate page for project {project_id} branch {name}")
            if expected_total is None:
                expected_total = total_count
            elif total_count != expected_total:
                raise RuntimeError(f"candidate total changed during pagination for project {project_id} branch {name}")
            if not results and offset < total_count:
                raise RuntimeError(f"candidate pagination made no progress for project {project_id} branch {name}")
            for item in results:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise RuntimeError(f"invalid candidate item for project {project_id} branch {name}")
                _validate_branch_item(name, item, week_start, week_end, collection_started_at)
                record = dict(item)
                record["project"] = project_id
                record["state"] = record.get("state_id")
                record["assignees"] = record.get("assignee_ids", [])
                record["labels"] = record.get("label_ids", [])
                candidates.setdefault(item["id"], record)
            count = len(results)
            branch_count += count
            offset += count
            if offset >= total_count:
                break
        branch_metadata.append({"name": name, "count": branch_count, "pages": pages})
    return list(candidates.values()), branch_metadata


def _activity_history(
    client: Any,
    workspace: str,
    project_id: str,
    work_item_id: str,
    collection_started_at: str,
    page_size: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], int]:
    cursor = None
    pages = 0
    activities = {}
    expected_total = None
    raw_scanned = 0
    upper = _timestamp(collection_started_at, "collection_started_at")
    ranges = {
        "created_at": {"minimum": (None, True), "maximum": (upper, True)},
        "updated_at": {"minimum": (None, True), "maximum": (None, True)},
    }
    while True:
        if pages >= max_pages:
            raise RuntimeError(f"activity pagination incomplete for work item {work_item_id}")
        page = _page_payload(
            client.work_items.activities.list(
                workspace_slug=workspace,
                project_id=project_id,
                work_item_id=work_item_id,
                params={"cursor": cursor, "per_page": page_size, "order_by": "created_at"},
            )
        )
        pages += 1
        if page.get("results_truncated"):
            raise RuntimeError(f"activity results truncated for work item {work_item_id}")
        results = page.get("results") or []
        if not isinstance(results, list):
            raise RuntimeError(f"invalid activity page for work item {work_item_id}")
        next_cursor = page.get("next_cursor") or ""
        has_more = bool(page.get("next_page_results", page.get("has_more", False)))
        scan_has_more = bool(page.get("scan_has_more", has_more))
        if has_more != bool(next_cursor):
            if has_more:
                raise RuntimeError(f"unsafe activity continuation for work item {work_item_id}")
            raise RuntimeError(f"inconsistent activity pagination for work item {work_item_id}")
        total_count = page.get("total_count")
        if not isinstance(total_count, int) or total_count < 0:
            raise RuntimeError(f"invalid activity total_count for work item {work_item_id}")
        if expected_total is None:
            expected_total = total_count
        elif total_count != expected_total:
            raise RuntimeError(f"activity total changed during pagination for work item {work_item_id}")
        raw_scanned += len(results)
        if scan_has_more and not next_cursor:
            raise RuntimeError(f"unsafe activity continuation for work item {work_item_id}")
        for raw_activity in results:
            activity = _activity_payload(raw_activity)
            if not _activity_matches(activity, ranges, None, None, "state", None):
                continue
            activity_id = activity.get("id")
            if not isinstance(activity_id, str) or not activity_id:
                raise RuntimeError(f"activity without an ID for work item {work_item_id}")
            activity_project = activity.get("project")
            if isinstance(activity_project, dict):
                activity_project = activity_project.get("id")
            if activity_project != project_id:
                raise RuntimeError(f"activity project mismatch for work item {work_item_id}")
            for identity_field in ("issue", "work_item", "work_item_id"):
                activity_work_item = activity.get(identity_field)
                if isinstance(activity_work_item, dict):
                    activity_work_item = activity_work_item.get("id")
                if activity_work_item is not None and activity_work_item != work_item_id:
                    raise RuntimeError(f"activity work-item mismatch for work item {work_item_id}")
            activity["work_item"] = work_item_id
            activities.setdefault(activity_id, activity)
        if not has_more:
            if scan_has_more:
                raise RuntimeError(f"unsafe activity continuation for work item {work_item_id}")
            if raw_scanned != expected_total:
                raise RuntimeError(f"activity raw count mismatch for work item {work_item_id}")
            break
        cursor = next_cursor
    return list(activities.values()), pages


def _validate_candidate_references(items: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    known = {
        "state": {entry["id"] for entry in metadata["states"]},
        "label": {entry["id"] for entry in metadata["labels"]},
        "type": {entry["id"] for entry in metadata["work_item_types"]},
    }
    for item in items:
        references = {
            "state": [item.get("state_id")],
            "label": item.get("label_ids") or [],
            "type": [item.get("type_id")],
        }
        for name, values in references.items():
            for value in values:
                if value is not None and value not in known[name]:
                    raise RuntimeError(f"candidate {item['id']} has unresolved {name} reference {value}")


def collect_weekly_report_bundle(
    projects: dict[str, str] | list[dict[str, Any]],
    staff: list[dict[str, Any]],
    week_start: str,
    week_end: str,
    collection_started_at: str,
    sync_timeout_seconds: float = DEFAULT_SYNC_TIMEOUT_SECONDS,
    retry_initial_backoff_seconds: float = 0.1,
    retry_max_backoff_seconds: float = 2.0,
    sync_max_pages: int = 1000,
    candidate_page_size: int = 100,
    candidate_max_pages: int = 1000,
    activity_page_size: int = 100,
    activity_max_pages: int = 100,
) -> dict[str, Any]:
    project_entries = _project_metadata(projects)
    staff_entries = _staff_metadata(staff)
    start = _timestamp(week_start, "week_start")
    end = _timestamp(week_end, "week_end")
    end_calendar = datetime.fromisoformat(week_end.replace("Z", "+00:00"))
    collected_at = _timestamp(collection_started_at, "collection_started_at")
    if start >= end:
        raise ValueError("week_start must be before exclusive week_end")
    if collected_at < end:
        raise ValueError("collection_started_at must not be before exclusive week_end")
    effective_collection = _capture_collection_boundary()
    if collected_at > effective_collection:
        raise ValueError("collection_started_at must not be after effective collection boundary")
    _validate_settings(
        sync_timeout_seconds,
        retry_initial_backoff_seconds,
        retry_max_backoff_seconds,
        sync_max_pages,
        candidate_page_size,
        candidate_max_pages,
        activity_page_size,
        activity_max_pages,
    )
    normalized_start = _timestamp_text(start)
    normalized_end = _timestamp_text(end)
    normalized_collection = _timestamp_text(collected_at)
    normalized_effective_collection = _timestamp_text(effective_collection)
    requested = {
        "projects": projects,
        "staff": staff,
        "week_start": week_start,
        "week_end": week_end,
        "collection_started_at": collection_started_at,
    }
    client, workspace = get_plane_client_context()
    metadata = {
        "projects": project_entries,
        "staff": staff_entries,
        **_collect_metadata(client, workspace, project_entries),
    }
    server = os.getenv("PLANE_INTERNAL_BASE_URL") or os.getenv("PLANE_BASE_URL", "https://api.plane.so")
    cache_workspace = f"{workspace}:{get_plane_cache_scope()}"
    cache = WorkItemCache()
    work_items = []
    project_collection = []
    for project in project_entries:
        project_id = project["id"]
        sync_result, sync_attempts = _sync_project(
            cache,
            client,
            workspace,
            server,
            cache_workspace,
            project_id,
            sync_timeout_seconds,
            retry_initial_backoff_seconds,
            retry_max_backoff_seconds,
            sync_max_pages,
        )
        project_collection.append({"project_id": project_id, "sync": sync_result, "sync_attempts": sync_attempts})

    branches = _branch_definitions(start, end_calendar, effective_collection)
    with cache.read_snapshot() as snapshot:
        for project, project_result in zip(project_entries, project_collection, strict=True):
            project_id = project["id"]
            candidates, branch_metadata = _candidate_items(
                snapshot,
                server,
                cache_workspace,
                project_id,
                branches,
                start,
                end_calendar,
                effective_collection,
                candidate_page_size,
                candidate_max_pages,
            )
            work_items.extend(candidates)
            project_result["branches"] = branch_metadata
            project_result["candidate_count"] = len(candidates)

    _validate_candidate_references(work_items, metadata)
    activities = []
    seen_activity_ids = set()
    activity_pages = 0
    for item in work_items:
        item_activities, pages = _activity_history(
            client,
            workspace,
            item["project"],
            item["id"],
            normalized_effective_collection,
            activity_page_size,
            activity_max_pages,
        )
        activity_pages += pages
        for activity in item_activities:
            if activity["id"] not in seen_activity_ids:
                activities.append(activity)
                seen_activity_ids.add(activity["id"])

    completeness = {
        "complete": True,
        "cache_complete": True,
        "candidate_pagination_complete": True,
        "activity_pagination_complete": True,
        "candidate_branches_per_project": 3,
    }
    collection = {
        "week_start": normalized_start,
        "week_end": normalized_end,
        "week_end_exclusive": True,
        "requested_collection_started_at": normalized_collection,
        "collection_started_at": normalized_effective_collection,
        "collection_started_at_semantics": (
            "inclusive effective boundary captured before metadata and every forced project synchronization; "
            "requested_collection_started_at must be at or before it"
        ),
        "projects": project_collection,
        "work_item_count": len(work_items),
        "activity_count": len(activities),
        "activity_pages": activity_pages,
    }
    return {
        "tool": "collect_weekly_report_bundle",
        "requested": requested,
        "metadata": metadata,
        "work_items": {"metadata": metadata, "results": work_items},
        "activities": {"metadata": metadata, "results": activities},
        "completeness": completeness,
        "collection": collection,
    }


def register_weekly_report_tools(mcp: FastMCP) -> None:
    """Register weekly report collection tools."""
    collect_bundle = globals()["collect_weekly_report_bundle"]

    @mcp.tool()
    def collect_weekly_report_bundle(
        projects: dict[str, str] | list[dict[str, Any]],
        staff: list[dict[str, Any]],
        week_start: str,
        week_end: str,
        collection_started_at: str,
        sync_timeout_seconds: float = DEFAULT_SYNC_TIMEOUT_SECONDS,
        retry_initial_backoff_seconds: float = 0.1,
        retry_max_backoff_seconds: float = 2.0,
        sync_max_pages: int = 1000,
        candidate_page_size: int = 100,
        candidate_max_pages: int = 1000,
        activity_page_size: int = 100,
        activity_max_pages: int = 100,
    ) -> dict[str, Any]:
        """Collect a complete read-only input bundle for plane-weekly-report.

        Use this instead of orchestrating filter_work_items and
        filter_work_item_activities when one call must safely collect every weekly
        candidate and its complete state history. projects accepts either an
        identifier-to-project-ID mapping or a list of project metadata objects;
        staff is preserved exactly as the configured identity metadata.

        For every project the tool forces a forced full scan using a cache generation,
        reconciles deletions, then runs exactly three separate client-side cache branches
        from a single SQLite read snapshot shared by every project: updated items
        from inclusive week_start through inclusive collection_started_at; items in
        the started state group; and active overdue items in backlog, unstarted, or
        started with a due date before exclusive week_end. It paginates every branch
        using candidate_page_size and candidate_max_pages and deduplicates by project
        and work-item ID. It never invokes other MCP tools and does not write files.

        week_start, week_end, and collection_started_at must be strict ISO-8601
        timestamps with timezone; comparisons are normalized to UTC. week_end is
        exclusive. Before metadata and every forced project scan, the collector captures
        one inclusive effective collection boundary. The requested collection_started_at
        must be at or after week_end and at or before that captured boundary. Updated
        candidates and activities are capped at this same pre-scan boundary, preventing
        records changed during collection from leaking into the bundle. The original
        requested bound is normalized separately from the effective bound in collection.
        State activities are fetched directly from Plane, filtered
        client-side to field=state and created_at at or before the inclusive
        effective collection boundary, paginated deterministically with
        order_by=created_at, activity_page_size, and activity_max_pages, and
        deduplicated by activity ID. Plane self-host may
        ignore activity filters, so all returned records are checked locally. Raw
        activity issue, work_item, and work_item_id identities are validated, and
        every retained state activity normalizes work_item to the candidate ID while
        preserving old_value and new_value state names.

        The collector fetches complete states, labels, and work item types metadata
        itself for every project and the workspace using Plane list APIs. Paginated
        metadata APIs are followed to completion, deduplicates metadata by ID, and
        the call fails rather than returning partial metadata when continuation is
        missing, inconsistent, or exceeds its safety bound. Metadata and activity
        pagination require stable total_count on every page and an exact cumulative raw
        result count. The collector resolves every candidate state, label, and type UUID
        in the gathered metadata or the call fails closed.

        A cache sync status of busy is retried until sync_timeout_seconds, whose
        180-second default exceeds the 120-second cache lease, using bounded
        exponential backoff beginning at retry_initial_backoff_seconds and
        capped by retry_max_backoff_seconds. sync_max_pages bounds each cache sync.
        The call fails rather than returning partial data on timeout, a non-synced
        cache, exhausted candidate or activity page bounds, truncation, unsafe
        continuation, changing totals, or project/work-item mismatch.

        Returns tool=collect_weekly_report_bundle and requested containing exactly the
        five validated input arguments projects, staff, week_start, week_end, and
        collection_started_at so a finalizer can bind the result to its prepared request.
        Their original canonical representations are retained exactly; normalized UTC
        boundaries are reported separately in collection. The result also includes
        metadata plus separate work_items and activities aggregates shaped as
        {metadata, results}, explicit completeness flags, and collection metadata with
        the separately captured effective boundary, sync attempts, branch/page counts,
        and totals. There is no continuation token because success guarantees the
        complete aggregate.
        """
        return collect_bundle(
            projects,
            staff,
            week_start,
            week_end,
            collection_started_at,
            sync_timeout_seconds,
            retry_initial_backoff_seconds,
            retry_max_backoff_seconds,
            sync_max_pages,
            candidate_page_size,
            candidate_max_pages,
            activity_page_size,
            activity_max_pages,
        )

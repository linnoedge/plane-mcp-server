"""Work item activity-related tools for Plane MCP Server."""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP
from plane.models.work_items import WorkItemActivity

from plane_mcp.client import get_plane_client_context

_RANGE_PARAM_MAP = {
    "created_at__gt": ("created_at", "minimum", False),
    "created_at__gte": ("created_at", "minimum", True),
    "created_at__lt": ("created_at", "maximum", False),
    "created_at__lte": ("created_at", "maximum", True),
    "updated_at__gt": ("updated_at", "minimum", False),
    "updated_at__gte": ("updated_at", "minimum", True),
    "updated_at__lt": ("updated_at", "maximum", False),
    "updated_at__lte": ("updated_at", "maximum", True),
}
_EXACT_FILTERS = ("activity_type", "verb", "field", "actor")


def _activity_payload(activity: Any) -> dict[str, Any]:
    if isinstance(activity, dict):
        return activity
    if hasattr(activity, "model_dump"):
        return activity.model_dump()
    return vars(activity)


def _page_payload(page: Any) -> dict[str, Any]:
    if isinstance(page, dict):
        return page
    if hasattr(page, "model_dump"):
        return page.model_dump()
    return vars(page)


def _parse_timestamp(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        if "T" not in value:
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a strict ISO-8601 timestamp with timezone") from error
    return parsed.astimezone(timezone.utc)


def _activity_matches(
    activity: dict[str, Any],
    ranges: dict[str, dict[str, tuple[datetime | None, bool]]],
    activity_type: str | None,
    verb: str | None,
    field: str | None,
    actor: str | None,
) -> bool:
    for timestamp_field, bounds in ranges.items():
        minimum, minimum_inclusive = bounds["minimum"]
        maximum, maximum_inclusive = bounds["maximum"]
        if minimum is None and maximum is None:
            continue
        value = _parse_timestamp(activity.get(timestamp_field), timestamp_field)
        if value is None:
            return False
        if minimum is not None and (value < minimum or not minimum_inclusive and value == minimum):
            return False
        if maximum is not None and (value > maximum or not maximum_inclusive and value == maximum):
            return False

    exact_filters = {
        "activity_type": activity_type,
        "verb": verb,
        "field": field,
        "actor": actor,
    }
    return all(expected is None or activity.get(name) == expected for name, expected in exact_filters.items())


def _filter_activity_pages(
    fetch_page: Callable[[str | None], Any],
    created_at_from: str | None = None,
    created_at_to: str | None = None,
    updated_at_from: str | None = None,
    updated_at_to: str | None = None,
    activity_type: str | None = None,
    verb: str | None = None,
    field: str | None = None,
    actor: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    max_pages: int = 10,
    range_inclusive: dict[str, bool] | None = None,
) -> dict[str, Any]:
    inclusive = range_inclusive or {}
    ranges = {
        "created_at": {
            "minimum": (_parse_timestamp(created_at_from, "created_at_from"), inclusive.get("created_at_from", True)),
            "maximum": (_parse_timestamp(created_at_to, "created_at_to"), inclusive.get("created_at_to", True)),
        },
        "updated_at": {
            "minimum": (_parse_timestamp(updated_at_from, "updated_at_from"), inclusive.get("updated_at_from", True)),
            "maximum": (_parse_timestamp(updated_at_to, "updated_at_to"), inclusive.get("updated_at_to", True)),
        },
    }
    for name, bounds in ranges.items():
        minimum = bounds["minimum"][0]
        maximum = bounds["maximum"][0]
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"{name}_from must not be after {name}_to")

    matches = []
    total_scanned = 0
    pages_scanned = 0
    next_cursor = cursor or ""
    has_more = False
    while pages_scanned < max_pages:
        page = _page_payload(fetch_page(next_cursor or None))
        pages_scanned += 1
        activities = page.get("results") or []
        total_scanned += len(activities)
        next_cursor = page.get("next_cursor") or ""
        has_more = bool(page.get("next_page_results") and next_cursor)

        matches.extend(
            _activity_payload(item)
            for item in activities
            if _activity_matches(_activity_payload(item), ranges, activity_type, verb, field, actor)
        )

        if not has_more:
            break

    results_truncated = len(matches) > limit
    return {
        "results": matches[:limit],
        "count": min(len(matches), limit),
        "total_matches": len(matches),
        "total_scanned": total_scanned,
        "pages_scanned": pages_scanned,
        "results_truncated": results_truncated,
        "scan_has_more": has_more,
        "next_cursor": next_cursor if has_more and not results_truncated else "",
        "has_more": has_more and not results_truncated,
    }


def _validate_bound(name: str, value: int) -> None:
    if not 1 <= value <= 100:
        raise ValueError(f"{name} must be between 1 and 100")


def _legacy_filters(
    params: dict[str, Any],
    explicit_ranges: dict[str, str | None],
    explicit_exact: dict[str, str | None],
) -> tuple[dict[str, str | None], dict[str, str | None], dict[str, bool]]:
    ranges = explicit_ranges.copy()
    exact = explicit_exact.copy()
    inclusive = {}

    for parameter, (timestamp_field, boundary, is_inclusive) in _RANGE_PARAM_MAP.items():
        argument = f"{timestamp_field}_{'from' if boundary == 'minimum' else 'to'}"
        if ranges[argument] is None and parameter in params:
            ranges[argument] = params[parameter]
            inclusive[argument] = is_inclusive

    for name in _EXACT_FILTERS:
        if exact[name] is None and name in params:
            exact[name] = params[name]

    return ranges, exact, inclusive


def register_work_item_activity_tools(mcp: FastMCP) -> None:
    """Register all work item activity-related tools with the MCP server."""

    def filter_activities(
        project_id: str,
        work_item_id: str,
        params: dict[str, Any] | None,
        created_at_from: str | None,
        created_at_to: str | None,
        updated_at_from: str | None,
        updated_at_to: str | None,
        activity_type: str | None,
        verb: str | None,
        field: str | None,
        actor: str | None,
        limit: int,
        per_page: int | None,
        cursor: str | None,
        max_pages: int,
    ) -> dict[str, Any]:
        legacy_params = params or {}
        effective_per_page = per_page if per_page is not None else legacy_params.get("per_page", 100)
        effective_cursor = cursor if cursor is not None else legacy_params.get("cursor")
        _validate_bound("limit", limit)
        _validate_bound("per_page", effective_per_page)
        _validate_bound("max_pages", max_pages)
        ranges, exact, inclusive = _legacy_filters(
            legacy_params,
            {
                "created_at_from": created_at_from,
                "created_at_to": created_at_to,
                "updated_at_from": updated_at_from,
                "updated_at_to": updated_at_to,
            },
            {"activity_type": activity_type, "verb": verb, "field": field, "actor": actor},
        )
        client, workspace_slug = get_plane_client_context()

        def fetch_page(page_cursor: str | None) -> Any:
            return client.work_items.activities.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                params={"cursor": page_cursor, "per_page": effective_per_page},
            )

        return _filter_activity_pages(
            fetch_page=fetch_page,
            **ranges,
            **exact,
            limit=limit,
            cursor=effective_cursor,
            max_pages=max_pages,
            range_inclusive=inclusive,
        )

    @mcp.tool()
    def list_work_item_activities(
        project_id: str,
        work_item_id: str,
        params: dict[str, Any] | None = None,
        created_at_from: str | None = None,
        created_at_to: str | None = None,
        updated_at_from: str | None = None,
        updated_at_to: str | None = None,
        activity_type: str | None = None,
        verb: str | None = None,
        field: str | None = None,
        actor: str | None = None,
        limit: int = 50,
        per_page: int | None = None,
        cursor: str | None = None,
        max_pages: int = 10,
    ) -> list[WorkItemActivity]:
        """List activities for a work item, preserving the legacy list return shape.

        Use this for callers that need only activity records. Plane self-host ignores
        activity filters, so this tool scans cursor pages and filters client-side.
        Use created_at_from/created_at_to or updated_at_from/updated_at_to for
        inclusive timestamp ranges. Timestamps must be strict ISO-8601 values with a
        timezone; values are compared in UTC. activity_type, verb, field, and actor
        are exact, case-sensitive filters. Legacy params keys created_at__gt,
        created_at__gte, created_at__lt, created_at__lte and updated_at equivalents
        remain supported; explicit arguments take precedence.

        per_page controls the server page size, cursor selects the first page,
        max_pages bounds the client-side scan, and limit caps returned matches. All
        bounds are 1-100. The result is list[WorkItemActivity] without pagination
        metadata; use filter_work_item_activities when scan/truncation metadata or a
        safe continuation cursor is required.
        """
        result = filter_activities(
            project_id,
            work_item_id,
            params,
            created_at_from,
            created_at_to,
            updated_at_from,
            updated_at_to,
            activity_type,
            verb,
            field,
            actor,
            limit,
            per_page,
            cursor,
            max_pages,
        )
        return [WorkItemActivity.model_validate(activity) for activity in result["results"]]

    @mcp.tool()
    def filter_work_item_activities(
        project_id: str,
        work_item_id: str,
        params: dict[str, Any] | None = None,
        created_at_from: str | None = None,
        created_at_to: str | None = None,
        updated_at_from: str | None = None,
        updated_at_to: str | None = None,
        activity_type: str | None = None,
        verb: str | None = None,
        field: str | None = None,
        actor: str | None = None,
        limit: int = 50,
        per_page: int | None = None,
        cursor: str | None = None,
        max_pages: int = 10,
    ) -> dict[str, Any]:
        """Filter work-item activities by time or exact fields with scan metadata.

        Use this when filtering activity history, especially by time, or when the
        caller needs bounded-scan metadata. Plane self-host ignores activity filters,
        so created_at_from/created_at_to and updated_at_from/updated_at_to are applied
        client-side as inclusive ranges after cursor pages are fetched. Timestamps
        must be strict ISO-8601 values with a timezone and are compared in UTC.
        activity_type, verb, field, and actor use exact, case-sensitive matching.
        Legacy params range keys are accepted, but explicit arguments take precedence.

        per_page controls server page size, cursor selects the first page, max_pages
        bounds scanning, and limit caps returned matches; each numeric bound is 1-100.
        Returns results, count, total_matches, total_scanned, pages_scanned,
        results_truncated, scan_has_more, has_more, and next_cursor. When matching
        results are truncated within scanned pages, has_more is false and next_cursor
        is empty because continuing would skip matches. If max_pages stops cleanly at
        a page boundary, has_more and next_cursor provide safe continuation.
        """
        return filter_activities(
            project_id,
            work_item_id,
            params,
            created_at_from,
            created_at_to,
            updated_at_from,
            updated_at_to,
            activity_type,
            verb,
            field,
            actor,
            limit,
            per_page,
            cursor,
            max_pages,
        )

    @mcp.tool()
    def retrieve_work_item_activity(
        project_id: str,
        work_item_id: str,
        activity_id: str,
    ) -> WorkItemActivity:
        """Retrieve a specific activity for a work item."""
        client, workspace_slug = get_plane_client_context()
        return client.work_items.activities.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            activity_id=activity_id,
        )

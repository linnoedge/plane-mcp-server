"""Project-related tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.enums import TimezoneEnum
from plane.models.projects import (
    CreateProject,
    PaginatedProjectLiteResponse,
    PaginatedProjectMemberResponse,
    Project,
    UpdateProject,
)
from plane.models.query_params import MemberListQueryParams, ProjectLiteListQueryParams

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools._compat import paginated_payload


def _project_value_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("id")
    if hasattr(value, "id"):
        return value.id
    return None


def _project_member_ids(project: dict[str, Any]) -> list[str]:
    values = project.get("members") or project.get("project_members") or []
    ids = []
    for value in values:
        value_id = _project_value_id(value)
        if value_id:
            ids.append(value_id)
    return ids


def _project_matches(
    project: dict[str, Any],
    query: str | None,
    identifier: str | None,
    is_member: bool | None,
    archived: bool | None,
    lead_id: str | None,
    member_id: str | None,
) -> bool:
    if query:
        needle = query.lower()
        haystacks = [project.get("name"), project.get("identifier"), project.get("description")]
        if not any(needle in str(value or "").lower() for value in haystacks):
            return False
    if identifier is not None and str(project.get("identifier") or "").lower() != identifier.lower():
        return False
    if is_member is not None and bool(project.get("is_member")) != is_member:
        return False
    if archived is not None and bool(project.get("archived_at")) != archived:
        return False
    if lead_id is not None and _project_value_id(project.get("project_lead")) != lead_id:
        return False
    if member_id is not None and member_id not in _project_member_ids(project):
        return False
    return True


def _filter_projects_from_pages(
    fetch_page: Any,
    query: str | None,
    identifier: str | None,
    is_member: bool | None,
    archived: bool | None,
    lead_id: str | None,
    member_id: str | None,
    limit: int,
    max_pages: int,
    start_cursor: str | None = None,
) -> dict[str, Any]:
    results = []
    cursor = start_cursor
    pages_scanned = 0
    total_scanned = 0
    total_available = None
    next_cursor = ""
    has_more = False

    while pages_scanned < max_pages and len(results) < limit:
        page = fetch_page(cursor)
        pages_scanned += 1
        projects = page.get("results") or []
        total_available = page.get("total_count", total_available)
        total_scanned += len(projects)
        next_cursor = page.get("next_cursor") or ""
        has_more = bool(page.get("next_page_results"))

        for project in projects:
            if _project_matches(project, query, identifier, is_member, archived, lead_id, member_id):
                results.append(project)
                if len(results) >= limit:
                    break

        if not has_more or not next_cursor:
            break
        cursor = next_cursor

    return {
        "results": results,
        "count": len(results),
        "total_scanned": total_scanned,
        "pages_scanned": pages_scanned,
        "total_available": total_available,
        "next_cursor": next_cursor if has_more else "",
        "has_more": has_more,
    }


def _project_list_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    return response.model_dump()


def register_project_tools(mcp: FastMCP) -> None:
    """Register project-related tools with the MCP server."""

    @mcp.tool()
    def list_projects(
        cursor: str | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
    ) -> PaginatedProjectLiteResponse:
        """List projects in a workspace (paginated)."""
        client, workspace_slug = get_plane_client_context()
        params = ProjectLiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by, include_archived=False)
        try:
            return client.projects.list_lite(workspace_slug=workspace_slug, params=params)
        except HttpError as e:
            if e.status_code != 404:
                raise
            response = client.projects.list(workspace_slug=workspace_slug, params=params)
            return PaginatedProjectLiteResponse.model_validate(response.model_dump())

    @mcp.tool()
    def filter_projects(
        query: str | None = None,
        identifier: str | None = None,
        is_member: bool | None = None,
        archived: bool | None = None,
        lead_id: str | None = None,
        member_id: str | None = None,
        limit: int = 25,
        per_page: int = 100,
        cursor: str | None = None,
        max_pages: int = 10,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Filter/search projects client-side across paginated project pages.

        Use this to find projects by exact identifier or search-like text matching
        on name, identifier, and description. Plane self-host project-lite exposes
        ordering, archived toggle, and cursor pagination but no rich server-side
        filters, so this tool scans pages and filters client-side.

        Args:
            query: Case-insensitive contains match over name, identifier, description.
            identifier: Exact project identifier match, case-insensitive (e.g. BAM).
            is_member: Match project membership flag when returned by Plane.
            archived: False scans active projects, True scans archived projects.
            lead_id: Project lead user UUID when available in response.
            member_id: Project member UUID when available in response.
            limit: Maximum matching projects to return.
            per_page: Projects fetched per scanned page, 1-1000.
            cursor: Optional starting cursor to continue a previous scan.
            max_pages: Maximum pages to scan in this call.
            order_by: Sort before scanning, e.g. created_at, -created_at, name.

        Returns:
            results: Matching projects.
            total_scanned: Number of projects inspected.
            pages_scanned: Number of pages scanned.
            next_cursor: Cursor to pass back when has_more is true.
            has_more: True if more underlying project pages exist.
        """
        client, workspace_slug = get_plane_client_context()
        safe_limit = max(1, min(limit, 100))
        safe_per_page = max(1, min(per_page, 1000))
        safe_max_pages = max(1, min(max_pages, 100))

        def fetch_page(page_cursor: str | None) -> dict[str, Any]:
            params = ProjectLiteListQueryParams(
                cursor=page_cursor,
                per_page=safe_per_page,
                order_by=order_by,
                include_archived=archived,
            )
            try:
                response = client.projects.list_lite(workspace_slug=workspace_slug, params=params)
            except HttpError as e:
                if e.status_code != 404:
                    raise
                response = client.projects.list(workspace_slug=workspace_slug, params=params)
            return _project_list_payload(response)

        result = _filter_projects_from_pages(
            fetch_page=fetch_page,
            query=query,
            identifier=identifier,
            is_member=is_member,
            archived=archived,
            lead_id=lead_id,
            member_id=member_id,
            limit=safe_limit,
            max_pages=safe_max_pages,
            start_cursor=cursor,
        )
        result["filter_note"] = (
            "Projects were filtered client-side after scanning Plane self-host project pages. "
            "Use next_cursor to continue scanning if has_more is true."
        )
        return result

    @mcp.tool()
    def create_project(
        name: str,
        identifier: str,
        description: str | None = None,
        project_lead: str | None = None,
        default_assignee: str | None = None,
        emoji: str | None = None,
        cover_image: str | None = None,
        module_view: bool | None = None,
        cycle_view: bool | None = None,
        issue_views_view: bool | None = None,
        page_view: bool | None = None,
        intake_view: bool | None = None,
        guest_view_all_features: bool | None = None,
        archive_in: int | None = None,
        close_in: int | None = None,
        timezone: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        is_issue_type_enabled: bool | None = None,
    ) -> Project:
        """Create a new project."""
        client, workspace_slug = get_plane_client_context()
        data = CreateProject(
            name=name,
            identifier=identifier,
            description=description,
            project_lead=project_lead,
            default_assignee=default_assignee,
            emoji=emoji,
            cover_image=cover_image,
            module_view=module_view,
            cycle_view=cycle_view,
            issue_views_view=issue_views_view,
            page_view=page_view,
            intake_view=intake_view,
            guest_view_all_features=guest_view_all_features,
            archive_in=archive_in,
            close_in=close_in,
            timezone=timezone if timezone in get_args(TimezoneEnum) else None,  # type: ignore[assignment]
            external_source=external_source,
            external_id=external_id,
            is_issue_type_enabled=is_issue_type_enabled,
        )
        return client.projects.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def retrieve_project(project_id: str) -> Project:
        """Retrieve a project by ID."""
        client, workspace_slug = get_plane_client_context()
        return client.projects.retrieve(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def update_project(
        project_id: str,
        name: str | None = None,
        description: str | None = None,
        project_lead: str | None = None,
        default_assignee: str | None = None,
        identifier: str | None = None,
        emoji: str | None = None,
        cover_image: str | None = None,
        network: int | None = None,
        module_view: bool | None = None,
        cycle_view: bool | None = None,
        issue_views_view: bool | None = None,
        page_view: bool | None = None,
        intake_view: bool | None = None,
        guest_view_all_features: bool | None = None,
        archive_in: int | None = None,
        close_in: int | None = None,
        timezone: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        is_issue_type_enabled: bool | None = None,
        is_time_tracking_enabled: bool | None = None,
        default_state: str | None = None,
        estimate: str | None = None,
    ) -> Project:
        """Update a project by ID."""
        client, workspace_slug = get_plane_client_context()
        data = UpdateProject(
            name=name,
            description=description,
            project_lead=project_lead,
            default_assignee=default_assignee,
            identifier=identifier,
            emoji=emoji,
            cover_image=cover_image,
            network=network,
            module_view=module_view,
            cycle_view=cycle_view,
            issue_views_view=issue_views_view,
            page_view=page_view,
            intake_view=intake_view,
            guest_view_all_features=guest_view_all_features,
            archive_in=archive_in,
            close_in=close_in,
            timezone=timezone if timezone in get_args(TimezoneEnum) else None,  # type: ignore[assignment]
            external_source=external_source,
            external_id=external_id,
            is_issue_type_enabled=is_issue_type_enabled,
            is_time_tracking_enabled=is_time_tracking_enabled,
            default_state=default_state,
            estimate=estimate,
        )
        return client.projects.update(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def delete_project(project_id: str) -> None:
        """Delete a project by ID."""
        client, workspace_slug = get_plane_client_context()
        client.projects.delete(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def manage_project_archive(project_id: str, archive: bool) -> None:
        """Archive or unarchive a project."""
        client, workspace_slug = get_plane_client_context()
        if archive:
            client.projects.archive(workspace_slug=workspace_slug, project_id=project_id)
        else:
            client.projects.unarchive(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def get_project_members(
        project_id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        display_name: str | None = None,
        role_slug: str | None = None,
        is_active: bool | None = None,
        is_bot: bool | None = None,
        cursor: str | None = None,
        per_page: int | None = 100,
        order_by: str | None = None,
    ) -> PaginatedProjectMemberResponse:
        """List members of a project."""
        client, workspace_slug = get_plane_client_context()
        params = MemberListQueryParams(
            first_name=first_name,
            last_name=last_name,
            email=email,
            display_name=display_name,
            role_slug=role_slug,
            is_active=is_active,
            is_bot=is_bot,
            cursor=cursor,
            per_page=per_page,
            order_by=order_by,
        )
        try:
            return client.projects.get_members_lite(workspace_slug=workspace_slug, project_id=project_id, params=params)
        except HttpError as e:
            if e.status_code != 404:
                raise
            members = client.projects.get_members(workspace_slug=workspace_slug, project_id=project_id)
            return PaginatedProjectMemberResponse.model_validate(paginated_payload(members))

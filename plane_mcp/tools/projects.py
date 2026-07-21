"""Project-related tools for Plane MCP Server."""

from typing import get_args

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

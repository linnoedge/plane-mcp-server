"""Workspace-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.projects import ProjectFeature
from plane.models.query_params import MemberListQueryParams
from plane.models.workspaces import PaginatedWorkspaceMemberResponse, WorkspaceFeature

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools._compat import paginated_payload


def register_workspace_tools(mcp: FastMCP) -> None:
    """Register workspace tools with the MCP server."""

    @mcp.tool()
    def get_workspace_members(
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
    ) -> PaginatedWorkspaceMemberResponse:
        """List members of the current workspace."""
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
            return client.workspaces.get_members_lite(workspace_slug=workspace_slug, params=params)
        except HttpError as e:
            if e.status_code != 404:
                raise
            members = client.workspaces.get_members(workspace_slug=workspace_slug)
            return PaginatedWorkspaceMemberResponse.model_validate(paginated_payload(members))

    @mcp.tool()
    def get_features(project_id: str | None = None) -> WorkspaceFeature | ProjectFeature:
        """Get feature flags."""
        client, workspace_slug = get_plane_client_context()
        try:
            if project_id is not None:
                return client.projects.get_features(workspace_slug=workspace_slug, project_id=project_id)
            return client.workspaces.get_features(workspace_slug=workspace_slug)
        except HttpError as e:
            if e.status_code != 404:
                raise
            if project_id is not None:
                return ProjectFeature()
            return WorkspaceFeature(
                project_grouping=False,
                initiatives=False,
                teams=False,
                customers=False,
                wiki=False,
                pi=False,
            )

"""Page-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.pages import Page
from plane.models.query_params import PaginatedQueryParams

from plane_mcp.client import get_plane_client_context


def register_page_tools(mcp: FastMCP) -> None:
    """Register page-related tools with the MCP server."""

    @mcp.tool()
    def list_pages(project_id: str | None = None, params: dict[str, Any] | None = None) -> list[Page]:
        """List pages."""
        client, workspace_slug = get_plane_client_context()
        query_params = PaginatedQueryParams.model_validate(params or {})
        try:
            if project_id is not None:
                response = client.pages.list_project_pages(
                    workspace_slug=workspace_slug, project_id=project_id, params=query_params
                )
            else:
                response = client.pages.list_workspace_pages(workspace_slug=workspace_slug, params=query_params)
            return response.results
        except HttpError as e:
            if e.status_code == 404:
                return []
            raise

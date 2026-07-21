"""Module-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.modules import PaginatedArchivedModuleResponse, PaginatedModuleLiteResponse
from plane.models.query_params import LiteListQueryParams

from plane_mcp.client import get_plane_client_context


def register_module_tools(mcp: FastMCP) -> None:
    """Register module tools with the MCP server."""

    @mcp.tool()
    def list_modules(
        project_id: str,
        archived: bool = False,
        cursor: str | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
    ) -> PaginatedModuleLiteResponse | PaginatedArchivedModuleResponse:
        """List modules in a project."""
        client, workspace_slug = get_plane_client_context()
        try:
            params = LiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by)
            if archived:
                return client.modules.list_archived(workspace_slug=workspace_slug, project_id=project_id, params=params)
            return client.modules.list_lite(workspace_slug=workspace_slug, project_id=project_id, params=params)
        except HttpError as e:
            if e.status_code == 404:
                return PaginatedModuleLiteResponse.model_validate(
                    {"results": [], "total_count": 0, "count": 0, "next_page_results": False}
                )
            raise

"""Module-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.query_params import LiteListQueryParams

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools._compat import paginated_payload


def register_module_tools(mcp: FastMCP) -> None:
    """Register module tools with the MCP server."""

    @mcp.tool()
    def list_modules(
        project_id: str,
        archived: bool = False,
        cursor: str | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        """List modules in a project."""
        client, workspace_slug = get_plane_client_context()
        try:
            params = LiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by)
            if archived:
                response = client.modules.list_archived(
                    workspace_slug=workspace_slug, project_id=project_id, params=params
                )
            else:
                response = client.modules.list_lite(workspace_slug=workspace_slug, project_id=project_id, params=params)
        except HttpError as e:
            if e.status_code == 404:
                return paginated_payload([])
            raise
        return response.model_dump()

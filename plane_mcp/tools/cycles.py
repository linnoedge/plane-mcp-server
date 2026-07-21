"""Cycle-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.enums import CycleStatusEnum
from plane.models.query_params import CycleLiteListQueryParams, LiteListQueryParams

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools._compat import paginated_payload


def register_cycle_tools(mcp: FastMCP) -> None:
    """Register cycle tools with the MCP server."""

    @mcp.tool()
    def list_cycles(
        project_id: str,
        archived: bool = False,
        status: CycleStatusEnum | None = None,
        cursor: str | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        """List cycles in a project."""
        client, workspace_slug = get_plane_client_context()
        try:
            if archived:
                params = LiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by)
                response = client.cycles.list_archived(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=params.model_dump(exclude_none=True),
                )
            else:
                params = CycleLiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by, status=status)
                response = client.cycles.list_lite(workspace_slug=workspace_slug, project_id=project_id, params=params)
        except HttpError as e:
            if e.status_code == 404:
                return paginated_payload([])
            raise
        return response.model_dump()

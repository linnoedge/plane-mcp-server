"""Cycle-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.cycles import PaginatedArchivedCycleResponse, PaginatedCycleLiteResponse
from plane.models.enums import CycleStatusEnum
from plane.models.query_params import CycleLiteListQueryParams, LiteListQueryParams

from plane_mcp.client import get_plane_client_context


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
    ) -> PaginatedCycleLiteResponse | PaginatedArchivedCycleResponse:
        """List cycles in a project."""
        client, workspace_slug = get_plane_client_context()
        try:
            if archived:
                params = LiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by)
                return client.cycles.list_archived(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=params.model_dump(exclude_none=True),
                )
            params = CycleLiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by, status=status)
            return client.cycles.list_lite(workspace_slug=workspace_slug, project_id=project_id, params=params)
        except HttpError as e:
            if e.status_code == 404:
                return PaginatedCycleLiteResponse.model_validate(
                    {"results": [], "total_count": 0, "count": 0, "next_page_results": False}
                )
            raise

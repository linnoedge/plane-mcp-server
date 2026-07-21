"""Work item type-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.work_item_types import WorkItemType

from plane_mcp.client import get_plane_client_context


def register_work_item_type_tools(mcp: FastMCP) -> None:
    """Register work item type tools with the MCP server."""

    @mcp.tool()
    def list_work_item_types(
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemType]:
        """List work item types. Omit project_id for workspace-level types."""
        client, workspace_slug = get_plane_client_context()
        try:
            if project_id:
                return client.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id, params=params)
            return client.workspace_work_item_types.list(workspace_slug=workspace_slug)
        except HttpError as e:
            if e.status_code == 404:
                return []
            raise

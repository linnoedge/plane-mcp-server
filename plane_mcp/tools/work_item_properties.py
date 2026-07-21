"""Work item property-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.work_item_properties import WorkItemProperty

from plane_mcp.client import get_plane_client_context


def register_work_item_property_tools(mcp: FastMCP) -> None:
    """Register work item property tools with the MCP server."""

    @mcp.tool()
    def list_work_item_properties(
        work_item_type_id: str | None = None,
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemProperty]:
        """List custom work item properties."""
        client, workspace_slug = get_plane_client_context()
        try:
            if project_id and work_item_type_id:
                return client.work_item_properties.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_type_id=work_item_type_id,
                    params=params,
                )
            if project_id:
                return client.work_item_properties.list_project(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=params,
                )
            if work_item_type_id:
                return client.workspace_work_item_properties.list(
                    workspace_slug=workspace_slug,
                    work_item_type_id=work_item_type_id,
                    params=params,
                )
            return client.workspace_work_item_properties.list(workspace_slug=workspace_slug, params=params)
        except HttpError as e:
            if e.status_code == 404:
                return []
            raise

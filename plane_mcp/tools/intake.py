"""Intake work item-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.intake import IntakeWorkItem, PaginatedIntakeWorkItemResponse
from plane.models.query_params import PaginatedQueryParams

from plane_mcp.client import get_plane_client_context


def register_intake_tools(mcp: FastMCP) -> None:
    """Register intake work item tools with the MCP server."""

    @mcp.tool()
    def list_intake_work_items(project_id: str, params: dict[str, Any] | None = None) -> list[IntakeWorkItem]:
        """List all intake work items in a project."""
        client, workspace_slug = get_plane_client_context()
        query_params = PaginatedQueryParams.model_validate(params or {})
        try:
            response: PaginatedIntakeWorkItemResponse = client.intake.list(
                workspace_slug=workspace_slug, project_id=project_id, params=query_params
            )
        except HttpError as e:
            if e.status_code == 404:
                return []
            raise
        return response.results

"""Initiative-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.initiatives import Initiative, PaginatedInitiativeResponse

from plane_mcp.client import get_plane_client_context


def register_initiative_tools(mcp: FastMCP) -> None:
    """Register initiative tools with the MCP server."""

    @mcp.tool()
    def list_initiatives(params: dict[str, Any] | None = None) -> list[Initiative]:
        """List all initiatives in a workspace."""
        client, workspace_slug = get_plane_client_context()
        try:
            response: PaginatedInitiativeResponse = client.initiatives.list(
                workspace_slug=workspace_slug, params=params
            )
        except HttpError as e:
            if e.status_code == 404:
                return []
            raise
        return response.results

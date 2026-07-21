"""Label-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.labels import CreateLabel, Label, PaginatedLabelResponse, UpdateLabel

from plane_mcp.client import get_plane_client_context


def register_label_tools(mcp: FastMCP) -> None:
    """Register label tools with the MCP server."""

    @mcp.tool()
    def list_labels(project_id: str, params: dict[str, Any] | None = None) -> list[Label]:
        """List all labels in a project."""
        client, workspace_slug = get_plane_client_context()
        response: PaginatedLabelResponse = client.labels.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def create_label(
        project_id: str,
        name: str,
        color: str | None = None,
        description: str | None = None,
        parent: str | None = None,
        sort_order: float | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Label:
        """Create a new label."""
        client, workspace_slug = get_plane_client_context()
        return client.labels.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateLabel(
                name=name,
                color=color,
                description=description,
                parent=parent,
                sort_order=sort_order,
                external_source=external_source,
                external_id=external_id,
            ),
        )

    @mcp.tool()
    def retrieve_label(project_id: str, label_id: str) -> Label:
        """Retrieve a label by ID."""
        client, workspace_slug = get_plane_client_context()
        return client.labels.retrieve(workspace_slug=workspace_slug, project_id=project_id, label_id=label_id)

    @mcp.tool()
    def update_label(
        project_id: str,
        label_id: str,
        name: str | None = None,
        color: str | None = None,
        description: str | None = None,
        parent: str | None = None,
        sort_order: float | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Label:
        """Update a label by ID."""
        client, workspace_slug = get_plane_client_context()
        return client.labels.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            label_id=label_id,
            data=UpdateLabel(
                name=name,
                color=color,
                description=description,
                parent=parent,
                sort_order=sort_order,
                external_source=external_source,
                external_id=external_id,
            ),
        )

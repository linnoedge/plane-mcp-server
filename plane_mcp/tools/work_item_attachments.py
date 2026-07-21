"""Work item attachment tools for Plane MCP Server."""

from fastmcp import FastMCP

from plane_mcp.client import get_plane_client_context


def register_work_item_attachment_tools(mcp: FastMCP) -> None:
    """Register work item attachment tools with the MCP server."""

    @mcp.tool()
    def list_work_item_attachments(project_id: str, work_item_id: str) -> dict:
        """List all attachments for a work item."""
        client, workspace_slug = get_plane_client_context()
        result = client.work_items.attachments.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )
        return {"result": result}

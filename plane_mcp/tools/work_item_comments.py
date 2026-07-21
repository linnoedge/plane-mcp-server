"""Work item comment-related tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.enums import AccessEnum
from plane.models.work_items import (
    CreateWorkItemComment,
    PaginatedWorkItemCommentResponse,
    UpdateWorkItemComment,
    WorkItemComment,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_comment_tools(mcp: FastMCP) -> None:
    """Register work item comment tools with the MCP server."""

    @mcp.tool()
    def list_work_item_comments(
        project_id: str,
        work_item_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemComment]:
        """List comments for a work item."""
        client, workspace_slug = get_plane_client_context()
        response: PaginatedWorkItemCommentResponse = client.work_items.comments.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )
        return response.results

    @mcp.tool()
    def retrieve_work_item_comment(project_id: str, work_item_id: str, comment_id: str) -> WorkItemComment:
        """Retrieve a specific comment for a work item."""
        client, workspace_slug = get_plane_client_context()
        return client.work_items.comments.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            comment_id=comment_id,
        )

    @mcp.tool()
    def create_work_item_comment(
        project_id: str,
        work_item_id: str,
        comment_html: str | None = None,
        comment_json: dict[str, Any] | None = None,
        access: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemComment:
        """Create a comment for a work item."""
        client, workspace_slug = get_plane_client_context()
        return client.work_items.comments.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemComment(
                comment_html=comment_html,
                comment_json=comment_json,
                access=access if access in get_args(AccessEnum) else None,  # type: ignore[assignment]
                external_source=external_source,
                external_id=external_id,
            ),
        )

    @mcp.tool()
    def update_work_item_comment(
        project_id: str,
        work_item_id: str,
        comment_id: str,
        comment_html: str | None = None,
        comment_json: dict[str, Any] | None = None,
        access: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemComment:
        """Update a comment for a work item."""
        client, workspace_slug = get_plane_client_context()
        return client.work_items.comments.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            comment_id=comment_id,
            data=UpdateWorkItemComment(
                comment_html=comment_html,
                comment_json=comment_json,
                access=access if access in get_args(AccessEnum) else None,  # type: ignore[assignment]
                external_source=external_source,
                external_id=external_id,
            ),
        )

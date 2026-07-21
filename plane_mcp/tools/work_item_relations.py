"""Work item relation tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.enums import WorkItemRelationTypeEnum
from plane.models.work_items import CreateWorkItemRelation

from plane_mcp.client import get_plane_client_context

_CORE_RELATION_TYPES: tuple[str, ...] = get_args(WorkItemRelationTypeEnum)


def register_work_item_relation_tools(mcp: FastMCP) -> None:
    """Register work item relation tools with the MCP server."""

    @mcp.tool()
    def list_work_item_relations(project_id: str, work_item_id: str) -> dict[str, Any]:
        """List relations for a work item."""
        client, workspace_slug = get_plane_client_context()
        try:
            return client.work_items.relations._get(
                f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}/relations"
            )
        except HttpError as e:
            if e.status_code == 404:
                return {}
            raise

    @mcp.tool()
    def create_work_item_relation(
        project_id: str,
        work_item_id: str,
        work_item_ids: list[str],
        relation_type: str | None = None,
    ) -> None:
        """Create a core relation between work items."""
        if relation_type not in _CORE_RELATION_TYPES:
            raise ValueError(f"relation_type must be one of {list(_CORE_RELATION_TYPES)}")
        client, workspace_slug = get_plane_client_context()
        client.work_items.relations.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemRelation(
                relation_type=relation_type,  # type: ignore[arg-type]
                issues=work_item_ids,
            ),
        )

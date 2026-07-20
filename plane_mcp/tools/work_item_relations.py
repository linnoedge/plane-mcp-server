"""Work item relation tools for Plane MCP Server.

Consolidates the two relation systems behind one set of tools:

- Built-in dependencies — six fixed directional types (blocking, blocked_by,
  start_before, start_after, finish_before, finish_after).
- Custom relations — workspace-defined types created via
  list/create_work_item_relation_definition, each with an outward/inward label.

create_work_item_relation routes between them by which argument is supplied.
The LLM discovers both kinds in one place via list_work_item_relation_definitions
(built_in_dependencies + custom_definitions) and matches the user's wording to an
entry there, so a custom label like "dependent on" is never mistaken for the
built-in blocked_by.
"""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.enums import WorkItemRelationTypeEnum
from plane.models.work_items import (
    CreateWorkItemCustomRelation,
    CreateWorkItemDependency,
    CreateWorkItemRelation,
    DependencyTypeEnum,
    RemoveWorkItemRelation,
    WorkItemWithRelationType,
)

from plane_mcp.client import get_plane_client_context

# Built-in dependency relation_type values (sourced from the SDK contract).
_DEPENDENCY_TYPES: tuple[str, ...] = get_args(DependencyTypeEnum)
_CORE_RELATION_TYPES: tuple[str, ...] = get_args(WorkItemRelationTypeEnum)


def register_work_item_relation_tools(mcp: FastMCP) -> None:
    """Register work item relation tools with the MCP server."""

    @mcp.tool()
    def list_work_item_relations(
        project_id: str,
        work_item_id: str,
    ) -> dict[str, Any]:
        """List every relation for a work item.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the work item.

        Returns:
            dependencies: Built-in dependencies grouped by the six directions.
            custom: Custom relations grouped by definition label.
        """
        client, workspace_slug = get_plane_client_context()
        try:
            core = client.work_items.relations.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
        except HttpError as e:
            if e.status_code != 404:
                raise
            core = None
        try:
            dependencies = client.work_items.dependencies.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
        except HttpError as e:
            if e.status_code != 404:
                raise
            dependencies = None
        try:
            custom = client.work_items.custom_relations.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
        except HttpError as e:
            if e.status_code != 404:
                raise
            custom = None
        if core is None and dependencies is None and custom is None:
            return {
                "error": "work_item_relations_unavailable_on_plane_self_host",
                "hint": "This Plane self-host server does not expose work item relation endpoints.",
            }
        return {
            "core": core.model_dump() if core is not None else None,
            "dependencies": dependencies.model_dump() if dependencies is not None else None,
            "custom": (
                {label: [item.model_dump() for item in items] for label, items in custom.items()}
                if custom is not None
                else None
            ),
        }

    @mcp.tool()
    def create_work_item_relation(
        project_id: str,
        work_item_id: str,
        work_item_ids: list[str],
        relation_type: str | None = None,
        relation_definition_id: str | None = None,
        relation_definition_label: str | None = None,
    ) -> list[WorkItemWithRelationType]:
        """Relate a work item to one or more targets.

        Always call list_work_item_relation_definitions first and match the user's
        wording to an entry there. If it is a built_in_dependencies value, pass it
        as relation_type. If it is a custom_definitions entry, pass that
        definition's id as relation_definition_id and the matched outward/inward
        label as relation_definition_label (the label sets directionality).

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the source work item.
            work_item_ids: UUIDs of the target work items.
            relation_type: A built_in_dependencies value, or None for a custom relation.
            relation_definition_id: UUID of the relation definition (custom relations).
            relation_definition_label: Definition's outward or inward label (custom relations).

        Returns:
            List of created WorkItemWithRelationType objects.
        """
        client, workspace_slug = get_plane_client_context()
        if relation_type:
            if relation_type in _DEPENDENCY_TYPES:
                try:
                    return client.work_items.dependencies.create(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        work_item_id=work_item_id,
                        data=CreateWorkItemDependency(
                            relation_type=relation_type,  # type: ignore[arg-type]
                            work_item_ids=work_item_ids,
                        ),
                    )
                except HttpError as e:
                    if e.status_code != 404:
                        raise
            if relation_type in _CORE_RELATION_TYPES:
                client.work_items.relations.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=work_item_id,
                    data=CreateWorkItemRelation(
                        relation_type=relation_type,  # type: ignore[arg-type]
                        issues=work_item_ids,
                    ),
                )
                return []
            raise ValueError(
                f"relation_type must be one of {list(_CORE_RELATION_TYPES)}. For custom "
                "relationships, pass relation_definition_id + relation_definition_label "
                "from list_work_item_relation_definitions."
            )
        if relation_definition_id and relation_definition_label:
            return client.work_items.custom_relations.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                data=CreateWorkItemCustomRelation(
                    relation_definition_id=relation_definition_id,
                    relation_definition_type=relation_definition_label,
                    work_item_ids=work_item_ids,
                ),
            )
        raise ValueError(
            "Provide relation_type for a built-in dependency, or "
            "relation_definition_id + relation_definition_label for a custom "
            "relation (call list_work_item_relation_definitions to find one)."
        )

    @mcp.tool()
    def remove_work_item_relation(
        project_id: str,
        work_item_id: str,
        related_work_item_id: str,
        is_dependency: bool | None = None,
    ) -> None:
        """Remove ONE relation between two work items.

        A built-in dependency, core relation, and custom relation are removed independently.
        Set is_dependency from the relation the user named (see list_work_item_relations):
        True for a built-in dependency, False for a custom relation, or omit it for
        core relations such as duplicate/relates_to.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the source work item.
            related_work_item_id: UUID of the related work item.
            is_dependency: True to remove a built-in dependency, False to remove a
                custom relation, or None to remove a core relation.
        """
        client, workspace_slug = get_plane_client_context()
        if is_dependency is None:
            client.work_items.relations.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                data=RemoveWorkItemRelation(related_issue=related_work_item_id),
            )
            return
        remove = client.work_items.dependencies.remove if is_dependency else client.work_items.custom_relations.remove
        remove(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            related_work_item_id=related_work_item_id,
        )

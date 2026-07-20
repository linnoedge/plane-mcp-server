"""Workflow tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.workflows import (
    AttachWorkflowStates,
    CreateWorkflow,
    CreateWorkflowTransition,
    UpdateWorkflow,
    UpdateWorkflowTransition,
    Workflow,
    WorkflowTransition,
)

from plane_mcp.client import get_plane_client_context


def register_workflow_tools(mcp: FastMCP) -> None:
    """Register workflow-related tools with the MCP server."""

    @mcp.tool()
    def list_workflows(project_id: str) -> list[Workflow]:
        """List workflows for a project.

        Args:
            project_id: UUID of the project.

        Returns:
            List of workflows.
        """
        client, workspace_slug = get_plane_client_context()
        return client.workflows.list(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def create_workflow(
        project_id: str,
        name: str,
        description: str | None = None,
        is_active: bool | None = None,
        work_item_type_ids: list[str] | None = None,
    ) -> Workflow:
        """Create a workflow for a project.

        Args:
            project_id: UUID of the project.
            name: Workflow name.
            description: Workflow description.
            is_active: Whether the workflow is active.
            work_item_type_ids: Work item type UUIDs attached to this workflow.

        Returns:
            Created workflow.
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateWorkflow(
            name=name,
            description=description,
            is_active=is_active,
            work_item_type_ids=work_item_type_ids,
        )
        return client.workflows.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def update_workflow(
        project_id: str,
        workflow_id: str,
        name: str | None = None,
        description: str | None = None,
        is_active: bool | None = None,
        work_item_type_ids: list[str] | None = None,
    ) -> Workflow:
        """Update a workflow by ID.

        Args:
            project_id: UUID of the project.
            workflow_id: UUID of the workflow.
            name: Workflow name.
            description: Workflow description.
            is_active: Whether the workflow is active.
            work_item_type_ids: Work item type UUIDs attached to this workflow.

        Returns:
            Updated workflow.
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateWorkflow(
            name=name,
            description=description,
            is_active=is_active,
            work_item_type_ids=work_item_type_ids,
        )
        return client.workflows.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            workflow_id=workflow_id,
            data=data,
        )

    @mcp.tool()
    def manage_workflow_states(
        project_id: str,
        workflow_id: str,
        action: str,
        state_ids: list[str],
    ) -> None:
        """Attach or detach states on a workflow.

        Args:
            project_id: UUID of the project.
            workflow_id: UUID of the workflow.
            action: "attach" or "detach".
            state_ids: State UUIDs to attach or detach.
        """
        client, workspace_slug = get_plane_client_context()
        if action == "attach":
            client.workflows.states.attach(
                workspace_slug=workspace_slug,
                project_id=project_id,
                workflow_id=workflow_id,
                data=AttachWorkflowStates(state_ids=state_ids),
            )
            return None
        if action == "detach":
            for state_id in state_ids:
                client.workflows.states.detach(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    workflow_id=workflow_id,
                    state_id=state_id,
                )
            return None
        raise ValueError('action must be "attach" or "detach"')

    @mcp.tool()
    def list_workflow_transitions(project_id: str, workflow_id: str) -> list[WorkflowTransition]:
        """List transitions for a workflow.

        Args:
            project_id: UUID of the project.
            workflow_id: UUID of the workflow.

        Returns:
            List of workflow transitions.
        """
        client, workspace_slug = get_plane_client_context()
        return client.workflows.transitions.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            workflow_id=workflow_id,
        )

    @mcp.tool()
    def create_workflow_transition(
        project_id: str,
        workflow_id: str,
        state_id: str,
        transition_state_id: str,
        type: str | None = None,
        member_ids: list[str] | None = None,
        pre_rules: list[dict[str, Any]] | None = None,
        post_rules: list[dict[str, Any]] | None = None,
    ) -> WorkflowTransition | None:
        """Create a workflow state transition.

        Args:
            project_id: UUID of the project.
            workflow_id: UUID of the workflow.
            state_id: Source state UUID.
            transition_state_id: Target state UUID.
            type: Transition type.
            member_ids: User UUIDs allowed for this transition.
            pre_rules: Pre-transition rules.
            post_rules: Post-transition rules.

        Returns:
            Created transition, or None if it already exists.
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateWorkflowTransition(
            state_id=state_id,
            transition_state_id=transition_state_id,
            type=type,
            member_ids=member_ids,
            pre_rules=pre_rules,
            post_rules=post_rules,
        )
        return client.workflows.transitions.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            workflow_id=workflow_id,
            data=data,
        )

    @mcp.tool()
    def update_workflow_transition(
        project_id: str,
        workflow_id: str,
        transition_id: str,
        pre_rules: list[dict[str, Any]] | None = None,
        post_rules: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update a workflow state transition.

        Args:
            project_id: UUID of the project.
            workflow_id: UUID of the workflow.
            transition_id: UUID of the transition.
            pre_rules: Pre-transition rules.
            post_rules: Post-transition rules.
        """
        client, workspace_slug = get_plane_client_context()
        client.workflows.transitions.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            workflow_id=workflow_id,
            transition_id=transition_id,
            data=UpdateWorkflowTransition(pre_rules=pre_rules, post_rules=post_rules),
        )

    @mcp.tool()
    def delete_workflow_transition(project_id: str, workflow_id: str, transition_id: str) -> None:
        """Delete a workflow state transition.

        Args:
            project_id: UUID of the project.
            workflow_id: UUID of the workflow.
            transition_id: UUID of the transition.
        """
        client, workspace_slug = get_plane_client_context()
        client.workflows.transitions.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            workflow_id=workflow_id,
            transition_id=transition_id,
        )

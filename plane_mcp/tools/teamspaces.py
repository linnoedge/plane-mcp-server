"""Teamspace tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.teamspaces import CreateTeamspace, PaginatedTeamspaceResponse, Teamspace, UpdateTeamspace

from plane_mcp.client import get_plane_client_context


def register_teamspace_tools(mcp: FastMCP) -> None:
    """Register teamspace-related tools with the MCP server."""

    @mcp.tool()
    def list_teamspaces(params: dict[str, Any] | None = None) -> PaginatedTeamspaceResponse:
        """List teamspaces in the workspace.

        Args:
            params: Optional query parameters (e.g., per_page, cursor).

        Returns:
            Paginated list of teamspaces.
        """
        client, workspace_slug = get_plane_client_context()
        return client.teamspaces.list(workspace_slug=workspace_slug, params=params)

    @mcp.tool()
    def retrieve_teamspace(teamspace_id: str) -> Teamspace:
        """Retrieve a teamspace by ID.

        Args:
            teamspace_id: UUID of the teamspace.

        Returns:
            Teamspace object.
        """
        client, workspace_slug = get_plane_client_context()
        return client.teamspaces.retrieve(workspace_slug=workspace_slug, teamspace_id=teamspace_id)

    @mcp.tool()
    def create_teamspace(
        name: str,
        description_html: str | None = None,
        logo_props: dict[str, Any] | None = None,
        lead: str | None = None,
    ) -> Teamspace:
        """Create a teamspace.

        Args:
            name: Teamspace name.
            description_html: Teamspace description in HTML.
            logo_props: Logo properties.
            lead: UUID of the teamspace lead.

        Returns:
            Created teamspace.
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateTeamspace(name=name, description_html=description_html, logo_props=logo_props, lead=lead)
        return client.teamspaces.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def update_teamspace(
        teamspace_id: str,
        name: str | None = None,
        description_html: str | None = None,
        logo_props: dict[str, Any] | None = None,
        lead: str | None = None,
    ) -> Teamspace:
        """Update a teamspace by ID.

        Args:
            teamspace_id: UUID of the teamspace.
            name: Teamspace name.
            description_html: Teamspace description in HTML.
            logo_props: Logo properties.
            lead: UUID of the teamspace lead.

        Returns:
            Updated teamspace.
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateTeamspace(name=name, description_html=description_html, logo_props=logo_props, lead=lead)
        return client.teamspaces.update(workspace_slug=workspace_slug, teamspace_id=teamspace_id, data=data)

    @mcp.tool()
    def delete_teamspace(teamspace_id: str) -> None:
        """Delete a teamspace by ID.

        Args:
            teamspace_id: UUID of the teamspace.
        """
        client, workspace_slug = get_plane_client_context()
        client.teamspaces.delete(workspace_slug=workspace_slug, teamspace_id=teamspace_id)

    @mcp.tool()
    def list_teamspace_projects(teamspace_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """List projects in a teamspace.

        Args:
            teamspace_id: UUID of the teamspace.
            params: Optional query parameters (e.g., per_page, cursor).

        Returns:
            Paginated project response.
        """
        client, workspace_slug = get_plane_client_context()
        response = client.teamspaces.projects.list(
            workspace_slug=workspace_slug,
            teamspace_id=teamspace_id,
            params=params,
        )
        return response.model_dump()

    @mcp.tool()
    def manage_teamspace_projects(
        teamspace_id: str,
        action: str,
        project_ids: list[str],
    ) -> list[dict[str, Any]] | None:
        """Add or remove projects from a teamspace.

        Args:
            teamspace_id: UUID of the teamspace.
            action: "add" or "remove".
            project_ids: Project UUIDs to add or remove.

        Returns:
            Added projects for add, otherwise None.
        """
        client, workspace_slug = get_plane_client_context()
        if action == "add":
            projects = client.teamspaces.projects.add(
                workspace_slug=workspace_slug,
                teamspace_id=teamspace_id,
                project_ids=project_ids,
            )
            return [project.model_dump() for project in projects]
        if action == "remove":
            client.teamspaces.projects.remove(
                workspace_slug=workspace_slug,
                teamspace_id=teamspace_id,
                project_ids=project_ids,
            )
            return None
        raise ValueError('action must be "add" or "remove"')

    @mcp.tool()
    def list_teamspace_members(teamspace_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """List members in a teamspace.

        Args:
            teamspace_id: UUID of the teamspace.
            params: Optional query parameters (e.g., per_page, cursor).

        Returns:
            Paginated user response.
        """
        client, workspace_slug = get_plane_client_context()
        response = client.teamspaces.members.list(
            workspace_slug=workspace_slug,
            teamspace_id=teamspace_id,
            params=params,
        )
        return response.model_dump()

    @mcp.tool()
    def manage_teamspace_members(
        teamspace_id: str,
        action: str,
        member_ids: list[str],
    ) -> list[dict[str, Any]] | None:
        """Add or remove members from a teamspace.

        Args:
            teamspace_id: UUID of the teamspace.
            action: "add" or "remove".
            member_ids: User UUIDs to add or remove.

        Returns:
            Added members for add, otherwise None.
        """
        client, workspace_slug = get_plane_client_context()
        if action == "add":
            members = client.teamspaces.members.add(
                workspace_slug=workspace_slug,
                teamspace_id=teamspace_id,
                member_ids=member_ids,
            )
            return [member.model_dump() for member in members]
        if action == "remove":
            client.teamspaces.members.remove(
                workspace_slug=workspace_slug,
                teamspace_id=teamspace_id,
                member_ids=member_ids,
            )
            return None
        raise ValueError('action must be "add" or "remove"')

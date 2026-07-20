"""Work item-related tools for Plane MCP Server."""

from html import escape
from typing import Any, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.enums import PriorityEnum
from plane.models.query_params import RetrieveQueryParams, WorkItemQueryParams
from plane.models.work_items import (
    CreateWorkItem,
    PaginatedWorkItemResponse,
    UpdateWorkItem,
    WorkItem,
    WorkItemSearch,
)

from plane_mcp.client import get_plane_client_context


def _resolve_description_html(description_html: str | None, description_stripped: str | None) -> str | None:
    """Resolve the description_html to persist.

    Plane recomputes description_stripped server-side from description_html on
    every save, so a stripped value sent on write is silently discarded. When the
    caller supplies only plain text, wrap it into minimal HTML so the description
    actually lands. description_html always wins when both are given.
    """
    if description_html is not None:
        return description_html
    if description_stripped is not None:
        return "<p>" + escape(description_stripped).replace("\n", "<br/>") + "</p>"
    return None


def _id_list(values: Any) -> list[str]:
    if not values:
        return []
    ids = []
    for value in values:
        if isinstance(value, str):
            ids.append(value)
        elif isinstance(value, dict):
            value_id = value.get("id")
            if value_id:
                ids.append(value_id)
        elif hasattr(value, "id") and value.id:
            ids.append(value.id)
    return ids


def _work_item_detail_payload(
    client: Any,
    workspace_slug: str,
    project_id: str,
    work_item_id: str,
    params: RetrieveQueryParams,
) -> dict[str, Any]:
    query_params = params.model_dump(exclude_none=True)
    return client.work_items._get(
        f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}",
        params=query_params,
    )


def _work_item_identifier_payload(
    client: Any,
    workspace_slug: str,
    project_identifier: str,
    sequence_id: int,
    params: RetrieveQueryParams,
) -> dict[str, Any]:
    query_params = params.model_dump(exclude_none=True)
    return client.work_items._get(
        f"{workspace_slug}/work-items/{project_identifier}-{sequence_id}",
        params=query_params,
    )


def _item_value(item: dict[str, Any], field: str) -> Any:
    aliases = {
        "project_id": "project",
        "state_id": "state",
        "type": "type_id",
        "assignees__id": "assignees",
        "labels__id": "labels",
        "issue_module__module_id": "module_ids",
        "cycle_id": "cycle_id",
        "milestone_id": "milestone_id",
    }
    value = item.get(aliases.get(field, field))
    if isinstance(value, dict):
        return value.get("id")
    if isinstance(value, list):
        return [entry.get("id") if isinstance(entry, dict) else entry for entry in value]
    return value


def _fetch_project_work_items(
    client: Any,
    workspace_slug: str,
    project_id: str,
    order_by: str | None = None,
    expand: str | None = None,
    fields: str | None = None,
) -> list[dict[str, Any]]:
    params = WorkItemQueryParams(order_by=order_by, per_page=100, expand=expand, fields=fields)
    results = []
    while True:
        response = client.work_items._get(
            f"{workspace_slug}/projects/{project_id}/work-items",
            params=params.model_dump(exclude_none=True),
        )
        results.extend(response.get("results") or [])
        if not response.get("next_page_results") or not response.get("next_cursor"):
            break
        params.cursor = response.get("next_cursor")
    return results


def _count_items(items: list[dict[str, Any]], group_by: str | None, sub_group_by: str | None) -> dict[str, Any]:
    if group_by is None:
        return {
            "grouped_by": None,
            "sub_grouped_by": None,
            "total_count": len(items),
            "grouped_counts": {},
        }
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        actual = _item_value(item, group_by)
        keys = actual if isinstance(actual, list) else [actual]
        for raw_key in keys or [None]:
            key = str(raw_key or "None")
            entry = grouped.setdefault(key, {"count": 0})
            entry["count"] += 1
            if sub_group_by is not None:
                sub_actual = _item_value(item, sub_group_by)
                sub_keys = sub_actual if isinstance(sub_actual, list) else [sub_actual]
                sub_counts = entry.setdefault("sub_grouped_counts", {})
                for raw_sub_key in sub_keys or [None]:
                    sub_key = str(raw_sub_key or "None")
                    sub_counts[sub_key] = {"count": sub_counts.get(sub_key, {}).get("count", 0) + 1}
    return {
        "grouped_by": group_by,
        "sub_grouped_by": sub_group_by,
        "total_count": len(items),
        "grouped_counts": grouped,
    }


def register_work_item_tools(mcp: FastMCP) -> None:
    """Register all work item-related tools with the MCP server."""

    @mcp.tool()
    def list_work_items(
        project_id: str | None = None,
        pql: str | None = None,
        order_by: str | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> dict[str, Any]:
        """
        List work items.

        Omit project_id to list across the entire workspace.
        Pass project_id to scope results to a single project.

        For UUID fields (assignee, state, label, cycle, module, type,
        milestone) call the relevant list tool first to get the UUID.

        Args:
            project_id: UUID of the project. Omit for workspace-wide results.
            pql: Unsupported on Plane self-host. If provided, this tool returns an unsupported_pql error.
            order_by: Sort field; prefix `-` for descending (e.g. `-created_at`).
            per_page: 1-100, default 25.
            cursor: From previous response's next_cursor.
            expand: Comma-separated relations to expand (e.g. assignees,labels,state).
            fields: Sparse fieldset — id, name, sequence_id, priority, state,
                project, assignees, labels, type_id, description_html, start_date,
                target_date, created_at, updated_at, created_by, is_draft. Use
                `project` (not `project_id`) and `description_html` (there is no
                `description` field). Any field you omit or misname comes back
                null — a null here does NOT mean the item lacks that value; it
                means it was not requested. To read the description, include
                description_html; for the type, include type_id.
            external_id / external_source: Filter by external system.

        Returns:
            results: Paginated list of work items.
            total_count: True DB total, not page-bounded — use for counts.
            next_cursor: Cursor for the next page.
            prev_cursor: Cursor for the previous page.
        """
        client, workspace_slug = get_plane_client_context()

        if pql:
            return {
                "error": "unsupported_pql_on_plane_self_host",
                "failed_pql": pql,
                "hint": (
                    "This Plane self-host server ignores or does not support PQL. "
                    "Retry without pql and filter results client-side."
                ),
            }

        params = WorkItemQueryParams(
            order_by=order_by,
            per_page=per_page,
            cursor=cursor,
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
        )

        try:
            if project_id:
                response: PaginatedWorkItemResponse = client.work_items.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=params,
                )
            else:
                response = client.work_items.list_workspace(
                    workspace_slug=workspace_slug,
                    params=params,
                )
        except HttpError:
            raise

        return {
            "results": [
                item.model_dump() if hasattr(item, "model_dump") else item for item in (response.results or [])
            ],
            "total_count": response.total_count,
            "count": response.count,
            "next_cursor": response.next_cursor,
            "prev_cursor": response.prev_cursor,
            "next_page_results": response.next_page_results,
            "prev_page_results": response.prev_page_results,
            "shape_note": (
                "assignees, labels, state, project, parent, type_id are UUIDs unless expanded by the Plane API."
            ),
        }

    @mcp.tool()
    def count_work_items(
        project_id: str | None = None,
        pql: str | None = None,
        group_by: str | None = None,
        sub_group_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Count work items across the workspace with optional grouping.

        Use this for analytics — "how many urgent items?", "distribution by state?" —
        without fetching full work item payloads.

        Args:
            pql: Unsupported on Plane self-host. If provided, this tool returns an unsupported_pql error.
            group_by: Dimension to group counts by. Supported values:
                state_id, state__group, priority, project_id, type_id,
                labels__id, assignees__id, issue_module__module_id,
                release_work_items__release_id, cycle_id, milestone_id,
                created_by, target_date, start_date.
            sub_group_by: Second dimension for nested grouping (requires group_by).

        Returns:
            grouped_by: The group_by field used (null if none).
            sub_grouped_by: The sub_group_by field used (null if none).
            total_count: Total matching work items.
            grouped_counts: Dict of group_key → {count} or
                {count, sub_grouped_counts} when sub_group_by is set.
                Keys are UUIDs for FK fields, plain strings for priority/state__group,
                ISO dates for target_date/start_date, "None" for unset values.
        """
        client, workspace_slug = get_plane_client_context()
        if pql:
            return {
                "error": "unsupported_pql_on_plane_self_host",
                "failed_pql": pql,
                "hint": (
                    "This Plane self-host server ignores or does not support PQL. "
                    "Retry without pql and filter results client-side."
                ),
            }
        if project_id is None:
            return {
                "error": "workspace_count_unavailable_on_plane_self_host",
                "hint": "Pass project_id so count_work_items can count safely on Plane self-host.",
            }
        items = _fetch_project_work_items(client, workspace_slug, project_id, expand="assignees,labels,state")
        return _count_items(items, group_by, sub_group_by)

    @mcp.tool()
    def create_work_item(
        project_id: str,
        name: str,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        type_id: str | None = None,
        point: int | None = None,
        description_html: str | None = None,
        description_stripped: str | None = None,
        priority: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        sort_order: float | None = None,
        is_draft: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        parent: str | None = None,
        state: str | None = None,
        estimate_point: str | None = None,
        type: str | None = None,
    ) -> WorkItem:
        """
        Create a new work item.

        Args:
            project_id: UUID of the project
            name: Work item name (required)
            assignees: List of user IDs to assign to the work item
            labels: List of label IDs to attach to the work item
            type_id: UUID of the work item type
            point: Story point value
            description_html: HTML description of the work item
            description_stripped: Plain text description. Convenience only — it is
                wrapped into HTML and stored as description_html (Plane derives
                description_stripped server-side). Ignored if description_html is set.
            priority: Priority level (urgent, high, medium, low, none)
            start_date: Start date (ISO 8601 format)
            target_date: Target/end date (ISO 8601 format)
            sort_order: Sort order value
            is_draft: Whether the work item is a draft
            external_source: External system source name
            external_id: External system identifier
            parent: UUID of the parent work item
            state: UUID of the state
            estimate_point: Estimate point value
            type: Work item type identifier

        Returns:
            Created WorkItem object
        """
        client, workspace_slug = get_plane_client_context()

        validated_priority: PriorityEnum | None = (
            priority if priority in get_args(PriorityEnum) else None  # type: ignore[assignment]
        )

        data = CreateWorkItem(
            name=name,
            assignees=assignees,
            labels=labels,
            type_id=type_id,
            point=point,
            description_html=_resolve_description_html(description_html, description_stripped),
            priority=validated_priority,
            start_date=start_date,
            target_date=target_date,
            sort_order=sort_order,
            is_draft=is_draft,
            external_source=external_source,
            external_id=external_id,
            parent=parent,
            state=state,
            estimate_point=estimate_point,
            type=type,
        )

        return client.work_items.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def retrieve_work_item(
        project_id: str,
        work_item_id: str,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Retrieve a work item by ID.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            expand: Comma-separated fields to expand if supported by Plane.
            fields: Sparse fieldset. Use description_html for description. On Plane self-host v1.3.1,
                assignees, labels, state, project, parent, and type_id are returned as UUIDs.
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by

        Returns:
            Raw work item payload.
        """
        client, workspace_slug = get_plane_client_context()

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
            order_by=order_by,
        )

        return _work_item_detail_payload(client, workspace_slug, project_id, work_item_id, params)

    @mcp.tool()
    def retrieve_work_item_by_identifier(
        work_item_identifier: str,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Retrieve a work item by its full identifier (project prefix + sequence number).

        The identifier must be in PROJECT-N format where PROJECT is the project's
        identifier string and N is the sequence number. Both parts are required.

        Valid sparse `fields` values include: id, name, sequence_id, priority,
        state, project, workspace, parent, assignees, labels, type_id,
        start_date, target_date, created_at, updated_at, created_by,
        updated_by, is_draft, external_source, external_id, estimate_point.
        Use `project` (not `project_id`) to get the project UUID.

        If you need the project UUID from a short identifier like "SHO",
        use `list_projects()` instead — it returns `id` and `identifier`
        for every project.

        Args:
            work_item_identifier: Full work item identifier in PROJECT-N format
            expand: Comma-separated fields to expand if supported by Plane.
            fields: Comma-separated sparse fieldset (see valid values above). On Plane self-host v1.3.1,
                assignees, labels, state, project, parent, and type_id are returned as UUIDs.
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by

        Returns:
            Raw work item payload.
        """
        parts = work_item_identifier.rsplit("-", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            raise ValueError(
                f"Invalid work item identifier {work_item_identifier!r}. "
                "Expected PROJECT-N format where N is the sequence number."
            )
        project_identifier, sequence_str = parts
        client, workspace_slug = get_plane_client_context()

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
            order_by=order_by,
        )

        return _work_item_identifier_payload(client, workspace_slug, project_identifier, int(sequence_str), params)

    @mcp.tool()
    def update_work_item(
        project_id: str,
        work_item_id: str,
        name: str | None = None,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        type_id: str | None = None,
        point: int | None = None,
        description_html: str | None = None,
        description_stripped: str | None = None,
        priority: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        sort_order: float | None = None,
        is_draft: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        parent: str | None = None,
        state: str | None = None,
        estimate_point: str | None = None,
        type: str | None = None,
    ) -> WorkItem:
        """
        Update a work item by ID.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            name: Work item name
            assignees: List of user IDs to assign to the work item
            labels: List of label IDs to attach to the work item
            type_id: UUID of the work item type
            point: Story point value
            description_html: HTML description of the work item
            description_stripped: Plain text description. Convenience only — it is
                wrapped into HTML and stored as description_html (Plane derives
                description_stripped server-side). Ignored if description_html is set.
            priority: Priority level (urgent, high, medium, low, none)
            start_date: Start date (ISO 8601 format)
            target_date: Target/end date (ISO 8601 format)
            sort_order: Sort order value
            is_draft: Whether the work item is a draft
            external_source: External system source name
            external_id: External system identifier
            parent: UUID of the parent work item
            state: UUID of the state
            estimate_point: Estimate point value
            type: Work item type identifier

        Returns:
            Updated WorkItem object
        """
        client, workspace_slug = get_plane_client_context()

        validated_priority: PriorityEnum | None = (
            priority if priority in get_args(PriorityEnum) else None  # type: ignore[assignment]
        )

        data = UpdateWorkItem(
            name=name,
            assignees=assignees,
            labels=labels,
            type_id=type_id,
            point=point,
            description_html=_resolve_description_html(description_html, description_stripped),
            priority=validated_priority,
            start_date=start_date,
            target_date=target_date,
            sort_order=sort_order,
            is_draft=is_draft,
            external_source=external_source,
            external_id=external_id,
            parent=parent,
            state=state,
            estimate_point=estimate_point,
            type=type,
        )

        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item(project_id: str, work_item_id: str) -> None:
        """
        Delete a work item by ID.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.delete(workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id)

    @mcp.tool()
    def manage_work_item_assignee(
        project_id: str,
        work_item_id: str,
        add_user_id: str | None = None,
        remove_user_id: str | None = None,
    ) -> WorkItem:
        """
        Add or remove a single assignee on a work item without replacing the full list.

        Provide add_user_id, remove_user_id, or both. If both are given the
        removal is applied first, then the addition. Already-assigned users in
        add_user_id are silently skipped.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            add_user_id: UUID of the user to add as assignee
            remove_user_id: UUID of the user to remove from assignees

        Returns:
            Updated WorkItem object
        """
        client, workspace_slug = get_plane_client_context()
        current = _work_item_detail_payload(client, workspace_slug, project_id, work_item_id, RetrieveQueryParams())
        ids = _id_list(current.get("assignees"))
        if remove_user_id:
            ids = [uid for uid in ids if uid != remove_user_id]
        if add_user_id and add_user_id not in ids:
            ids.append(add_user_id)
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(assignees=ids),
        )

    @mcp.tool()
    def manage_work_item_label(
        project_id: str,
        work_item_id: str,
        add_label_id: str | None = None,
        remove_label_id: str | None = None,
    ) -> WorkItem:
        """
        Add or remove a single label on a work item without replacing the full list.

        Provide add_label_id, remove_label_id, or both. If both are given the
        removal is applied first, then the addition. Already-attached labels in
        add_label_id are silently skipped.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            add_label_id: UUID of the label to add
            remove_label_id: UUID of the label to remove

        Returns:
            Updated WorkItem object
        """
        client, workspace_slug = get_plane_client_context()
        current = _work_item_detail_payload(client, workspace_slug, project_id, work_item_id, RetrieveQueryParams())
        ids = _id_list(current.get("labels"))
        if remove_label_id:
            ids = [lid for lid in ids if lid != remove_label_id]
        if add_label_id and add_label_id not in ids:
            ids.append(add_label_id)
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(labels=ids),
        )

    @mcp.tool()
    def list_archived_work_items(
        project_id: str,
        pql: str | None = None,
        order_by: str | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
        expand: str | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """
        List archived work items in a project.

        Args:
            project_id: UUID of the project
            pql: Unsupported on Plane self-host. If provided, this tool returns an unsupported_pql error.
            order_by: Field to sort by; prefix with `-` for descending
                (default `-archived_at`).
            per_page: Results per page, 1-100 (default 100).
            cursor: Pagination cursor from a previous response's `next_cursor`.
            expand: Comma-separated related fields to expand.
            fields: Comma-separated sparse fieldset.

        Returns:
            Paginated envelope with results, total_count, next_cursor, prev_cursor.
        """
        client, workspace_slug = get_plane_client_context()
        if pql:
            return {
                "error": "unsupported_pql_on_plane_self_host",
                "failed_pql": pql,
                "hint": (
                    "This Plane self-host server ignores or does not support PQL. "
                    "Retry without pql and filter results client-side."
                ),
            }
        params = WorkItemQueryParams(
            order_by=order_by,
            per_page=per_page,
            cursor=cursor,
            expand=expand,
            fields=fields,
        )
        try:
            response = client.work_items.list_archived(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=params,
            )
        except HttpError as e:
            if e.status_code == 404:
                return {
                    "error": "archived_work_items_unavailable_on_plane_self_host",
                    "hint": "This Plane self-host server does not expose the archived work items endpoint.",
                }
            raise
        return {
            "results": [
                item.model_dump() if hasattr(item, "model_dump") else item for item in (response.results or [])
            ],
            "total_count": response.total_count,
            "count": response.count,
            "next_cursor": response.next_cursor,
            "prev_cursor": response.prev_cursor,
            "next_page_results": response.next_page_results,
            "prev_page_results": response.prev_page_results,
        }

    @mcp.tool()
    def manage_work_item_archive(project_id: str, work_item_id: str, archive: bool) -> None:
        """
        Archive or unarchive a work item.

        Only work items in a completed or cancelled state can be archived.
        Archived work items no longer appear in active work item lists.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            archive: True to archive the work item, False to unarchive it
        """
        client, workspace_slug = get_plane_client_context()
        if archive:
            client.work_items.archive(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
        else:
            client.work_items.unarchive(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )

    @mcp.tool()
    def search_work_items(
        query: str,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> WorkItemSearch:
        """
        Search work items by text across a workspace.

        Matches on work item name, sequence id, and project identifier (not
        description). For structured filtering, list work items without pql and
        filter client-side because Plane self-host does not support PQL reliably.

        Args:
            query: Free-text string matched against name, sequence id, and project identifier
            expand: Comma-separated list of related fields to expand in response
            fields: Comma-separated list of fields to include in response
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by. Prefix with '-' for descending

        Returns:
            WorkItemSearch object containing search results
        """
        client, workspace_slug = get_plane_client_context()

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
            order_by=order_by,
        )

        search_params = {"search": query}
        search_params.update(params.model_dump(exclude_none=True))
        response = client.work_items._get(f"{workspace_slug}/work-items/search", params=search_params)
        return WorkItemSearch.model_validate(response)

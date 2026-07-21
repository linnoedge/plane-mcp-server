"""
Simple integration test for Plane MCP Server.

Environment Variables Required:
    PLANE_TEST_API_KEY: API key for authentication
    PLANE_TEST_WORKSPACE_SLUG: Workspace slug for testing
    PLANE_TEST_MCP_URL: MCP server URL (default: http://localhost:8211)
"""

import asyncio
import os
import uuid

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


def get_config():
    """Load test configuration from environment."""
    api_key = os.getenv("PLANE_TEST_API_KEY", "")
    workspace_slug = os.getenv("PLANE_TEST_WORKSPACE_SLUG", "")
    mcp_url = os.getenv("PLANE_TEST_MCP_URL", "http://localhost:8211")

    if not api_key or not workspace_slug:
        raise RuntimeError("Missing required env vars: PLANE_TEST_API_KEY, PLANE_TEST_WORKSPACE_SLUG")

    return {
        "api_key": api_key,
        "workspace_slug": workspace_slug,
        "mcp_url": mcp_url,
    }


def extract_result(result):
    """Extract data from MCP tool result."""
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content
    if hasattr(result, "content") and result.content:
        import json

        content = result.content[0]
        if hasattr(content, "text"):
            try:
                return json.loads(content.text)
            except Exception:
                return {"raw": content.text}
    return {}


async def run_integration_test():
    """
    Full integration test:
    1. Create a project
    2. Create parent and child work items
    3. Update the child with the parent
    4. List/retrieve/update/search/count work items
    5. Delete work items
    6. Delete project
    """
    config = get_config()
    unique_id = uuid.uuid4().hex[:6]

    transport = StreamableHttpTransport(
        f"{config['mcp_url']}/http/api-key/mcp",
        headers={
            "x-workspace-slug": config["workspace_slug"],
            "authorization": f"Bearer {config['api_key']}",
        },
    )

    async with Client(transport=transport) as client:
        # 1. Create project
        print("Creating project...")
        project_result = await client.call_tool(
            "create_project",
            {
                "name": f"Test Project {unique_id}",
                "identifier": f"TP{unique_id[:3].upper()}",
                "description": "Integration test project",
            },
        )
        project = extract_result(project_result)
        project_id = project["id"]
        print(f"Created project: {project_id}")

        # 2. Create work item 1
        print("Creating work item 1...")
        work_item_1_result = await client.call_tool(
            "create_work_item",
            {
                "project_id": project_id,
                "name": f"Parent Work Item {unique_id}",
            },
        )
        work_item_1 = extract_result(work_item_1_result)
        work_item_1_id = work_item_1["id"]
        print(f"Created work item 1: {work_item_1_id}")

        # 3. Create work item 2
        print("Creating work item 2...")
        work_item_2_result = await client.call_tool(
            "create_work_item",
            {
                "project_id": project_id,
                "name": f"Child Work Item {unique_id}",
            },
        )
        work_item_2 = extract_result(work_item_2_result)
        work_item_2_id = work_item_2["id"]
        print(f"Created work item 2: {work_item_2_id}")

        # 4. Update work item 2 with work item 1 as parent
        print("Setting parent relationship...")
        await client.call_tool(
            "update_work_item",
            {
                "project_id": project_id,
                "work_item_id": work_item_2_id,
                "parent": work_item_1_id,
            },
        )
        print("Set work item 1 as parent of work item 2")

        print("Listing work items...")
        list_result = await client.call_tool("list_work_items", {"project_id": project_id})
        work_items = extract_result(list_result)["results"]
        assert any(item["id"] == work_item_1_id for item in work_items)

        print("Retrieving work item...")
        retrieve_result = await client.call_tool(
            "retrieve_work_item",
            {"project_id": project_id, "work_item_id": work_item_2_id},
        )
        assert extract_result(retrieve_result)["parent"] == work_item_1_id

        print("Searching work items...")
        search_result = await client.call_tool("search_work_items", {"query": f"Child Work Item {unique_id}"})
        assert extract_result(search_result)

        print("Counting work items...")
        count_result = await client.call_tool("count_work_items", {"project_id": project_id})
        assert extract_result(count_result)["total_count"] >= 2

        # 5. Delete work items
        print("Deleting work items...")
        await client.call_tool(
            "delete_work_item",
            {"project_id": project_id, "work_item_id": work_item_2_id},
        )
        print("Deleted work item 2")

        await client.call_tool(
            "delete_work_item",
            {"project_id": project_id, "work_item_id": work_item_1_id},
        )
        print("Deleted work item 1")

        # 6. Delete project
        print("Deleting project...")
        await client.call_tool("delete_project", {"project_id": project_id})
        print("Deleted project")

        print("Integration test passed!")


def test_full_integration():
    """Pytest entry point - runs the async integration test."""
    if not os.getenv("PLANE_TEST_API_KEY") or not os.getenv("PLANE_TEST_WORKSPACE_SLUG"):
        pytest.skip("Set PLANE_TEST_API_KEY and PLANE_TEST_WORKSPACE_SLUG to run live integration tests")
    asyncio.run(run_integration_test())


# Expected tools that should be registered with the self-host MCP server
EXPECTED_TOOLS = [
    "count_work_items",
    "create_label",
    "create_project",
    "create_state",
    "create_work_item",
    "create_work_item_comment",
    "create_work_item_link",
    "create_work_item_relation",
    "delete_project",
    "delete_work_item",
    "filter_work_items",
    "get_features",
    "get_me",
    "get_project_members",
    "get_workspace_members",
    "list_cycles",
    "list_initiatives",
    "list_intake_work_items",
    "list_labels",
    "list_modules",
    "list_pages",
    "list_projects",
    "list_states",
    "list_work_item_activities",
    "get_work_item_attachment_download_url",
    "list_work_item_attachments",
    "read_work_item_attachment",
    "upload_work_item_attachment_from_url",
    "delete_work_item_attachment",
    "list_work_item_comments",
    "list_work_item_links",
    "list_work_item_properties",
    "list_work_item_relations",
    "list_work_item_types",
    "list_work_items",
    "manage_project_archive",
    "manage_work_item_assignee",
    "manage_work_item_label",
    "retrieve_label",
    "retrieve_project",
    "retrieve_state",
    "retrieve_work_item",
    "retrieve_work_item_activity",
    "retrieve_work_item_by_identifier",
    "retrieve_work_item_comment",
    "retrieve_work_item_link",
    "search_work_items",
    "update_label",
    "update_project",
    "update_state",
    "update_work_item",
    "update_work_item_comment",
    "update_work_item_link",
]


async def run_tools_availability_test():
    """
    Test that all expected tools are available on the MCP server.
    This test verifies that all registered tools are exposed correctly.
    """
    config = get_config()

    transport = StreamableHttpTransport(
        f"{config['mcp_url']}/http/api-key/mcp",
        headers={
            "x-workspace-slug": config["workspace_slug"],
            "authorization": f"Bearer {config['api_key']}",
        },
    )

    async with Client(transport=transport) as client:
        # Get list of available tools
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}

        print(f"Found {len(tool_names)} tools on the server")

        # Check that all expected tools are available
        missing_tools = []
        for expected_tool in EXPECTED_TOOLS:
            if expected_tool not in tool_names:
                missing_tools.append(expected_tool)

        if missing_tools:
            print(f"Missing tools: {missing_tools}")
            raise AssertionError(f"The following expected tools are not available: {missing_tools}")

        print(f"All {len(EXPECTED_TOOLS)} expected tools are available!")
        print("Tools availability test passed!")


def test_tools_availability():
    """Pytest entry point - verifies all expected tools are registered."""
    if not os.getenv("PLANE_TEST_API_KEY") or not os.getenv("PLANE_TEST_WORKSPACE_SLUG"):
        pytest.skip("Set PLANE_TEST_API_KEY and PLANE_TEST_WORKSPACE_SLUG to run live integration tests")
    asyncio.run(run_tools_availability_test())


if __name__ == "__main__":
    asyncio.run(run_integration_test())

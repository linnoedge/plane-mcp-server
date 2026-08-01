# Plane MCP Server

A Model Context Protocol (MCP) server for Plane integration. This server provides tools and resources for interacting with Plane through AI agents.

## About this fork

This repository is a fork of the official Plane MCP Server maintained by Plane. It maintains a focused tool surface for self-hosted Plane instances, especially Plane `v1.3.1`, with compatibility behavior for endpoints that are unavailable or differ from newer deployments.

Use this fork when your MCP client connects to a self-hosted Plane workspace and the official server returns 404/403 responses, ignores PQL filters, or produces oversized responses from fallback endpoints.

## Features

* 🔧 **Plane Integration**: Interact with Plane APIs and services
* 🔌 **Multiple Transports**: Supports stdio, SSE, and streamable HTTP transports
* 🌐 **Remote & Local**: Works both locally and as a remote service
* 🛠️ **Extensible**: Easy to add new tools and resources

## Usage

The server supports stdio, streamable HTTP, and legacy SSE transports. Python 3.10 or newer is required.

### Install

Install the package from the current checkout:

```bash
uv pip install -e .
```

For development dependencies:

```bash
uv pip install -e ".[dev]"
```

### 1. Stdio Transport

Use stdio when the MCP server runs on the same machine as the client:

```json
{
  "mcpServers": {
    "plane": {
      "command": "plane-mcp-server",
      "args": ["stdio"],
      "env": {
        "PLANE_API_KEY": "<api-key>",
        "PLANE_WORKSPACE_SLUG": "<workspace-slug>",
        "PLANE_BASE_URL": "<plane-api-base-url>"
      }
    }
  }
}
```

### 2. Streamable HTTP Transport

Start the HTTP server with:

```bash
plane-mcp-server http
```

The HTTP process serves OAuth-authenticated streamable HTTP at `/http/mcp`, API-key-authenticated streamable HTTP at `/http/api-key/mcp`, and the legacy OAuth SSE transport at `/sse`. The API-key endpoint uses `Authorization: Bearer <api-key>` and `X-Workspace-Slug: <workspace-slug>`. Set `MCP_PATH_PREFIX` to prepend the same path segment to these routes.

### 3. SSE Transport (Legacy)

Running `plane-mcp-server http` also exposes the legacy OAuth-only SSE transport at `/sse`; there is no separate standalone SSE invocation. New clients should use streamable HTTP instead.

## Self-hosted Plane compatibility

This fork includes compatibility behavior for older/self-hosted Plane deployments:

- Falls back when `*-lite` endpoints are not available for projects, modules, cycles, members, and related resources.
- Uses the self-host-compatible search endpoint with `search=`.
- Does not expose PQL because affected self-hosted versions may ignore it and return misleading unfiltered data.
- Provides project-scoped SQLite filtering and counting for work items.
- Keeps workspace-wide work item counts conservative when the backend cannot provide them safely.

## Configuration

### Authentication

The server requires authentication via environment variables:

- `PLANE_BASE_URL`: Base URL for Plane API (default: `https://api.plane.so`) - Optional
- `PLANE_API_KEY`: API key for authentication (required for stdio transport)
- `PLANE_WORKSPACE_SLUG`: Workspace slug identifier (required for stdio transport)

**Example** (for stdio transport):
```bash
export PLANE_BASE_URL="https://api.plane.so"
export PLANE_API_KEY="<api-key>"
export PLANE_WORKSPACE_SLUG="<workspace-slug>"
```

**Note**: For remote HTTP transports, authentication is handled through the OAuth flow or API key/header authentication and does not require these stdio environment variables.

### Work-item SQLite cache

`filter_work_items` synchronizes a project into a shared, lightweight SQLite cache before applying filters. Plane remains the source of truth; the cache is a local projection for reliable filtering when backend filters are unavailable or inconsistent.

- Default path: `~/.cache/plane-mcp-server/work-items.sqlite3`
- Override path: set `PLANE_MCP_CACHE_PATH` to a writable SQLite file path.
- Sharing: SQLite WAL mode and a 30-second busy timeout allow MCP server processes on the same filesystem and configured with the same cache file to share cached data. This does not provide cache sharing across hosts or filesystems. A per-project lease prevents duplicate concurrent synchronization.
- Isolation: rows are scoped by Plane server, workspace, a hash of the active credential, and project. Raw credentials are not stored in the cache.

Each call fetches at most `max_pages` pages of up to `per_page` items, ordered by descending `updated_at`. An unfinished scan stores its cursor and returns `sync.status` as `partial`; a later call resumes it. A completed scan returns `synced`. If another process holds the synchronization lease, the call filters the current cache and returns `busy`. The `sync` object also reports `pages_fetched`, `items_upserted`, and the current `watermark`.

After initialization, synchronization normally fetches only records at or newer than the saved watermark. A `filter_work_items` call made after the 24-hour full-sync interval has elapsed triggers a full scan; there is no background reconciliation job. The full scan assigns a new generation and removes cached rows not seen in that completed scan, reconciling deletions. Bounded full scans resume across later filter calls, and deletion reconciliation occurs only after the scan completes.

`filter_work_items` supports:

- Text: case-insensitive name substring or an exact numeric sequence ID through `query`.
- Singular scalar filters: `priority`, `state_id`, `state_group`, `type_id`, `parent_id`, `cycle_id`, and `created_by`.
- Multi-value scalar filters: `priorities`, `state_ids`, and `state_groups`.
- Relations: singular or multi-value assignee and label IDs, plus `module_id`. `relation_match` selects `any` or `all` for each multi-value relation list.
- Inclusive ranges: `created_at`, `updated_at`, and `completed_at` timestamp bounds; `start_date` and `target_date` date bounds; and `sequence_id` numeric bounds, each expressed with `_from` and `_to` arguments.
- Flags: `is_draft`, `has_assignee`, `has_label`, `has_parent`, and `overdue`. Overdue means a target date before the current UTC date in a state group other than completed or cancelled.
- Pagination and ordering: `sort_by`, `sort_direction`, `offset`, and `limit`. Supported sort fields are `updated_at`, `created_at`, `sequence_id`, `priority`, `start_date`, `target_date`, and `name`; the work-item ID provides stable secondary ordering.

Singular and multi-value filters can be combined. Filter arguments do not narrow synchronization; they are applied to the cached project after the bounded sync. Range boundaries are inclusive. `limit` is 1-100 and controls returned rows, while `total_count` reports all cached matches before `offset` and `limit`; `count` reports rows in the current result.

Results are lightweight cache records rather than complete Plane work-item models. They contain IDs and cached scalar fields such as name, sequence, priority, state group, relation ID lists, dates, timestamps, and draft state. Use `retrieve_work_item` when full details, description content, or expanded objects are required. Until the initial scan reaches `synced`, filtered results and `total_count` cover only the pages cached so far; `busy` results may also lag until the active synchronizer finishes.

### Weekly report bundle collection

`collect_weekly_report_bundle` is a read-only high-level collector for `plane-weekly-report`. It accepts project and staff metadata plus timezone-aware `week_start`, exclusive `week_end`, and `collection_started_at` timestamps. Immediately after validating inputs—and before metadata or any project full scan—the collector captures one inclusive effective boundary. Requested `collection_started_at` must be at or after `week_end` and at or before that boundary. Updated candidate ranges and activity histories are capped at the captured boundary, so writes made during collection do not leak into the report. Each forced scan attempt starts at cursor `None` with a fresh generation; a rerun after a failed later page therefore rescans completely and safely reconciles deleted cache rows. Normal incremental cache scans retain their resumable cursor behavior. The three candidate branches (updated range, started, and active overdue before the exclusive week end) are read from one SQLite snapshot shared by every project, so branches and offset pages cannot observe concurrent writes. No all-items payload is returned to the agent.

Metadata and activity pagination require a stable `total_count` on every page and an exact cumulative raw scanned count, allowing a normal short final page. Activities request deterministic `order_by=created_at`. The collector fetches complete project states, labels, project work-item types, and workspace work-item types; self-host servers that return 404 for workspace types use project types as the authoritative fallback. It fails closed unless every candidate state, label, and type UUID resolves. Real Plane state activities identify their item with `issue`; the collector validates `issue`, `work_item`, and `work_item_id`, normalizes retained activities to the candidate ID, and preserves state names. Busy cache synchronization uses bounded exponential backoff; the 180-second default timeout exceeds the 120-second cache lease. Any incomplete sync or pagination, changing/mismatched totals, unresolved reference, truncation, or identity mismatch fails the call. On success, top-level `tool` identifies `collect_weekly_report_bundle`; top-level `requested` contains exactly the five validated input arguments `projects`, `staff`, `week_start`, `week_end`, and `collection_started_at`, preserving their original canonical representations for exact finalizer binding. Normalized UTC request bounds and the effective boundary are recorded separately in `collection`. The result also contains `{metadata, results}` work-item and activity aggregates, exact completeness flags, and `collection` metadata that separately records the effective boundary. It never invokes MCP tools internally or writes files.

### OAuth redirect URIs

For the OAuth HTTP/SSE transports, the server validates each client's redirect URI against an allowlist. Common MCP clients (Cursor, VS Code, Claude.ai, ChatGPT connectors, localhost) are allowed by default.

To onboard a new client without a code change or release, append extra patterns via an environment variable:

- `PLANE_OAUTH_ALLOWED_REDIRECT_URIS`: Comma-separated redirect URI patterns appended to the built-in allowlist.

```bash
export PLANE_OAUTH_ALLOWED_REDIRECT_URIS="https://client.example.com/callback,https://connector.example.com/oauth/*"
```

Patterns support glob matching (`*` matches any port, path segment, or subdomain). For security, keep the host pinned and wildcard only the port/path.

### Logging

The server emits structured JSON logs. Each tool call is logged with its tool name, duration, status, and (when available) the opaque user id and workspace slug.

- `LOG_USER_INFO`: When `true`, include user info (PII such as the display name) in logs alongside the opaque user id. Defaults to `false` so PII is never logged unless explicitly opted in. Only the OAuth and API key/header HTTP transports carry a display name; stdio is unaffected.

```bash
export LOG_USER_INFO="true"
```

## Available Tools

The currently registered tool surface is intentionally limited to operations supported reliably by this server:

| Area | Tools |
|------|-------|
| Projects | `list_projects`, `filter_projects`, `create_project`, `retrieve_project`, `update_project`, `delete_project`, `manage_project_archive`, `get_project_members` |
| Work items | `list_work_items`, `filter_work_items`, `count_work_items`, `collect_weekly_report_bundle`, `create_work_item`, `retrieve_work_item`, `retrieve_work_item_by_identifier`, `update_work_item`, `delete_work_item`, `manage_work_item_assignee`, `manage_work_item_label`, `search_work_items` |
| Attachments | `list_work_item_attachments`, `get_work_item_attachment_download_url`, `upload_work_item_attachment_from_url`, `delete_work_item_attachment`, `read_work_item_attachment` |
| Comments | `list_work_item_comments`, `retrieve_work_item_comment`, `create_work_item_comment`, `update_work_item_comment` |
| Links | `list_work_item_links`, `retrieve_work_item_link`, `create_work_item_link`, `update_work_item_link` |
| Relations and activities | `list_work_item_relations`, `create_work_item_relation`, `list_work_item_activities`, `retrieve_work_item_activity` |
| Project metadata | `list_cycles`, `list_modules`, `list_intake_work_items`, `list_labels`, `create_label`, `retrieve_label`, `update_label`, `list_states`, `create_state`, `retrieve_state`, `update_state` |
| Workspace metadata | `list_initiatives`, `list_pages`, `list_work_item_properties`, `list_work_item_types`, `get_workspace_members`, `get_features`, `get_me` |

## Development

### Running Tests

```bash
pytest
```

### Code Formatting and Linting

```bash
ruff format plane_mcp/
ruff check plane_mcp/
```

## License

MIT License - see LICENSE for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Deprecation Notice

⚠️ **The Node.js-based `plane-mcp-server` is deprecated and no longer maintained.**

This repository represents the new Python+FastMCP based implementation of the Plane MCP server. If you were using the previous Node.js version, please migrate to this Python-based version for continued support and updates.

The new implementation offers:
- Better type safety with Pydantic models
- Improved performance with FastMCP
- Enhanced tool coverage
- Active maintenance and development

For migration assistance, please refer to the configuration examples in this README or open an issue for support.

**Old Node.js Configuration (Deprecated):**

If you were using the previous Node.js-based `@makeplane/plane-mcp-server`, your configuration looked like this:

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": [
        "-y",
        "@makeplane/plane-mcp-server"
      ],
      "env": {
        "PLANE_API_KEY": "<YOUR_API_KEY>",
        "PLANE_API_HOST_URL": "<HOST_URL_FOR_SELF_HOSTED>",
        "PLANE_WORKSPACE_SLUG": "<YOUR_WORKSPACE_SLUG>"
      }
    }
  }
}
```

**Please migrate to the new Python-based configuration shown in the Usage section above.**


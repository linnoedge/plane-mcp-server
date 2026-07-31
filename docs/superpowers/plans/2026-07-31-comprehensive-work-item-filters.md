# Comprehensive Work-Item Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add comprehensive, validated SQLite-backed work-item filters, generic README documentation, and release/install verification.

**Architecture:** Extend the existing lightweight cache projection and migrate SQLite in place. Normalize temporal values before persistence, construct parameterized SQL for scalar/range predicates, use SQLite JSON functions for relation arrays, and keep synchronization independent from filter arguments.

**Tech Stack:** Python 3.10+, stdlib SQLite/JSON/datetime, FastMCP, plane-sdk, pytest, Ruff, uv.

## Global Constraints

- Keep the Plane API as source of truth and filter cached records only after bounded synchronization.
- Do not add dependencies.
- Keep cache namespaces isolated by server, workspace, credential hash, and project.
- Preserve existing singular filter parameters.
- Range boundaries are inclusive.
- README and examples must contain no organization-specific domains, IDs, users, credentials, or product names.

---

### Task 1: Expand and migrate cache records

**Files:**
- Modify: `plane_mcp/work_item_cache.py`
- Modify: `tests/test_work_item_cache.py`

**Interfaces:**
- Consumes: Plane work-item dictionaries from `fetch_page(cursor)`.
- Produces: normalized columns for type, parent, cycle, modules, creator, created/updated/start/target/completed dates, and draft state.

- [ ] Add failing tests that open the prior schema, instantiate `WorkItemCache`, and assert all new columns exist.
- [ ] Add failing tests that upsert expanded/scalar relations and timezone-bearing timestamps and assert normalized records.
- [ ] Run `uv run pytest tests/test_work_item_cache.py -v` and confirm schema/record failures.
- [ ] Add columns with `ALTER TABLE` migration checks and explicit-column INSERT/UPSERT SQL.
- [ ] Normalize timestamps to UTC ISO strings and dates to `YYYY-MM-DD`.
- [ ] Run focused tests and confirm they pass.

### Task 2: Implement comprehensive SQL filtering

**Files:**
- Modify: `plane_mcp/work_item_cache.py`
- Modify: `tests/test_work_item_cache.py`

**Interfaces:**
- Consumes: singular/multi scalar filters, relation any/all filters, ranges, flags, sorting, offset, and limit.
- Produces: `{"results": list[dict], "total_count": int}` from `WorkItemCache.filter_items`.

- [ ] Add failing tests for query text, singular and multi scalar filters.
- [ ] Add failing tests for relation any/all matching and relation IDs.
- [ ] Add failing tests for inclusive temporal/numeric ranges, null flags, draft, and overdue.
- [ ] Add failing tests for sort direction, offset/limit, and total count.
- [ ] Add failing validation tests for malformed ranges, dates, enums, and sort fields.
- [ ] Run focused tests and confirm expected failures.
- [ ] Implement parameterized SQL predicate construction and SQLite JSON predicates.
- [ ] Implement validation and stable secondary ordering by ID.
- [ ] Run focused tests and confirm they pass.

### Task 3: Expose filter API through FastMCP

**Files:**
- Modify: `plane_mcp/tools/work_items.py`
- Modify: `tests/test_work_items.py`

**Interfaces:**
- Consumes: all public filter arguments from `filter_work_items`.
- Produces: `results`, `count`, `total_count`, `sync`, and generic `filter_note`.

- [ ] Add failing tool tests asserting new arguments are forwarded to cache filtering.
- [ ] Add failing tests asserting the Plane sync projection contains every cached field and remains ordered by `-updated_at`.
- [ ] Add backward-compatibility tests for existing singular calls.
- [ ] Run `uv run pytest tests/test_work_items.py -v` and confirm failures.
- [ ] Extend the FastMCP function signature/docstring and fixed sparse projection.
- [ ] Adapt result assembly to include `total_count`.
- [ ] Run focused tests and confirm they pass.

### Task 4: Update generic README documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: implemented tool behavior and environment variables.
- Produces: generic installation, cache, filtering, synchronization, result, and limitation documentation.

- [ ] Read the full README and remove stale claims conflicting with current tools.
- [ ] Document default cache path, `PLANE_MCP_CACHE_PATH`, WAL sharing, credential isolation, sync states, reconciliation, supported filters, and lightweight results.
- [ ] Search README for local domains, project IDs, user IDs, API keys, and BIZ/FAM-specific text; ensure none are present.
- [ ] Run Ruff and README-specific content searches.

### Task 5: Verify, release, install, restart, and smoke test

**Files:**
- Modify outside repo: `~/.config/opencode/opencode.json` version pin only.

**Interfaces:**
- Consumes: verified repository state and next `v0.2.10-selfhost.N` tag.
- Produces: pushed commit/tag, GitHub release, updated running OpenCode service, and real MCP smoke-test evidence.

- [ ] Run `uv run pytest` and require zero failures.
- [ ] Run `uv run ruff check plane_mcp tests` and `uv run ruff format --check plane_mcp tests`.
- [ ] Run `git diff --check`, inspect status/diff/log, and review security/generic-documentation constraints.
- [ ] Run real-server smoke tests for text, multi-value, range, sorting, total count, and backward-compatible filters.
- [ ] Commit only intended files with repository-style messages.
- [ ] Create and push the next annotated self-host tag and GitHub release.
- [ ] Update the OpenCode MCP version pin, restart `dev.opencode.server` via launchctl, and verify its PID changed.
- [ ] Call the installed MCP for generic project discovery and comprehensive work-item filters; require valid cached results and sync metadata.

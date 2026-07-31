# Activity Client Filtering Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make work-item activity history filters reliable on Plane self-host and accept explicit null relation matching for cached work-item filters.

**Architecture:** The activity tool will fetch cursor pages without relying on ignored server filters, normalize timestamps, filter records locally, and return bounded result metadata. The work-item filter tool will normalize `relation_match=None` to `"any"` before calling SQLite.

**Tech Stack:** Python 3.10+, FastMCP, plane-sdk, pytest, Ruff, uv.

## Global Constraints

- Do not add dependencies.
- Preserve backward-compatible activity calls using `params`.
- Keep scans bounded by `limit`, `per_page`, and `max_pages`.
- Use inclusive `from`/`to` timestamps and strict ISO-8601 validation.
- Release as `v0.2.10-selfhost.11`, update OpenCode, restart its launch agent, and smoke test the installed MCP.

---

### Task 1: Client-side activity filtering

**Files:**
- Modify: `plane_mcp/tools/work_item_activities.py`
- Create: `tests/test_work_item_activities.py`

**Interfaces:**
- Consumes: project/work-item IDs, legacy `params`, created/updated ranges, activity type, verb, field, actor, and scan bounds.
- Produces: bounded activity list response with results/count/pages scanned/continuation metadata.

- [ ] Add failing pagination/range tests proving ignored server filters do not leak old records.
- [ ] Add failing exact-filter and malformed timestamp tests.
- [ ] Implement strict UTC normalization, cursor scanning, and local predicates.
- [ ] Run focused tests and Ruff.

### Task 2: Nullable relation matching

**Files:**
- Modify: `plane_mcp/tools/work_items.py`
- Modify: `tests/test_work_items.py`

**Interfaces:**
- Consumes: `relation_match: "any" | "all" | None`.
- Produces: cache call with `"any"` when omitted or explicitly null.

- [ ] Add a failing explicit-null tool test and schema assertion.
- [ ] Normalize null before cache filtering.
- [ ] Run focused tests and Ruff.

### Task 3: Verify and release

**Files:**
- Modify: `README.md` only if activity tool docs require updates.
- Modify outside repository: `~/.config/opencode/opencode.json` version pin.

**Interfaces:**
- Produces: verified tag/release and restarted installed MCP.

- [ ] Run full pytest, Ruff lint/format, and diff checks.
- [ ] Reproduce future activity range returning zero records.
- [ ] Commit, tag, push, and create `.11` release.
- [ ] Update OpenCode pin, restart `dev.opencode.server`, verify PID, and smoke test both fixes.

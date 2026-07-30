# Comprehensive Cached Work-Item Filters Design

## Goal

Expand `filter_work_items` into a general-purpose, project-scoped query tool backed by the shared SQLite work-item cache, while preserving bounded incremental synchronization and generic documentation.

## Architecture

The Plane API remains the source of truth. Each filter call first performs or resumes a bounded synchronization ordered by descending `updated_at`, then executes the requested filter entirely in SQLite. Cache namespaces remain isolated by Plane server, workspace, credential hash, and project.

The cache stores only filterable scalar fields and relation identifiers. It does not store descriptions, attachments, comments, activities, or expanded objects. Callers use `retrieve_work_item` when full details are required.

## Cached Fields

Each work-item record stores:

- Identity: `id`, `name`, `sequence_id`
- Classification: `priority`, `state_id`, `state_group`, `type_id`
- Relations: `assignee_ids`, `label_ids`, `parent_id`, `cycle_id`, `module_ids`, `created_by`
- Dates: `created_at`, `updated_at`, `start_date`, `target_date`, `completed_at`
- Flags: `is_draft`
- Reconciliation metadata: `generation`

Timestamps are converted to UTC before persistence and comparison. Dates use ISO `YYYY-MM-DD`. Existing SQLite databases are migrated in place by adding missing columns.

## Filter Interface

Existing singular parameters remain backward compatible:

- `priority`
- `state_id`
- `state_group`
- `assignee_id`
- `label_id`

New parameters:

- Text: `query`, matched case-insensitively against `name` and exact/string `sequence_id`
- Multi-value: `priorities`, `state_ids`, `state_groups`, `assignee_ids`, `label_ids`
- Relation matching: `relation_match="any" | "all"` for multi-assignee and multi-label filters
- Relations: `type_id`, `parent_id`, `cycle_id`, `module_id`, `created_by`
- Date/time ranges: `created_at_from/to`, `updated_at_from/to`, `start_date_from/to`, `target_date_from/to`, `completed_at_from/to`
- Numeric range: `sequence_id_from/to`
- Flags: `is_draft`, `has_assignee`, `has_label`, `has_parent`, `overdue`
- Result controls: `sort_by`, `sort_direction`, `offset`, `limit`

Range boundaries are inclusive. `overdue=true` means target date is before the current UTC date and state group is neither completed nor cancelled. Singular and multi-value forms are combined with AND; values inside a multi-value scalar filter use OR.

## Query and Result Behavior

SQLite applies scalar predicates directly. JSON-array relation predicates use SQLite JSON functions. Every query remains scoped by server, workspace/credential, and project.

Allowed sort fields are `updated_at`, `created_at`, `sequence_id`, `priority`, `start_date`, `target_date`, and `name`. The default is `updated_at desc`. Invalid enums, ranges, date formats, and sort fields raise a clear value error before SQL execution.

The result contains:

- `results`: lightweight cached records
- `count`: number returned after offset/limit
- `total_count`: all matching cached records before pagination
- `sync`: synchronization status and metrics
- `filter_note`: generic cache behavior note

## Synchronization

Synchronization always requests the full cache projection regardless of active filters. Initial synchronization and daily reconciliation may return `partial` when they reach `max_pages`; later calls resume from the stored cursor. Incremental synchronization scans from the newest page until it reaches records older than the saved watermark. Database leases prevent duplicate project synchronization by local MCP processes.

If synchronization is busy, filtering still reads the latest committed cache. If synchronization fails, the lease is released and the API error is propagated.

## README

README updates describe:

- Supported transports and existing project capabilities
- Shared work-item cache behavior and default location
- Optional `PLANE_MCP_CACHE_PATH`
- Complete cached filter parameter groups
- Initial, incremental, partial, busy, and daily reconciliation behavior
- Lightweight result shape and use of `retrieve_work_item` for details
- Same-filesystem sharing limitation

Documentation contains no organization-specific domains, project identifiers, user identities, credentials, or product-specific examples.

## Testing

Unit tests cover schema migration, timestamp normalization, every filter category, any/all relation semantics, inclusive boundaries, null/boolean predicates, overdue behavior, validation, sort/pagination, total count, cache projection, synchronization continuation, reconciliation, leases, and backward compatibility.

Verification includes the complete test suite, Ruff lint/format checks, SQLite migration against an existing cache, and installed-release smoke tests against generic project discovery and filtering flows.

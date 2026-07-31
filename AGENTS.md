# AGENTS.md

## Mandatory Tool-Change Documentation

Every change that adds, removes, renames, or modifies an MCP tool, parameter, default, filter, return shape, pagination behavior, validation rule, compatibility behavior, or limitation MUST update the tool's public docstring in the same change.

The public docstring MUST clearly tell an agent:

- when to use the tool instead of similar tools;
- which parameters implement the changed behavior;
- whether filtering is server-side or client-side;
- timestamp format, timezone, boundary, and inclusivity semantics;
- pagination, scan bounds, truncation, and continuation behavior;
- return shape and compatibility behavior;
- known self-host API limitations that affect correct usage.

Descriptions such as "list items", "filter results", or "client-side filtering" alone are insufficient. Parameter names without usage semantics are insufficient.

Every tool behavior change MUST include a regression test that inspects the FastMCP-exposed tool description and input schema, not only direct Python behavior. The test MUST prove that an MCP agent can discover the changed behavior from `tools/list` without reading repository source.

Before completing a tool change, verify all of the following:

1. The behavior test passes.
2. The exposed description names the relevant parameters and explains when to use them.
3. The exposed input schema contains the intended parameters, types, defaults, and nullability.
4. A realistic MCP call using the documented parameters returns the expected result.
5. README or other user-facing documentation is updated when the behavior is broadly useful or changes compatibility.

A tool change is incomplete until its agent-facing documentation and discovery tests are complete.

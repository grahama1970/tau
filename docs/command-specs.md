# Declarative Custom Slash Commands

Tau can load bounded custom slash commands from JSON manifests governed by
`tau.command_spec_policy.v1`. A manifest is a declaration, not executable code:
Tau validates it, compiles it into an existing governed route, and emits command
policy and execution receipts when the command runs.

## Resource Directories

Command specs are discovered in increasing precedence order:

1. `~/.tau/commands`
2. `~/.agents/commands`
3. `<project>/.agents/commands`
4. `<project>/.tau/commands`

Higher-precedence manifests with the same command name override lower-precedence
manifests. Duplicate names at the same precedence are rejected with
`duplicate_custom_name_same_precedence`.

Use `/command-specs` inside Tau to inspect accepted manifests, rejected
manifests, exact reason codes, and active resource directories.

## Policy Boundary

Accepted manifests must declare:

- `schema: "tau.command_spec_policy.v1"`
- command `name`, `description`, and `usage`
- enum argument schema and static completions
- an allowlisted route such as `builtin_command`
- input/output types
- `side_effect_class`
- project-root-relative `resources`
- `limits.timeout_seconds` and `limits.max_output_bytes`
- explicit `requirements.network`, `requirements.subprocess`, and
  `requirements.provider`
- expected receipt schemas

Tau rejects manifests that collide with built-ins, duplicate another custom
command at the same precedence, traverse outside the project root, declare
subprocess/network/provider access, omit permission classes for side effects,
target missing routes, exceed limits, or attempt to control security/policy
settings.

## Receipts

Each admitted execution writes:

- `tau.command_spec_policy.v1`
- `tau.command_execution_receipt.v1`

Side-effect commands also stop at Tau's existing permission boundary and require
an action/resource-bound approval receipt before mutation.

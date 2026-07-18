# Understand codebase structure and contents

## Goal

Build an evidence-based overview of the repository so the user can quickly understand its purpose, directory organization, execution flow, major modules, configuration, and extension points before making code changes.

## Requirements

- Inspect repository documentation, build configuration, source code, scripts, tests, and representative configuration files.
- Identify the primary entry points and trace the main runtime flow through the important modules.
- Explain the responsibility of each major directory and the relationships between significant components.
- Record how to build, configure, and run the project when those instructions are available in the repository.
- Highlight important interfaces, data flows, extension points, and implementation constraints supported by code evidence.
- Keep the investigation read-only except for Trellis planning and journal artifacts.

## Acceptance Criteria

- [x] The final report summarizes the repository's purpose and technology stack.
- [x] The final report provides a directory-level map with major files and responsibilities.
- [x] The final report traces the main execution path from startup to the core simulation or control loop.
- [x] The final report explains configuration, build, run, and test workflows found in the repository.
- [x] The final report identifies key abstractions, dependencies, and likely extension points with file references.
- [x] Uncertain conclusions are explicitly distinguished from facts confirmed in the repository.

## Out of Scope

- Modifying production code or project behavior.
- Performing a full correctness, performance, or security audit.
- Reverse-engineering external dependencies beyond what is needed to explain their role in this repository.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

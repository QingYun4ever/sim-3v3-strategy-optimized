# Implementation Plan: 比赛流程分析文档

## Ordered Checklist

1. Read official PDFs and extract rule-defined match states, restart events, referee instructions, penalties, pause/resume, and end conditions.
2. Inventory repository source, configuration, behavior tree files, and existing analysis documents.
3. Locate program startup and main decision loop.
4. Trace initialization of robots, communication, localization, world state, and match information.
5. Trace how referee or match-state information is received, stored, interpreted, and consumed.
6. Trace role assignment, task selection, attacking, defending, goalkeeper, set-piece, and action execution flows.
7. Build chronological match-flow narrative from startup through match end.
8. Build rule-to-code correspondence table with implementation classification.
9. Review existing code analysis documents against official rules and current Demo code.
10. Write the final Markdown analysis document.
11. Summarize the confirmed flow, main code entries, code/rule gaps, and unresolved questions for the user.

## Validation Policy

- Do not run build, test, lint, type-check, format, simulation, deployment, development servers, runtime processes, or IDE diagnostics.
- Validate conclusions only by reading source, documents, configuration, and provided PDFs.

## Expected Output

- Recommended document path: `docs/match-flow-rules-analysis.md` unless repository evidence suggests a more appropriate existing documentation directory.

## Rollback / Safety

- Only Trellis planning artifacts and the requested Markdown analysis document should be created or edited.
- If unexpected unrelated user changes are found in a file that would need editing, stop and ask how to proceed.

# Evaluate k1_booster migration candidates

## Goal

Compare the prior real-robot demo with the current simulation framework, exclude GK-related code and recent GK/RLVK power changes, and identify safe, valuable capabilities to migrate for user approval.

## Background

- Source repository: `D:\dev\Robot\k1_booster`.
- Target repository: `D:\dev\Robot\sim-3v3-simple-framework`.
- Approved source baseline: commit `261030e`; content introduced after this commit must not be inspected or used.
- The source is a prior real-robot demo and differs substantially from the target simulation framework in platform integration, sensing, control, and strategy structure.
- The user specifically mentioned Kalman filtering, ball scanning/search, and dynamic speed changes as areas worth evaluating.

## Requirements

- Perform read-only analysis before proposing any code changes.
- Compare capabilities and behavioral ideas rather than copying platform-specific real-robot code directly.
- Exclude all goalkeeper/GK-specific functions and behavior from inspection and recommendations.
- Exclude all content introduced after source commit `261030e`, including GK and RLVK kick-power changes.
- Identify candidate improvements across sensing/state estimation, ball search, motion control, speed scheduling, strategy stability, recovery, and observability when evidence exists.
- For each candidate, explain the source behavior, user value, target integration point, platform dependencies, risks, and adaptation effort.
- Classify candidates as recommended, conditionally useful, or not suitable for migration.
- Present the candidate list to the user for explicit approval before modifying production code.
- Do not run build, test, lint, type-check, formatting, simulation, deployment, or other verification commands.

## Approved Migration Scope

- Continuous distance/heading speed scheduling, moderate-angle arc walking, near/far and turn-mode hysteresis, obstacle-clearance speed reduction, and final velocity validation/clamping.
- Stateful direct-behind-ball versus circle-back approach selection, retained approach side, transition hysteresis, and bounded timeout fallback.
- Explicit normal-play ball-loss recovery using last-seen memory, one stable searcher, bounded body rotation, safe non-searcher behavior, and short reacquisition hysteresis.
- Record ball velocity estimation, short prediction, and a possible Kalman filter as a documentation TODO only; do not implement them in this task.
- Preserve existing role assignment and other current behaviors unless a minimal compatibility change is required for the approved features.

## Acceptance Criteria

- [x] The excluded GK scope and recent-commit boundary are explicit and respected.
- [x] The report distinguishes reusable algorithms from real-robot-only hardware or perception code.
- [x] Kalman filtering, ball scanning/search, and dynamic speed behavior are each evaluated.
- [x] Additional valuable source capabilities are identified without including excluded code.
- [x] Every proposed migration has a concrete destination in the target architecture and an evidence-based benefit/risk assessment.
- [x] No production code is changed before the user approves selected candidates.
- [x] Only the three user-approved migration areas are implemented; unselected role-scoring, boundary, interception, and watchdog changes remain out of scope.
- [x] The future ball-estimation/Kalman work is documented as a TODO without runtime implementation.

## Out of Scope

- Goalkeeper/GK strategy, actions, tuning, and supporting helpers.
- RLVK kick-power changes and other explicitly excluded recent changes.
- Directly copying hardware drivers, camera pipelines, ROS transport details, or robot-specific constants without adaptation.
- Building or validating either repository.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

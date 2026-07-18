# Implementation Plan: Selected k1_booster Migration

## Ordered Checklist

1. Add centralized parameters in `src/param.py` for:
   - near/far mode hysteresis;
   - turn/arc mode hysteresis;
   - arrival braking distance;
   - obstacle-clearance speed scaling;
   - circle-back radius, angular step, transition tolerances, progress threshold, and timeout;
   - ball-loss memory, search speed, sweep interval, and reacquisition frames.
2. Update `Player` state in `src/player.py` for movement-mode and ball-approach persistence.
3. Harden `Player.set_velocity` with finite checks and final planar/angular limits.
4. Refactor local heading planning to return heading plus clearance.
5. Replace hard far-range turn/walk switching with continuous distance/heading scheduling and mode hysteresis while preserving near-range omnidirectional control.
6. Add stateful direct/circle ball-approach target selection and timeout fallback, then integrate it into `Player.attack`.
7. Add a safe `Player.search_for_ball` action and make `Player.support` handle a missing ball.
8. Extend `SoccerSimAgent` store and normal-play dispatch with last-seen ball memory, stable searcher selection, bounded search, and reacquisition hysteresis.
9. Ensure non-normal phases do not initiate search movement when the ball is unavailable.
10. Add a documentation TODO for future velocity estimation, short prediction, and optional Kalman filtering.
11. Review the edited code manually for state reset paths, phase priority, per-player state ownership, and unchanged unselected behavior.

## Manual Verification Handoff

Per project rule, the agent will not run build, test, lint, type-check, formatting, simulation, deployment, IDE diagnostics, or other verification commands.

The user can manually inspect these scenarios after implementation:

- far target with small, medium, and large heading errors;
- approach to a near target without near/far mode chatter;
- obstacle-free versus low-clearance local planning;
- approaching the ball from behind, the side, and in front;
- retained circle side across frames and timeout fallback;
- ball feed loss during normal play;
- ball recovery after several missing frames;
- missing ball during READY, STOPPED, kickoff, and set play;
- non-finite or excessive velocity request safety.

## Review Gates

- No GK or RLVK-related code is touched.
- No attacker cost scoring, boundary recovery, interception, teammate fusion, or full Kalman implementation is introduced.
- New constants have one definition in `src/param.py`.
- Team state remains in `store`; robot state remains on `Player`.
- Existing A* and obstacle models are reused rather than replaced.

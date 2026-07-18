# Design: Selected k1_booster Migration

## Scope and Constraints

- Migrate only the approved motion-control, ball-approach, and ball-loss-recovery ideas.
- Preserve the current `Phase -> role -> Player action` architecture; do not introduce behavior trees or distributed communication.
- Keep all cross-frame robot-specific state on each persistent `Player` instance.
- Keep team-wide ball-loss state in the existing strategy `store`.
- Do not alter GK-specific behavior or use post-`261030e` source content.
- Do not implement ball prediction or Kalman filtering in this task.

## 1. Continuous Motion Scheduling

### State

Add per-player controller state for:

- near-range versus far-range movement mode;
- turn-in-place versus arc-walk mode.

Use separate enter and exit thresholds so controller modes do not switch on the same boundary in both directions.

### Command generation

Keep the existing A* and local heading planner. After a heading is chosen:

1. Compute final-target distance for arrival braking.
2. Compute heading error relative to robot orientation.
3. Compute a distance speed factor that approaches zero at the arrival threshold.
4. Compute a heading speed factor that smoothly approaches zero as the target moves toward the side of the robot.
5. Compute an obstacle-clearance factor from the selected heading when obstacles are active.
6. Use turn-in-place only when the heading error crosses the larger enter threshold; return to arc walking only after crossing the smaller exit threshold.

Near-range movement remains omnidirectional because it is an existing target capability, but its translational magnitude receives the same arrival and clearance scaling.

### Planner contract

Change local heading selection to return both the selected heading and its measured clearance. For A* waypoints, calculate clearance along the selected waypoint heading using the same obstacle model.

### Command boundary

`Player.set_velocity` becomes the shared validation boundary:

- non-finite commands become a safe zero command;
- planar velocity magnitude is limited to `MAX_LINEAR`;
- angular velocity is limited to `MAX_ANGULAR`;
- exact zero commands remain exact zero commands.

No temporal acceleration/slew limiter is added.

## 2. Stateful Ball Approach

### Approach modes

Each `Player` owns one of two modes:

- `direct`: walk toward the standard point behind the ball;
- `circle`: move around the ball toward the desired kick line.

The mode decision uses the robot's angular position around the ball relative to the desired behind-ball angle. Separate direct-entry and direct-exit tolerances prevent mode chatter.

### Circle target

When the robot is on the wrong side of the ball:

1. Calculate the desired behind-ball angle from the kick direction.
2. Select the shorter left/right route when entering circle mode.
3. Retain that side while circle mode remains active.
4. Advance a bounded angular step around a configurable circle radius to produce the next waypoint.
5. Face the intended kick direction while approaching.

### Progress fallback

Track the start time and best alignment error for the current circle attempt. If the attempt exceeds a configurable timeout without sufficient progress, reset circle state and temporarily use the direct behind-ball target. This is a bounded anti-deadlock fallback, not a general stuck detector.

Reset approach state when the ball is unavailable, the player enters kick mode, or the action is otherwise abandoned.

## 3. Ball-Loss Recovery

### Team state

Extend strategy `store` with:

- last observed ball position and observation time;
- consecutive visible-frame count;
- stable searcher player ID;
- loss-start time.

Fresh ball observations update the memory. Normal strategy resumes only after a small configurable number of consecutive visible frames.

### Normal-play dispatch

In `NORMAL` only:

- if the ball is confirmed visible, retain existing attacker/guard/support dispatch;
- if the ball is absent or not yet reacquired, enter a dedicated team recovery handler.

The recovery handler:

1. Retains the previous normal attacker as searcher when still active; otherwise selects one stable active player.
2. Makes the searcher first face the last-known ball location when available.
3. Then performs bounded in-place rotation, alternating sweep direction on a configurable interval.
4. Assigns one remaining player to the existing safe guard behavior.
5. Stops other remaining players.

Search movement is not used in READY, STOPPED, kickoff, or set-play phases. Missing-ball handling in those phases remains stationary/safe.

### Defensive null handling

`Player.support` must stop safely when the ball is absent, even though normal ball-loss dispatch should prevent it from being called in that state.

## 4. Future Estimation TODO

Add a documentation TODO describing a future platform-neutral ball estimator:

- recognize genuinely new observations by timestamp;
- estimate field-frame velocity with finite and time-gap checks;
- provide a short prediction-only grace period;
- preserve raw observation time and prediction provenance;
- consider `[x, y, vx, vy]` Kalman filtering only when noisy perception or interception needs justify it.

The TODO must explicitly warn against filtering authoritative simulator resets as outliers and against copying the source confidence scale unchanged.

## Files Expected to Change

- `src/param.py`
- `src/player.py`
- `src/main.py`
- one new documentation TODO under `docs/`

No framework type, ROS source, backend lifecycle, role-scoring, boundary-recovery, or GK changes are planned.

## Compatibility and Rollback

- Existing public `Player.walk_to`, `attack`, and strategy call signatures remain compatible unless an internal optional argument is necessary.
- Existing A* and obstacle collection remain authoritative.
- New behavior is controlled by centralized constants in `src/param.py`.
- Rollback is file-local: restore the previous movement scheduler, fixed behind-ball target, and direct normal-play dispatch while retaining the independent `support` null guard if desired.

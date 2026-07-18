# Migration Research: k1_booster to Simulation Framework

## Scope

- Source baseline: `D:\dev\Robot\k1_booster` at commit `261030e`.
- Target: `D:\dev\Robot\sim-3v3-simple-framework`.
- Excluded: all post-baseline content, GK/goalkeeper logic, and RLVK kick-power behavior.
- Research only; no production code changes or verification commands.

## Recommended First Batch

### 1. Continuous motion scheduling and command safety

- Replace hard turn-versus-walk switching with distance and heading based speed scaling.
- Allow moderate-angle arc walking while smoothly reducing forward speed as heading error grows.
- Add hysteresis around near/far and turn/arc controller modes.
- Reduce speed when local obstacle clearance is small.
- Validate finite velocity commands and apply final hard limits.
- Target: `src/player.py`, `src/param.py`, optionally final defense in `src/framework/robot_backend.py`.

### 2. Stateful behind-ball and circle-back approach

- Use a direct behind-ball target when the player is already on the correct side.
- Otherwise approach around the ball using a retained left/right circle-back side.
- Add enter/exit hysteresis and a progress timeout that falls back to chase.
- Store all state per `Player`; do not copy source function-static state.
- Target: `src/player.py`, `src/param.py`.

### 3. Stable cost-based attacker selection

- Extend distance-only ranking with heading error and direct-path obstruction cost.
- Smooth scores across frames and retain the current attacker unless another player is clearly better.
- Continue excluding penalized, unready, fallen, and unlocalized players before scoring.
- Target: `src/main.py` and strategy `store`.

### 4. Explicit ball-loss recovery

- Make every `ball=None` path safe, including support behavior.
- Preserve last-seen ball position and age in team strategy state.
- Select one stable searcher; use bounded body rotation biased toward the last-known direction.
- Other players hold safe ball-independent positions.
- Require short reacquisition confirmation before normal chase resumes.
- Do not migrate real-robot head-joint scan angles or pretend body rotation can repair a global simulator feed.
- Target: `src/main.py`, `src/player.py`, `src/param.py`.

### 5. Boundary recovery and diagnostics

- Add field-bound predicates and bounded robot re-entry behavior before normal role actions.
- Keep restart and GameController rules higher priority than boundary recovery.
- Add concise debug labels for attacker score, fallback reason, ball age, and recovery state.
- Target: `src/utils/geom.py`, `src/main.py`, `src/player.py`, debug logging/drawing.

## Conditional Second Batch

### Ball velocity estimation and short prediction

- Correct estimator state only when a genuinely new observation timestamp arrives.
- Estimate field-frame velocity with finite checks, valid time bounds, smoothing, and speed caps.
- Permit only a short prediction-only grace period during brief data loss.
- Preserve the distinction between raw observation time and estimated time.

### Kalman filter

- Useful when a noisy perception source exists or when moving-ball interception is added.
- Current ground-truth input has fixed perfect confidence, so a full Kalman filter has limited immediate value and can add lag.
- If introduced later, use a platform-neutral runtime estimator and normalize confidence to the target's `0..1` convention.

### Interception and scoped progress watchdog

- Add moving-ball interception only after reliable velocity exists.
- Add a progress watchdog only for actions that are commanded to move but make no progress; do not treat intentional waiting as stuck.

## Reject for Current Target

- Real-robot pan/tilt head scan angles and camera field-of-view timing.
- Teammate ball-sharing transport and fusion while all players share centralized truth.
- Full behavior-tree migration or a new command-arbitration framework.
- Source hardware deadband floors, random step commands, calibration factors, and fixed timed escape speeds.
- Landmark/vision localization logic while the target uses simulation ground truth.
- Long-horizon constant-velocity trajectories and acceleration prediction without a concrete consumer.

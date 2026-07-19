# Technical Design

## Scope and Ownership

本任务保留现有分层：

- `src/main.py` 拥有守门员战术状态、球速样本、威胁判断、出击许可和整队信息。
- `src/player.py` 只提供具体守门动作原语：前往指定目标、直取危险球、按指定安全目标和力度解围。
- `src/param.py` 保存所有状态阈值、几何余量、超时和解围力度。
- `src/framework/`、ROS、GameController 和 Backend 不修改。

守门员身份仍由 `_select_current_goalkeeper()` 每帧统一决定。高级策略只消费该结果，不拥有身份选择权。

## Goalkeeper State Contract

新增 `GoalkeeperMode`：

- `HOLD`：球未知或远离己方威胁，走向动态 home。
- `TRACK`：球可见但没有直接射门威胁，按球 Y 跟随。
- `BLOCK`：快速射门或保守位置威胁，走向门前射门线交点。
- `CHALLENGE`：守门员安全先到慢球，直接接近球。
- `CLEAR`：球进入守门员解围距离，按安全目标踢球。
- `RETURN`：出击、解围或超时后回到动态 home；到位且局面稳定后转 HOLD/TRACK。

`store` 新增：

- `goalkeeper_mode`
- `goalkeeper_mode_entered_at`
- `goalkeeper_challenge_started_at`
- `goalkeeper_threat_reason`
- `goalkeeper_previous_ball_position`
- `goalkeeper_previous_ball_sample_at`
- `goalkeeper_ball_velocity`
- `goalkeeper_ball_speed`
- `goalkeeper_target`
- `goalkeeper_clearance_target`

当前守门员 ID 变化时重置模式为 HOLD，并清除 challenge/clear 状态，避免临时守门员继承前一名守门员的出击状态。

## Per-Frame Data Flow

```text
play() phase + current_goalkeeper
  -> NORMAL / 我方 live restart action
  -> collect assigned field-player protection count
  -> update goalkeeper ball velocity sample
  -> estimate threat and safe-challenge evidence
  -> update GoalkeeperMode with hysteresis/timeout
  -> compute target or clearance target
  -> call Player goalkeeper action primitive
  -> write action/debug/store observability
```

对方重启、READY 和 STOPPED 不进入该数据流。离开允许高级守门策略的 phase 时清除速度样本和模式活动状态。

## Ball Velocity Estimation

新增 `_estimate_goalkeeper_ball_velocity(context, store)`：

1. 球不可见：清除上次样本并返回 `None`。
2. 读取 `ball.last_seen_at`；只有观测时间前进时才接受新样本，避免同一观测在多个策略帧中重复计算。
3. 使用观测时间差计算 `(vx, vy)`，要求 `dt` 位于可调最小/最大范围。
4. 若位移超过跳变阈值或速度超过最大可信速度，返回 `None` 并以当前样本重新播种。
5. phase 变化、对方重启、READY、STOPPED 和守门员身份变化时重置。

速度只用于威胁分类，不写回 `BallState`。

## Dynamic Home and Track Target

动态目标 X：

```text
home_x = own_goal_line_x + GOALKEEPER_HOME_X_OFFSET_M
```

动态目标 Y：

1. 球不可见时为 `0`。
2. 根据球到己方门线距离，在远距离增益和近距离增益之间线性插值。
3. `target_y = ball.y * interpolated_gain`。
4. 将 Y 限制到 `min(GOALKEEPER_MAX_LATERAL_M, goal_width / 2 - post_margin)`。

HOLD、TRACK 和 RETURN 使用 `Player.guard(target=..., avoid_ball=True, avoid_robots=True)` 或等价新原语。BLOCK、CHALLENGE 和 CLEAR 显式关闭球避障。

## Threat Model

新增轻量结果对象，包含：

- `is_fast_goal_threat`
- `is_position_threat`
- `projected_goal_y`
- `reason`

### 速度可靠时

满足以下条件判定快速射门威胁：

- 球位于己方半场或己方威胁距离内；
- `vx` 小于负向速度阈值；
- 球总速度超过快速球阈值；
- 按速度射线投影到己方门线的时间为正；
- 投影 Y 位于球门半宽加威胁余量内。

### 速度不可靠时

仅当球已进入己方门前距离、中路通道且最近对手接近球时产生位置威胁。该分支不标记“快速来球”。

BLOCK 目标使用门线前方固定 block X。若速度投影可靠，则把射线投影到 block X；否则使用动态 track Y。最终 Y 夹入安全范围。

## Challenge Eligibility

新增 `_goalkeeper_can_challenge(...)`，全部条件同时满足：

- 球和守门员 pose 有效；
- 球位于己方允许出击 X 范围和横向范围；
- 球速未知或低于慢球阈值，且不是快速射门威胁；
- 至少一名 available 场上机器人仍承担保护；
- 至少一名对手有有效 pose；
- 守门员到球距离不超过进入阈值；
- `goalkeeper_distance + GOALKEEPER_CHALLENGE_ADVANTAGE_M < opponent_distance`。

已处于 CHALLENGE 时使用更宽松的退出距离，形成迟滞。超过 `GOALKEEPER_CHALLENGE_TIMEOUT_SEC`、快速威胁出现、球离开出击区或先到优势消失时进入 RETURN/BLOCK。

## State Transition Priority

```text
球未知 -> HOLD / RETURN
快速射门威胁 -> BLOCK（可立即抢占）
守门员已在解围距离且球位于危险区 -> CLEAR
安全先到且允许出击 -> CHALLENGE
CHALLENGE/CLEAR 条件消失或超时 -> RETURN
位置威胁 -> BLOCK
球可见 -> TRACK
其他 -> HOLD
```

普通模式使用最短保持时间；BLOCK 的快速威胁可跳过保持时间。CLEAR 具有短保持时间，避免 kick/release 每帧抖动。RETURN 到动态目标距离内后才允许恢复 HOLD/TRACK。

## Player Action Primitives

扩展 `Player.guard()` 为接受目标和避障选项，默认值保持兼容：

```python
guard(target=None, *, avoid_ball=True, avoid_robots=True, action="guard:home")
```

新增或等价实现：

- `goalkeeper_challenge(target)`：面向球直走，`avoid_ball=False`，可保留机器人避障为关闭或保守关闭。
- `goalkeeper_clear(clearance_target, power)`：不使用普通 `attack()` 的绕球状态机；靠近球时直接按指定正 X 方向踢，否则直取球。

CLEAR 不调用 `plan_kick()`，因为该方法只瞄准对方球门且无法表达选择空侧。由 main 计算目标，Player 负责转换为踢球方向和发出动作。

## Clearance Target

候选目标始终位于球的正 X 前方：

- 中路前场候选；
- `+Y` 边路候选；
- `-Y` 边路候选。

对每个候选计算附近对手到目标走廊的简单距离分数，选择更空的方向。所有候选满足：

- `target_x > ball.x + minimum_forward_distance`；
- 目标夹在场内；
- 踢球方向 `cos(direction) > 0`。

由于目标全部位于球前方，解围轨迹不会向负 X 穿过己方门线；边路目标从球向前展开，而不是纯横向穿过门前。

## Compatibility

- `_assign_open_play_roles()` 和 T06 模式估计不修改。
- T04/T05/T06 只把原有 `goalkeeper.guard()` 调用替换为统一高级守门入口，场上机器人动作不变。
- 我方开球/我方 set play 可使用 HOLD/TRACK，但禁止 CHALLENGE/CLEAR，以免本任务隐式改变固定战术；高级出击仅在 `Phase.NORMAL`。
- 对方重启继续使用 `_walk_to_restart_target()`，不调用高级守门入口。
- READY 和 STOPPED 保持现状。

## Risks and Mitigations

- **有限差分受裁判摆球影响**：要求观测时间推进、采样间隔有效并限制位置跳变；phase 变化清除样本。
- **守门员过度出击**：要求有效对手距离、明确先到优势和场上保护者；缺失数据时禁用 CHALLENGE。
- **快速球被误判为可解围**：状态优先级让快速射门 BLOCK 高于 CHALLENGE/CLEAR。
- **解围横穿门前**：目标必须正 X 前移，Player 再检查方向正 X 分量。
- **临时守门员继承旧状态**：current goalkeeper ID 变化时重置。
- **破坏 T01**：对方重启函数不改，不调用普通守门状态机。

## Rollback Shape

可按三层回退：

1. `param.py` 守门参数；
2. `player.py` 可参数化 guard 和守门动作原语；
3. `main.py` 状态机、球速/威胁估计及调用替换。

对方重启、职责分配和 T06 模式代码不需要随本任务回退。

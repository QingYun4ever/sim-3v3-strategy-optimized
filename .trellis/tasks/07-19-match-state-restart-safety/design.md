# Technical Design

## Scope and Boundaries

本任务只在策略层增加规则安全分派。裁判数据契约、运行时编解码、角色系统和 Player 技术动作保持不变。预计仅修改：

- `src/main.py`
- `src/param.py`

## Phase Safety

`get_phase()` 保持裁判字段为唯一事实来源，但将 `g.stopped` 提升为早期最高优先级判断：

1. `context.game is None` -> `STOPPED`
2. `g.stopped` -> `STOPPED`
3. `READY` -> `READY`
4. 未 stopped 的 `PLAYING` 根据 `set_play`、`secondary_time` 和 `kicking_team` 分派
5. 其他状态 -> `STOPPED`

这样可修复 `READY + stopped` 仍走位的问题，同时不改变裁判清空重启字段后恢复普通比赛的现有机制。

## Restart-Safe Geometry

在 `src/main.py` 增加少量局部 helper，职责保持单一：

- 将目标点限制在场内安全边界。
- 将目标点沿远离球的方向投影到至少 `OPPONENT_RESTART_AVOID_M`。
- 对中场开球目标额外投影到中圈半径加现有 margin 之外。
- 计算面向球的朝向；球未知时使用默认朝向。
- 统一通过 `walk_to(..., avoid_ball=True, avoid_robots=True)` 发出重启期间的移动指令。

目标点投影是规则安全的最后保护层。若目标点因场地边界无法同时完美满足全部几何条件，则优先选择场内、远离球且保守靠近己方半场的位置，符合“尽量保持至少 1.5m”的要求。

## Opponent Kickoff

1. 没有 active 球员时返回。
2. 选择离己方球门最近者作为 guard。
3. guard 走向己方小禁区中心，经重启安全目标 helper 修正，并标记 `opp_kickoff:guard`。
4. 其余球员使用己方半场的固定保守槽位，经中圈和球距离双重修正，并标记 `opp_kickoff:avoid`。
5. 该函数不调用 `guard()`、`attack()`、`kick()` 或其他高层触球动作，只直接使用 `walk_to()` 或在无法安全走位时 `stop()`。

## Opponent Set Play

1. 没有 active 球员时返回。
2. 选择离己方球门最近者作为 guard，走向安全修正后的己方小禁区中心。
3. 球未知时，所有非 guard 球员停车并标记 `opp_restart:stop_no_ball`。
4. 球已知时：
   - 第一名非 guard 球员站到球朝己方球门方向、距离球至少 `1.5m` 的封堵点，标记 `opp_restart:block`。
   - 其余球员走向己方半场中路保护槽位，经安全 helper 修正，标记 `opp_restart:protect`。
5. 全部移动均使用球和机器人避障，不进入 `_act_normal()`，不调用攻击或踢球动作。

## Compatibility

- `Phase` 枚举和裁判协议结构不变。
- 我方开球、我方 set play、READY 和 NORMAL 的既有策略不扩展。
- `Player` 公共接口不变；现有 `walk_to()` 已具备释放踢球状态和启用避障的能力。
- 唯一参数行为变化是把现有未使用的 `OPPONENT_RESTART_AVOID_M` 从 `1.65` 调整为明确要求的 `1.5` 并开始使用。

## Risks and Mitigations

- **边界附近投影后距离缩短**：先夹入场内，再进行远离球投影，并在最终结果上再次检查；若受场地边界限制则选择保守 fallback。
- **guard 高层动作覆盖 action 标签或引入额外行为**：重启期间直接调用 `walk_to()`，不调用 `guard()`。
- **误用本地球移动判断开放球权**：不读取 `CENTER_LEAVE_DIST_M`，不新增计时状态，只依赖 `get_phase()` 的裁判字段。
- **扩大任务范围**：不修改 Player 技术动作、不修改 framework、不增加我方重启战术。

## Rollback Shape

改动局限于一个参数值、`get_phase()` 的 stopped 优先级、两个对方重启动作函数及其局部 helper。若人工验证发现问题，可按这四个独立区域逐项回退，不影响协议层。

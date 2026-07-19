# 比赛状态、发球权与规则安全

## Goal

为当前 3v3 足球机器人策略增加最小规则安全层，确保裁判尚未开放球权时，我方不会移动、抢球或提前触球；裁判开放球权后，现有阶段状态机自然恢复对应策略。

## Background

- 策略入口位于 `src/main.py`，由 `get_phase()` 将裁判状态映射到 `Phase`，再由 `SoccerSimAgent.play()` 分派整队动作。
- `GameState` 提供 `INITIAL / READY / SET / PLAYING / FINISHED`。
- `SetPlay` 提供 `NONE / DIRECT_FREE_KICK / INDIRECT_FREE_KICK / PENALTY_KICK / THROW_IN / GOAL_KICK / CORNER_KICK`。
- 当前 `_act_opp_set_play()` 直接调用 `_act_normal()`，因此会选择攻击者并可能提前追球或触球。
- 当前 `get_phase()` 在检查 `g.stopped` 之前处理 `READY`，因此 `READY + stopped` 仍可能走位，不满足 stopped 必须停车的规则。
- 我方球门位于负 X，对方球门位于正 X。

## Requirements

### R1. 停止状态安全

- 无裁判数据、`INITIAL`、`SET`、`FINISHED` 或 `g.stopped == True` 时，所有 active 球员必须停车。
- `READY` 仍允许合法走位，但不得抢球；`g.stopped` 的停车要求优先于 `READY`。

### R2. 对方中场开球安全

- 离己方球门最近的 active 球员负责守门，其余球员在中圈外等待。
- 所有移动只能使用不触球的走位或停车动作，启用球和机器人避障。
- 有球位置时，目标点还应尽量与球保持至少 `1.5m`。
- 不调用 `attack()`、踢球动作或任何可能主动触球的策略。
- 动作标签应明确区分 `opp_kickoff:guard` 与 `opp_kickoff:avoid`。

### R3. 对方定位球安全

- 对方 `THROW_IN / GOAL_KICK / CORNER_KICK / DIRECT_FREE_KICK / INDIRECT_FREE_KICK / PENALTY_KICK` 均由独立安全逻辑处理，不得进入 `_act_normal()`。
- 离己方球门最近的 active 球员负责守门。
- 其余球员中，一名在合法距离外封堵球到己方球门或中路的路线，另一名保护中路或己方门方向。
- 所有目标点应尽量与球保持至少 `1.5m`，所有移动启用球和机器人避障。
- 球位置未知时不得追球；非守门员停车并标记 `opp_restart:stop_no_ball`，守门员保持保守守门位置。
- 不调用 `attack()`、不主动踢球。
- 动作标签应明确区分 `opp_restart:guard`、`opp_restart:block`、`opp_restart:protect` 和 `opp_restart:stop_no_ball`。

### R4. 裁判字段为唯一开放依据

- 不使用本地计时、球移动距离或其他本地推断提前开放球权。
- 当裁判字段清空对方开球或对方 set play 后，现有 `get_phase()` 应自然进入 `NORMAL / OUR_KICKOFF / OUR_SET_PLAY / READY / STOPPED`。

### R5. 参数与范围约束

- 将对方重启避让距离统一为 `OPPONENT_RESTART_AVOID_M = 1.5`。
- 优先只修改 `src/main.py` 和 `src/param.py`，不修改 `src/framework/` 数据契约，不引入依赖。
- 不实现后续进攻、防守、固定中场开球、角球、球门球、界外球或守门员增强策略。

## Acceptance Criteria

- [ ] 无裁判数据、`INITIAL`、`SET`、`FINISHED` 和任意 `g.stopped == True` 情况下，active 球员均停车。
- [ ] `READY` 且未 stopped 时仍沿用现有合法走位，不调用抢球策略。
- [ ] `_act_opp_kickoff()` 只发出安全走位或停车指令，非守门员目标位于中圈外，并在球已知时尽量距球至少 `1.5m`。
- [ ] `_act_opp_set_play()` 不再调用 `_act_normal()`，也不调用 `attack()` 或踢球动作。
- [ ] 对方定位球期间，守门、封堵和中路保护角色均通过安全目标点走位；球未知时非守门员停车。
- [ ] `OPPONENT_RESTART_AVOID_M` 的值为准确的 `1.5`。
- [ ] 球权恢复只依赖裁判字段，没有新增本地计时开放逻辑。
- [ ] 未修改 `src/framework/`，未实现任何明确排除的后续策略。
- [ ] 按项目规则不运行 build、test、lint、type-check、format、仿真、开发服务器或 IDE 诊断验证。

## Out of Scope

- 我方中场开球配合。
- 我方角球、球门球和界外球战术。
- 普通进攻双前场协同或其他进攻增强。
- 守门员主动出击或默认守门员角色重构。
- 整体角色系统重构。

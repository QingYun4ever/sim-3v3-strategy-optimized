# Technical Design

## Scope

本任务在 `src/main.py` 建立整队可用性和守门员选择层，并在 `src/param.py` 增加稳定的默认守门员回退 ID。Player 恢复原语和 framework 数据契约保持不变。

## Per-Frame Availability Pipeline

新增 `_collect_available_players(players, store)`，顺序保持现有恢复能力：

1. 对每个 Player 调用 `ensure_ready()`，允许摔倒起身或请求 walk 模式。
2. 按优先级分类：
   - `penalized`
   - `fallen`
   - `switching_mode`
   - `no_pose`
   - `available`
3. 非 available Player 设置对应 action 并调用 `stop()`，清除旧速度和踢球状态；恢复请求已经在前一步发出。
4. available Player 临时标记 `available` 并进入返回列表。
5. 写入：
   - `store.player_availability: dict[int, str]`
   - `store.available_player_ids: tuple[int, ...]`

后续所有职责分派只接收该 available 列表。

## Default Goalkeeper Contract

新增 `_resolve_default_goalkeeper_id(context, players)`：

1. 读取 `context.game.get_team_state(context.team_id).goalkeeper`。
2. 若值是 roster 内的正整数 Player ID，作为默认守门员。
3. 否则使用 `DEFAULT_GOALKEEPER_ID`。
4. 若配置 ID 不在 roster，回退到最小 roster ID；无 Player 时返回 `None`。

该逻辑只决定身份，不判断本帧是否 available。

## Temporary Goalkeeper State Machine

新增 `_select_current_goalkeeper(context, all_players, available_players, phase, store)`：

- 每帧更新 `store.default_goalkeeper_id`。
- 若处于 `READY / STOPPED`、默认守门员 available，则清除 `store.temporary_goalkeeper_id`。
- 若已有临时守门员且仍 available，则继续返回该 Player；PLAYING 中不会因默认守门员恢复而切换。
- 若临时守门员已不可用，立即清除并重新选择。
- 默认守门员 available 且没有需保持的临时守门员时，返回默认守门员。
- 默认守门员 unavailable 时，从 available 中选择离己方门最近者，写入 `temporary_goalkeeper_id` 并返回。
- 无 available Player 时返回 `None` 并将 `current_goalkeeper_id` 设为 `None`。

同时计算：

- `store.current_goalkeeper_id`
- `store.available_field_player_ids`
- `store.can_run_two_player_tactic`

## Role Dispatch Changes

当前守门员由 `play()` 每帧选择一次，再传给各 `_act_*`：

- `NORMAL`：守门员执行现有 `guard()`；场上机器人执行现有 attacker/support。唯一 available Player 走单人降级分支。
- 丢球恢复：守门员保持 guard，搜索者只从场上机器人选择。
- 我方开球和我方 set play：主罚/普通候选优先只用场上机器人；没有场上机器人时守门员守门，不新增固定战术。
- `READY`：当前守门员进入现有门前 ready 位，场上机器人使用其余现有站位。
- T01 对方中场开球和对方 set play：使用传入的当前守门员作为 guard，其他安全逻辑和 action 标签保持不变。
- `STOPPED`：所有 available Player 停车；该窗口只负责允许身份交还，不移动守门员。

默认守门动作完成后使用 `goalkeeper:guard`；临时守门员使用 `temp_goalkeeper:guard`。T01 分支继续使用 `opp_kickoff:guard`、`opp_restart:guard` 等规则安全标签。

## Single-Player Degradation

新增 `_should_single_player_guard(context, player)`，只影响 `NORMAL`：

- 球未知 -> 守门。
- 球 X 位于己方罚球区前沿以内 -> 守门。
- Player 距己方门不超过罚球区长度加小幅余量 -> 守门。
- 否则允许沿用现有 attack 入口。

该判断仅建立保守降级入口，不新增防守移动、主动出击或进攻协同。

## T01 Compatibility

- `get_phase()` 和 stopped 优先级不改。
- `_prepare_restart_target()` 和安全 `walk_to()` 不改；根据用户实测，将共用避让参数从 1.5m 提高到 1.6m。
- `_act_opp_set_play()` 仍为独立逻辑，不调用 `_act_normal()`。
- 受罚或异常的默认守门员不会被传入 T01 guard 分支；统一选择层会提供临时守门员。

## Risks and Mitigations

- **裁判 goalkeeper 为 0 或无效**：使用集中回退 ID，并最终回退 roster 最小 ID。
- **PLAYING 中默认守门员恢复造成频繁交换**：store 中的临时守门员锁定到安全窗口。
- **临时守门员自身异常**：不等待安全窗口，立即从 available 中重选。
- **Player 恢复命令被 stop 覆盖**：`ensure_ready()` 先发起 get-up/mode 请求，随后 stop 仅清旧运动和踢球命令，不取消恢复请求。
- **扩大策略范围**：只重排现有 guard/attacker/support 候选，不修改动作算法。

## Rollback Shape

改动可按三层独立回退：available 收集、守门员状态机、各 phase 的参数传递和少人分支。T01 几何 helper 不需回退。

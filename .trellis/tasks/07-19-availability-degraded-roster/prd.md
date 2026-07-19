# 异常状态与少人降级

## Goal

整理机器人每帧可用性、不可用原因、默认与临时守门员接管，以及 0-3 台可用机器人时的安全降级入口，使后续角色、攻防和固定战术能够复用统一的可行动阵容。

## Background

- `SoccerSimAgent.play()` 当前会调用 `ensure_ready()`，并排除受罚、摔倒、切换模式和无位姿机器人，但可用性结果仅存在于局部 `active` 列表中。
- 不可用 action 已大致区分 `penalized / fallen / switching_mode / no_pose`，但摔倒和切模式分支没有统一显式停车。
- 当前 `_act_normal()`、丢球恢复、我方开球、T01 对方中场开球和对方 set play 都会在各自函数内重新按离己方门距离选择 guard，没有统一的当前守门员身份。
- `GameControlState.get_team_state()` 返回的 `TeamState.goalkeeper` 是现有裁判协议中的默认守门员字段；字段为 `0` 或不匹配本队 roster 时，需要稳定回退约定。
- T01 已完成并经用户手动验证：STOP/SET 安全、对方重启禁止抢球。后续实测发现 1.5m 余量过小，用户要求将对方重启目标避让提高到 1.6m；T02 不得破坏这些行为。

## Requirements

### R1. 统一每帧可用性分类

- 每帧为所有 Player 生成明确状态：`penalized / fallen / switching_mode / no_pose / available`。
- 只有 `available` Player 可进入职责和动作分派。
- `ensure_ready()` 继续负责起身和切 walk 模式；不可用 Player 不得参与主攻、支援、发球、接应或守门候选。
- 受罚和无位姿 Player 必须停车；摔倒和切模式 Player 只允许恢复动作，并显式清除旧移动或踢球命令。
- 将每帧状态和 available ID 保存到 `store`，供后续策略观察和复用。

### R2. 默认守门员解析

- 优先读取我方 `TeamState.goalkeeper`，前提是该 ID 属于当前 Player roster。
- 裁判字段无有效守门员时，使用集中参数 `DEFAULT_GOALKEEPER_ID = 1`；若 roster 不含该 ID，再回退到最小 Player ID。
- 默认守门员不可用时不能移动，也不能继续占用守门职责候选。

### R3. 临时守门员接管和保持

- 默认守门员不可用时，从 available Player 中选择离己方球门最近者作为临时守门员。
- 在 `store` 保存 `default_goalkeeper_id`、`temporary_goalkeeper_id` 和 `current_goalkeeper_id`。
- PLAYING 期间，只要当前临时守门员仍 available，即使默认守门员恢复，也继续由临时守门员承担职责。
- 当前临时守门员也不可用时，应立即从剩余 available Player 中重新选择，不能空门等待安全窗口。
- 仅在 `READY` 或 `STOPPED` 安全窗口且默认守门员已经 available 时，清除临时守门员并交还默认守门员；`SET / INITIAL / FINISHED / stopped` 均已映射到 `STOPPED`。

### R4. 统一角色入口

- 正常比赛、丢球恢复、我方开球、我方 set play、READY、对方中场开球和对方 set play 均复用当前守门员选择结果，不再分别临时挑选 guard。
- 正常和我方重启阶段优先将守门员从场上处理球候选中排除。
- T01 对方重启阶段仍只执行安全走位或停车，不调用普通追球逻辑；只是将 guard 改为统一选择出的当前守门员。
- action 应能观察默认或临时守门员职责，同时保留 T01 对方重启的清晰安全标签。

### R5. 0-3 台 available 的降级

- 3 台 available：当前守门员 + 2 名场上机器人；不在本任务中增强两名前场协同。
- 2 台 available：当前守门员 + 1 名场上机器人；场上机器人沿用现有处理球入口。
- 1 台 available：
  - 球未知、球位于己方罚球区危险纵深，或唯一机器人靠近己方门时，优先 `guard()`；
  - 球远离己方门且机器人不处于门前保护位置时，可沿用普通处理球逻辑。
- 0 台 available：动作分派直接返回，不报错。

### R6. 固定战术人数预留

- 每帧在 `store` 保存 available 场上机器人 ID。
- 提供“是否至少有两名 available 场上机器人”的布尔入口，供后续中场开球、角球等固定传射判断使用。
- 本任务不据此实现或改写任何固定战术。

### R7. 范围和验证约束

- 优先只修改 `src/main.py`，必要时在 `src/param.py` 增加默认守门员参数。
- 不修改 `src/framework/` 协议，不引入依赖。
- 不实现普通进攻、防守或守门员动作增强，不实现任何专项固定战术。
- 对方中场开球及所有对方 set play 共用 `OPPONENT_RESTART_AVOID_M = 1.6`，降低边界误差导致的罚下风险。
- 按项目规则不运行 build、test、lint、type-check、format、仿真、开发服务器或 IDE 诊断。

## Acceptance Criteria

- [ ] `store.player_availability` 每帧记录所有 Player 的五类状态，`store.available_player_ids` 只包含真正可行动机器人。
- [ ] 所有不可用机器人具有明确 action，不参与任何角色候选，并安全停车或只执行恢复动作。
- [ ] 默认守门员优先来自 `TeamState.goalkeeper`，无有效字段时使用稳定回退 ID。
- [ ] 默认守门员不可用时，available 中离己方门最近者成为并记录为临时守门员。
- [ ] 默认守门员在 PLAYING 中恢复不会导致仍 available 的临时守门员立即交还职责。
- [ ] READY/STOPPED 且默认守门员 available 时，临时守门员记录被清除并交还默认守门员。
- [ ] 3/2/1/0 台 available 均有明确且不报错的分派入口；1 台时按球门危险条件选择守门或普通处理球。
- [ ] `store.available_field_player_ids` 和两人固定战术可用标记可供后续任务使用。
- [ ] `_act_opp_set_play()` 仍不调用 `_act_normal()`，对方重启安全走位保持不变，避让目标距离提高到 1.6m。
- [ ] 未实现任何明确排除的后续策略，未运行自动验证命令。

## Out of Scope

- 普通进攻双前场协同或其他进攻增强。
- 普通防守的一人逼抢、一人保护等增强。
- 守门员横移、主动出击、扑救或球速预测。
- 我方中场开球传射、角球、球门球和界外球战术。
- framework 协议或角色系统的大规模重构。

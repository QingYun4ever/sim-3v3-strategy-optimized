# Implementation Plan

## Ordered Checklist

1. 在 `src/param.py` 增加 `DEFAULT_GOALKEEPER_ID = 1`，作为裁判字段无效时的稳定回退。
2. 在 `SoccerSimAgent.init_store()` 初始化：
   - `player_availability`
   - `available_player_ids`
   - `default_goalkeeper_id`
   - `temporary_goalkeeper_id`
   - `current_goalkeeper_id`
   - `available_field_player_ids`
   - `can_run_two_player_tactic`
3. 将 `play()` 内联 active 构造提取为统一 helper，明确五类状态并让所有不可用 Player 安全停止。
4. 新增默认守门员解析和临时守门员选择 helper，实现 PLAYING 保持、安全窗口交还和临时守门员异常时立即重选。
5. 在 `play()` 每帧只选择一次当前守门员和场上阵容，并把结果传给各阶段动作。
6. 重构现有职责入口但不增强动作：
   - NORMAL 和丢球恢复固定保留当前守门员；
   - 我方开球/set play 不把守门员选为处理球候选；
   - READY 将当前守门员放入门前站位；
   - T01 对方重启使用统一当前守门员，保持所有安全 helper 和标签。
7. 增加 NORMAL 的 1 台 available 降级判断；0 台直接返回，2/3 台按守门员加场上机器人自然降级。
8. 写入场上 available ID 和两人固定战术可用标记，不实现固定战术。
9. 只进行源代码人工复读：检查所有 `_act_*` 调用签名、空列表分支、临时守门员保持条件以及 T01 独立安全逻辑。

## Manual Validation Only

本任务不运行 build、test、lint、type-check、format、仿真、开发服务器、watcher 或 IDE 诊断。建议用户手动验证：

- 默认守门员正常时保持 guard 身份。
- 默认守门员受罚时，最近己方门的 available Player 临时接管，受罚者停车。
- 默认守门员 PLAYING 中恢复时不立即交还，READY/STOPPED 后交还。
- 临时守门员也异常时立即由剩余 available Player 接管。
- 摔倒、切模式和无位姿 Player 的 action 与排除行为。
- 3/2/1/0 台 available 的降级。
- 对方中场开球和各类 set play 的 T01 安全行为。

## Review Gates

- 实现前由用户批准本 PRD、设计和计划。
- 实现后不执行自动检查；仅报告人工复读结果和建议场景。
- 不自动提交、推送或进入后续策略任务。

## Rollback Points

- 默认守门员参数可单独回退。
- available helper 可恢复为 play 内联筛选。
- 守门员状态机和 phase 签名调整应作为一个逻辑单元回退。
- 单人 NORMAL 降级可独立回退，不影响 T01。

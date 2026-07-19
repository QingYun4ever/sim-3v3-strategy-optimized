# Implementation Plan

## Ordered Checklist

1. 在 `src/param.py` 增加集中守门参数：
   - home X、远近跟随增益、最大横向范围和门柱余量；
   - 球速采样最小/最大间隔、跳变距离和可信速度上限；
   - 快速射门阈值、威胁门框余量和位置威胁距离；
   - challenge 进入/退出距离、先到优势、横向/X 出击范围和超时；
   - clear 距离、保持时间、前向/边路目标距离和力度；
   - mode 最短保持和 return 到位距离。
2. 在 `src/main.py` 增加 `GoalkeeperMode`、威胁估计结果类型以及 `init_store()` 的守门状态字段。
3. 增加守门员状态重置 helper，在以下情况清除球速和活动状态：
   - 离开允许高级守门动作的 phase；
   - 球未知；
   - 当前守门员 ID 变化；
   - 球观测发生不可信跳变。
4. 实现独立有限差分球速估计，只消费 `BallState.last_seen_at` 前进的新样本，并保存最近有效 `(vx, vy)` 和速度。
5. 实现动态门前目标：固定安全 X、按球距离插值的 Y 跟随增益、门框内横向夹取。
6. 实现威胁判断和 BLOCK 目标：
   - 快速朝门射线投影；
   - 无可靠速度时的保守位置威胁；
   - 投影到门前 block X 并限制 Y。
7. 实现最近对手距离、保护者存在和 `_goalkeeper_can_challenge()`，使用进入/退出距离与先到优势迟滞。
8. 实现安全解围目标选择：构造正 X 中路和两侧候选，按对手净空选择目标，并保证所有目标位于球前方和场内。
9. 实现 `_update_goalkeeper_mode()`，按 `BLOCK > CLEAR > CHALLENGE > RETURN > TRACK/HOLD` 优先级应用快速威胁抢占、最短保持和 challenge/clear 超时。
10. 扩展 `Player.guard()` 支持显式目标和避障开关，保持无参数调用兼容；增加直取球和指定目标解围动作，避免复用普通 attack 的绕球/射门规划。
11. 增加统一 `_act_goalkeeper_strategy(...)`：
    - NORMAL 启用完整状态机；
    - 我方开球/我方 set play 只允许 HOLD/TRACK；
    - 对方重启、READY、STOPPED 不接入。
12. 将 T04/T05/T06 中直接 `goalkeeper.guard()` 的分散调用替换为统一守门入口，但不改场上机器人动作和模式判断。
13. 增加 action/debug：守门员模式与默认/临时身份、动态目标点、可靠射门线和解围目标。
14. 人工复读所有调用路径：
    - current goalkeeper 不进入场上职责；
    - 对方重启仍只调用安全走位；
    - STOP/READY 行为不变；
    - 单人守门不 challenge；
    - 球未知和无 pose 分支安全；
    - 所有 CLEAR 方向具有正 X 分量。

## Manual Validation Only

按照项目规则，不运行 build、test、lint、type-check、format、仿真、开发服务器、watcher 或 IDE 诊断。完成后建议用户手动验证：

- 球在远端和横向移动时 HOLD/TRACK 的 X/Y 目标和范围。
- 快球直冲门框时 BLOCK 抢占以及不绕球。
- 快球射向门外时不误判为直接门框威胁。
- 慢球在己方危险区、守门员明显先到且有保护者时 CHALLENGE/CLEAR。
- 对手先到、对手位姿缺失或只有守门员一台可用时不出击。
- 解围目标始终向正 X 前场或边路，不横穿己方门前。
- challenge 超时、球离开危险区和解围后进入 RETURN。
- 默认守门员不可用时临时守门员复用同一状态机，PLAYING 中身份不切回。
- 对方重启、READY、STOPPED、T04/T05/T06 行为保持。

## Review Gates

- 规划阶段完成后由用户批准 `prd.md`、`design.md` 和本计划，再运行 `task.py start`。
- 实现后只进行人工源代码复读；不得执行自动验证命令或 IDE 诊断。
- 在用户手动验证 T01-T06 与本任务前，不宣称运行时行为已确认。
- 不自动提交或进入 T08-T11 固定战术任务。

## Rollback Points

- `param.py` 参数组可独立回退。
- `Player.guard()` 参数化和守门动作原语作为一个动作层单元回退。
- 球速/威胁 helper 可回退为仅位置策略，不影响职责分配。
- 主状态机和 T04/T05/T06 守门调用替换作为一个策略层单元回退。
- 不触碰对方重启函数，因此 T01 无需随 T07 回退。

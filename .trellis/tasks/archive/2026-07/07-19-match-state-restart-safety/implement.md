# Implementation Plan

## Ordered Checklist

1. 在 `src/param.py` 将 `OPPONENT_RESTART_AVOID_M` 调整为准确的 `1.5`，不新增重复避让常量。
2. 在 `src/main.py:get_phase()` 中让 `g.stopped` 优先于 `READY` 和所有 `PLAYING` 分支，保持无裁判数据和非 PLAYING 状态进入 `STOPPED`。
3. 在 `src/main.py` 增加局部几何 helper：
   - 场内目标限制；
   - 距球 `1.5m` 安全投影；
   - 中圈外投影；
   - 面向球的安全走位封装。
4. 重写 `_act_opp_kickoff()`：
   - 最近己方门者守门；
   - 其余球员在中圈外和球避让距离外等待；
   - 只调用 `walk_to()` 或 `stop()`；
   - 设置清晰 action 标签。
5. 重写 `_act_opp_set_play()`：
   - 移除 `_act_normal()` 调用；
   - 分派 guard、block、protect；
   - 球未知时非 guard 停车；
   - 所有已知球目标经过 `1.5m` 安全修正；
   - 不调用 `attack()` 或踢球动作。
6. 静态复读修改区域，确认没有触碰我方重启、普通进攻、守门员增强或 framework 协议。

## Manual Validation Only

依据项目规则，本任务不运行任何 build、test、lint、type-check、format、仿真、开发服务器、watcher 或 IDE 诊断。完成后仅向用户提供以下人工测试建议：

- 对方中场开球：不进中圈、不提前触球、裁判开放后恢复普通比赛。
- 对方角球：不冲向角旗球、保持约 `1.5m`、有人保护门前或中路。
- 对方球门球：不提前逼抢、在合法距离外封堵路线。
- 对方界外球：不贴球、在场内合法距离外等待。
- STOP / SET / FINISHED / 无裁判数据 / `stopped`：全员停车。

## Review Gates

- 开始实现前由用户确认本计划。
- 修改后只做源代码人工检查，不执行验证命令。
- 最终报告明确列出修改文件、核心逻辑、未修改内容、人工测试建议、遗留问题和后续任务影响。

## Rollback Points

- 参数调整可独立恢复。
- stopped 优先级修复可独立恢复。
- kickoff 与 set play 两个动作函数可分别恢复，helper 仅在对应逻辑使用。

# Design: 策略理解与实现规划

## Boundaries

- 输出主要在聊天中完成；Trellis 目录只记录任务日志、研究材料和规划上下文。
- 不修改项目正式代码、配置或文档。
- 仅通过静态阅读 DOCX、已生成规则/代码流程分析和当前代码完成规划。

## Evidence Sources

1. `o:\desktop\3v3足球机器人比赛整体策略方案-更新版.docx`。
2. `docs/match-flow-rules-analysis.md`。
3. 当前 `src/main.py`、`src/player.py`、`src/framework/*`、`src/param.py`。
4. 官方规则和教程中已抽取的事实。

## Output Model

最终响应必须包含 14 个章节：

1. 策略总体理解；
2. 图片逐张解析；
3. 需要转换坐标的图片与坐标推算；
4. 不需要固定坐标的动态策略；
5. 完整比赛流程与策略触发关系；
6. 现有代码与策略的对应关系；
7. 总体实现框架；
8. 中场开球状态规划；
9. 角球状态规划；
10. 球门球状态规划；
11. 分阶段优化顺序；
12. 风险与测试重点；
13. 需要用户确认的问题；
14. 最终规划结论。

## Coordinate Assumptions to Verify from Code

- Field origin at center.
- Our goal at negative X, opponent goal at positive X from helpers and current ready positions.
- X increases toward opponent goal; Y lateral axis.
- Angles are radians; zero faces positive X; positive rotation follows `atan2` / standard mathematical convention.
- Field length 14.0m, width 9.0m, goal width 2.6m, center circle radius 1.5m, penalty area 3.0m x 6.0m, goal area 1.0m x 4.0m.

## Risk Controls

- Distinguish document intent from inferred implementation details.
- Distinguish image-determined geometry from derived approximate coordinates.
- Mark all initial coordinates as proposed planning values pending simulation calibration.
- Do not treat fixed-tactic planning as implementation approval.

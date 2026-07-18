# Static analysis execution plan

## Phase A. Establish evidence inventory

- [x] 枚举仓库全部受版本控制和关键未跟踪文件类型，建立目录职责草图。
- [x] 读取构建、启动、配置和项目说明文件，确定候选入口与环境边界。
- [x] 分批读取五份 PDF，建立规则、建议、参数、术语和冲突笔记。

## Phase B. Reconstruct runtime and data flow

- [x] 从实际入口追踪初始化、配置加载、对象装配和主循环/回调。
- [x] 追踪比赛状态、自身、球、队友、对手数据的来源、转换、缓存和消费者。
- [x] 追踪策略决策到运动、转向、踢球、射门和发布接口的完整调用链。
- [x] 确认通信、ROS/仿真适配层和底层接口边界。

## Phase C. Reconstruct active strategy

- [x] 识别当前生效的角色分配、状态机/行为树和比赛状态分支。
- [x] 按触发、判断、动作、结束、失败/超时格式记录进攻、防守、守门和比赛重启策略。
- [x] 检查多人抢球协调、离线降级、动作抢占、状态变化恢复和超时路径。
- [x] 搜索未调用、重复、旧版和废弃逻辑并单独标注。

## Phase D. Cross-reference official guidance

- [x] 将规则强制项映射到 Demo 的处理位置或缺口。
- [x] 将每项官方推荐映射为 Demo 已实现、未实现、部分实现或后续可选。
- [x] 记录 PDF 间和 PDF/代码间的冲突、版本差异与不确定项。

## Phase E. Produce deliverable

- [x] 创建并持续完善项目根目录 `PROJECT_ANALYSIS.md`。
- [x] 完成用户要求的 16 章，添加可信度标记、路径、符号、调用关系和风险索引。
- [x] 对文档进行证据一致性复查，删除无证据推断和重复内容。
- [x] 检查 git diff，确认未修改源代码或配置。

## Validation gates

- [x] 五份 PDF 均有对应分析内容和来源标识。
- [x] 入口到动作发布至少形成一条完整主调用链。
- [x] 所有关键状态、角色和动作结论均能回指代码定义与消费位置。
- [x] 所有静态无法确认事项集中进入运行验证章节。
- [x] `PROJECT_ANALYSIS.md` 的章节与用户要求一致。
- [x] `git status --short` 只显示允许的分析与 Trellis 任务文件。

## Rollback points

- `PROJECT_ANALYSIS.md` 是唯一允许新增的项目交付文件，可在结构错误时整体重写。
- `.trellis/tasks/07-18-project-analysis/` 仅保存任务规划和研究上下文。
- 若发现任何源代码或配置被意外修改，停止分析并在继续前向用户报告。

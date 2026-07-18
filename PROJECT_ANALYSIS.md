# 虚拟足球机器人 Demo 静态分析

> 分析日期：2026-07-18  
> 分析方式：仅静态阅读、全局搜索、定义/调用点追踪和文档对照；未编译、未运行仿真、未修改源代码或配置。  
> 可信度标记：
>
> - **已确认**：代码中存在明确实现，且定义、调用和最终消费关系能够闭合；或规则 PDF 有明确条文。
> - **高度可能**：有多处代码或文档相互印证，但仍依赖外部平台细节。
> - **存在疑问**：文档、注释、配置或代码之间存在冲突，或存在多个可能解释。
> - **需要运行验证**：依赖 Booster Studio、ROS、BoosterOS SDK、裁判机实际消息或仿真时序，静态阅读无法最终确认。

## 1. 资料与分析范围

### 1.1 已分析资料

| 资料 | 主要用途 |
|---|---|
| `o:\Robot\赛场规则.pdf` | 比赛流程、状态、重启、进球、处罚、站位、触球和技术合规约束 |
| `o:\Robot\配置参数指南.pdf` | 战术参数、运动参数、重启避让、传球、带球、支援和门将调参建议 |
| `o:\Robot\如何为场上选手分配角色.pdf` | `PlayContext`、`Playbook`、角色分配、少人降级和角色行为建议 |
| `o:\Robot\如何优化机器人移动行为.pdf` | 路径绕行、行走控制、到达阈值和近邻转向避让建议 |
| `o:\Robot\如何找到最适合的射门队员.pdf` | Chaser/射手资格过滤、抢球评分、平局仲裁和出球目标选择建议 |

### 1.2 已分析项目范围

- 项目入口与构建：`agent.toml`、`build.toml`、`.booster-studio/project.json`、`README.md`。
- 全部运行相关代码：`src/main.py`、`src/player.py`、`src/param.py`、`src/framework/`、`src/utils/`。
- 平台文档：`docs/Booster Agent Framework Python API.md`、`docs/BoosterOS 开发者接口文档 - V1.0.md`。
- 测试、生成文件、第三方依赖和开发脚手架目录。

### 1.3 分析边界

- **已确认**：当前生产代码是 Python，不是 C/C++；动作最终进入外部 BoosterOS SDK，SDK 内部实现不在仓库中。
- **已确认**：本项目没有传统 `main()` 或 `if __name__ == "__main__"`；入口由 Booster Agent 平台按 `agent.toml` 反射装载。
- **已确认**：除 Trellis 任务记录和本分析文档外，本阶段没有创建或修改其他项目文件，也没有修改任何比赛代码或配置。
- **需要运行验证**：Booster Studio 装载细节、ROS Topic 实际发布情况、裁判 JSON 时序、SDK 动作完成语义和物理效果。

### 1.4 PDF 覆盖索引

以下索引用于证明五份资料均已纳入分析；行号为 PDF 文本提取后的近似范围，版式变化可能造成少量偏移。

| PDF | 已覆盖主题 | 主要来源范围 |
|---|---|---|
| 《赛场规则》 | 技术合规、场地、主状态、开球、各类出界重启、进球、定位球触球、坠球、站位、移动和处罚 | 约 L9-L329 |
| 《配置参数指南》 | 配置分层、速度、踢球迟滞、重启避让、障碍、Chaser、传球、带球、接应、门将、边界恢复、调参预设 | 约 L3-L517、L527-L705 |
| 《如何为场上选手分配角色》 | `PlayContext`、角色数据流、角色职责、动态少人、处罚/数据新鲜度和每帧分配 | 约 L39-L295 |
| 《如何优化机器人移动行为》 | via 点绕行、四态运动控制、近邻转向避让、到达/转向/速度和障碍参数 | 约 L4-L125 |
| 《如何找到最适合的射门队员》 | ReadySlot、Chaser 评分、危险区门将偏置、平局仲裁、射门/传球/带球/解围选择 | 约 L11-L137 |

## 2. 比赛流程与规则约束

### 2.1 比赛主流程

规则给出的主状态流为：

```text
INITIAL -> READY -> SET -> PLAYING -> FINISHED
```

- **已确认（规则）**：比赛启动时进入开球准备流程；有效进球后由失球方重新开球；时间耗尽或手动结束后进入 `FINISHED`。（《赛场规则》约 L69-L78、L113-L120）
- **已确认（规则）**：`READY` 允许机器人移动到合法站位；全部未罚下机器人连续稳定约 5 秒后可提前进入 `SET`，最长约 45 秒后强制进入 `SET`。（约 L85-L96）
- **已确认（规则）**：`SET` 阶段机器人必须保持不动，约 5 秒后进入 `PLAYING`。（约 L97-L107）
- **已确认（规则）**：出界摆球、裁判重新放球或 `PLAYING + stopped` 时机器人必须停止。（约 L79-L84）
- **已确认（代码）**：`src/main.py:get_phase()` 将 `READY` 单独处理，将 `SET`、`INITIAL`、`FINISHED`、`stopped` 和无裁判数据统一映射为 `Phase.STOPPED`。

### 2.2 各状态对代码的直接影响

| 裁判状态 | 规则要求 | Demo 当前处理 | 评价 |
|---|---|---|---|
| `INITIAL` | 不进行比赛动作 | `STOPPED`，对 active 球员执行 `stop()` | **已确认，基本符合** |
| `READY` | 允许走到合法站位 | `_act_ready()` 使用固定位置走位 | **已确认，部分实现**；没有动态合法性检查 |
| `SET` | 必须静止 | `STOPPED` | **已确认，方向正确**；惯性与旧命令需运行验证 |
| `PLAYING` | 按普通比赛或重启规则行动 | 按 `set_play`、`secondary_time` 和 `kicking_team` 分类 | **已确认**；字段语义依赖裁判机 |
| `PLAYING + stopped` | 必须停止 | `STOPPED` | **已确认，基本符合** |
| `FINISHED` | 比赛结束并停止 | `STOPPED` | **已确认，符合** |

### 2.3 开球

- **已确认（规则）**：开球专属球权窗口为 10 秒；超时后球进入自由状态，双方均可抢球，直接进球有效。（约 L102-L112、L121-L127）
- **已确认（规则）**：开球 `SET` 阶段所有机器人必须在场内和本方半场；非开球方不得进入中圈；开球方在对方半场的中圈内最多一人。（约 L252-L282）
- **已确认（规则）**：开球方若在专属窗口内直接进球，至少需要两次稳定触球，否则不计分并重开；窗口过期后直接进球有效。（约 L157-L165）
- **存在疑问（规则）**：“两次稳定触球”没有说明是否必须由不同机器人完成，也没有给出接触间隔、球速或稳定判定阈值。
- **已确认（代码）**：我方开球由 `_act_our_kickoff()` 锁定最近球员，持续调用 `attacker.kick(0.1, KICK_POWER_OUR_KICKOFF)`；没有两次触球状态、传球队友、球离开中点检测或完成反馈。
- **已确认（代码）**：`Player.take_kickoff()` 虽包含绕到球后再接近的逻辑，但没有任何调用点，不能视为当前开球策略。

### 2.4 界外球、球门球和角球

| 重启 | 规则球权与摆球 | 完成/超时 | 对策略的要求 |
|---|---|---|---|
| 界外球 `THROW_IN` | 整颗球越过边线，判给最后触球方的对方 | 主罚方触球/移动球后完成；专属球权 45 秒 | 固定主罚者、队友接应、防守方禁止提前触球 |
| 球门球 `GOAL_KICK` | 底线出界且判给防守方；球约放在 `x=+/-6.0, y=+/-2.0` | 专属球权 45 秒 | 安全出球、明确主罚者、对方避让 |
| 角球 `CORNER_KICK` | 底线出界且判给进攻方；球放在对应角区内约 0.05m | 专属球权 45 秒 | 传中/短角接应、防守站位、避免提前触球 |

- **已确认（规则）**：上述三类定位球中，主罚方第一次触球或移动球后，裁判清空 `setPlay`，球进入自由状态。（约 L182-L216）
- **已确认（规则）**：主罚方在上述定位球未过期、触球不超过一次时将球踢入本方球门，不计乌龙球，改判对方角球。（约 L166-L174）
- **已确认（代码）**：`_act_our_set_play()` 对三类重启全部调用 `_act_normal()`；没有独立主罚、接应、传球或完成状态。
- **已确认（代码，高风险）**：`_act_opp_set_play()` 同样调用 `_act_normal()`，会安排一名我方机器人主动追球。

### 2.5 任意球与点球

- **已确认（规则）**：对方定位球尚未开放时，防守方需与球保持至少 1.45m；若防守方提前触球，则重踢同一定位球，并罚下相关机器人 30 秒。（约 L217-L230、L271-L275）
- **已确认（规则）**：正在明显远离球的机器人可能获得短暂避让豁免；站在本方球门线上且位于球门宽度内也可能获得豁免。（约 L239-L245）
- **存在疑问（规则）**：PDF 没有完整定义直接任意球、间接任意球和点球的触发条件、摆球位置、直接得分条件及点球站位。
- **已确认（代码）**：数据类型支持 `DIRECT_FREE_KICK`、`INDIRECT_FREE_KICK` 和 `PENALTY_KICK`，但策略没有专用实现。
- **存在疑问（代码/文档）**：`src/main.py:get_set_play_type()` 的注释称直接任意球可直接得分、间接任意球需先触碰他人，但该语义没有在提供的规则 PDF 中完整出现，应以正式裁判协议为准。

### 2.6 进球、出界和比赛僵局

- **已确认（规则）**：只有整颗球越过门线、位于两门柱之间且横梁下方才构成进球；有效进球后加分并由失球方开球。（约 L144-L156）
- **已确认（规则）**：球半径为 0.11m，进球和出界都以整颗球越线为准。
- **已确认（规则）**：正常比赛连续 30 秒没有机器人触球会触发坠球；流程重新经过 `READY -> SET -> PLAYING`，双方在 `SET` 均不得进入中圈，进入 `PLAYING` 后双方都可直接抢球和得分。（约 L231-L238）
- **已确认（代码）**：Demo 没有坠球专用 Phase 或 READY 站位；是否能正确处理完全依赖裁判字段组合。

### 2.7 处罚、移动和机器人状态限制

- **已确认（规则）**：标准罚时为 30 秒；Set 非法站位、Set 移动、Stop 移动、定位球提前触球、避让距离不足、倒地过久和长时间无活动都可能触发处罚。（约 L246-L318）
- **已确认（规则）**：`SET` 宽限后，1 秒内路径长度超过 0.15m可处罚；`PLAYING + stopped` 宽限后，2 秒内路径长度超过 0.3m可处罚。（约 L289-L298）
- **已确认（规则）**：警告、黄牌和红牌存在累积升级关系；罚时期间提前返场会重置罚时并增加警告。（约 L319-L329）
- **已确认（代码）**：任何 `Penalty != NONE` 的机器人会 `stop()` 并从 active 角色候选中排除。
- **已确认（代码）**：`secs_till_unpenalised`、`warnings`、`cautions` 和红牌永久离场语义虽然已解码，但策略没有使用。
- **存在疑问（规则）**：倒地 `upDot` 阈值、长时间无活动的距离和位移阈值在 PDF 提取文本中不完整。

### 2.8 技术合规和通信限制

- **已确认（规则）**：只允许使用官方公开的机器人传感器、GameController、ROS Topic、Agent API 和 SDK；禁止连接仿真器内部管理接口、扫描端口、读取宿主机/其他进程私有信息、伪造裁判请求或干扰对手与赛事服务。（约 L9-L28）
- **已确认（代码）**：项目订阅 ground-truth 球与机器人位姿，并发布调试与日志 Topic。
- **需要运行验证**：这些 ground-truth、`/soccer/debug`、`/soccer/agent_log` 以及一个 Agent 集中控制三台机器人是否全部在本届赛事白名单中。
- **已确认（代码）**：裁判数据中的 `message_budget` 已解析但未使用；项目没有独立队内网络消息。

### 2.9 规则文本冲突和缺失

1. **存在疑问——定位球时限冲突**：规则概览近似写成“定位球固定 10 秒”，详细条目则为开球 10 秒、其他重启 45 秒。应优先采用详细条目并向赛事方确认。
2. **存在疑问——`kick-offReady` 编码不明**：未说明是独立状态还是 `READY + kickoff` 的组合。
3. **需要运行验证——`ball_free` 字段不明**：规则使用该概念，但当前 `GameControlState` 没有对应字段。
4. **存在疑问——任意球、点球、守门员专属权限不完整**。
5. **存在疑问——进球几何个别句子存在排版/提取缺失**，应以裁判实现为最终依据。

## 3. 官方优化资料总结

### 3.1 官方建议的总体结构

综合四份优化资料，可以归纳出以下分层；各层分别由配置、角色、移动和射手资料提供证据，并非每份 PDF 都完整提出了整套架构：

```text
配置/参数
  -> 世界状态 PlayContext
  -> 整队 Playbook / RoleAssignment
  -> 角色行为（Chaser / Supporter / Goalkeeper / Idle）
  -> 目标选择（射门 / 传球 / 带球 / 解围 / 接应）
  -> 导航与运动控制
  -> MoveIntent / SDK 动作
```

- **官方推荐**：角色分配结果写入共享黑板 `/team/roles`，各角色节点通过 `IsRole` 分支执行。（《如何为场上选手分配角色》约 L39-L54）
- **已确认（Demo）**：Demo 没有黑板或行为树；它在同一进程、同一帧内直接计算并调用每个 `Player` 的动作。

### 3.2 角色分配建议

**已确认（官方资料）**，角色资料建议：

- 角色候选不应只看欧氏距离，还应考虑朝向、可通行性、障碍、数据新鲜度、处罚、比分、剩余时间和重启状态。
- 角色建议包括：
  - `chaser`：追球、控球、射门；
  - `supporter`：侧后方接应和传球；
  - `goalkeeper`：门前站位和危险区出击；
  - `none/idle`：未分配时待命。
- 少人时优先保留 `goalkeeper` 和 `chaser`，可取消 `supporter`；不要永久将固定 ID 绑定到角色。
- 使用历史角色迟滞：新候选只有明显更优时才替换当前 Chaser；无历史时再按 `player_id` 做确定性平局裁决。

**Demo 对照：**

- **已实现**：每帧动态分配；处罚、跌倒、非 walk 和无位姿机器人不参与；Chaser 有 0.3m 距离迟滞；少人时自然退化为 1 名 attacker 或 attacker+guard。
- **未实现**：朝向、ETA、障碍、球速、对手威胁、比分和剩余时间评分；固定角色优先级；角色最短持有时间；无进展让权。
- **后续可选**：保留集中式架构，在 `src/main.py:_select_closest_attacker()` 中逐步扩展为可解释成本函数，不必先引入完整行为树。

### 3.3 射手、传球和出球选择建议

**已确认（官方资料）**，资料建议：

1. 使用 `ball_claim_score = distance_to_ball + slot_bias` 作为简单基线，再扩展为 ETA、朝向、障碍和角色切换成本。
2. 进入出球阶段后，特殊边线/底线脱困优先；普通情况下对射门、传球和带球候选评分；没有合法候选时解围。
3. 射门评分至少考虑射门通道、可见球门角、距离和身体朝向。
4. 传球候选需满足前向推进、通道净空和最低综合评分。
5. 使用进入距离、退出距离、退出延迟和最短激活时间构成踢球迟滞。

**Demo 对照：**

- **部分实现**：使用 `KICK_ENTER_M=2.0` 和 `KICK_EXIT_M=2.5` 的距离迟滞；从球后方接近射门方向。
- **未实现**：退出延迟、最短激活时间、传球、带球、候选评分、射门通道和门将位置判断。
- **未调用实现**：`Player.kick_can_score()` 能判断直线是否穿过球门，但无调用点。
- **当前限制**：`Player.plan_kick()` 始终瞄准对方球门中心附近；`attack(kick_target)` 的自定义目标只影响接近站位，最终踢球仍调用固定球门目标。

### 3.4 守门和防守建议

**已确认（官方资料）**，配置与角色资料建议：

- 门将通常守门线/门前，根据球进入危险区域决定出击；官方参数指南提供 `goalkeeper_challenge_margin_m` 控制出击区域。
- 防守建议保持中路、覆盖球到己方球门的危险通道、避免与 Chaser 重复追球；门将出击时其他角色补位。
- 稳健防守可增大对手障碍半径、安全余量和支援纵深，减小门将出击范围。

**Demo 对照：**

- **部分实现**：guard 在剩余球员中动态选择，support 位于球到己方球门连线上。
- **未实现**：沿门线跟随、球轨迹预测、拦截、出击、扑救、解围和门将/补门协作。
- **反向行为**：当前 `guard()` 使用 `avoid_ball=True`，会将门前球当作障碍绕开。

### 3.5 定位、导航和避障建议

**已确认（官方资料）**，移动资料建议三层结构：

1. 路径层：检测直线路径走廊中的障碍，生成 via 点或局部绕行目标。
2. 控制层：已到达/仅转向/先转向/边走边转四种状态，速度随距离和角差变化。
3. 近邻层：预测短期距离，只修正 `vyaw` 进行近邻避让。

文档示例参数包括：到达距离 0.15m、转向阈值 0.5rad、障碍半径、安全余量、起点/终点忽略区和近邻预测窗口。

**Demo 对照：**

- **已实现**：`Player.walk_to()` 有到达判断、近距全向控制、远距先转向、速度夹紧；`src/utils/path_planner.py` 提供 8 邻域 A*；失败后使用方向扫描回退。
- **已实现**：球、队友、对手和两侧球门可作为圆形障碍。
- **不同于资料**：Demo 当前主路径是 A* + 局部方向扫描，不是资料描述的单 via 点 + `vyaw` 预测修正；`utils.obstacles.detour()` 是未调用的候选/疑似旧实现。
- **未实现**：路径缓存、动态轨迹预测、移动卡死/超时、到达结果向高层反馈。

### 3.6 配置方法建议

**已确认（官方资料）**，配置资料建议将比赛固定配置与策略调参分离，例如：

- `SoccerConfig`：队伍 ID、机器人名称、场地、控制频率、Topic。
- `SoccerStrategyTuning`：速度、踢球迟滞、避障、传球、带球、支援和门将参数。

**Demo 对照：**

- **已实现**：`SoccerConfig.from_env()` 读取队伍 ID、机器人名、对手名、控制频率和裁判 Topic。
- **部分实现**：策略参数集中在 `src/param.py`，但为 import-time 常量，不支持运行时配置、校验或预设切换。
- **未实现**：参数合法性校验、嵌套参数对象、场地尺寸外部注入、比赛策略预设。

### 3.7 PDF 之间的冲突或版本差异

| 项目 | 文档差异 | Demo 当前值 | 判断 |
|---|---|---:|---|
| 到达角度 | 0.12rad 与 0.20rad 两种版本 | `walk_to()` 使用 0.1rad 完成朝向 | **存在版本差异** |
| 线速度保底 | 0.15m/s 与 0.3m/s | 没有 floor；P 控制后夹到 2.0m/s | **实现不同** |
| Chaser 平局 | 保留历史 Chaser vs 直接选较小 ID | 历史 Chaser 0.3m 迟滞 | **部分采用第一种** |
| `pass_enabled` | “传球总开关”与个别文本含义冲突 | 无此参数 | **文档疑似笔误/版本差异** |
| 出球决策 | 固定优先链 vs 候选综合评分 | 固定直接射门 | **Demo 未采用** |
| 场地罚球区 | 参数指南一处写 2m x 5m | 规则与代码为 3m x 6m | **明显版本冲突；以规则和本届场地为准** |
| 罚球点距离 | 参数指南写 1.5m | 代码 `penalty_dist=2.1` | **规则 PDF 未闭合，需赛事方确认** |
| 踢球力度 | 建议常见范围约 0.8-2.5，演示又使用 5.0 | 5.0 | **可能是接口版本/演示差异** |

### 3.8 强制、推荐和可选方法的分类

| 内容 | 类型 | Demo 状态 |
|---|---|---|
| 对方重启至少离球 1.45m | **规则强制** | **未实现，且当前可能主动追球** |
| Set/Stop 阶段静止 | **规则强制** | 顶层已处理；底层惯性需验证 |
| 开球两次稳定触球或等待过期后直接射门 | **规则强制** | 未实现 |
| 动态 Chaser/Guard/Support | 官方推荐 | 基础版本已实现 |
| 角色迟滞 | 官方推荐 | 距离迟滞已实现 |
| 射门/传球/带球综合评分 | 官方推荐 | 未实现，后续可选 |
| A* 或 via 点避障 | 官方方法 | Demo 使用 A* + 扫描回退 |
| 黑板和行为树 | 官方推荐结构 | Demo 未采用；不是规则要求 |
| 传球、带球、激进/稳健预设 | 后续可选方法 | 未实现，不应在本阶段直接采用 |

## 4. 项目目录和模块说明

```text
sim-3v3-simple-framework/
├─ agent.toml                    # Booster Agent 元数据和入口
├─ build.toml                    # Python 构建、目标平台、依赖
├─ README.md                     # Booster Studio 启动说明
├─ .booster-studio/project.json # football3v3 / soccer-match 场景
├─ docs/                         # Booster Agent 和 BoosterOS API 文档
├─ src/
│  ├─ main.py                    # 顶层 Phase、角色分配、比赛策略
│  ├─ player.py                  # 单机器人动作、走位、踢球和高层角色行为
│  ├─ param.py                   # 策略、运动和导航常量
│  ├─ framework/
│  │  ├─ agent.py                # Agent 生命周期和对象装配
│  │  ├─ config.py               # 环境变量配置
│  │  ├─ runtime.py              # 30Hz 循环、Context 构造、故障停车
│  │  ├─ ros_source.py           # ROS 真值与裁判状态输入
│  │  ├─ game_codec.py           # GameController JSON 解码
│  │  ├─ types.py                # 数据契约和枚举
│  │  ├─ robot_backend.py        # BoosterRobot / SoccerKickManager 适配
│  │  ├─ debugdraw.py            # `/soccer/debug` MarkerArray
│  │  └─ log_publisher.py        # `/soccer/agent_log`
│  └─ utils/
│     ├─ geom.py                 # 纯几何函数
│     ├─ obstacles.py            # 障碍建模和未调用的 detour
│     └─ path_planner.py         # A* 栅格规划
└─ .trellis/、.agents/ 等        # 开发流程脚手架，不参与比赛运行
```

- **已确认**：没有 `tests/`、`test/`、`test_*.py`、CI 或仓库内自动化测试。
- **已确认**：在当前仓库可见文件中未发现 vendor 二进制或生成产物；ROS 和 BoosterOS 依赖由外部运行环境提供。
- **已确认**：`build.toml` 声明 `py_trees==2.4.0`，但源码没有任何 import 或行为树节点。
- **存在疑问**：`agent.toml` 引用 `/res/logo.png`，仓库中未发现 `res/`。

## 5. 程序入口与运行流程

### 5.1 入口和初始化

**已确认**的完整装配链：

```text
agent.toml: entry = "src/main.py:SoccerSimAgent"
  -> Booster 平台实例化 SoccerSimAgent
  -> SoccerAgentMixin.__init__()
  -> AgentBase.__init__(AgentFeatures())
  -> SoccerConfig.from_env()
  -> RosContextSource(config)
  -> SoccerRuntime(agent, source)
  -> 创建 Player 1..N
  -> 为每个 Player 创建 RobotBackend
  -> RobotBackend 创建 BoosterRobot + SoccerKickManager + worker
```

关键符号：

- `agent.toml:entry`
- `src/main.py:SoccerSimAgent`
- `src/framework/agent.py:SoccerAgentMixin.__init__()`
- `src/framework/config.py:SoccerConfig.from_env()`
- `src/framework/runtime.py:SoccerRuntime.__init__()`
- `src/framework/agent.py:_create_backends()`

### 5.2 激活、主循环和关闭

```text
on_agent_activated()
  -> SoccerRuntime.start()
  -> RosContextSource.start()
     -> ROS node + subscriptions + executor thread
  -> init_store()（仅首次）
  -> soccer_runtime 控制线程
  -> 默认 30Hz _loop()
  -> _tick()
  -> 构造 Context
  -> SoccerSimAgent.play()
```

- **已确认**：`SoccerRuntime._loop()` 以 `1 / control_hz` 为目标周期；单帧超时不会补偿或跳帧。
- **已确认**：任一未捕获的 tick 异常会记录日志，并逐个调用 `Player.stop()`；线程下一帧继续运行。
- **已确认**：关闭时停止控制线程、ROS source 和全部 backend，释放踢球并尝试发零速度。
- **需要运行验证**：Agent 对象关闭后是否可能再次激活；backend 被关闭后并未重新创建。

### 5.3 一条完整动作调用链

```text
ROS ball / robot pose / GameController
  -> RosContextSource 回调
  -> WorldSnapshot
  -> SoccerRuntime._build_context()
  -> 新鲜度过滤后的 Context
  -> SoccerSimAgent.play()
  -> get_phase()
  -> _act_normal()
  -> Player.attack()
  -> Player.walk_to() 或 Player.kick()
  -> RobotBackend.set_velocity() / kick()
  -> BoosterRobot.set_velocity() / SoccerKickManager
```

这是当前生产路径，不经过行为树或独立机器人间通信。

## 6. 数据流和比赛状态

### 6.1 ROS 输入

| 数据 | 默认 Topic | 类型 | 写入位置 |
|---|---|---|---|
| 我方位姿 | `/team{team_id}/{robot_name}/soccer/sim/ground_truth/robot_pose` | `Pose2D` | `RosContextSource._make_pose_cb()` |
| 对手位姿 | 同一 team namespace 下的 opponent robot name | `Pose2D` | 同上，写入 `_opponents` |
| 球 | `/team{team_id}/soccer/sim/ground_truth/ball` | `Pose2D` | `_ball_cb()` |
| 裁判机 | `/soccer/game_controller` | `String(JSON)` | `_game_cb()` |

- **已确认**：所有数据都使用 `time.monotonic()` 记录本进程接收时间。
- **已确认**：ROS 回调用 `RLock` 保护，控制线程通过 `get_snapshot()` 获取字典浅拷贝。
- **需要运行验证**：对手真值是否确实位于本队 namespace；team 2 坐标是否由仿真器翻转为“+x 朝对方门”。

### 6.2 新鲜度过滤

`src/framework/config.py:SoccerConfig` 默认：

- 球：1.5 秒；
- 机器人位姿：2.0 秒；
- 裁判状态：2.0 秒。

`src/framework/runtime.py` 的行为：

- 裁判过期 -> `Context.game = None` -> `Phase.STOPPED`；
- 球过期 -> `Context.ball = None`；
- 位姿过期 -> 保留 `RobotState`，但将 `pose` 置 `None`。

### 6.3 裁判数据契约

`GameControlState` 已解析：

- `players_per_team`、`stopped`、`game_phase`、`state`、`set_play`；
- `kicking_team`、`secs_remaining`、`secondary_time`；
- 比分、守门员编号、处罚、解罚时间、警告、黄牌和 `message_budget`。

当前策略实际消费的主要字段只有：

- `state`
- `stopped`
- `set_play`
- `kicking_team`
- `secondary_time`
- 每名球员的 `penalty`

未使用的字段包括 `game_phase`、比分、剩余时间、守门员编号、通信预算和警告信息。

### 6.4 解码兼容性

- **已确认**：`game_codec.py` 直接构造 Enum；未知枚举值会抛 `ValueError`，整包被 `_game_cb()` 忽略。
- **高度可能**：协议新增值时，Agent 会继续使用上一份有效裁判状态，直到 2 秒后进入停车降级。
- **需要运行验证**：正式裁判 JSON 的大小写、枚举值和是否存在未解析的 `ballFree` 等字段。

## 7. 角色分配与多人协作

### 7.1 当前集中式角色分配

`src/main.py:_act_normal()`：

1. 从 active 球员中选择距离球最近者为 attacker。
2. 若上一 attacker 不比新最佳者差超过 `ATTACKER_KEEP_DIST_MARGIN_M=0.3m`，保留上一 attacker。
3. 在剩余球员中选择离己方球门最近者为 guard。
4. 所有剩余球员为 support。

- **已确认**：同一进程集中控制全部机器人，通常每帧只有一个机器人调用 `attack()`，从策略层避免多人同时抢球。
- **已确认**：guard 和 support 的走位都启用避球，进一步减少非 attacker 触球。
- **已确认**：局部避障按 Player ID 奇偶优先选择不同绕行侧，减少对称冲突。

### 7.2 少人、处罚和离线降级

active 过滤顺序位于 `SoccerSimAgent.play()`：

- 跌倒 -> 异步 `get_up()`，不参与本帧分派；
- mode 非 `walk` -> 异步切模式，不参与；
- 被罚下 -> `stop()`，不参与；
- 自身位姿未知 -> `stop()`，不参与；
- 其余进入 active。

自然降级结果：

- 3 人：attacker + guard + support；
- 2 人：attacker + guard；
- 1 人：只有 attacker；
- 0 人：该策略分支不动作。

- **已确认**：单个队友位姿恢复后会自动重新加入角色分配。
- **已确认**：裁判通信超过 2 秒中断时全队停车。
- **已确认**：在当前运行代码中未发现队友心跳、角色租约、分布式共识或传球协商；集中式共享 Context 取代了队内通信。

### 7.3 当前角色分配缺口

以下均为**已确认的当前实现缺口**，除非单项另有标记：

- Chaser 代价只考虑距离；不考虑朝向、障碍、球速、机器人速度和预估到球时间。
- guard 不读取裁判指定 `goalkeeper`，可能与正式守门员身份不同。
- 没有 attacker 无进展让权、最短角色持有时间或角色切换原因日志。
- 无球时距离均为 `inf`，仍会选出一个 attacker；角色语义不再可靠。
- `FALLEN_COST` 虽被距离函数调用，但 fallen 球员在此前已被排除，因此当前生产路径中实质无效。

## 8. 当前进攻、防守和守门员策略

### 8.1 普通进攻

`Player.attack()`：

- 球或自身位姿缺失：`stop()`。
- 距球小于进入阈值 2.0m：进入踢球状态。
- 踢球状态中，只有距球大于 2.5m 才退出。
- 未进入踢球：计算球到目标方向的后方 0.35m 点，直接走向该点，不避机器人。
- 进入踢球：`plan_kick()` 瞄准对方球门中心附近，调用 `SoccerKickManager`。

**已确认的局限：**

- 不判断控球权、对手遮挡、射门角、门将位置和球速。
- 不传球、不带球、不选择空门角。
- 前后场踢球力度常量当前都为 5.0，代码分支不会产生实际力度差异。
- 没有触球成功、射门完成、进球确认或踢球超时。

### 8.2 当前防守

Demo 没有独立“攻/守转换”状态。无论球在何处，NORMAL 始终为一攻、一守、一支援。

support 目标：球到己方球门中心的连线上，距球最多 3m，并夹在场内；到点时面向球，避球和机器人。

- **高度可能**：该站位有基础补防作用。
- **高度可能**：support 与 guard 都偏向中路，缺少左右槽位，可能互相阻挡或扎堆。

### 8.3 当前守门员

`Player.guard()`：

- 目标固定为己方球门区中心，约 `(-6.5, 0)`。
- 球可见时面向球，否则朝 +x。
- 走位时避球、避机器人和球门结构。

**已确认未实现：**

- 沿门线横向跟踪；
- 球轨迹、门线交点和威胁预测；
- 出击、拦截、扑救、封角；
- 门前解围；
- 门将出击后其他机器人补门。

`_guard_threatened`、`_goal_line_push`、`GUARD_THREAT_ENTER_X` 和 `GUARD_THREAT_EXIT_X` 均没有当前调用关系，属于未完成或遗留痕迹。

## 9. 开球及其他比赛重启策略

### 9.1 READY 站位

`_act_ready()` 按 active 列表顺序分配最多三个固定位置。

我方开球：

1. `(-circle_radius, 0)`；
2. `(-length/2 + goal_area_length, 0)`；
3. `(-0.5, circle_radius + 2)`。

对方开球：

1. `(-circle_radius - 0.5, 0)`；
2. `(-length/2 + goal_area_length, 0)`；
3. `(-length/2 + penalty_area_length, 0)`。

- **已确认**：走位开启避球和避机器人。
- **存在疑问**：第一名我方开球站位位于中圈边界，未考虑机器人实体半径和裁判容差。
- **已确认**：路径规划只保证最终目标在合法位置，不把中圈/半场作为动态禁区，途中可能穿越受限区域。
- **已确认**：坠球没有独立 READY 站位。

### 9.2 我方开球

- 锁定距球最近的 active 球员为主罚者。
- 主罚者失活时重新选择。
- 主罚者持续向固定场地方向 0.1rad、力度 5.0 踢球。
- 余者中一人 guard，其余停车。

缺少：预备站位、两次稳定触球、接球队友、球离开中点、超时和重开恢复。

### 9.3 对方开球

- 一人 guard；其余两人走到中圈外固定点。
- 完成完全依赖 GameController 退出开球 Phase。
- 没有将中圈和半场边界作为导航禁区。

### 9.4 我方定位球

全部重启类型退化为 `_act_normal()`。这意味着主罚者不锁定、队友不执行接应槽位、间接任意球无二触球状态、点球无专用站位。

### 9.5 对方定位球

直接执行 `_act_normal()`，是当前最严重的规则风险之一：最近机器人会主动追球，既没有 1.45m 距离保护，也没有提前触球保护。

## 10. 行为树或状态机结构

### 10.1 当前真实结构

**已确认：当前没有行为树。**

顶层是 `src/main.py:Phase`：

```text
NORMAL
OUR_KICKOFF
OPP_KICKOFF
OUR_SET_PLAY
OPP_SET_PLAY
READY
STOPPED
```

`get_phase()` 的优先级为：

```text
game=None -> STOPPED
READY -> READY
PLAYING && !stopped:
  set_play != NONE -> OUR/OPP_SET_PLAY
  secondary_time > 0 -> OUR/OPP_KICKOFF
  otherwise -> NORMAL
其他 -> STOPPED
```

### 10.2 跨帧状态

`store` 当前保存：

- `prev_phase`
- `cur_phase`
- `kickoff_taker`
- `normal_attacker`

`Player` 当前真正生效的跨帧状态主要是 `_kicking`。其他多个 guard/support 字段没有读写。

### 10.3 状态机缺口

- 没有 `game_phase` 的 TIMEOUT、加时和点球大战分支。
- 没有各定位球内部子状态。
- 没有动作 `RUNNING/SUCCEEDED/FAILED/TIMED_OUT/PREEMPTED` 契约。
- 没有走位、踢球、起身和模式切换的完成回调。
- `py_trees` 仅为未使用依赖，不能据此认为 Demo 已经实现行为树。

## 11. 配置文件和关键参数

### 11.1 环境配置

| 环境变量 | 默认值 | 消费位置 |
|---|---|---|
| `SOCCER_TEAM_ID` | `1` | Topic、Context、处罚和主罚队判断 |
| `SOCCER_ROBOT_NAMES` | `robot1,robot2,robot3` | Player 数量、队友 Topic、Backend |
| `SOCCER_OPPONENT_ROBOT_NAMES` | team 1 时 `robot4,5,6` | 对手 Topic |
| `SOCCER_CONTROL_HZ` | `30.0` | runtime 周期 |
| `SOCCER_GAME_CONTROLLER_TOPIC` | `/soccer/game_controller` | 裁判订阅 |

- **已确认**：没有队伍 ID、机器人数量、数值范围和重复名称校验。
- **已确认**：特殊名称 `default/none/null` 会归一化为空字符串；多机器人环境中的连接语义需验证。

### 11.2 场地参数

`src/framework/types.py:ADULT_FIELD_DIMENSIONS`：

- 长 14.0m、宽 9.0m；
- 球门宽 2.6m；
- 中圈半径 1.5m；
- 罚球区 3.0m x 6.0m；
- 球门区 1.0m x 4.0m；
- `penalty_dist=2.1m`。

除 `penalty_dist` 外，主要几何与比赛规则 PDF 一致。参数指南中的部分场地数值与规则/代码冲突，不应直接覆盖。

### 11.3 当前生效策略参数

- 踢球：`KICK_POWER_DEFAULT=5.0`、`KICK_ENTER_M=2.0`、`KICK_EXIT_M=2.5`。
- 角色：`ATTACKER_KEEP_DIST_MARGIN_M=0.3`、`SUPPORT_DIST_M=3.0`。
- 运动：到达 0.15m、近距阈值 1.0m、最大线速度 2.0m/s、最大角速度 2.0rad/s。
- 导航：0.35m A* 栅格、障碍膨胀、安全半径和局部扫描参数。

### 11.4 定义但未生效或仅旧代码使用的参数

- `GUARD_THREAT_ENTER_X`
- `GUARD_THREAT_EXIT_X`
- `CENTER_LEAVE_DIST_M`
- `OPP_SET_WALL_DIST_M`
- `OPPONENT_RESTART_AVOID_M`
- `CIRCLE_MARGIN_M`
- `KICKOFF_STAGE_M`、`KICKOFF_FRONT_MARGIN`、`KICKOFF_LATERAL_TOL`：只被未调用的 `take_kickoff()` 使用。

调整这些参数不会自动改变当前比赛行为，必须先确认其消费路径。

## 12. 仿真、通信与底层接口

### 12.1 仿真输入边界

`src/framework/ros_source.py` 是 ROS/仿真数据边界；策略层不直接 import ROS。`ContextSource` 协议只暴露 `start()`、`stop()`、`get_snapshot()`。

- **已确认**：当前实现使用仿真 ground-truth，虽然 `build.toml` 声明支持 `real_jetson`，但仓库没有真机感知数据源。
- **高度可能**：真正的真机支持需要另一个未包含的数据源适配，而不是简单调参。

### 12.2 动作边界

`src/framework/robot_backend.py` 封装：

- `BoosterRobot.set_velocity()`
- `BoosterRobot.set_gait("soccer")`
- `BoosterRobot.set_mode()`
- `BoosterRobot.get_up()`
- `SoccerKickManager.start/update_command/update_ball/stop`

速度和踢球只在 backend 确认 `_mode == "walk"` 时下发；踢球期间速度命令被忽略。

- **已确认**：Player 先将场地坐标下的球和方向转换为机器人体坐标，再调用 backend。
- **需要运行验证**：SDK mode 返回类型、踢球方向/力度的真实语义、持续更新频率和动作完成行为。

### 12.3 慢操作和恢复

每个 backend 有一个长度为 1 的覆盖式意图槽，异步执行 mode 切换和起身。新请求可能覆盖尚未执行的旧请求；上层每帧重复请求以实现最终重试。

- **已确认**：没有最大重试次数、完成 future、失败状态或动作 deadline。
- **已确认**：`fall_down_recoverable` 被采集但没有消费者。
- **已确认**：fallen 或 switching_mode 分支没有显式 `stop()`/`release_kick()`，旧动作是否继续取决于 SDK。

### 12.4 调试与日志

- `/soccer/debug`：每帧发布 MarkerArray，先 `DELETEALL` 再发布当前帧。
- `/soccer/agent_log`：将 Python logging 转成 ROS `Log`。

- **高度可能**：调试发布增加 30Hz DDS 带宽和 CPU 开销。
- **已确认**：debugdraw 异常可能冒泡到 runtime 总异常处理并导致全员停车。
- **高度可能**：重复激活会累积 ROS log handler，因为安装时没有去重或卸载。

### 12.5 原则上不应因策略优化修改的边界

- `src/framework/agent.py`
- `src/framework/runtime.py`
- `src/framework/ros_source.py`
- `src/framework/robot_backend.py`
- `src/framework/game_codec.py`
- `src/framework/types.py`
- `agent.toml`、`build.toml`、`.booster-studio/project.json`

只有确认平台协议、数据源、生命周期或 SDK 适配存在独立缺陷时，才应另立任务修改。

## 13. 当前策略完整流程

以下均描述当前生效代码，不包含未调用的候选实现。

### 13.1 比赛启动与 READY

**触发条件**  
裁判状态为 `READY`。

**判断过程**  
读取 `kicking_team` 判断我方或对方开球；按 active 列表顺序选择最多三名机器人。

**执行动作**  
移动到固定站位，面向 +x，避球和机器人。

**结束条件**  
裁判状态离开 `READY`。

**失败或超时处理**  
没有到位反馈和站位合法性检查；路径失败回退局部扫描；位姿离线者被排除；READY 最长时间由裁判推进。

### 13.2 SET、停止与比赛结束

**触发条件**  
`INITIAL`、`SET`、`FINISHED`、`PLAYING + stopped` 或裁判数据缺失。

**判断过程**  
`get_phase()` 统一返回 `STOPPED`。

**执行动作**  
对 active 球员释放踢球并发送零速度。

**结束条件**  
裁判进入可行动 Phase。

**失败或超时处理**  
backend 异常只记录日志并在下一帧重试；fallen/切模式球员不在 active 中，没有同帧显式停车。

### 13.3 普通比赛角色分配

**触发条件**  
`PLAYING`、未 stopped、无定位球且不处于识别出的开球窗口。

**判断过程**  
选择最近球员为 attacker，使用 0.3m 历史迟滞；剩余中离己方门最近者为 guard；其余 support。

**执行动作**  
分别调用 `attack()`、`guard()`、`support()`。

**结束条件**  
每帧重新计算；Phase 变化时清除 normal attacker 记忆。

**失败或超时处理**  
没有角色或动作超时；单帧异常时 runtime 全员停车，下一帧重试。

### 13.4 Attacker 追球和射门

**触发条件**  
机器人被分配为 attacker。

**判断过程**  
检查球和位姿；根据 2.0m/2.5m距离迟滞决定追球或踢球。

**执行动作**  
追球时走到球后 0.35m；踢球时固定瞄准对方球门中心附近，力度 5.0。

**结束条件**  
距球超过退出阈值、角色/Phase 改变或上层调用其他会释放踢球的动作。

**失败或超时处理**  
无球/无位姿时停车；SDK 异常由 backend 记录并下一帧继续；无踢球完成和超时。

### 13.5 Guard 守门

**触发条件**  
机器人被分配为 guard。

**判断过程**  
计算固定球门区中心；球可见时计算面向球的角度。

**执行动作**  
走向固定 home，避球和机器人。

**结束条件**  
角色或 Phase 变化；到达后停止平移并调整朝向。

**失败或超时处理**  
无位姿时停车；无拦截、解围、卡死或出击恢复状态。

### 13.6 Support 支援/补防

**触发条件**  
active 人数在 attacker 和 guard 之外仍有剩余。

**判断过程**  
在球到己方球门连线上计算距球最多 3m 的目标，并夹在场内。

**执行动作**  
走向目标，面向球，避球和机器人。

**结束条件**  
角色或 Phase 变化。

**失败或超时处理**  
无卡死处理；球为 `None` 时直接访问 `ball.x/ball.y` 会抛异常，由 runtime 触发全员停车。

### 13.7 我方开球

**触发条件**  
`PLAYING`、无 set play、`secondary_time > 0`、`kicking_team` 为我方。

**判断过程**  
进入 Phase 时选择并锁定最近球员；主罚者失活后重新选择。

**执行动作**  
主罚者持续固定方向踢球；一人 guard；其余停车。

**结束条件**  
裁判字段使 Phase 退出。

**失败或超时处理**  
没有本地完成、两触球、超时或球离开中心检测；SDK 失败时下一帧重试。

### 13.8 对方开球

**触发条件**  
识别为 `OPP_KICKOFF`。

**判断过程**  
选择离己方门最近者 guard，其余按列表顺序取固定槽位。

**执行动作**  
guard 回门，其余走到中圈外。

**结束条件**  
裁判退出开球 Phase。

**失败或超时处理**  
无到位反馈；超过三名时多余机器人不会收到该分支的新命令，但默认 3v3 不触发。

### 13.9 我方定位球

**触发条件**  
`set_play != NONE` 且主罚队为我方。

**判断过程**  
读取定位球类型，但所有分支最终相同。

**执行动作**  
复用普通比赛 attacker/guard/support。

**结束条件**  
裁判清除 `set_play` 或改变状态。

**失败或超时处理**  
没有主罚锁定、二触球、接应、重踢或 45 秒管理。

### 13.10 对方定位球

**触发条件**  
`set_play != NONE` 且主罚队为对方。

**判断过程**  
不检查具体类型或球权是否开放。

**执行动作**  
直接复用普通比赛，安排一名机器人追球。

**结束条件**  
裁判清除 `set_play`。

**失败或超时处理**  
没有避让或提前触球保护；可能触发重踢和 30 秒处罚。

### 13.11 位姿、裁判和球数据中断

**触发条件**  
数据超过新鲜度阈值或 ROS spin 停止更新。

**判断过程**  
位姿、球和裁判分别按 2.0s、1.5s、2.0s 过滤。

**执行动作**  
位姿离线机器人停车并退出角色分配；裁判离线全队进入 STOPPED；球离线时 attacker 停车、guard 回门、support 触发异常路径。

**结束条件**  
新鲜数据恢复。

**失败或超时处理**  
没有主动重连状态；ROS subscription 保持存在；tick 异常最终使全队停车。

## 14. 代码中的问题、冲突和疑点

### 14.1 高优先级规则/安全问题

1. **对方定位球主动追球**  
   `src/main.py:_act_opp_set_play()` -> `_act_normal()`。违反 1.45m 避让和禁止提前触球的风险极高。**已确认代码行为。**

2. **开球没有两次稳定触球**  
   固定方向直接踢，可能形成无效直接进球。**已确认实现缺失。**

3. **所有我方定位球均无专用流程**  
   间接任意球、点球、界外球、角球和球门球均可能不满足规则或战术要求。**已确认。**

4. **球数据缺失时 support 空值异常**  
   `src/player.py:support()` 未检查 `ball is None`；三名 active 时可导致每帧 tick 异常和全员停车。**已确认静态路径。**

5. **fallen/切模式分支未显式抢占旧动作**  
   `ensure_ready()==False` 时顶层没有 `stop()`；旧速度或 kick 是否继续由 SDK 决定。**已确认控制流，物理后果需验证。**

### 14.2 策略问题

以下均为**已确认的代码现状**；涉及实际比赛后果的部分仍需结合仿真验证：

- guard 固定站位且主动避球，没有任何扑救或解围。
- 普通策略没有攻防转换、传球、带球、射门候选和对手威胁。
- 所有高层调用忽略 `walk_to()` 的到达返回值。
- 没有动作超时、进展监测、卡死恢复和失败次数。
- `attack(kick_target)` 的目标不会真正控制最终踢球方向。
- Ready 和开球路径未将规则区域视为动态禁区。

### 14.3 未调用、重复或遗留逻辑

| 符号/字段 | 状态 | 说明 |
|---|---|---|
| `Player.take_kickoff()` | 未调用 | 较完整的开球预备动作，但当前主流程绕过 |
| `Player.kick_can_score()` | 未调用 | 射门直线可行性 helper |
| `Player.block_path_projection()` | 未调用 | 疑似旧防守封堵逻辑 |
| `Player.face_to()` | 未调用 | 功能被 `walk_to(face=...)` 覆盖 |
| `utils.obstacles.detour()` | 未调用 | 疑似旧版或候选的单障碍 via 点算法 |
| `_avoid_side` 等多个 Player 字段 | 仅初始化 | 旧版/未完成状态 |
| `_act_our_set_play()` 多分支 | 重复实现 | 所有分支相同 |
| `KICK_POWER_BACKFIELD` | 当前效果重复 | 与默认力度同为 5.0 |
| `py_trees` | 未使用依赖 | 当前无行为树 |

### 14.4 文档和注释漂移

- 多个模块引用不存在的 `docs/new_design.md`。
- `src/__init__.py` 的目录说明与当前实际结构不一致。
- `src/main.py` 顶部称开球使用 `take_kickoff()`，实际没有。
- `plan_kick()` 注释称力度 2.0，实际常量为 5.0。
- `log_publisher.py` 注释称发布 `/rosout`，实际为 `/soccer/agent_log`。

### 14.5 平台和配置疑点

- **已确认**：`agent.toml` 的 `/res/logo.png` 在当前仓库中不存在；构建后果需验证。
- **已确认**：`build.toml` 声明 `real_jetson`，但当前数据源为仿真真值。
- **高度可能**：源码 import 的 ROS/Booster 依赖由平台镜像提供，因为它们未列入普通 Python 依赖。
- **已确认**：`Context` 为 frozen dataclass，但内部 dict 可变，不是深只读。
- **已确认**：A* 允许 start/goal 栅格位于障碍内；末端实际碰撞风险需运行验证。

## 15. 需要运行验证的内容

1. Booster Studio 如何装载 `src/main.py:SoccerSimAgent` 并处理相对 import。
2. 缺失 `/res/logo.png` 是否影响构建。
3. 正式 GameController JSON 的全部字段、大小写和枚举值。
4. `secondaryTime` 的单位、递减方式、适用状态和开球完成后的变化。
5. 是否广播 `ballFree`、`kickOffReady`、触球次数或重踢标志。
6. 坠球、直接/间接任意球、点球和重踢的真实字段组合。
7. `stopped` 在出界、进球、摆球、处罚和重启中的置位/清除时刻。
8. 两次“稳定触球”的裁判算法。
9. 站位、中圈和 1.45m 距离按机器人中心还是碰撞体边缘计算。
10. Set/Stop 移动处罚的宽限时间、倒地 `upDot` 和无活动阈值。
11. 球门线避让豁免的几何容差和适用身份。
12. team 2 是否自动得到“己方在 -x、进攻朝 +x”的坐标。
13. 对手 pose Topic 是否确实使用本队 namespace。
14. ground-truth、调试和日志 Topic 是否为赛事允许接口。
15. 集中式单 Agent 控制三台机器人是否为正式部署模型。
16. `BoosterRobot.get_mode()` 是否返回普通字符串。
17. SoccerKickManager 的方向、力度、球体坐标、更新频率和完成条件。
18. mode 切换、起身和跌倒时旧速度/kick 是否由 SDK 自动取消。
19. 球 Topic 中断 1.5 秒后的 support 异常、日志频率和停车表现。
20. 每帧最多多个 A* 搜索、Marker 发布和日志在 30Hz 下的 CPU/DDS 开销。
21. Agent close 后再次 activated 是否受支持。
22. 未知 GameController 枚举导致整包丢弃的实际风险。
23. 罚下机器人是否由仿真器自动移走，以及合法返场流程。
24. 速度上限 2.0m/s、角速度 2.0rad/s 和力度 5.0 的真实稳定性与合规性。

## 16. 后续修改索引

### 16.1 可以优先修改的策略层

| 路径/符号 | 当前作用 | 上下游 | 可影响策略 | 风险 |
|---|---|---|---|---|
| `src/main.py:get_phase()` | 裁判状态到 Phase | `Context.game` -> 所有 `_act_*` | 开球、定位球、TIMEOUT、坠球 | **高**；错误会全局违规 |
| `src/main.py:_select_closest_attacker()` | 最近球员和历史迟滞 | active/ball -> `_act_normal()` | Chaser ETA、角色稳定、门将限制 | 中 |
| `src/main.py:_act_normal()` | 普通角色分配 | Phase -> `attack/guard/support` | 攻防转换、动态阵型、多人协作 | 中 |
| `src/main.py:_act_our_kickoff()` | 我方开球 | GameController -> `Player.kick` | 两触球、传球、主罚锁定 | **高** |
| `src/main.py:_act_opp_kickoff()` | 对方开球站位 | GameController -> `walk_to/guard` | 合法中圈外阵型 | **高** |
| `src/main.py:_act_our_set_play()` | 我方定位球 | `SetPlay` -> 当前 normal | 各重启专用状态机 | **高** |
| `src/main.py:_act_opp_set_play()` | 对方定位球 | `SetPlay` -> 当前 normal | 1.45m避让、墙和禁区防守 | **最高** |
| `src/main.py:_act_ready()` | READY 站位 | `kicking_team` -> `walk_to` | 合法槽位、坠球、掉线补位 | 中高 |
| `src/param.py` | 策略和运动常量 | 被 main/player/utils import | 阈值、力度、距离、避障 | 低至中；先确认调用点 |

### 16.2 可修改但需要谨慎的公共动作层

| 路径/符号 | 当前作用 | 上下游 | 可影响策略 | 风险 |
|---|---|---|---|---|
| `src/player.py:attack()` | 追球+固定射门 | main -> backend | 射门、传球、带球、迟滞 | 中高；当前定位球也间接使用 |
| `src/player.py:plan_kick()` | 踢球方向/力度 | `attack/kick` -> backend | 空门角、通道、力度模型 | 中 |
| `src/player.py:guard()` | 固定门前站位 | main -> `walk_to` | 跟随、拦截、出击、解围 | 高 |
| `src/player.py:support()` | 球门侧补防 | main -> `move_to_position` | 接应、左右槽、防守补位 | 中 |
| `src/player.py:take_kickoff()` | 未调用的预备开球 | 当前无上游 | 可作为后续参考，不能直接认定正确 | 中高 |
| `src/player.py:walk_to()` | 通用导航和速度控制 | 所有走位 -> backend | 卡死、路径缓存、规则区域 | **高**；影响全部 Phase |
| `src/utils/geom.py` | 纯几何 | main/player/utils | 评分和目标计算 | 低至中 |
| `src/utils/obstacles.py` | 障碍收集和球门模型 | `walk_to` | 避让、安全距离 | 中高 |
| `src/utils/path_planner.py` | A* | `walk_to` | 所有站位路径 | 高 |

### 16.3 需要谨慎修改的公共模块

| 路径/符号 | 当前作用 | 上游 | 下游/影响范围 | 修改风险 |
|---|---|---|---|---|
| `src/framework/config.py:SoccerConfig` | 运行身份、Topic、频率和新鲜度 | 环境变量、Agent 初始化 | ROS source、runtime、Player、Backend 数量 | 高；改错会导致连接或数据失效 |
| `src/framework/runtime.py:SoccerRuntime` | 控制线程、Context、异常停车 | Agent 生命周期、ContextSource | 所有 Player 和策略 tick | 高；影响全局安全、时序和降级 |
| `src/framework/types.py` 的数据类/枚举 | 全层共享数据契约 | ROS source、game codec | runtime、main、player、utils | 高；字段变化会跨层传播 |
| `src/framework/game_codec.py` | 裁判 JSON 映射 | GameController String | `GameControlState` 和全部 Phase | 高；必须与正式协议精确一致 |

### 16.4 原则上不应因普通策略优化修改

| 路径/符号 | 当前作用 | 上游 | 下游/影响范围 | 不应普通修改的原因 |
|---|---|---|---|---|
| `src/framework/agent.py:SoccerAgentMixin` | Booster 生命周期和对象装配 | Booster Agent 平台 | config、source、runtime、backend | 平台入口边界，错误会使 Agent 无法启动 |
| `src/framework/ros_source.py:RosContextSource` | ROS Topic、QoS、坐标和真值输入 | 仿真器与 GameController | `WorldSnapshot`、全部策略数据 | 数据源/合规边界，应单独验证后再改 |
| `src/framework/robot_backend.py:RobotBackend` | SDK、线程和踢球管理器 | Player 动作 | BoosterRobot/SoccerKickManager | 底层动作边界，错误可能影响全队控制 |
| `src/framework/debugdraw.py` | 调试 Marker 旁路 | runtime/player/main | `/soccer/debug` | 不承载策略，除非处理独立性能问题 |
| `src/framework/log_publisher.py` | ROS 日志旁路 | Python logging | `/soccer/agent_log` | 不承载策略，修改需考虑 handler 生命周期 |
| `agent.toml` | 平台入口、API Level、机型 | Booster Studio 构建 | Agent 装载 | 平台契约文件 |
| `build.toml` | 构建平台和依赖 | 构建器 | 安装环境和目标平台 | 依赖版本可能与平台镜像冲突 |
| `.booster-studio/project.json` | 场景和项目模式 | Booster Studio | football3v3 环境 | 场景契约，不属于比赛策略 |

### 16.5 分析建议：后续候选优化顺序

后续收到具体策略方案后，建议先核对其是否遵守以下顺序，而不是直接叠加复杂战术：

1. 先补规则安全：对方定位球避让、无球安全降级、Set/Stop 抢占、每名 active 每帧有明确命令。
2. 再拆分开球和各类定位球状态机。
3. 建立守门员 HOME/TRACK/INTERCEPT/CLEAR/RECOVER 子状态。
4. 完善 Chaser 成本、角色租约和掉线转移。
5. 建立动作完成、失败、超时和抢占契约。
6. 最后加入传球、射门评分、带球和阵型优化。

以上顺序仅是风险索引，不代表本阶段已经采用或实施任何优化方案。

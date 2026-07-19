# 比赛规则与当前 Demo 完整比赛流程分析

> 生成日期：2026-07-19  
> 分析范围：官方比赛规则、官方优化教程、项目现有解析文档、当前 `src/` Demo 代码。  
> 工作方式：仅静态阅读与文档整理；未修改程序代码，未运行构建、测试、lint、type-check、格式化、仿真或运行时验证。

## 1. 资料与规则依据

### 1.1 资料优先级

本分析按以下优先级判断事实：

1. **官方比赛规则**：`赛场规则(1).pdf`，本文中简称“规则”。规则是比赛状态、重启、处罚、得分和技术合规的最高依据。
2. **当前 Demo 代码**：`src/main.py`、`src/player.py`、`src/framework/*`、`src/param.py`、`src/utils/*`。用于确认当前程序实际如何执行。
3. **官方教程 PDF**：`如何找到最适合的射门队员(1).pdf`、`如何优化机器人移动行为(1).pdf`、`如何为场上选手分配角色(1).pdf`、`配置参数指南(1).pdf`。这些资料说明官方推荐思路或示例架构，但不能替代规则，也不能自动视为当前代码已实现。
4. **项目已有解析文档**：`PROJECT_ANALYSIS.md`、`docs/官方人员建议.md`、`docs/TODO_球状态估计与卡尔曼滤波.md`、Trellis 任务记录。用于核对既有理解是否仍与当前代码一致。

### 1.2 官方规则中的关键状态与术语

规则明确给出主状态流：

```text
initial -> ready -> set -> playing -> finished
```

规则还使用以下关键事件或状态概念：

- `Stop` / `stopped`：出界重启、摆球等过程中机器人须停止。
- `kick-off`：开球；专属球权时间 10 秒。
- `setPlay`：界外球、球门球、角球、任意球、点球等重启；除开球外规则表中重启时限为 45 秒。
- `ball_free`：主罚方完成触球/移动球或定位球时限过期后，球权开放，双方均可触球。
- 处罚状态：非法站位、Set 移动、Stop 移动、提前触球、避让距离不足、倒地过久、长时间无活动、红黄牌级联等。

### 1.3 当前代码中的比赛状态数据模型

当前代码通过 `src/framework/types.py` 定义裁判机相关数据：

- `GamePhase`：`NORMAL`、`PENALTY_SHOOT_OUT`、`EXTRA_TIME`、`TIMEOUT`（`src/framework/types.py:49`）。
- `GameState`：`INITIAL`、`READY`、`SET`、`PLAYING`、`FINISHED`（`src/framework/types.py:56`）。
- `SetPlay`：`NONE`、`DIRECT_FREE_KICK`、`INDIRECT_FREE_KICK`、`PENALTY_KICK`、`THROW_IN`、`GOAL_KICK`、`CORNER_KICK`（`src/framework/types.py:64`）。
- `Penalty`：包含 `ILLEGAL_POSITIONING`、`MOTION_IN_SET`、`LOCAL_GAME_STUCK`、`INCAPABLE_ROBOT`、`PUSHING`、`SENT_OFF` 等（`src/framework/types.py:74`）。
- `GameControlState`：包含 `stopped`、`game_phase`、`state`、`set_play`、`kicking_team`、`secs_remaining`、`secondary_time`、`teams` 等字段（`src/framework/types.py:143` 附近）。

当前数据模型没有单独的 `ball_free` 字段；代码主要用 `set_play == NONE`、`kicking_team` 和 `secondary_time` 间接判断重启窗口是否仍存在。

## 2. 官方比赛规则下的完整比赛流程

### 2.1 比赛开始前准备

规则要求参赛程序只能使用官方公开接口，包括机器人传感器、官方 GameControl/裁判广播、官方 SDK、ROS topic 和 Agent API。禁止连接仿真内部接口、伪造裁判、移动球/机器人、扫描端口、读取非公开文件或干扰对手。

比赛开始前，策略程序应完成自身初始化并等待裁判机状态。规则层面不要求策略程序自己决定开赛，只响应裁判状态。

### 2.2 比赛启动：initial / kickoff ready

规则 F-01、F-02 描述比赛启动后进入开球准备流程，主状态从 `initial` 流转到 `ready`、`set`、`playing`，最终 `finished`。

规则没有要求机器人在 `initial` 中移动；此阶段应视为非比赛动作阶段。

### 2.3 READY 阶段

规则要求：

- 机器人在 READY 阶段可以移动到合法开球站位。
- 全部未罚下机器人连续稳定约 5 秒后可提前进入 SET。
- 若未稳定，最长约 45 秒后仍进入 SET。
- READY 阶段移动不处罚（规则 M-03）。

READY 的核心目标是让机器人完成合法站位，而不是抢球。

### 2.4 SET 阶段

规则要求：

- 裁判机将球放到对应发球点，例如开球时放到中圈。
- 所有机器人保持静止。
- SET 阶段非法站位、移动都可能罚下 30 秒。
- SET 约 5 秒后进入 PLAYING。

开球 SET 站位要求：

- 开球方：本方半场或中圈内。
- 非开球方：本方半场且中圈外。
- 开球方在对方半场中圈内超过 1 人会处罚最远者。

### 2.5 PLAYING 正常比赛阶段

当进入 PLAYING 且不处于 stopped 或定位球独占窗口时，双方可以正常抢球、进攻、防守和射门。

正常比赛中：

- 整球越过球门线、在门柱间、横梁下、门深内，判有效进球。
- 整球越过边线或底线，进入对应出界重启。
- 正常比赛中 30 秒内没有任何机器人触球，触发坠球重启。
- 机器人倒地过久、长时间无活动等也可能触发处罚。

### 2.6 开球与中场重新开球

开球触发场景包括比赛开始、中场/下半场开球、有效进球后失球方开球。

规则要求：

- 开球专属球权窗口为 10 秒。
- 若开球方未在规定时间内完成开球，球权开放，双方均可触球，直接进球有效。
- 开球方在专属窗口内直接进球，必须满足至少 2 次稳定触球或 setplay 已过期，否则不加分并重新开球。
- 进球后由失球方重新开球，状态回 READY。

### 2.7 进球后的状态变化

有效进球后：

1. 得分增加。
2. 发球方切换为失球方。
3. 全体机器人重新进入 READY 流程。
4. READY 稳定或超时后进入 SET。
5. SET 静止后进入 PLAYING，由新的开球方开球。

无效进球包括：

- 球未整体越过门线。
- 不在门柱间或横梁下。
- 开球方在开球专属窗口中未满足两次稳定触球而直接进球。
- 规则中特定定位球乌龙场景不计分，并改判对方角球。

### 2.8 球出界后的重启

规则区分：

- 边线出界：判给最后触球方的对方，重启类型为 `throwIn`。
- 底线出界且非进球：按最后触球方判为 `goalKick` 或 `cornerKick`。
- 球门球：通常判给防守方，摆球约在 `x=±6.0, y=±2.0`。
- 角球：通常判给进攻方，摆球在角旗区向内约 `0.05m`。

重启期间：

- 出界和摆球时机器人须停止。
- 进入 PLAYING 后，主罚方有定位球专属时间。
- 主罚方触球/移动球后，`setPlay` 清空，球进入 `ball_free`。
- 非主罚方在主罚方触球前提前触球，会罚下相关机器人 30 秒并重踢。
- 非主罚方在未开放期间距球小于 1.45m，若无豁免，也会罚下并重踢。

### 2.9 任意球、点球及其他重启

代码枚举支持直接任意球、间接任意球和点球，但提供的规则 PDF 对它们的完整触发、摆球和进球条件描述不足。能确认的是：

- 它们属于定位球/重启范畴。
- 防守方在主罚方触球前不得提前触球。
- 防守方需保持至少 1.45m 避让距离，除非满足规则豁免。
- 主罚方未在时限内完成开球后，球权开放。

需要向赛事方确认直接任意球、间接任意球和点球在本届仿真中的具体裁判字段、摆球位置和直接得分条件。

### 2.10 犯规、处罚及裁判指令

规则中处罚统一罚时 30 秒，主要包括：

- SET 非法站位。
- 开球前非法进入中圈或越半场。
- 定位球避让距离不足。
- 主罚方触球前防守方先触球。
- SET 阶段移动。
- STOP 阶段移动。
- 倒地过久。
- 长时间无活动。
- 警告、黄牌、红牌级联。

策略代码不应自行解除处罚，只应读取裁判机处罚字段并停止受罚机器人。

### 2.11 暂停、继续和比赛结束

- `playing + stopped` 或摆球过程：机器人必须停止。
- stopped 结束后，裁判机会重新给出对应状态；策略继续按新的 `state`、`setPlay`、`kickingTeam`、`secondaryTime` 判断。
- 时间到或手动结束后进入 `finished`，比赛结束。

## 3. 当前代码的整体架构

### 3.1 总体分层

当前 Demo 是集中式 Python 策略，主要分层如下：

```text
Booster Agent 平台
  -> src/main.py: SoccerSimAgent
  -> src/framework/agent.py: SoccerAgentMixin 生命周期与配置
  -> src/framework/runtime.py: 30Hz 控制循环与 Context 构造
  -> src/framework/ros_source.py: ROS 真值与裁判机输入
  -> src/framework/game_codec.py: 裁判 JSON 解码
  -> src/main.py: Phase 状态机与整队策略分派
  -> src/player.py: 单机器人动作封装
  -> src/framework/robot_backend.py: BoosterRobot / 踢球接口封装
```

当前代码不是官方教程中描述的 Playbook + 行为树 + 黑板架构。没有找到 `AssignRoles` 行为树节点、`RoleAssignment` 黑板写入、`ChaserRole.build_subtree()` 等当前生效代码。当前实际角色分配发生在 `src/main.py` 中，由 `_act_*` 函数直接调用各 `Player` 动作。

### 3.2 程序入口与生命周期

- 入口类：`src/main.py:110` 的 `SoccerSimAgent(SoccerAgentMixin, AgentBase)`。
- 配置和 Runtime 初始化：`src/framework/agent.py:112` 的 `SoccerAgentMixin.__init__()`。
- Agent 激活：`src/framework/agent.py:158` 的 `on_agent_activated()` 调用 `runtime.start()`。
- Agent 关闭：`src/framework/agent.py:162` 的 `on_agent_close()` 调用 `runtime.stop()`。
- Runtime 创建玩家：`src/framework/runtime.py:47` 的 `SoccerRuntime.__init__()` 根据 `config.player_ids` 创建 `Player` 实例。
- Backend 注入：`src/framework/agent.py:130` `_create_backends()` 为每个 player 注入 `RobotBackend`。

### 3.3 通信、定位和比赛信息输入

ROS 数据源为 `src/framework/ros_source.py:45` 的 `RosContextSource`。

订阅入口：`src/framework/ros_source.py:117` `_create_subscriptions()`：

- 队友位姿：`/team{id}/{robot_name}/soccer/sim/ground_truth/robot_pose`。
- 对手位姿：对手机器人名对应同类 topic。
- 球位置：`/team{id}/soccer/sim/ground_truth/ball`。
- 裁判机：默认 `/soccer/game_controller`。

回调：

- `_make_pose_cb()`：`src/framework/ros_source.py:154`，更新 `RobotState`。
- `_ball_cb()`：`src/framework/ros_source.py:165`，更新 `BallState`。
- `_game_cb()`：`src/framework/ros_source.py:175`，调用 `game_control_state_from_json()` 解码裁判 JSON。

裁判 JSON 解码：

- `src/framework/game_codec.py:33` `game_control_state_from_json()`。
- `src/framework/game_codec.py:43` `game_control_state_from_dict()`，读取 `stopped`、`gamePhase`、`state`、`setPlay`、`kickingTeam`、`secsRemaining`、`secondaryTime`、`teams`。
- `src/framework/game_codec.py:65` `team_state_from_dict()`，读取比分、守门员、球员列表等。
- `src/framework/game_codec.py:84` `player_state_from_dict()`，读取 penalty、剩余罚时、warnings、cautions。

### 3.4 配置和场地尺寸

- `src/framework/config.py:24` `SoccerConfig` 读取队伍 ID、机器人名、对手名、控制频率和裁判 topic。
- `src/framework/config.py:49` `SoccerConfig.from_env()` 从环境变量读取：`SOCCER_TEAM_ID`、`SOCCER_ROBOT_NAMES`、`SOCCER_OPPONENT_ROBOT_NAMES`、`SOCCER_CONTROL_HZ`、`SOCCER_GAME_CONTROLLER_TOPIC`。
- `src/framework/types.py:126` `ADULT_FIELD_DIMENSIONS` 定义场地长 14.0m、宽 9.0m、球门宽 2.6m、中圈半径 1.5m、点球区 3.0m × 6.0m、球门区 1.0m × 4.0m。
- `src/param.py` 集中存放当前策略常量，例如 `KICK_ENTER_M=2.0`、`KICK_EXIT_M=2.5`、`ATTACKER_KEEP_DIST_MARGIN_M=0.3`、`KICK_POWER_OUR_KICKOFF=5.0`。

## 4. 当前代码的主运行循环

### 4.1 Runtime 循环

主循环位于 `src/framework/runtime.py`：

1. `SoccerRuntime.start()`（`src/framework/runtime.py:80`）启动 ROS source，调用一次 `agent.init_store(store)`，创建后台线程。
2. `_loop()`（`src/framework/runtime.py:125`）按 `control_hz` 周期循环，默认 30Hz。
3. `_tick(now)`（`src/framework/runtime.py:143`）每帧：
   - 调用 `_build_context()` 构造只读 `Context`。
   - 将同一个 `Context` 写入所有 `Player.context`。
   - 开始 debugdraw 帧。
   - 调用 `self._agent.play(ctx, self._players, self._store)`。
   - flush debugdraw。
4. `_build_context()`（`src/framework/runtime.py:252`）从 `WorldSnapshot` 读取裁判、球、队友、对手信息，并通过 `_fresh_game()`、`_fresh_ball()`、`_fresh_robot()` 过滤过期数据。

### 4.2 Strategy 每帧分派

每帧策略入口为 `src/main.py:128` 的 `SoccerSimAgent.play(context, players, store)`。

流程：

1. 调用 `get_phase(context)` 得到当前 `Phase`。
2. 更新 `store.prev_phase` / `store.cur_phase`。
3. 绘制 debug 信息。
4. 对每个 `Player` 调用 `ensure_ready()`：
   - 倒地：调用 `get_up()`，本帧不参与行动。
   - 非 `walk` 模式：调用 `request_mode("walk")`，本帧不参与行动。
   - 受罚：设置 `action="penalized"` 并 `stop()`。
   - 无自身 pose：设置 `action="no_pose"` 并 `stop()`。
   - 其余加入 active 列表。
5. 按 `Phase` 调用对应整队动作函数：
   - `NORMAL` -> `_act_normal()`。
   - `OUR_KICKOFF` -> `_act_our_kickoff()`。
   - `OPP_KICKOFF` -> `_act_opp_kickoff()`。
   - `OUR_SET_PLAY` -> `_act_our_set_play()`。
   - `OPP_SET_PLAY` -> `_act_opp_set_play()`。
   - `READY` -> `_act_ready()`。
   - `STOPPED` -> 所有 active 球员 `stop()`。

### 4.3 Phase 判断入口

Phase 定义在 `src/main.py:37`，判断函数为 `src/main.py:48` `get_phase(context)`。

当前映射：

```text
context.game is None -> STOPPED
state == READY -> READY
state == PLAYING and not stopped:
  set_play != NONE and kicking_team != 255 -> OUR_SET_PLAY / OPP_SET_PLAY
  secondary_time > 0 and kicking_team != 255 -> OUR_KICKOFF / OPP_KICKOFF
  otherwise -> NORMAL
all other states -> STOPPED
```

注意：`get_phase()` 不直接使用 `game_phase`、`secs_remaining`、比分变化、`first_half` 或 warnings/cautions。

## 5. 完整比赛状态流转过程：规则与当前代码结合

### 5.1 程序启动到等待裁判状态

1. Booster 平台加载 `SoccerSimAgent`。
2. `SoccerAgentMixin.__init__()` 读取 `SoccerConfig.from_env()`，创建 `RosContextSource`、`SoccerRuntime` 和 `RobotBackend`。
3. `on_agent_activated()` 启动 Runtime。
4. Runtime 开始 30Hz tick。
5. ROS source 持续更新 `WorldSnapshot`。
6. 如果裁判机数据不存在或超过 `game_state_max_age_sec`，`Context.game=None`，`get_phase()` 返回 `STOPPED`，active 球员停车。

### 5.2 INITIAL

规则：比赛尚未进入就绪或开球准备，机器人不应抢球。

代码：`GameState.INITIAL` 未单独分支，落入 `STOPPED`，active 球员执行 `stop()`。

实现分类：**明确实现为停车**。

### 5.3 READY

规则：机器人合法走位；可提前稳定进入 SET 或超时进入 SET。

代码：`get_phase()` 将 `GameState.READY` 映射为 `Phase.READY`，`_act_ready()` 根据 `kicking_team == context.team_id` 区分我方开球准备或对方开球准备。

我方开球 ready 位置：

- 第 1 个 active player：`(-center_circle_radius, 0)`。
- 第 2 个：己方球门区中心附近 `(-field.length/2 + field.goal_area_length, 0)`。
- 第 3 个：`(-0.5, center_circle_radius + 2)`。

对方开球 ready 位置：

- 第 1 个：`(-center_circle_radius - 0.5, 0)`。
- 第 2 个：己方球门区中心。
- 第 3 个：己方禁区线中心。

执行动作为 `Player.walk_to(... avoid_ball=True, avoid_robots=True)`。

实现分类：**部分明确实现**。代码能走固定 ready 位，但没有检查“所有机器人是否稳定”、没有显式判断站位是否完全满足规则，也没有专门处理坠球 READY 的双方中圈外要求。

### 5.4 SET / stopped / 摆球

规则：机器人必须静止；移动或非法站位会处罚。

代码：`GameState.SET`、`g.stopped == True`、`GameState.INITIAL`、`GameState.FINISHED` 均映射为 `STOPPED`。`SoccerSimAgent.play()` 对 active 球员调用 `stop()`。

实现分类：**明确实现为停车**。但是否完全避免惯性移动导致规则 M-01/M-02 处罚，需要运行环境验证。

### 5.5 PLAYING + 开球窗口

规则：开球方有 10 秒专属窗口；开球方直接进球需至少两次稳定触球或窗口已过期。

代码：如果 `state == PLAYING`、`set_play == NONE`、`secondary_time > 0`、`kicking_team` 有效，则进入 `OUR_KICKOFF` 或 `OPP_KICKOFF`。

我方开球 `_act_our_kickoff()`：

- 第一次进入该 phase 或原主罚不 active 时，用 `_select_closest_attacker()` 选择最近球员并锁定到 `store.kickoff_taker`。
- 主罚球员每帧直接 `kick(0.1, KICK_POWER_OUR_KICKOFF)`。
- 其余球员中离己方门最近者 `guard()`，剩余 `stop()`。

对方开球 `_act_opp_kickoff()`：

- 离己方门最近者 `guard()`。
- 其他球员移动到中圈外固定点 `(-r - 0.5, 0)`、`(-r - 2.0, 0.5)`，并避球/避机器人。

实现分类：

- 我方开球动作：**明确实现，但规则完整性不足**。没有两次触球保护、没有触球完成状态、没有等球权开放判断。
- 对方开球避让：**明确实现部分避让**。固定点通常在中圈外，但没有基于球实时保持 1.45m，也没有全路径避让硬约束。
- `Player.take_kickoff()`（`src/player.py:897`）存在但当前 `_act_our_kickoff()` 没调用，不能视为当前生效流程。

### 5.6 PLAYING + 普通比赛

规则：双方正常抢球、进攻、防守；出界、进球、犯规、僵局等由裁判机切状态。

代码：`Phase.NORMAL` 调用 `_act_normal()`。

普通流程：

1. `_update_ball_recovery_state()` 要求球连续可见 `BALL_REACQUIRE_FRAMES=3` 帧后才恢复正常策略。
2. 如果球不可见且允许搜索，则 `_act_ball_recovery()` 选择一人搜索，其他人守位或停住。
3. 球确认后，`_select_closest_attacker()` 选择距球最近者为 attacker，并用 `ATTACKER_KEEP_DIST_MARGIN_M=0.3` 保留上一 attacker，避免频繁切换。
4. attacker 执行 `Player.attack()`。
5. 其余球员中离己方球门最近者执行 `guard()`。
6. 剩余球员执行 `support()`。

`Player.attack()`（`src/player.py:757`）：

- 球或自身 pose 不存在时停车。
- 默认目标为对方球门中心。
- 根据距球距离 `KICK_ENTER_M=2.0` / `KICK_EXIT_M=2.5` 和绕球对齐角度进入/退出踢球态。
- 踢球态下调用 `plan_kick()` -> `kick()`。
- 非踢球态下走到球后方或绕球接近点。

`Player.guard()`（`src/player.py:838`）：走到己方球门区中心，面向球，避球和避机器人。

`Player.support()`（`src/player.py:865`）：站在“球 -> 己方球门中心”连线上距球最多 `SUPPORT_DIST_M=3.0` 的位置。

实现分类：**明确实现普通攻防基础流程**。但没有球权估计、传球、射门通道判断、守门员主动出击、出界检测、比分/剩余时间策略、僵局本地判断。

### 5.7 PLAYING + 我方定位球

规则：主罚方在专属窗口内应完成开球；完成后 `setPlay` 清空，球权开放。

代码：`_act_our_set_play()` 读取 `get_set_play_type()`，对 `THROW_IN`、`CORNER_KICK`、`GOAL_KICK` 有分支，但所有分支都调用 `_act_normal(... allow_ball_search=False)`。直接任意球、间接任意球、点球也落入同样 fallback。

实现分类：**识别入口明确，专项逻辑未实现**。我方定位球实际按普通比赛抢球/射门处理，没有主罚、接应、避让、完成/超时状态。

### 5.8 PLAYING + 对方定位球

规则：我方作为防守方，在主罚方触球前不得触球，且需距球至少 1.45m，除非满足豁免。

代码：`_act_opp_set_play()` 直接调用 `_act_normal(... allow_ball_search=False)`。

实现分类：**规则关键要求未找到明确实现，且当前逻辑可能冲突**。由于 `_act_normal()` 会选择 attacker 并 `attack()`，对方定位球未开放时当前代码可能主动接近并触球，存在提前触球/避让距离违规风险。

### 5.9 进球后重启

规则：有效进球后比分增加，由失球方重新开球，状态回 READY。

代码：`TeamState.score` 已在 `game_codec.py` 解码，但 `src/main.py` 没有比分变化监听或进球事件处理。当前程序依赖裁判机把 `state`、`kicking_team`、`secondary_time` 改为新的 READY/开球流程。

实现分类：**通过裁判状态间接实现**。代码不自行判断进球，也不处理无效进球或开球直接进球限制。

### 5.10 出界和摆球

规则：整球出边线/底线后进入停止、摆球和对应重启。

代码：没有找到基于球坐标判断出界的逻辑。只有当裁判机发出 `stopped`、`READY`、`SET`、`SetPlay.THROW_IN/GOAL_KICK/CORNER_KICK` 时才进入对应顶层 phase。

实现分类：**通过裁判状态间接实现**。几何出界判定不在策略代码中实现。

### 5.11 坠球 / 比赛僵局

规则：正常比赛 30 秒无机器人触球，触发坠球重启，流程为 READY -> SET -> PLAYING，SET 时球放中点，双方不能进圈，PLAYING 后双方可抢球。

代码：没有找到 “30 秒无触球” 本地计时，也没有坠球专用 setPlay 或 ready 站位。若裁判机用普通 READY/SET/PLAYING 表示坠球，当前代码会按开球方或普通 ready 处理。

实现分类：**未找到明确实现**。是否可间接处理取决于裁判字段编码。

### 5.12 处罚期间和恢复

规则：处罚由裁判控制，标准罚时 30 秒，提前返场有额外警告。

代码：`Player.penalty` 从 `Context.game.get_player_state()` 读取；`Player.is_penalized` 判断 `Penalty != NONE`；`SoccerSimAgent.play()` 中受罚机器人 `stop()` 且不加入 active。

实现分类：**明确实现基础停机和排除候选**。但未使用 `secs_till_unpenalised`、warnings、cautions、红牌永久离场语义，也没有按具体犯规类型调整策略。

## 6. 规则状态与代码模块对应表

| 规则状态/事件 | 代码判断入口 | 主要文件/类/函数 | 执行逻辑 | 结束/切换方式 | 实现分类 |
|---|---|---|---|---|---|
| 无裁判数据/裁判数据失鲜 | `get_phase(): g is None` | `src/main.py:48`; `runtime._fresh_game()` | 映射 `STOPPED`，active 球员停车 | 裁判数据恢复 | 明确实现 |
| INITIAL | `state` 非 READY/PLAYING | `src/main.py:get_phase()` | `STOPPED` 停车 | 裁判进入 READY | 明确实现 |
| READY | `state == READY` | `src/main.py:_act_ready()` | 固定 ready 位走位，避球/避机器人 | 裁判进入 SET | 部分明确实现 |
| SET | fallback 到 STOPPED | `src/main.py:get_phase()` / `play()` | 停车 | 裁判进入 PLAYING | 明确实现 |
| PLAYING + stopped | `not g.stopped` 条件失败 | `src/main.py:get_phase()` | `STOPPED` 停车 | stopped 解除 | 明确实现 |
| 正常 PLAYING | `PLAYING and set_play NONE and secondary_time <= 0` | `_act_normal()` | attacker/guard/support 分配并动作 | 裁判状态变化或球丢失 | 明确实现 |
| 我方开球 | `secondary_time > 0 and kicking_team == team_id` | `_act_our_kickoff()` | 锁最近主罚者直接 kick；其余 guard/stop | 裁判清 secondary_time 或切状态 | 明确实现但规则不完整 |
| 对方开球 | `secondary_time > 0 and kicking_team != team_id` | `_act_opp_kickoff()` | 一人 guard，其余中圈外固定点 | 裁判清 secondary_time 或切状态 | 部分明确实现 |
| 我方界外球 | `set_play == THROW_IN` + 我方 | `_act_our_set_play()` | 调 `_act_normal()` | 裁判清 setPlay | 入口明确，专项未实现 |
| 我方角球 | `set_play == CORNER_KICK` + 我方 | `_act_our_set_play()` | 调 `_act_normal()` | 裁判清 setPlay | 入口明确，专项未实现 |
| 我方球门球 | `set_play == GOAL_KICK` + 我方 | `_act_our_set_play()` | 调 `_act_normal()` | 裁判清 setPlay | 入口明确，专项未实现 |
| 我方任意球/点球 | 其他 `set_play != NONE` + 我方 | `_act_our_set_play()` fallback | 调 `_act_normal()` | 裁判清 setPlay | 入口明确，专项未实现 |
| 对方任意定位球 | `set_play != NONE` + 对方 | `_act_opp_set_play()` | 调 `_act_normal()`，可能主动追球 | 裁判清 setPlay | 未满足规则避让要求 |
| 进球 | 无本地入口 | `TeamState.score` 解码；策略未读 | 依赖裁判切 READY/开球 | 裁判状态变化 | 间接实现 |
| 出界 | 无本地几何判断 | `SetPlay` 解码；策略读 setPlay | 依赖裁判发 stopped/setPlay | 裁判状态变化 | 间接实现 |
| 坠球 | 未找到专用入口 | 无 | 无专用站位或抢球规则 | 取决于裁判字段 | 未找到明确实现 |
| 受罚 | `p.is_penalized` | `src/player.py:178`; `main.py:145` 附近 | 停车并排除 active | penalty 变 NONE | 明确实现基础逻辑 |
| FINISHED | fallback STOPPED | `src/main.py:get_phase()` | 停车 | 比赛结束 | 明确实现 |
| TIMEOUT / 加时 / 点球大战 phase | 未使用 `game_phase` | `GamePhase` 已解码 | 无专项逻辑 | 取决于 `state/stopped` | 未找到明确实现 |

## 7. 各类比赛重启和特殊情况的处理流程

### 7.1 我方开球

当前流程：

```text
裁判: PLAYING + secondary_time > 0 + kicking_team=我方
  -> get_phase() = OUR_KICKOFF
  -> _act_our_kickoff()
  -> 选择/锁定最近 active player 为 kickoff_taker
  -> kickoff_taker.kick(0.1, KICK_POWER_OUR_KICKOFF)
  -> 一名 guard，其余 stop
  -> 裁判清 secondary_time 后转 NORMAL 或其他 phase
```

差异：没有规则要求的“两次稳定触球或等待过期后直接进球”保护；没有检测球是否离开中点；没有对开球完成建立状态机。

### 7.2 对方开球

当前流程：

```text
裁判: PLAYING + secondary_time > 0 + kicking_team=对方
  -> get_phase() = OPP_KICKOFF
  -> _act_opp_kickoff()
  -> 离己方门最近者 guard()
  -> 其他人走到中圈外固定点
  -> 裁判清 secondary_time 后转 NORMAL
```

差异：没有基于球位置的 1.45m 实时避让约束；移动路径是否穿越中圈或球附近不受顶层硬约束。

### 7.3 我方界外球/球门球/角球

当前流程：

```text
裁判: PLAYING + set_play in THROW_IN/GOAL_KICK/CORNER_KICK + kicking_team=我方
  -> get_phase() = OUR_SET_PLAY
  -> _act_our_set_play()
  -> _act_normal(allow_ball_search=False)
  -> 最近球员 attack()，其余 guard/support
```

差异：没有固定主罚者、没有接应站位、没有根据重启类型区分踢法、没有主罚完成状态。

### 7.4 对方界外球/球门球/角球/任意球/点球

当前流程：

```text
裁判: PLAYING + set_play != NONE + kicking_team=对方
  -> get_phase() = OPP_SET_PLAY
  -> _act_opp_set_play()
  -> _act_normal(allow_ball_search=False)
  -> 最近球员 attack()
```

差异：这是当前最明显的规则风险。规则要求对方主罚前我方不得触球并保持 1.45m 避让；当前代码会让最近球员按普通比赛追球。

### 7.5 进球后

当前流程完全依赖裁判机：

```text
裁判判进球并更新 score / kicking_team / state
  -> 若进入 READY: _act_ready()
  -> 若进入 SET/stopped: STOPPED
  -> 若进入 PLAYING 开球窗口: OUR_KICKOFF 或 OPP_KICKOFF
```

代码没有 score change detector，也没有主动判断进球几何。

### 7.6 球不可见/数据失鲜

这不是规则中的比赛重启，但影响实际比赛流程。

- 球数据失鲜：`runtime._fresh_ball()` 将 `Context.ball=None`。
- NORMAL 且允许搜索：`_act_ball_recovery()` 一人搜索，其余 guard/hold。
- 重启 phase 中 `allow_ball_search=False` 时，若球为空，active 球员 `ball_unknown:stop`。
- GameController 失鲜：`get_phase()` 返回 STOPPED，全员停车。

### 7.7 机器人倒地、非 walk、受罚、无位姿

每帧在 phase 分派前处理：

- 倒地：`Player.ensure_ready()` 调用 `get_up()`，不加入 active。
- 非 walk：调用 `request_mode("walk")`，不加入 active。
- 受罚：`stop()`，不加入 active。
- 自身 pose 未知：`stop()`，不加入 active。

这使角色分配只在本帧 active 球员之间进行，能实现少人降级的基础效果。

## 8. 现有代码解析的核对结果

### 8.1 `PROJECT_ANALYSIS.md`

总体评价：**大部分仍准确，且比当前任务要求更宽泛**。

准确之处：

- 正确区分官方规则、官方建议和 Demo 实际代码。
- 正确指出主状态流 `INITIAL -> READY -> SET -> PLAYING -> FINISHED`。
- 正确定位 `src/main.py:get_phase()`、`_act_normal()`、`_act_our_kickoff()`、`_act_our_set_play()`、`_act_opp_set_play()` 等关键入口。
- 正确指出对方定位球当前会走普通逻辑，存在提前触球/避让距离违规风险。
- 正确指出进球和出界依赖裁判机状态，代码没有本地几何判定。
- 正确指出官方教程的 Playbook/行为树结构与当前 Demo 不一致。

需要注意或更新之处：

- `PROJECT_ANALYSIS.md` 资料路径写的是 `o:\Robot\赛场规则.pdf` 等旧路径；本次用户提供的是微信文件目录下带 `(1)` 的 PDF。内容看起来一致或高度相近，但引用路径应更新为本次资料路径。
- `PROJECT_ANALYSIS.md` 覆盖了大量优化建议和后续修改索引，本次任务只要求确认比赛规则和完整比赛流程；后续不应直接把其中优化建议当成本阶段结论。
- 该文档部分“需要运行验证”项仍有效，例如 ground-truth topic 是否在赛事白名单、裁判字段时序、SDK 动作完成语义。

### 8.2 `docs/官方人员建议.md`

总体评价：**这是优化建议文档，不是规则解析，也不是当前代码流程说明**。

准确用途：

- 可作为后续进攻、防守、守门员主动性、角色分工优化的参考。

不足：

- 没有覆盖完整比赛状态流。
- 没有建立规则状态与代码入口对应关系。
- 不能用于判断当前 Demo 已实现某项建议。

### 8.3 `docs/TODO_球状态估计与卡尔曼滤波.md`

总体评价：**描述了球状态估计的未来扩展边界，与当前比赛状态流程不是同一层问题**。

准确之处：

- 正确指出当前仿真输入是场地坐标系中的球位置真值，`BallState.confidence=1.0`。
- 正确建议不要在没有需求时引入滤波，避免裁判摆球/重置被误判。

不足：

- 不覆盖裁判状态、重启、处罚、开球、出界等比赛流程。

### 8.4 官方教程 PDF 与当前 Demo 的不一致

官方教程中多次提到：

- `DefaultPlaybook.select_chaser()`。
- `AssignRoles.update()`。
- 行为树黑板 `/team/roles`。
- `ChaserRole`、`SupporterRole`、`GoalkeeperRole`。
- `MotionController.move_to_target()`、`SoccerStrategyTuning` 等结构。

当前代码中没有找到这些作为生效架构的文件或类。当前 Demo 使用 `src/main.py` 中的集中式 `Phase + _act_*` 分派和 `src/player.py` 中的 `Player` 方法。因此：

- 教程说明可作为官方推荐或旧版/另一个 Demo 架构参考。
- 不能据此认为当前项目已有行为树、黑板、Playbook 或教程中的 Chaser 评分实现。

## 9. 已确认实现、间接实现、未找到实现的内容

### 9.1 已确认明确实现

- 裁判 JSON 接收和解码：`RosContextSource._game_cb()` -> `game_control_state_from_json()`。
- 数据新鲜度过滤：`SoccerRuntime._fresh_game()`、`_fresh_ball()`、`_fresh_robot()`。
- 主循环：`SoccerRuntime._loop()` / `_tick()`，默认 30Hz。
- 顶层 phase 状态机：`src/main.py:get_phase()`。
- `INITIAL` / `SET` / `FINISHED` / stopped / 无裁判数据停车。
- READY 固定站位。
- PLAYING 普通攻防：attacker、guard、support。
- 我方开球基础动作：锁定最近球员直接 kick。
- 对方开球基础站位：一人守门，其余到中圈外固定点。
- 受罚机器人停车并排除 active 候选。
- 倒地起身、非 walk 切 walk、无 pose 停车。
- 球丢失时 NORMAL 搜索/守位逻辑。
- 动作输出路径：`Player.set_velocity()` / `Player.kick()` -> `RobotBackend` -> BoosterOS SDK。

### 9.2 通过裁判状态间接实现

- 进球后重新开球：依赖裁判机更新 score、state、kickingTeam、secondaryTime。
- 出界后重启：依赖裁判机发送 stopped、READY/SET/PLAYING、setPlay。
- 定位球完成和球权开放：依赖裁判机清空 `setPlay` 或 `secondaryTime`。
- 比赛结束：依赖裁判机进入 `FINISHED`。
- 处罚结束：依赖裁判机把 `Penalty` 改回 `NONE`。

### 9.3 当前代码未找到明确实现

- 本地进球几何判断。
- 本地出界几何判断。
- 开球两次稳定触球保护。
- 开球直接进球无效处理。
- 我方界外球、球门球、角球、任意球、点球的专项主罚/接应/完成状态机。
- 对方定位球 1.45m 避让和禁止提前触球的安全逻辑。
- 坠球专用处理。
- 比赛 30 秒无触球本地僵局检测。
- 基于 `game_phase` 的 TIMEOUT、加时、点球大战专项处理。
- 基于比分、剩余时间、半场的策略调整。
- warnings、cautions、`secs_till_unpenalised`、红牌永久离场语义的策略使用。
- 官方教程中的 Playbook、行为树、黑板、RoleAssignment 当前生效实现。
- 传球、射门通道评分、带球候选评分、守门员主动出击、球速预测等官方优化教程中的完整策略。

## 10. 存在歧义或需要确认的问题

1. **裁判字段中的 `ball_free` 如何编码？** 规则使用 `ball_free` 概念，但当前 `GameControlState` 没有该字段。需要确认裁判机是否通过 `setPlay=NONE`、`kickingTeam=255`、`secondaryTime=0` 表示。
2. **`secondary_time` 在开球和其他定位球中的真实语义是什么？** 当前代码用 `secondary_time > 0` 判断开球窗口，但 setPlay 时优先走定位球分支。需要确认裁判机对开球、定位球倒计时和过期的具体字段组合。
3. **坠球在裁判机中如何表示？** 规则有坠球，但当前枚举没有 DROP_BALL。可能由 READY/SET/PLAYING + kickingTeam none 表示，需要确认。
4. **直接任意球、间接任意球、点球在本届规则中的完整细节是什么？** 提供的 PDF 提取文本没有完整摆球、站位和得分规则。
5. **开球“两次稳定触球”的判定是否完全由裁判机负责？** 如果裁判机会自动判无效进球，策略只需避免风险；如果策略必须主动防止，需要新增状态逻辑。当前阶段未做修改。
6. **对方定位球避让是否需要路径全程保持 1.45m，还是只要求触球瞬间和最终站位？** 规则描述的是主罚方触球瞬间和未开放期间防守方距球，但实际裁判实现可能连续检测。
7. **ground-truth 位姿和球位置 topic 是否在本届比赛官方允许接口内？** 规则允许官方明确允许的 ROS topic，但当前项目使用 `/soccer/sim/ground_truth/*`，需要赛事白名单确认。
8. **一个 Agent 进程集中控制三名机器人是否符合比赛部署要求？** 当前代码这样实现，需以赛事平台实际要求为准。
9. **守门员门线避让豁免如何绑定正式守门员？** 规则提到门线防守豁免，但当前代码没有使用 GameController 的 `goalkeeper` 字段决定正式守门员。
10. **当前代码对对方定位球的普通追球逻辑是否必须作为后续最高优先级修复？** 从规则看风险很高，但本阶段只分析，不修改。

## 11. 本文确认的完整比赛流程摘要

从当前代码角度，一场比赛实际运行可概括为：

```text
Agent 加载
  -> SoccerAgentMixin 初始化配置、ROS 数据源、Runtime、Player、Backend
  -> on_agent_activated 启动 Runtime
  -> Runtime 30Hz tick
  -> RosContextSource 提供球/机器人/裁判快照
  -> Runtime 构造 Context 并做新鲜度过滤
  -> SoccerSimAgent.play()
  -> get_phase() 把裁判状态压缩为 READY/STOPPED/NORMAL/开球/定位球
  -> 每个 Player ensure_ready / penalty / pose 过滤
  -> _act_ready / _act_normal / _act_our_kickoff / _act_opp_kickoff / _act_our_set_play / _act_opp_set_play / STOPPED
  -> Player.attack / guard / support / walk_to / kick / stop
  -> RobotBackend.set_velocity / kick / release_kick
  -> 下一帧重新读取裁判和世界状态
```

规则完整流程比当前代码更丰富。当前 Demo 能响应主要裁判状态并完成基础攻防，但大量重启规则、触球限制、避让限制和特殊状态依赖裁判机间接处理，或尚未在策略层明确实现。

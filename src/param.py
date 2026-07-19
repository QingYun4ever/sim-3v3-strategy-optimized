"""集中调参入口。

这里放策略、走位、踢球和避障的可调参数。数值迁移自当前代码文件,
以后优先在本文件调整参数。
"""

from __future__ import annotations

import math


# ======================================================================
# 踢球力度
# ======================================================================

# Player.kick() 会把力度夹到这个范围内。如果超过范围，因为控制接口不支持，可能会摔倒。
KICK_POWER_MIN = 1.0
KICK_POWER_MAX = 10.0

# 普通比赛踢球力度。
KICK_POWER_DEFAULT = 5.0
KICK_POWER_BACKFIELD = 5.0
KICK_POWER_OUR_KICKOFF = 5.0


# ======================================================================
# Player 走位控制
# ======================================================================

ARRIVE_DIST = 0.15             # 到达目标点阈值 (m)
MAX_LINEAR = 2.0               # 指令前进速度上限 (m/s)
MAX_ANGULAR = 2.0              # 指令转向速度上限 (rad/s)
LINEAR_GAIN = 1.5              # 平移 P 增益
ANGULAR_GAIN = 2.0             # 转向 P 增益

# 近距全向 / 远距弧线行走使用不同的进入、退出阈值,避免在 1m 附近来回切换。
OMNI_ENTER_DIST = 0.90
OMNI_EXIT_DIST = 1.10

# 远距移动优先边走边转;角差足够大时才进入原地转向,回到较小角差后恢复前进。
TURN_IN_PLACE_ENTER = 0.85
TURN_IN_PLACE_EXIT = 0.55

# 距离到达阈值进入该范围后逐步降低平移速度。
ARRIVAL_BRAKE_DISTANCE_M = 0.35

# 沿规划方向的障碍净空越小,平移速度越低。小于 STOP 时只允许转向。
CLEARANCE_SPEED_STOP_M = 0.05
CLEARANCE_SPEED_FULL_M = 0.65


# ======================================================================
# Player 踢球 / 射门规划
# ======================================================================

KICK_ENTER_M = 2.0             # 距球小于该值进入踢球状态
KICK_EXIT_M = 2.5              # 踢球中距球大于该值才退出踢球状态
CHASE_BEHIND_M = 0.35          # 追球时站到球后方的距离

# 机器人位于球的错误一侧时,先沿球周围的圆弧绕到射门线后方。
CHASE_CIRCLE_RADIUS_M = 0.65
CHASE_CIRCLE_STEP_RAD = math.radians(35.0)
CHASE_DIRECT_ENTER_ANGLE_RAD = math.radians(25.0)
CHASE_DIRECT_EXIT_ANGLE_RAD = math.radians(40.0)
CHASE_CIRCLE_PROGRESS_RAD = math.radians(5.0)
CHASE_CIRCLE_TIMEOUT_SEC = 3.0
CHASE_CIRCLE_FALLBACK_SEC = 1.0


# ======================================================================
# Player 技术动作参数：守门 / 支援 
# ======================================================================

GUARD_FACE_BALL = True
GUARD_THREAT_ENTER_X = -1.0
GUARD_THREAT_EXIT_X = -0.7

# 守门员动态门前站位。X 为己方门线向场内的距离，Y 始终夹在门柱内侧。
GOALKEEPER_HOME_X_OFFSET_M = 0.55
GOALKEEPER_BLOCK_X_OFFSET_M = 0.45
GOALKEEPER_TRACK_GAIN_FAR = 0.18
GOALKEEPER_TRACK_GAIN_NEAR = 0.55
GOALKEEPER_TRACK_NEAR_DISTANCE_M = 4.0
GOALKEEPER_MAX_LATERAL_M = 1.0
GOALKEEPER_POST_MARGIN_M = 0.25
GOALKEEPER_TRACK_ARRIVE_M = 0.12

# 独立有限差分球速估计；异常采样只重播种，不用于威胁判断。
GOALKEEPER_BALL_SAMPLE_MIN_SEC = 0.04
GOALKEEPER_BALL_SAMPLE_MAX_SEC = 0.80
GOALKEEPER_BALL_SAMPLE_MAX_JUMP_M = 2.5
GOALKEEPER_BALL_MAX_CREDIBLE_SPEED_MPS = 12.0
GOALKEEPER_BALL_VELOCITY_MAX_AGE_SEC = 0.35

# 快速射门与无可靠速度时的位置威胁。
GOALKEEPER_FAST_BALL_SPEED_MPS = 2.0
GOALKEEPER_GOALWARD_VX_MPS = 0.8
GOALKEEPER_SLOW_BALL_SPEED_MPS = 0.9
GOALKEEPER_SHOT_PROJECTION_MARGIN_M = 0.35
GOALKEEPER_POSITION_THREAT_X_M = -3.5
GOALKEEPER_POSITION_THREAT_LATERAL_M = 2.0
GOALKEEPER_POSITION_THREAT_OPPONENT_DISTANCE_M = 1.5

# 慢球安全出击。进入和退出距离不同，避免在边界反复切换。
GOALKEEPER_CHALLENGE_ENTER_DISTANCE_M = 2.2
GOALKEEPER_CHALLENGE_EXIT_DISTANCE_M = 2.8
GOALKEEPER_CHALLENGE_ADVANTAGE_M = 0.5
GOALKEEPER_CHALLENGE_MAX_X_M = -3.4
GOALKEEPER_CHALLENGE_MAX_LATERAL_M = 2.6
GOALKEEPER_CHALLENGE_TIMEOUT_SEC = 3.0

# 门前解围和状态迟滞。解围目标始终位于球的正 X 前方。
GOALKEEPER_CLEAR_ENTER_DISTANCE_M = 0.65
GOALKEEPER_CLEAR_EXIT_DISTANCE_M = 1.10
GOALKEEPER_CLEAR_MIN_HOLD_SEC = 0.45
GOALKEEPER_CLEAR_TIMEOUT_SEC = 1.5
GOALKEEPER_CLEAR_FORWARD_DISTANCE_M = 4.0
GOALKEEPER_CLEAR_LATERAL_DISTANCE_M = 2.2
GOALKEEPER_CLEAR_CENTER_BAND_M = 0.35
GOALKEEPER_CLEAR_FIELD_MARGIN_M = 0.4
GOALKEEPER_CLEAR_POWER = 7.0
GOALKEEPER_MODE_MIN_HOLD_SEC = 0.45
GOALKEEPER_RETURN_ARRIVE_M = 0.25

SUPPORT_DIST_M = 3.0

# ======================================================================
# Normal 阶段策略
# ======================================================================

DEFAULT_GOALKEEPER_ID = 1  # 裁判守门员字段无效时的稳定回退编号

ATTACKER_KEEP_DIST_MARGIN_M = 0.3  # 防止 Attacker 选择产生震荡

FALLEN_COST = 10.0  # 摔倒球员的距离惩罚值(米)

# 双前场普通进攻:搭档围绕球动态接应,并在明显更接近球时临时处理球。
NORMAL_ATTACK_PARTNER_CHALLENGE_DISTANCE_M = 1.4
NORMAL_ATTACK_PARTNER_CLOSER_MARGIN_M = 0.15
NORMAL_ATTACK_SUPPORT_FORWARD_DISTANCE_M = 0.6
NORMAL_ATTACK_SUPPORT_LATERAL_DISTANCE_M = 1.2
NORMAL_ATTACK_REBOUND_FORWARD_DISTANCE_M = 0.85
NORMAL_ATTACK_REBOUND_LATERAL_DISTANCE_M = 0.9
NORMAL_ATTACK_SUPPORT_PRIMARY_SPACING_M = 1.0
NORMAL_ATTACK_SUPPORT_TOUCHLINE_ZONE_M = 0.9
NORMAL_ATTACK_SUPPORT_FIELD_MARGIN_M = 0.3
NORMAL_ATTACK_REBOUND_PENALTY_MARGIN_M = 0.5

# 普通比赛攻防态估计。distance_advantage = 对方最近距离 - 我方最近距离。
OPEN_PLAY_ATTACK_DISTANCE_ADVANTAGE_M = 0.45
OPEN_PLAY_DEFENSE_DISTANCE_ADVANTAGE_M = 0.35
OPEN_PLAY_CONTESTED_DISTANCE_BAND_M = 0.15
OPEN_PLAY_MODE_MIN_HOLD_SEC = 1.0

# 中场位置带只用于距离证据不充分时辅助判断，不再直接按半场切换攻防。
OPEN_PLAY_CONTESTED_BAND_M = 0.6

# 己方禁区前沿及门前中路属于可立即打断迟滞的危险区域。
OPEN_PLAY_DANGER_X_M = -3.5
OPEN_PLAY_DANGER_LATERAL_MARGIN_M = 0.5
OPEN_PLAY_IMMEDIATE_GOAL_DANGER_DEPTH_M = 1.2

# 保护球员沿球到己方球门的连线站位，并尽量与球保持可用拦截距离。
NORMAL_DEFENSE_PROTECT_DISTANCE_M = 2.0
NORMAL_DEFENSE_GOAL_LINE_CLEARANCE_M = 1.0
NORMAL_DEFENSE_FIELD_MARGIN_M = 0.3

# 逼抢球员进入该距离后立即尝试向对方球门方向解围。
NORMAL_DEFENSE_PRESSURE_CLEAR_DISTANCE_M = 0.5


# ======================================================================
# 丢球恢复
# ======================================================================

BALL_LAST_SEEN_MEMORY_SEC = 3.0
BALL_REACQUIRE_FRAMES = 3
BALL_SEARCH_FACE_TOLERANCE_RAD = 0.20
BALL_SEARCH_YAW_SPEED = 0.60
BALL_SEARCH_SWEEP_SEC = 2.5


# ======================================================================
# 开球 / 定位球策略
# ======================================================================

# 开球
KICKOFF_STAGE_M = 2.0
KICKOFF_FRONT_MARGIN = 0.1
KICKOFF_LATERAL_TOL = 0.35

CENTER_LEAVE_DIST_M = 0.15 # 球离开中心点多少距离，认为球已经动了


# ======================================================================
# 站位 / 避让
# ======================================================================

OPPONENT_RESTART_AVOID_M = 1.6
CIRCLE_MARGIN_M = 0.3


# ======================================================================
# 踢球目标几何
# ======================================================================

GOAL_TARGET_DEPTH_M = 0.25              # 踢球目标点在球门内的深度 (m)

# ======================================================================
# 障碍物几何参数
# ======================================================================

BALL_OBSTACLE_RADIUS = 0.5              # 球的障碍半径 (m)；用于避障计算
OPPONENT_RADIUS = 0.55                  # 对手机器人半径 (m)
TEAMMATE_RADIUS = 0.48                  # 队友机器人半径 (m)
SAFETY_MARGIN = 0.22                    # 通用安全余量 (m)

GOAL_DEPTH = 0.6                        # 球门深度 (m)；用于球门障碍建模
POST_RADIUS = 0.18                      # 门柱半径 (m)
NET_RADIUS = 0.20                       # 球网半径 (m)
NET_STEP = 0.35                         # 球网离散化步长 (m)

START_IGNORE = 0.0                      # 起点忽略半径 (m)；起点附近障碍不参与规划
TARGET_IGNORE = 0.0                     # 终点忽略半径 (m)；终点附近障碍不参与规划

# ======================================================================
# 全局路径规划器 (A* Grid Planner)
# ======================================================================

USE_GLOBAL_PATH_PLANNER = True          # 是否启用全局规划器；False 时回退到局部 VFH
GLOBAL_GRID_RESOLUTION_M = 0.35         # 栅格分辨率 (m/格)；越小路径越精细，但计算量越大
GLOBAL_FIELD_MARGIN_M = 0.25            # 场地边界外扩余量 (m)；确保边界附近路径可行
GLOBAL_OBSTACLE_MARGIN_M = 0.10         # 障碍物膨胀余量 (m)；在障碍半径上额外扩大多少
GLOBAL_PATH_LOOKAHEAD_M = 0.9           # 从规划路径中提取前方多远的路径点 (m)

# ======================================================================
# 局部路径规划器 (VFH Direction Scan)
# ======================================================================

PLAN_LOOKAHEAD = 1.2                    # 前方探测射线长度 (m)；规划器"看"的范围
PLAN_CLEARANCE = 0.35                   # 最小安全余量 (m)；候选方向前方必须大于此值
PLAN_STEP = math.radians(15)            # 候选方向扫描步长 (rad)；越小方向越精细
PLAN_MAX_OFFSET = math.radians(100)     # 最大偏离目标方向的角度 (rad)；扫描范围 ± 该值

# ======================================================================
# 可视化
# ======================================================================

KICK_TARGET_MARK_SIZE_M = 0.18

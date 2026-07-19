"""Player:单个机器人的控制 handle —— 用户可直接编辑这个类。

平台原语(set_velocity / kick / release_kick / request_mode / get_up)委托给注入的
``_backend``(framework 层);状态 property(pose / mode / is_fallen / penalty)从
``self.context`` 或 backend 读。

走位行为(walk_to / face_to / ensure_ready)也是 Player 方法:它们本质是"对本
球员下命令的动词",且未来加迟滞/避障需要的跨帧状态可直接挂 ``self``。纯坐标计算
(dist / angle_to / 球门坐标等)才放 utils/geom。

实例跨整场比赛存活;``self.context`` 每帧被框架覆写。用户想加自己的拐棍方法直接
在这里加。
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from .framework.types import Context, Penalty, Pose2D
from .framework import debugdraw

from .param import *
from .utils.geom import (
    angle_to,
    clamp,
    dist,
    normalize_angle,
    opponent_goal,
    own_goal,
    own_goal_area_center,
)
from .utils.obstacles import collect_obstacles
from .utils.path_planner import plan_global_path

if TYPE_CHECKING:
    from .framework.config import SoccerConfig


__all__ = ["Player"]


_log = logging.getLogger(__name__)

def _heading_clearance(
    px: float, py: float, heading: float, obstacles: list,
) -> float:
    """用于局部避障。沿 (px,py) 朝 ``heading`` 的 lookahead 射线,到最近障碍的余量(dist - radius)。

    只考虑**前方**(投影 t>0)的障碍;身后 / 侧后的障碍不挡这个方向,跳过——否则贴近
    任何障碍(哪怕正后方)都会把所有方向的余量拉低,误判"全堵"。
    无障碍返回 inf;越大越空,负值表示会撞。
    """
    ux, uy = math.cos(heading), math.sin(heading)
    min_clear = math.inf
    for obs in obstacles:
        t = (obs.x - px) * ux + (obs.y - py) * uy
        if t <= 0.0:
            continue                      # 障碍在身后/侧后,不挡此方向
        if t > PLAN_LOOKAHEAD:
            t = PLAN_LOOKAHEAD
        nx, ny = px + ux * t, py + uy * t
        clear = math.hypot(obs.x - nx, obs.y - ny) - obs.radius
        if clear < min_clear:
            min_clear = clear
    return min_clear


def _behind_ball(
    ball_x: float, ball_y: float, aim: tuple[float, float], offset: float,
) -> tuple[float, float]:
    """用于计算站位。球在 球→``aim`` 连线上的"后方"点:从球沿【背离 aim】方向退 ``offset`` 米。

    追球走位目标用它:球落在机器人与 ``aim`` 之间,到位时天然对准 aim 方向。
    aim 与球重合(退化)时,方向不定,直接返回球位。
    """
    dx, dy = aim[0] - ball_x, aim[1] - ball_y
    d = math.hypot(dx, dy)
    if d < 1e-6:
        return (ball_x, ball_y)
    ux, uy = dx / d, dy / d              # 球指向 aim 的单位向量
    return (ball_x - ux * offset, ball_y - uy * offset)   # 背离 aim 退 offset


class Player:
    """单个球员的 handle。可以在这里加入新的技术动作。
    """

    def __init__(
        self,
        player_id: int,
        config: "SoccerConfig",
        _backend: object | None,
    ) -> None:
        self.id: int = player_id
        self.config: "SoccerConfig" = config
        self._backend = _backend           # 机器人控制接口的包装，通常不用改
        self.context: Context | None = None

        # 当前高层动作名(仅供可视化/调试)。策略分派层每帧写入;有子状态的动作
        # (如 guard)在方法内部细化。可视化 pass 读它标注文字,见 main.py。
        self.action: str = "init"

        # SDK 缓存字段,框架后台会自动更新
        self._mode: str | None = None
        self._fall_down_state: str | None = None

        # 避障绕行侧记忆(跨帧;None=当前无绕行)
        self._avoid_side: float | None = None

        # 走位控制模式迟滞。None 表示尚未根据首个目标初始化。
        self._near_walk_mode: bool | None = None
        self._turn_in_place: bool = False

        # 踢球迟滞状态(跨帧)
        self._kicking: bool = False

        # 追球接近状态:在球的错误一侧时,保持固定方向沿球周围绕到射门线后方。
        self._ball_approach_mode: str = "direct"
        self._ball_approach_side: float | None = None
        self._ball_approach_best_error: float = math.inf
        self._ball_approach_last_progress_at: float | None = None
        self._ball_approach_direct_until: float = 0.0

        # block/guard 的跨帧迟滞状态
        self._block_pressing: bool = False
        self._guard_threatened: bool = False
        # 门线撞球进门迟滞状态
        self._goal_line_push: bool = False
        # support 卡住后临时转 attack 的跨帧状态
        self._support_last_pos: tuple[float, float] | None = None
        self._support_stationary_since: float | None = None
        self._support_last_update_at: float | None = None

    # ------------------------------------------------------------------
    # 状态读取
    # ------------------------------------------------------------------

    @property
    def is_kicking(self) -> bool:
        """当前是否处于踢球状态。"""
        return self._kicking

    @property
    def pose(self) -> Pose2D | None:
        ctx = self.context
        if ctx is None:
            return None
        robot = ctx.teammates.get(self.id)
        return None if robot is None else robot.pose

    @property
    def mode(self) -> str | None:
        if self._backend is not None:
            return self._backend.mode
        return self._mode

    @property
    def is_fallen(self) -> bool:
        return self.fall_down_state not in (None, "normal")

    @property
    def fall_down_state(self) -> str | None:
        if self._backend is not None:
            return getattr(self._backend, "fall_down_state", None)
        return self._fall_down_state

    @property
    def penalty(self) -> Penalty:
        ctx = self.context
        if ctx is None or ctx.game is None:
            return Penalty.NONE
        state = ctx.game.get_player_state(self.config.team_id, self.id)
        return Penalty.NONE if state is None else state.penalty

    @property
    def is_penalized(self) -> bool:
        return self.penalty != Penalty.NONE

    # ------------------------------------------------------------------
    # 底盘控制
    # ------------------------------------------------------------------

    def set_velocity(self, vx: float, vy: float, vyaw: float) -> None:
        if not all(math.isfinite(value) for value in (vx, vy, vyaw)):
            _log.warning(
                "player %d rejected non-finite velocity command: %r, %r, %r",
                self.id, vx, vy, vyaw,
            )
            vx, vy, vyaw = 0.0, 0.0, 0.0

        planar_speed = math.hypot(vx, vy)
        if planar_speed > MAX_LINEAR:
            scale = MAX_LINEAR / planar_speed
            vx *= scale
            vy *= scale
        vyaw = clamp(vyaw, -MAX_ANGULAR, MAX_ANGULAR)

        if self._backend is None:
            _log.debug(
                "player %d set_velocity vx=%.3f vy=%.3f vyaw=%.3f (no backend)",
                self.id, vx, vy, vyaw,
            )
            return
        self._backend.set_velocity(vx, vy, vyaw)

    def stop(self, *, preserve_ball_approach: bool = False) -> None:
        if not preserve_ball_approach:
            self._reset_ball_approach()
        self.release_kick()
        self.set_velocity(0.0, 0.0, 0.0)

    # ------------------------------------------------------------------
    # 踢球
    # ------------------------------------------------------------------

    def kick(
        self,
        kick_direction: float | None = None,
        power: float = KICK_POWER_DEFAULT,
    ) -> None:
        pose = self.pose
        if pose is None:
            _log.warning("player %d kick skipped: pose unknown", self.id)
            return

        ball = self.context.ball if self.context is not None else None
        if ball is None:
            _log.warning("player %d kick skipped: ball unknown", self.id)
            return

        if kick_direction is None:
            kick_plan = self.plan_kick()
            if kick_plan is None:
                _log.warning("player %d kick skipped: kick plan unavailable", self.id)
                return
            kick_direction, power = kick_plan

        if self._backend is None:
            _log.debug(
                "player %d kick ball=(%.3f, %.3f) dir=%.3f (no backend)",
                self.id, ball.x, ball.y, kick_direction,
            )
            return
        # 场地坐标 → 体坐标(用当前 pose)
        dx = ball.x - pose.x
        dy = ball.y - pose.y
        cos_t = math.cos(pose.theta)
        sin_t = math.sin(pose.theta)
        ball_x_body = dx * cos_t + dy * sin_t
        ball_y_body = -dx * sin_t + dy * cos_t
        kick_direction = normalize_angle(kick_direction)
        direction_body = normalize_angle(kick_direction - pose.theta)
        power_clamped = max(KICK_POWER_MIN, min(KICK_POWER_MAX, power))
        self._kicking = True
        self._backend.kick(direction_body, power_clamped, ball_x_body, ball_y_body)

    def plan_kick(self) -> tuple[float, float] | None:
        """计算踢球方向和力度。

        从当前球位踢向对方球门中心,力度 2.0。
        返回 ``(kick_direction, kick_power)``;球或上下文不可用时返回 None。
        """
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return None

        kick_target = opponent_goal(ctx)
        kick_direction = angle_to(ball.x, ball.y, *kick_target)
        kick_target = self._goal_target_for_direction(kick_direction)
        kick_power = (
            KICK_POWER_BACKFIELD if self._in_backfield()
            else KICK_POWER_DEFAULT
        )

        self._draw_kick_target(kick_target)
        return kick_direction, kick_power

    def _goal_target_for_direction(
        self, kick_direction: float,
    ) -> tuple[float, float]:
        """把射门方向投到对方门线上,用于可视化踢球目标。"""
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return (0.0, 0.0)

        dx = math.cos(kick_direction)
        if dx <= 1e-6:
            return opponent_goal(ctx)
        goal_x = ctx.field.length / 2.0
        t = max(0.0, (goal_x - ball.x) / dx)
        return (goal_x, ball.y + math.sin(kick_direction) * t)

    def _in_backfield(self) -> bool:
        """球在我方后场时,默认踢球加大力度。"""
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return False
        # own_penalty_edge_x = -ctx.field.length / 2.0 + ctx.field.penalty_area_length
        # return ball.x < own_penalty_edge_x
        return ball.x < 0

    def _draw_kick_target(self, target: tuple[float, float]) -> None:
        """以 X 标出 plan_kick 选择的踢球目标。"""
        from .framework import debugdraw

        x, y = target
        s = KICK_TARGET_MARK_SIZE_M
        debugdraw.line(
            [(x - s, y - s), (x + s, y + s)],
            rgb=(1.0, 0.0, 1.0), ns="kick_target",
        )
        debugdraw.line(
            [(x - s, y + s), (x + s, y - s)],
            rgb=(1.0, 0.0, 1.0), ns="kick_target",
        )

    def release_kick(self) -> None:
        self._kicking = False  # 清除踢球迟滞标志(取消方块显示)
        if self._backend is None:
            _log.debug("player %d release_kick (no backend)", self.id)
            return
        self._backend.release_kick()

    def kick_can_score(self, kick_direction: float) -> bool:
        """判断从当前球位按 ``kick_direction`` 踢,直线轨迹是否能进对方球门。
        """
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return False

        goal_x = ctx.field.length / 2.0
        dx = math.cos(kick_direction)
        dy = math.sin(kick_direction)
        if dx <= 1e-6:
            return False

        half_goal = ctx.field.goal_width / 2.0
        if half_goal <= 0.0:
            return False
        if ball.x >= goal_x:
            y_at_goal = ball.y
        else:
            t = (goal_x - ball.x) / dx
            if t < 0.0:
                return False
            y_at_goal = ball.y + dy * t
        return -half_goal <= y_at_goal <= half_goal

    # ------------------------------------------------------------------
    # 慢操作（同步接口异步调取，使得不阻塞）
    # ------------------------------------------------------------------

    def request_mode(self, mode: str) -> None:
        if self._backend is None:
            _log.debug("player %d request_mode -> %s (no backend)", self.id, mode)
            return
        self._backend.request_mode(mode)

    def get_up(self) -> None:
        if self._backend is None:
            _log.debug("player %d get_up (no backend)", self.id)
            return
        self._backend.get_up()

    # ------------------------------------------------------------------
    # 走位
    # ------------------------------------------------------------------

    def ensure_ready(self) -> bool:
        """活性自理:摔倒→起身,非 walk→切模式(都异步、不产生移动)。

        返回本帧是否可执行动作(True=已就绪)。
        """
        if self.is_fallen:
            self.get_up()
            return False
        if self.mode != "walk":
            self.request_mode("walk")
            return False
        return True

    def face_to(self, target_theta: float) -> None:
        """原地转向到目标朝向。"""
        if self.pose is None:
            self.stop()
            return
        err = normalize_angle(target_theta - self.pose.theta)
        self.set_velocity(0.0, 0.0, self._angular(err))

    def walk_to(
        self,
        target: tuple[float, float],
        *,
        face: float | None = None,
        avoid_ball: bool = False,
        avoid_robots: bool = False,
        arrive_dist: float = ARRIVE_DIST,
        preserve_ball_approach: bool = False,
    ) -> bool:
        """走向目标点。返回是否已到达。

        避障(局部规划器,简化版 VFH):``avoid_ball`` / ``avoid_robots`` 开启时,把
        球 / 机器人 / 球门结构收集成圆形障碍,在机器人周围扫一圈候选方向,选"最朝
        目标 + 前方 ``PLAN_LOOKAHEAD`` 内不撞障碍"的方向前进。能处理多障碍、凹形
        结构和对称冲突,比单障碍绕行 / 势场排斥稳。

        行走两种模式:近距离全向,远距离弧线行走或大角差原地转向。``face`` 指定
        到达/近距离时的朝向。
        """
        if not preserve_ball_approach:
            self._reset_ball_approach()
        self.release_kick() # 踢球状态下，走位会被覆盖，先取消踢球状态
        pose = self.pose
        if pose is None:
            self.stop()
            return False

        tx, ty = target
        dx = tx - pose.x
        dy = ty - pose.y
        distance = math.hypot(dx, dy)

        if distance < arrive_dist:
            self._turn_in_place = False
            # 已到达:按需转到目标朝向
            if face is not None:
                err = normalize_angle(face - pose.theta)
                if abs(err) > 0.1:
                    self.set_velocity(0.0, 0.0, self._angular(err))
                else:
                    self.stop(
                        preserve_ball_approach=preserve_ball_approach,
                    )
            else:
                self.stop(
                    preserve_ball_approach=preserve_ball_approach,
                )
            return True

        # 规划:默认全局 A*,找不到路时退回旧局部规划。
        goal_dir = math.atan2(dy, dx)
        planned_path: list[tuple[float, float]] | None = None
        waypoint: tuple[float, float] | None = None
        obstacles: list = []
        selected_clearance = math.inf
        if (avoid_ball or avoid_robots) and self.context is not None:
            obstacles = collect_obstacles(
                self.context, self.id,
                ball=avoid_ball, robots=avoid_robots,
                goals=(avoid_ball or avoid_robots),
            )
            if USE_GLOBAL_PATH_PLANNER:
                planned_path = plan_global_path(
                    self.context,
                    (pose.x, pose.y),
                    (tx, ty),
                    obstacles,
                )
                if planned_path is not None:
                    waypoint = self._path_waypoint(pose, planned_path)
                    heading = angle_to(pose.x, pose.y, waypoint[0], waypoint[1])
                    selected_clearance = _heading_clearance(
                        pose.x, pose.y, heading, obstacles,
                    )
                else:
                    heading, selected_clearance = self._plan_heading(
                        pose, goal_dir, obstacles,
                    )
            else:
                heading, selected_clearance = self._plan_heading(
                    pose, goal_dir, obstacles,
                )
        else:
            heading = goal_dir

        # 可视化:目标点(绿)、到目标连线(灰)、规划朝向(黄箭头)、
        # 前方探测射线(青,长度=lookahead;规划器"看"的范围,无 path/途径点概念)
        from .framework import debugdraw
        debugdraw.point(tx, ty, rgb=(0.0, 1.0, 0.0), scale=0.15, ns="target")
        debugdraw.line([(pose.x, pose.y), (tx, ty)], rgb=(0.4, 0.4, 0.4), ns="to_target")
        if planned_path is not None and len(planned_path) >= 2:
            debugdraw.line(planned_path, rgb=(0.2, 0.8, 1.0), ns="global_path")
        if waypoint is not None:
            debugdraw.point(
                waypoint[0], waypoint[1],
                rgb=(0.2, 0.8, 1.0), scale=0.12, ns="global_waypoint",
            )
        debugdraw.arrow(
            pose.x, pose.y,
            pose.x + math.cos(heading) * 0.6, pose.y + math.sin(heading) * 0.6,
            rgb=(1.0, 1.0, 0.0), ns="heading",
        )
        debugdraw.line(
            [(pose.x, pose.y),
             (pose.x + math.cos(heading) * PLAN_LOOKAHEAD,
              pose.y + math.sin(heading) * PLAN_LOOKAHEAD)],
            rgb=(0.0, 0.8, 0.8), ns="lookahead",
        )

        self._update_near_walk_mode(distance)
        distance_factor = self._distance_speed_factor(distance, arrive_dist)
        clearance_factor = self._clearance_speed_factor(selected_clearance)

        if self._near_walk_mode:
            self._turn_in_place = False
            # 近距离:全向行走,沿 heading 平移,同时转向 face
            wdx, wdy = math.cos(heading) * distance, math.sin(heading) * distance
            cos_t, sin_t = math.cos(pose.theta), math.sin(pose.theta)
            translation_scale = distance_factor * clearance_factor
            vx = LINEAR_GAIN * (wdx * cos_t + wdy * sin_t) * translation_scale
            vy = LINEAR_GAIN * (-wdx * sin_t + wdy * cos_t) * translation_scale
            vyaw = (
                self._angular(normalize_angle(face - pose.theta))
                if face is not None else 0.0
            )
            self.set_velocity(vx, vy, vyaw)
        else:
            # 远距离:大角差原地转向,中小角差按距离/角差连续降速并弧线行走。
            angle_err = normalize_angle(heading - pose.theta)
            self._update_turn_mode(abs(angle_err))
            if self._turn_in_place:
                self.set_velocity(0.0, 0.0, self._angular(angle_err))
            else:
                heading_factor = math.sqrt(max(0.0, math.cos(angle_err)))
                forward_speed = (
                    min(MAX_LINEAR, LINEAR_GAIN * distance)
                    * distance_factor
                    * heading_factor
                    * clearance_factor
                )
                self.set_velocity(
                    forward_speed, 0.0, self._angular(angle_err),
                )
        return False

    def _update_near_walk_mode(self, distance: float) -> None:
        if self._near_walk_mode is None:
            initial_threshold = (OMNI_ENTER_DIST + OMNI_EXIT_DIST) / 2.0
            self._near_walk_mode = distance <= initial_threshold
        elif self._near_walk_mode and distance >= OMNI_EXIT_DIST:
            self._near_walk_mode = False
        elif not self._near_walk_mode and distance <= OMNI_ENTER_DIST:
            self._near_walk_mode = True

    def _update_turn_mode(self, absolute_angle_error: float) -> None:
        if self._turn_in_place:
            if absolute_angle_error <= TURN_IN_PLACE_EXIT:
                self._turn_in_place = False
        elif absolute_angle_error >= TURN_IN_PLACE_ENTER:
            self._turn_in_place = True

    @staticmethod
    def _distance_speed_factor(distance: float, arrive_dist: float) -> float:
        remaining_distance = max(0.0, distance - arrive_dist)
        return clamp(
            remaining_distance / max(ARRIVAL_BRAKE_DISTANCE_M, 1e-6),
            0.0,
            1.0,
        )

    @staticmethod
    def _clearance_speed_factor(clearance: float) -> float:
        if math.isinf(clearance):
            return 1.0
        clearance_span = CLEARANCE_SPEED_FULL_M - CLEARANCE_SPEED_STOP_M
        if clearance_span <= 1e-6:
            return 1.0 if clearance > CLEARANCE_SPEED_STOP_M else 0.0
        return clamp(
            (clearance - CLEARANCE_SPEED_STOP_M) / clearance_span,
            0.0,
            1.0,
        )

    def _path_waypoint(
        self, pose: Pose2D, path: list[tuple[float, float]],
    ) -> tuple[float, float]:
        """Pick a short lookahead waypoint from a planned global path."""
        if not path:
            return (pose.x, pose.y)
        prev = (pose.x, pose.y)
        points = path[1:] if len(path) > 1 else path
        for point in points:
            seg_len = dist(prev[0], prev[1], point[0], point[1])
            if seg_len >= GLOBAL_PATH_LOOKAHEAD_M:
                ratio = GLOBAL_PATH_LOOKAHEAD_M / max(seg_len, 1e-6)
                return (
                    prev[0] + (point[0] - prev[0]) * ratio,
                    prev[1] + (point[1] - prev[1]) * ratio,
                )
            prev = point
        return path[-1]

    def _plan_heading(
        self, pose: Pose2D, goal_dir: float, obstacles: list,
    ) -> tuple[float, float]:
        """返回规划方向及其净空;都不够空时返回净空最大的方向。

        候选按偏离目标方向 ``|offset|`` 从小到大试;先试哪一侧由 player_id 奇偶决定,
        用来打破两人同侧避让的对称。
        """
        sign_first = 1.0 if self.id % 2 == 0 else -1.0
        best_h = goal_dir
        best_clear = -math.inf

        offsets = [0.0]
        k = 1
        while k * PLAN_STEP <= PLAN_MAX_OFFSET + 1e-9:
            offsets.append(sign_first * k * PLAN_STEP)
            offsets.append(-sign_first * k * PLAN_STEP)
            k += 1

        for off in offsets:
            h = goal_dir + off
            clear = _heading_clearance(pose.x, pose.y, h, obstacles)
            if clear >= PLAN_CLEARANCE:
                return h, clear
            if clear > best_clear:
                best_clear, best_h = clear, h
        return best_h, best_clear

    # ------------------------------------------------------------------
    # 高层动作(策略在 main.py 里直接调这些)
    # ------------------------------------------------------------------

    def _reset_ball_approach(self, *, clear_fallback: bool = True) -> None:
        self._ball_approach_mode = "direct"
        self._ball_approach_side = None
        self._ball_approach_best_error = math.inf
        self._ball_approach_last_progress_at = None
        if clear_fallback:
            self._ball_approach_direct_until = 0.0

    def _ball_approach_target(
        self,
        kick_target: tuple[float, float],
    ) -> tuple[tuple[float, float], float]:
        """选择直接球后点或沿球周围绕行的下一目标点。"""
        ctx = self.context
        pose = self.pose
        ball = ctx.ball if ctx is not None else None
        if ctx is None or pose is None or ball is None:
            return kick_target, 0.0

        kick_direction = angle_to(ball.x, ball.y, *kick_target)
        desired_behind_angle = normalize_angle(kick_direction + math.pi)
        player_angle_around_ball = angle_to(ball.x, ball.y, pose.x, pose.y)
        alignment_error = normalize_angle(
            desired_behind_angle - player_angle_around_ball,
        )
        absolute_alignment_error = abs(alignment_error)
        now = ctx.now

        direct_fallback_active = now < self._ball_approach_direct_until
        if self._ball_approach_mode == "circle":
            passed_target_angle = (
                self._ball_approach_side is not None
                and alignment_error * self._ball_approach_side <= 0.0
            )
            if (
                absolute_alignment_error <= CHASE_DIRECT_ENTER_ANGLE_RAD
                or passed_target_angle
            ):
                self._reset_ball_approach(clear_fallback=False)
            else:
                progress = self._ball_approach_best_error - absolute_alignment_error
                if progress >= CHASE_CIRCLE_PROGRESS_RAD:
                    self._ball_approach_best_error = absolute_alignment_error
                    self._ball_approach_last_progress_at = now

                progress_timed_out = (
                    self._ball_approach_last_progress_at is not None
                    and now - self._ball_approach_last_progress_at
                    >= CHASE_CIRCLE_TIMEOUT_SEC
                )
                if progress_timed_out:
                    self._ball_approach_direct_until = (
                        now + CHASE_CIRCLE_FALLBACK_SEC
                    )
                    self._reset_ball_approach(clear_fallback=False)

        direct_fallback_active = now < self._ball_approach_direct_until
        if (
            self._ball_approach_mode == "direct"
            and not direct_fallback_active
            and absolute_alignment_error >= CHASE_DIRECT_EXIT_ANGLE_RAD
        ):
            self._ball_approach_mode = "circle"
            self._ball_approach_side = 1.0 if alignment_error >= 0.0 else -1.0
            self._ball_approach_best_error = absolute_alignment_error
            self._ball_approach_last_progress_at = now

        if self._ball_approach_mode != "circle":
            self.action = "attack:direct"
            return (
                _behind_ball(ball.x, ball.y, kick_target, CHASE_BEHIND_M),
                kick_direction,
            )

        approach_side = self._ball_approach_side or 1.0
        if approach_side > 0.0:
            remaining_angle = (
                desired_behind_angle - player_angle_around_ball
            ) % (2.0 * math.pi)
        else:
            remaining_angle = (
                player_angle_around_ball - desired_behind_angle
            ) % (2.0 * math.pi)
        angular_step = min(remaining_angle, CHASE_CIRCLE_STEP_RAD)
        waypoint_angle = player_angle_around_ball + approach_side * angular_step
        circle_target = (
            ball.x + math.cos(waypoint_angle) * CHASE_CIRCLE_RADIUS_M,
            ball.y + math.sin(waypoint_angle) * CHASE_CIRCLE_RADIUS_M,
        )
        self.action = (
            "attack:circle_left"
            if approach_side > 0.0 else "attack:circle_right"
        )
        debugdraw.point(
            circle_target[0], circle_target[1],
            rgb=(0.8, 0.2, 1.0), scale=0.14, ns="ball_approach",
        )
        return circle_target, kick_direction


    def block_path_projection(
        self, opponent_id: int,
    ) -> tuple[float, float, float, float] | None:
        """自己到 对手→球 线段的垂足:返回 (x, y, 垂距, 线段参数 t)。"""
        ctx = self.context
        pose = self.pose
        ball = ctx.ball if ctx is not None else None
        opponent = ctx.opponents.get(opponent_id) if ctx is not None else None
        if ctx is None or pose is None or ball is None or opponent is None:
            return None
        if opponent.pose is None:
            return None

        ax, ay = opponent.pose.x, opponent.pose.y
        bx, by = ball.x, ball.y
        vx, vy = bx - ax, by - ay
        length2 = vx * vx + vy * vy
        if length2 < 1e-6:
            return None

        raw_t = ((pose.x - ax) * vx + (pose.y - ay) * vy) / length2
        t = clamp(raw_t, 0.0, 1.0)
        tx = ax + vx * t
        ty = ay + vy * t
        return tx, ty, dist(pose.x, pose.y, tx, ty), raw_t

    def attack(self, kick_target: tuple[float, float] | None = None) -> None:
        """追球射门。踢向 ``kick_target``(默认对方球门中心)。

        踢球迟滞同时检查距球和球后方对齐角度。不在合适一侧时,保持固定绕行方向
        沿球周围接近;对齐后再走直接球后点并踢球。不避障——正常拼抢要直取球。
        """
        ball = self.context.ball if self.context is not None else None
        if ball is None or self.pose is None:
            self._reset_ball_approach()
            self.stop()
            return
        if kick_target is None:
            kick_target = opponent_goal(self.context)

        d = dist(self.pose.x, self.pose.y, ball.x, ball.y)
        kick_direction = angle_to(ball.x, ball.y, *kick_target)
        desired_behind_angle = normalize_angle(kick_direction + math.pi)
        player_angle_around_ball = angle_to(
            ball.x, ball.y, self.pose.x, self.pose.y,
        )
        alignment_error = abs(normalize_angle(
            desired_behind_angle - player_angle_around_ball,
        ))
        kick_distance_limit = KICK_EXIT_M if self._kicking else KICK_ENTER_M
        kick_alignment_limit = (
            CHASE_DIRECT_EXIT_ANGLE_RAD
            if self._kicking else CHASE_DIRECT_ENTER_ANGLE_RAD
        )
        self._kicking = (
            d <= kick_distance_limit
            and alignment_error <= kick_alignment_limit
        )
        if self._kicking:
            self._reset_ball_approach()
            kick_plan = self.plan_kick()
            if kick_plan is None:
                self.stop()
                return
            kick_direction, kick_power = kick_plan
            self.kick(kick_direction, kick_power)
        else:
            self.release_kick()
            approach_target, kick_direction = self._ball_approach_target(
                kick_target,
            )
            self.walk_to(
                approach_target,
                face=kick_direction,
                preserve_ball_approach=True,
            )

    def search_for_ball(
        self,
        last_known_ball: tuple[float, float] | None,
        sweep_direction: float,
    ) -> None:
        """先朝最后球位转向,随后按整队搜索方向原地低速扫场。"""
        self._reset_ball_approach()
        self.release_kick()
        pose = self.pose
        if pose is None:
            self.stop()
            return

        if last_known_ball is not None:
            target_heading = angle_to(
                pose.x, pose.y, last_known_ball[0], last_known_ball[1],
            )
            heading_error = normalize_angle(target_heading - pose.theta)
            if abs(heading_error) > BALL_SEARCH_FACE_TOLERANCE_RAD:
                limited_yaw = clamp(
                    ANGULAR_GAIN * heading_error,
                    -BALL_SEARCH_YAW_SPEED,
                    BALL_SEARCH_YAW_SPEED,
                )
                self.set_velocity(0.0, 0.0, limited_yaw)
                return

        direction = 1.0 if sweep_direction >= 0.0 else -1.0
        self.set_velocity(0.0, 0.0, direction * BALL_SEARCH_YAW_SPEED)

    def guard(
        self,
        target: tuple[float, float] | None = None,
        *,
        avoid_ball: bool = True,
        avoid_robots: bool = True,
        arrive_dist: float = ARRIVE_DIST,
    ) -> None:
        """走向指定门前目标；无目标时兼容原有小禁区中央站位。"""
        self._reset_ball_approach()
        if target is None and self.context is not None:
            target = own_goal_area_center(self.context)
        if target is None or self.pose is None:
            self.action = "guard:stop"
            self.stop()
            return

        ball = self.context.ball if self.context is not None else None

        # 待命朝向:朝球(便于快速反应);球不可见则朝对方门方向(默认 0)。
        face = 0.0
        if GUARD_FACE_BALL and ball is not None:
            face = angle_to(
                self.pose.x, self.pose.y, ball.x, ball.y,
            )

        self.action = "guard:home"
        debugdraw.point(
            target[0], target[1],
            rgb=(0.0, 0.6, 1.0), scale=0.2, ns="guard_home",
        )
        self.walk_to(
            target,
            face=face,
            avoid_ball=avoid_ball,
            avoid_robots=avoid_robots,
            arrive_dist=arrive_dist,
        )

    def goalkeeper_challenge(
        self,
        target: tuple[float, float],
    ) -> None:
        """守门员直取慢速危险球，不把球当作绕行障碍。"""
        self._reset_ball_approach()
        ball = self.context.ball if self.context is not None else None
        pose = self.pose
        if ball is None or pose is None:
            self.action = "goalkeeper:challenge:stop"
            self.stop()
            return

        face = angle_to(pose.x, pose.y, ball.x, ball.y)
        self.action = "goalkeeper:challenge:approach"
        self.walk_to(
            target,
            face=face,
            avoid_ball=False,
            avoid_robots=False,
            arrive_dist=GOALKEEPER_CLEAR_ENTER_DISTANCE_M,
        )

    def goalkeeper_clear(
        self,
        clearance_target: tuple[float, float],
        power: float,
    ) -> None:
        """接近门前球并按指定正向目标解围，不使用普通进攻绕球状态。"""
        self._reset_ball_approach()
        context = self.context
        ball = context.ball if context is not None else None
        pose = self.pose
        if context is None or ball is None or pose is None:
            self.action = "goalkeeper:clear:stop"
            self.stop()
            return

        target_x = max(clearance_target[0], ball.x + 0.5)
        safe_target = (target_x, clearance_target[1])
        kick_direction = angle_to(ball.x, ball.y, *safe_target)
        if math.cos(kick_direction) <= 0.0:
            kick_direction = 0.0

        ball_distance = dist(pose.x, pose.y, ball.x, ball.y)
        if ball_distance <= GOALKEEPER_CLEAR_ENTER_DISTANCE_M:
            self.kick(kick_direction, power)
            self.action = "goalkeeper:clear:kick"
            return

        self.release_kick()
        self.action = "goalkeeper:clear:approach"
        self.walk_to(
            (ball.x, ball.y),
            face=kick_direction,
            avoid_ball=False,
            avoid_robots=False,
            arrive_dist=GOALKEEPER_CLEAR_ENTER_DISTANCE_M,
        )

    def support(self) -> None:
        """支援:站在 球→己方门中心 连线上距球 ``SUPPORT_DIST_M`` 处补防。

        站位点在球与己方门之间的封堵线上。
        """
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            self._reset_ball_approach()
            self.stop()
            return

        self._reset_ball_approach()
        gx, gy = own_goal(ctx)
        bx, by = (ball.x, ball.y)
        dx, dy = gx - bx, gy - by
        d = math.hypot(dx, dy)
        if d < 1e-6:
            ux, uy = -1.0, 0.0
        else:
            ux, uy = dx / d, dy / d
        along = min(SUPPORT_DIST_M, d)
        tx = bx + ux * along
        ty = by + uy * along

        # 不越过己方底线(x >= 门线 + 0.3),横向夹进场内
        half_l = ctx.field.length / 2.0
        half_w = ctx.field.width / 2.0 - 0.3
        tx = clamp(tx, -half_l + 0.3, half_l)
        ty = clamp(ty, -half_w, half_w)
        self.move_to_position((tx, ty))

    def take_kickoff(self, kick_target: tuple[float, float] | None = None) -> None:
        """我方开球/重开:未就位则绕到球后待命(避球不碰),就位后接近并踢。"""
        self._reset_ball_approach()
        ball = self.context.ball if self.context is not None else None
        if ball is None or self.pose is None:
            self.stop()
            return
        if kick_target is None:
            kick_target = opponent_goal(self.context)
        kick_dir = angle_to(ball.x, ball.y, *kick_target)
        cos_k, sin_k = math.cos(kick_dir), math.sin(kick_dir)
        rel_x, rel_y = self.pose.x - ball.x, self.pose.y - ball.y
        behind = rel_x * cos_k + rel_y * sin_k          # <0 在球后方(己方侧)
        lateral = abs(-rel_x * sin_k + rel_y * cos_k)
        if behind > KICKOFF_FRONT_MARGIN or lateral > KICKOFF_LATERAL_TOL:
            stage = (
                ball.x - cos_k * KICKOFF_STAGE_M,
                ball.y - sin_k * KICKOFF_STAGE_M,
            )
            self.release_kick()
            self.walk_to(stage, face=kick_dir, avoid_ball=True)
        else:
            self.attack(kick_target)

    def move_to_position(self, target: tuple[float, float] | None) -> None:
        """走到站位点(支援/防守/避让),面向球,避球+避机器人。"""
        if target is None:
            self.stop()
            return
        face = None
        ball = self.context.ball if self.context is not None else None
        if ball is not None and self.pose is not None:
            face = angle_to(self.pose.x, self.pose.y, ball.x, ball.y)
        self.release_kick()
        self.walk_to(target, face=face, avoid_ball=True, avoid_robots=True)

    @staticmethod
    def _angular(err: float) -> float:
        return clamp(ANGULAR_GAIN * err, -MAX_ANGULAR, MAX_ANGULAR)

"""避障:障碍收集 + 单障碍绕行 via 点 —— 纯函数,可单测。

【utils】移植自旧 MotionController 的路径绕行层:从起点到目标画一条走廊,找第一个
挡路的圆形障碍,在其侧面生成一个 via 点绕过去。绕哪侧由调用方跨帧记忆(walk_to
用 self._avoid_side)。

障碍是可选的:球在对方重开时才算障碍(见 collect_obstacles 的 ball/robots 开关)。
半径等常数在此,可调。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..framework.types import Context
from ..param import (
    BALL_OBSTACLE_RADIUS,
    GOAL_DEPTH,
    NET_RADIUS,
    NET_STEP,
    OPPONENT_RADIUS,
    POST_RADIUS,
    SAFETY_MARGIN,
    START_IGNORE,
    TARGET_IGNORE,
    TEAMMATE_RADIUS,
)


__all__ = ["Obstacle", "collect_obstacles", "goal_obstacles", "detour"]


@dataclass(frozen=True)
class Obstacle:
    x: float
    y: float
    radius: float


def collect_obstacles(
    context: Context,
    exclude_id: int,
    *,
    ball: bool,
    robots: bool,
    goals: bool = False,
) -> list[Obstacle]:
    """按开关收集圆形障碍。ball=球,robots=对手+队友(排除自己),goals=两侧球门结构。"""
    obstacles: list[Obstacle] = []
    if ball and context.ball is not None:
        obstacles.append(
            Obstacle(context.ball.x, context.ball.y, BALL_OBSTACLE_RADIUS)
        )
    if robots:
        for r in context.opponents.values():
            if r.pose is not None:
                obstacles.append(Obstacle(r.pose.x, r.pose.y, OPPONENT_RADIUS))
        for tid, r in context.teammates.items():
            if tid != exclude_id and r.pose is not None:
                obstacles.append(Obstacle(r.pose.x, r.pose.y, TEAMMATE_RADIUS))
    if goals:
        obstacles.extend(goal_obstacles(context))
    return obstacles


def goal_obstacles(context: Context) -> list[Obstacle]:
    """两侧球门做成不可穿越的 U 形结构:4 根柱 + 3 面网采样成圆。"""
    f = context.field
    half_l = f.length / 2.0
    half_gw = f.goal_width / 2.0
    obstacles: list[Obstacle] = []
    for sign_x in (-1.0, 1.0):
        front_x = sign_x * half_l
        back_x = sign_x * (half_l + GOAL_DEPTH)
        for sign_y in (-1.0, 1.0):                       # 四根柱(前后各两根)
            obstacles.append(Obstacle(front_x, sign_y * half_gw, POST_RADIUS))
            obstacles.append(Obstacle(back_x, sign_y * half_gw, POST_RADIUS))
        # 后网
        obstacles += _sample_segment(
            back_x, -half_gw, back_x, half_gw, NET_STEP, NET_RADIUS,
        )
        # 两侧网
        for sign_y in (-1.0, 1.0):
            obstacles += _sample_segment(
                front_x, sign_y * half_gw, back_x, sign_y * half_gw,
                NET_STEP, NET_RADIUS,
            )
    return obstacles


def _sample_segment(
    x0: float, y0: float, x1: float, y1: float, step: float, radius: float,
) -> list[Obstacle]:
    """沿线段均匀采样圆形障碍(不含端点,端点由柱覆盖)。"""
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= step:
        return []
    n = max(1, int(length / step) - 1)
    return [
        Obstacle(
            x0 + (x1 - x0) * (i + 1) / (n + 1),
            y0 + (y1 - y0) * (i + 1) / (n + 1),
            radius,
        )
        for i in range(n)
    ]


def detour(
    sx: float, sy: float, tx: float, ty: float,
    obstacles: list[Obstacle],
    side_hint: float | None,
) -> tuple[tuple[float, float], float | None]:
    """在 (sx,sy)→(tx,ty) 路径上绕开第一个挡路障碍。

    返回 (可能被替换成 via 点的目标, 本次用的绕行侧)。无障碍时返回原目标和 None
    (调用方据此清空侧记忆)。``side_hint`` 是上帧记住的侧别,保持避免横跳。
    """
    blocker = _first_blocking_obstacle(sx, sy, tx, ty, obstacles)
    if blocker is None:
        return (tx, ty), None
    side = side_hint if side_hint is not None else _choose_side(sx, sy, tx, ty, blocker)
    via = _via_point(sx, sy, tx, ty, blocker, side)
    return via, side


def _first_blocking_obstacle(
    sx: float, sy: float, tx: float, ty: float, obstacles: list[Obstacle],
) -> Obstacle | None:
    """找真正挡在走廊里、离起点最近的障碍。"""
    seg_dx, seg_dy = tx - sx, ty - sy
    seg_len = math.hypot(seg_dx, seg_dy)
    if seg_len < 1e-6:
        return None
    dir_x, dir_y = seg_dx / seg_len, seg_dy / seg_len
    left_x, left_y = -dir_y, dir_x
    best: Obstacle | None = None
    best_along = 0.0
    for obs in obstacles:
        rel_x, rel_y = obs.x - sx, obs.y - sy
        along = rel_x * dir_x + rel_y * dir_y
        if along <= START_IGNORE or along >= seg_len - TARGET_IGNORE:
            continue
        lateral = abs(rel_x * left_x + rel_y * left_y)
        if lateral >= obs.radius + SAFETY_MARGIN:
            continue
        if best is None or along < best_along:
            best, best_along = obs, along
    return best


def _choose_side(
    sx: float, sy: float, tx: float, ty: float, obstacle: Obstacle,
) -> float:
    """障碍在路径左侧则从右绕(-1),反之从左绕(+1),取较短绕行。"""
    seg_dx, seg_dy = tx - sx, ty - sy
    seg_len = math.hypot(seg_dx, seg_dy)
    if seg_len < 1e-6:
        return 1.0
    left_x, left_y = -seg_dy / seg_len, seg_dx / seg_len
    lateral = (obstacle.x - sx) * left_x + (obstacle.y - sy) * left_y
    return -1.0 if lateral > 0.0 else 1.0


def _via_point(
    sx: float, sy: float, tx: float, ty: float,
    obstacle: Obstacle, side_sign: float,
) -> tuple[float, float]:
    """在障碍侧面生成 via 点:投影到路径的最近点,再沿法向偏移 (半径+余量)。"""
    seg_dx, seg_dy = tx - sx, ty - sy
    seg_len = math.hypot(seg_dx, seg_dy)
    if seg_len < 1e-6:
        return (tx, ty)
    dir_x, dir_y = seg_dx / seg_len, seg_dy / seg_len
    left_x, left_y = -dir_y, dir_x
    along = (obstacle.x - sx) * dir_x + (obstacle.y - sy) * dir_y
    closest_x, closest_y = sx + dir_x * along, sy + dir_y * along
    offset = obstacle.radius + SAFETY_MARGIN
    return (
        closest_x + left_x * side_sign * offset,
        closest_y + left_y * side_sign * offset,
    )

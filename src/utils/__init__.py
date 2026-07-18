"""工具层(utils)—— 框架预置的样例工具 + 用户自己加的工具。

纯函数、无状态、无平台依赖(只依赖 framework.types 数据契约),可独立复用。
用户可直接改这里,或新增自己的 util 模块(如出界判定、传球评分等)。

- geom:几何 helper(opponent_goal / dist / angle_to / clamp / clamp_inside_field)
- obstacles:避障(Obstacle / collect_obstacles / detour)

注:走位/活性(walk_to / face_to / ensure_ready)是"对 player 下命令的动词"、
且需要跨帧状态,已作为 Player 方法放在 src/player.py,不在 utils。
"""

from .geom import (
    angle_to,
    clamp,
    clamp_inside_field,
    deg2rad,
    dist,
    normalize_angle,
    opponent_goal,
    own_goal,
    own_goal_area_center,
    rad2deg,
)
from .obstacles import Obstacle, collect_obstacles, detour

__all__ = [
    "Obstacle",
    "angle_to",
    "clamp",
    "clamp_inside_field",
    "collect_obstacles",
    "deg2rad",
    "detour",
    "dist",
    "normalize_angle",
    "opponent_goal",
    "own_goal",
    "own_goal_area_center",
    "rad2deg",
]

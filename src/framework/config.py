"""框架配置:per-match identity 从环境变量读,其余用默认值。

字段集合最小化,只保留 Phase 1 用得到的:team_id / robot_names /
opponent_robot_names / control_hz。

将来增加 strategy tuning(走路 floor、kick power 等)时,要不要进 config
还是直接放 stdlib 模块常量,届时再定。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


__all__ = ["SoccerConfig"]


_DEFAULT_ROBOT_NAMES = ("robot1", "robot2", "robot3")


@dataclass(frozen=True)
class SoccerConfig:
    team_id: int = 1
    robot_names: tuple[str, ...] = _DEFAULT_ROBOT_NAMES
    opponent_robot_names: tuple[str, ...] = ()
    control_hz: float = 30.0
    game_controller_topic: str = "/soccer/game_controller"
    # 新鲜度阈值:数据超过对应秒数视为陈旧,runtime 构造 Context 时置 None。
    ball_max_age_sec: float = 1.5
    robot_pose_max_age_sec: float = 2.0
    game_state_max_age_sec: float = 2.0

    def __post_init__(self) -> None:
        if not self.opponent_robot_names:
            # frozen dataclass:用 object.__setattr__ 绕过赋值保护
            object.__setattr__(
                self,
                "opponent_robot_names",
                _default_opponent_robot_names(self.team_id),
            )

    @property
    def player_ids(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.robot_names) + 1))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SoccerConfig":
        env = os.environ if environ is None else environ
        team_id = _parse_int(env.get("SOCCER_TEAM_ID"), 1)
        robot_names = _parse_robot_names(
            env.get("SOCCER_ROBOT_NAMES"),
            default=_DEFAULT_ROBOT_NAMES,
        )
        opponent_robot_names = _parse_robot_names(
            env.get("SOCCER_OPPONENT_ROBOT_NAMES"),
            default=_default_opponent_robot_names(team_id),
        )
        return cls(
            team_id=team_id,
            robot_names=robot_names,
            opponent_robot_names=opponent_robot_names,
            control_hz=_parse_float(env.get("SOCCER_CONTROL_HZ"), 30.0),
            game_controller_topic=env.get(
                "SOCCER_GAME_CONTROLLER_TOPIC",
                "/soccer/game_controller",
            ),
        )


def _default_opponent_robot_names(team_id: int) -> tuple[str, ...]:
    if team_id == 1:
        return ("robot4", "robot5", "robot6")
    return ("robot1", "robot2", "robot3")


def _parse_robot_names(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None or not value.strip():
        return default
    names = tuple(_normalize(item) for item in value.split(",") if item.strip())
    return names if names else default


def _normalize(value: str) -> str:
    v = value.strip()
    if v.lower() in {"default", "<default>", "none", "null"}:
        return ""
    return v


def _parse_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    return int(value)


def _parse_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    return float(value)

"""ROS 真值数据源:订阅仿真真值 + 裁判机,提供 WorldSnapshot。

【平台层,Docker-only】依赖 rclpy / geometry_msgs / std_msgs,只在装有 ROS 的运行
环境导入。逻辑层(runtime / player / types)不 import 本模块,靠 runtime 的
ContextSource 注入解耦——因此 runtime 仍可在开发机无 ROS 单测。

订阅(队伍视角坐标,坐标翻转由仿真器侧完成,这里透传):
- 队友 pose:  /team{id}/{robot_name}/soccer/sim/ground_truth/robot_pose
- 对手 pose:  同上,用 opponent_robot_names
- 球:        /team{id}/soccer/sim/ground_truth/ball
- 裁判机:    game_controller_topic(std_msgs/String JSON)

节点 + SingleThreadedExecutor + spin 线程的生命周期从旧 SoccerRosAdapter 移植而来。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import rclpy
from geometry_msgs.msg import Pose2D as RosPose2D
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String as RosString

from .config import SoccerConfig
from .game_codec import game_control_state_from_json
from .types import BallState, GameControlState, Pose2D, RobotState, WorldSnapshot


__all__ = ["RosContextSource"]


_log = logging.getLogger(__name__)


class RosContextSource:
    """拥有 ROS 节点/订阅/executor,把最新真值汇成 WorldSnapshot。

    实现 runtime 的 ContextSource 协议:``start`` / ``stop`` / ``get_snapshot``。
    所有存储对象是 frozen dataclass(不可变),``get_snapshot`` 只需浅拷贝字典,
    无需深拷贝。
    """

    def __init__(self, config: SoccerConfig) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._subscriptions: list[Any] = []

        self._teammates: dict[int, RobotState] = {
            pid: RobotState(player_id=pid) for pid in config.player_ids
        }
        self._opponents: dict[int, RobotState] = {
            pid: RobotState(player_id=pid)
            for pid in range(1, len(config.opponent_robot_names) + 1)
        }
        self._ball: BallState | None = None
        self._game: GameControlState | None = None

        # ROS 节点/执行器生命周期
        self._owns_context = False
        self._ros_context: Any = None
        self._node: Any = None
        self._executor: SingleThreadedExecutor | None = None
        self._spin_thread: threading.Thread | None = None
        self._started = False

    # ------------------------------------------------------------------
    # ContextSource 协议
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._node = self._create_node()
        self._create_subscriptions()
        self._start_spin()
        # 复用本 node 发布调试可视化 MarkerArray + Python log(Docker-only)
        from . import debugdraw
        from . import log_publisher
        debugdraw.install(self._node)
        log_publisher.install(self._node)
        _log.info(
            "RosContextSource started: team_id=%d gc_topic=%s",
            self._config.team_id, self._config.game_controller_topic,
        )

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._stop_spin()
        self._destroy_node()

    def get_snapshot(self) -> WorldSnapshot:
        with self._lock:
            return WorldSnapshot(
                game=self._game,
                ball=self._ball,
                teammates=dict(self._teammates),
                opponents=dict(self._opponents),
            )

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------

    def _create_subscriptions(self) -> None:
        truth_qos = self._qos(depth=1)
        for pid, name in enumerate(self._config.robot_names, start=1):
            self._subscriptions.append(
                self._node.create_subscription(
                    RosPose2D,
                    self._robot_topic(name, "soccer/sim/ground_truth/robot_pose"),
                    self._make_pose_cb(self._teammates, pid),
                    truth_qos,
                )
            )
        for pid, name in enumerate(self._config.opponent_robot_names, start=1):
            self._subscriptions.append(
                self._node.create_subscription(
                    RosPose2D,
                    self._robot_topic(name, "soccer/sim/ground_truth/robot_pose"),
                    self._make_pose_cb(self._opponents, pid),
                    truth_qos,
                )
            )
        self._subscriptions.append(
            self._node.create_subscription(
                RosPose2D,
                self._team_topic("soccer/sim/ground_truth/ball"),
                self._ball_cb,
                truth_qos,
            )
        )
        self._subscriptions.append(
            self._node.create_subscription(
                RosString,
                self._config.game_controller_topic,
                self._game_cb,
                self._qos(depth=10),
            )
        )

    def _make_pose_cb(self, store: dict[int, RobotState], player_id: int):
        def callback(msg: Any) -> None:
            pose = Pose2D(x=float(msg.x), y=float(msg.y), theta=float(msg.theta))
            with self._lock:
                store[player_id] = RobotState(
                    player_id=player_id,
                    pose=pose,
                    last_seen_at=time.monotonic(),
                )
        return callback

    def _ball_cb(self, msg: Any) -> None:
        ball = BallState(
            x=float(msg.x),
            y=float(msg.y),
            last_seen_at=time.monotonic(),
            confidence=1.0,
        )
        with self._lock:
            self._ball = ball

    def _game_cb(self, msg: Any) -> None:
        try:
            game = game_control_state_from_json(str(msg.data))
        except ValueError as exc:
            _log.warning("ignore invalid GameController payload: %s", exc)
            return
        # frozen dataclass:通过 replace 打上 last_seen_at
        import dataclasses
        game = dataclasses.replace(game, last_seen_at=time.monotonic())
        with self._lock:
            self._game = game

    # ------------------------------------------------------------------
    # topic 名
    # ------------------------------------------------------------------

    def _robot_topic(self, robot_name: str, suffix: str) -> str:
        if robot_name:
            return self._join(f"team{self._config.team_id}", robot_name, suffix)
        return self._team_topic(suffix)

    def _team_topic(self, suffix: str) -> str:
        return self._join(f"team{self._config.team_id}", suffix)

    @staticmethod
    def _join(*parts: str) -> str:
        clean = [p.strip("/") for p in parts if p.strip("/")]
        return "/" + "/".join(clean)

    def _qos(self, depth: int) -> QoSProfile:
        return QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=depth,
            durability=QoSDurabilityPolicy.VOLATILE,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )

    # ------------------------------------------------------------------
    # 节点 / executor 生命周期(移植自旧 SoccerRosAdapter)
    # ------------------------------------------------------------------

    def _create_node(self) -> Any:
        context = rclpy.get_default_context()
        self._ros_context = context
        if not rclpy.ok(context=context):
            context.init(args=None, initialize_logging=False)
            self._owns_context = True
        return rclpy.create_node("soccer_sim_bridge", context=context)

    def _start_spin(self) -> None:
        self._executor = SingleThreadedExecutor(context=self._ros_context)
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(
            target=self._spin, name="soccer_ros_source_spin", daemon=True,
        )
        self._spin_thread.start()

    def _spin(self) -> None:
        if self._executor is None:
            return
        try:
            self._executor.spin()
        except ExternalShutdownException:
            pass
        except Exception as exc:
            _log.warning("RosContextSource spin failed: %s", exc)

    def _stop_spin(self) -> None:
        if self._executor is not None:
            try:
                self._executor.shutdown()
            except Exception as exc:
                _log.warning("executor shutdown failed: %s", exc)
        if self._spin_thread is not None and self._spin_thread.is_alive():
            self._spin_thread.join(timeout=2.0)
        self._spin_thread = None
        self._executor = None

    def _destroy_node(self) -> None:
        for sub in self._subscriptions:
            try:
                self._node.destroy_subscription(sub)
            except Exception:
                pass
        self._subscriptions.clear()
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception as exc:
                _log.warning("node destroy failed: %s", exc)
        if (
            self._owns_context
            and self._ros_context is not None
            and rclpy.ok(context=self._ros_context)
        ):
            self._ros_context.shutdown()
        self._node = None

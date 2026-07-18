"""BoosterRobot SDK 包装:每个 player 一个 backend handle。

【平台层,Docker-only】依赖 boosteros.robots.booster,只在装有 SDK 的运行环境
导入。Player 通过 ``_backend`` 调底盘/踢球/慢操作,但 Player 本身(player.py)不
import 本模块——runtime 构造时由 agent 注入,保持 player.py 平台无关。

SDK 方法名对齐旧代码经过验证的调用。

慢操作(request_mode / get_up)是秒级同步 SDK 调用,放到每 backend 一个 worker
线程执行,主循环非阻塞。意图队列长度 1(覆盖式):连续请求只保留最后一个。

mode 由用户经 request_mode 管理(见 docs/new_design.md §5);set_velocity / kick
只在 ``_mode == "walk"`` 时下发,否则跳过——避免非 walk 模式下 SDK 返回 400 刷屏。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, cast

from boosteros.robots.booster import BoosterRobot, SoccerKickManager


__all__ = ["RobotBackend"]


_log = logging.getLogger(__name__)

_GET_UP_THROTTLE_SEC = 1.0


class RobotBackend:
    """单个球员的 SDK 包装 + 慢操作 worker 线程。

    生命周期:mixin 创建并注入到 Player,runtime stop 时统一 close(会停 worker)。
    """

    def __init__(self, player_id: int, robot_name: str) -> None:
        self._player_id = player_id
        self._robot_name = robot_name
        self._robot = BoosterRobot(
            virtual_robot_name=robot_name,
            enable_tf_listener=False,
            timeout=10.0,
        )
        self._kick_manager = SoccerKickManager(self._robot)
        self._mode: str | None = None   # 已确认的 SDK mode(worker 更新)
        self._fall_down_state: str | None = None
        self._fall_down_recoverable: bool = False
        self._kicking = False

        # 慢操作 worker:长度 1 覆盖式意图槽 + 唤醒事件
        self._pending: tuple[str, object] | None = None
        self._slot_lock = threading.Lock()
        self._wake = threading.Event()
        self._worker_stop = threading.Event()
        self._last_get_up_at = 0.0
        self._worker = threading.Thread(
            target=self._worker_loop,
            name=f"backend_worker_{player_id}",
            daemon=True,
        )
        self._worker.start()

        _log.info(
            "RobotBackend created: player_id=%d robot_name=%s",
            player_id, robot_name,
        )

    def close(self) -> None:
        """释放 SDK 资源:停 worker、停踢球、停车、关闭连接。"""
        self._worker_stop.set()
        self._wake.set()
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
        self.release_kick()
        try:
            self._robot.set_velocity(vx=0.0, vy=0.0, vyaw=0.0)
        except Exception as exc:
            _log.warning(
                "player %d set_velocity(0,0,0) on close failed: %s",
                self._player_id, exc,
            )
        try:
            close_fn = getattr(self._robot, "_close", None)
            if callable(close_fn):
                cast(Callable[[], None], close_fn)()
        except Exception as exc:
            _log.warning("player %d SDK close failed: %s", self._player_id, exc)

    @property
    def mode(self) -> str | None:
        """当前已确认的 SDK mode;worker 完成切换后更新。"""
        return self._mode

    @property
    def fall_down_state(self) -> str | None:
        """当前 SDK 跌倒状态;None 表示未知。"""
        return self._fall_down_state

    @property
    def fall_down_recoverable(self) -> bool:
        return self._fall_down_recoverable

    # ------------------------------------------------------------------
    # 底盘控制(步骤 1)
    # ------------------------------------------------------------------

    def set_velocity(self, vx: float, vy: float, vyaw: float) -> None:
        """底盘速度。踢球时跳过(踢球独占底盘);非 walk 模式跳过(需先 request_mode)。"""
        if self._kicking:
            return
        if self._mode != "walk":
            _log.debug(
                "player %d set_velocity skipped: mode=%s (call request_mode first)",
                self._player_id, self._mode,
            )
            return
        try:
            self._robot.set_velocity(vx=vx, vy=vy, vyaw=vyaw)
        except Exception as exc:
            _log.warning(
                "player %d set_velocity(%.3f,%.3f,%.3f) failed: %s",
                self._player_id, vx, vy, vyaw, exc,
            )

    # ------------------------------------------------------------------
    # 踢球(步骤 2)—— 入参为体坐标系
    # ------------------------------------------------------------------

    def kick(
        self, direction: float, power: float, ball_x: float, ball_y: float,
    ) -> None:
        """启动或更新踢球(体坐标系)。非 walk 模式跳过(需先 request_mode)。"""
        if self._mode != "walk":
            _log.debug(
                "player %d kick skipped: mode=%s (call request_mode first)",
                self._player_id, self._mode,
            )
            return
        try:
            if not self._kicking:
                self._kick_manager.start()
                self._kicking = True
                _log.info("player %d kick started", self._player_id)
            self._kick_manager.update_command(direction=direction, power=power)
            self._kick_manager.update_ball(x=ball_x, y=ball_y)
        except Exception as exc:
            _log.warning("player %d kick failed: %s", self._player_id, exc)
            self._kicking = False

    def release_kick(self) -> None:
        """结束踢球,底盘重新接受 set_velocity。"""
        if not self._kicking:
            return
        try:
            self._kick_manager.stop()
            _log.info("player %d kick released", self._player_id)
        except Exception as exc:
            _log.warning("player %d kick stop failed: %s", self._player_id, exc)
        finally:
            self._kicking = False

    # ------------------------------------------------------------------
    # 慢操作(步骤 3)—— 非阻塞,由 worker 线程执行
    # ------------------------------------------------------------------

    def request_mode(self, mode: str) -> None:
        """异步请求切换 SDK mode。已在目标模式则短路。"""
        if self._mode == mode:
            return
        self._enqueue(("mode", mode))

    def get_up(self) -> None:
        """异步触发起身。~1s 节流,每帧无脑调安全。"""
        now = time.monotonic()
        if now - self._last_get_up_at < _GET_UP_THROTTLE_SEC:
            return
        self._last_get_up_at = now
        self._enqueue(("get_up", None))

    def _enqueue(self, intent: tuple[str, object]) -> None:
        with self._slot_lock:
            self._pending = intent   # 覆盖式:只保留最后一个
        self._wake.set()

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            self._wake.wait(timeout=0.5)
            if self._worker_stop.is_set():
                break
            self._wake.clear()
            # 轮询真实 SDK 模式,保持 _mode 反映现实(而非乐观缓存)。
            # 比赛重启后仿真会把机器人重置出 walk 模式;轮询让 _mode 跟着变,
            # 上层 ensure_ready 看到 p.mode != "walk" 会自动重新 request_mode 自愈。
            self._poll_mode()
            self._poll_fall_down_state()
            with self._slot_lock:
                intent = self._pending
                self._pending = None
            if intent is None:
                continue
            kind, arg = intent
            if kind == "mode":
                self._exec_set_mode(cast(str, arg))
            elif kind == "get_up":
                self._exec_get_up()

    def _poll_mode(self) -> None:
        try:
            mode = self._robot.get_mode()
        except Exception as exc:
            _log.debug("player %d get_mode failed: %s", self._player_id, exc)
            return
        if isinstance(mode, str):
            self._mode = mode
            if mode == "walk":
                self._fall_down_state = "normal"
                self._fall_down_recoverable = False

    def _poll_fall_down_state(self) -> None:
        if self._mode == "walk":
            return
        try:
            fall_down_state = self._robot.get_fall_down_state()
        except Exception as exc:
            _log.debug(
                "player %d get_fall_down_state failed: %s", self._player_id, exc,
            )
            return
        state_value = getattr(fall_down_state, "state", None)
        recoverable_value = getattr(fall_down_state, "recoverable", False)
        self._fall_down_state = state_value if isinstance(state_value, str) else None
        self._fall_down_recoverable = (
            recoverable_value if isinstance(recoverable_value, bool) else False
        )

    def _exec_set_mode(self, mode: str) -> None:
        try:
            self._robot.set_gait("soccer")
            self._robot.set_mode(mode)
            self._mode = mode   # 乐观即时反馈;下次 _poll_mode 会用实际值校正
            _log.info("player %d entered %s mode", self._player_id, mode)
        except Exception as exc:
            _log.warning("player %d set_mode(%s) failed: %s", self._player_id, mode, exc)

    def _exec_get_up(self) -> None:
        try:
            self._robot.get_up()
            self._mode = None   # 起身后 mode 未知,需重新 request_mode
            self._fall_down_state = None
            self._fall_down_recoverable = False
            _log.info("player %d get_up done", self._player_id)
        except Exception as exc:
            _log.warning("player %d get_up failed: %s", self._player_id, exc)

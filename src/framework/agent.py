"""SoccerAgent 框架 mixin:用户入口类的框架行为来源。

平台约束:Booster 构建校验只认入口类的【直接基类】是否为
``booster_agent_framework.AgentBase``,不追溯多层继承。因此框架不能提供一个
``SoccerAgent(AgentBase)`` 让用户单继承——那样用户类的直接基类是 SoccerAgent,
校验不过。

解法:框架行为放在【不继承 AgentBase】的 mixin 里,用户入口类写成
``class MyAgent(SoccerAgentMixin, AgentBase)``,让 AgentBase 成为直接基类之一。

详细 API 见 docs/new_design.md 第 8 节。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from booster_agent_framework import AgentFeatures

from .config import SoccerConfig
from .runtime import SoccerRuntime

if TYPE_CHECKING:
    from types import SimpleNamespace

    from ..player import Player
    from .types import Context


__all__ = ["SoccerAgentMixin"]


_log = logging.getLogger(__name__)


class _PlatformLogHandler(logging.Handler):
    """把 Python 标准 logging 记录转发到 Booster 平台 logger(``self.logger``)。

    平台 logger 是 rclcpp 风格,只有 ``.info/.warn/.error(msg: str)``。标准
    logging 默认无 handler、INFO 被丢弃,所以框架和用户代码的日志都看不到;装上
    这个桥后,``logging.getLogger(__name__).info(...)`` 会正确路由到控制台/日志文件。

    平台耦合只集中在这里;runtime / player 等仍用平台无关的标准 logging。
    """

    def __init__(self, platform_logger: object) -> None:
        super().__init__()
        self._platform = platform_logger

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if record.levelno >= logging.ERROR:
                self._platform.error(msg)  # type: ignore[attr-defined]
            elif record.levelno >= logging.WARNING:
                warn = getattr(self._platform, "warn", None) or getattr(
                    self._platform, "warning", None
                )
                (warn or self._platform.info)(msg)  # type: ignore[attr-defined]
            else:
                self._platform.info(msg)  # type: ignore[attr-defined]
        except Exception:
            self.handleError(record)


class SoccerAgentMixin:
    """框架行为 mixin;不继承 AgentBase,须与 AgentBase 组合。

    用法::

        from booster_agent_framework import AgentBase
        from .soccer.agent import SoccerAgentMixin

        class MyAgent(SoccerAgentMixin, AgentBase):
            player_class = MyPlayer

            @staticmethod
            def play(context, players, store): ...

            def init_store(self, store): ...

    MRO 为 ``[MyAgent, SoccerAgentMixin, AgentBase, object]``,``super().__init__``
    会正确走到 ``AgentBase.__init__``。
    """

    # ------------------------------------------------------------------
    # 用户填的槽
    # ------------------------------------------------------------------

    # 槽 1:Player 类。main.py 必须设置 ``player_class = Player``(从 src.player 导入)。
    #       框架不 import 用户的 player.py,依赖注入保持依赖向下。
    player_class: "type[Player]"

    # 槽 2:play —— 每帧调用(默认 no-op)
    @staticmethod
    def play(
        context: "Context",
        players: "list[Player]",
        store: "SimpleNamespace",
    ) -> None:
        """30Hz 调用。默认啥都不做;子类按需 override。"""

    # 槽 3:init_store —— 开赛前调一次(可选)
    def init_store(self, store: "SimpleNamespace") -> None:
        """默认 no-op;子类按需 override。"""

    # ------------------------------------------------------------------
    # 框架内部 —— 用户通常不改
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        # 走 MRO 到 AgentBase.__init__(AgentFeatures())
        super().__init__(AgentFeatures())  # type: ignore[call-arg]
        self._setup_logging()
        self.config = SoccerConfig.from_env()
        # ROS 数据源(Docker-only);延迟 import,避免开发机无 rclpy 时污染
        from .ros_source import RosContextSource

        source = RosContextSource(self.config)
        self.runtime = SoccerRuntime(self, context_source=source)
        # 为每个 player 创建 backend(SDK 包装)并注入
        self._create_backends()
        _log.info(
            "SoccerAgent initialized: team_id=%d robots=%s",
            self.config.team_id, list(self.config.robot_names),
        )

    def _create_backends(self) -> None:
        """为每个 player 创建 SDK backend。Docker-only,延迟 import。"""
        from .robot_backend import RobotBackend

        for player in self.runtime._players:
            robot_name = self.config.robot_names[player.id - 1]
            player._backend = RobotBackend(player.id, robot_name)

    def _setup_logging(self) -> None:
        """把标准 logging 桥接到平台 logger,让框架和用户日志可见。

        ``self.logger`` 由 AgentBase 在 ``super().__init__`` 后提供。桥只装一次;
        重复激活时先清掉旧桥避免重复输出。
        """

        platform_logger = getattr(self, "logger", None)
        if platform_logger is None:
            # 没有平台 logger(理论上不该发生):退回 stderr,std 流已被框架重定向。
            logging.basicConfig(level=logging.INFO)
            return
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        for handler in list(root.handlers):
            if isinstance(handler, _PlatformLogHandler):
                root.removeHandler(handler)
        bridge = _PlatformLogHandler(platform_logger)
        bridge.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root.addHandler(bridge)

    def on_agent_activated(self) -> None:
        _log.info("SoccerAgent activated")
        self.runtime.start()

    def on_agent_close(self) -> None:
        _log.info("SoccerAgent closing")
        self.runtime.stop()

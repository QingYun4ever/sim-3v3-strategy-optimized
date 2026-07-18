"""Python logging → ROS topic 桥接 —— 把 agent 日志发到 ROS,可录 rosbag 回放调试。

框架侧:ros_source 在 node 就绪后调 install(node),把 handler 装到 root logger。
开发机无 ROS 时不装,log 继续走平台 logger,不影响。

话题:/soccer/agent_log,类型 rcl_interfaces/msg/Log(ROS 标准 log 消息,Studio 原生支持)。
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_TOPIC = "/soccer/agent_log"


def install(node) -> None:
    """把 ROS log publisher 装到 Python root logger(Docker-only)。"""
    try:
        handler = _RosLogHandler(node)
        # 装到 root logger,捕获所有模块;级别继承 root(默认 INFO)
        logging.getLogger().addHandler(handler)
        _log.info("log_publisher installed, publishing to %s", _TOPIC)
    except Exception as exc:
        _log.warning("log_publisher install failed (ROS log disabled): %s", exc)


# logging level → ROS Log 常量映射
_LEVEL_MAP = {
    logging.DEBUG: 10,     # Log.DEBUG
    logging.INFO: 20,      # Log.INFO
    logging.WARNING: 30,   # Log.WARN
    logging.ERROR: 40,     # Log.ERROR
    logging.CRITICAL: 50,  # Log.FATAL
}


class _RosLogHandler(logging.Handler):
    """logging.Handler:把 LogRecord 转成 rcl_interfaces/msg/Log 发到 /rosout。"""

    def __init__(self, node) -> None:
        super().__init__()
        from rcl_interfaces.msg import Log
        from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy

        # /rosout 标准 QoS:TRANSIENT_LOCAL + RELIABLE,history=1000
        qos = QoSProfile(
            depth=1000,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )
        self._node = node
        self._pub = node.create_publisher(Log, _TOPIC, qos)

    def emit(self, record: logging.LogRecord) -> None:
        """转成 ROS Log 消息发到 /rosout。异常不能再 log(递归),直接吞。"""
        try:
            from rcl_interfaces.msg import Log

            msg = Log()
            msg.stamp = self._node.get_clock().now().to_msg()
            msg.level = _LEVEL_MAP.get(record.levelno, 20)  # 默认 INFO
            msg.name = record.name
            msg.msg = record.getMessage()
            msg.file = record.pathname
            msg.function = record.funcName
            msg.line = record.lineno
            self._pub.publish(msg)
        except Exception:
            pass  # 不能再调 logging(递归),直接吞异常

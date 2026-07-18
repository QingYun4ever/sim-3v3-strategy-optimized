"""框架层 —— 用户几乎不改的平台管线。

包含:数据契约(types)、配置(config)、运行时主循环(runtime)、SDK 包装
(robot_backend)、ROS 数据源(ros_source)、裁判机解码(game_codec)、Agent
入口 mixin(agent)。

按需从子模块直接 import(如 ``from .framework.types import Context``),__init__
不做 eager import,避免在无 SDK 的开发机上因 agent/robot_backend/ros_source 依赖
SDK 而 import 失败。
"""

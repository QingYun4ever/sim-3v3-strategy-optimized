"""调试可视化汇聚层 —— 策略随手画,框架统一发 ROS MarkerArray。

用法(策略侧,零 ROS 依赖):
    from .framework import debugdraw
    debugdraw.point(x, y, rgb=(1,0,0), ns="target")
    debugdraw.arrow(x0, y0, x1, y1, rgb=(0,1,0), ns="heading")
    debugdraw.line([(x0,y0),(x1,y1)], ns="path")
    debugdraw.text(x, y, "chaser", ns="label")

框架侧:agent 在 ROS node 就绪后调 install(node);runtime 每帧 begin_frame()→...→flush()。
没装(开发机无 ROS / 未 install)时所有绘制调用是 no-op,可安全在任意环境 import。

坐标系:队伍视角场地系(与 context 一致)。Marker 的 frame_id 默认 "world";在
Booster Studio / RViz 里把 Fixed Frame 设成同名即可看到。我们同时把球/机器人也画出来,
所以即使没有外部 TF,这套 marker 自成一致的俯视图。
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_FRAME = "world"          # Marker frame_id;Studio 的 Fixed Frame 设成同名
_TOPIC = "/soccer/debug"
_Z = 0.05                 # 画在地面略上方

_impl = None              # 由 install() 注入;None = no-op


def install(node) -> None:
    """框架注入真实 ROS 发布器(Docker-only)。开发机不调 → 全程 no-op。"""
    global _impl
    try:
        _impl = _RosDrawSink(node)
        _log.info("debugdraw installed, publishing MarkerArray on %s", _TOPIC)
    except Exception as exc:
        _impl = None
        _log.warning("debugdraw install failed (viz disabled): %s", exc)


def begin_frame() -> None:
    if _impl is not None:
        _impl.begin()


def flush() -> None:
    if _impl is not None:
        _impl.flush()


def point(x, y, rgb=(1.0, 1.0, 1.0), scale=0.12, ns="point") -> None:
    if _impl is not None:
        _impl.point(x, y, rgb, scale, ns)


def cube(x, y, rgb=(1.0, 1.0, 1.0), scale=0.12, ns="cube") -> None:
    if _impl is not None:
        _impl.cube(x, y, rgb, scale, ns)


def arrow(x0, y0, x1, y1, rgb=(1.0, 1.0, 0.0), ns="arrow") -> None:
    if _impl is not None:
        _impl.arrow(x0, y0, x1, y1, rgb, ns)


def line(points, rgb=(0.5, 0.5, 0.5), ns="line") -> None:
    """points: [(x,y), ...] 折线。"""
    if _impl is not None and len(points) >= 2:
        _impl.line(points, rgb, ns)


def text(x, y, s, rgb=(1.0, 1.0, 1.0), ns="text") -> None:
    if _impl is not None:
        _impl.text(x, y, s, rgb, ns)


class _RosDrawSink:
    """真实实现:累积本帧 marker,flush 时发一个 MarkerArray(先 DELETEALL 清旧)。"""

    def __init__(self, node) -> None:
        # 延迟 import,避免开发机无 ROS 时污染
        from visualization_msgs.msg import MarkerArray

        self._node = node
        self._pub = node.create_publisher(MarkerArray, _TOPIC, 1)
        self._markers: list = []
        self._next_id = 0

    def begin(self) -> None:
        self._markers = []
        self._next_id = 0

    def flush(self) -> None:
        from visualization_msgs.msg import Marker, MarkerArray

        arr = MarkerArray()
        clear = Marker()
        clear.action = Marker.DELETEALL
        arr.markers.append(clear)
        arr.markers.extend(self._markers)
        self._pub.publish(arr)

    # -- 各图元 --

    def _new(self, ns, mtype):
        from visualization_msgs.msg import Marker

        m = Marker()
        m.header.frame_id = _FRAME
        m.header.stamp = self._node.get_clock().now().to_msg()
        m.ns = ns
        m.id = self._next_id
        self._next_id += 1
        m.type = mtype
        m.action = Marker.ADD
        m.pose.orientation.w = 1.0
        return m

    @staticmethod
    def _rgba(m, rgb):
        m.color.r, m.color.g, m.color.b = float(rgb[0]), float(rgb[1]), float(rgb[2])
        m.color.a = 1.0

    def point(self, x, y, rgb, scale, ns) -> None:
        from visualization_msgs.msg import Marker

        m = self._new(ns, Marker.SPHERE)
        m.pose.position.x, m.pose.position.y, m.pose.position.z = float(x), float(y), _Z
        m.scale.x = m.scale.y = m.scale.z = float(scale)
        self._rgba(m, rgb)
        self._markers.append(m)

    def cube(self, x, y, rgb, scale, ns) -> None:
        from visualization_msgs.msg import Marker

        m = self._new(ns, Marker.CUBE)
        m.pose.position.x, m.pose.position.y, m.pose.position.z = float(x), float(y), _Z
        m.scale.x = m.scale.y = m.scale.z = float(scale)
        self._rgba(m, rgb)
        self._markers.append(m)

    def arrow(self, x0, y0, x1, y1, rgb, ns) -> None:
        from geometry_msgs.msg import Point
        from visualization_msgs.msg import Marker

        m = self._new(ns, Marker.ARROW)
        m.points = [
            Point(x=float(x0), y=float(y0), z=_Z),
            Point(x=float(x1), y=float(y1), z=_Z),
        ]
        m.scale.x = 0.03   # 杆径
        m.scale.y = 0.08   # 箭头宽
        m.scale.z = 0.12   # 箭头长
        self._rgba(m, rgb)
        self._markers.append(m)

    def line(self, points, rgb, ns) -> None:
        from geometry_msgs.msg import Point
        from visualization_msgs.msg import Marker

        m = self._new(ns, Marker.LINE_STRIP)
        m.points = [Point(x=float(px), y=float(py), z=_Z) for px, py in points]
        m.scale.x = 0.02   # 线宽
        self._rgba(m, rgb)
        self._markers.append(m)

    def text(self, x, y, s, rgb, ns) -> None:
        from visualization_msgs.msg import Marker

        m = self._new(ns, Marker.TEXT_VIEW_FACING)
        m.pose.position.x, m.pose.position.y, m.pose.position.z = float(x), float(y), 0.3
        m.scale.z = 0.25   # 字高
        self._rgba(m, rgb)
        m.text = str(s)
        self._markers.append(m)

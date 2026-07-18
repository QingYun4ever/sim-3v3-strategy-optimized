"""SoccerSim 策略入口 —— 比赛策略主逻辑都在这里,改打法就改这个文件。

结构(由浅入深):
- main.py(本文件):比赛策略。play() 按 Phase 状态机分派到 _act_*;各 _act_* 选出
  attacker(离球最近)并直接调 player 动作。
- player.py:Player 控制 handle + 高层动作(attack / take_kickoff /
  move_to_position / walk_to);想加拐棍/技术动作直接改它。
- utils/:走位/几何/避障工具(opponent_goal / dist / angle_to ...)。
- framework/:平台管线,用户不改。

改打法主要改本文件:Phase 状态机、各 _act_* 行为、站位公式。
"""

from __future__ import annotations

import logging
import math
from enum import Enum

from booster_agent_framework import AgentBase

from .framework.agent import SoccerAgentMixin
from .framework.types import KICKING_TEAM_NONE, Context, GameState, SetPlay
from .param import *
from .player import Player
from .utils import dist, opponent_goal, own_goal


_log = logging.getLogger(__name__)


# ======================================================================
# Phase 状态机 —— 比赛阶段分类
# ======================================================================


class Phase(Enum):
    """比赛阶段。顶层状态机,决定当前是正常拼抢/开球/定位球/准备/停止。"""
    NORMAL = "normal"              # PLAYING 正常拼抢
    OUR_KICKOFF = "our_kickoff"    # 我方开球(SET+PLAYING 初期,take_kickoff)
    OPP_KICKOFF = "opp_kickoff"    # 对方开球(避让)
    OUR_SET_PLAY = "our_set_play"  # 我方定位球(任意球/角球/球门球)
    OPP_SET_PLAY = "opp_set_play"  # 对方定位球(避让)
    READY = "ready"                # READY 走位
    STOPPED = "stopped"            # SET(非开球重开) / INITIAL / FINISHED / stopped


def get_phase(context: Context) -> Phase:
    """根据裁判机状态判断当前比赛阶段。"""
    g = context.game
    if g is None:
        return Phase.STOPPED

    state = g.state

    # READY:走 ready 位
    if state == GameState.READY:
        return Phase.READY

    # PLAYING:正常拼抢 or 开球/定位球执行中
    if state == GameState.PLAYING and not g.stopped:
        # 定位球:set_play != NONE,kicking_team 指示哪方
        if g.set_play != SetPlay.NONE and g.kicking_team != KICKING_TEAM_NONE:
            our_team = context.team_id
            if g.kicking_team == our_team:
                return Phase.OUR_SET_PLAY
            else:
                return Phase.OPP_SET_PLAY

        # 开球:secondary_time > 0(倒计时窗口),kicking_team 指示哪方
        if g.secondary_time > 0 and g.kicking_team != KICKING_TEAM_NONE:
            our_team = context.team_id
            if g.kicking_team == our_team:
                return Phase.OUR_KICKOFF
            else:
                return Phase.OPP_KICKOFF

        # 正常拼抢
        return Phase.NORMAL

    # SET / INITIAL / FINISHED / stopped:站定
    return Phase.STOPPED

def get_set_play_type(context: Context) -> SetPlay:
    """当前生效的定位球类型;无定位球(或无裁判机数据)时返回 ``SetPlay.NONE``。

    直接读裁判机的 ``set_play`` 字段,不区分是哪方主罚 —— 哪方由 :func:`get_phase`
    (OUR_SET_PLAY / OPP_SET_PLAY)判定。这里只回答"是什么类型的定位球"。

    共 7 种可能返回值(见 framework.types.SetPlay):
    - ``NONE``:无定位球(正常比赛/开球等)
    - ``DIRECT_FREE_KICK``:直接任意球(可直接射门得分)
    - ``INDIRECT_FREE_KICK``:间接任意球(须先触碰他人才能进球)
    - ``PENALTY_KICK``:点球
    - ``THROW_IN``:界外球(踢入)
    - ``GOAL_KICK``:球门球
    - ``CORNER_KICK``:角球
    """
    g = context.game
    if g is None:
        return SetPlay.NONE
    return g.set_play


# ======================================================================
# Agent 入口
# ======================================================================


class SoccerSimAgent(SoccerAgentMixin, AgentBase):
    """3v3 SoccerSim agent。"""

    player_class = Player

    def init_store(self, store) -> None:
        _log.info("init_store called")
        store.prev_phase = None       # 上一帧 phase,用于检测 phase 跳变(边沿)
        store.cur_phase = None
        store.kickoff_taker = None    # 锁定的开球主罚球员 id(每次进入开球时重选)
        store.normal_attacker = None
        store.last_ball_position = None
        store.last_ball_seen_at = None
        store.ball_visible_frames = 0
        store.ball_searcher = None
        store.ball_lost_since = None

    @staticmethod
    def play(context: Context, players: list[Player], store) -> None:
        phase = get_phase(context)
        store.prev_phase = store.cur_phase
        store.cur_phase = phase

        # 画可视化(每帧)
        _analyze_and_draw(context, players, store)

        # 当前 phase 以 label 画在场外。
        from .framework import debugdraw
        g = context.game
        game_state = g.state.value if g is not None else "none"
        set_play = g.set_play.value if g is not None else "none"
        secondary_time = g.secondary_time if g is not None else 0.0
        debugdraw.text(
            0.0, context.field.width / 2.0 + 0.2,
            f"phase={phase.value} state={game_state} set={set_play} secondary={secondary_time:.1f}",
            rgb=(1.0, 1.0, 0.0), ns="phase",
        )

        # 活性自理 + 过滤出本帧可行动的球员。
        # ensure_ready:摔倒起身 / 切 walk 模式(异步,不产生移动);被罚下的也做,
        # 这样解罚后能立刻投入。被罚下或未就绪的不参与分派(也不进角色分配,避免把
        # 动不了的人选成 attacker 导致该帧无人进攻)。
        active: list[Player] = []
        for p in players:
            ready = p.ensure_ready()
            if p.is_penalized:
                p.action = "penalized"     # 罚下:可起身/切模式,但不能移动
                p.stop()
            elif not ready:
                p.action = "fallen" if p.is_fallen else "switching_mode"
            elif p.pose is None:
                p.action = "no_pose"       # 自己位置未知:不参与分派(下游按 pose 已知处理)
                p.stop()
            else:
                active.append(p)

        # 按 phase 对整队分派一次(角色分配等全队计算只在 _act_* 里算一次)。
        if phase == Phase.NORMAL:
            _act_normal(context, active, store)
        elif phase == Phase.OUR_KICKOFF:
            _clear_normal_sticky(store)
            _act_our_kickoff(context, active, store)
        elif phase == Phase.OPP_KICKOFF:
            _clear_normal_sticky(store)
            _act_opp_kickoff(context, active)
        elif phase == Phase.OUR_SET_PLAY:
            _clear_normal_sticky(store)
            _act_our_set_play(context, active, store)
        elif phase == Phase.OPP_SET_PLAY:
            _clear_normal_sticky(store)
            _act_opp_set_play(context, active, store)
        elif phase == Phase.READY:
            _clear_normal_sticky(store)
            _act_ready(context, active)
        elif phase == Phase.STOPPED:
            _clear_normal_sticky(store)
            for p in active:
                p.action = "stopped"
                p.stop()

        # 队员可视化统一在最后画一遍:覆盖所有球员(含判罚/未就绪/STOPPED),
        # 修复 SET 等状态下红球/标签消失的问题。
        for p in players:
            _draw_teammate_marker(p)


def _clear_normal_sticky(store) -> None:
    store.normal_attacker = None
    store.last_ball_position = None
    store.last_ball_seen_at = None
    store.ball_visible_frames = 0
    store.ball_searcher = None
    store.ball_lost_since = None


def _player_dist_to_ball(context: Context, p: Player) -> float:
    """球员到球当前位置的距离。"""
    ball = context.ball
    return (
        dist(p.pose.x, p.pose.y, ball.x, ball.y) + _fallen_time_cost(p)
        if ball is not None else math.inf
    )


def _fallen_time_cost(p: Player) -> float:
    return FALLEN_COST if p.is_fallen else 0.0


def _select_closest_attacker(
    context: Context,
    players: list[Player],
    preferred_id: int | None = None,
) -> Player:
    """选到球距离最小的球员。

    ``players`` 非空、已就绪、pose 已知。normal 与开球共用。
    """
    ranked = [(p, _player_dist_to_ball(context, p)) for p in players]
    best, best_dist = min(ranked, key=lambda item: item[1])
    preferred = next((item for item in ranked if item[0].id == preferred_id), None)
    if (
        preferred is not None
        and preferred[1] <= best_dist + ATTACKER_KEEP_DIST_MARGIN_M
    ):
        return preferred[0]
    return best


def _act_normal(
    context: Context,
    players: list[Player],
    store,
    *,
    allow_ball_search: bool = True,
) -> None:
    """NORMAL:距球最近者 attack,剩下人里离己方门最近者 guard,其余 support。

    ``players`` 是本帧可行动球员(已就绪、pose 已知),这里直接挑角色并执行。
    """
    if not players:
        return

    ball_confirmed = (
        _update_ball_recovery_state(context, store)
        if allow_ball_search else context.ball is not None
    )
    if not ball_confirmed:
        if allow_ball_search:
            _act_ball_recovery(context, players, store)
        else:
            for player in players:
                player.action = "ball_unknown:stop"
                player.stop()
        return

    attacker = _select_closest_attacker(
        context, players, getattr(store, "normal_attacker", None),
    )
    store.normal_attacker = attacker.id
    attacker.action = "attack"
    attacker.attack()

    # guard:剩下人里离己方门最近者(如还有人)
    rest = [p for p in players if p is not attacker]
    if rest:
        gx, gy = own_goal(context)
        guard = min(rest, key=lambda p: dist(p.pose.x, p.pose.y, gx, gy))
        guard.guard()  
        rest = [p for p in rest if p is not guard]

    # support:其余全部
    for p in rest:
        p.action = "support"
        p.support()


def _update_ball_recovery_state(context: Context, store) -> bool:
    """更新球记忆,返回本帧是否已满足恢复正常策略的确认条件。"""
    ball = context.ball
    if ball is None:
        store.ball_visible_frames = 0
        if store.ball_lost_since is None:
            store.ball_lost_since = context.now
        return False

    store.last_ball_position = (ball.x, ball.y)
    store.last_ball_seen_at = ball.last_seen_at
    store.ball_visible_frames = min(
        store.ball_visible_frames + 1,
        BALL_REACQUIRE_FRAMES,
    )
    if store.ball_visible_frames < BALL_REACQUIRE_FRAMES:
        return False

    store.ball_lost_since = None
    store.ball_searcher = None
    return True


def _act_ball_recovery(context: Context, players: list[Player], store) -> None:
    """NORMAL 丢球恢复:一人定向扫场,其余保持守位或停止。"""
    active_ids = {player.id for player in players}
    preferred_searcher = getattr(store, "ball_searcher", None)
    if preferred_searcher not in active_ids:
        preferred_searcher = getattr(store, "normal_attacker", None)
    if preferred_searcher not in active_ids:
        preferred_searcher = min(player.id for player in players)
    store.ball_searcher = preferred_searcher

    searcher = next(
        player for player in players if player.id == preferred_searcher
    )
    last_ball_position = getattr(store, "last_ball_position", None)
    last_ball_seen_at = getattr(store, "last_ball_seen_at", None)
    memory_is_fresh = (
        last_ball_position is not None
        and last_ball_seen_at is not None
        and context.now - last_ball_seen_at <= BALL_LAST_SEEN_MEMORY_SEC
    )
    search_target = last_ball_position if memory_is_fresh else None

    lost_since = getattr(store, "ball_lost_since", None)
    if lost_since is None:
        lost_since = context.now
        store.ball_lost_since = lost_since
    sweep_index = int(
        max(0.0, context.now - lost_since) / max(BALL_SEARCH_SWEEP_SEC, 1e-6)
    )
    base_direction = 1.0 if searcher.id % 2 == 0 else -1.0
    sweep_direction = base_direction if sweep_index % 2 == 0 else -base_direction

    searcher.action = (
        "ball_search:last_seen" if search_target is not None
        else "ball_search:sweep"
    )
    searcher.search_for_ball(search_target, sweep_direction)

    rest = [player for player in players if player is not searcher]
    if rest:
        goal_x, goal_y = own_goal(context)
        guard = min(
            rest,
            key=lambda player: dist(
                player.pose.x, player.pose.y, goal_x, goal_y,
            ),
        )
        guard.guard()
        rest = [player for player in rest if player is not guard]

    for player in rest:
        player.action = "ball_unknown:hold"
        player.stop()


def _act_our_kickoff(context: Context, players: list[Player], store) -> None:
    """OUR_KICKOFF:锁定距离最小者开球,剩下人里离己方门最近者 guard,其余 support。"""
    if not players:
        return

    active_ids = {p.id for p in players}
    if store.prev_phase != Phase.OUR_KICKOFF or store.kickoff_taker not in active_ids:
        # 进入开球阶段，重新选择开球球员
        store.kickoff_taker = _select_closest_attacker(context, players).id

    attacker_id = store.kickoff_taker
    attacker = next((p for p in players if p.id == attacker_id), None)
    if attacker is None:
        return

    attacker.action = "kickoff"
    attacker.kick(0.1, KICK_POWER_OUR_KICKOFF)

    rest = [p for p in players if p is not attacker]
    if rest:
        gx, gy = own_goal(context)
        guard = min(rest, key=lambda p: dist(p.pose.x, p.pose.y, gx, gy))
        guard.guard()
        rest = [p for p in rest if p is not guard]

    for p in rest:
        p.action = "stay"
        p.stop()


def _act_opp_kickoff(context: Context, players: list[Player]) -> None:
    """对方开球:一人守门,其余人站到中圈外固定点等待。"""
    if not players:
        return
    gx, gy = own_goal(context)
    guard = min(players, key=lambda p: dist(p.pose.x, p.pose.y, gx, gy))
    guard.guard()

    rest = [p for p in players if p is not guard]
    r = context.field.circle_radius
    slots = [(-r - 0.5, 0.0), (-r - 2.0, 0.5)]
    for p, target in zip(rest, slots):
        p.action = "opp_kickoff:ready"
        p.walk_to(target, avoid_ball=True, avoid_robots=True)


def _act_our_set_play(context: Context, players: list[Player], store) -> None:
    """OUR_SET_PLAY:按定位球类型分派, TODO：加入自己的逻辑。默认为 _act_normal"""
    set_play = get_set_play_type(context)
    if set_play == SetPlay.THROW_IN:
        _act_normal(context, players, store, allow_ball_search=False)
        return
    if set_play == SetPlay.CORNER_KICK:
        _act_normal(context, players, store, allow_ball_search=False)
        return
    if set_play == SetPlay.GOAL_KICK:
        _act_normal(context, players, store, allow_ball_search=False)
        return
    _act_normal(context, players, store, allow_ball_search=False)


def _act_opp_set_play(context: Context, players: list[Player], store) -> None:
    """对方开球： TODO: 实现自己的逻辑。默认与 Normal 相同"""
    _act_normal(context, players, store, allow_ball_search=False)


def _act_ready(context: Context, players: list[Player]) -> None:
    """READY:各自走 ready 位。"""
    game = context.game
    our_kickoff = game is not None and game.kicking_team == context.team_id
    field = context.field
    # 我方开球：优先级站位：(-中圈半径, 0), (我方 goal area 中心点，0)， （0， 中圈半径）
    if our_kickoff:
        if len(players) >= 1:
            p1 = players[0]
            p1.action = "ready"
            p1.walk_to(
                (-field.circle_radius, 0.0),
                face=0.0,
                avoid_ball=True,
                avoid_robots=True,
            )
        if len(players) >= 2:
            p2 = players[1]
            p2.action = "ready"
            p2.walk_to(
                (-field.length / 2.0 + field.goal_area_length, 0.0),
                face=0.0,
                avoid_ball=True,
                avoid_robots=True,
            )
        if len(players) >= 3:
            p3 = players[2]
            p3.action = "ready"
            p3.walk_to(
                (-0.5, field.circle_radius + 2),
                face=0.0,
                avoid_ball=True,
                avoid_robots=True,
            )
    # 对方开球：优先级站位：(-中圈半径 - 0.5, 0), (我方 goal area 中心点，0)， （我方禁区线中心点， 0)
    else:
        if len(players) >= 1:
            p1 = players[0]
            p1.action = "ready"
            p1.walk_to(
                (-field.circle_radius - 0.5, 0.0),
                face=0.0,
                avoid_ball=True,
                avoid_robots=True,
            )
        if len(players) >= 2:
            p2 = players[1]
            p2.action = "ready"
            p2.walk_to(
                (-field.length / 2.0 + field.goal_area_length, 0.0),
                face=0.0,
                avoid_ball=True,
                avoid_robots=True,
            )
        if len(players) >= 3:
            p3 = players[2]
            p3.action = "ready"
            p3.walk_to(
                (-field.length / 2.0 + field.penalty_area_length, 0.0),
                face=0.0,
                avoid_ball=True,
                avoid_robots=True,
            )



# ======================================================================
# 战场可视化 —— 显示球位置 + 球员到球的距离,画到 ROS 可视化
# ======================================================================

def _draw_teammate_marker(p: Player) -> None:
    """我方队员可视化:红色。踢球中→方块,否则→球体。

    每帧对所有球员统一调用(不受 phase/判罚/就绪影响)。标签两行:
    - 上:编号 + 当前高层动作(``p.action``),踢球中追加 ``[KICK]``。
    - 通过形状(方块 vs 球体)再次区分是否进入 kick 状态。
    """
    from .framework import debugdraw

    if p.pose is None:
        return
    red = (1.0, 0.2, 0.2)
    if p.is_kicking:
        debugdraw.cube(p.pose.x, p.pose.y, rgb=red, scale=0.38, ns="teammate")
    else:
        debugdraw.point(p.pose.x, p.pose.y, rgb=red, scale=0.3, ns="teammate")
    kick_tag = " [KICK]" if p.is_kicking else ""
    label = f"{p.id}:{p.action}{kick_tag}"
    debugdraw.text(p.pose.x, p.pose.y, label, rgb=(1.0, 0.9, 0.6), ns="teammate_id")


def _analyze_and_draw(context: Context, players: list[Player], store) -> None:
    """每帧:计算球员到球的距离,画可视化。

    不再依赖 analysis 模块;距离改为基于球当前位置。
    """
    from .framework import debugdraw

    ball = context.ball

    # 球不可见:无可视化
    if ball is None:
        return

    # 1. 画球当前位置(绿色点)
    debugdraw.point(ball.x, ball.y, rgb=(0.0, 1.0, 0.0), scale=0.2, ns="ball_current")

    # 2. 球员到球的距离:我方(红标签)+ 敌方(蓝标签)
    for p in players:
        if p.pose is None:
            continue
        d = dist(p.pose.x, p.pose.y, ball.x, ball.y)
        debugdraw.text(
            p.pose.x + 0.3, p.pose.y - 0.3, f"{d:.1f}m",
            rgb=(1.0, 0.6, 0.6), ns="dist_ours",
        )
    for r in context.opponents.values():
        if r.pose is None:
            continue
        d = dist(r.pose.x, r.pose.y, ball.x, ball.y)
        debugdraw.text(
            r.pose.x + 0.3, r.pose.y - 0.3, f"{d:.1f}m",
            rgb=(0.6, 0.6, 1.0), ns="dist_opp",
        )

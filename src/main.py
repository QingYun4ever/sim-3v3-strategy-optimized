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
from dataclasses import dataclass
from enum import Enum

from booster_agent_framework import AgentBase

from .framework.agent import SoccerAgentMixin
from .framework.types import KICKING_TEAM_NONE, Context, GameState, SetPlay
from .param import *
from .player import Player
from .utils import (
    angle_to,
    clamp,
    dist,
    opponent_goal,
    own_goal,
    own_goal_area_center,
)


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


class OpenPlayAvailability(Enum):
    """普通比赛角色分配可使用的机器人数量。"""

    FULL_THREE = "3_available"
    DEGRADED_TWO = "2_available"
    DEGRADED_ONE = "1_available"
    UNAVAILABLE = "0_available"


@dataclass(frozen=True)
class OpenPlayRoleAssignment:
    """一帧普通比赛的基础职责分配结果。"""

    goalkeeper_id: int | None
    primary_attacker_id: int | None
    front_partner_id: int | None
    available_player_ids: tuple[int, ...]
    availability: OpenPlayAvailability


def get_phase(context: Context) -> Phase:
    """根据裁判机状态判断当前比赛阶段。"""
    g = context.game
    if g is None:
        return Phase.STOPPED

    # 裁判明确停止时优先停车,包括 READY + stopped 等组合状态。
    if g.stopped:
        return Phase.STOPPED

    state = g.state

    # READY:走 ready 位
    if state == GameState.READY:
        return Phase.READY

    # PLAYING:正常拼抢 or 开球/定位球执行中
    if state == GameState.PLAYING:
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

    # SET / INITIAL / FINISHED:站定
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
        store.player_availability = {}
        store.available_player_ids = ()
        store.default_goalkeeper_id = None
        store.temporary_goalkeeper_id = None
        store.current_goalkeeper_id = None
        store.available_field_player_ids = ()
        store.can_run_two_player_tactic = False
        store.latest_open_play_role_assignment = OpenPlayRoleAssignment(
            goalkeeper_id=None,
            primary_attacker_id=None,
            front_partner_id=None,
            available_player_ids=(),
            availability=OpenPlayAvailability.UNAVAILABLE,
        )
        # 后续固定战术可以维护自己的锁定职责,不与普通比赛分配状态混用。
        store.active_tactic = None
        store.locked_roles = None
        store.tactic_roles = None

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

        available_players = _collect_available_players(players, store)
        current_goalkeeper = _select_current_goalkeeper(
            context,
            players,
            available_players,
            phase,
            store,
        )

        # 按 phase 对整队分派一次(角色分配等全队计算只在 _act_* 里算一次)。
        if phase == Phase.NORMAL:
            _act_normal(context, available_players, current_goalkeeper, store)
        elif phase == Phase.OUR_KICKOFF:
            _clear_normal_sticky(store)
            _act_our_kickoff(
                context, available_players, current_goalkeeper, store,
            )
        elif phase == Phase.OPP_KICKOFF:
            _clear_normal_sticky(store)
            _act_opp_kickoff(context, available_players, current_goalkeeper)
        elif phase == Phase.OUR_SET_PLAY:
            _clear_normal_sticky(store)
            _act_our_set_play(
                context, available_players, current_goalkeeper, store,
            )
        elif phase == Phase.OPP_SET_PLAY:
            _clear_normal_sticky(store)
            _act_opp_set_play(
                context, available_players, current_goalkeeper, store,
            )
        elif phase == Phase.READY:
            _clear_normal_sticky(store)
            _act_ready(context, available_players, current_goalkeeper, store)
        elif phase == Phase.STOPPED:
            _clear_normal_sticky(store)
            for player in available_players:
                player.action = "stopped"
                player.stop()

        # 队员可视化统一在最后画一遍:覆盖所有球员(含判罚/未就绪/STOPPED),
        # 修复 SET 等状态下红球/标签消失的问题。
        for p in players:
            _draw_teammate_marker(p)


def _collect_available_players(players: list[Player], store) -> list[Player]:
    """统一分类每帧可用性,并清除不可用球员的旧动作命令。"""
    player_availability: dict[int, str] = {}
    available_players: list[Player] = []

    for player in players:
        ready = player.ensure_ready()
        if player.is_penalized:
            availability = "penalized"
        elif player.is_fallen:
            availability = "fallen"
        elif not ready:
            availability = "switching_mode"
        elif player.pose is None:
            availability = "no_pose"
        else:
            availability = "available"

        player_availability[player.id] = availability
        player.action = availability
        if availability == "available":
            available_players.append(player)
        else:
            player.stop()

    store.player_availability = player_availability
    store.available_player_ids = tuple(
        player.id for player in available_players
    )
    return available_players


def _resolve_default_goalkeeper_id(
    context: Context,
    players: list[Player],
) -> int | None:
    """解析裁判指定的默认守门员,无效时使用稳定 roster 回退。"""
    roster_ids = {player.id for player in players}
    if not roster_ids:
        return None

    team_state = (
        context.game.get_team_state(context.team_id)
        if context.game is not None else None
    )
    referee_goalkeeper_id = (
        team_state.goalkeeper if team_state is not None else 0
    )
    if referee_goalkeeper_id > 0 and referee_goalkeeper_id in roster_ids:
        return referee_goalkeeper_id
    if DEFAULT_GOALKEEPER_ID in roster_ids:
        return DEFAULT_GOALKEEPER_ID
    return min(roster_ids)


def _select_current_goalkeeper(
    context: Context,
    all_players: list[Player],
    available_players: list[Player],
    phase: Phase,
    store,
) -> Player | None:
    """选择并保持当前守门员,只在安全窗口交还默认守门员职责。"""
    default_goalkeeper_id = _resolve_default_goalkeeper_id(context, all_players)
    store.default_goalkeeper_id = default_goalkeeper_id

    available_by_id = {
        player.id: player for player in available_players
    }
    default_goalkeeper = available_by_id.get(default_goalkeeper_id)
    temporary_goalkeeper_id = getattr(
        store, "temporary_goalkeeper_id", None,
    )

    safe_handover_window = phase in (Phase.READY, Phase.STOPPED)
    if safe_handover_window and default_goalkeeper is not None:
        temporary_goalkeeper_id = None

    temporary_goalkeeper = available_by_id.get(temporary_goalkeeper_id)
    if temporary_goalkeeper is None:
        temporary_goalkeeper_id = None

    if temporary_goalkeeper is not None:
        current_goalkeeper = temporary_goalkeeper
    elif default_goalkeeper is not None:
        current_goalkeeper = default_goalkeeper
    elif available_players:
        own_goal_x, own_goal_y = own_goal(context)
        current_goalkeeper = min(
            available_players,
            key=lambda player: dist(
                player.pose.x, player.pose.y, own_goal_x, own_goal_y,
            ),
        )
        temporary_goalkeeper_id = current_goalkeeper.id
    else:
        current_goalkeeper = None

    store.temporary_goalkeeper_id = temporary_goalkeeper_id
    store.current_goalkeeper_id = (
        current_goalkeeper.id if current_goalkeeper is not None else None
    )
    field_players = [
        player for player in available_players
        if player is not current_goalkeeper
    ]
    store.available_field_player_ids = tuple(
        player.id for player in field_players
    )
    store.can_run_two_player_tactic = len(field_players) >= 2
    return current_goalkeeper


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


def _classify_open_play_availability(
    available_player_count: int,
) -> OpenPlayAvailability:
    """把 3v3 可用人数映射为明确的普通比赛降级等级。"""
    if available_player_count >= 3:
        return OpenPlayAvailability.FULL_THREE
    if available_player_count == 2:
        return OpenPlayAvailability.DEGRADED_TWO
    if available_player_count == 1:
        return OpenPlayAvailability.DEGRADED_ONE
    return OpenPlayAvailability.UNAVAILABLE


def _assign_open_play_roles(
    context: Context,
    available_players: list[Player],
    current_goalkeeper: Player | None,
    store,
) -> OpenPlayRoleAssignment:
    """只从本帧 available 阵容分配守门员、主攻和前场搭档。"""
    available_player_ids = tuple(
        player.id for player in available_players
    )
    availability = _classify_open_play_availability(
        len(available_players),
    )
    available_by_id = {
        player.id: player for player in available_players
    }
    goalkeeper_id = (
        current_goalkeeper.id
        if current_goalkeeper is not None
        and current_goalkeeper.id in available_by_id
        else None
    )
    field_players = [
        player for player in available_players
        if player.id != goalkeeper_id
    ]

    primary_attacker_id = None
    front_partner_id = None
    if len(available_players) == 1:
        if goalkeeper_id is None:
            primary_attacker_id = available_players[0].id
            store.normal_attacker = primary_attacker_id
    elif len(available_players) >= 2 and field_players:
        primary_attacker = _select_closest_attacker(
            context,
            field_players,
            getattr(store, "normal_attacker", None),
        )
        primary_attacker_id = primary_attacker.id
        store.normal_attacker = primary_attacker_id

        if len(available_players) >= 3:
            front_partner = next(
                (
                    player for player in field_players
                    if player.id != primary_attacker_id
                ),
                None,
            )
            front_partner_id = (
                front_partner.id if front_partner is not None else None
            )

    assignment = OpenPlayRoleAssignment(
        goalkeeper_id=goalkeeper_id,
        primary_attacker_id=primary_attacker_id,
        front_partner_id=front_partner_id,
        available_player_ids=available_player_ids,
        availability=availability,
    )
    store.latest_open_play_role_assignment = assignment
    return assignment


def _get_assigned_player(
    available_players_by_id: dict[int, Player],
    player_id: int | None,
) -> Player | None:
    """按角色结果中的 ID 取得本帧 available Player。"""
    if player_id is None:
        return None
    return available_players_by_id.get(player_id)


def _act_normal_primary_attacker(attacker: Player) -> None:
    """执行原有 attack,并在标签中保留其内部追球子动作。"""
    attacker.action = "attack"
    attacker.attack()
    attacker.action = f"normal:primary_attacker:{attacker.action}"


def _act_normal_front_partner(front_partner: Player) -> None:
    """执行原有 support,并标明普通比赛前场搭档职责。"""
    front_partner.support()
    front_partner.action = "normal:front_partner:support"


def _should_enter_normal_defense(context: Context) -> bool:
    """只在已知球明确进入己方半场时启用普通比赛防守阵型。"""
    ball = context.ball
    return ball is not None and ball.x < NORMAL_DEFENSE_BALL_X_MAX_M


def _get_normal_defense_protect_target(
    context: Context,
) -> tuple[float, float] | None:
    """计算球到己方球门线段上的保护点。"""
    ball = context.ball
    if ball is None:
        return None

    own_goal_x, own_goal_y = own_goal(context)
    route_x = own_goal_x - ball.x
    route_y = own_goal_y - ball.y
    route_length = math.hypot(route_x, route_y)
    if route_length <= 1e-6:
        return own_goal_area_center(context)

    desired_route_ratio = min(
        1.0,
        NORMAL_DEFENSE_PROTECT_DISTANCE_M / route_length,
    )
    if route_length > NORMAL_DEFENSE_GOAL_LINE_CLEARANCE_M:
        maximum_route_ratio = (
            1.0
            - NORMAL_DEFENSE_GOAL_LINE_CLEARANCE_M / route_length
        )
    else:
        # 球已贴近门线时无法同时满足门线余量，退化为线段中点。
        maximum_route_ratio = 0.5

    half_length = context.field.length / 2.0
    field_margin_x = -half_length + NORMAL_DEFENSE_FIELD_MARGIN_M
    if ball.x >= field_margin_x and ball.x > own_goal_x:
        maximum_field_ratio = (
            (ball.x - field_margin_x) / (ball.x - own_goal_x)
        )
        maximum_route_ratio = min(
            maximum_route_ratio,
            maximum_field_ratio,
        )

    route_ratio = clamp(
        min(desired_route_ratio, maximum_route_ratio),
        0.0,
        1.0,
    )
    return (
        ball.x + route_x * route_ratio,
        ball.y + route_y * route_ratio,
    )


def _act_normal_defense(
    context: Context,
    goalkeeper: Player | None,
    pressure_player: Player | None,
    protect_player: Player | None,
    store,
) -> None:
    """按已分配职责执行普通比赛的守门、逼抢和保护。"""
    if goalkeeper is not None:
        goalkeeper.guard()
        goalkeeper_kind = (
            "temporary"
            if goalkeeper.id == getattr(
                store, "temporary_goalkeeper_id", None,
            )
            else "default"
        )
        goalkeeper.action = f"defense:goalkeeper:{goalkeeper_kind}"

    if pressure_player is not None:
        pressure_player.action = "attack"
        pressure_player.attack()
        pressure_player.action = (
            f"defense:pressure:{pressure_player.action}"
        )

    if protect_player is not None:
        protect_target = _get_normal_defense_protect_target(context)
        protect_player.move_to_position(protect_target)
        protect_player.action = "defense:protect"


def _act_normal(
    context: Context,
    players: list[Player],
    goalkeeper: Player | None,
    store,
    *,
    allow_ball_search: bool = True,
) -> None:
    """NORMAL:消费基础职责分配并执行现有 guard/attack/support 动作。

    固定战术未来可在调用本入口前独立分派,从而绕过普通比赛角色分配。
    """
    assignment = _assign_open_play_roles(
        context, players, goalkeeper, store,
    )
    available_players_by_id = {
        player.id: player for player in players
    }
    role_goalkeeper = _get_assigned_player(
        available_players_by_id, assignment.goalkeeper_id,
    )
    primary_attacker = _get_assigned_player(
        available_players_by_id, assignment.primary_attacker_id,
    )
    front_partner = _get_assigned_player(
        available_players_by_id, assignment.front_partner_id,
    )
    assigned_field_players = [
        player for player in (primary_attacker, front_partner)
        if player is not None
    ]

    if assignment.availability == OpenPlayAvailability.UNAVAILABLE:
        return

    assigned_player_ids = {
        player.id for player in assigned_field_players
    }
    if role_goalkeeper is not None:
        assigned_player_ids.add(role_goalkeeper.id)
    for player in players:
        if player.id in assigned_player_ids:
            continue
        player.action = "normal:unassigned"
        player.stop()

    ball_confirmed = (
        _update_ball_recovery_state(context, store)
        if allow_ball_search else context.ball is not None
    )
    if not ball_confirmed:
        if allow_ball_search:
            _act_ball_recovery(
                context, assigned_field_players, role_goalkeeper, store,
            )
        else:
            if role_goalkeeper is not None:
                _act_goalkeeper_guard(role_goalkeeper, store)
            for player in assigned_field_players:
                player.action = "ball_unknown:stop"
                player.stop()
        return

    if allow_ball_search and _should_enter_normal_defense(context):
        _act_normal_defense(
            context,
            role_goalkeeper,
            primary_attacker,
            front_partner,
            store,
        )
        return

    if role_goalkeeper is not None:
        _act_goalkeeper_guard(role_goalkeeper, store)
    if primary_attacker is not None:
        _act_normal_primary_attacker(primary_attacker)
    if front_partner is not None:
        _act_normal_front_partner(front_partner)


def _act_goalkeeper_guard(goalkeeper: Player, store) -> None:
    """执行现有守门动作,并保留默认或临时守门员身份标签。"""
    goalkeeper.guard()
    if goalkeeper.id == getattr(store, "temporary_goalkeeper_id", None):
        goalkeeper.action = "temp_goalkeeper:guard"
    else:
        goalkeeper.action = "goalkeeper:guard"


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


def _act_ball_recovery(
    context: Context,
    field_players: list[Player],
    goalkeeper: Player | None,
    store,
) -> None:
    """NORMAL 丢球恢复:一人定向扫场,其余保持守位或停止。"""
    if goalkeeper is not None:
        _act_goalkeeper_guard(goalkeeper, store)
    if not field_players:
        return

    active_ids = {player.id for player in field_players}
    preferred_searcher = getattr(store, "ball_searcher", None)
    if preferred_searcher not in active_ids:
        preferred_searcher = getattr(store, "normal_attacker", None)
    if preferred_searcher not in active_ids:
        preferred_searcher = min(player.id for player in field_players)
    store.ball_searcher = preferred_searcher

    searcher = next(
        player for player in field_players if player.id == preferred_searcher
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

    for player in field_players:
        if player is searcher:
            continue
        player.action = "ball_unknown:hold"
        player.stop()


def _act_our_kickoff(
    context: Context,
    players: list[Player],
    goalkeeper: Player | None,
    store,
) -> None:
    """OUR_KICKOFF:守门员留守,场上球员沿用现有开球入口。"""
    if not players:
        return

    if goalkeeper is not None:
        _act_goalkeeper_guard(goalkeeper, store)
    field_players = [
        player for player in players if player is not goalkeeper
    ]
    if not field_players:
        store.kickoff_taker = None
        return

    active_ids = {player.id for player in field_players}
    if store.prev_phase != Phase.OUR_KICKOFF or store.kickoff_taker not in active_ids:
        # 进入开球阶段，重新选择开球球员
        store.kickoff_taker = _select_closest_attacker(
            context, field_players,
        ).id

    attacker_id = store.kickoff_taker
    attacker = next(
        (player for player in field_players if player.id == attacker_id),
        None,
    )
    if attacker is None:
        return

    attacker.action = "kickoff"
    attacker.kick(0.1, KICK_POWER_OUR_KICKOFF)

    for player in field_players:
        if player is attacker:
            continue
        player.action = "stay"
        player.stop()


def _clamp_restart_target(
    context: Context,
    target: tuple[float, float],
) -> tuple[float, float]:
    """把重启等待点限制在场内,避免规则避让点落到边线之外。"""
    field_margin = 0.3
    half_length = max(0.0, context.field.length / 2.0 - field_margin)
    half_width = max(0.0, context.field.width / 2.0 - field_margin)
    return (
        clamp(target[0], -half_length, half_length),
        clamp(target[1], -half_width, half_width),
    )


def _project_target_outside_radius(
    target: tuple[float, float],
    center: tuple[float, float],
    minimum_distance: float,
    fallback_direction: tuple[float, float],
) -> tuple[float, float]:
    """目标落入禁入圆时,沿当前方向投影到圆外。"""
    offset_x = target[0] - center[0]
    offset_y = target[1] - center[1]
    current_distance = math.hypot(offset_x, offset_y)
    if current_distance >= minimum_distance:
        return target

    if current_distance > 1e-6:
        direction_x = offset_x / current_distance
        direction_y = offset_y / current_distance
    else:
        fallback_length = math.hypot(*fallback_direction)
        if fallback_length <= 1e-6:
            direction_x, direction_y = -1.0, 0.0
        else:
            direction_x = fallback_direction[0] / fallback_length
            direction_y = fallback_direction[1] / fallback_length

    return (
        center[0] + direction_x * minimum_distance,
        center[1] + direction_y * minimum_distance,
    )


def _prepare_restart_target(
    context: Context,
    preferred_target: tuple[float, float],
    *,
    stay_outside_center_circle: bool = False,
) -> tuple[float, float]:
    """生成场内、避球且可选中圈外的对方重启等待点。"""
    target = _clamp_restart_target(context, preferred_target)
    own_goal_x, own_goal_y = own_goal(context)

    # 反复投影可处理球不完全位于中点时两个禁入圆部分重叠的情况。
    for _projection_pass in range(4):
        if stay_outside_center_circle:
            target = _project_target_outside_radius(
                target,
                (0.0, 0.0),
                context.field.circle_radius + CIRCLE_MARGIN_M,
                (-1.0, 0.0),
            )
            target = _clamp_restart_target(context, target)

        ball = context.ball
        if ball is not None:
            target = _project_target_outside_radius(
                target,
                (ball.x, ball.y),
                OPPONENT_RESTART_AVOID_M,
                (own_goal_x - ball.x, own_goal_y - ball.y),
            )
            target = _clamp_restart_target(context, target)

    return target


def _walk_to_restart_target(
    context: Context,
    player: Player,
    target: tuple[float, float],
    action: str,
    *,
    stay_outside_center_circle: bool = False,
) -> None:
    """对方重启期间只执行避球、避机器人的安全走位。"""
    safe_target = _prepare_restart_target(
        context,
        target,
        stay_outside_center_circle=stay_outside_center_circle,
    )
    face = 0.0
    ball = context.ball
    if ball is not None:
        face = angle_to(player.pose.x, player.pose.y, ball.x, ball.y)

    player.action = action
    player.walk_to(
        safe_target,
        face=face,
        avoid_ball=True,
        avoid_robots=True,
    )


def _act_opp_kickoff(
    context: Context,
    players: list[Player],
    goalkeeper: Player | None,
) -> None:
    """对方中场开球:守门并在中圈、球的合法距离外等待。"""
    if not players:
        return

    if goalkeeper is not None:
        _walk_to_restart_target(
            context,
            goalkeeper,
            own_goal_area_center(context),
            "opp_kickoff:guard",
            stay_outside_center_circle=True,
        )

    waiting_players = [
        player for player in players if player is not goalkeeper
    ]
    center_clearance = context.field.circle_radius + CIRCLE_MARGIN_M
    waiting_slots = [
        (-center_clearance - 0.4, 0.0),
        (-center_clearance - 1.2, 1.0),
        (-center_clearance - 1.2, -1.0),
    ]
    for slot_index, player in enumerate(waiting_players):
        target = waiting_slots[slot_index % len(waiting_slots)]
        _walk_to_restart_target(
            context,
            player,
            target,
            "opp_kickoff:avoid",
            stay_outside_center_circle=True,
        )


def _act_our_set_play(
    context: Context,
    players: list[Player],
    goalkeeper: Player | None,
    store,
) -> None:
    """OUR_SET_PLAY:按定位球类型分派, TODO：加入自己的逻辑。默认为 _act_normal"""
    field_players = [
        player for player in players if player is not goalkeeper
    ]
    if not field_players:
        if goalkeeper is not None:
            _act_goalkeeper_guard(goalkeeper, store)
        return

    set_play = get_set_play_type(context)
    if set_play == SetPlay.THROW_IN:
        _act_normal(
            context, players, goalkeeper, store, allow_ball_search=False,
        )
        return
    if set_play == SetPlay.CORNER_KICK:
        _act_normal(
            context, players, goalkeeper, store, allow_ball_search=False,
        )
        return
    if set_play == SetPlay.GOAL_KICK:
        _act_normal(
            context, players, goalkeeper, store, allow_ball_search=False,
        )
        return
    _act_normal(
        context, players, goalkeeper, store, allow_ball_search=False,
    )


def _act_opp_set_play(
    context: Context,
    players: list[Player],
    goalkeeper: Player | None,
    _store,
) -> None:
    """对方定位球:守门、封堵和保护均在球的合法距离外执行。"""
    if not players:
        return

    own_goal_x, own_goal_y = own_goal(context)
    if goalkeeper is not None:
        _walk_to_restart_target(
            context,
            goalkeeper,
            own_goal_area_center(context),
            "opp_restart:guard",
        )

    field_players = [
        player for player in players if player is not goalkeeper
    ]
    ball = context.ball
    if ball is None:
        for player in field_players:
            player.action = "opp_restart:stop_no_ball"
            player.stop()
        return

    route_to_goal_x = own_goal_x - ball.x
    route_to_goal_y = own_goal_y - ball.y
    route_length = math.hypot(route_to_goal_x, route_to_goal_y)
    if route_length <= 1e-6:
        route_direction_x, route_direction_y = -1.0, 0.0
    else:
        route_direction_x = route_to_goal_x / route_length
        route_direction_y = route_to_goal_y / route_length

    block_target = _prepare_restart_target(
        context,
        (
            ball.x + route_direction_x * OPPONENT_RESTART_AVOID_M,
            ball.y + route_direction_y * OPPONENT_RESTART_AVOID_M,
        ),
    )
    blocker = min(
        field_players,
        key=lambda player: dist(
            player.pose.x,
            player.pose.y,
            block_target[0],
            block_target[1],
        ),
        default=None,
    )
    if blocker is not None:
        _walk_to_restart_target(
            context,
            blocker,
            block_target,
            "opp_restart:block",
        )

    protecting_players = [
        player for player in field_players if player is not blocker
    ]
    protect_distance = min(
        max(OPPONENT_RESTART_AVOID_M + 0.8, 2.3),
        route_length,
    )
    central_protect_x = ball.x + route_direction_x * protect_distance
    central_protect_y = (
        ball.y + route_direction_y * protect_distance
    ) * 0.35

    for protect_index, player in enumerate(protecting_players):
        if protect_index == 0:
            lateral_offset = 0.0
        else:
            offset_rank = (protect_index + 1) // 2
            offset_direction = 1.0 if protect_index % 2 == 1 else -1.0
            lateral_offset = offset_direction * offset_rank * 0.8

        _walk_to_restart_target(
            context,
            player,
            (central_protect_x, central_protect_y + lateral_offset),
            "opp_restart:protect",
        )


def _act_ready(
    context: Context,
    players: list[Player],
    goalkeeper: Player | None,
    store,
) -> None:
    """READY:当前守门员进门前位置,场上机器人使用既有保守站位。"""
    if not players:
        return

    game = context.game
    our_kickoff = game is not None and game.kicking_team == context.team_id
    field = context.field
    if goalkeeper is not None:
        goalkeeper.action = (
            "ready:temp_goalkeeper"
            if goalkeeper.id == getattr(store, "temporary_goalkeeper_id", None)
            else "ready:goalkeeper"
        )
        goalkeeper.walk_to(
            own_goal_area_center(context),
            face=0.0,
            avoid_ball=True,
            avoid_robots=True,
        )

    field_players = [
        player for player in players if player is not goalkeeper
    ]
    if our_kickoff:
        ready_targets = [
            (-field.circle_radius, 0.0),
            (-0.5, field.circle_radius + 2.0),
        ]
    else:
        ready_targets = [
            (-field.circle_radius - 0.5, 0.0),
            (-field.length / 2.0 + field.penalty_area_length, 0.0),
        ]

    for player, target in zip(field_players, ready_targets):
        player.action = "ready"
        player.walk_to(
            target,
            face=0.0,
            avoid_ball=True,
            avoid_robots=True,
        )

    for player in field_players[len(ready_targets):]:
        player.action = "ready:hold"
        player.stop()



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

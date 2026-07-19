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


class OpenPlayMode(Enum):
    """普通比赛的稳定战术模式。"""

    ATTACKING = "attacking"
    DEFENDING = "defending"
    CONTESTED = "contested"


class GoalkeeperMode(Enum):
    """普通 live play 中的守门员战术状态。"""

    HOLD = "hold"
    TRACK = "track"
    BLOCK = "block"
    CHALLENGE = "challenge"
    CLEAR = "clear"
    RETURN = "return"


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


@dataclass(frozen=True)
class OpenPlayModeEstimate:
    """一帧普通比赛的可解释模式估计结果。"""

    candidate_mode: OpenPlayMode
    reason: str
    our_nearest_ball_distance: float | None
    opponent_nearest_ball_distance: float | None
    distance_advantage: float | None
    ball_in_own_danger_area: bool


@dataclass(frozen=True)
class GoalkeeperThreatEstimate:
    """守门员使用的可解释射门威胁估计。"""

    fast_goal_threat: bool
    position_threat: bool
    projected_goal_y: float | None
    reason: str


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
        store.open_play_mode = None
        store.open_play_mode_entered_at = None
        store.open_play_last_switch_at = None
        store.open_play_mode_reason = "inactive"
        store.open_play_last_switch_reason = None
        store.open_play_our_ball_distance = None
        store.open_play_opponent_ball_distance = None
        store.open_play_distance_advantage = None
        store.goalkeeper_strategy_player_id = None
        store.goalkeeper_mode = None
        store.goalkeeper_mode_entered_at = None
        store.goalkeeper_challenge_started_at = None
        store.goalkeeper_threat_reason = "inactive"
        store.goalkeeper_previous_ball_position = None
        store.goalkeeper_previous_ball_sample_at = None
        store.goalkeeper_ball_velocity = None
        store.goalkeeper_ball_speed = None
        store.goalkeeper_target = None
        store.goalkeeper_clearance_target = None
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
        if current_goalkeeper is None or store.prev_phase != phase:
            _reset_goalkeeper_strategy(store)

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
    store.open_play_mode = None
    store.open_play_mode_entered_at = None
    store.open_play_last_switch_at = None
    store.open_play_mode_reason = "inactive"
    store.open_play_last_switch_reason = None
    store.open_play_our_ball_distance = None
    store.open_play_opponent_ball_distance = None
    store.open_play_distance_advantage = None


def _reset_goalkeeper_strategy(store) -> None:
    """清除 phase 或守门员身份相关的高级守门跨帧状态。"""
    store.goalkeeper_strategy_player_id = None
    store.goalkeeper_mode = None
    store.goalkeeper_mode_entered_at = None
    store.goalkeeper_challenge_started_at = None
    store.goalkeeper_threat_reason = "inactive"
    store.goalkeeper_previous_ball_position = None
    store.goalkeeper_previous_ball_sample_at = None
    store.goalkeeper_ball_velocity = None
    store.goalkeeper_ball_speed = None
    store.goalkeeper_target = None
    store.goalkeeper_clearance_target = None


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


def _get_attack_subaction(player: Player) -> str:
    """把 Player.attack 的内部状态压缩为可读的策略标签。"""
    if player.action.startswith("attack:"):
        return player.action.removeprefix("attack:")
    return "kick" if player.is_kicking else "chase"


def _act_attack_ball_handler(player: Player, role_label: str) -> None:
    """执行 attack,并保留绕球、追球或踢球子动作。"""
    player.action = "attack"
    player.attack()
    player.action = f"attack:{role_label}:{_get_attack_subaction(player)}"


def _select_attack_support_side(
    context: Context,
    ball_handler: Player,
    supporting_player: Player,
    goal_direction: tuple[float, float],
) -> float:
    """选择接应侧:边线附近向内,其他位置优先避开主攻到球线路。"""
    ball = context.ball
    if (
        ball is None
        or ball_handler.pose is None
        or supporting_player.pose is None
    ):
        return 1.0

    lateral_direction = (-goal_direction[1], goal_direction[0])
    handler_lateral_offset = (
        (ball_handler.pose.x - ball.x) * lateral_direction[0]
        + (ball_handler.pose.y - ball.y) * lateral_direction[1]
    )
    supporting_lateral_offset = (
        (supporting_player.pose.x - ball.x) * lateral_direction[0]
        + (supporting_player.pose.y - ball.y) * lateral_direction[1]
    )
    half_width = context.field.width / 2.0
    ball_near_touchline = (
        half_width - abs(ball.y)
        <= NORMAL_ATTACK_SUPPORT_TOUCHLINE_ZONE_M
    )

    best_side = 1.0
    best_score = -math.inf
    for candidate_side in (-1.0, 1.0):
        score = 0.0
        candidate_y_direction = candidate_side * lateral_direction[1]
        if ball_near_touchline and candidate_y_direction * ball.y < 0.0:
            score += 4.0
        if (
            abs(handler_lateral_offset) > 1e-6
            and candidate_side * handler_lateral_offset < 0.0
        ):
            score += 2.0
        if candidate_side * supporting_lateral_offset >= 0.0:
            score += 0.5
        if score > best_score:
            best_side = candidate_side
            best_score = score
    return best_side


def _ball_in_attack_rebound_area(context: Context) -> bool:
    """判断球是否进入对方禁区附近的补射、二点球区域。"""
    ball = context.ball
    if ball is None:
        return False

    opponent_goal_line_x = context.field.length / 2.0
    distance_inside_goal_line = opponent_goal_line_x - ball.x
    return (
        -NORMAL_ATTACK_REBOUND_PENALTY_MARGIN_M
        <= distance_inside_goal_line
        <= context.field.penalty_area_length
        + NORMAL_ATTACK_REBOUND_PENALTY_MARGIN_M
        and abs(ball.y)
        <= context.field.penalty_area_width / 2.0
        + NORMAL_ATTACK_REBOUND_PENALTY_MARGIN_M
    )


def _get_dynamic_attack_support_target(
    context: Context,
    ball_handler: Player,
    supporting_player: Player,
) -> tuple[float, float] | None:
    """计算围绕当前球位、朝向对方球门的动态接应或补射站位。"""
    ball = context.ball
    if (
        ball is None
        or ball_handler.pose is None
        or supporting_player.pose is None
    ):
        return None

    opponent_goal_x, opponent_goal_y = opponent_goal(context)
    goal_offset_x = opponent_goal_x - ball.x
    goal_offset_y = opponent_goal_y - ball.y
    goal_distance = math.hypot(goal_offset_x, goal_offset_y)
    if goal_distance <= 1e-6:
        goal_direction = (1.0, 0.0)
    else:
        goal_direction = (
            goal_offset_x / goal_distance,
            goal_offset_y / goal_distance,
        )
    lateral_direction = (-goal_direction[1], goal_direction[0])
    support_side = _select_attack_support_side(
        context,
        ball_handler,
        supporting_player,
        goal_direction,
    )

    near_opponent_penalty_area = _ball_in_attack_rebound_area(context)
    if near_opponent_penalty_area:
        forward_distance = NORMAL_ATTACK_REBOUND_FORWARD_DISTANCE_M
        lateral_distance = NORMAL_ATTACK_REBOUND_LATERAL_DISTANCE_M
    else:
        forward_distance = NORMAL_ATTACK_SUPPORT_FORWARD_DISTANCE_M
        lateral_distance = NORMAL_ATTACK_SUPPORT_LATERAL_DISTANCE_M

    def build_target(selected_lateral_distance: float) -> tuple[float, float]:
        return (
            ball.x + goal_direction[0] * forward_distance
            + lateral_direction[0] * support_side * selected_lateral_distance,
            ball.y + goal_direction[1] * forward_distance
            + lateral_direction[1] * support_side * selected_lateral_distance,
        )

    target_x, target_y = build_target(lateral_distance)
    distance_from_handler = dist(
        target_x,
        target_y,
        ball_handler.pose.x,
        ball_handler.pose.y,
    )
    if distance_from_handler < NORMAL_ATTACK_SUPPORT_PRIMARY_SPACING_M:
        lateral_distance += (
            NORMAL_ATTACK_SUPPORT_PRIMARY_SPACING_M - distance_from_handler
        )
        target_x, target_y = build_target(lateral_distance)

    half_length = max(
        0.0,
        context.field.length / 2.0 - NORMAL_ATTACK_SUPPORT_FIELD_MARGIN_M,
    )
    half_width = max(
        0.0,
        context.field.width / 2.0 - NORMAL_ATTACK_SUPPORT_FIELD_MARGIN_M,
    )
    clamped_target_x = clamp(target_x, -half_length, half_length)
    clamped_target_y = clamp(target_y, -half_width, half_width)
    clamped_handler_distance = dist(
        clamped_target_x,
        clamped_target_y,
        ball_handler.pose.x,
        ball_handler.pose.y,
    )
    if clamped_handler_distance < NORMAL_ATTACK_SUPPORT_PRIMARY_SPACING_M:
        away_from_handler_x = clamped_target_x - ball_handler.pose.x
        away_from_handler_y = clamped_target_y - ball_handler.pose.y
        away_length = math.hypot(away_from_handler_x, away_from_handler_y)
        if away_length <= 1e-6:
            away_from_handler_x = lateral_direction[0] * support_side
            away_from_handler_y = lateral_direction[1] * support_side
            away_length = 1.0
        spacing_scale = NORMAL_ATTACK_SUPPORT_PRIMARY_SPACING_M / away_length
        clamped_target_x = clamp(
            ball_handler.pose.x + away_from_handler_x * spacing_scale,
            -half_length,
            half_length,
        )
        clamped_target_y = clamp(
            ball_handler.pose.y + away_from_handler_y * spacing_scale,
            -half_width,
            half_width,
        )
    return (clamped_target_x, clamped_target_y)


def _should_front_partner_challenge(
    context: Context,
    primary_attacker: Player,
    front_partner: Player,
) -> bool:
    """允许近球且明显更近的搭档临时接管,不改变粘滞角色。"""
    if primary_attacker.is_kicking:
        return False
    partner_distance = _player_dist_to_ball(context, front_partner)
    primary_distance = _player_dist_to_ball(context, primary_attacker)
    return (
        partner_distance <= NORMAL_ATTACK_PARTNER_CHALLENGE_DISTANCE_M
        and partner_distance + NORMAL_ATTACK_PARTNER_CLOSER_MARGIN_M
        < primary_distance
    )


def _act_normal_attacking_shape(
    context: Context,
    goalkeeper: Player | None,
    primary_attacker: Player | None,
    front_partner: Player | None,
    store,
) -> None:
    """执行普通比赛双前场进攻形态及安全人数降级。"""
    if primary_attacker is None:
        return
    if front_partner is None:
        _act_attack_ball_handler(primary_attacker, "solo")
        return

    if _should_front_partner_challenge(
        context,
        primary_attacker,
        front_partner,
    ):
        followup_target = _get_dynamic_attack_support_target(
            context,
            front_partner,
            primary_attacker,
        )
        _act_attack_ball_handler(front_partner, "partner_challenge")
        primary_attacker.move_to_position(followup_target)
        primary_attacker.action = "attack:partner_support"
        return

    support_target = _get_dynamic_attack_support_target(
        context,
        primary_attacker,
        front_partner,
    )
    _act_attack_ball_handler(primary_attacker, "primary")
    front_partner.move_to_position(support_target)
    front_partner.action = (
        "attack:rebound"
        if _ball_in_attack_rebound_area(context)
        else "attack:partner_support"
    )


def _nearest_available_teammate_distance(
    context: Context,
    field_players: list[Player],
) -> float | None:
    """返回可用非守门员到球的最近距离。"""
    ball = context.ball
    player_distances = [
        dist(player.pose.x, player.pose.y, ball.x, ball.y)
        for player in field_players
        if ball is not None and player.pose is not None
    ]
    return min(player_distances) if player_distances else None


def _nearest_opponent_distance(context: Context) -> float | None:
    """返回具有有效位姿的对方机器人到球的最近距离。"""
    ball = context.ball
    opponent_distances = [
        dist(robot.pose.x, robot.pose.y, ball.x, ball.y)
        for robot in context.opponents.values()
        if ball is not None and robot.pose is not None
    ]
    return min(opponent_distances) if opponent_distances else None


def _ball_in_own_danger_area(context: Context) -> bool:
    """判断球是否位于可立即打断模式迟滞的己方门前危险区。"""
    ball = context.ball
    if ball is None:
        return False

    own_goal_line_x = -context.field.length / 2.0
    own_penalty_edge_x = own_goal_line_x + context.field.penalty_area_length
    danger_front_x = max(OPEN_PLAY_DANGER_X_M, own_penalty_edge_x)
    danger_half_width = (
        context.field.penalty_area_width / 2.0
        + OPEN_PLAY_DANGER_LATERAL_MARGIN_M
    )
    in_penalty_channel = (
        ball.x <= danger_front_x
        and abs(ball.y) <= danger_half_width
    )
    immediately_near_goal = (
        ball.x
        <= own_goal_line_x + OPEN_PLAY_IMMEDIATE_GOAL_DANGER_DEPTH_M
    )
    return in_penalty_channel or immediately_near_goal


def _goalkeeper_safe_lateral_limit(context: Context) -> float:
    """返回门柱内侧的守门员最大横向站位。"""
    goal_limited_lateral = max(
        0.0,
        context.field.goal_width / 2.0 - GOALKEEPER_POST_MARGIN_M,
    )
    return min(GOALKEEPER_MAX_LATERAL_M, goal_limited_lateral)


def _get_goalkeeper_home_target(context: Context) -> tuple[float, float]:
    """根据球到己方门的距离计算远近不同增益的动态门前目标。"""
    own_goal_x, _own_goal_y = own_goal(context)
    home_x = own_goal_x + GOALKEEPER_HOME_X_OFFSET_M
    ball = context.ball
    if ball is None:
        return (home_x, 0.0)

    ball_goal_distance = max(0.0, ball.x - own_goal_x)
    near_weight = 1.0 - clamp(
        ball_goal_distance / max(GOALKEEPER_TRACK_NEAR_DISTANCE_M, 1e-6),
        0.0,
        1.0,
    )
    tracking_gain = (
        GOALKEEPER_TRACK_GAIN_FAR
        + (GOALKEEPER_TRACK_GAIN_NEAR - GOALKEEPER_TRACK_GAIN_FAR)
        * near_weight
    )
    lateral_limit = _goalkeeper_safe_lateral_limit(context)
    home_y = clamp(
        ball.y * tracking_gain,
        -lateral_limit,
        lateral_limit,
    )
    return (home_x, home_y)


def _estimate_goalkeeper_ball_velocity(
    context: Context,
    store,
) -> tuple[float, float] | None:
    """以新球观测做有限差分；异常样本只重播种，不输出速度。"""
    ball = context.ball
    if ball is None:
        store.goalkeeper_previous_ball_position = None
        store.goalkeeper_previous_ball_sample_at = None
        store.goalkeeper_ball_velocity = None
        store.goalkeeper_ball_speed = None
        return None

    sample_at = ball.last_seen_at if ball.last_seen_at > 0.0 else context.now
    current_position = (ball.x, ball.y)
    previous_position = getattr(
        store, "goalkeeper_previous_ball_position", None,
    )
    previous_sample_at = getattr(
        store, "goalkeeper_previous_ball_sample_at", None,
    )

    if previous_position is None or previous_sample_at is None:
        store.goalkeeper_previous_ball_position = current_position
        store.goalkeeper_previous_ball_sample_at = sample_at
        store.goalkeeper_ball_velocity = None
        store.goalkeeper_ball_speed = None
        return None

    sample_interval = sample_at - previous_sample_at
    if sample_interval <= 0.0:
        velocity_age = max(0.0, context.now - previous_sample_at)
        if velocity_age <= GOALKEEPER_BALL_VELOCITY_MAX_AGE_SEC:
            return getattr(store, "goalkeeper_ball_velocity", None)
        store.goalkeeper_ball_velocity = None
        store.goalkeeper_ball_speed = None
        return None

    displacement = dist(
        previous_position[0],
        previous_position[1],
        current_position[0],
        current_position[1],
    )
    store.goalkeeper_previous_ball_position = current_position
    store.goalkeeper_previous_ball_sample_at = sample_at

    valid_sample_interval = (
        GOALKEEPER_BALL_SAMPLE_MIN_SEC
        <= sample_interval
        <= GOALKEEPER_BALL_SAMPLE_MAX_SEC
    )
    if (
        not valid_sample_interval
        or displacement > GOALKEEPER_BALL_SAMPLE_MAX_JUMP_M
    ):
        store.goalkeeper_ball_velocity = None
        store.goalkeeper_ball_speed = None
        return None

    velocity_x = (current_position[0] - previous_position[0]) / sample_interval
    velocity_y = (current_position[1] - previous_position[1]) / sample_interval
    ball_speed = math.hypot(velocity_x, velocity_y)
    if ball_speed > GOALKEEPER_BALL_MAX_CREDIBLE_SPEED_MPS:
        store.goalkeeper_ball_velocity = None
        store.goalkeeper_ball_speed = None
        return None

    velocity = (velocity_x, velocity_y)
    store.goalkeeper_ball_velocity = velocity
    store.goalkeeper_ball_speed = ball_speed
    return velocity


def _estimate_goalkeeper_threat(
    context: Context,
    ball_velocity: tuple[float, float] | None,
    opponent_nearest_distance: float | None,
) -> GoalkeeperThreatEstimate:
    """判断快速射门投影或无可靠速度时的保守门前位置威胁。"""
    ball = context.ball
    if ball is None:
        return GoalkeeperThreatEstimate(False, False, None, "ball_unknown")

    own_goal_x, _own_goal_y = own_goal(context)
    goal_projection_limit = (
        context.field.goal_width / 2.0
        + GOALKEEPER_SHOT_PROJECTION_MARGIN_M
    )
    if ball_velocity is not None:
        velocity_x, velocity_y = ball_velocity
        ball_speed = math.hypot(velocity_x, velocity_y)
        moving_toward_goal = velocity_x <= -GOALKEEPER_GOALWARD_VX_MPS
        if moving_toward_goal and ball.x > own_goal_x:
            time_to_goal_line = (own_goal_x - ball.x) / velocity_x
            projected_goal_y = ball.y + velocity_y * time_to_goal_line
            fast_goal_threat = (
                ball.x < 0.0
                and ball_speed >= GOALKEEPER_FAST_BALL_SPEED_MPS
                and time_to_goal_line >= 0.0
                and abs(projected_goal_y) <= goal_projection_limit
            )
            if fast_goal_threat:
                return GoalkeeperThreatEstimate(
                    True,
                    True,
                    projected_goal_y,
                    "fast_shot_projection",
                )

    opponent_can_shoot = (
        opponent_nearest_distance is not None
        and opponent_nearest_distance
        <= GOALKEEPER_POSITION_THREAT_OPPONENT_DISTANCE_M
    )
    ball_in_shooting_channel = (
        ball.x <= GOALKEEPER_POSITION_THREAT_X_M
        and abs(ball.y) <= GOALKEEPER_POSITION_THREAT_LATERAL_M
    )
    position_threat = (
        ball_in_shooting_channel
        and (opponent_can_shoot or _ball_in_own_danger_area(context))
    )
    return GoalkeeperThreatEstimate(
        False,
        position_threat,
        None,
        "position_threat" if position_threat else "no_direct_threat",
    )


def _get_goalkeeper_block_target(
    context: Context,
    ball_velocity: tuple[float, float] | None,
) -> tuple[float, float]:
    """计算球运动射线在门前封堵 X 上的交点。"""
    own_goal_x, _own_goal_y = own_goal(context)
    block_x = own_goal_x + GOALKEEPER_BLOCK_X_OFFSET_M
    home_target = _get_goalkeeper_home_target(context)
    block_y = home_target[1]
    ball = context.ball
    if ball is not None and ball_velocity is not None:
        velocity_x, velocity_y = ball_velocity
        if velocity_x < -1e-6 and ball.x > block_x:
            time_to_block_x = (block_x - ball.x) / velocity_x
            if time_to_block_x >= 0.0:
                block_y = ball.y + velocity_y * time_to_block_x

    lateral_limit = _goalkeeper_safe_lateral_limit(context)
    return (
        block_x,
        clamp(block_y, -lateral_limit, lateral_limit),
    )


def _goalkeeper_can_challenge(
    context: Context,
    goalkeeper: Player,
    opponent_nearest_distance: float | None,
    ball_speed: float | None,
    field_player_count: int,
    current_mode: GoalkeeperMode | None,
) -> bool:
    """仅在慢球、对手数据有效且守门员明确先到时允许出击。"""
    ball = context.ball
    pose = goalkeeper.pose
    if (
        ball is None
        or pose is None
        or opponent_nearest_distance is None
        or field_player_count < 2
    ):
        return False

    distance_limit = (
        GOALKEEPER_CHALLENGE_EXIT_DISTANCE_M
        if current_mode == GoalkeeperMode.CHALLENGE
        else GOALKEEPER_CHALLENGE_ENTER_DISTANCE_M
    )
    goalkeeper_distance = dist(pose.x, pose.y, ball.x, ball.y)
    ball_is_slow_enough = (
        ball_speed is None or ball_speed <= GOALKEEPER_SLOW_BALL_SPEED_MPS
    )
    ball_in_challenge_area = (
        ball.x <= GOALKEEPER_CHALLENGE_MAX_X_M
        and abs(ball.y) <= GOALKEEPER_CHALLENGE_MAX_LATERAL_M
    )
    goalkeeper_arrives_first = (
        goalkeeper_distance + GOALKEEPER_CHALLENGE_ADVANTAGE_M
        < opponent_nearest_distance
    )
    return (
        ball_is_slow_enough
        and ball_in_challenge_area
        and goalkeeper_distance <= distance_limit
        and goalkeeper_arrives_first
    )


def _point_to_segment_distance(
    point: tuple[float, float],
    segment_start: tuple[float, float],
    segment_end: tuple[float, float],
) -> float:
    """返回点到线段的最短距离，用于选择较空的解围走廊。"""
    segment_x = segment_end[0] - segment_start[0]
    segment_y = segment_end[1] - segment_start[1]
    segment_length_squared = segment_x * segment_x + segment_y * segment_y
    if segment_length_squared <= 1e-9:
        return dist(point[0], point[1], segment_start[0], segment_start[1])

    projection = (
        (point[0] - segment_start[0]) * segment_x
        + (point[1] - segment_start[1]) * segment_y
    ) / segment_length_squared
    projection = clamp(projection, 0.0, 1.0)
    nearest_x = segment_start[0] + segment_x * projection
    nearest_y = segment_start[1] + segment_y * projection
    return dist(point[0], point[1], nearest_x, nearest_y)


def _get_safe_goalkeeper_clearance_target(
    context: Context,
) -> tuple[float, float] | None:
    """从全部正 X 候选中选择对手走廊净空最大的前场或边路目标。"""
    ball = context.ball
    if ball is None:
        return None

    half_length = (
        context.field.length / 2.0 - GOALKEEPER_CLEAR_FIELD_MARGIN_M
    )
    half_width = max(
        0.0,
        context.field.width / 2.0 - GOALKEEPER_CLEAR_FIELD_MARGIN_M,
    )
    if half_length <= ball.x:
        return None

    minimum_target_x = min(ball.x + 0.5, half_length)
    target_x = clamp(
        ball.x + GOALKEEPER_CLEAR_FORWARD_DISTANCE_M,
        minimum_target_x,
        half_length,
    )
    center_target_y = clamp(
        ball.y * 0.35,
        -min(half_width, GOALKEEPER_CLEAR_CENTER_BAND_M),
        min(half_width, GOALKEEPER_CLEAR_CENTER_BAND_M),
    )
    side_target_y = min(
        half_width,
        max(abs(ball.y), GOALKEEPER_CLEAR_LATERAL_DISTANCE_M),
    )
    candidates = [
        (target_x, center_target_y),
        (target_x, side_target_y),
        (target_x, -side_target_y),
    ]
    opponent_positions = [
        (robot.pose.x, robot.pose.y)
        for robot in context.opponents.values()
        if robot.pose is not None
    ]
    if not opponent_positions:
        return candidates[0]

    ball_position = (ball.x, ball.y)

    def corridor_clearance(candidate: tuple[float, float]) -> float:
        return min(
            _point_to_segment_distance(
                opponent_position,
                ball_position,
                candidate,
            )
            for opponent_position in opponent_positions
        )

    return max(candidates, key=corridor_clearance)


def _goalkeeper_has_returned(
    goalkeeper: Player,
    home_target: tuple[float, float],
) -> bool:
    pose = goalkeeper.pose
    return (
        pose is not None
        and dist(pose.x, pose.y, home_target[0], home_target[1])
        <= GOALKEEPER_RETURN_ARRIVE_M
    )


def _update_goalkeeper_mode(
    context: Context,
    goalkeeper: Player,
    threat: GoalkeeperThreatEstimate,
    can_challenge: bool,
    ball_clearable: bool,
    home_target: tuple[float, float],
    allow_active_response: bool,
    store,
) -> GoalkeeperMode:
    """按快速封堵优先级、迟滞和超时更新守门员模式。"""
    current_mode = getattr(store, "goalkeeper_mode", None)
    mode_entered_at = getattr(store, "goalkeeper_mode_entered_at", None)
    challenge_started_at = getattr(
        store, "goalkeeper_challenge_started_at", None,
    )
    mode_elapsed = (
        math.inf
        if mode_entered_at is None
        else max(0.0, context.now - mode_entered_at)
    )
    challenge_timed_out = (
        current_mode == GoalkeeperMode.CHALLENGE
        and challenge_started_at is not None
        and context.now - challenge_started_at
        >= GOALKEEPER_CHALLENGE_TIMEOUT_SEC
    )
    clear_timed_out = (
        current_mode == GoalkeeperMode.CLEAR
        and mode_elapsed >= GOALKEEPER_CLEAR_TIMEOUT_SEC
    )

    ball = context.ball
    if not allow_active_response:
        candidate_mode = (
            GoalkeeperMode.HOLD if ball is None else GoalkeeperMode.TRACK
        )
        reason = "conservative_restart"
    elif threat.fast_goal_threat:
        candidate_mode = GoalkeeperMode.BLOCK
        reason = threat.reason
    elif challenge_timed_out:
        candidate_mode = GoalkeeperMode.RETURN
        reason = "challenge_timeout"
    elif clear_timed_out:
        candidate_mode = GoalkeeperMode.RETURN
        reason = "clear_timeout"
    elif (
        current_mode == GoalkeeperMode.CLEAR
        and mode_elapsed < GOALKEEPER_CLEAR_MIN_HOLD_SEC
    ):
        candidate_mode = GoalkeeperMode.CLEAR
        reason = "clear_min_hold"
    elif current_mode == GoalkeeperMode.RETURN and not _goalkeeper_has_returned(
        goalkeeper, home_target,
    ):
        if threat.position_threat:
            candidate_mode = GoalkeeperMode.BLOCK
            reason = threat.reason
        else:
            candidate_mode = GoalkeeperMode.RETURN
            reason = "returning_home"
    elif ball_clearable:
        candidate_mode = GoalkeeperMode.CLEAR
        reason = "danger_ball_in_clear_range"
    elif can_challenge:
        candidate_mode = GoalkeeperMode.CHALLENGE
        reason = "goalkeeper_arrives_first"
    elif threat.position_threat:
        candidate_mode = GoalkeeperMode.BLOCK
        reason = threat.reason
    elif current_mode in (GoalkeeperMode.CHALLENGE, GoalkeeperMode.CLEAR):
        candidate_mode = GoalkeeperMode.RETURN
        reason = "active_condition_ended"
    elif ball is not None and ball.x <= OPEN_PLAY_CONTESTED_BAND_M:
        candidate_mode = GoalkeeperMode.TRACK
        reason = "track_visible_ball"
    else:
        candidate_mode = GoalkeeperMode.HOLD
        reason = "hold_safe_area"

    returning_into_position_threat = (
        current_mode == GoalkeeperMode.RETURN
        and threat.position_threat
    )
    active_mode_lost_ball = (
        ball is None
        and current_mode in (
            GoalkeeperMode.BLOCK,
            GoalkeeperMode.CHALLENGE,
            GoalkeeperMode.CLEAR,
        )
    )
    force_switch = (
        not allow_active_response
        or threat.fast_goal_threat
        or ball_clearable
        or challenge_timed_out
        or clear_timed_out
        or returning_into_position_threat
        or active_mode_lost_ball
    )
    held_long_enough = (
        current_mode is None
        or mode_elapsed >= GOALKEEPER_MODE_MIN_HOLD_SEC
    )
    if (
        current_mode is None
        or (
            candidate_mode != current_mode
            and (force_switch or held_long_enough)
        )
    ):
        previous_mode = current_mode
        current_mode = candidate_mode
        store.goalkeeper_mode = current_mode
        store.goalkeeper_mode_entered_at = context.now
        if current_mode == GoalkeeperMode.CHALLENGE:
            store.goalkeeper_challenge_started_at = context.now
        else:
            store.goalkeeper_challenge_started_at = None
        _log.info(
            "goalkeeper mode %s -> %s reason=%s",
            previous_mode.value if previous_mode is not None else "none",
            current_mode.value,
            reason,
        )
    elif candidate_mode != current_mode:
        reason = "hold_hysteresis"

    store.goalkeeper_threat_reason = reason
    return current_mode


def _draw_goalkeeper_strategy(
    context: Context,
    goalkeeper: Player,
    mode: GoalkeeperMode,
    target: tuple[float, float],
    threat: GoalkeeperThreatEstimate,
    clearance_target: tuple[float, float] | None,
    store,
) -> None:
    """显示守门模式、动态目标、可靠射门线和解围目标。"""
    from .framework import debugdraw

    debugdraw.point(
        target[0], target[1],
        rgb=(0.0, 0.8, 1.0), scale=0.22, ns="goalkeeper_target",
    )
    ball = context.ball
    if ball is not None and threat.projected_goal_y is not None:
        own_goal_x, _own_goal_y = own_goal(context)
        debugdraw.line(
            [(ball.x, ball.y), (own_goal_x, threat.projected_goal_y)],
            rgb=(1.0, 0.3, 0.0), ns="goalkeeper_shot_line",
        )
    if ball is not None and clearance_target is not None:
        debugdraw.line(
            [(ball.x, ball.y), clearance_target],
            rgb=(0.2, 1.0, 0.4), ns="goalkeeper_clearance",
        )
        debugdraw.point(
            clearance_target[0], clearance_target[1],
            rgb=(0.2, 1.0, 0.4), scale=0.18,
            ns="goalkeeper_clearance_target",
        )

    goalkeeper_kind = (
        "temporary"
        if goalkeeper.id == getattr(store, "temporary_goalkeeper_id", None)
        else "default"
    )
    speed = getattr(store, "goalkeeper_ball_speed", None)
    speed_label = "n/a" if speed is None else f"{speed:.2f}"
    debugdraw.text(
        0.0,
        context.field.width / 2.0 + 0.9,
        (
            f"goalkeeper={goalkeeper_kind} mode={mode.value} "
            f"reason={store.goalkeeper_threat_reason} speed={speed_label}"
        ),
        rgb=(0.2, 0.8, 1.0),
        ns="goalkeeper_mode",
    )


def _act_goalkeeper_strategy(
    context: Context,
    goalkeeper: Player,
    field_players: list[Player],
    store,
    *,
    allow_active_response: bool,
) -> None:
    """统一执行默认或临时守门员的动态站位和高级动作。"""
    if goalkeeper.pose is None:
        goalkeeper.action = "goalkeeper:no_pose"
        goalkeeper.stop()
        return

    if getattr(store, "goalkeeper_strategy_player_id", None) != goalkeeper.id:
        _reset_goalkeeper_strategy(store)
        store.goalkeeper_strategy_player_id = goalkeeper.id

    ball_velocity = _estimate_goalkeeper_ball_velocity(context, store)
    ball_speed = getattr(store, "goalkeeper_ball_speed", None)
    opponent_nearest_distance = _nearest_opponent_distance(context)
    threat = _estimate_goalkeeper_threat(
        context,
        ball_velocity,
        opponent_nearest_distance,
    )
    home_target = _get_goalkeeper_home_target(context)
    current_mode = getattr(store, "goalkeeper_mode", None)
    open_play_mode = getattr(store, "open_play_mode", None)
    has_explicit_field_protection = open_play_mode in (
        OpenPlayMode.DEFENDING,
        OpenPlayMode.CONTESTED,
    )
    can_challenge = (
        allow_active_response
        and has_explicit_field_protection
        and not threat.fast_goal_threat
        and _goalkeeper_can_challenge(
            context,
            goalkeeper,
            opponent_nearest_distance,
            ball_speed,
            len(field_players),
            current_mode,
        )
    )

    ball = context.ball
    goalkeeper_ball_distance = (
        dist(
            goalkeeper.pose.x,
            goalkeeper.pose.y,
            ball.x,
            ball.y,
        )
        if ball is not None else math.inf
    )
    clear_distance_limit = (
        GOALKEEPER_CLEAR_EXIT_DISTANCE_M
        if current_mode == GoalkeeperMode.CLEAR
        else GOALKEEPER_CLEAR_ENTER_DISTANCE_M
    )
    ball_clearable = (
        allow_active_response
        and ball is not None
        and not threat.fast_goal_threat
        and _ball_in_own_danger_area(context)
        and goalkeeper_ball_distance <= clear_distance_limit
    )
    mode = _update_goalkeeper_mode(
        context,
        goalkeeper,
        threat,
        can_challenge,
        ball_clearable,
        home_target,
        allow_active_response,
        store,
    )

    clearance_target = None
    goalkeeper_subaction = None
    if mode == GoalkeeperMode.BLOCK:
        target = _get_goalkeeper_block_target(context, ball_velocity)
        goalkeeper.guard(
            target,
            avoid_ball=False,
            avoid_robots=True,
            arrive_dist=GOALKEEPER_TRACK_ARRIVE_M,
        )
    elif mode == GoalkeeperMode.CHALLENGE and ball is not None:
        target = (ball.x, ball.y)
        goalkeeper.goalkeeper_challenge(target)
        goalkeeper_subaction = goalkeeper.action.removeprefix(
            "goalkeeper:challenge:",
        )
    elif mode == GoalkeeperMode.CLEAR:
        clearance_target = _get_safe_goalkeeper_clearance_target(context)
        target = (ball.x, ball.y) if ball is not None else home_target
        if clearance_target is None:
            goalkeeper.guard(home_target)
            goalkeeper_subaction = "fallback_guard"
        else:
            goalkeeper.goalkeeper_clear(
                clearance_target,
                GOALKEEPER_CLEAR_POWER,
            )
            goalkeeper_subaction = goalkeeper.action.removeprefix(
                "goalkeeper:clear:",
            )
    else:
        target = home_target
        goalkeeper.guard(
            target,
            avoid_ball=True,
            avoid_robots=True,
            arrive_dist=GOALKEEPER_TRACK_ARRIVE_M,
        )

    store.goalkeeper_target = target
    store.goalkeeper_clearance_target = clearance_target
    goalkeeper_kind = (
        "temporary"
        if goalkeeper.id == getattr(store, "temporary_goalkeeper_id", None)
        else "default"
    )
    goalkeeper.action = f"goalkeeper:{mode.value}:{goalkeeper_kind}"
    if goalkeeper_subaction is not None:
        goalkeeper.action = f"{goalkeeper.action}:{goalkeeper_subaction}"
    _draw_goalkeeper_strategy(
        context,
        goalkeeper,
        mode,
        target,
        threat,
        clearance_target,
        store,
    )


def _estimate_open_play_mode(
    context: Context,
    field_players: list[Player],
    previous_mode: OpenPlayMode | None,
) -> OpenPlayModeEstimate:
    """综合门前危险、双方到球距离和球场区域估计普通比赛模式。"""
    ball = context.ball
    our_nearest_distance = _nearest_available_teammate_distance(
        context, field_players,
    )
    opponent_nearest_distance = _nearest_opponent_distance(context)
    distance_advantage = (
        opponent_nearest_distance - our_nearest_distance
        if our_nearest_distance is not None
        and opponent_nearest_distance is not None
        else None
    )
    ball_in_danger_area = _ball_in_own_danger_area(context)

    if ball_in_danger_area:
        candidate_mode = OpenPlayMode.DEFENDING
        reason = "own_danger_area"
    elif distance_advantage is not None:
        if distance_advantage >= OPEN_PLAY_ATTACK_DISTANCE_ADVANTAGE_M:
            candidate_mode = OpenPlayMode.ATTACKING
            reason = "own_distance_advantage"
        elif distance_advantage <= -OPEN_PLAY_DEFENSE_DISTANCE_ADVANTAGE_M:
            candidate_mode = OpenPlayMode.DEFENDING
            reason = "opponent_distance_advantage"
        elif abs(distance_advantage) <= OPEN_PLAY_CONTESTED_DISTANCE_BAND_M:
            candidate_mode = OpenPlayMode.CONTESTED
            reason = "balanced_ball_distance"
        elif ball is not None and ball.x >= OPEN_PLAY_CONTESTED_BAND_M:
            candidate_mode = OpenPlayMode.ATTACKING
            reason = "opponent_half_no_clear_opponent_advantage"
        elif previous_mode is not None:
            candidate_mode = previous_mode
            reason = "hold_hysteresis"
        else:
            candidate_mode = OpenPlayMode.CONTESTED
            reason = "uncertain_distance_advantage"
    elif our_nearest_distance is not None:
        if ball is not None and ball.x >= -OPEN_PLAY_CONTESTED_BAND_M:
            candidate_mode = OpenPlayMode.ATTACKING
            reason = "opponent_data_missing_safe_ball"
        else:
            candidate_mode = OpenPlayMode.CONTESTED
            reason = "opponent_data_missing_backfield"
    elif opponent_nearest_distance is not None:
        candidate_mode = OpenPlayMode.DEFENDING
        reason = "no_available_field_player"
    else:
        candidate_mode = OpenPlayMode.CONTESTED
        reason = "insufficient_pose_data"

    return OpenPlayModeEstimate(
        candidate_mode=candidate_mode,
        reason=reason,
        our_nearest_ball_distance=our_nearest_distance,
        opponent_nearest_ball_distance=opponent_nearest_distance,
        distance_advantage=distance_advantage,
        ball_in_own_danger_area=ball_in_danger_area,
    )


def _update_open_play_mode(
    context: Context,
    estimate: OpenPlayModeEstimate,
    store,
) -> OpenPlayMode:
    """应用最短保持和危险区抢占，返回本帧稳定战术模式。"""
    current_mode = getattr(store, "open_play_mode", None)
    mode_entered_at = getattr(store, "open_play_mode_entered_at", None)

    store.open_play_our_ball_distance = estimate.our_nearest_ball_distance
    store.open_play_opponent_ball_distance = (
        estimate.opponent_nearest_ball_distance
    )
    store.open_play_distance_advantage = estimate.distance_advantage

    danger_forces_defense = (
        estimate.ball_in_own_danger_area
        and estimate.candidate_mode == OpenPlayMode.DEFENDING
    )
    mode_has_been_held_long_enough = (
        mode_entered_at is None
        or context.now - mode_entered_at >= OPEN_PLAY_MODE_MIN_HOLD_SEC
    )
    should_switch = (
        current_mode is None
        or (
            estimate.candidate_mode != current_mode
            and (danger_forces_defense or mode_has_been_held_long_enough)
        )
    )

    if should_switch:
        previous_mode = current_mode
        current_mode = estimate.candidate_mode
        store.open_play_mode = current_mode
        store.open_play_mode_entered_at = context.now
        store.open_play_last_switch_at = context.now
        store.open_play_mode_reason = estimate.reason
        store.open_play_last_switch_reason = estimate.reason
        _log.info(
            "open play mode %s -> %s reason=%s our_distance=%s "
            "opponent_distance=%s advantage=%s",
            previous_mode.value if previous_mode is not None else "none",
            current_mode.value,
            estimate.reason,
            estimate.our_nearest_ball_distance,
            estimate.opponent_nearest_ball_distance,
            estimate.distance_advantage,
        )
    elif estimate.candidate_mode == current_mode:
        store.open_play_mode_reason = estimate.reason
    else:
        store.open_play_mode_reason = "hold_hysteresis"

    return current_mode


def _format_open_play_distance(distance: float | None) -> str:
    return "n/a" if distance is None else f"{distance:.2f}"


def _draw_open_play_mode(context: Context, store) -> None:
    """在场外显示当前模式、原因和双方最近到球距离。"""
    from .framework import debugdraw

    mode = getattr(store, "open_play_mode", None)
    if mode is None:
        return
    reason = getattr(store, "open_play_mode_reason", "unknown")
    our_distance = _format_open_play_distance(
        getattr(store, "open_play_our_ball_distance", None),
    )
    opponent_distance = _format_open_play_distance(
        getattr(store, "open_play_opponent_ball_distance", None),
    )
    advantage = _format_open_play_distance(
        getattr(store, "open_play_distance_advantage", None),
    )
    debugdraw.text(
        0.0,
        context.field.width / 2.0 + 0.55,
        (
            f"mode={mode.value} mode_reason={reason} "
            f"our={our_distance} opp={opponent_distance} adv={advantage}"
        ),
        rgb=(0.4, 1.0, 1.0),
        ns="open_play_mode",
    )


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


def _act_normal_defense_pressure(
    context: Context,
    pressure_player: Player,
) -> None:
    """Directly close down the ball, then clear it toward the opponent goal."""
    ball = context.ball
    pose = pressure_player.pose
    if ball is None or pose is None:
        pressure_player.action = "defense:no_ball"
        pressure_player.stop()
        return

    ball_distance = dist(pose.x, pose.y, ball.x, ball.y)
    if ball_distance > NORMAL_DEFENSE_PRESSURE_CLEAR_DISTANCE_M:
        ball_direction = angle_to(pose.x, pose.y, ball.x, ball.y)
        pressure_player.walk_to(
            (ball.x, ball.y),
            face=ball_direction,
            avoid_ball=False,
            avoid_robots=False,
        )
        pressure_player.action = "defense:pressure:chase"
        return

    kick_plan = pressure_player.plan_kick()
    if kick_plan is None:
        pressure_player.action = "defense:no_ball"
        pressure_player.stop()
        return

    kick_direction, kick_power = kick_plan
    pressure_player.kick(kick_direction, kick_power)
    pressure_player.action = "defense:pressure:clear"


def _act_normal_defense(
    context: Context,
    goalkeeper: Player | None,
    pressure_player: Player | None,
    protect_player: Player | None,
    store,
) -> None:
    """按已分配职责执行普通比赛的守门、逼抢和保护。"""
    if pressure_player is not None:
        _act_normal_defense_pressure(context, pressure_player)

    if protect_player is not None:
        protect_target = _get_normal_defense_protect_target(context)
        protect_player.move_to_position(protect_target)
        protect_player.action = "defense:protect"


def _act_normal_contested_shape(
    context: Context,
    goalkeeper: Player | None,
    challenge_player: Player | None,
    protect_player: Player | None,
    store,
) -> None:
    """执行争议球安全结构：一人处理球，另一人保护球门方向中路。"""
    if challenge_player is not None:
        _act_normal_defense_pressure(context, challenge_player)
        challenge_subaction = challenge_player.action.removeprefix(
            "defense:pressure:",
        )
        challenge_player.action = f"contested:challenge:{challenge_subaction}"

    if protect_player is not None:
        protect_target = _get_normal_defense_protect_target(context)
        protect_player.move_to_position(protect_target)
        protect_player.action = "contested:protect"


def _act_normal(
    context: Context,
    players: list[Player],
    goalkeeper: Player | None,
    store,
    *,
    allow_ball_search: bool = True,
) -> None:
    """NORMAL:稳定选择进攻、防守或争议球结构后执行对应动作。

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
                _act_goalkeeper_guard(
                    context,
                    role_goalkeeper,
                    assigned_field_players,
                    store,
                    allow_active_response=False,
                )
            for player in assigned_field_players:
                player.action = "ball_unknown:stop"
                player.stop()
        return

    if not allow_ball_search:
        # OUR_SET_PLAY 暂时复用该入口，但固定战术不进入普通比赛三态。
        if role_goalkeeper is not None:
            _act_goalkeeper_guard(
                context,
                role_goalkeeper,
                assigned_field_players,
                store,
                allow_active_response=False,
            )
        if primary_attacker is not None:
            _act_normal_primary_attacker(primary_attacker)
        if front_partner is not None:
            _act_normal_front_partner(front_partner)
        return

    mode_estimate = _estimate_open_play_mode(
        context,
        assigned_field_players,
        getattr(store, "open_play_mode", None),
    )
    open_play_mode = _update_open_play_mode(
        context, mode_estimate, store,
    )
    _draw_open_play_mode(context, store)

    challenge_player = primary_attacker
    protect_player = front_partner
    if (
        open_play_mode in (OpenPlayMode.DEFENDING, OpenPlayMode.CONTESTED)
        and assigned_field_players
    ):
        challenge_player = min(
            assigned_field_players,
            key=lambda player: _player_dist_to_ball(context, player),
        )
        protect_player = next(
            (
                player for player in assigned_field_players
                if player is not challenge_player
            ),
            None,
        )
        store.normal_attacker = challenge_player.id

    if (
        assignment.availability == OpenPlayAvailability.DEGRADED_ONE
        and role_goalkeeper is not None
        and primary_attacker is None
    ):
        # 唯一可用者既然承担守门身份，就不再降级为普通 primary attacker。
        _act_goalkeeper_guard(
            context,
            role_goalkeeper,
            assigned_field_players,
            store,
            allow_active_response=True,
        )
        return

    if role_goalkeeper is not None:
        _act_goalkeeper_guard(
            context,
            role_goalkeeper,
            assigned_field_players,
            store,
            allow_active_response=True,
        )

    if open_play_mode == OpenPlayMode.ATTACKING:
        _act_normal_attacking_shape(
            context,
            role_goalkeeper,
            primary_attacker,
            front_partner,
            store,
        )
        return
    if open_play_mode == OpenPlayMode.DEFENDING:
        _act_normal_defense(
            context,
            role_goalkeeper,
            challenge_player,
            protect_player,
            store,
        )
        return

    _act_normal_contested_shape(
        context,
        role_goalkeeper,
        challenge_player,
        protect_player,
        store,
    )


def _act_goalkeeper_guard(
    context: Context,
    goalkeeper: Player,
    field_players: list[Player],
    store,
    *,
    allow_active_response: bool,
) -> None:
    """统一守门入口；固定重启只允许保守 HOLD/TRACK。"""
    _act_goalkeeper_strategy(
        context,
        goalkeeper,
        field_players,
        store,
        allow_active_response=allow_active_response,
    )


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
        _act_goalkeeper_guard(
            context,
            goalkeeper,
            field_players,
            store,
            allow_active_response=False,
        )
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

    field_players = [
        player for player in players if player is not goalkeeper
    ]
    if goalkeeper is not None:
        _act_goalkeeper_guard(
            context,
            goalkeeper,
            field_players,
            store,
            allow_active_response=False,
        )
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
            _act_goalkeeper_guard(
                context,
                goalkeeper,
                field_players,
                store,
                allow_active_response=False,
            )
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

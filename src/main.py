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
    OPP_KICKOFF = "opp_kickoff"    # 对方开球(球动前等待)
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


class KickoffTacticState(Enum):
    """我方中场固定开球一传一射的跨帧状态。"""

    IDLE = "idle"
    SETUP = "setup"
    WAIT_FOR_PLAYING = "wait_for_playing"
    ALIGN_PASSER = "align_passer"
    PASS = "pass"
    VERIFY_FIRST_TOUCH = "verify_first_touch"
    RECEIVE_AND_SHOOT = "receive_and_shoot"
    VERIFY_SECOND_TOUCH = "verify_second_touch"
    COMPLETE = "complete"
    ABORT_BEFORE_FIRST_TOUCH = "abort_before_first_touch"
    ABORT_AFTER_FIRST_TOUCH = "abort_after_first_touch"
    ABORT_TO_DEFENSE = "abort_to_defense"
    ABORT_STOP = "abort_stop"


@dataclass(frozen=True)
class OpenPlayRoleAssignment:
    """一帧普通比赛的基础职责分配结果。"""

    goalkeeper_id: int | None
    primary_attacker_id: int | None
    front_partner_id: int | None
    available_player_ids: tuple[int, ...]
    availability: OpenPlayAvailability


@dataclass(frozen=True)
class KickoffRoles:
    """固定开球锁定职责和镜像布局。"""

    passer_id: int
    shooter_id: int
    side_sign: float
    passer_setup: tuple[float, float]
    shooter_setup: tuple[float, float]
    receive_target: tuple[float, float]


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
            if _kickoff_ball_has_moved(context):
                return Phase.NORMAL
            return Phase.OPP_KICKOFF

        # 正常拼抢
        return Phase.NORMAL

    # SET / INITIAL / FINISHED:站定
    return Phase.STOPPED


def _kickoff_ball_has_moved(context: Context) -> bool:
    """对方开球约束解除条件：球离开中点后，我方才恢复移动。"""
    ball = context.ball
    if ball is None:
        return False
    return dist(ball.x, ball.y, 0.0, 0.0) >= CENTER_LEAVE_DIST_M


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
        store.deep_defense_active = False
        store.deep_defense_entered_at = None
        store.deep_defense_primary_id = None
        store.deep_defense_secondary_id = None
        store.deep_defense_secondary_target = None
        store.deep_defense_clearance_target = None
        store.deep_defense_clearance_level = None
        store.deep_defense_clearance_power = None
        store.deep_defense_goalkeeper_priority = False
        store.deep_defense_crowded = False
        store.deep_defense_crowded_entered_at = None
        store.deep_defense_crowd_count = 0
        store.deep_defense_ball_owner_id = None
        store.deep_defense_outward_target = None
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
        store.goalkeeper_clear_kicked_at = None
        store.goalkeeper_clear_ball_x_at_kick = None
        store.goalkeeper_handover_candidate_id = None
        store.goalkeeper_handover_requested_at = None
        store.goalkeeper_last_handover_at = None
        store.goalkeeper_post_clear_attacker_id = None
        store.goalkeeper_post_clear_attack_until = None
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
        store.kickoff_tactic_state = KickoffTacticState.IDLE
        store.kickoff_roles = None
        store.kickoff_state_entered_at = None
        store.kickoff_tactic_started_at = None
        store.kickoff_first_touch_confirmed = False
        store.kickoff_second_touch_confirmed = False
        store.kickoff_pass_start_ball = None
        store.kickoff_pass_start_seen_at = None
        store.kickoff_pass_attempts = 0
        store.kickoff_last_pass_attempt_at = None
        store.kickoff_abort_reason = None
        store.kickoff_ready_passer_arrived = False
        store.kickoff_ready_shooter_arrived = False
        store.kickoff_safe_first_touch_player_id = None
        store.kickoff_safe_first_touch_done = False

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
            _reset_goalkeeper_handover_state(store)

        # 按 phase 对整队分派一次(角色分配等全队计算只在 _act_* 里算一次)。
        if phase == Phase.NORMAL:
            _clear_kickoff_tactic(store, "referee_window_cleared")
            _act_normal(context, available_players, current_goalkeeper, store)
        elif phase == Phase.OUR_KICKOFF:
            _clear_normal_sticky(store)
            _act_our_kickoff(
                context, available_players, current_goalkeeper, store,
            )
        elif phase == Phase.OPP_KICKOFF:
            _clear_normal_sticky(store)
            _clear_kickoff_tactic(store, "opponent_kickoff")
            _act_opp_kickoff(context, available_players, current_goalkeeper)
        elif phase == Phase.OUR_SET_PLAY:
            _clear_normal_sticky(store)
            _clear_kickoff_tactic(store, "our_set_play")
            _act_our_set_play(
                context, available_players, current_goalkeeper, store,
            )
        elif phase == Phase.OPP_SET_PLAY:
            _clear_normal_sticky(store)
            _clear_kickoff_tactic(store, "opponent_set_play")
            _act_opp_set_play(
                context, available_players, current_goalkeeper, store,
            )
        elif phase == Phase.READY:
            _clear_normal_sticky(store)
            _act_ready(context, available_players, current_goalkeeper, store)
        elif phase == Phase.STOPPED:
            _clear_normal_sticky(store)
            if _is_our_kickoff_set(context):
                if getattr(store, "kickoff_roles", None) is not None:
                    _enter_kickoff_state(
                        store,
                        KickoffTacticState.WAIT_FOR_PLAYING,
                        context.now,
                    )
                _draw_kickoff_tactic(context, store)
            else:
                _clear_kickoff_tactic(store, "stopped_not_our_kickoff_set")
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
    """统一选择守门员，并在 NORMAL 中原子应用已批准的主动交接。"""
    default_goalkeeper_id = _resolve_default_goalkeeper_id(context, all_players)
    store.default_goalkeeper_id = default_goalkeeper_id

    available_by_id = {
        player.id: player for player in available_players
    }
    default_goalkeeper = available_by_id.get(default_goalkeeper_id)
    temporary_goalkeeper_id = getattr(
        store, "temporary_goalkeeper_id", None,
    )

    pending_handover_id = getattr(
        store, "goalkeeper_handover_candidate_id", None,
    )
    previous_goalkeeper_id = getattr(store, "current_goalkeeper_id", None)
    if phase == Phase.NORMAL and pending_handover_id is not None:
        handover_goalkeeper = available_by_id.get(pending_handover_id)
        previous_goalkeeper = available_by_id.get(previous_goalkeeper_id)
        if (
            handover_goalkeeper is not None
            and previous_goalkeeper is not None
            and handover_goalkeeper.id != previous_goalkeeper_id
            and _goalkeeper_handover_is_still_valid(
                context,
                previous_goalkeeper,
                handover_goalkeeper,
                store,
            )
        ):
            temporary_goalkeeper_id = (
                None
                if handover_goalkeeper.id == default_goalkeeper_id
                else handover_goalkeeper.id
            )
            store.goalkeeper_post_clear_attacker_id = previous_goalkeeper.id
            store.goalkeeper_post_clear_attack_until = (
                context.now + GOALKEEPER_POST_CLEAR_ATTACK_SEC
            )
            store.normal_attacker = previous_goalkeeper.id
            store.goalkeeper_last_handover_at = context.now
            _log.info(
                "goalkeeper handover %s -> %s after successful clearance",
                previous_goalkeeper_id,
                handover_goalkeeper.id,
            )
        store.goalkeeper_handover_candidate_id = None
        store.goalkeeper_handover_requested_at = None
    elif pending_handover_id is not None:
        store.goalkeeper_handover_candidate_id = None
        store.goalkeeper_handover_requested_at = None

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
    store.deep_defense_active = False
    store.deep_defense_entered_at = None
    store.deep_defense_primary_id = None
    store.deep_defense_secondary_id = None
    store.deep_defense_secondary_target = None
    store.deep_defense_clearance_target = None
    store.deep_defense_clearance_level = None
    store.deep_defense_clearance_power = None
    store.deep_defense_goalkeeper_priority = False
    store.deep_defense_crowded = False
    store.deep_defense_crowded_entered_at = None
    store.deep_defense_crowd_count = 0
    store.deep_defense_ball_owner_id = None
    store.deep_defense_outward_target = None
    _reset_goalkeeper_handover_state(store)


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
    store.goalkeeper_clear_kicked_at = None
    store.goalkeeper_clear_ball_x_at_kick = None


def _reset_goalkeeper_handover_state(store) -> None:
    """清除只允许在 NORMAL 中存活的交接请求和反击窗口。"""
    store.goalkeeper_handover_candidate_id = None
    store.goalkeeper_handover_requested_at = None
    store.goalkeeper_post_clear_attacker_id = None
    store.goalkeeper_post_clear_attack_until = None


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


def _get_post_clear_attacker(
    context: Context,
    field_players: list[Player],
    store,
) -> Player | None:
    """返回仍处于有效反击窗口的原守门员，否则清除过期状态。"""
    attacker_id = getattr(store, "goalkeeper_post_clear_attacker_id", None)
    attack_until = getattr(store, "goalkeeper_post_clear_attack_until", None)
    attack_window_valid = (
        attacker_id is not None
        and attack_until is not None
        and context.now <= attack_until
        and context.ball is not None
        and not _ball_in_own_danger_area(context)
    )
    if attack_window_valid:
        attacker = next(
            (player for player in field_players if player.id == attacker_id),
            None,
        )
        if attacker is not None:
            return attacker

    store.goalkeeper_post_clear_attacker_id = None
    store.goalkeeper_post_clear_attack_until = None
    return None


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
        primary_attacker = _get_post_clear_attacker(
            context,
            field_players,
            store,
        )
        if primary_attacker is None:
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
    *,
    force_primary_handler: bool = False,
) -> None:
    """执行普通比赛双前场进攻形态及安全人数降级。"""
    if primary_attacker is None:
        return
    if front_partner is None:
        role_label = (
            "post_clear_goalkeeper"
            if force_primary_handler else "solo"
        )
        _act_attack_ball_handler(primary_attacker, role_label)
        return

    if not force_primary_handler and _should_front_partner_challenge(
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
    role_label = (
        "post_clear_goalkeeper"
        if force_primary_handler else "primary"
    )
    _act_attack_ball_handler(primary_attacker, role_label)
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


def _ball_in_single_player_guard_area(context: Context) -> bool:
    """判断单机器人降级时球是否仍值得优先守门。"""
    ball = context.ball
    if ball is None:
        return True

    own_goal_line_x = -context.field.length / 2.0
    own_penalty_edge_x = own_goal_line_x + context.field.penalty_area_length
    danger_half_width = (
        context.field.penalty_area_width / 2.0
        + OPEN_PLAY_DANGER_LATERAL_MARGIN_M
    )
    return (
        ball.x <= own_penalty_edge_x
        and abs(ball.y) <= danger_half_width
    )


def _should_single_player_guard(context: Context, player: Player) -> bool:
    """单台 available 时，在门前风险仍高时守门，否则允许普通追球。"""
    pose = player.pose
    if pose is None or context.ball is None:
        return True
    if _ball_in_single_player_guard_area(context):
        return True

    own_goal_x, own_goal_y = own_goal(context)
    goalkeeper_protection_radius = (
        context.field.penalty_area_length
        + SINGLE_PLAYER_GOAL_PROTECTION_MARGIN_M
    )
    return (
        dist(pose.x, pose.y, own_goal_x, own_goal_y)
        <= goalkeeper_protection_radius
    )


def _ball_in_deep_defensive_danger(
    context: Context,
    *,
    was_active: bool,
) -> bool:
    """判断球是否进入比普通防守更严格的门前深度危险区。"""
    ball = context.ball
    if ball is None:
        return False

    hysteresis_margin = (
        DEEP_DEFENSE_EXIT_MARGIN_M if was_active else 0.0
    )
    own_goal_x, own_goal_y = own_goal(context)
    penalty_front_x = (
        own_goal_x
        + context.field.penalty_area_length
        + DEEP_DEFENSE_X_MARGIN_M
        + hysteresis_margin
    )
    penalty_lateral_limit = (
        context.field.penalty_area_width / 2.0
        + DEEP_DEFENSE_LATERAL_MARGIN_M
        + hysteresis_margin
    )
    field_lateral_limit = max(
        0.0,
        context.field.width / 2.0 - NORMAL_DEFENSE_FIELD_MARGIN_M,
    )
    lateral_limit = min(
        DEEP_DEFENSE_MAX_ABS_Y_M + hysteresis_margin,
        penalty_lateral_limit,
        field_lateral_limit,
    )
    goal_distance_limit = (
        DEEP_DEFENSE_GOAL_DISTANCE_M + hysteresis_margin
    )
    ball_goal_distance = dist(
        ball.x,
        ball.y,
        own_goal_x,
        own_goal_y,
    )
    return (
        ball.x <= penalty_front_x
        and abs(ball.y) <= lateral_limit
        and ball_goal_distance <= goal_distance_limit
    )


def _update_deep_defense_state(context: Context, store) -> bool:
    """应用深度危险区退出余量，并维护可观察的跨帧状态。"""
    previous_active = bool(getattr(store, "deep_defense_active", False))
    active = _ball_in_deep_defensive_danger(
        context,
        was_active=previous_active,
    )
    if active != previous_active:
        store.deep_defense_entered_at = context.now if active else None
        _log.info(
            "deep defense %s -> %s",
            previous_active,
            active,
        )

    store.deep_defense_active = active
    if not active:
        store.deep_defense_primary_id = None
        store.deep_defense_secondary_id = None
        store.deep_defense_secondary_target = None
        store.deep_defense_clearance_target = None
        store.deep_defense_clearance_level = None
        store.deep_defense_clearance_power = None
        store.deep_defense_goalkeeper_priority = False
        store.deep_defense_crowded = False
        store.deep_defense_crowded_entered_at = None
        store.deep_defense_crowd_count = 0
        store.deep_defense_ball_owner_id = None
        store.deep_defense_outward_target = None
    return active


def _count_robots_near_ball(context: Context, radius: float) -> int:
    """统计球附近具有有效位姿的双方机器人，包含倒地或不可用的物理障碍。"""
    ball = context.ball
    if ball is None:
        return 0

    nearby_count = 0
    for robot in (
        list(context.teammates.values())
        + list(context.opponents.values())
    ):
        pose = robot.pose
        if (
            pose is not None
            and dist(pose.x, pose.y, ball.x, ball.y) <= radius
        ):
            nearby_count += 1
    return nearby_count


def _update_deep_defense_crowding(context: Context, store) -> bool:
    """根据球周围人数更新拥挤子模式，并使用人数迟滞防止频繁切换。

    语义为达到 ``DEEP_DEFENSE_CROWD_ENTER_COUNT`` 进入拥挤；进入后人数
    降到 ``DEEP_DEFENSE_CROWD_EXIT_COUNT`` 或更少即退出。这样 3 人混战进入，
    下降到 2 人时恢复普通深度防守结构。
    """
    crowd_count = _count_robots_near_ball(
        context,
        DEEP_DEFENSE_CROWD_RADIUS_M,
    )
    previous_crowded = bool(
        getattr(store, "deep_defense_crowded", False),
    )
    if previous_crowded:
        crowded = crowd_count > DEEP_DEFENSE_CROWD_EXIT_COUNT
    else:
        crowded = crowd_count >= DEEP_DEFENSE_CROWD_ENTER_COUNT

    if crowded != previous_crowded:
        store.deep_defense_crowded_entered_at = (
            context.now if crowded else None
        )
        _log.info(
            "deep defense crowded %s -> %s nearby=%d",
            previous_crowded,
            crowded,
            crowd_count,
        )

    store.deep_defense_crowded = crowded
    store.deep_defense_crowd_count = crowd_count
    if not crowded:
        store.deep_defense_outward_target = None
    return crowded


def _get_controlled_clearance_target(
    context: Context,
    *,
    emergency: bool,
) -> tuple[float, float] | None:
    """计算正 X 前方且在边线附近向中路回收的场上机器人解围目标。"""
    ball = context.ball
    if ball is None:
        return None

    half_length = (
        context.field.length / 2.0 - DEFENSIVE_CLEAR_FIELD_MARGIN_M
    )
    half_width = max(
        0.0,
        context.field.width / 2.0 - DEFENSIVE_CLEAR_FIELD_MARGIN_M,
    )
    if half_length <= ball.x:
        return None
    forward_distance = (
        DEFENSIVE_CLEAR_EMERGENCY_FORWARD_DISTANCE_M
        if emergency else DEFENSIVE_CLEAR_FORWARD_DISTANCE_M
    )
    minimum_target_x = min(ball.x + 0.75, half_length)
    target_x = clamp(
        ball.x + forward_distance,
        minimum_target_x,
        half_length,
    )

    distance_to_touchline = half_width - abs(ball.y)
    if distance_to_touchline <= DEFENSIVE_CLEAR_TOUCHLINE_ZONE_M:
        center_direction = -1.0 if ball.y > 0.0 else 1.0
        target_y = ball.y + (
            center_direction * DEFENSIVE_CLEAR_CENTERING_DISTANCE_M
        )
    else:
        target_y = ball.y * 0.35
    target_y = clamp(target_y, -half_width, half_width)
    return (target_x, target_y)


def _is_emergency_defensive_clearance(context: Context, store) -> bool:
    """仅把门线近距或 T07 快速射门封堵视为强力紧急解围。"""
    ball = context.ball
    if ball is None:
        return False

    own_goal_x, own_goal_y = own_goal(context)
    ball_goal_distance = dist(
        ball.x,
        ball.y,
        own_goal_x,
        own_goal_y,
    )
    in_goal_mouth_channel = (
        abs(ball.y)
        <= context.field.goal_width / 2.0
        + DEFENSIVE_EMERGENCY_LATERAL_MARGIN_M
    )
    immediate_goal_line_threat = (
        ball_goal_distance <= DEFENSIVE_EMERGENCY_GOAL_DISTANCE_M
        and in_goal_mouth_channel
    )
    goalkeeper_blocking_fast_shot = (
        getattr(store, "goalkeeper_mode", None) == GoalkeeperMode.BLOCK
        and getattr(store, "goalkeeper_threat_reason", "")
        == "fast_shot_projection"
    )
    return immediate_goal_line_threat or goalkeeper_blocking_fast_shot


def _draw_deep_defense_state(context: Context, store) -> None:
    """显示深度防守职责、解围级别和守门员优先状态。"""
    from .framework import debugdraw

    if not getattr(store, "deep_defense_active", False):
        return

    primary_id = getattr(store, "deep_defense_primary_id", None)
    secondary_id = getattr(store, "deep_defense_secondary_id", None)
    clearance_level = getattr(
        store,
        "deep_defense_clearance_level",
        None,
    )
    clearance_power = getattr(
        store,
        "deep_defense_clearance_power",
        None,
    )
    clearance_power_label = (
        "-" if clearance_power is None else f"{clearance_power:.1f}"
    )
    goalkeeper_priority = getattr(
        store,
        "deep_defense_goalkeeper_priority",
        False,
    )
    crowded = getattr(store, "deep_defense_crowded", False)
    crowd_count = getattr(store, "deep_defense_crowd_count", 0)
    ball_owner_id = getattr(store, "deep_defense_ball_owner_id", None)
    debugdraw.text(
        0.0,
        context.field.width / 2.0 + 1.25,
        (
            f"deep_defense=on primary={primary_id or '-'} "
            f"secondary={secondary_id or '-'} "
            f"clear={clearance_level or '-'} "
            f"power={clearance_power_label} "
            f"goalkeeper_priority={goalkeeper_priority} "
            f"crowded={crowded} count={crowd_count} "
            f"owner={ball_owner_id or '-'}"
        ),
        rgb=(1.0, 0.55, 0.15),
        ns="deep_defense",
    )

    ball = context.ball
    clearance_target = getattr(
        store,
        "deep_defense_clearance_target",
        None,
    )
    if ball is not None and clearance_target is not None:
        debugdraw.line(
            [(ball.x, ball.y), clearance_target],
            rgb=(1.0, 0.65, 0.15),
            ns="deep_defense_clearance",
        )

    secondary_target = getattr(
        store,
        "deep_defense_secondary_target",
        None,
    )
    if secondary_target is not None:
        debugdraw.point(
            secondary_target[0],
            secondary_target[1],
            rgb=(1.0, 0.3, 0.7),
            scale=0.18,
            ns="deep_defense_secondary_target",
        )

    outward_target = getattr(
        store,
        "deep_defense_outward_target",
        None,
    )
    if outward_target is not None:
        debugdraw.point(
            outward_target[0],
            outward_target[1],
            rgb=(1.0, 0.2, 0.2),
            scale=0.20,
            ns="deep_defense_outward_target",
        )


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


def _goalkeeper_handover_is_still_valid(
    context: Context,
    previous_goalkeeper: Player,
    handover_goalkeeper: Player,
    store,
) -> bool:
    """在原子应用交接前重新检查球的安全性和候选位置优势。"""
    ball = context.ball
    previous_pose = previous_goalkeeper.pose
    handover_pose = handover_goalkeeper.pose
    requested_at = getattr(store, "goalkeeper_handover_requested_at", None)
    if (
        ball is None
        or previous_pose is None
        or handover_pose is None
        or requested_at is None
        or context.now - requested_at
        > GOALKEEPER_HANDOVER_CONFIRM_TIMEOUT_SEC
        or _ball_in_own_danger_area(context)
    ):
        return False

    ball_velocity = _estimate_goalkeeper_ball_velocity(context, store)
    threat = _estimate_goalkeeper_threat(
        context,
        ball_velocity,
        _nearest_opponent_distance(context),
    )
    if threat.fast_goal_threat or threat.position_threat:
        return False

    previous_goalkeeper_ball_distance = dist(
        previous_pose.x,
        previous_pose.y,
        ball.x,
        ball.y,
    )
    if (
        previous_goalkeeper_ball_distance
        > GOALKEEPER_HANDOVER_MAX_ATTACK_DISTANCE_M
    ):
        return False

    home_target = _get_goalkeeper_home_target(context)
    handover_home_distance = dist(
        handover_pose.x,
        handover_pose.y,
        home_target[0],
        home_target[1],
    )
    previous_home_distance = dist(
        previous_pose.x,
        previous_pose.y,
        home_target[0],
        home_target[1],
    )
    return (
        handover_home_distance
        <= GOALKEEPER_HANDOVER_MAX_HOME_DISTANCE_M
        and handover_home_distance
        + GOALKEEPER_HANDOVER_POSITION_ADVANTAGE_M
        < previous_home_distance
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


def _select_goalkeeper_handover_candidate(
    context: Context,
    goalkeeper: Player,
    field_players: list[Player],
    home_target: tuple[float, float],
    store,
) -> Player | None:
    """选择已明显比当前守门员更适合占据动态 home 的场上机器人。"""
    goalkeeper_pose = goalkeeper.pose
    if goalkeeper_pose is None or not field_players:
        return None

    last_handover_at = getattr(store, "goalkeeper_last_handover_at", None)
    if (
        last_handover_at is not None
        and context.now - last_handover_at
        < GOALKEEPER_HANDOVER_COOLDOWN_SEC
    ):
        return None

    candidates = [
        player for player in field_players if player.pose is not None
    ]
    if not candidates:
        return None

    candidate = min(
        candidates,
        key=lambda player: dist(
            player.pose.x,
            player.pose.y,
            home_target[0],
            home_target[1],
        ),
    )
    candidate_home_distance = dist(
        candidate.pose.x,
        candidate.pose.y,
        home_target[0],
        home_target[1],
    )
    goalkeeper_home_distance = dist(
        goalkeeper_pose.x,
        goalkeeper_pose.y,
        home_target[0],
        home_target[1],
    )
    candidate_is_near_goal = (
        candidate_home_distance
        <= GOALKEEPER_HANDOVER_MAX_HOME_DISTANCE_M
    )
    candidate_has_position_advantage = (
        candidate_home_distance + GOALKEEPER_HANDOVER_POSITION_ADVANTAGE_M
        < goalkeeper_home_distance
    )
    if candidate_is_near_goal and candidate_has_position_advantage:
        return candidate
    return None


def _request_goalkeeper_handover_after_clear(
    context: Context,
    goalkeeper: Player,
    field_players: list[Player],
    home_target: tuple[float, float],
    threat: GoalkeeperThreatEstimate,
    allow_active_response: bool,
    store,
) -> None:
    """确认解围产生正 X 进展后，提交下一帧应用的激进交接请求。"""
    clear_kicked_at = getattr(store, "goalkeeper_clear_kicked_at", None)
    clear_ball_x_at_kick = getattr(
        store, "goalkeeper_clear_ball_x_at_kick", None,
    )
    if clear_kicked_at is None or clear_ball_x_at_kick is None:
        return

    if not allow_active_response:
        store.goalkeeper_clear_kicked_at = None
        store.goalkeeper_clear_ball_x_at_kick = None
        return

    confirmation_elapsed = context.now - clear_kicked_at
    if confirmation_elapsed < GOALKEEPER_HANDOVER_CONFIRM_MIN_SEC:
        return
    if confirmation_elapsed > GOALKEEPER_HANDOVER_CONFIRM_TIMEOUT_SEC:
        store.goalkeeper_clear_kicked_at = None
        store.goalkeeper_clear_ball_x_at_kick = None
        return

    ball = context.ball
    goalkeeper_pose = goalkeeper.pose
    if ball is None or goalkeeper_pose is None:
        store.goalkeeper_clear_kicked_at = None
        store.goalkeeper_clear_ball_x_at_kick = None
        return

    goalkeeper_ball_distance = dist(
        goalkeeper_pose.x,
        goalkeeper_pose.y,
        ball.x,
        ball.y,
    )
    if goalkeeper_ball_distance > GOALKEEPER_HANDOVER_MAX_ATTACK_DISTANCE_M:
        store.goalkeeper_clear_kicked_at = None
        store.goalkeeper_clear_ball_x_at_kick = None
        return

    clearance_confirmed = (
        ball.x - clear_ball_x_at_kick
        >= GOALKEEPER_HANDOVER_CLEAR_PROGRESS_M
        and not _ball_in_own_danger_area(context)
        and not threat.fast_goal_threat
        and not threat.position_threat
    )
    handover_already_pending = (
        getattr(store, "goalkeeper_handover_candidate_id", None) is not None
    )
    if not clearance_confirmed or handover_already_pending:
        return

    candidate = _select_goalkeeper_handover_candidate(
        context,
        goalkeeper,
        field_players,
        home_target,
        store,
    )
    if candidate is None:
        return

    store.goalkeeper_handover_candidate_id = candidate.id
    store.goalkeeper_handover_requested_at = context.now
    _log.info(
        "goalkeeper handover requested %s -> %s after clear progress %.2f",
        goalkeeper.id,
        candidate.id,
        ball.x - clear_ball_x_at_kick,
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
    handover_candidate = getattr(
        store, "goalkeeper_handover_candidate_id", None,
    )
    post_clear_attacker = getattr(
        store, "goalkeeper_post_clear_attacker_id", None,
    )
    debugdraw.text(
        0.0,
        context.field.width / 2.0 + 0.9,
        (
            f"goalkeeper={goalkeeper_kind} mode={mode.value} "
            f"reason={store.goalkeeper_threat_reason} speed={speed_label} "
            f"handover={handover_candidate or '-'} "
            f"counter={post_clear_attacker or '-'}"
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
    _request_goalkeeper_handover_after_clear(
        context,
        goalkeeper,
        field_players,
        home_target,
        threat,
        allow_active_response,
        store,
    )
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
            if (
                goalkeeper_subaction == "kick"
                and ball is not None
                and getattr(store, "goalkeeper_clear_kicked_at", None)
                is None
            ):
                store.goalkeeper_clear_kicked_at = context.now
                store.goalkeeper_clear_ball_x_at_kick = ball.x
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


def _get_defensive_clear_subaction(player: Player) -> str:
    if player.action.startswith("defensive_clear:"):
        return player.action.removeprefix("defensive_clear:")
    return "kick" if player.is_kicking else "approach"


def _act_quick_defensive_clear(
    player: Player,
    clearance_target: tuple[float, float],
    power: float,
    action_prefix: str,
    *,
    avoid_crowding: bool = False,
) -> None:
    """执行专用快速解围，并保留接近或出脚子状态。"""
    player.quick_defensive_clear(
        clearance_target,
        power,
        avoid_crowding=avoid_crowding,
    )
    subaction = _get_defensive_clear_subaction(player)
    player.action = f"{action_prefix}:{subaction}"


def _select_deep_defense_challengers(
    context: Context,
    field_players: list[Player],
    store,
) -> tuple[Player | None, Player | None, bool]:
    """稳定选择第一拼抢者，并在第二人明显更近时允许接管。"""
    if not field_players:
        store.deep_defense_primary_id = None
        store.deep_defense_secondary_id = None
        return (None, None, False)

    previous_primary_id = getattr(store, "deep_defense_primary_id", None)
    previous_secondary_id = getattr(
        store,
        "deep_defense_secondary_id",
        None,
    )
    preferred_primary = next(
        (
            player for player in field_players
            if player.id == previous_primary_id
        ),
        None,
    )
    nearest_player = min(
        field_players,
        key=lambda player: _player_dist_to_ball(context, player),
    )
    primary_player = preferred_primary or nearest_player
    takeover = False
    if preferred_primary is not None and nearest_player is not preferred_primary:
        preferred_distance = _player_dist_to_ball(
            context,
            preferred_primary,
        )
        nearest_distance = _player_dist_to_ball(context, nearest_player)
        if (
            nearest_distance + DEEP_DEFENSE_SECONDARY_TAKEOVER_MARGIN_M
            < preferred_distance
        ):
            primary_player = nearest_player
            takeover = True
    elif (
        previous_primary_id is not None
        and primary_player.id != previous_primary_id
        and primary_player.id == previous_secondary_id
    ):
        takeover = True

    secondary_player = next(
        (
            player for player in field_players
            if player is not primary_player
        ),
        None,
    )
    store.deep_defense_primary_id = primary_player.id
    store.deep_defense_secondary_id = (
        secondary_player.id if secondary_player is not None else None
    )
    store.normal_attacker = primary_player.id
    return (primary_player, secondary_player, takeover)


def _build_deep_defense_offset_target(
    context: Context,
    side: float,
    offset_distance: float = DEEP_DEFENSE_SECONDARY_OFFSET_M,
) -> tuple[float, float] | None:
    """在球的门侧附近生成一个横向错位的二点球目标。"""
    ball = context.ball
    if ball is None:
        return None

    own_goal_x, own_goal_y = own_goal(context)
    goal_to_ball_x = ball.x - own_goal_x
    goal_to_ball_y = ball.y - own_goal_y
    goal_to_ball_distance = math.hypot(goal_to_ball_x, goal_to_ball_y)
    if goal_to_ball_distance <= 1e-6:
        forward_x, forward_y = (1.0, 0.0)
    else:
        forward_x = goal_to_ball_x / goal_to_ball_distance
        forward_y = goal_to_ball_y / goal_to_ball_distance
    lateral_x, lateral_y = (-forward_y, forward_x)

    target_x = (
        ball.x
        - forward_x * DEEP_DEFENSE_SECONDARY_GOALWARD_OFFSET_M
        + lateral_x * side * offset_distance
    )
    target_y = (
        ball.y
        - forward_y * DEEP_DEFENSE_SECONDARY_GOALWARD_OFFSET_M
        + lateral_y * side * offset_distance
    )
    half_length = context.field.length / 2.0
    half_width = context.field.width / 2.0
    target_x = clamp(
        target_x,
        own_goal_x + NORMAL_DEFENSE_FIELD_MARGIN_M,
        half_length - NORMAL_DEFENSE_FIELD_MARGIN_M,
    )
    target_y = clamp(
        target_y,
        -half_width + NORMAL_DEFENSE_FIELD_MARGIN_M,
        half_width - NORMAL_DEFENSE_FIELD_MARGIN_M,
    )
    return (target_x, target_y)


def _select_deep_outward_wall_target(
    context: Context,
    wall_player: Player,
    ball_owner: Player | None,
    goalkeeper_target: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """选择球门侧外压点，让非触球者封住回门路线并避开球权所有者。"""
    ball = context.ball
    pose = wall_player.pose
    if ball is None or pose is None:
        return None

    own_goal_x, own_goal_y = own_goal(context)
    goal_side_x = own_goal_x - ball.x
    goal_side_y = own_goal_y - ball.y
    goal_side_length = math.hypot(goal_side_x, goal_side_y)
    if goal_side_length <= 1e-6:
        goal_side_direction = (-1.0, 0.0)
    else:
        goal_side_direction = (
            goal_side_x / goal_side_length,
            goal_side_y / goal_side_length,
        )
    lateral_direction = (
        -goal_side_direction[1],
        goal_side_direction[0],
    )

    half_length = context.field.length / 2.0
    half_width = context.field.width / 2.0
    minimum_x = own_goal_x + NORMAL_DEFENSE_FIELD_MARGIN_M
    maximum_x = half_length - NORMAL_DEFENSE_FIELD_MARGIN_M
    minimum_y = -half_width + NORMAL_DEFENSE_FIELD_MARGIN_M
    maximum_y = half_width - NORMAL_DEFENSE_FIELD_MARGIN_M
    expanded_lateral_distance = max(
        DEEP_DEFENSE_OUTWARD_WALL_LATERAL_M,
        DEEP_DEFENSE_NON_OWNER_RADIUS_M + 0.15,
    )

    candidates: list[tuple[float, float]] = []
    for goalward_distance in (
        DEEP_DEFENSE_OUTWARD_WALL_DISTANCE_M,
        DEEP_DEFENSE_OUTWARD_WALL_DISTANCE_M * 0.55,
    ):
        for lateral_distance in (
            DEEP_DEFENSE_OUTWARD_WALL_LATERAL_M,
            expanded_lateral_distance,
        ):
            for side in (-1.0, 1.0):
                target_x = (
                    ball.x
                    + goal_side_direction[0] * goalward_distance
                    + lateral_direction[0] * side * lateral_distance
                )
                target_y = (
                    ball.y
                    + goal_side_direction[1] * goalward_distance
                    + lateral_direction[1] * side * lateral_distance
                )
                candidates.append((
                    clamp(target_x, minimum_x, maximum_x),
                    clamp(target_y, minimum_y, maximum_y),
                ))

    owner_pose = ball_owner.pose if ball_owner is not None else None
    valid_candidates = [
        target for target in candidates
        if _deep_non_owner_target_is_safe(
            context,
            target,
            goalkeeper_target=goalkeeper_target,
            owner_pose=owner_pose,
        )
    ]
    if not valid_candidates:
        return None

    def candidate_score(target: tuple[float, float]) -> float:
        travel_distance = dist(pose.x, pose.y, target[0], target[1])
        goal_side_progress = (
            (target[0] - ball.x) * goal_side_direction[0]
            + (target[1] - ball.y) * goal_side_direction[1]
        )
        center_recovery = abs(ball.y) - abs(target[1])
        return (
            goal_side_progress * 2.0
            + center_recovery * 0.25
            - travel_distance
        )

    return max(valid_candidates, key=candidate_score)


def _act_deep_outward_wall(
    context: Context,
    wall_player: Player,
    ball_owner: Player | None,
    store,
) -> None:
    """让非触球防守者退出球周围，站到门侧并形成向正 X 的外压支撑。"""
    target = _select_deep_outward_wall_target(
        context,
        wall_player,
        ball_owner,
        getattr(store, "goalkeeper_target", None),
    )
    store.deep_defense_outward_target = target
    store.deep_defense_secondary_target = target
    ball = context.ball
    pose = wall_player.pose
    if target is None or ball is None or pose is None:
        wall_player.action = "defense:deep_outward_wall:stop"
        wall_player.stop()
        return

    ball_distance = dist(pose.x, pose.y, ball.x, ball.y)
    wall_player.walk_to(
        target,
        face=angle_to(pose.x, pose.y, ball.x, ball.y),
        avoid_ball=True,
        avoid_robots=True,
    )
    wall_player.action = (
        "defense:deep_non_owner_yield"
        if ball_distance < DEEP_DEFENSE_NON_OWNER_RADIUS_M
        else "defense:deep_outward_wall"
    )


def _select_deep_secondary_target(
    context: Context,
    secondary_player: Player,
    goalkeeper_target: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """选择路程较短且不与守门员目标重叠的第二拼抢错位点。"""
    pose = secondary_player.pose
    ball = context.ball
    if pose is None or ball is None:
        return None

    candidates = [
        target for target in (
            _build_deep_defense_offset_target(context, -1.0),
            _build_deep_defense_offset_target(context, 1.0),
        )
        if target is not None
    ]
    if not candidates:
        return None

    valid_candidates = [
        target for target in candidates
        if dist(ball.x, ball.y, target[0], target[1])
        >= DEEP_DEFENSE_CHALLENGE_SPACING_M
        and (
            goalkeeper_target is None
            or dist(
                target[0],
                target[1],
                goalkeeper_target[0],
                goalkeeper_target[1],
            ) >= DEEP_DEFENSE_CHALLENGE_SPACING_M
        )
    ]
    if not valid_candidates:
        expanded_offset = max(
            DEEP_DEFENSE_SECONDARY_OFFSET_M,
            DEEP_DEFENSE_CHALLENGE_SPACING_M + 0.25,
        )
        expanded_candidates = [
            target for target in (
                _build_deep_defense_offset_target(
                    context,
                    -1.0,
                    expanded_offset,
                ),
                _build_deep_defense_offset_target(
                    context,
                    1.0,
                    expanded_offset,
                ),
            )
            if target is not None
        ]
        valid_candidates = [
            target for target in expanded_candidates
            if dist(ball.x, ball.y, target[0], target[1])
            >= DEEP_DEFENSE_CHALLENGE_SPACING_M
            and (
                goalkeeper_target is None
                or dist(
                    target[0],
                    target[1],
                    goalkeeper_target[0],
                    goalkeeper_target[1],
                ) >= DEEP_DEFENSE_CHALLENGE_SPACING_M
            )
        ]
    selectable_candidates = valid_candidates or candidates

    def candidate_score(target: tuple[float, float]) -> float:
        travel_cost = dist(pose.x, pose.y, target[0], target[1])
        spacing_from_ball = dist(ball.x, ball.y, target[0], target[1])
        spacing_penalty = max(
            0.0,
            DEEP_DEFENSE_CHALLENGE_SPACING_M - spacing_from_ball,
        ) * 10.0
        goalkeeper_penalty = 0.0
        if goalkeeper_target is not None:
            goalkeeper_spacing = dist(
                target[0],
                target[1],
                goalkeeper_target[0],
                goalkeeper_target[1],
            )
            goalkeeper_penalty = max(
                0.0,
                DEEP_DEFENSE_CHALLENGE_SPACING_M - goalkeeper_spacing,
            ) * 10.0
        touchline_bonus = max(0.0, abs(ball.y) - abs(target[1]))
        return (
            -travel_cost
            - spacing_penalty
            - goalkeeper_penalty
            + touchline_bonus
        )

    return max(selectable_candidates, key=candidate_score)


def _goalkeeper_has_deep_defense_priority(
    context: Context,
    goalkeeper: Player | None,
    store,
) -> bool:
    """守门员进入主动处理状态后拥有硬球权，场上球员不得再争同一球。"""
    goalkeeper_mode = getattr(store, "goalkeeper_mode", None)
    return (
        goalkeeper is not None
        and goalkeeper.pose is not None
        and context.ball is not None
        and goalkeeper_mode in (
            GoalkeeperMode.CHALLENGE,
            GoalkeeperMode.CLEAR,
        )
    )


def _deep_non_owner_target_is_safe(
    context: Context,
    target: tuple[float, float],
    *,
    goalkeeper_target: tuple[float, float] | None = None,
    owner_pose=None,
) -> bool:
    """确认非触球防守点不会被 clamp 后挤回球、持球者或守门员目标附近。"""
    ball = context.ball
    if (
        ball is not None
        and dist(ball.x, ball.y, target[0], target[1])
        < DEEP_DEFENSE_NON_OWNER_RADIUS_M
    ):
        return False

    if (
        owner_pose is not None
        and dist(owner_pose.x, owner_pose.y, target[0], target[1])
        < DEEP_DEFENSE_NON_OWNER_RADIUS_M
    ):
        return False

    if (
        goalkeeper_target is not None
        and dist(
            goalkeeper_target[0],
            goalkeeper_target[1],
            target[0],
            target[1],
        ) < DEEP_DEFENSE_NON_OWNER_RADIUS_M
    ):
        return False

    return True


def _select_deep_opponent_pressure_target(
    context: Context,
    pressure_player: Player,
    goalkeeper_target: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """选择贴防最近门前进攻者的位置，但仍尊重守门员硬球权的球周围禁入圈。"""
    ball = context.ball
    pressure_pose = pressure_player.pose
    if ball is None or pressure_pose is None:
        return None

    dangerous_opponents = [
        opponent.pose for opponent in context.opponents.values()
        if opponent.pose is not None
        and dist(
            opponent.pose.x,
            opponent.pose.y,
            ball.x,
            ball.y,
        ) <= DEEP_DEFENSE_OPPONENT_PRESS_BALL_RADIUS_M
    ]
    if not dangerous_opponents:
        return None

    own_goal_x, own_goal_y = own_goal(context)
    half_length = context.field.length / 2.0
    half_width = context.field.width / 2.0
    minimum_x = own_goal_x + NORMAL_DEFENSE_FIELD_MARGIN_M
    maximum_x = half_length - NORMAL_DEFENSE_FIELD_MARGIN_M
    minimum_y = -half_width + NORMAL_DEFENSE_FIELD_MARGIN_M
    maximum_y = half_width - NORMAL_DEFENSE_FIELD_MARGIN_M

    pressure_candidates: list[tuple[float, float]] = []
    for opponent_pose in dangerous_opponents:
        goalward_x = own_goal_x - opponent_pose.x
        goalward_y = own_goal_y - opponent_pose.y
        goalward_length = math.hypot(goalward_x, goalward_y)
        if goalward_length <= 1e-6:
            goalward_direction = (-1.0, 0.0)
        else:
            goalward_direction = (
                goalward_x / goalward_length,
                goalward_y / goalward_length,
            )
        lateral_direction = (
            -goalward_direction[1],
            goalward_direction[0],
        )
        for forward_distance in (
            DEEP_DEFENSE_OPPONENT_PRESS_DISTANCE_M,
            DEEP_DEFENSE_OPPONENT_PRESS_DISTANCE_M * 1.35,
        ):
            for lateral_distance in (
                0.0,
                DEEP_DEFENSE_OPPONENT_PRESS_DISTANCE_M * 0.55,
                -DEEP_DEFENSE_OPPONENT_PRESS_DISTANCE_M * 0.55,
            ):
                target_x = (
                    opponent_pose.x
                    + goalward_direction[0] * forward_distance
                    + lateral_direction[0] * lateral_distance
                )
                target_y = (
                    opponent_pose.y
                    + goalward_direction[1] * forward_distance
                    + lateral_direction[1] * lateral_distance
                )
                pressure_candidates.append((
                    clamp(target_x, minimum_x, maximum_x),
                    clamp(target_y, minimum_y, maximum_y),
                ))

    safe_candidates = [
        target for target in pressure_candidates
        if _deep_non_owner_target_is_safe(
            context,
            target,
            goalkeeper_target=goalkeeper_target,
        )
    ]
    if not safe_candidates:
        return None

    def candidate_score(target: tuple[float, float]) -> float:
        nearest_opponent_distance = min(
            dist(target[0], target[1], pose.x, pose.y)
            for pose in dangerous_opponents
        )
        travel_distance = dist(
            pressure_pose.x,
            pressure_pose.y,
            target[0],
            target[1],
        )
        ball_spacing = dist(ball.x, ball.y, target[0], target[1])
        desired_spacing_error = abs(
            ball_spacing - DEEP_DEFENSE_NON_OWNER_RADIUS_M,
        )
        return (
            -nearest_opponent_distance * 2.0
            - travel_distance * 0.35
            - desired_spacing_error * 0.5
        )

    return max(safe_candidates, key=candidate_score)


def _act_deep_opponent_pressure(
    context: Context,
    pressure_player: Player,
    store,
) -> bool:
    """守门员处理球时，让一名场上队员贴住最近的门前进攻者。"""
    pressure_target = _select_deep_opponent_pressure_target(
        context,
        pressure_player,
        getattr(store, "goalkeeper_target", None),
    )
    if pressure_target is None or pressure_player.pose is None:
        return False

    ball = context.ball
    face = (
        angle_to(
            pressure_player.pose.x,
            pressure_player.pose.y,
            ball.x,
            ball.y,
        )
        if ball is not None else None
    )
    pressure_player.walk_to(
        pressure_target,
        face=face,
        avoid_ball=True,
        avoid_robots=True,
    )
    pressure_player.action = "defense:deep_opponent_pressure"
    store.deep_defense_outward_target = pressure_target
    return True


def _act_deep_defense_support_pair(
    context: Context,
    primary_player: Player | None,
    secondary_player: Player | None,
    store,
) -> None:
    """守门员出击或解围时，一人贴防进攻者，另一人安全让出球权。"""
    available_pair = [
        player for player in (primary_player, secondary_player)
        if player is not None and player.pose is not None
    ]
    if not available_pair:
        return

    pressure_player = min(
        available_pair,
        key=lambda player: _player_dist_to_ball(context, player),
    )
    pressure_assigned = _act_deep_opponent_pressure(
        context,
        pressure_player,
        store,
    )
    yield_players = [
        player for player in available_pair
        if not pressure_assigned or player is not pressure_player
    ]
    if not yield_players:
        store.deep_defense_secondary_target = None
        return

    goalkeeper_yield_offset = max(
        DEEP_DEFENSE_SECONDARY_OFFSET_M,
        DEEP_DEFENSE_NON_OWNER_RADIUS_M + 0.15,
    )
    goalkeeper_target = getattr(store, "goalkeeper_target", None)
    expanded_offset = max(
        DEEP_DEFENSE_SECONDARY_OFFSET_M,
        DEEP_DEFENSE_NON_OWNER_RADIUS_M + 0.35,
    )
    fallback_offset = max(
        expanded_offset,
        DEEP_DEFENSE_NON_OWNER_RADIUS_M + 0.7,
    )

    def select_safe_yield_target(
        side: float,
    ) -> tuple[float, float] | None:
        for offset_distance in (
            goalkeeper_yield_offset,
            expanded_offset,
            fallback_offset,
        ):
            target = _build_deep_defense_offset_target(
                context,
                side,
                offset_distance,
            )
            if target is not None and _deep_non_owner_target_is_safe(
                context,
                target,
                goalkeeper_target=goalkeeper_target,
            ):
                return target
        return None

    negative_target = select_safe_yield_target(-1.0)
    positive_target = select_safe_yield_target(1.0)
    if len(yield_players) == 1:
        single_safe_target = negative_target or positive_target
        negative_target = single_safe_target
        positive_target = single_safe_target
    if negative_target is None or positive_target is None:
        for index, player in enumerate(yield_players):
            role_label = "deep_primary" if index == 0 else "deep_secondary"
            player.action = f"defense:{role_label}:goalkeeper_yield:stop"
            player.stop()
        store.deep_defense_secondary_target = None
        return

    first_player = yield_players[0]
    first_pose = first_player.pose
    if first_pose is None:
        return
    first_uses_negative = dist(
        first_pose.x,
        first_pose.y,
        negative_target[0],
        negative_target[1],
    ) <= dist(
        first_pose.x,
        first_pose.y,
        positive_target[0],
        positive_target[1],
    )
    assigned_targets = (
        [negative_target, positive_target]
        if first_uses_negative else [positive_target, negative_target]
    )
    for index, player in enumerate(yield_players):
        target = assigned_targets[min(index, len(assigned_targets) - 1)]
        ball = context.ball
        face = (
            angle_to(player.pose.x, player.pose.y, ball.x, ball.y)
            if player.pose is not None and ball is not None else None
        )
        player.walk_to(
            target,
            face=face,
            avoid_ball=True,
            avoid_robots=True,
        )
        role_label = "deep_primary" if index == 0 else "deep_secondary"
        player.action = f"defense:{role_label}:goalkeeper_yield"

    store.deep_defense_secondary_target = (
        assigned_targets[1]
        if len(yield_players) > 1 else assigned_targets[0]
    )
    if not pressure_assigned:
        store.deep_defense_outward_target = None


def _act_deep_defense(
    context: Context,
    goalkeeper: Player | None,
    field_players: list[Player],
    store,
) -> None:
    """执行门前深度防守：第一人处理球，第二人错位拼抢或接管。"""
    primary_player, secondary_player, secondary_took_over = (
        _select_deep_defense_challengers(context, field_players, store)
    )
    crowded = _update_deep_defense_crowding(context, store)
    goalkeeper_priority = _goalkeeper_has_deep_defense_priority(
        context,
        goalkeeper,
        store,
    )
    store.deep_defense_goalkeeper_priority = goalkeeper_priority
    if primary_player is None:
        store.deep_defense_ball_owner_id = (
            goalkeeper.id if goalkeeper is not None else None
        )
        store.deep_defense_secondary_target = None
        store.deep_defense_clearance_target = None
        store.deep_defense_clearance_level = "goalkeeper_only"
        store.deep_defense_clearance_power = None
        store.deep_defense_outward_target = None
        _draw_deep_defense_state(context, store)
        return
    if goalkeeper_priority:
        store.deep_defense_ball_owner_id = goalkeeper.id
        goalkeeper_mode = getattr(store, "goalkeeper_mode", None)
        goalkeeper_priority_reason = (
            "goalkeeper_clear_priority"
            if goalkeeper_mode == GoalkeeperMode.CLEAR
            else "goalkeeper_challenge_priority"
        )
        store.deep_defense_clearance_target = None
        store.deep_defense_clearance_level = goalkeeper_priority_reason
        store.deep_defense_clearance_power = None
        _act_deep_defense_support_pair(
            context,
            primary_player,
            secondary_player,
            store,
        )
        _draw_deep_defense_state(context, store)
        return

    store.deep_defense_ball_owner_id = primary_player.id

    emergency_clearance = _is_emergency_defensive_clearance(context, store)
    clearance_target = _get_controlled_clearance_target(
        context,
        emergency=emergency_clearance,
    )
    if emergency_clearance:
        clearance_level = "emergency_clear"
    elif crowded:
        clearance_level = "crowded_clear"
    else:
        clearance_level = "quick_clear"
    clearance_power = (
        DEFENSIVE_EMERGENCY_CLEAR_POWER
        if emergency_clearance
        else (
            DEEP_DEFENSE_CROWD_CLEAR_POWER
            if crowded else DEFENSIVE_QUICK_CLEAR_POWER
        )
    )
    store.deep_defense_clearance_target = clearance_target
    store.deep_defense_clearance_level = clearance_level
    store.deep_defense_clearance_power = clearance_power

    if primary_player is not None and clearance_target is not None:
        if secondary_took_over:
            role_label = "deep_owner_takeover"
        elif crowded:
            role_label = "deep_crowded_owner"
        else:
            role_label = "deep_primary"
        _act_quick_defensive_clear(
            primary_player,
            clearance_target,
            clearance_power,
            f"defense:{role_label}:{clearance_level}",
            avoid_crowding=crowded,
        )

    if secondary_player is not None:
        if crowded:
            _act_deep_outward_wall(
                context,
                secondary_player,
                primary_player,
                store,
            )
            _draw_deep_defense_state(context, store)
            return

        store.deep_defense_outward_target = None
        secondary_target = _select_deep_secondary_target(
            context,
            secondary_player,
            getattr(store, "goalkeeper_target", None),
        )
        store.deep_defense_secondary_target = secondary_target
        if secondary_target is None or secondary_player.pose is None:
            secondary_player.action = "defense:deep_secondary:stop"
            secondary_player.stop()
        else:
            ball = context.ball
            face = (
                angle_to(
                    secondary_player.pose.x,
                    secondary_player.pose.y,
                    ball.x,
                    ball.y,
                )
                if ball is not None else None
            )
            secondary_player.walk_to(
                secondary_target,
                face=face,
                avoid_ball=True,
                avoid_robots=True,
            )
            secondary_player.action = "defense:deep_secondary:close"
    else:
        store.deep_defense_secondary_target = None
        store.deep_defense_outward_target = None

    _draw_deep_defense_state(context, store)


def _act_normal_defense_pressure(
    context: Context,
    pressure_player: Player,
) -> None:
    """普通防守逼抢；己方半场使用低力度快速受控解围。"""
    ball = context.ball
    pose = pressure_player.pose
    if ball is None or pose is None:
        pressure_player.action = "defense:no_ball"
        pressure_player.stop()
        return

    if ball.x < 0.0:
        clearance_target = _get_controlled_clearance_target(
            context,
            emergency=False,
        )
        if clearance_target is None:
            pressure_player.action = "defense:no_ball"
            pressure_player.stop()
            return
        _act_quick_defensive_clear(
            pressure_player,
            clearance_target,
            DEFENSIVE_CONTROLLED_CLEAR_POWER,
            "defense:normal_pressure:controlled_clear",
        )
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
        pressure_player.action = "defense:normal_pressure:chase"
        return

    kick_plan = pressure_player.plan_kick()
    if kick_plan is None:
        pressure_player.action = "defense:no_ball"
        pressure_player.stop()
        return

    kick_direction, kick_power = kick_plan
    pressure_player.kick(kick_direction, kick_power)
    pressure_player.action = "defense:normal_pressure:clear"


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
        protect_player.action = "defense:normal_protect"


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
            "defense:normal_pressure:",
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
    post_clear_counterattack = (
        primary_attacker is not None
        and primary_attacker.id
        == getattr(store, "goalkeeper_post_clear_attacker_id", None)
        and not mode_estimate.ball_in_own_danger_area
    )
    if post_clear_counterattack:
        if open_play_mode != OpenPlayMode.ATTACKING:
            store.open_play_mode_entered_at = context.now
            store.open_play_last_switch_at = context.now
            store.open_play_last_switch_reason = (
                "goalkeeper_post_clear_counterattack"
            )
        open_play_mode = OpenPlayMode.ATTACKING
        store.open_play_mode = open_play_mode
        store.open_play_mode_reason = (
            "goalkeeper_post_clear_counterattack"
        )
    _draw_open_play_mode(context, store)

    deep_defense_active = _update_deep_defense_state(context, store)

    if (
        assignment.availability == OpenPlayAvailability.DEGRADED_ONE
        and role_goalkeeper is not None
        and primary_attacker is None
    ):
        # 唯一可用者在门前风险高时守门；球远离己方门且自身不在门前保护位时，
        # 释放为普通处理球入口，避免少人状态下整队只留守不追球。
        if _should_single_player_guard(context, role_goalkeeper):
            _act_goalkeeper_guard(
                context,
                role_goalkeeper,
                assigned_field_players,
                store,
                allow_active_response=True,
            )
            if deep_defense_active:
                _act_deep_defense(
                    context,
                    role_goalkeeper,
                    assigned_field_players,
                    store,
                )
        else:
            store.normal_attacker = role_goalkeeper.id
            _act_attack_ball_handler(
                role_goalkeeper,
                "single_degraded",
            )
        return

    if deep_defense_active:
        if role_goalkeeper is not None:
            _act_goalkeeper_guard(
                context,
                role_goalkeeper,
                assigned_field_players,
                store,
                allow_active_response=True,
            )
        _act_deep_defense(
            context,
            role_goalkeeper,
            assigned_field_players,
            store,
        )
        return

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
            force_primary_handler=post_clear_counterattack,
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


def _normalize_angle(angle: float) -> float:
    """把角度规约到 [-pi, pi]，避免 main.py 依赖 Player 内部工具。"""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _is_our_kickoff_ready(context: Context) -> bool:
    game = context.game
    return (
        game is not None
        and game.state == GameState.READY
        and game.kicking_team == context.team_id
    )


def _is_our_kickoff_set(context: Context) -> bool:
    game = context.game
    return (
        game is not None
        and game.state == GameState.SET
        and game.kicking_team == context.team_id
    )


def _is_our_kickoff_playing(context: Context) -> bool:
    game = context.game
    return (
        game is not None
        and game.state == GameState.PLAYING
        and game.kicking_team == context.team_id
        and game.secondary_time > 0
        and game.set_play == SetPlay.NONE
    )


def _enter_kickoff_state(
    store,
    state: KickoffTacticState,
    now: float,
    reason: str | None = None,
) -> None:
    previous_state = getattr(
        store,
        "kickoff_tactic_state",
        KickoffTacticState.IDLE,
    )
    if previous_state == state:
        return
    store.kickoff_tactic_state = state
    store.kickoff_state_entered_at = now
    if reason is not None:
        store.kickoff_abort_reason = reason


def _clear_kickoff_tactic(store, reason: str) -> None:
    """清理固定开球锁定状态；只由裁判窗口结束或明确非开球分支调用。"""
    store.kickoff_tactic_state = KickoffTacticState.IDLE
    store.kickoff_roles = None
    store.locked_roles = None
    store.tactic_roles = None
    store.active_tactic = None
    store.kickoff_state_entered_at = None
    store.kickoff_tactic_started_at = None
    store.kickoff_first_touch_confirmed = False
    store.kickoff_second_touch_confirmed = False
    store.kickoff_pass_start_ball = None
    store.kickoff_pass_start_seen_at = None
    store.kickoff_pass_attempts = 0
    store.kickoff_last_pass_attempt_at = None
    store.kickoff_abort_reason = reason
    store.kickoff_ready_passer_arrived = False
    store.kickoff_ready_shooter_arrived = False
    store.kickoff_safe_first_touch_player_id = None
    store.kickoff_safe_first_touch_done = False


def _kickoff_clamp_target(
    context: Context,
    target: tuple[float, float],
) -> tuple[float, float]:
    half_length = max(
        0.0,
        context.field.length / 2.0 - KICKOFF_FIELD_MARGIN_M,
    )
    half_width = max(
        0.0,
        context.field.width / 2.0 - KICKOFF_FIELD_MARGIN_M,
    )
    return (
        clamp(target[0], -half_length, half_length),
        clamp(target[1], -half_width, half_width),
    )


def _kickoff_layout_for_side(
    context: Context,
    side_sign: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """返回镜像后的 passer setup、shooter setup 和接应点。"""
    passer_setup = _kickoff_clamp_target(
        context,
        (
            KICKOFF_PASSER_SETUP_X_M,
            KICKOFF_PASSER_SETUP_Y_M * side_sign,
        ),
    )
    shooter_setup = _kickoff_clamp_target(
        context,
        (
            KICKOFF_SHOOTER_SETUP_X_M,
            KICKOFF_SHOOTER_SETUP_Y_M * side_sign,
        ),
    )
    receive_target = _kickoff_clamp_target(
        context,
        (
            KICKOFF_RECEIVE_TARGET_X_M,
            KICKOFF_RECEIVE_TARGET_Y_M * side_sign,
        ),
    )
    return passer_setup, shooter_setup, receive_target


def _select_kickoff_roles(
    context: Context,
    field_players: list[Player],
) -> KickoffRoles | None:
    """比较两名场上机器人、两种角色顺序、两种上下镜像的总代价。"""
    candidates = [player for player in field_players if player.pose is not None]
    if len(candidates) < 2:
        return None

    best_roles: KickoffRoles | None = None
    best_cost = math.inf
    for side_sign in (1.0, -1.0):
        passer_setup, shooter_setup, receive_target = _kickoff_layout_for_side(
            context,
            side_sign,
        )
        for passer in candidates:
            for shooter in candidates:
                if passer is shooter:
                    continue
                passer_pose = passer.pose
                shooter_pose = shooter.pose
                passer_cost = dist(
                    passer_pose.x,
                    passer_pose.y,
                    passer_setup[0],
                    passer_setup[1],
                )
                shooter_cost = dist(
                    shooter_pose.x,
                    shooter_pose.y,
                    shooter_setup[0],
                    shooter_setup[1],
                )
                ball = context.ball
                if ball is None:
                    ball_cost = 0.0
                else:
                    pass_direction = angle_to(
                        ball.x,
                        ball.y,
                        receive_target[0],
                        receive_target[1],
                    )
                    desired_passer_heading = _normalize_angle(
                        pass_direction + math.pi,
                    )
                    passer_ball_angle = angle_to(
                        ball.x,
                        ball.y,
                        passer_pose.x,
                        passer_pose.y,
                    )
                    ball_cost = 0.4 * abs(_normalize_angle(
                        desired_passer_heading - passer_ball_angle,
                    ))
                receive_cost = 0.25 * dist(
                    shooter_pose.x,
                    shooter_pose.y,
                    receive_target[0],
                    receive_target[1],
                )
                cost = passer_cost + shooter_cost + ball_cost + receive_cost
                if cost < best_cost:
                    best_cost = cost
                    best_roles = KickoffRoles(
                        passer_id=passer.id,
                        shooter_id=shooter.id,
                        side_sign=side_sign,
                        passer_setup=passer_setup,
                        shooter_setup=shooter_setup,
                        receive_target=receive_target,
                    )
    return best_roles


def _initialize_our_kickoff_tactic(
    context: Context,
    field_players: list[Player],
    store,
) -> bool:
    """初始化并锁定固定开球职责；已经锁定时不重新选择。"""
    existing_roles = getattr(store, "kickoff_roles", None)
    if existing_roles is not None:
        return True

    roles = _select_kickoff_roles(context, field_players)
    if roles is None:
        store.kickoff_abort_reason = "not_enough_field_players"
        return False

    store.kickoff_roles = roles
    store.locked_roles = {
        "kickoff_passer": roles.passer_id,
        "kickoff_shooter": roles.shooter_id,
    }
    store.tactic_roles = roles
    store.active_tactic = "our_midfield_kickoff_one_pass_one_shot"
    store.kickoff_tactic_started_at = context.now
    store.kickoff_state_entered_at = context.now
    store.kickoff_first_touch_confirmed = False
    store.kickoff_second_touch_confirmed = False
    store.kickoff_pass_attempts = 0
    store.kickoff_last_pass_attempt_at = None
    store.kickoff_pass_start_ball = None
    store.kickoff_pass_start_seen_at = None
    store.kickoff_abort_reason = None
    store.kickoff_ready_passer_arrived = False
    store.kickoff_ready_shooter_arrived = False
    store.kickoff_tactic_state = KickoffTacticState.SETUP
    return True


def _get_kickoff_role_players(
    field_players: list[Player],
    roles: KickoffRoles | None,
) -> tuple[Player | None, Player | None]:
    if roles is None:
        return None, None
    by_id = {player.id: player for player in field_players}
    return by_id.get(roles.passer_id), by_id.get(roles.shooter_id)


def _kickoff_player_at_target(
    player: Player,
    target: tuple[float, float],
    heading_target: tuple[float, float],
    position_tolerance: float,
    heading_tolerance: float,
) -> bool:
    pose = player.pose
    if pose is None:
        return False
    target_distance = dist(pose.x, pose.y, target[0], target[1])
    target_heading = angle_to(
        pose.x,
        pose.y,
        heading_target[0],
        heading_target[1],
    )
    heading_error = abs(_normalize_angle(target_heading - pose.theta))
    return (
        target_distance <= position_tolerance
        and heading_error <= heading_tolerance
    )


def _kickoff_ball_has_moved_toward_receive(
    context: Context,
    store,
    receive_target: tuple[float, float],
) -> bool:
    ball = context.ball
    start_ball = getattr(store, "kickoff_pass_start_ball", None)
    start_seen_at = getattr(store, "kickoff_pass_start_seen_at", None)
    if ball is None or start_ball is None or start_seen_at is None:
        return False
    ball_seen_at = ball.last_seen_at if ball.last_seen_at > 0.0 else context.now
    if ball_seen_at <= start_seen_at:
        return False

    moved_x = ball.x - start_ball[0]
    moved_y = ball.y - start_ball[1]
    moved_distance = math.hypot(moved_x, moved_y)
    if moved_distance < KICKOFF_BALL_MOVED_DISTANCE_M:
        return False

    target_x = receive_target[0] - start_ball[0]
    target_y = receive_target[1] - start_ball[1]
    target_distance = math.hypot(target_x, target_y)
    if target_distance <= 1e-6:
        return False
    direction_dot = (
        (moved_x / moved_distance) * (target_x / target_distance)
        + (moved_y / moved_distance) * (target_y / target_distance)
    )
    return direction_dot >= KICKOFF_PASS_DIRECTION_DOT_MIN


def _ball_in_kickoff_receive_zone(
    context: Context,
    shooter: Player,
    receive_target: tuple[float, float],
) -> bool:
    ball = context.ball
    pose = shooter.pose
    if ball is None or pose is None:
        return False
    ball_receive_distance = dist(
        ball.x,
        ball.y,
        receive_target[0],
        receive_target[1],
    )
    shooter_ball_distance = dist(pose.x, pose.y, ball.x, ball.y)
    goal_heading = angle_to(ball.x, ball.y, *opponent_goal(context))
    robot_to_ball = angle_to(pose.x, pose.y, ball.x, ball.y)
    ball_bearing = abs(_normalize_angle(robot_to_ball - pose.theta))
    goal_facing_error = abs(_normalize_angle(goal_heading - pose.theta))
    return (
        ball_receive_distance <= KICKOFF_RECEIVE_ZONE_RADIUS_M
        or shooter_ball_distance <= KICKOFF_SHOT_KICK_DISTANCE_M
    ) and (
        ball_bearing <= KICKOFF_SHOT_BALL_BEARING_RAD
        and goal_facing_error <= KICKOFF_SHOT_GOAL_FACING_RAD
    )


def _kickoff_ball_is_severely_off_course(
    context: Context,
    roles: KickoffRoles,
) -> bool:
    ball = context.ball
    if ball is None:
        return True
    start = (0.0, 0.0)
    segment_x = roles.receive_target[0] - start[0]
    segment_y = roles.receive_target[1] - start[1]
    segment_length_squared = segment_x * segment_x + segment_y * segment_y
    if segment_length_squared <= 1e-9:
        return False
    projection = (
        (ball.x - start[0]) * segment_x + (ball.y - start[1]) * segment_y
    ) / segment_length_squared
    projection = clamp(projection, 0.0, 1.0)
    nearest = (
        start[0] + segment_x * projection,
        start[1] + segment_y * projection,
    )
    corridor_distance = dist(ball.x, ball.y, nearest[0], nearest[1])
    far_beyond_receive = dist(
        ball.x,
        ball.y,
        roles.receive_target[0],
        roles.receive_target[1],
    ) > KICKOFF_RECEIVE_ZONE_RADIUS_M * 1.8
    return corridor_distance > KICKOFF_RECEIVE_CORRIDOR_WIDTH_M and far_beyond_receive


def _kickoff_opponent_controls_ball(context: Context) -> bool:
    ball = context.ball
    if ball is None:
        return False
    nearest_opponent_distance = _nearest_opponent_distance(context)
    return (
        nearest_opponent_distance is not None
        and nearest_opponent_distance <= 0.65
    )


def _command_kickoff_direct_touch(
    player: Player,
    target: tuple[float, float],
    power: float,
    action_prefix: str,
    *,
    kick_distance: float,
    alignment_tolerance: float,
    ball_bearing_tolerance: float,
    approach_behind: float,
) -> bool:
    """对固定目标直接触球；返回本帧是否下发踢球命令。"""
    context = player.context
    ball = context.ball if context is not None else None
    pose = player.pose
    if context is None or ball is None or pose is None:
        player.action = f"{action_prefix}:stop"
        player.stop()
        return False

    kick_direction = angle_to(ball.x, ball.y, target[0], target[1])
    desired_behind_angle = _normalize_angle(kick_direction + math.pi)
    player_angle_around_ball = angle_to(ball.x, ball.y, pose.x, pose.y)
    alignment_error = abs(_normalize_angle(
        desired_behind_angle - player_angle_around_ball,
    ))
    ball_bearing = abs(_normalize_angle(
        angle_to(pose.x, pose.y, ball.x, ball.y) - pose.theta,
    ))
    ball_distance = dist(pose.x, pose.y, ball.x, ball.y)

    player._draw_kick_target(target)
    if (
        ball_distance <= kick_distance
        and alignment_error <= alignment_tolerance
        and ball_bearing <= ball_bearing_tolerance
    ):
        player.kick(kick_direction, power)
        player.action = f"{action_prefix}:kick"
        return True

    approach_target = (
        ball.x - math.cos(kick_direction) * approach_behind,
        ball.y - math.sin(kick_direction) * approach_behind,
    )
    player.release_kick()
    player.walk_to(
        approach_target,
        face=kick_direction,
        avoid_ball=False,
        avoid_robots=False,
        arrive_dist=min(ARRIVE_DIST, approach_behind * 0.75),
    )
    player.action = f"{action_prefix}:approach"
    return False


def _act_kickoff_safe_first_touch(
    context: Context,
    field_players: list[Player],
    store,
) -> None:
    """双人战术不可用时的一脚安全短触球，避免第一脚直接射门。"""
    if not field_players:
        return
    if getattr(store, "kickoff_safe_first_touch_done", False):
        for player in field_players:
            player.action = "kickoff:safe_first_touch_wait"
            player.stop()
        return

    active_ids = {player.id for player in field_players}
    fallback_id = getattr(store, "kickoff_safe_first_touch_player_id", None)
    if fallback_id not in active_ids:
        fallback_id = min(
            field_players,
            key=lambda player: _player_dist_to_ball(context, player),
        ).id
        store.kickoff_safe_first_touch_player_id = fallback_id
    handler = next(player for player in field_players if player.id == fallback_id)
    side_sign = 1.0 if handler.pose is None or handler.pose.y <= 0.0 else -1.0
    safe_target = _kickoff_clamp_target(
        context,
        (
            KICKOFF_SINGLE_PLAYER_SAFE_TOUCH_X_M,
            KICKOFF_SINGLE_PLAYER_SAFE_TOUCH_Y_M * side_sign,
        ),
    )
    kicked = _command_kickoff_direct_touch(
        handler,
        safe_target,
        KICKOFF_PASS_POWER,
        "kickoff:safe_first_touch",
        kick_distance=KICKOFF_PASS_KICK_DISTANCE_M,
        alignment_tolerance=KICKOFF_PASS_ALIGNMENT_RAD,
        ball_bearing_tolerance=KICKOFF_PASS_BALL_BEARING_RAD,
        approach_behind=KICKOFF_PASS_APPROACH_BEHIND_M,
    )
    if kicked:
        store.kickoff_safe_first_touch_done = True
    for player in field_players:
        if player is handler:
            continue
        player.action = "kickoff:safe_wait"
        player.stop()


def _act_kickoff_passer_protect(
    context: Context,
    passer: Player,
    roles: KickoffRoles,
) -> None:
    protect_target = _kickoff_clamp_target(
        context,
        (
            KICKOFF_PASSER_PROTECT_X_M,
            KICKOFF_PASSER_PROTECT_Y_M * roles.side_sign,
        ),
    )
    passer.move_to_position(protect_target)
    passer.action = "kickoff:passer_protect"


def _draw_kickoff_tactic(context: Context, store) -> None:
    from .framework import debugdraw

    roles = getattr(store, "kickoff_roles", None)
    state = getattr(store, "kickoff_tactic_state", KickoffTacticState.IDLE)
    if roles is None and state == KickoffTacticState.IDLE:
        return
    debugdraw.text(
        0.0,
        context.field.width / 2.0 + 0.55,
        (
            f"kickoff={state.value} passer="
            f"{roles.passer_id if roles else '-'} shooter="
            f"{roles.shooter_id if roles else '-'} side="
            f"{roles.side_sign if roles else '-'} first="
            f"{getattr(store, 'kickoff_first_touch_confirmed', False)} "
            f"second={getattr(store, 'kickoff_second_touch_confirmed', False)} "
            f"abort={getattr(store, 'kickoff_abort_reason', None) or '-'}"
        ),
        rgb=(1.0, 0.35, 0.35),
        ns="kickoff_tactic",
    )
    if roles is None:
        return
    debugdraw.point(
        roles.passer_setup[0], roles.passer_setup[1],
        rgb=(1.0, 0.2, 0.2), scale=0.18, ns="kickoff_passer_setup",
    )
    debugdraw.point(
        roles.shooter_setup[0], roles.shooter_setup[1],
        rgb=(1.0, 0.6, 0.1), scale=0.18, ns="kickoff_shooter_setup",
    )
    debugdraw.point(
        roles.receive_target[0], roles.receive_target[1],
        rgb=(0.2, 1.0, 0.2), scale=0.22, ns="kickoff_receive",
    )
    ball = context.ball
    if ball is not None:
        debugdraw.line(
            [(ball.x, ball.y), roles.receive_target],
            rgb=(0.2, 1.0, 0.2), ns="kickoff_pass_line",
        )
        debugdraw.line(
            [roles.receive_target, opponent_goal(context)],
            rgb=(1.0, 1.0, 1.0), ns="kickoff_shot_line",
        )


def _abort_kickoff_tactic(
    context: Context,
    store,
    reason: str,
) -> None:
    first_touch_confirmed = bool(
        getattr(store, "kickoff_first_touch_confirmed", False),
    )
    state = (
        KickoffTacticState.ABORT_AFTER_FIRST_TOUCH
        if first_touch_confirmed else KickoffTacticState.ABORT_BEFORE_FIRST_TOUCH
    )
    _enter_kickoff_state(store, state, context.now, reason)


def _complete_kickoff_tactic(context: Context, store) -> None:
    _enter_kickoff_state(store, KickoffTacticState.COMPLETE, context.now)
    _clear_kickoff_tactic(store, "complete")


def _act_our_kickoff(
    context: Context,
    players: list[Player],
    goalkeeper: Player | None,
    store,
) -> None:
    """OUR_KICKOFF:执行我方中场固定一传一射，避免第一脚直接射门。"""
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
        _abort_kickoff_tactic(context, store, "no_field_player")
        return

    if len(field_players) < 2:
        _act_kickoff_safe_first_touch(context, field_players, store)
        _draw_kickoff_tactic(context, store)
        return

    if not _initialize_our_kickoff_tactic(context, field_players, store):
        _act_kickoff_safe_first_touch(context, field_players, store)
        _draw_kickoff_tactic(context, store)
        return

    roles = getattr(store, "kickoff_roles", None)
    passer, shooter = _get_kickoff_role_players(field_players, roles)
    if roles is None or passer is None or shooter is None:
        _abort_kickoff_tactic(context, store, "locked_player_unavailable")
        if not getattr(store, "kickoff_first_touch_confirmed", False):
            _act_kickoff_safe_first_touch(context, field_players, store)
        else:
            _act_normal(context, players, goalkeeper, store)
        _draw_kickoff_tactic(context, store)
        return

    state = getattr(
        store,
        "kickoff_tactic_state",
        KickoffTacticState.IDLE,
    )
    state_entered_at = getattr(store, "kickoff_state_entered_at", None)
    tactic_started_at = getattr(store, "kickoff_tactic_started_at", None)
    if tactic_started_at is not None and (
        context.now - tactic_started_at > KICKOFF_TOTAL_TIMEOUT_SEC
    ):
        _abort_kickoff_tactic(context, store, "total_timeout")
        state = getattr(store, "kickoff_tactic_state")

    if state in (
        KickoffTacticState.IDLE,
        KickoffTacticState.SETUP,
        KickoffTacticState.WAIT_FOR_PLAYING,
    ):
        _enter_kickoff_state(store, KickoffTacticState.ALIGN_PASSER, context.now)
        state = KickoffTacticState.ALIGN_PASSER

    if state == KickoffTacticState.ALIGN_PASSER:
        ball = context.ball
        if ball is None:
            _abort_kickoff_tactic(context, store, "ball_unknown")
        else:
            pass_direction = angle_to(
                ball.x,
                ball.y,
                roles.receive_target[0],
                roles.receive_target[1],
            )
            passer.action = "kickoff:passer_align"
            if passer.pose is not None:
                passer.walk_to(
                    (
                        ball.x - math.cos(pass_direction)
                        * KICKOFF_PASS_APPROACH_BEHIND_M,
                        ball.y - math.sin(pass_direction)
                        * KICKOFF_PASS_APPROACH_BEHIND_M,
                    ),
                    face=pass_direction,
                    avoid_ball=False,
                    avoid_robots=False,
                    arrive_dist=ARRIVE_DIST,
                )
            shooter.action = "kickoff:shooter_receive"
            shooter.walk_to(
                roles.receive_target,
                face=angle_to(
                    shooter.pose.x,
                    shooter.pose.y,
                    *opponent_goal(context),
                ) if shooter.pose is not None else 0.0,
                avoid_ball=False,
                avoid_robots=True,
                arrive_dist=KICKOFF_READY_ARRIVE_M,
            )
            timed_out = (
                state_entered_at is not None
                and context.now - state_entered_at > KICKOFF_ALIGN_TIMEOUT_SEC
            )
            if timed_out or _kickoff_player_at_target(
                passer,
                (passer.pose.x, passer.pose.y) if passer.pose is not None else roles.passer_setup,
                roles.receive_target,
                10.0,
                KICKOFF_PASSER_HEADING_TOLERANCE_RAD,
            ):
                _enter_kickoff_state(store, KickoffTacticState.PASS, context.now)
        _draw_kickoff_tactic(context, store)
        return

    if state == KickoffTacticState.PASS:
        ball = context.ball
        if ball is None:
            _abort_kickoff_tactic(context, store, "ball_unknown")
            _draw_kickoff_tactic(context, store)
            return
        if getattr(store, "kickoff_pass_attempts", 0) >= KICKOFF_PASS_MAX_RETRIES:
            _abort_kickoff_tactic(context, store, "pass_retry_exhausted")
            _draw_kickoff_tactic(context, store)
            return
        kicked = _command_kickoff_direct_touch(
            passer,
            roles.receive_target,
            KICKOFF_PASS_POWER,
            "kickoff:passer_kick",
            kick_distance=KICKOFF_PASS_KICK_DISTANCE_M,
            alignment_tolerance=KICKOFF_PASS_ALIGNMENT_RAD,
            ball_bearing_tolerance=KICKOFF_PASS_BALL_BEARING_RAD,
            approach_behind=KICKOFF_PASS_APPROACH_BEHIND_M,
        )
        if kicked:
            store.kickoff_pass_start_ball = (ball.x, ball.y)
            store.kickoff_pass_start_seen_at = (
                ball.last_seen_at if ball.last_seen_at > 0.0 else context.now
            )
            store.kickoff_pass_attempts += 1
            store.kickoff_last_pass_attempt_at = context.now
        shooter.walk_to(
            roles.receive_target,
            face=angle_to(
                shooter.pose.x,
                shooter.pose.y,
                *opponent_goal(context),
            ) if shooter.pose is not None else 0.0,
            avoid_ball=False,
            avoid_robots=True,
            arrive_dist=KICKOFF_READY_ARRIVE_M,
        )
        shooter.action = "kickoff:shooter_receive"
        if kicked:
            _enter_kickoff_state(
                store,
                KickoffTacticState.VERIFY_FIRST_TOUCH,
                context.now,
            )
        _draw_kickoff_tactic(context, store)
        return

    if state == KickoffTacticState.VERIFY_FIRST_TOUCH:
        if _kickoff_ball_has_moved_toward_receive(
            context,
            store,
            roles.receive_target,
        ):
            store.kickoff_first_touch_confirmed = True
            _enter_kickoff_state(
                store,
                KickoffTacticState.RECEIVE_AND_SHOOT,
                context.now,
            )
        else:
            if state_entered_at is not None and (
                context.now - state_entered_at > KICKOFF_FIRST_TOUCH_TIMEOUT_SEC
            ):
                if getattr(store, "kickoff_pass_attempts", 0) < KICKOFF_PASS_MAX_RETRIES:
                    _enter_kickoff_state(store, KickoffTacticState.PASS, context.now)
                else:
                    _abort_kickoff_tactic(context, store, "first_touch_timeout")
            passer.action = "kickoff:verify_first_touch"
            passer.stop()
            shooter.walk_to(
                roles.receive_target,
                face=angle_to(
                    shooter.pose.x,
                    shooter.pose.y,
                    *opponent_goal(context),
                ) if shooter.pose is not None else 0.0,
                avoid_ball=False,
                avoid_robots=True,
                arrive_dist=KICKOFF_READY_ARRIVE_M,
            )
            shooter.action = "kickoff:shooter_receive"
        _draw_kickoff_tactic(context, store)
        return

    if state == KickoffTacticState.RECEIVE_AND_SHOOT:
        if _kickoff_opponent_controls_ball(context):
            _enter_kickoff_state(
                store,
                KickoffTacticState.ABORT_TO_DEFENSE,
                context.now,
                "opponent_controls_ball",
            )
        elif _kickoff_ball_is_severely_off_course(context, roles):
            _abort_kickoff_tactic(context, store, "pass_off_course")
        elif state_entered_at is not None and (
            context.now - state_entered_at > KICKOFF_RECEIVE_TIMEOUT_SEC
        ):
            _abort_kickoff_tactic(context, store, "receive_timeout")
        else:
            _act_kickoff_passer_protect(context, passer, roles)
            shoot_now = _ball_in_kickoff_receive_zone(
                context,
                shooter,
                roles.receive_target,
            )
            if shoot_now:
                shot_target = opponent_goal(context)
                kicked = _command_kickoff_direct_touch(
                    shooter,
                    shot_target,
                    KICKOFF_SHOT_POWER,
                    "kickoff:shooter_shoot",
                    kick_distance=KICKOFF_SHOT_KICK_DISTANCE_M,
                    alignment_tolerance=KICKOFF_SHOT_ALIGNMENT_RAD,
                    ball_bearing_tolerance=KICKOFF_SHOT_BALL_BEARING_RAD,
                    approach_behind=KICKOFF_PASS_APPROACH_BEHIND_M,
                )
                if kicked:
                    store.kickoff_second_touch_confirmed = True
                    _enter_kickoff_state(
                        store,
                        KickoffTacticState.VERIFY_SECOND_TOUCH,
                        context.now,
                    )
            else:
                shooter.walk_to(
                    roles.receive_target,
                    face=angle_to(
                        shooter.pose.x,
                        shooter.pose.y,
                        *opponent_goal(context),
                    ) if shooter.pose is not None else 0.0,
                    avoid_ball=False,
                    avoid_robots=True,
                    arrive_dist=KICKOFF_READY_ARRIVE_M,
                )
                shooter.action = "kickoff:shooter_receive"
        _draw_kickoff_tactic(context, store)
        return

    if state == KickoffTacticState.VERIFY_SECOND_TOUCH:
        _complete_kickoff_tactic(context, store)
        _act_normal(context, players, goalkeeper, store)
        return

    if state == KickoffTacticState.ABORT_BEFORE_FIRST_TOUCH:
        _act_kickoff_safe_first_touch(context, field_players, store)
        _draw_kickoff_tactic(context, store)
        return

    if state == KickoffTacticState.ABORT_TO_DEFENSE:
        _act_normal(context, players, goalkeeper, store)
        _draw_kickoff_tactic(context, store)
        return

    if state == KickoffTacticState.ABORT_AFTER_FIRST_TOUCH:
        _act_normal(context, players, goalkeeper, store)
        _draw_kickoff_tactic(context, store)
        return

    for player in field_players:
        player.action = "kickoff:abort_stop"
        player.stop()
    _draw_kickoff_tactic(context, store)


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
    """对方中场开球:PLAYING 后先完全静止，直到球离开中点。"""
    if not players:
        return

    for player in players:
        player.action = (
            "opp_kickoff:wait_guard"
            if player is goalkeeper else "opp_kickoff:wait_touch"
        )
        player.stop()


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
        if _initialize_our_kickoff_tactic(context, field_players, store):
            roles = getattr(store, "kickoff_roles", None)
            passer, shooter = _get_kickoff_role_players(field_players, roles)
            if roles is not None and passer is not None and shooter is not None:
                pass_face = angle_to(
                    roles.passer_setup[0],
                    roles.passer_setup[1],
                    roles.receive_target[0],
                    roles.receive_target[1],
                )
                shot_face = angle_to(
                    roles.shooter_setup[0],
                    roles.shooter_setup[1],
                    *opponent_goal(context),
                )
                store.kickoff_ready_passer_arrived = passer.walk_to(
                    roles.passer_setup,
                    face=pass_face,
                    avoid_ball=True,
                    avoid_robots=True,
                    arrive_dist=KICKOFF_READY_ARRIVE_M,
                )
                passer.action = "kickoff:passer_setup"
                store.kickoff_ready_shooter_arrived = shooter.walk_to(
                    roles.shooter_setup,
                    face=shot_face,
                    avoid_ball=True,
                    avoid_robots=True,
                    arrive_dist=KICKOFF_READY_ARRIVE_M,
                )
                shooter.action = "kickoff:shooter_setup"
                for player in field_players:
                    if player in (passer, shooter):
                        continue
                    player.action = "kickoff:ready_hold"
                    player.stop()
                _draw_kickoff_tactic(context, store)
                return

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

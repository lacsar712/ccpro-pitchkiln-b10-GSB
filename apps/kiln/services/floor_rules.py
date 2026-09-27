"""灶台相位切换业务规则。"""
from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError

DRAWING_SOFT_POINT_MAX = Decimal("95")


class DrawingEligibility(NamedTuple):
    """出胶资格判定结果：eligible 是否达标，reason 给人看的理由。"""

    eligible: bool
    reason: str


# drawing_eligibility 的 open_run 缺省哨兵：区分「没传」与「确无值守」。
_UNSET = object()


def drawing_eligibility(hearth, open_run=_UNSET) -> DrawingEligibility:
    """
    出胶资格的唯一判定来源：瓦片/抽屉的资格提示与「改相位」入口
    都读这一套，不得另写。

    规则：进行中值守至少一条 softPointC ≤ 95℃ 的探针 → 具备资格；
    读数缺失或全部高于 95℃ → 不具备。

    注意：资格只是提示，不等于相位。达标后相位仍停在原位，
    只能经 change_hearth_phase 显式切换，禁止静默跳到出胶。
    """
    if open_run is _UNSET:
        open_run = hearth.open_run()
    if open_run is None:
        return DrawingEligibility(False, "该灶没有进行中的值守纪录。")

    # 用 .all() 而非 filter()：看板预取过 probes 时直接命中缓存，避免 N+1。
    ok = any(p.softPointC <= DRAWING_SOFT_POINT_MAX for p in open_run.probes.all())
    if not ok:
        return DrawingEligibility(
            False,
            f"进行中值守尚无软化点探针 ≤ {DRAWING_SOFT_POINT_MAX}℃。",
        )
    return DrawingEligibility(
        True,
        f"已有探针 ≤ {DRAWING_SOFT_POINT_MAX}℃，具备出胶资格（相位仍需手动切换）。",
    )


def assert_can_enter_drawing(hearth) -> None:
    """进入「出胶」相位前校验：与界面资格提示读同一套判定。"""
    result = drawing_eligibility(hearth)
    if not result.eligible:
        raise ValidationError({"phase": f"无法进入出胶：{result.reason}"})


def change_hearth_phase(hearth, new_phase: str):
    """统一入口：改相位时校验出胶规则并保存。

    全站唯一改相位的地方；探针写入等其它路径不得在此之外动 phase。
    """
    from apps.kiln.models import FireHearth

    if new_phase == FireHearth.PHASE_DRAWING:
        assert_can_enter_drawing(hearth)

    hearth.phase = new_phase
    hearth.save(update_fields=["phase"])
    return hearth

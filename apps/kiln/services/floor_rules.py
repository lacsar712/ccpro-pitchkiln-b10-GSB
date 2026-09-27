"""灶台相位切换业务规则。"""
from decimal import Decimal

from django.core.exceptions import ValidationError

DRAWING_SOFT_POINT_MAX = Decimal("95")


def _run_has_drawing_reading(open_run) -> bool:
    """值守内是否已存在 ≤95℃ 的合法软化点读数。"""
    if open_run is None:
        return False
    return any(
        p.softPointC <= DRAWING_SOFT_POINT_MAX for p in open_run.probes.all()
    )


def drawing_eligibility(hearth) -> bool:
    """
    出胶资格判定 —— 全站唯一规则来源。

    瓦片/抽屉上的资格提示与「改相位」入口都读这一套：
    有进行中值守、且其中至少一条探针 softPointC ≤ 95℃ → True（资格·是）；
    读数缺失、或全部读数高于 95℃ → False（资格·否）。

    注意：资格只是只读提示，本函数绝不改动灶台相位。
    """
    return _run_has_drawing_reading(hearth.open_run())


def assert_can_enter_drawing(hearth) -> None:
    """
    进入「出胶」相位前：当前未收灶的 CookRun 须至少有一条
    softPointC <= 95 的 SoftPointProbe。
    """
    open_run = hearth.open_run()
    if open_run is None:
        raise ValidationError(
            {"phase": "无法进入出胶：该灶没有进行中的值守纪录。"}
        )

    if not _run_has_drawing_reading(open_run):
        raise ValidationError(
            {
                "phase": (
                    "无法进入出胶：进行中值守尚无软化点探针 "
                    f"≤ {DRAWING_SOFT_POINT_MAX}℃。"
                )
            }
        )


def change_hearth_phase(hearth, new_phase: str):
    """统一入口：改相位时校验出胶规则并保存。"""
    from apps.kiln.models import FireHearth

    if new_phase == FireHearth.PHASE_DRAWING:
        assert_can_enter_drawing(hearth)

    hearth.phase = new_phase
    hearth.save(update_fields=["phase"])
    return hearth

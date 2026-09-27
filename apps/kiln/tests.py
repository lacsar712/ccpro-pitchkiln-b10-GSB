"""出胶资格 / 相位规则测试。

核心约束：
- 读数缺失或 >95℃ → 资格否；合法读数写入 → 资格是，但相位不动。
- 资格提示与改相位入口读同一套判定（drawing_eligibility）。
- 图例出胶计数只认相位已是 drawing 的灶。
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe
from .seed import ensure_seed_data
from .services.floor_rules import (
    DRAWING_SOFT_POINT_MAX,
    change_hearth_phase,
    drawing_eligibility,
)


def _lot(code="脂-测-0001"):
    return ResinLot.objects.create(
        lotCode=code,
        originPlace="松脂坳东沟",
        arrivalKg=Decimal("100.00"),
        receivedAt=timezone.now(),
    )


def _hearth(tag="测-甲", phase=FireHearth.PHASE_HOLDING):
    return FireHearth.objects.create(
        lane=1, tag=tag, resinGrade="特级脂", phase=phase
    )


def _run(hearth, lot):
    return CookRun.objects.create(
        hearth=hearth,
        resinLot=lot,
        openedAt=timezone.now(),
        targetSoftPointC=Decimal("88.00"),
    )


def _probe(run, value):
    return SoftPointProbe.objects.create(
        run=run,
        sampledAt=timezone.now(),
        softPointC=Decimal(value),
        samplerName="测试员",
    )


class DrawingEligibilityTests(TestCase):
    def setUp(self):
        self.lot = _lot()
        self.hearth = _hearth()

    def test_no_open_run_not_eligible(self):
        result = drawing_eligibility(self.hearth)
        self.assertFalse(result.eligible)
        self.assertIn("值守", result.reason)

    def test_open_run_without_probe_not_eligible(self):
        _run(self.hearth, self.lot)
        self.assertFalse(drawing_eligibility(self.hearth).eligible)

    def test_probe_above_max_not_eligible(self):
        run = _run(self.hearth, self.lot)
        _probe(run, "95.01")
        _probe(run, "102.40")
        self.assertFalse(drawing_eligibility(self.hearth).eligible)

    def test_probe_at_max_boundary_eligible(self):
        run = _run(self.hearth, self.lot)
        _probe(run, str(DRAWING_SOFT_POINT_MAX))  # 恰为 95℃ 也算合法读数
        self.assertTrue(drawing_eligibility(self.hearth).eligible)

    def test_probe_below_max_eligible(self):
        run = _run(self.hearth, self.lot)
        _probe(run, "108.00")
        _probe(run, "94.60")
        self.assertTrue(drawing_eligibility(self.hearth).eligible)

    def test_phase_entry_reads_same_judgement(self):
        """改相位入口与提示共用判定：资格否→拒绝；资格是→放行。"""
        run = _run(self.hearth, self.lot)
        _probe(run, "96.20")
        self.assertFalse(drawing_eligibility(self.hearth).eligible)
        with self.assertRaises(ValidationError):
            change_hearth_phase(self.hearth, FireHearth.PHASE_DRAWING)
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_HOLDING)

        _probe(run, "93.50")
        self.assertTrue(drawing_eligibility(self.hearth).eligible)
        change_hearth_phase(self.hearth, FireHearth.PHASE_DRAWING)
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_DRAWING)


class ProbeWriteFlowTests(TestCase):
    """探针写入后：提示立即刷新、相位停在原位。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user("w", password="pw")
        self.client.force_login(self.user)
        self.lot = _lot()
        self.hearth = _hearth(phase=FireHearth.PHASE_HOLDING)
        self.run = _run(self.hearth, self.lot)

    def _post_probe(self, value, htmx=True):
        extra = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(
            reverse("add_probe", args=[self.hearth.pk]),
            {
                "sampledAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "softPointC": value,
                "samplerName": "测试员",
            },
            **extra,
        )

    def test_valid_probe_refreshes_drawer_hint_but_keeps_phase(self):
        resp = self._post_probe("94.60")
        self.assertEqual(resp.status_code, 200)
        # 抽屉立即重渲染出「具备」提示
        self.assertContains(resp, "出胶资格：具备")
        # 并通知瓦片刷新
        self.assertEqual(resp["HX-Trigger"], "floor-refresh")
        # 相位停在原位，禁止静默跳到出胶
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_HOLDING)

    def test_hot_probe_hint_stays_not_eligible(self):
        resp = self._post_probe("96.50")
        self.assertContains(resp, "出胶资格：不具备")
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_HOLDING)

    def test_grid_tile_shows_eligibility_hint(self):
        resp = self.client.get(reverse("floor_grid"))
        self.assertContains(resp, "出胶资格 · 未达标")
        self._post_probe("94.60")
        resp = self.client.get(reverse("floor_grid"))
        self.assertContains(resp, "出胶资格 · 具备")

    def test_legend_counts_phase_not_eligibility(self):
        # 资格达标但相位仍是保温：图例出胶数必须为 0
        self._post_probe("94.60")
        resp = self.client.get(reverse("home"))
        legend = dict((key, count) for key, _label, count in resp.context["phase_legend"])
        self.assertEqual(legend[FireHearth.PHASE_DRAWING], 0)
        self.assertEqual(legend[FireHearth.PHASE_HOLDING], 1)
        # 显式改相位后才计入出胶
        change_hearth_phase(self.hearth, FireHearth.PHASE_DRAWING)
        resp = self.client.get(reverse("home"))
        legend = dict((key, count) for key, _label, count in resp.context["phase_legend"])
        self.assertEqual(legend[FireHearth.PHASE_DRAWING], 1)
        self.assertEqual(legend[FireHearth.PHASE_HOLDING], 0)

    def test_change_phase_blocked_until_eligible(self):
        url = reverse("change_phase", args=[self.hearth.pk])
        self.client.post(url, {"phase": FireHearth.PHASE_DRAWING})
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_HOLDING)

        self._post_probe("94.60")
        self.client.post(url, {"phase": FireHearth.PHASE_DRAWING})
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_DRAWING)


class SeedDataTests(TestCase):
    def test_seed_has_one_holding_hearth_eligible_but_still_holding(self):
        ensure_seed_data()
        holding = FireHearth.objects.filter(phase=FireHearth.PHASE_HOLDING)
        self.assertEqual(holding.count(), 1)
        hearth = holding.get()
        # 保温灶已具备出胶资格，但相位停在保温：资格 ≠ 相位
        self.assertTrue(drawing_eligibility(hearth).eligible)
        self.assertNotEqual(hearth.phase, FireHearth.PHASE_DRAWING)

"""出胶资格 / 相位分离的验收测试。"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe
from .services.floor_rules import (
    DRAWING_SOFT_POINT_MAX,
    assert_can_enter_drawing,
    change_hearth_phase,
    drawing_eligibility,
)


class EligibilityTestCase(TestCase):
    """同一套判定：读数缺失或 >95 → 否；合法读数 → 是（相位不动）。"""

    def setUp(self):
        self.lot = ResinLot.objects.create(
            lotCode="脂-松脂坳-T01",
            originPlace="松脂坳东沟",
            arrivalKg=Decimal("100.00"),
            receivedAt=timezone.now(),
        )
        self.hearth = FireHearth.objects.create(
            lane=1,
            tag="坳火-测",
            resinGrade="特级脂",
            phase=FireHearth.PHASE_HOLDING,
        )
        self.run = CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=timezone.now(),
            targetSoftPointC=Decimal("88.00"),
        )

    def _probe(self, value):
        return SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal(str(value)),
            samplerName="测试员",
        )

    def test_missing_reading_is_ineligible(self):
        self.assertFalse(drawing_eligibility(self.hearth))
        with self.assertRaises(ValidationError):
            assert_can_enter_drawing(self.hearth)

    def test_reading_above_95_is_ineligible(self):
        self._probe("96.20")
        self.assertFalse(drawing_eligibility(self.hearth))
        with self.assertRaises(ValidationError):
            assert_can_enter_drawing(self.hearth)

    def test_boundary_95_is_eligible(self):
        self._probe(DRAWING_SOFT_POINT_MAX)  # 恰为 95 → 合法
        self.assertTrue(drawing_eligibility(self.hearth))
        assert_can_enter_drawing(self.hearth)  # 不抛异常

    def test_valid_reading_flips_eligibility(self):
        self._probe("102.40")
        self.assertFalse(drawing_eligibility(self.hearth))
        self._probe("94.80")
        self.assertTrue(drawing_eligibility(self.hearth))

    def test_no_open_run_is_ineligible(self):
        self.run.closedAt = timezone.now()
        self.run.save(update_fields=["closedAt"])
        self.assertFalse(drawing_eligibility(self.hearth))
        with self.assertRaises(ValidationError):
            assert_can_enter_drawing(self.hearth)

    def test_eligibility_never_changes_phase(self):
        """资格判定本身不得改动相位。"""
        self._probe("90.00")
        self.assertTrue(drawing_eligibility(self.hearth))
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_HOLDING)


class PhaseChangeEntryTestCase(TestCase):
    """改相位入口与提示读同一套判定。"""

    def setUp(self):
        self.lot = ResinLot.objects.create(
            lotCode="脂-桐油坑-T02",
            originPlace="桐油坑北坡",
            arrivalKg=Decimal("100.00"),
            receivedAt=timezone.now(),
        )
        self.hearth = FireHearth.objects.create(
            lane=2,
            tag="坑火-测",
            resinGrade="一级脂",
            phase=FireHearth.PHASE_HOLDING,
        )
        self.run = CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=timezone.now(),
            targetSoftPointC=Decimal("90.00"),
        )

    def test_drawing_rejected_without_eligibility(self):
        self._probe = SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal("96.00"),
            samplerName="测试员",
        )
        with self.assertRaises(ValidationError):
            change_hearth_phase(self.hearth, FireHearth.PHASE_DRAWING)
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_HOLDING)

    def test_drawing_allowed_with_eligibility(self):
        SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal("93.50"),
            samplerName="测试员",
        )
        change_hearth_phase(self.hearth, FireHearth.PHASE_DRAWING)
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_DRAWING)

    def test_non_drawing_phase_needs_no_probe(self):
        change_hearth_phase(self.hearth, FireHearth.PHASE_RAMPING)
        self.hearth.refresh_from_db()
        self.assertEqual(self.hearth.phase, FireHearth.PHASE_RAMPING)


class BoardViewTestCase(TestCase):
    """探针写入后瓦片/抽屉立即刷新资格提示；图例只数相位。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "viewer", "viewer@pitchkiln.local", "pw"
        )
        self.client.force_login(self.user)
        self.lot = ResinLot.objects.create(
            lotCode="脂-松脂坳-T03",
            originPlace="松脂坳西岔",
            arrivalKg=Decimal("100.00"),
            receivedAt=timezone.now(),
        )
        # 保温灶：读数 >95 → 资格否
        self.holding = FireHearth.objects.create(
            lane=1,
            tag="坳火-保",
            resinGrade="特级脂",
            phase=FireHearth.PHASE_HOLDING,
        )
        self.run = CookRun.objects.create(
            hearth=self.holding,
            resinLot=self.lot,
            openedAt=timezone.now(),
            targetSoftPointC=Decimal("88.00"),
        )
        SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal("96.20"),
            samplerName="值守周磊",
        )
        # 出胶灶：图例出胶计数 = 1
        self.drawing = FireHearth.objects.create(
            lane=2,
            tag="坑火-出",
            resinGrade="特级脂",
            phase=FireHearth.PHASE_DRAWING,
        )

    def _legend_count(self, phase_key):
        resp = self.client.get(reverse("home"))
        legend = dict((k, c) for k, _label, c in resp.context["phase_legend"])
        return legend[phase_key]

    def test_tile_and_drawer_show_ineligible_initially(self):
        resp = self.client.get(reverse("floor_grid"))
        self.assertContains(resp, "出胶资格 · 否")
        resp = self.client.get(
            reverse("hearth_drawer", args=[self.holding.pk]),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(resp, "出胶资格 · 否")

    def test_probe_write_refreshes_hints_without_phase_jump(self):
        """写入合法读数：瓦片/抽屉立刻资格·是，相位仍保温，图例出胶数不变。"""
        url = reverse("add_probe", args=[self.holding.pk])
        payload = {
            "sampledAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "softPointC": "94.50",
            "samplerName": "值守周磊",
        }
        resp = self.client.post(url, payload, HTTP_HX_REQUEST="true")
        # 抽屉响应立即带新资格提示，并触发网格刷新
        self.assertContains(resp, "出胶资格 · 是")
        self.assertEqual(resp["HX-Trigger"], "floor-refresh")
        # 相位未静默跳到出胶
        self.holding.refresh_from_db()
        self.assertEqual(self.holding.phase, FireHearth.PHASE_HOLDING)
        # 瓦片（网格局部刷新）也立刻资格·是
        grid = self.client.get(reverse("floor_grid"))
        self.assertContains(grid, "出胶资格 · 是")
        # 图例出胶计数仍只认相位 = 出胶的灶
        self.assertEqual(self._legend_count(FireHearth.PHASE_DRAWING), 1)
        self.assertEqual(self._legend_count(FireHearth.PHASE_HOLDING), 1)

    def test_legend_counts_phase_not_eligibility(self):
        """资格达标但未改相位，图例出胶计数不变。"""
        SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal("90.00"),
            samplerName="值守周磊",
        )
        self.assertTrue(drawing_eligibility(self.holding))
        self.assertEqual(self._legend_count(FireHearth.PHASE_DRAWING), 1)

    def test_phase_change_view_enforces_same_rule(self):
        url = reverse("change_phase", args=[self.holding.pk])
        # 资格不足：被拒，相位不动
        resp = self.client.post(url, {"phase": FireHearth.PHASE_DRAWING})
        self.assertRedirects(resp, f"/?hearth={self.holding.pk}", fetch_redirect_response=False)
        self.holding.refresh_from_db()
        self.assertEqual(self.holding.phase, FireHearth.PHASE_HOLDING)
        # 写入合法读数后：同一入口放行
        SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal("95.00"),
            samplerName="值守周磊",
        )
        self.client.post(url, {"phase": FireHearth.PHASE_DRAWING})
        self.holding.refresh_from_db()
        self.assertEqual(self.holding.phase, FireHearth.PHASE_DRAWING)

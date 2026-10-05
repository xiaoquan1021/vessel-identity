"""等价性验证的行为测试。

要点：本模块**不判定身份**，只回答"下游产物还能不能用"。
因此测试必须覆盖三值输出（尤其 UNCERTAIN 不能被掩盖），
以及"阈值必须有来源"这一纪律。
"""

from __future__ import annotations

import math

import pytest

from vessel_identity import (
    BranchGeometry,
    EquivalenceThresholds,
    Verdict,
    compare_branches,
)


def _straight_line(n: int = 21, radius: float = 2.0, length: float = 100.0,
                   scale: float = 1.0, noise: float = 0.0) -> BranchGeometry:
    """构造一条直线段血管，可选缩放与扰动。"""
    pts = [(i * length / (n - 1), 0.0, noise * math.sin(i)) for i in range(n)]
    radii = [radius * scale] * n
    return BranchGeometry(pts, radii)


THRESHOLDS = EquivalenceThresholds(
    max_length_rel_diff=0.02,
    max_radius_profile_distance=0.05,
    max_centerline_distance_rel=0.02,
    ambiguity_margin=0.10,
    calibration_status="PENDING_CALIBRATION",
)


def test_identical_geometry_is_equivalent():
    v = compare_branches(_straight_line(), _straight_line(), THRESHOLDS)
    assert v.result is Verdict.EQUIVALENT
    assert v.allows_reuse
    assert "长度比" in v.reason or "指标" in v.reason


def test_subpixel_resampling_stays_equivalent():
    """重提中心线导致的亚像素差异必须仍判等价——这是"不因哈希变化而全链重算"的依据。"""
    base = _straight_line(n=21)
    resampled = _straight_line(n=37)          # 采样点不同，几何相同
    v = compare_branches(base, resampled, THRESHOLDS)
    assert v.result is Verdict.EQUIVALENT, f"采样变化被误判为不等价：{v.reason}"


def test_large_shape_change_is_not_equivalent():
    a = _straight_line(length=100.0)
    b = _straight_line(length=140.0)          # 弧长差 40% >> 2%
    v = compare_branches(a, b, THRESHOLDS)
    assert v.result is Verdict.NON_EQUIVALENT
    assert not v.allows_reuse


def test_radius_change_beyond_tolerance_is_not_equivalent():
    a = _straight_line(radius=2.0)
    b = _straight_line(radius=2.6)            # 半径 +30%
    v = compare_branches(a, b, THRESHOLDS)
    assert v.result is Verdict.NON_EQUIVALENT


def test_borderline_case_returns_uncertain_not_a_guess():
    """接近阈值的差异必须输出 UNCERTAIN——不许把不确定硬塞进二值判定。"""
    a = _straight_line(length=100.0)
    b = _straight_line(length=101.9)          # 约 1.9%，落在 2% 阈值 ±10% 的灰区
    v = compare_branches(a, b, THRESHOLDS)
    assert v.result is Verdict.UNCERTAIN, f"灰区被判成了 {v.result}：{v.reason}"
    assert not v.allows_reuse, "UNCERTAIN 不得自动允许复用"


def test_uncertain_is_reported_with_metrics_and_threshold_ref():
    a = _straight_line(length=100.0)
    b = _straight_line(length=101.9)
    v = compare_branches(a, b, THRESHOLDS)
    assert v.result is Verdict.UNCERTAIN
    assert v.calibration_status == "PENDING_CALIBRATION"
    assert v.metrics.length_rel_diff > 0
    assert "阈值" in v.reason, "理由必须包含所用阈值，便于回放"


def test_thresholds_must_declare_calibration_status():
    with pytest.raises(ValueError, match="calibration_status"):
        EquivalenceThresholds(0.02, 0.05, 0.02, 0.1, calibration_status="LOOKS_FINE")


def test_calibrated_status_requires_reference():
    """声称已标定却不给标定依据 = 伪精确。"""
    with pytest.raises(ValueError, match="calibration_ref"):
        EquivalenceThresholds(0.02, 0.05, 0.02, 0.1, calibration_status="CALIBRATED")


def test_calibrated_with_reference_is_accepted():
    t = EquivalenceThresholds(
        0.02, 0.05, 0.02, 0.1,
        calibration_status="CALIBRATED",
        calibration_ref="SEG.A. 同中心 12 例受控扰动标定，2026-11",
    )
    v = compare_branches(_straight_line(), _straight_line(), t)
    assert v.calibration_status == "CALIBRATED"
    assert v.threshold_ref == t.calibration_ref


def test_invalid_geometry_is_rejected_early():
    with pytest.raises(ValueError, match="长度必须一致"):
        BranchGeometry([(0, 0, 0), (1, 0, 0)], [1.0])

    with pytest.raises(ValueError, match="半径必须为正"):
        BranchGeometry([(0, 0, 0), (1, 0, 0)], [1.0, 0.0])

    with pytest.raises(ValueError, match="至少需要两个"):
        BranchGeometry([(0, 0, 0)], [1.0])


def test_length_ratio_reveals_split_like_geometry():
    """长度比显著小于 1 是"可能发生了拆分/截断"的信号，必须出现在指标里。"""
    a = _straight_line(length=100.0)
    b = _straight_line(length=55.0)
    v = compare_branches(a, b, THRESHOLDS)
    assert v.metrics.length_ratio < 0.6
    assert v.result is Verdict.NON_EQUIVALENT

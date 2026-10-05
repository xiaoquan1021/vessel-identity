"""几何等价性验证（对应单向门 D7 / D13）。

**定位（2026-10-05 修正）**：本模块**不判定身份**。
身份由 lineage 模块的显式谱系承载；本模块只回答一件事：

    上游被修订之后，这份下游产物还能不能用？

因此它的输出是三值的（EQUIVALENT / NON_EQUIVALENT / UNCERTAIN），
并且"不可判定"是一等公民——不许把不确定硬塞进二值判断里。

为什么不做身份判定：血管树匹配文献明确拒绝靠分叉点/几何恢复身份
（假分叉、断裂中心线、同一批 50 例中出现 28 种标签排布）。
把不可靠的推断用在身份上会让错误不可见；用在"可复用性"上则是
可接受的工程折中——因为判不了可以标 UNCERTAIN 并交人工裁决。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class Verdict(str, Enum):
    EQUIVALENT = "EQUIVALENT"
    NON_EQUIVALENT = "NON_EQUIVALENT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class EquivalenceThresholds:
    """判定阈值。**任何取值都必须有来源**（解析解、文献或实测标定）。

    未标定的阈值必须写成 PENDING_CALIBRATION 并在报告中显式声明，
    否则平台会输出"看起来精确"的判定。
    """

    max_length_rel_diff: float          # 弧长相对差上限
    max_radius_profile_distance: float  # 半径剖面归一化 L∞ 距离上限
    max_centerline_distance_rel: float  # 中心线 Hausdorff 距离 / 弧长
    ambiguity_margin: float             # 落入灰区的比例宽度（触发 UNCERTAIN）
    calibration_status: str = "PENDING_CALIBRATION"
    calibration_ref: str = ""

    def __post_init__(self) -> None:
        for name in (
            "max_length_rel_diff",
            "max_radius_profile_distance",
            "max_centerline_distance_rel",
            "ambiguity_margin",
        ):
            v = getattr(self, name)
            if v < 0:
                raise ValueError(f"{name} 不得为负")
        if self.calibration_status not in ("PENDING_CALIBRATION", "CALIBRATED"):
            raise ValueError(
                "calibration_status 只能取 PENDING_CALIBRATION 或 CALIBRATED；"
                "标定完成时必须提供 calibration_ref"
            )
        if self.calibration_status == "CALIBRATED" and not self.calibration_ref:
            raise ValueError("标定为 CALIBRATED 时必须给出 calibration_ref（标定数据集与过程）")


@dataclass(frozen=True)
class BranchGeometry:
    """一段血管的几何摘要（足够做等价性验证的最小表示）。

    centerline: 有序点列，首尾为端点；坐标单位由外部保证一致（本库不做单位换算）
    radii:      与 centerline 等长的最大内切球半径
    """

    centerline: Sequence[tuple[float, float, float]]
    radii: Sequence[float]

    def __post_init__(self) -> None:
        if len(self.centerline) != len(self.radii):
            raise ValueError(
                f"centerline 与 radii 长度必须一致：{len(self.centerline)} vs {len(self.radii)}"
            )
        if len(self.centerline) < 2:
            raise ValueError("至少需要两个中心线点才能定义一段血管")
        if any(r <= 0 for r in self.radii):
            raise ValueError("半径必须为正（非正半径意味着中心线贴壁或几何无效）")

    @property
    def arc_length(self) -> float:
        total = 0.0
        for a, b in zip(self.centerline, self.centerline[1:]):
            total += math.dist(a, b)
        return total


@dataclass(frozen=True)
class EquivalenceMetrics:
    length_rel_diff: float
    radius_profile_distance: float
    centerline_distance_rel: float
    length_ratio: float          # 短/长，用于识别单侧显著变长（拆分征兆）


@dataclass(frozen=True)
class EquivalenceVerdict:
    """等价性判定结果。理由必须可回放（审稿人会问"你的判定器凭什么"）。"""

    result: Verdict
    metrics: EquivalenceMetrics
    threshold_ref: str
    reason: str
    calibration_status: str

    @property
    def allows_reuse(self) -> bool:
        """只有明确等价才允许复用下游产物。

        UNCERTAIN **不允许**自动复用——这是与"容忍不确定性"相反的取向：
        在科学计算里，宁可重算，不可假装确定。
        """
        return self.result is Verdict.EQUIVALENT


def compare_branches(
    a: BranchGeometry,
    b: BranchGeometry,
    thresholds: EquivalenceThresholds,
) -> EquivalenceVerdict:
    """比较两段血管几何，给出三值判定。"""
    len_a, len_b = a.arc_length, b.arc_length
    if len_a <= 0 or len_b <= 0:
        raise ValueError("弧长必须为正")

    length_rel_diff = abs(len_a - len_b) / max(len_a, len_b)
    length_ratio = min(len_a, len_b) / max(len_a, len_b)

    radius_profile_distance = _radius_profile_distance(a, b)
    centerline_distance_rel = _centerline_distance(a, b) / max(len_a, len_b)

    metrics = EquivalenceMetrics(
        length_rel_diff=length_rel_diff,
        radius_profile_distance=radius_profile_distance,
        centerline_distance_rel=centerline_distance_rel,
        length_ratio=length_ratio,
    )

    t = thresholds
    # 明确超出：任一指标显著超标
    if (
        length_rel_diff > t.max_length_rel_diff * (1.0 + t.ambiguity_margin)
        or radius_profile_distance > t.max_radius_profile_distance * (1.0 + t.ambiguity_margin)
        or centerline_distance_rel > t.max_centerline_distance_rel * (1.0 + t.ambiguity_margin)
    ):
        reason = _explain(metrics, t, "超出容差")
        return EquivalenceVerdict(
            result=Verdict.NON_EQUIVALENT,
            metrics=metrics,
            threshold_ref=t.calibration_ref,
            reason=reason,
            calibration_status=t.calibration_status,
        )

    # 明确在容差内：所有指标都远低于阈值
    if (
        length_rel_diff <= t.max_length_rel_diff * (1.0 - t.ambiguity_margin)
        and radius_profile_distance <= t.max_radius_profile_distance * (1.0 - t.ambiguity_margin)
        and centerline_distance_rel <= t.max_centerline_distance_rel * (1.0 - t.ambiguity_margin)
    ):
        reason = _explain(metrics, t, "全部指标在容差内")
        return EquivalenceVerdict(
            result=Verdict.EQUIVALENT,
            metrics=metrics,
            threshold_ref=t.calibration_ref,
            reason=reason,
            calibration_status=t.calibration_status,
        )

    # 灰区
    reason = _explain(metrics, t, "落入灰区（指标在阈值附近，无法可靠判定）")
    return EquivalenceVerdict(
        result=Verdict.UNCERTAIN,
        metrics=metrics,
        threshold_ref=t.calibration_ref,
        reason=reason,
        calibration_status=t.calibration_status,
    )


def _radius_profile_distance(a: BranchGeometry, b: BranchGeometry) -> float:
    """半径剖面的归一化 L∞ 距离（沿归一化弧长重采样后比较）。

    归一化到共同半径尺度，避免血管粗细不同导致的误判。
    """
    ra = _resample_profile(a.radii, len(a.centerline))
    rb = _resample_profile(b.radii, len(b.centerline))
    n = max(len(ra), len(rb), 8)
    ra2 = _resample_profile(ra, n)
    rb2 = _resample_profile(rb, n)
    scale = max(sum(ra2) / n, sum(rb2) / n)
    return max(abs(x - y) for x, y in zip(ra2, rb2)) / scale


def _resample_profile(values: Sequence[float], n: int) -> list[float]:
    """按弧长比例线性重采样到 n 个点。"""
    if len(values) == n:
        return list(values)
    out = []
    m = len(values)
    for i in range(n):
        pos = i * (m - 1) / (n - 1) if n > 1 else 0.0
        lo = int(math.floor(pos))
        hi = min(lo + 1, m - 1)
        frac = pos - lo
        out.append(values[lo] * (1 - frac) + values[hi] * frac)
    return out


def _centerline_distance(a: BranchGeometry, b: BranchGeometry) -> float:
    """对称 Hausdorff 距离的离散近似。

    **必须先重采样到公共采样率**：直接比较两个点集时，较稀疏的那条会把
    最坏点推到两个相邻采样点之间，距离被采样密度放大（实测：21 点与 37 点
    表示同一条直线时，归一化距离 0.0222 而真值为 0），导致把"重采样导致的
    表示差异"误判为几何差异。
    """
    n = 200
    xs = _resample_points(a.centerline, n)
    ys = _resample_points(b.centerline, n)
    return max(_directed_hausdorff(xs, ys), _directed_hausdorff(ys, xs))


def _resample_points(
    points: Sequence[tuple[float, float, float]], n: int
) -> list[tuple[float, float, float]]:
    """按弧长比例线性重采样折线到 n 个点。"""
    if len(points) < 2:
        raise ValueError("至少需要两个点")
    # 累积弧长
    cum = [0.0]
    for p, q in zip(points, points[1:]):
        cum.append(cum[-1] + math.dist(p, q))
    total = cum[-1]
    if total <= 0:
        raise ValueError("中心线弧长为零（存在重合点）")

    out: list[tuple[float, float, float]] = []
    j = 0
    for i in range(n):
        target = total * i / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        seg_len = cum[j + 1] - cum[j]
        frac = 0.0 if seg_len <= 0 else (target - cum[j]) / seg_len
        p, q = points[j], points[j + 1]
        out.append(tuple(p[k] + (q[k] - p[k]) * frac for k in range(3)))
    return out


def _directed_hausdorff(xs, ys) -> float:
    worst = 0.0
    for x in xs:
        best = min(math.dist(x, y) for y in ys)
        worst = max(worst, best)
    return worst


def _explain(metrics: EquivalenceMetrics, t: EquivalenceThresholds, headline: str) -> str:
    return (
        f"{headline}；"
        f"弧长相对差 {metrics.length_rel_diff:.4g}（阈值 {t.max_length_rel_diff:g}）、"
        f"半径剖面距离 {metrics.radius_profile_distance:.4g}（阈值 {t.max_radius_profile_distance:g}）、"
        f"中心线距离/弧长 {metrics.centerline_distance_rel:.4g}（阈值 {t.max_centerline_distance_rel:g}）、"
        f"长度比 {metrics.length_ratio:.4g}；标定状态 {t.calibration_status}"
    )

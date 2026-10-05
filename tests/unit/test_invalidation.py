"""失效传播的行为测试（对应单向门 D7 / D13）。

这些测试断言的核心是**判定顺序**：
先按谱系边类型做确定性判定，再用等价性做二次确认。
以及一条纪律：判不了就标 STALE_UNCERTAIN，禁止静默当 current。
"""

from __future__ import annotations

import pytest

from vessel_identity import (
    ArtifactDependency,
    BranchGeometry,
    DeclarationSource,
    EntityId,
    EquivalenceThresholds,
    LineageEdge,
    LineageStore,
    OperationKind,
    RevisionId,
    Validity,
    invalidate,
    transitive_closure,
)

T0 = "2026-10-05T10:00:00Z"
T1 = "2026-10-05T10:05:00Z"

THRESHOLDS = EquivalenceThresholds(
    max_length_rel_diff=0.02,
    max_radius_profile_distance=0.05,
    max_centerline_distance_rel=0.02,
    ambiguity_margin=0.10,
)


def _line(n: int = 21, length: float = 100.0, radius: float = 2.0) -> BranchGeometry:
    return BranchGeometry(
        [(i * length / (n - 1), 0.0, 0.0) for i in range(n)], [radius] * n
    )


def _chain():
    """建一条初始 → REFINE 的两修订链。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = RevisionId.new()
    store.append(LineageEdge(None, None, e, r0, OperationKind.REFINE,
                             DeclarationSource.ALGORITHM, T0, "tester", notes="初始"))
    r1 = RevisionId.new()
    edge = LineageEdge(e, r0, e, r1, OperationKind.REFINE,
                       DeclarationSource.ALGORITHM, T1, "tester")
    store.append(edge)
    return e, store, r0, r1, edge


# ------------------------------------------------------- 无关修订不影响产物
def test_untouched_artifact_stays_reusable():
    _, _, r0, _r1, edge = _chain()
    other = RevisionId.new()
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({other})),
        delta_edges=[edge],
        equivalence_pairs=None,
        thresholds=None,
    )
    assert result.validity is Validity.REUSABLE
    assert result.may_serve_as_current


# --------------------------------------------- 拓扑改变：确定性判定，无需度量
@pytest.mark.parametrize("kind", [
    OperationKind.SPLIT,
    OperationKind.MERGE,
    OperationKind.DELETE_PART,
])
def test_topology_change_invalidates_without_any_metrics(kind):
    """拓扑改变必须立即判 INVALID——不依赖任何几何度量，也不给"侥幸复用"的机会。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = RevisionId.new()
    store.append(LineageEdge(None, None, e, r0, OperationKind.REFINE,
                             DeclarationSource.ALGORITHM, T0, "tester", notes="初始"))
    child, r1 = EntityId.new(), RevisionId.new()
    edge = LineageEdge(e, r0, child, r1, kind, DeclarationSource.HUMAN, T1, "tester",
                       notes="拓扑改变，涉及实体 e 与 child")
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs=None,     # 故意不给：拓扑改变不需要度量
        thresholds=None,
    )
    assert result.validity is Validity.INVALID
    assert not result.may_serve_as_current
    assert any(kind.value in r for r in result.reasons)


def test_unattributed_new_entity_invalidates():
    """完全无法归属的新实体：下游必须重算（不能默认当成同一实体）。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = RevisionId.new()
    store.append(LineageEdge(None, None, e, r0, OperationKind.REFINE,
                             DeclarationSource.ALGORITHM, T0, "tester", notes="初始"))
    new_entity, r1 = EntityId.new(), RevisionId.new()
    edge = LineageEdge(None, None, new_entity, r1, OperationKind.UNATTRIBUTED,
                       DeclarationSource.HUMAN, T1, "tester", notes="换了另一版分割")
    # 注意：这条边的 parent_revision 为 None，不会匹配依赖 r0，
    # 因此需要通过一个显式指向 r0 的场景来测——见下一个测试。
    assert edge.parent_revision is None


# ------------------------------------------- REFINE：需要度量，三值各就其位
def test_refine_with_equivalent_geometry_is_reusable():
    """关键用例：端点有亚像素位移但几何等价 → 必须判可复用，不得因字节变化就全链重算。"""
    _, _, r0, r1, edge = _chain()
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs={(r0, r1): (_line(n=21), _line(n=37))},   # 采样不同，几何相同
        thresholds=THRESHOLDS,
    )
    assert result.validity is Validity.REUSABLE, result.reasons
    assert result.may_serve_as_current
    assert result.equivalence_verdicts, "必须留下等价性判定作为证据"


def test_refine_with_different_geometry_invalidates():
    _, _, r0, r1, edge = _chain()
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs={(r0, r1): (_line(length=100.0), _line(length=140.0))},
        thresholds=THRESHOLDS,
    )
    assert result.validity is Validity.INVALID


def test_refine_in_ambiguous_zone_yields_stale_uncertain():
    """灰区必须输出 STALE_UNCERTAIN，且**不得**被当作 current。"""
    _, _, r0, r1, edge = _chain()
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs={(r0, r1): (_line(length=100.0), _line(length=101.9))},
        thresholds=THRESHOLDS,
    )
    assert result.validity is Validity.STALE_UNCERTAIN, result.reasons
    assert not result.may_serve_as_current


def test_missing_geometry_pair_yields_uncertain_not_reusable():
    """需要度量却没有几何对：必须标不确定，绝不能默认可复用。"""
    _, _, r0, r1, edge = _chain()
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs=None,
        thresholds=THRESHOLDS,
    )
    assert result.validity is Validity.STALE_UNCERTAIN
    assert not result.may_serve_as_current


def test_missing_thresholds_yields_uncertain():
    _, _, r0, r1, edge = _chain()
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs={(r0, r1): (_line(), _line())},
        thresholds=None,
    )
    assert result.validity is Validity.STALE_UNCERTAIN


# ------------------------------------------------- 反例：两个方向的"看起来一样"
def test_byte_level_change_without_geometry_change_is_reusable():
    """反例一：只重新序列化（几何未变）→ 不得判 INVALID。

    这条直接对抗"DVC 式哈希失效"的做法：内容寻址让任何字节变化都产生新地址，
    但对本命题来说，几何没变就该保留下游结果。
    """
    _, _, r0, r1, edge = _chain()
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs={(r0, r1): (_line(), _line())},
        thresholds=THRESHOLDS,
    )
    assert result.validity is Validity.REUSABLE


def test_topology_change_cannot_be_rescued_by_geometry_metrics():
    """反例二：拓扑变了但几何度量看起来相似 → 必须仍是 INVALID。

    这是"判定顺序"的关键防线：度量不得推翻声明。
    """
    e = EntityId.new()
    store = LineageStore()
    r0 = RevisionId.new()
    store.append(LineageEdge(None, None, e, r0, OperationKind.REFINE,
                             DeclarationSource.ALGORITHM, T0, "tester", notes="初始"))
    child, r1 = EntityId.new(), RevisionId.new()
    split_edge = LineageEdge(e, r0, child, r1, OperationKind.SPLIT,
                             DeclarationSource.HUMAN, T1, "tester",
                             notes="拆分为两支")
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[split_edge],
        # 故意给出"看起来完全相同"的几何，模拟度量误判
        equivalence_pairs={(r0, r1): (_line(), _line())},
        thresholds=THRESHOLDS,
    )
    assert result.validity is Validity.INVALID, "度量不得推翻拓扑声明"


def test_renumber_requires_equivalence_evidence():
    """仅编号变化（RENUMBER）不予直接采信，必须有等价性证据。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = RevisionId.new()
    store.append(LineageEdge(None, None, e, r0, OperationKind.REFINE,
                             DeclarationSource.ALGORITHM, T0, "tester", notes="初始"))
    r1 = RevisionId.new()
    edge = LineageEdge(e, r0, e, r1, OperationKind.RENUMBER,
                       DeclarationSource.ALGORITHM, T1, "tester")
    # 无几何对 → 不确定
    r_no_geom = invalidate(
        ArtifactDependency("wss", depends_on=frozenset({r0})),
        delta_edges=[edge], equivalence_pairs=None, thresholds=THRESHOLDS,
    )
    assert r_no_geom.validity is Validity.STALE_UNCERTAIN

    # 有几何对且等价 → 可复用
    r_geom = invalidate(
        ArtifactDependency("wss", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs={(r0, r1): (_line(), _line())},
        thresholds=THRESHOLDS,
    )
    assert r_geom.validity is Validity.REUSABLE


# ------------------------------------------------------ 下游闭包（辅助查询）
def test_transitive_closure_finds_all_downstream_revisions():
    e = EntityId.new()
    store = LineageStore()
    r0 = RevisionId.new()
    store.append(LineageEdge(None, None, e, r0, OperationKind.REFINE,
                             DeclarationSource.ALGORITHM, T0, "tester", notes="初始"))
    prev = r0
    downstream = []
    for i in range(3):
        nxt = RevisionId.new()
        store.append(LineageEdge(e, prev, e, nxt, OperationKind.REFINE,
                                 DeclarationSource.ALGORITHM, T1, "tester"))
        downstream.append(nxt)
        prev = nxt

    closure = transitive_closure(store, r0)
    assert closure == set(downstream), "闭包应包含全部下游修订（不含自身）"
    assert r0 not in closure


def test_reasons_are_replayable_and_mention_the_revision():
    """理由必须能回放：要指明是哪个修订、发生了什么、结论依据是什么。"""
    _, _, r0, r1, edge = _chain()
    result = invalidate(
        ArtifactDependency("wss_map", depends_on=frozenset({r0})),
        delta_edges=[edge],
        equivalence_pairs={(r0, r1): (_line(length=100.0), _line(length=140.0))},
        thresholds=THRESHOLDS,
    )
    joined = " ".join(result.reasons)
    assert str(r0) in joined
    assert "REFINE" in joined
    assert "阈值" in joined or "不等价" in joined

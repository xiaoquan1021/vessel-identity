"""谱系与身份的行为测试（对应单向门 D1 / D2）。

这些测试描述的是**行为契约**，不是实现细节：
任何人重写实现，只要行为符合这些断言，就算通过。
"""

from __future__ import annotations

import pytest

from vessel_identity import (
    DeclarationSource,
    EntityId,
    LineageEdge,
    LineageError,
    LineageStore,
    OperationKind,
    RevisionId,
)

T0 = "2026-10-05T10:00:00Z"
T1 = "2026-10-05T10:05:00Z"
T2 = "2026-10-05T10:10:00Z"


def _initial(store: LineageStore, entity: EntityId) -> RevisionId:
    rev = RevisionId.new()
    store.append(
        LineageEdge(None, None, entity, rev, OperationKind.REFINE,
                    DeclarationSource.ALGORITHM, T0, "tester", notes="初始修订")
    )
    return rev


def _refine(store: LineageStore, entity: EntityId, parent: RevisionId, at: str = T1) -> RevisionId:
    child = RevisionId.new()
    store.append(
        LineageEdge(entity, parent, entity, child, OperationKind.REFINE,
                    DeclarationSource.ALGORITHM, at, "tester")
    )
    return child


# ---------------------------------------------------------------- 身份持久性
def test_refinement_keeps_entity_identity():
    """重提中心线（REFINE）不得改变实体身份——这是 D1 的核心断言。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)
    r1 = _refine(store, e, r0)
    r2 = _refine(store, e, r1)

    assert store.root_of(r2) == r0, "重算后身份链的根必须不变"
    assert len(store.ancestors(r2)) == 2
    assert store.entities_of(r2) == {e}


def test_three_recomputations_yield_identical_identity_chain():
    """同一分割上重提中心线三次：实体 ID 不变、修订 ID 递增。"""
    e = EntityId.new()
    store = LineageStore()
    r = _initial(store, e)
    chain = [r]
    for i in range(3):
        r = _refine(store, e, r, at=f"2026-10-05T10:0{i}:00Z")
        chain.append(r)

    assert len(set(chain)) == 4, "每次重算必须产生新的修订 ID"
    assert store.entities_of(chain[-1]) == {e}, "实体身份始终唯一且不变"
    assert store.root_of(chain[-1]) == chain[0]


def test_split_creates_new_entity_with_traceable_parent():
    """拆分：产生新实体，但父子关系可追溯，旧实体与旧修订仍在。"""
    parent = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, parent)

    child = EntityId.new()
    r1 = RevisionId.new()
    store.append(
        LineageEdge(parent, r0, child, r1, OperationKind.SPLIT,
                    DeclarationSource.HUMAN, T1, "tester",
                    notes="在长支中部发现真实分叉，拆为两支")
    )

    assert store.entities_of(r1) == {parent, child}, "拆分后两侧实体都必须可追溯"
    assert store.incoming(r0) is not None, "旧修订不得被删除"


def test_merge_is_recorded_and_old_revisions_survive():
    a, b = EntityId.new(), EntityId.new()
    store = LineageStore()
    ra, rb = _initial(store, a), _initial(store, b)

    merged = EntityId.new()
    rm = RevisionId.new()
    store.append(
        LineageEdge(a, ra, merged, rm, OperationKind.MERGE,
                    DeclarationSource.HUMAN, T2, "tester",
                    notes="修正误连：两条分支实为一条")
    )
    assert store.incoming(ra) is not None and store.incoming(rb) is not None
    assert merged in store.entities_of(rm)


# ---------------------------------------------------------------- append-only
def test_edges_are_append_only_and_correction_supersedes():
    """改正必须追加新边，不得就地修改旧边。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)

    r1 = RevisionId.new()
    wrong = LineageEdge(e, r0, e, r1, OperationKind.REFINE,
                        DeclarationSource.ALGORITHM, T1, "tester", notes="误标为 REFINE")
    store.append(wrong)
    assert len(store.all_edges_including_superseded()) == 2

    corrected = LineageEdge(e, r0, e, r1, OperationKind.UNATTRIBUTED,
                            DeclarationSource.HUMAN, T2, "tester",
                            notes="复核后发现无法声明对应关系", supersedes="wrong-1")
    store.supersede(1, corrected)

    assert len(store.all_edges_including_superseded()) == 3, "旧边必须仍在存储中"
    assert store.incoming(r1).operation is OperationKind.UNATTRIBUTED
    assert len(store) == 2, "活动边数不含被取代者"


def test_duplicate_incoming_edge_is_rejected():
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)
    r1 = _refine(store, e, r0)
    with pytest.raises(LineageError) as exc:
        store.append(
            LineageEdge(e, r0, e, r1, OperationKind.REFINE,
                        DeclarationSource.ALGORITHM, T2, "tester")
        )
    assert exc.value.code == "LINEAGE.DUPLICATE_INCOMING"


# ---------------------------------------------------------------- 诚实性约束
def test_unattributed_same_entity_requires_notes():
    """UNATTRIBUTED 但声称同一实体时，必须写明为何无法归类。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)
    r1 = RevisionId.new()
    with pytest.raises(LineageError) as exc:
        store.append(
            LineageEdge(e, r0, e, r1, OperationKind.UNATTRIBUTED,
                        DeclarationSource.HUMAN, T1, "tester", notes="")
        )
    assert exc.value.code == "LINEAGE.UNATTRIBUTED_NEEDS_NOTES"


def test_unattributed_with_parent_revision_is_rejected():
    """UNATTRIBUTED 且无父实体时，不得同时声明上游修订。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)
    r1 = RevisionId.new()
    with pytest.raises(LineageError) as exc:
        store.append(
            LineageEdge(None, r0, e, r1, OperationKind.UNATTRIBUTED,
                        DeclarationSource.HUMAN, T1, "tester", notes="换了另一版分割")
        )
    assert exc.value.code == "LINEAGE.UNATTRIBUTED_WITH_PARENT_REVISION"


def test_fully_unattributed_child_has_no_ancestors():
    """完全无法归属的新实体：没有祖先，必须被显式标注而非默认猜成同一实体。"""
    e = EntityId.new()
    store = LineageStore()
    r1 = RevisionId.new()
    store.append(
        LineageEdge(None, None, e, r1, OperationKind.UNATTRIBUTED,
                    DeclarationSource.HUMAN, T1, "tester",
                    notes="换了完全不同的分割流程，无法声明与任何既有实体的对应")
    )
    assert store.ancestors(r1) == []
    assert store.is_initial(r1)


def test_inferred_source_cannot_assert_identity():
    """推断来源不得声明同一性——这正是血管树匹配文献拒绝的做法。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)
    r1 = RevisionId.new()
    with pytest.raises(LineageError) as exc:
        store.append(
            LineageEdge(e, r0, e, r1, OperationKind.REFINE,
                        DeclarationSource.INFERRED, T1, "tester")
        )
    assert exc.value.code == "LINEAGE.INFERRED_CANNOT_ASSERT_IDENTITY"


def test_topology_change_requires_notes():
    """拆分/合并必须写明涉及哪些实体与理由，否则无法审计。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)
    child, r1 = EntityId.new(), RevisionId.new()
    with pytest.raises(LineageError) as exc:
        store.append(
            LineageEdge(e, r0, child, r1, OperationKind.SPLIT,
                        DeclarationSource.HUMAN, T1, "tester", notes="   ")
        )
    assert exc.value.code == "LINEAGE.TOPOLOGY_CHANGE_NEEDS_NOTES"


def test_initial_revision_cannot_have_parent_entity():
    e = EntityId.new()
    store = LineageStore()
    r0 = RevisionId.new()
    with pytest.raises(LineageError) as exc:
        store.append(
            LineageEdge(e, None, e, r0, OperationKind.REFINE,
                        DeclarationSource.ALGORITHM, T0, "tester")
        )
    assert exc.value.code == "LINEAGE.INITIAL_WITH_PARENT"


# ---------------------------------------------------------------- 完整性
def test_broken_chain_is_detected():
    """上游修订没有入边 = 谱系断裂，必须被检出。"""
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)
    orphan_parent = RevisionId.new()   # 凭空出现，没有入边
    r1 = RevisionId.new()
    store.append(
        LineageEdge(e, orphan_parent, e, r1, OperationKind.REFINE,
                    DeclarationSource.ALGORITHM, T1, "tester")
    )
    problems = store.validate_integrity()
    assert any("没有入边" in p for p in problems)


def test_identity_of_a_fresh_entity_is_itself():
    e = EntityId.new()
    store = LineageStore()
    r0 = _initial(store, e)
    assert store.root_of(r0) == r0
    assert store.ancestors(r0) == []

"""失效传播（对应单向门 D7 / D13）。

判定顺序（这是本模块最重要的设计决定）：

  0. **先看依赖是否还活着**（确定性判定）——依赖的修订已不是当前修订、
     本次谱系里又找不到它的后继，就是孤立依赖，必须重算；
  1. **再看谱系边的类型**（确定性判定）——拓扑是否改变由声明决定，不由度量决定；
  2. **再用等价性做二次确认**（度量判定）——仅在需要时才计算，且不能推翻第 1 步结论；
  3. 判不了就输出 ``STALE_UNCERTAIN``，禁止静默当作 current。

为什么是这个顺序：等价性是度量，会失败、会有灰区；身份与拓扑语义是声明，
应当是确定的。若让度量推翻声明，就等于把不可靠的推断升级成了事实。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .equivalence import (
    BranchGeometry,
    EquivalenceThresholds,
    EquivalenceVerdict,
    Verdict,
    compare_branches,
)
from .ids import RevisionId
from .lineage import LineageEdge, LineageStore, OperationKind


class Validity(str, Enum):
    INVALID = "INVALID"                     # 必须重算
    REUSABLE = "REUSABLE"                   # 与本次修订无关或等价，可保留
    STALE_UNCERTAIN = "STALE_UNCERTAIN"     # 无法判定，显式标记，禁止当 current


@dataclass(frozen=True)
class ArtifactDependency:
    """一个下游产物对上游修订的依赖声明。

    **必须显式声明**。未声明的依赖等于没有依赖——这正是工作流引擎
    （Snakemake/Nextflow）最常见的失效模式：忘了声明就静默陈旧复用。
    """

    artifact_id: str
    depends_on: frozenset[RevisionId]


@dataclass(frozen=True)
class InvalidationResult:
    artifact_id: str
    validity: Validity
    reasons: tuple[str, ...]
    equivalence_verdicts: tuple[EquivalenceVerdict, ...] = ()
    rebased_dependencies: tuple[tuple[RevisionId, RevisionId], ...] = ()
    """REUSABLE 且经等价性确认时的（旧修订, 新修订）对。

    调用方应据此把产物的依赖改钉到新修订；不改钉的话，下一次判定会把旧修订
    当作孤立依赖而保守地判 INVALID（多余重算，而不是错误复用）。
    """

    @property
    def may_serve_as_current(self) -> bool:
        """只有 REUSABLE 才可作为 current 结果提供。"""
        return self.validity is Validity.REUSABLE


def _as_current_set(current_revisions: Iterable[RevisionId]) -> frozenset[RevisionId]:
    if current_revisions is None:
        raise TypeError(
            "current_revisions 不得为 None：不知道哪些修订是当前的，就无法发现孤立依赖"
            "（依赖被无归属重导入取代或被删除）——这正是静默错误复用的来源。"
            "请传入当前全部修订的集合（可以为空集合）"
        )
    current = frozenset(current_revisions)
    wrong = [x for x in current if not isinstance(x, RevisionId)]
    if wrong:
        raise TypeError(
            f"current_revisions 只能包含 RevisionId，实际含 {type(wrong[0]).__name__}"
            "（常见错误：传入了 EntityId）"
        )
    return current


def invalidate(
    artifact: ArtifactDependency,
    delta_edges: Iterable[LineageEdge],
    *,
    current_revisions: Iterable[RevisionId],
    equivalence_pairs: Mapping[tuple[RevisionId, RevisionId], tuple[BranchGeometry, BranchGeometry]]
    | None = None,
    thresholds: EquivalenceThresholds | None = None,
) -> InvalidationResult:
    """判定一个产物在给定的修订集合下是否仍然有效。

    artifact:            下游产物的依赖声明
    delta_edges:         本次产生的谱系边（上游改动）
    current_revisions:   当前全部修订的集合（必填；用于发现孤立依赖）
    equivalence_pairs:   需要做等价性验证的（旧修订, 新修订）→ 几何对
    thresholds:          等价性阈值（仅在需要等价性验证时使用）
    """
    current = _as_current_set(current_revisions)
    edges = list(delta_edges)
    reasons: list[str] = []
    verdicts: list[EquivalenceVerdict] = []

    # 第 0 步：孤立依赖（确定性判定）。完全无法归属的 UNATTRIBUTED 边不带父修订，
    # 删除也可能不留边——两者都只能从「依赖已不是当前修订、且找不到后继」看出来。
    has_successor = {e.parent_revision for e in edges if e.parent_revision is not None}
    orphaned = sorted(
        (r for r in artifact.depends_on if r not in current and r not in has_successor),
        key=str,
    )
    if orphaned:
        for r in orphaned:
            reasons.append(
                f"依赖 {r} 已不是当前修订，且本次谱系中没有以它为父的边"
                "（被无归属的新实体取代，或已删除）→ 孤立依赖，下游必须重算"
            )
        return InvalidationResult(
            artifact_id=artifact.artifact_id,
            validity=Validity.INVALID,
            reasons=tuple(reasons),
        )

    relevant = [e for e in edges if e.parent_revision in artifact.depends_on]
    if not relevant:
        return InvalidationResult(
            artifact_id=artifact.artifact_id,
            validity=Validity.REUSABLE,
            reasons=("本次修订未触及该产物的任何依赖",),
        )

    needs_equivalence: list[tuple[RevisionId, RevisionId]] = []

    for edge in relevant:
        kind = edge.operation

        # 第 1 步：确定性判定（按谱系边类型）
        if kind in (
            OperationKind.SPLIT,
            OperationKind.MERGE,
            OperationKind.DELETE_PART,
            OperationKind.UNATTRIBUTED,
        ):
            reasons.append(
                f"依赖 {edge.parent_revision} 发生 {kind.value}"
                f"（拓扑或对应关系改变）→ 下游必须重算"
            )
            return InvalidationResult(
                artifact_id=artifact.artifact_id,
                validity=Validity.INVALID,
                reasons=tuple(reasons),
            )

        if kind is OperationKind.RENUMBER:
            # 仅编号变化：几何与语义不变，但**必须有证据**，否则不予采信
            reasons.append(
                f"依赖 {edge.parent_revision} 被声明为 RENUMBER（仅编号变化）；"
                "需等价性验证确认几何未变"
            )
            needs_equivalence.append((edge.parent_revision, edge.child_revision))
            continue

        if kind in (OperationKind.REFINE, OperationKind.REPLACE_EQUIVALENT):
            reasons.append(
                f"依赖 {edge.parent_revision} 发生 {kind.value}；"
                "需等价性验证确认下游可复用"
            )
            needs_equivalence.append((edge.parent_revision, edge.child_revision))
            continue

        # 未预期的操作类型：宁可重算
        reasons.append(f"依赖 {edge.parent_revision} 发生未知操作 {kind.value} → 保守判为需重算")
        return InvalidationResult(
            artifact_id=artifact.artifact_id,
            validity=Validity.INVALID,
            reasons=tuple(reasons),
        )

    # 第 2 步：等价性验证（度量判定）
    if not needs_equivalence:
        return InvalidationResult(
            artifact_id=artifact.artifact_id,
            validity=Validity.REUSABLE,
            reasons=tuple(reasons),
        )

    if equivalence_pairs is None or thresholds is None:
        reasons.append(
            "需要等价性验证但未提供几何对或阈值 → 无法判定，标记为 STALE_UNCERTAIN"
        )
        return InvalidationResult(
            artifact_id=artifact.artifact_id,
            validity=Validity.STALE_UNCERTAIN,
            reasons=tuple(reasons),
        )

    for old_rev, new_rev in needs_equivalence:
        pair = equivalence_pairs.get((old_rev, new_rev))
        if pair is None:
            reasons.append(
                f"缺少 {old_rev} → {new_rev} 的几何对 → 无法判定，标记为 STALE_UNCERTAIN"
            )
            return InvalidationResult(
                artifact_id=artifact.artifact_id,
                validity=Validity.STALE_UNCERTAIN,
                reasons=tuple(reasons),
            )
        verdict = compare_branches(pair[0], pair[1], thresholds)
        verdicts.append(verdict)
        if verdict.result is Verdict.NON_EQUIVALENT:
            reasons.append(f"{old_rev} → {new_rev} 几何不等价：{verdict.reason}")
            return InvalidationResult(
                artifact_id=artifact.artifact_id,
                validity=Validity.INVALID,
                reasons=tuple(reasons),
                equivalence_verdicts=tuple(verdicts),
            )
        if verdict.result is Verdict.UNCERTAIN:
            reasons.append(f"{old_rev} → {new_rev} 等价性无法判定：{verdict.reason}")
            return InvalidationResult(
                artifact_id=artifact.artifact_id,
                validity=Validity.STALE_UNCERTAIN,
                reasons=tuple(reasons),
                equivalence_verdicts=tuple(verdicts),
            )
        reasons.append(f"{old_rev} → {new_rev} 等价性确认通过：{verdict.reason}")

    # 后继本身也不是当前修订：本次谱系含多步修订（如 r0→r1→r2），
    # 只确认了第一步，不能声称对当前修订可复用。
    stale_successors = sorted({new for _, new in needs_equivalence if new not in current}, key=str)
    if stale_successors:
        for r in stale_successors:
            reasons.append(
                f"后继修订 {r} 也不是当前修订（本次谱系含多步修订）→ 无法判定，"
                "请逐步判定并改钉依赖，或提供直达当前修订的几何对"
            )
        return InvalidationResult(
            artifact_id=artifact.artifact_id,
            validity=Validity.STALE_UNCERTAIN,
            reasons=tuple(reasons),
            equivalence_verdicts=tuple(verdicts),
        )

    return InvalidationResult(
        artifact_id=artifact.artifact_id,
        validity=Validity.REUSABLE,
        reasons=tuple(reasons),
        equivalence_verdicts=tuple(verdicts),
        rebased_dependencies=tuple(needs_equivalence),
    )


def transitive_closure(
    store: LineageStore, start: RevisionId
) -> set[RevisionId]:
    """某修订的全部下游修订（用于把一次改动换算成受影响范围）。

    注意：这不等于"全部失效"——失效与否仍要逐个产物按 invalidate() 判定。
    """
    seen: set[RevisionId] = set()
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for edge in store.children_of(current):
            if edge.child_revision not in seen:
                seen.add(edge.child_revision)
                frontier.append(edge.child_revision)
    return seen

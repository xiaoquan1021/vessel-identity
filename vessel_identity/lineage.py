"""谱系：身份的真正来源（对应单向门 D1 / D2）。

核心立场：
  **身份由显式谱系承载，不由几何推断。**
  几何比对（见 equivalence 模块）只用于验证下游产物是否可复用，
  绝不用于回答"这条血管是不是原来那条"。

依据：血管树匹配文献明确拒绝靠分叉点恢复身份（假分叉、断裂中心线、
同一批 50 例中出现 28 种标签排布）；OpenLineage #4127 显示
"标识派生规则漏一处 = 血缘静默丢失"而非报错。

因此本模块的设计要求：
  1. 每条边都是显式声明的，可回放理由；
  2. 边是 append-only，改正只能追加新边并 supersede 旧边；
  3. 允许操作方声明"我不知道怎么对应"（UNATTRIBUTED）——诚实优于猜测；
  4. 每个修订恰好有一条入边（初始修订除外）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .ids import EntityId, RevisionId


class OperationKind(str, Enum):
    """几何操作的语义类型。

    这些取值决定下游失效传播的**默认**结论（见 invalidation 模块）：
    - REFINE / REPLACE_EQUIVALENT：几何变了但仍是同一实体、同一语义 → 可能可复用
    - SPLIT / MERGE / DELETE_PART：拓扑语义改变 → 下游必须失效
    - RENUMBER：仅编号变化，几何与语义不变（用于标记上游工具的重编号行为）
    - UNATTRIBUTED：操作方无法声明对应关系 → 不得假设 1:1
    """

    REFINE = "REFINE"
    REPLACE_EQUIVALENT = "REPLACE_EQUIVALENT"
    SPLIT = "SPLIT"
    MERGE = "MERGE"
    DELETE_PART = "DELETE_PART"
    RENUMBER = "RENUMBER"
    UNATTRIBUTED = "UNATTRIBUTED"


class DeclarationSource(str, Enum):
    """谁声明了这条谱系边。与证据语义（evidence × operation）对接。"""

    ALGORITHM = "ALGORITHM"     # 算法自动声明（确定性操作）
    HUMAN = "HUMAN"             # 人工声明（编辑、裁决）
    IMPORT = "IMPORT"           # 外部导入时声明
    INFERRED = "INFERRED"       # 推断得出——**必须显式，不得混入前两者**


# 每种操作类型要求参与的实体数量：(上游数下限, 下游数下限, 是否为拓扑改变)
_TOPOLOGY_CHANGING = {
    OperationKind.REFINE: False,
    OperationKind.REPLACE_EQUIVALENT: False,
    OperationKind.RENUMBER: False,
    OperationKind.SPLIT: True,
    OperationKind.MERGE: True,
    OperationKind.DELETE_PART: True,
    OperationKind.UNATTRIBUTED: False,
}


@dataclass(frozen=True)
class LineageEdge:
    """一条谱系边：从上游修订到下游修订的显式声明。

    初始修订的边：``parent_revision is None``（没有上游），
    且 ``parent_entity is None``（实体在此诞生）。
    """

    parent_entity: EntityId | None      # None 表示新建实体
    parent_revision: RevisionId | None  # None 表示初始修订（无上游）
    child_entity: EntityId
    child_revision: RevisionId
    operation: OperationKind
    source: DeclarationSource
    created_at: str                      # ISO-8601；由调用方提供，避免库内取时钟
    created_by: str
    notes: str = ""
    supersedes: str | None = None        # 被本边取代的边的标识（改正时使用）

    @property
    def is_initial(self) -> bool:
        return self.parent_revision is None

    @property
    def is_topology_changing(self) -> bool:
        return _TOPOLOGY_CHANGING[self.operation]


class LineageError(ValueError):
    """谱系违规。信息必须可操作。"""

    def __init__(self, code: str, what: str, how_to_fix: str) -> None:
        super().__init__(f"[{code}] {what} — 修法：{how_to_fix}")
        self.code = code
        self.what = what
        self.how_to_fix = how_to_fix


@dataclass
class LineageStore:
    """append-only 的谱系存储。

    刻意保持简单（内存 + 列表），以便被独立测试与审查；
    持久化由使用方负责（本库不绑定任何存储后端）。
    """

    _edges: list[LineageEdge] = field(default_factory=list)
    _superseded: set[int] = field(default_factory=set)

    # ---- 写入 ----------------------------------------------------------
    def append(self, edge: LineageEdge) -> None:
        self._validate_new(edge)
        self._edges.append(edge)

    def supersede(self, index: int, edge: LineageEdge) -> None:
        """用新边取代旧边。旧边保留在存储中（append-only），但不再参与判定。"""
        if index not in range(len(self._edges)):
            raise LineageError(
                code="LINEAGE.BAD_INDEX",
                what=f"要取代的边下标 {index} 不存在",
                how_to_fix=f"合法下标范围 0..{len(self._edges) - 1}",
            )
        if self._edges[index].child_revision != edge.child_revision:
            raise LineageError(
                code="LINEAGE.SUPERSEDE_MISMATCH",
                what="新边与被取代边的下游修订不一致",
                how_to_fix="取代必须针对同一个下游修订；若下游也变了，请追加新边而不是取代",
            )
        self._validate_new(edge, allow_existing_child=True)
        self._superseded.add(index)
        self._edges.append(edge)

    # ---- 查询 ----------------------------------------------------------
    def active_edges(self) -> list[LineageEdge]:
        return [e for i, e in enumerate(self._edges) if i not in self._superseded]

    def incoming(self, revision: RevisionId) -> LineageEdge | None:
        """某修订的入边（身份来源）。"""
        for e in self.active_edges():
            if e.child_revision == revision:
                return e
        return None

    def children_of(self, revision: RevisionId) -> list[LineageEdge]:
        return [e for e in self.active_edges() if e.parent_revision == revision]

    def ancestors(self, revision: RevisionId) -> list[RevisionId]:
        """沿入边回溯到初始修订，返回途中的**祖先修订**（不含自身）。

        身份追溯的主路径：链的末端是 root_of() 的结果。
        """
        chain: list[RevisionId] = []
        current = revision
        guard = 0
        while True:
            edge = self.incoming(current)
            if edge is None or edge.parent_revision is None:
                break
            chain.append(edge.parent_revision)
            current = edge.parent_revision
            guard += 1
            if guard > 10_000:
                raise LineageError(
                    code="LINEAGE.CYCLE_OR_TOO_DEEP",
                    what="追溯深度超过 10000，谱系可能存在环或异常",
                    how_to_fix="检查是否有反向或自指的谱系边",
                )
        return chain

    def root_of(self, revision: RevisionId) -> RevisionId:
        """回溯到最早可追溯的修订（身份链的起点）。"""
        chain = self.ancestors(revision)
        return chain[-1] if chain else revision

    def is_initial(self, revision: RevisionId) -> bool:
        edge = self.incoming(revision)
        return edge is not None and edge.is_initial

    def entities_of(self, revision: RevisionId) -> set[EntityId]:
        """某修订在谱系上关联的全部实体（含拆分/合并的祖先实体）。"""
        ents: set[EntityId] = set()
        edge = self.incoming(revision)
        while edge is not None:
            ents.add(edge.child_entity)
            if edge.parent_entity is None or edge.parent_revision is None:
                break
            ents.add(edge.parent_entity)
            edge = self.incoming(edge.parent_revision)
        return ents

    # ---- 校验 ----------------------------------------------------------
    def _validate_new(self, edge: LineageEdge, *, allow_existing_child: bool = False) -> None:
        if not allow_existing_child and self.incoming(edge.child_revision) is not None:
            raise LineageError(
                code="LINEAGE.DUPLICATE_INCOMING",
                what=f"修订 {edge.child_revision} 已有入边",
                how_to_fix="每个修订恰好一条入边；若要改正，请用 supersede() 追加取代边",
            )

        if edge.parent_revision is None and edge.parent_entity is not None:
            raise LineageError(
                code="LINEAGE.INITIAL_WITH_PARENT",
                what="初始修订（无上游）不得声明父实体",
                how_to_fix="初始修订应写 parent_revision=None 且 parent_entity=None",
            )

        if edge.operation is OperationKind.UNATTRIBUTED:
            # 两种合法情形，语义不同，必须分开：
            #   (a) 同一实体内的无法描述的操作 —— 父实体明确（几何确实变了，
            #       但说不清是哪一类操作）。操作未知就无法排除拓扑改变，
            #       等价性度量也不能替声明作保，所以下游必须重算（invalidate() 判 INVALID）；
            #   (b) 完全无法归属 —— 无父实体（例如换了一版完全不同的分割，
            #       无法声称任何对应关系），下游必须重算。
            # 两者都**必须**写明理由，否则无法审计。
            if edge.parent_entity is not None:
                if not edge.notes.strip():
                    raise LineageError(
                        code="LINEAGE.UNATTRIBUTED_NEEDS_NOTES",
                        what="UNATTRIBUTED 且声称同一实体时，必须写明为何无法给出具体操作类型",
                        how_to_fix="在 notes 中说明：几何确实属于同一实体，"
                                   "但无法归类为 REFINE/REPLACE_EQUIVALENT 等具体操作",
                    )
            elif edge.parent_revision is not None:
                raise LineageError(
                    code="LINEAGE.UNATTRIBUTED_WITH_PARENT_REVISION",
                    what="UNATTRIBUTED 且无父实体时，不得同时声明上游修订",
                    how_to_fix="要么给出父实体（表示同一实体内的未知操作），"
                               "要么把 parent_revision 置空（表示完全无法归属的新实体）",
                )

        if edge.source is DeclarationSource.INFERRED and edge.operation in (
            OperationKind.REFINE,
            OperationKind.REPLACE_EQUIVALENT,
        ):
            raise LineageError(
                code="LINEAGE.INFERRED_CANNOT_ASSERT_IDENTITY",
                what=f"推断来源（INFERRED）不得声明 {edge.operation.value} 这类同一性断言",
                how_to_fix="推断只能用 UNATTRIBUTED；若要断言同一性，"
                           "必须由算法确定性声明或人工明确声明",
            )

        if edge.operation in (OperationKind.SPLIT, OperationKind.MERGE):
            if not edge.notes.strip():
                raise LineageError(
                    code="LINEAGE.TOPOLOGY_CHANGE_NEEDS_NOTES",
                    what=f"{edge.operation.value} 改变了拓扑，必须写明涉及的实体清单与理由",
                    how_to_fix="在 notes 中列出参与拆分的父/子实体或参与合并的各实体",
                )

    def validate_integrity(self) -> list[str]:
        """全量一致性检查，返回问题清单（空表示通过）。"""
        problems: list[str] = []
        seen: dict[RevisionId, LineageEdge] = {}
        for e in self.active_edges():
            if e.child_revision in seen:
                problems.append(f"修订 {e.child_revision} 有多条入边")
            seen[e.child_revision] = e
        for e in self.active_edges():
            if e.parent_revision is None:
                continue          # 初始修订，无上游
            if e.parent_entity is None:
                problems.append(
                    f"修订 {e.child_revision} 有上游修订却无父实体（非法的悬空边）"
                )
                continue
            if self.incoming(e.parent_revision) is None:
                problems.append(
                    f"修订 {e.child_revision} 的上游 {e.parent_revision} 没有入边"
                    "（谱系链断裂）"
                )
        return problems

    def __len__(self) -> int:
        return len(self.active_edges())

    def all_edges_including_superseded(self) -> Iterable[LineageEdge]:
        return tuple(self._edges)

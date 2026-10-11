"""vessel-identity —— 血管实体身份与失效传播的最小语义库。

本库回答两个问题，且**刻意分开**：

1. **身份**（lineage 模块）：这条血管是不是原来那条？
   → 由**显式谱系**决定，确定性判定，不由几何推断。

2. **可复用性**（equivalence + invalidation 模块）：上游改了之后，
   这份下游产物还能不能用？
   → 由谱系边类型作确定性判定，几何等价性作二次确认；
     判不了就标 STALE_UNCERTAIN，禁止静默当 current。

设计立场：身份由声明承载，等价性只是护栏。
依据：血管树匹配文献明确拒绝靠分叉点/几何恢复身份；OpenLineage #4127
显示"标识派生规则漏一处 = 血缘静默丢失"而非报错。

只依赖标准库，无第三方运行时依赖。
"""

from .equivalence import (
    BranchGeometry,
    EquivalenceMetrics,
    EquivalenceThresholds,
    EquivalenceVerdict,
    Verdict,
    compare_branches,
)
from .ids import EntityId, RevisionId
from .invalidation import (
    ArtifactDependency,
    InvalidationResult,
    Validity,
    invalidate,
    transitive_closure,
)
from .lineage import (
    DeclarationSource,
    LineageEdge,
    LineageError,
    LineageStore,
    OperationKind,
)

__version__ = "0.2.0"

__all__ = [
    "EntityId",
    "RevisionId",
    "OperationKind",
    "DeclarationSource",
    "LineageEdge",
    "LineageError",
    "LineageStore",
    "Verdict",
    "EquivalenceThresholds",
    "BranchGeometry",
    "EquivalenceMetrics",
    "EquivalenceVerdict",
    "compare_branches",
    "Validity",
    "ArtifactDependency",
    "InvalidationResult",
    "invalidate",
    "transitive_closure",
    "__version__",
]

# vessel-identity

**血管实体身份与失效传播的最小语义库。**

回答两个问题，且刻意分开：

| 问题 | 模块 | 判定方式 |
|---|---|---|
| 这条血管**是不是**原来那条？ | `lineage` | **显式谱系**，确定性判定 |
| 上游改了之后，这份下游产物**还能不能用**？ | `equivalence` + `invalidation` | 谱系边类型作确定性判定，几何等价性作二次确认 |

只依赖 Python 标准库，无第三方运行时依赖。

## 为什么需要它

血流仿真流水线里，几何会被反复重算：重分割、重提中心线、重划分网格。此时有两个问题现有工具都答不上来：

1. **身份**：重算之后，"这条血管"还是原来那条吗？
2. **失效**：上游改了之后，哪些下游结果必须重算、哪些还能用？

同类工具与标准的现状：

- **SimVascular 家族**用局部字符串或位置序号作身份（svZeroDSolver 的 `branch0_seg0`、svOneDSolver 的 SEGMENT 位置序号）。中心线重提导致分支重新编号时，旧配置会**静默指向错误的血管**。
- **VMTK** 把分支身份挂在几何上（`GroupIds` / `CenterlineIds` / `TractIds` / `Blanking`），文件自描述，但整型 ID 只在单次抽取的上下文内有意义。
- **3D Slicer MRML** 的节点 ID 是场景局部的，上游文档明写跨场景复制会 "unsynchronize"。
- **W3C PROV** 可以表达"同一实体的多个化身"，但判定权在用户手上，且明确规定 `wasDerivedFrom` **不传递**——连沿依赖链传播失效都不承诺。
- **DVC** 用文件级内容哈希，而该哈希是 OS 敏感的（[iterative/dvc#6314](https://github.com/iterative/dvc/issues/6314)：同一文件哈希相同而 size 不同）。对几何而言方向相反：重分割后要么哈希全变，要么哈希巧合相同却给不出"同一根血管"的结论。
- **OpenLineage** 有最完整的列级血缘规范，但 [OpenLineage#4127](https://github.com/OpenLineage/OpenLineage/issues/4127) 显示：标识派生规则漏一处，血缘不是报错而是**静默丢边**。
- **工作流引擎**（Snakemake / Nextflow）能回答"改了没有"，不能回答"改的是不是同一件事"；且未声明的依赖会导致静默陈旧复用。

**血管树匹配文献**明确拒绝了靠分叉点/几何恢复身份（假分叉、断裂中心线、同一批 50 例中出现 28 种标签排布）。这直接决定了本库的设计立场。

## 设计立场

> **身份由声明承载，等价性只是护栏。**

1. 新几何产生时，身份来自产生它的操作的**显式声明**，不由几何推断。
2. 几何比对只用于回答"下游能不能复用"，且输出**三值**（等价 / 不等价 / 不可判定）。
3. **宁可重算，不可假装确定**：`UNCERTAIN` 不自动允许复用。
4. 允许说"不知道"：操作方无法声明对应关系时标 `UNATTRIBUTED`，而不是默认猜成 1:1。
5. 推断来源（`INFERRED`）被禁止声明同一性——因为它正是不可靠的那种判断。

## 安装

```bash
pip install -e .
```

## 用法

```python
from vessel_identity import (
    EntityId, RevisionId, LineageStore, LineageEdge,
    OperationKind, DeclarationSource,
)

entity = EntityId.new()
store = LineageStore()

# 初始修订
r0 = RevisionId.new()
store.append(LineageEdge(None, None, entity, r0, OperationKind.REFINE,
                         DeclarationSource.ALGORITHM, "2026-10-05T10:00:00Z", "alice",
                         notes="初始分割与中心线"))

# 重提中心线：实体不变，修订递增
r1 = RevisionId.new()
store.append(LineageEdge(entity, r0, entity, r1, OperationKind.REFINE,
                         DeclarationSource.ALGORITHM, "2026-10-05T10:05:00Z", "alice"))

assert store.root_of(r1) == r0          # 身份链的根不变
assert store.entities_of(r1) == {entity}
```

失效传播：

```python
from vessel_identity import (
    ArtifactDependency, EquivalenceThresholds, compare_branches, invalidate,
)

thresholds = EquivalenceThresholds(
    max_length_rel_diff=0.02,
    max_radius_profile_distance=0.05,
    max_centerline_distance_rel=0.02,
    ambiguity_margin=0.10,
    calibration_status="PENDING_CALIBRATION",
)

result = invalidate(
    ArtifactDependency("wss_map", depends_on=frozenset({r0})),
    delta_edges=[edge_for_r0_to_r1],
    equivalence_pairs={(r0, r1): (geom_before, geom_after)},
    thresholds=thresholds,
)

print(result.validity)          # INVALID | REUSABLE | STALE_UNCERTAIN
print(result.may_serve_as_current)
for reason in result.reasons:
    print(reason)               # 理由可回放
```

## 核心概念

| 概念 | 含义 |
|---|---|
| `EntityId` | 血管实体标识。不透明、跨重算不变、不含语义 |
| `RevisionId` | 几何修订标识。每次几何语义改变产生一个 |
| `LineageEdge` | 一条显式谱系边；append-only；改正只能追加取代边 |
| `OperationKind` | `REFINE` / `REPLACE_EQUIVALENT` / `SPLIT` / `MERGE` / `DELETE_PART` / `RENUMBER` / `UNATTRIBUTED` |
| `DeclarationSource` | `ALGORITHM` / `HUMAN` / `IMPORT` / `INFERRED` |
| `EquivalenceThresholds` | 判定阈值；**必须声明标定状态**，声称已标定必须给出标定依据 |
| `Validity` | `INVALID` / `REUSABLE` / `STALE_UNCERTAIN` |

## 判定顺序（最重要的设计决定）

```
上游修订 Δ
   │
   ├─ 第 1 步：看谱系边的类型（确定性）
   │     SPLIT / MERGE / DELETE_PART / UNATTRIBUTED  → INVALID（立即，无需度量）
   │     REFINE / REPLACE_EQUIVALENT / RENUMBER      → 进入第 2 步
   │
   └─ 第 2 步：几何等价性（度量，三值）
         EQUIVALENT      → REUSABLE
         NON_EQUIVALENT  → INVALID
         UNCERTAIN       → STALE_UNCERTAIN（交人工裁决，禁止当 current）
```

**为什么不能反过来**：等价性是度量，会失败、会有灰区；身份与拓扑语义是声明，应当是确定的。让度量推翻声明，等于把不可靠的推断升级成事实。

## 边界与已知限制

- **不做几何处理**：只接受已经算好的中心线与半径剖面，不做分割、重建或网格。
- **不做单位换算**：所有几何的坐标与半径必须在调用前统一单位（本库假设一致，不校验坐标系统）。
- **等价性阈值尚未标定**：默认阈值仅为占位，必须标 `PENDING_CALIBRATION`；标定方法与数据集见下方。
- **不处理持久化**：`LineageStore` 是内存结构，持久化由使用方负责（这是刻意的——避免绑定存储后端）。
- **不判定身份**：这是设计立场，不是缺陷。

## 阈值标定（尚未完成）

等价性阈值需要在标定集上确定。计划的方法：

1. 取同一例 CTA 的多次处理结果（不同分割版本、不同中心线参数），构成"已知同源"几何对；
2. 对同一几何施加受控扰动（1% / 5% / 10% 半径变化、亚像素位移、重采样），构成"已知异源"对；
3. 在标定集上取判定器的工作点，报告判据曲线与混淆矩阵。

标定完成前，本库的等价性判定只能用于**保守方向**（倾向重算），不得用于声称"无需重算"。

## 测试

```bash
python -m pytest -q
```

测试覆盖：身份持久性、append-only 谱系、拆分/合并谱系、诚实性约束（不得猜测、不得由推断断言同一性）、等价性三值输出、失效传播的判定顺序、以及边界输入拒绝。

## 许可

MIT。见 `LICENSE`。

## 引用

若本库对您的研究有帮助，请引用见 `CITATION.cff`（待补正式引用信息）。

## 状态

Alpha。接口可能变化，变化会记入 `CHANGELOG.md`。

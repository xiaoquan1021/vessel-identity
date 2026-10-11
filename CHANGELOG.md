# 变更日志

本文件记录所有值得注意的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循 [语义化版本](https://semver.org/)。

## [0.2.0] - 2026-10-11

### 不兼容变更

- `invalidate()` 新增**必填**的仅关键字参数 `current_revisions`（当前全部修订的集合）。缺省或传 `None` 时抛 `TypeError`；元素必须是 `RevisionId`（误传 `EntityId` 会被拒绝）。理由：没有它就发现不了孤立依赖，静默错误复用会成为默认行为。0.x 阶段按语义化版本允许此类变更。

### 修正

- **孤立依赖被判为可复用**（[#1](https://github.com/xiaoquan1021/vessel-identity/issues/1)）：完全无法归属的 `UNATTRIBUTED` 边不带父修订，删除也可能不留边，0.1.0 对这两种情形不可见。现在新增第 0 步：依赖不在 `current_revisions` 中、且本次谱系中没有以它为父的边 → `INVALID`。该缺陷由下游平台在公开病例上的实测发现（114/114 个构件被错误判为可复用）。
- 原测试 `test_unattributed_new_entity_invalidates` 并未调用 `invalidate()`，因此没能暴露上述缺陷；已改写为真实断言。
- `lineage.py` 中关于「有父实体的 `UNATTRIBUTED`」的注释与 `invalidate()` 行为不一致（注释称走等价性验证，实际判 `INVALID`）；以行为为准修正注释。

### 新增

- `InvalidationResult.rebased_dependencies`：经等价性确认为 `REUSABLE` 时给出（旧修订, 新修订）对，供调用方改钉依赖。不改钉时，下一次判定会把旧修订当孤立依赖而判 `INVALID`（保守方向）。
- 本次谱系含多步修订、后继修订本身不在当前集合时，输出 `STALE_UNCERTAIN`，不再只确认第一步就声称可复用。

### 文档

- README：判定顺序补第 0 步；说明 `current_revisions` 为何必填、如何改钉依赖；阈值标定一节改为「阈值须按下游模型类、QoI 与容差标定」，依据是下游平台的实测。

## [0.1.0] - 2026-10-05

### 新增

- `ids`：不透明的实体 ID 与修订 ID（不编码任何语义——带语义的 ID 在下游几何变化时会变成谎言）。
- `lineage`：append-only 显式谱系；`LineageEdge` / `OperationKind` / `DeclarationSource`；拆分/合并的父子追溯；改正通过追加取代边实现。
- `equivalence`：几何等价性验证，输出三值（`EQUIVALENT` / `NON_EQUIVALENT` / `UNCERTAIN`）；阈值必须声明标定状态。
- `invalidation`：失效传播，判定顺序为"先看谱系边类型（确定性）→ 再用等价性二次确认（度量）"。
- 诚实性约束：`INFERRED` 来源不得声明同一性；`UNATTRIBUTED` 必须写明理由；声称已标定必须给出标定依据。

### 已知限制

- 等价性阈值尚未标定（`PENDING_CALIBRATION`），只能用于保守方向。
- 不做几何处理、不做单位换算、不处理持久化。

### 设计说明

本库把"身份"与"等价性"刻意分开：身份由显式谱系承载，等价性只用于验证下游可复用性。
依据见 README「为什么需要它」一节。

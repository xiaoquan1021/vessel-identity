# 变更日志

本文件记录所有值得注意的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循 [语义化版本](https://semver.org/)。

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

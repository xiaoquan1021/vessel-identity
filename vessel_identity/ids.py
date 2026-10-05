"""不透明标识符：实体 ID 与修订 ID。

设计原则（对应单向门 D1）：
- ID 是**不透明**的：不编码解剖位置、分支序号、生成顺序或任何语义。
  理由：任何编码进 ID 的语义，都会在下游几何发生变化时变成谎言。
  真实案例：svZeroDSolver 用 ``branch<N>_seg<M>`` 字符串作身份，
  一旦重提中心线导致分支重新编号，旧配置会静默指向错误的血管。
- 实体 ID 与修订 ID 是**两种不同的东西**，不得互相替代：
  实体是「这条血管」，修订是「它在某一时刻的几何」。
- 生成方式独立于内容：ID 不随几何变化而变化（这正是"持久"的含义）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

PREFIX_ENTITY: Final[str] = "ve"      # vessel entity
PREFIX_REVISION: Final[str] = "vr"    # vessel revision


def _new_opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True, order=True)
class EntityId:
    """血管实体标识。跨重分割/重提中心线/重网格保持不变。"""

    value: str

    def __post_init__(self) -> None:
        _validate(self.value, PREFIX_ENTITY, "EntityId")

    @classmethod
    def new(cls) -> "EntityId":
        return cls(_new_opaque_id(PREFIX_ENTITY))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class RevisionId:
    """几何修订标识。每次几何语义改变产生一个新的修订。"""

    value: str

    def __post_init__(self) -> None:
        _validate(self.value, PREFIX_REVISION, "RevisionId")

    @classmethod
    def new(cls) -> "RevisionId":
        return cls(_new_opaque_id(PREFIX_REVISION))

    def __str__(self) -> str:
        return self.value


def _validate(value: str, prefix: str, kind: str) -> None:
    if not value.startswith(prefix + "_"):
        raise ValueError(
            f"{kind} 必须以 {prefix}_ 开头，实际为 {value!r}。"
            "ID 由本库生成，不要手工构造——手工构造的 ID 会带上语义，"
            "而带语义的 ID 在下游几何变化时会变成谎言。"
        )
    body = value[len(prefix) + 1 :]
    if len(body) != 32:
        raise ValueError(f"{kind} 的主体必须是 32 位十六进制，实际长度 {len(body)}")
    try:
        int(body, 16)
    except ValueError as exc:
        raise ValueError(f"{kind} 的主体必须是十六进制：{body!r}") from exc

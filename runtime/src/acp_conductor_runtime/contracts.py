# SPDX-License-Identifier: Apache-2.0
"""Local operator contracts; no authentication, process or filesystem effects."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}\Z")


def identity(value: object) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class Request:
    task_id: str
    adapter_id: str
    prompt: str
    expected: str
    timeout: float = 30.0
    max_output: int = 4096

    def __post_init__(self):
        if not (
            identity(self.task_id) and identity(self.adapter_id)
            and type(self.prompt) is str and 0 < len(self.prompt.encode("utf-8")) <= 32768
            and type(self.max_output) is int and 0 < self.max_output <= 1048576
            and type(self.expected) is str and self.expected == self.expected.strip()
            and 0 < len(self.expected.encode("utf-8")) <= self.max_output
            and finite(self.timeout) and 0 < self.timeout <= 300
        ):
            raise ValueError("invalid adapter request")

    @property
    def encoded(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Grant:
    request_digest: str
    adapter_id: str
    expires_at: float

    def permits(self, request: Request, now: float) -> bool:
        return (
            self.request_digest == request.digest
            and self.adapter_id == request.adapter_id
            and finite(self.expires_at) and self.expires_at > now
        )


class Adapter(Protocol):
    """Trusted operator code, not arbitrary model-provided commands or an OS sandbox."""
    identity: str

    def execute(self, request: Request, workspace: Path) -> bytes: ...

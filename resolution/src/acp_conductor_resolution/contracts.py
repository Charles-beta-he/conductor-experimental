# SPDX-License-Identifier: Apache-2.0
"""Trusted local successor contracts. These objects do not confer remote authority."""

from dataclasses import dataclass

from acp_conductor_runtime.contracts import Grant, Request

__all__ = ["ExecutionResult", "Grant", "Request", "ResolutionGrant"]


@dataclass(frozen=True, slots=True)
class ResolutionGrant:
    attempt_id: str
    request_digest: str
    evidence_digest: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Trusted adapter attestation AFTER its owned process group is absent.

    A raised exception, including cleanup failure, conveys no closure evidence.
    This is not a model-generated object or cryptographic/sandbox attestation.
    """

    output: bytes | None
    error: str | None
    quiescent: bool = True

# SPDX-License-Identifier: Apache-2.0
# Modified derivative of the runtime Store/process implementation.
# Changes: isolated v2 UNKNOWN-resolution protocol; Store 0.0.3 preserves
# authorized FAILED terminal readback despite later artifact-integrity failure.
# See the distributed PROVENANCE.md for lineage, scope and remaining limitations.
"""Version-isolated local attempt owner; no saved-PID recovery or remote effects."""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from acp_conductor_runtime.contracts import Grant, Request, finite, identity

from .contracts import ExecutionResult, ResolutionGrant


def _encode(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value):
    return hashlib.sha256(_encode(value).encode()).hexdigest()


class Store:
    def __init__(self, root, *, max_starts=8, max_running=1):
        if any(type(v) is not int or v < 1 for v in (max_starts, max_running)):
            raise ValueError("invalid budget")
        self.root = Path(root).resolve()
        # Refuse legacy roots before creating a file, marker, schema or directory.
        if (self.root / "attempts.sqlite3").exists():
            raise ValueError("v1 root cannot be opened by v2")
        self.root.mkdir(parents=True, exist_ok=True)
        if (self.root / "resolution-v2.sqlite3").exists():
            raise ValueError("pre-release candidate root requires explicit archival, not migration")
        self.db = self.root / "attempts-v2.sqlite3"
        with self._tx() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS config(protocol TEXT, root TEXT, starts INTEGER,
                                                running INTEGER);
                CREATE TABLE IF NOT EXISTS attempts(
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, request TEXT NOT NULL,
                    grant TEXT NOT NULL, state TEXT NOT NULL, started INTEGER NOT NULL,
                    token TEXT, artifact_path TEXT, artifact_sha256 TEXT, error TEXT,
                    closure TEXT, resolution TEXT);
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,
                    state TEXT NOT NULL, detail TEXT NOT NULL);
            """)
            conn.execute("BEGIN IMMEDIATE")
            expected = ("unknown-resolution-v2", str(self.root), max_starts, max_running)
            row = conn.execute("SELECT * FROM config").fetchone()
            if row is None:
                conn.execute("INSERT INTO config VALUES (?,?,?,?)", expected)
            elif tuple(row) != expected:
                raise ValueError("root protocol or budget mismatch")
        self.max_starts, self.max_running = max_starts, max_running

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.db, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row(conn, attempt):
        if not identity(attempt):
            raise ValueError("invalid attempt")
        row = conn.execute("SELECT * FROM attempts WHERE id=?", (attempt,)).fetchone()
        if row is None:
            raise KeyError(attempt)
        return dict(row)

    @staticmethod
    def _state(conn, attempt, state, detail=""):
        conn.execute("UPDATE attempts SET state=?,error=? WHERE id=?", (state, detail, attempt))
        conn.execute("INSERT INTO events(id,state,detail) VALUES (?,?,?)", (attempt, state, detail))

    def submit(self, attempt, request, grant):
        if not identity(attempt) or type(request) is not Request:
            raise ValueError("invalid attempt/request")
        if type(grant) is not Grant or not grant.permits(request, time.time()):
            raise PermissionError("execution Grant required")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT digest,grant FROM attempts WHERE id=?", (attempt,)
            ).fetchone()
            if row is not None:
                if row["digest"] != request.digest or row["grant"] != _encode(asdict(grant)):
                    raise ValueError("attempt identity already bound")
                return attempt
            for prior in conn.execute("SELECT request FROM attempts"):
                if json.loads(prior["request"])["task_id"] == request.task_id:
                    raise ValueError("task identity already bound to an attempt")
            conn.execute(
                "INSERT INTO attempts(id,digest,request,grant,state,started) VALUES (?,?,?,?,?,0)",
                (attempt, request.digest, request.encoded, _encode(asdict(grant)), "PREPARED"),
            )
            conn.execute(
                "INSERT INTO events(id,state,detail) VALUES (?,?,'')", (attempt, "PREPARED")
            )
        return attempt

    def query(self, attempt):
        with self._tx() as conn:
            row = self._row(conn, attempt)
        return self._project(row)

    def _project(self, row):
        # B1 local revision: committed resolution establishes terminal failure,
        # not artifact integrity. Retain damaged metadata for audit; artifact()
        # still refuses FAILED. Unresolved/verified artifacts keep normal checks.
        if row["state"] == "FAILED" and row["resolution"] is not None:
            return row
        if row["state"] in {"VERIFIED", "FAILED"} and row["artifact_path"] is not None:
            try:
                self._read(row)
            except (OSError, ValueError):
                row["state"], row["error"] = "UNKNOWN", "artifact_changed"
        return row

    def history(self, attempt):
        with self._tx() as conn:
            self._row(conn, attempt)
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT seq,state,detail FROM events WHERE id=? ORDER BY seq", (attempt,)
                )
            ]

    def inventory(self):
        """Public complete identity inventory for cross-store orphan detection."""
        with self._tx() as conn:
            return tuple(row[0] for row in conn.execute("SELECT id FROM attempts ORDER BY id"))

    def stats(self):
        with self._tx() as conn:
            return dict(
                conn.execute(
                    "SELECT COALESCE(SUM(started),0) AS starts, "
                    "COUNT(CASE WHEN state IN ('RUNNING','UNKNOWN') THEN 1 END) "
                    "AS active FROM attempts"
                ).fetchone()
            )

    def run(self, attempt, adapter):
        with self._tx() as conn:
            row = self._row(conn, attempt)
            if row["state"] != "PREPARED":
                return self._project(row)
            request = Request(**json.loads(row["request"]))
            grant = Grant(**json.loads(row["grant"]))
            if getattr(adapter, "identity", None) != request.adapter_id:
                raise PermissionError("adapter identity mismatch")
            if not callable(getattr(adapter, "execute_authorized", None)):
                raise TypeError("closure-capable authorized adapter required")
            if not grant.permits(request, time.time()):
                raise PermissionError("execution grant expired")
            starts, active = conn.execute(
                "SELECT COALESCE(SUM(started),0), "
                "COUNT(CASE WHEN state IN ('RUNNING','UNKNOWN') THEN 1 END) "
                "FROM attempts"
            ).fetchone()
            if starts >= self.max_starts or active >= self.max_running:
                raise ValueError("execution budget exhausted")
            token = uuid.uuid4().hex
            conn.execute("UPDATE attempts SET started=1,token=? WHERE id=?", (token, attempt))
            self._state(conn, attempt, "RUNNING")
        workspace = self.root / attempt
        try:
            workspace.mkdir(exist_ok=False)
            result = adapter.execute_authorized(request, workspace, grant)
        except BaseException as exc:
            with self._tx() as conn:
                current = self._row(conn, attempt)
                if current["state"] == "RUNNING" and current["token"] == token:
                    self._state(conn, attempt, "UNKNOWN", type(exc).__name__)
            if not isinstance(exc, Exception):
                raise
            return self.query(attempt)
        valid = (
            type(result) is ExecutionResult
            and result.quiescent is True
            and (
                (
                    type(result.output) is bytes
                    and len(result.output) <= request.max_output
                    and result.error is None
                )
                or (
                    result.output is None
                    and type(result.error) is str
                    and result.error in {"timeout", "output_limit", "nonzero_exit"}
                )
            )
        )
        with self._tx() as conn:
            current = self._row(conn, attempt)
            if current["token"] != token or current["state"] not in {"RUNNING", "UNKNOWN"}:
                return self._project(current)
            if not valid:
                if current["state"] == "RUNNING":
                    self._state(conn, attempt, "UNKNOWN", "invalid_execution_result")
            else:
                closed = {
                    "protocol": 2,
                    "token": token,
                    "quiescent": True,
                    "error": result.error,
                    "output_sha256": hashlib.sha256(result.output).hexdigest()
                    if result.output is not None
                    else None,
                }
                conn.execute("UPDATE attempts SET closure=? WHERE id=?", (_encode(closed), attempt))
                if current["state"] == "RUNNING":
                    if result.error:
                        self._state(conn, attempt, "UNKNOWN", result.error)
                    else:
                        target = workspace / "candidate.txt"
                        # State/token check and artifact+row publication share the owner lock.
                        try:
                            with target.open("xb") as stream:
                                stream.write(result.output)
                                stream.flush()
                                os.fsync(stream.fileno())
                            conn.execute(
                                "UPDATE attempts SET artifact_path=?,artifact_sha256=? WHERE id=?",
                                (str(target), closed["output_sha256"], attempt),
                            )
                            self._state(conn, attempt, "CANDIDATE")
                        except OSError:
                            self._state(conn, attempt, "UNKNOWN", "artifact_write_failed")
                # Late result in UNKNOWN: closure only, no artifact or state promotion.
        return self.query(attempt)

    def _read(self, row):
        target = self.root / row["id"] / "candidate.txt"
        if row["artifact_path"] != str(target) or target.is_symlink():
            raise ValueError("artifact identity changed")
        request = Request(**json.loads(row["request"]))
        with target.open("rb") as stream:
            data = stream.read(request.max_output + 1)
        if (
            len(data) > request.max_output
            or hashlib.sha256(data).hexdigest() != row["artifact_sha256"]
        ):
            raise ValueError("artifact changed")
        return data

    def verify(self, attempt):
        with self._tx() as conn:
            row = self._row(conn, attempt)
            if row["state"] not in {"CANDIDATE", "VERIFIED"}:
                return self._project(row)
            try:
                data = self._read(row)
                request = Request(**json.loads(row["request"]))
                good = data.decode("utf-8").strip() == request.expected
            except (OSError, ValueError):
                good = False
            if good:
                if row["state"] == "CANDIDATE":
                    self._state(conn, attempt, "VERIFIED")
            else:
                self._state(conn, attempt, "UNKNOWN", "verification_failed")
        return self.query(attempt)

    def artifact(self, attempt):
        row = self.query(attempt)
        if row["state"] != "VERIFIED":
            raise ValueError("artifact is not freshly verified")
        return self._read(row)

    def cancel(self, attempt):
        with self._tx() as conn:
            row = self._row(conn, attempt)
            if row["state"] == "PREPARED":
                self._state(conn, attempt, "CANCELLED")
            elif row["state"] == "RUNNING":
                self._state(conn, attempt, "UNKNOWN", "cancel_without_stop_proof")
        return self.query(attempt)

    def reconcile(self, attempt):
        with self._tx() as conn:
            if self._row(conn, attempt)["state"] == "RUNNING":
                self._state(conn, attempt, "UNKNOWN", "runner_unconfirmed")
        return self.query(attempt)

    def _evidence(self, conn, attempt):
        row = self._row(conn, attempt)
        if row["state"] != "UNKNOWN":
            raise ValueError("resolution requires persisted UNKNOWN")
        if row["closure"] is None:
            raise ValueError("execution not durably closed")
        sequence = conn.execute(
            "SELECT MAX(seq) FROM events WHERE id=? AND state='UNKNOWN'", (attempt,)
        ).fetchone()[0]
        value = {
            "protocol": 2,
            "attempt_id": attempt,
            "request_digest": row["digest"],
            "adapter_id": json.loads(row["request"])["adapter_id"],
            "call_token": row["token"],
            "unknown_sequence": sequence,
            "closure": json.loads(row["closure"]),
        }
        value["evidence_digest"] = _digest(value)
        return value

    def resolution_evidence(self, attempt):
        with self._tx() as conn:
            return self._evidence(conn, attempt)

    def resolve(self, attempt, grant):
        if type(grant) is not ResolutionGrant or not finite(grant.expires_at):
            raise PermissionError("finite ResolutionGrant required")
        with self._tx() as conn:
            row = self._row(conn, attempt)
            encoded = _encode(asdict(grant))
            if row["resolution"] is not None:
                if row["resolution"] != encoded or grant.attempt_id != attempt:
                    raise PermissionError("resolution grant conflicts with committed grant")
                return row
            # Fresh authority/evidence validation AFTER transaction lock acquisition.
            evidence = self._evidence(conn, attempt)
            if (
                grant.attempt_id != attempt
                or grant.request_digest != row["digest"]
                or grant.evidence_digest != evidence["evidence_digest"]
                or grant.expires_at <= time.time()
            ):
                raise PermissionError("resolution grant binding or expiry invalid")
            conn.execute("UPDATE attempts SET resolution=? WHERE id=?", (encoded, attempt))
            self._state(conn, attempt, "FAILED", "authorized_closed_execution_resolution")
            return self._row(conn, attempt)

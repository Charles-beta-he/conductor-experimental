# SPDX-License-Identifier: Apache-2.0
"""Private local adapter-attempt persistence; never writes ACP platform lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .contracts import Adapter, Grant, Request, identity


class Store:
    def __init__(self, root: Path, *, max_starts: int = 3, max_running: int = 1):
        if not all(type(n) is int and 0 < n <= 10000 for n in (max_starts, max_running)):
            raise ValueError("invalid store budget")
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        meta = self.root.lstat()
        if not stat.S_ISDIR(meta.st_mode) or meta.st_mode & 0o077:
            raise ValueError("store root must be a private real directory")
        self.db = self.root / "attempts.sqlite3"
        try:
            fd = os.open(self.db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            meta = self.db.lstat()
            if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
                raise ValueError("store database must be a private regular file")
        else:
            os.close(fd)
        with self._transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS config (singleton INTEGER PRIMARY KEY "
                         "CHECK(singleton=1), max_starts INTEGER, max_running INTEGER)")
            conn.execute("INSERT OR IGNORE INTO config VALUES (1,?,?)", (max_starts, max_running))
            cfg = conn.execute("SELECT max_starts,max_running FROM config").fetchone()
            if tuple(cfg) != (max_starts, max_running):
                raise ValueError("persisted store budget mismatch")
            conn.execute("""CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL,
                task_id TEXT UNIQUE NOT NULL, request TEXT NOT NULL, digest TEXT NOT NULL,
                adapter_id TEXT NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL,
                started INTEGER NOT NULL DEFAULT 0, artifact_path TEXT,
                artifact_digest TEXT, reason TEXT NOT NULL DEFAULT '')""")
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, attempt_id TEXT, state TEXT, reason TEXT)""")

    @contextmanager
    def _transaction(self):
        conn = sqlite3.connect(self.db, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row(conn, attempt_id):
        row = conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            raise KeyError("unknown attempt")
        return dict(row)

    @staticmethod
    def _event(conn, attempt_id, state, reason):
        conn.execute("INSERT INTO events(attempt_id,state,reason) VALUES (?,?,?)",
                     (attempt_id, state, reason))

    def submit(self, key: str, request: Request, grant: Grant | None) -> str:
        if not identity(key) or type(request) is not Request:
            raise ValueError("invalid submit")
        if type(grant) is not Grant or not grant.permits(request, time.time()):
            raise PermissionError("missing, mismatched or expired local grant")
        with self._transaction() as conn:
            old = conn.execute("SELECT attempt_id,digest FROM attempts WHERE key=?", (key,)).fetchone()
            if old:
                if old["digest"] != request.digest:
                    raise ValueError("idempotency conflict")
                return old["attempt_id"]
            attempt_id = uuid.uuid4().hex
            try:
                conn.execute("""INSERT INTO attempts
                    (attempt_id,key,task_id,request,digest,adapter_id,expires,state)
                    VALUES (?,?,?,?,?,?,?,'PREPARED')""",
                    (attempt_id, key, request.task_id, request.encoded, request.digest,
                     request.adapter_id, grant.expires_at))
            except sqlite3.IntegrityError as exc:
                raise ValueError("task already has an attempt; no implicit replay") from exc
            self._event(conn, attempt_id, "PREPARED", "local grant bound")
        return attempt_id

    def query(self, attempt_id: str) -> dict:
        with self._transaction() as conn:
            row = self._row(conn, attempt_id)
        if row["state"] == "VERIFIED":
            try:
                self._read_artifact(row)
            except (OSError, ValueError):
                # Read-only conservative projection, not a hidden lifecycle write.
                row["stored_state"] = row["state"]
                row["state"] = "UNKNOWN"
                row["reason"] = "verified artifact is no longer available unchanged"
        return row

    def _transition(self, attempt_id, expected_states, state, reason, **fields):
        if set(fields) - {"artifact_path", "artifact_digest"}:
            raise ValueError("invalid evidence fields")
        with self._transaction() as conn:
            row = self._row(conn, attempt_id)
            if row["state"] in expected_states:
                values = {"state": state, "reason": reason, **fields}
                conn.execute("UPDATE attempts SET " + ",".join(f"{k}=?" for k in values)
                             + " WHERE attempt_id=?", (*values.values(), attempt_id))
                self._event(conn, attempt_id, state, reason)
        return self.query(attempt_id)

    def run(self, attempt_id: str, adapter: Adapter) -> dict:
        with self._transaction() as conn:
            row = self._row(conn, attempt_id)
            if row["adapter_id"] != adapter.identity:
                raise ValueError("wrong adapter identity")
            if row["state"] != "PREPARED":
                claimed = False
            else:
                if row["expires"] <= time.time():
                    raise PermissionError("local execution grant expired")
                cfg = conn.execute("SELECT * FROM config").fetchone()
                count = conn.execute("SELECT SUM(started) FROM attempts").fetchone()[0] or 0
                active = conn.execute("SELECT COUNT(*) FROM attempts WHERE state "
                                      "IN ('RUNNING','UNKNOWN')").fetchone()[0]
                if count >= cfg["max_starts"] or active >= cfg["max_running"]:
                    raise ValueError("persisted start/concurrency budget exhausted")
                conn.execute("UPDATE attempts SET state='RUNNING',started=1 WHERE attempt_id=?",
                             (attempt_id,))
                self._event(conn, attempt_id, "RUNNING", "exclusive durable claim before call")
                claimed = True
        if not claimed:
            return self.query(attempt_id)
        try:
            request = Request(**json.loads(row["request"]))
            workspace = self.root / attempt_id
            workspace.mkdir(mode=0o700)
            grant = Grant(row["digest"], row["adapter_id"], row["expires"])
            if not grant.permits(request, time.time()):
                raise PermissionError("local execution grant expired before adapter call")
            authorized = getattr(adapter, "execute_authorized", None)
            output = (authorized(request, workspace, grant) if authorized is not None
                      else adapter.execute(request, workspace))
            if type(output) is not bytes or not 0 < len(output) <= request.max_output:
                raise ValueError("invalid output size/type")
            if not output.decode("utf-8").strip():
                raise ValueError("empty output")
            path = workspace / "result.txt"
            pending = workspace / "result.pending"
            fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(output)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)
            directory = os.open(workspace, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return self._transition(attempt_id, {"RUNNING"}, "CANDIDATE", "adapter returned bytes",
                                    artifact_path=str(path),
                                    artifact_digest=hashlib.sha256(output).hexdigest())
        except Exception as exc:  # noqa: BLE001 -- adapter boundary must conservatively retain UNKNOWN
            # Exception details may contain private provider data; persist only its type.
            return self._transition(attempt_id, {"RUNNING"}, "UNKNOWN", type(exc).__name__)
        except BaseException:
            self._transition(attempt_id, {"RUNNING"}, "UNKNOWN", "interrupted")
            raise

    def _read_artifact(self, row) -> bytes:
        expected = self.root / row["attempt_id"] / "result.txt"
        if row["artifact_path"] != str(expected) or expected.parent.is_symlink():
            raise ValueError("artifact identity/path mismatch")
        request = Request(**json.loads(row["request"]))
        if request.digest != row["digest"] or request.adapter_id != row["adapter_id"]:
            raise ValueError("request evidence binding mismatch")
        fd = os.open(expected, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("artifact is not a regular file")
            data = stream.read(request.max_output + 1)
        if (not 0 < len(data) <= request.max_output
                or hashlib.sha256(data).hexdigest() != row["artifact_digest"]):
            raise ValueError("artifact digest/size mismatch")
        return data

    def artifact(self, attempt_id: str) -> bytes:
        row = self.query(attempt_id)
        if row["state"] not in {"CANDIDATE", "VERIFIED"}:
            raise ValueError("no usable candidate artifact")
        return self._read_artifact(row)

    def verify(self, attempt_id: str) -> dict:
        row = self.query(attempt_id)
        if row["state"] not in {"CANDIDATE", "VERIFIED"}:
            return row
        try:
            data = self._read_artifact(row)
            request = Request(**json.loads(row["request"]))
            if data.decode("utf-8").strip() != request.expected:
                raise ValueError("expected text mismatch")
        except (ValueError, OSError):
            return self._transition(attempt_id, {"CANDIDATE", "VERIFIED"}, "UNKNOWN",
                                    "fresh independent readback rejected")
        return self._transition(attempt_id, {"CANDIDATE", "VERIFIED"}, "VERIFIED",
                                "fresh digest plus expected-text match; local observation only")

    def reconcile(self, attempt_id: str) -> dict:
        return self._transition(attempt_id, {"RUNNING"}, "UNKNOWN",
                                "owner reconciliation requested; no process adopted or replayed")

    def cancel(self, attempt_id: str) -> dict:
        with self._transaction() as conn:
            row = self._row(conn, attempt_id)
            if row["state"] in {"PREPARED", "RUNNING"}:
                state = "CANCELLED" if row["state"] == "PREPARED" else "UNKNOWN"
                conn.execute("UPDATE attempts SET state=?,reason=? WHERE attempt_id=?",
                             (state, "cancel request; no external termination claim", attempt_id))
                self._event(conn, attempt_id, state, "cancel request")
        return self.query(attempt_id)

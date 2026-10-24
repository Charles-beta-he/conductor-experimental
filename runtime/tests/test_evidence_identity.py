"""Cross-attempt metadata rejection and terminal-race evidence."""
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from acp_conductor_runtime import Grant, Request, Store


class Local:
    identity = "test:identity-v1"
    calls = 0

    def execute(self, request, workspace):
        self.calls += 1
        return b"OK"


def test_foreign_attempt_artifact_metadata_cannot_verify(tmp_path):
    store = Store(tmp_path / "state")
    adapter = Local()
    attempts = []
    for name in ("one", "two"):
        req = Request(name, adapter.identity, "Return OK", "OK")
        a = store.submit(name, req, Grant(req.digest, req.adapter_id, time.time() + 60))
        store.run(a, adapter)
        attempts.append(a)
    foreign = store.query(attempts[1])
    # Inject a cross-attempt receipt at the evidence seam, not an alternate public setter.
    with sqlite3.connect(store.db) as conn:
        conn.execute("UPDATE attempts SET artifact_path=?, artifact_digest=? WHERE attempt_id=?",
                     (foreign["artifact_path"], foreign["artifact_digest"], attempts[0]))
    assert Path(foreign["artifact_path"]).read_bytes() == b"OK"
    assert store.verify(attempts[0])["state"] == "UNKNOWN"
    assert store.verify(attempts[1])["state"] == "VERIFIED"


def test_cancel_start_race_never_replays_or_verifies_cancelled(tmp_path):
    for i in range(12):
        store = Store(tmp_path / str(i))
        adapter = Local()
        req = Request("task", adapter.identity, "Return OK", "OK")
        a = store.submit("key", req, Grant(req.digest, req.adapter_id, time.time() + 60))
        with ThreadPoolExecutor(2) as pool:
            run = pool.submit(store.run, a, adapter)
            cancel = pool.submit(store.cancel, a)
            run.result()
            cancel.result()
        before = adapter.calls
        row = store.run(a, adapter)
        assert adapter.calls == before <= 1
        assert row["state"] in {"CANCELLED", "UNKNOWN", "CANDIDATE"}
        if row["state"] == "CANCELLED":
            assert before == 0
        if row["state"] in {"CANCELLED", "UNKNOWN"}:
            assert store.verify(a)["state"] == row["state"]

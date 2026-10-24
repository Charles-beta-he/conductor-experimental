"""M2 adapter-attempt acceptance; canonical requirements: ../../SPEC.md."""
import hashlib
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from acp_conductor_runtime import Grant, Request, Store, SubprocessAdapter


class CountingAdapter:
    identity = "test:count-v1"

    def __init__(self, result=b"OK"):
        self.calls = 0
        self.result = result

    def execute(self, request, workspace):
        self.calls += 1
        return self.result


def request(adapter=None, **kw):
    return Request(task_id=kw.pop("task_id", "task"),
                   adapter_id=(adapter or CountingAdapter()).identity,
                   prompt=kw.pop("prompt", "Return OK"), expected=kw.pop("expected", "OK"), **kw)


def grant(req, **kw):
    return Grant(req.digest, req.adapter_id, kw.get("expires_at", time.time() + 120))


def prepared(tmp_path, adapter=None, **kw):
    adapter = adapter or CountingAdapter()
    req = request(adapter)
    store = Store(tmp_path / "state", **kw)
    attempt = store.submit("key", req, grant(req))
    return store, adapter, attempt


def test_candidate_requires_fresh_verifier_and_survives_reopen(tmp_path):
    store, adapter, attempt = prepared(tmp_path)
    assert store.query(attempt)["state"] == "PREPARED"
    assert store.run(attempt, adapter)["state"] == "CANDIDATE"
    assert store.query(attempt)["state"] != "VERIFIED"
    assert store.verify(attempt)["state"] == "VERIFIED"
    reopened = Store(tmp_path / "state")
    assert reopened.query(attempt)["state"] == "VERIFIED"
    assert reopened.artifact(attempt) == b"OK"
    assert reopened.run(attempt, adapter)["state"] == "VERIFIED"
    assert adapter.calls == 1


@pytest.mark.parametrize("which", ["absent", "expired", "digest", "adapter"])
def test_grant_rejects_before_call(tmp_path, which):
    adapter = CountingAdapter()
    req = request(adapter)
    g = {"absent": None, "expired": grant(req, expires_at=time.time() - 10),
         "digest": Grant("0" * 64, req.adapter_id, time.time() + 60),
         "adapter": Grant(req.digest, "other:adapter", time.time() + 60)}[which]
    with pytest.raises((ValueError, PermissionError)):
        Store(tmp_path / "state").submit("key", req, g)
    assert adapter.calls == 0


def test_grant_rechecked_at_execution(tmp_path):
    adapter = CountingAdapter()
    req = request(adapter)
    store = Store(tmp_path / "state")
    attempt = store.submit("key", req, grant(req, expires_at=time.time() + .05))
    time.sleep(.07)
    with pytest.raises(PermissionError):
        store.run(attempt, adapter)
    assert store.query(attempt)["state"] == "PREPARED"
    assert adapter.calls == 0


def test_idempotency_conflict_and_racing_submit(tmp_path):
    store = Store(tmp_path / "state")
    req = request()
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(lambda _: store.submit("key", req, grant(req)), range(12)))
    assert len(set(ids)) == 1
    other = request(prompt="different")
    with pytest.raises(ValueError):
        store.submit("key", other, grant(other))


def test_racing_run_calls_adapter_only_once(tmp_path):
    store, adapter, attempt = prepared(tmp_path)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: store.run(attempt, adapter), range(12)))
    assert adapter.calls == 1
    assert store.query(attempt)["state"] == "CANDIDATE"


def test_persisted_budget_cannot_reset_on_reopen(tmp_path):
    store, adapter, attempt = prepared(tmp_path, max_starts=1)
    store.run(attempt, adapter)
    req = request(adapter, task_id="other")
    other = store.submit("other", req, grant(req))
    reopened = Store(tmp_path / "state", max_starts=1)
    with pytest.raises(ValueError):
        reopened.run(other, adapter)
    assert adapter.calls == 1
    with pytest.raises(ValueError):
        Store(tmp_path / "state", max_starts=99)


@pytest.mark.parametrize("mutation", ["corrupt", "missing", "symlink", "wrong_expected"])
def test_bad_artifact_never_verifies(tmp_path, mutation):
    store, adapter, attempt = prepared(tmp_path)
    row = store.run(attempt, adapter)
    target = Path(row["artifact_path"])
    if mutation == "corrupt":
        target.write_bytes(b"BAD")
    elif mutation == "missing":
        target.unlink()
    elif mutation == "symlink":
        other = tmp_path / "other"
        other.write_bytes(b"OK")
        target.unlink()
        target.symlink_to(other)
    else:
        # Correct execution digest is necessary, not sufficient for semantic matching.
        adapter.result = b"WRONG"
        store2 = Store(tmp_path / "other-state")
        req = request(adapter)
        attempt = store2.submit("key", req, grant(req))
        store2.run(attempt, adapter)
        store = store2
    assert store.verify(attempt)["state"] != "VERIFIED"


def test_verified_artifact_is_rechecked_not_stale_trust(tmp_path):
    store, adapter, attempt = prepared(tmp_path)
    row = store.run(attempt, adapter)
    store.verify(attempt)
    Path(row["artifact_path"]).write_bytes(b"BAD")
    with pytest.raises(ValueError):
        store.artifact(attempt)
    assert store.verify(attempt)["state"] != "VERIFIED"


@pytest.mark.parametrize("result", [b"", b"\xff", b"A" * 4097])
def test_invalid_result_is_not_candidate(tmp_path, result):
    store, adapter, attempt = prepared(tmp_path, CountingAdapter(result))
    assert store.run(attempt, adapter)["state"] == "UNKNOWN"
    assert store.run(attempt, adapter)["state"] == "UNKNOWN"
    assert adapter.calls == 1


def test_cancel_prepared_and_cancel_uncertain_are_different(tmp_path):
    store, adapter, attempt = prepared(tmp_path)
    assert store.cancel(attempt)["state"] == "CANCELLED"
    assert store.run(attempt, adapter)["state"] == "CANCELLED"
    assert adapter.calls == 0


def test_reconcile_stops_late_result_promotion(tmp_path):
    store, adapter, attempt = prepared(tmp_path)
    def uncertain(req, workspace):
        assert store.query(attempt)["state"] == "RUNNING"
        assert store.cancel(attempt)["state"] == "UNKNOWN"
        return b"OK"
    adapter.execute = uncertain
    assert store.run(attempt, adapter)["state"] == "UNKNOWN"
    assert store.verify(attempt)["state"] == "UNKNOWN"


def test_wrong_adapter_rejected_without_claim(tmp_path):
    store, adapter, attempt = prepared(tmp_path)
    adapter.identity = "wrong:adapter"
    with pytest.raises(ValueError):
        store.run(attempt, adapter)
    assert store.query(attempt)["state"] == "PREPARED"
    assert adapter.calls == 0


@pytest.mark.parametrize("code", ["print('OK')", "import sys;sys.stdout.write('OK')"])
def test_real_python_subprocess_adapter(tmp_path, code):
    adapter = SubprocessAdapter((sys.executable, "-c", code))
    req = request(adapter)
    store = Store(tmp_path / "state")
    attempt = store.submit("key", req, grant(req))
    assert store.run(attempt, adapter)["state"] == "CANDIDATE"
    assert store.verify(attempt)["state"] == "VERIFIED"


@pytest.mark.parametrize("code", ["raise SystemExit(1)",
                                    "import time;time.sleep(10)",
                                    "print('A'*200000)"])
def test_subprocess_failure_timeout_and_output_cap(tmp_path, code):
    adapter = SubprocessAdapter((sys.executable, "-c", code))
    req = request(adapter, timeout=.15)
    store = Store(tmp_path / "state")
    attempt = store.submit("key", req, grant(req))
    start = time.monotonic()
    assert store.run(attempt, adapter)["state"] == "UNKNOWN"
    assert time.monotonic() - start < 4
    assert store.run(attempt, adapter)["state"] == "UNKNOWN"


def test_real_process_kill_reopen_and_no_replay(tmp_path):
    """Kill interpreter after adapter effect; a fresh interpreter must reconcile."""
    source = Path(__file__).resolve().parents[1] / "src"
    script = """
import os, signal, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from acp_conductor_runtime import Store, Request, Grant
class Crash:
 identity='test:crash-v1'
 def execute(self, request, workspace):
  Path(sys.argv[3]).write_text('one effect')
  os.kill(os.getpid(), signal.SIGKILL)
s=Store(Path(sys.argv[2]))
r=Request('task','test:crash-v1','Return OK','OK')
a=s.submit('key',r,Grant(r.digest,r.adapter_id,time.time()+120))
print(a,flush=True)
s.run(a,Crash())
"""
    effect = tmp_path / "effect"
    proc = subprocess.run([sys.executable, "-c", script, str(source),
                           str(tmp_path / "state"), str(effect)], check=False, capture_output=True,
                          timeout=10)
    assert proc.returncode == -signal.SIGKILL, proc.stderr
    attempt = proc.stdout.decode().strip()
    assert effect.read_text() == "one effect"
    reopened = Store(tmp_path / "state")
    assert reopened.query(attempt)["state"] == "RUNNING"
    assert reopened.reconcile(attempt)["state"] == "UNKNOWN"
    class MustNotRun:
        identity = "test:crash-v1"
        def execute(self, *args):
            raise AssertionError("blind replay")
    assert reopened.run(attempt, MustNotRun())["state"] == "UNKNOWN"
    assert effect.read_text() == "one effect"


@pytest.mark.parametrize("kw", [{"timeout": float("inf")}, {"timeout": 0},
                                  {"max_output": True}, {"max_output": 0},
                                  {"prompt": ""}, {"expected": ""},
                                  {"task_id": "../escape"}])
def test_invalid_request_rejected(kw):
    with pytest.raises(ValueError):
        request(**kw)


def test_persisted_concurrency_limit(tmp_path):
    entered, leave = threading.Event(), threading.Event()
    class Blocking(CountingAdapter):
        def execute(self, request, workspace):
            self.calls += 1
            entered.set()
            assert leave.wait(5)
            return b"OK"
    adapter = Blocking()
    store, _, attempt = prepared(tmp_path, adapter)
    req = request(adapter, task_id="other")
    other = store.submit("other", req, grant(req))
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(store.run, attempt, adapter)
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError):
                Store(tmp_path / "state").run(other, adapter)
            assert store.query(other)["state"] == "PREPARED"
        finally:
            leave.set()
        assert running.result()["state"] == "CANDIDATE"
    assert adapter.calls == 1


def test_unknown_keeps_slot_and_new_key_cannot_replay_task(tmp_path):
    store, adapter, attempt = prepared(tmp_path)
    def fail(request, workspace):
        raise RuntimeError("uncertain")
    adapter.execute = fail
    assert store.run(attempt, adapter)["state"] == "UNKNOWN"
    req = request(adapter)
    with pytest.raises(ValueError):
        store.submit("new-key", req, grant(req))
    other = request(adapter, task_id="other")
    b = store.submit("other", other, grant(other))
    with pytest.raises(ValueError):
        store.run(b, adapter)
    assert store.query(b)["state"] == "PREPARED"


def test_adapter_code_drift_rejected(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text("print('OK')")
    adapter = SubprocessAdapter((sys.executable, str(script)), pinned_files=(script,))
    req = request(adapter)
    store = Store(tmp_path / "state")
    attempt = store.submit("key", req, grant(req))
    script.write_text("print('changed')")
    assert store.run(attempt, adapter)["state"] == "UNKNOWN"
    assert store.verify(attempt)["state"] != "VERIFIED"


def test_private_store_permissions(tmp_path):
    store, adapter, attempt = prepared(tmp_path)
    row = store.run(attempt, adapter)
    assert (store.root.stat().st_mode & 0o077) == 0
    assert (Path(row["artifact_path"]).stat().st_mode & 0o077) == 0
    assert hashlib.sha256(store.artifact(attempt)).hexdigest() == row["artifact_digest"]

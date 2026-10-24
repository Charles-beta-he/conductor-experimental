"""Parent local regression, not another independent review or acceptance vote."""
import json
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from acp_conductor_experiment import Task
from acp_conductor_replanning import Approval, Journal

from acp_conductor_resolution import Grant, Request, ResolutionGrant, Store, SubprocessAdapter
from acp_conductor_resolution.host import Host


def setup_case(tmp_path, damage, origin="verified"):
    store = Store(tmp_path / "attempts", max_starts=3)
    journal = Journal(tmp_path / "plan.sqlite", initial=(Task("old"),), max_rounds=2, max_revisions=2)
    host = Host(store, journal)
    runner = SubprocessAdapter((sys.executable, "-I", "-c", "print('OK')" if origin == "verified" else "print('WRONG')"))
    request = Request("old", runner.identity, "probe", "OK")
    assert host.step("old", request, Grant(request.digest, runner.identity, time.time() + 60), runner)["state"] == ("VERIFIED" if origin == "verified" else "UNKNOWN")
    row = store.query("old")
    target = Path(row["artifact_path"])
    if damage == "corrupt":
        target.write_bytes(b"damaged")
    else:
        target.unlink()
    assert store.query("old")["state"] == "UNKNOWN"
    with pytest.raises(ValueError):
        store.artifact("old")
    if origin == "verified":
        with pytest.raises(ValueError, match="persisted UNKNOWN"):
            store.resolution_evidence("old")
    assert store.verify("old")["state"] == "UNKNOWN"
    assert store.history("old")[-1]["state"] == "UNKNOWN"
    journal.reserve("round")
    offer = journal.offer("round", b'{"version":1,"tasks":[{"task_id":"new","dependencies":[]}]}')
    approval = Approval(offer["base_digest"], offer["candidate_digest"], time.time() + 60)
    with pytest.raises(ValueError, match="uncertain"):
        host.adopt("adopt", "round", approval)
    e = store.resolution_evidence("old")
    grant = ResolutionGrant("old", e["request_digest"], e["evidence_digest"], time.time() + 60)
    return store, journal, host, runner, approval, grant, row


@pytest.mark.parametrize("origin", ["verified", "mismatch"])
@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_resolved_failure_fresh_host_recovery_without_bad_artifact(tmp_path, origin, damage):
    store, journal, _host, runner, approval, grant, original = setup_case(tmp_path, damage, origin)
    prior_history = store.history("old")
    assert store.resolve("old", grant)["state"] == "FAILED"
    # These are the actual 0.0.2 business defect assertions, not a new API/signature.
    assert store.query("old")["state"] == "FAILED"
    assert store.verify("old")["state"] == "FAILED"
    history = store.history("old")
    assert history[:-1] == prior_history
    assert history[-1]["state"] == "FAILED"
    assert store.stats() == {"starts": 1, "active": 0}
    for key in ("artifact_path", "artifact_sha256", "closure", "token", "digest"):
        assert store.query("old")[key] == original[key]
    for operation in (lambda: store.run("old", runner), lambda: store.cancel("old"), lambda: store.reconcile("old"), lambda: store.resolve("old", grant)):
        assert operation()["state"] == "FAILED"
    assert store.history("old") == history
    with pytest.raises(ValueError):
        store.artifact("old")
    with pytest.raises(PermissionError):
        store.resolve("old", replace(grant, evidence_digest="0" * 64))
    # A genuinely new interpreter reads the same private DBs, adopts and executes.
    program = f'''
import sys
sys.dont_write_bytecode = True
sys.path[:0] = {list(sys.path)!r}
import json, time
from acp_conductor_experiment import Task
from acp_conductor_replanning import Approval, Journal
from acp_conductor_resolution import Store, Request, Grant, ResolutionGrant, SubprocessAdapter
from acp_conductor_resolution.host import Host
s = Store({str(store.root)!r}, max_starts=3)
j = Journal({str(journal.path)!r}, initial=(Task("old"),), max_rounds=2, max_revisions=2)
h = Host(s, j)
assert s.query("old")["state"] == s.verify("old")["state"] == s.history("old")[-1]["state"] == "FAILED"
assert s.resolve("old", ResolutionGrant(**{asdict(grant)!r}))["state"] == "FAILED"
try:
    s.artifact("old")
except ValueError:
    pass
else:
    raise AssertionError("bad artifact became readable")
h.adopt("adopt", "round", Approval(**{asdict(approval)!r}))
a = SubprocessAdapter((sys.executable, "-I", "-c", "print('OK')"))
r = Request("new", a.identity, "repair", "OK")
assert h.step("new", r, Grant(r.digest, a.identity, time.time()+60), a)["state"] == "VERIFIED"
assert s.artifact("new") == b"OK\\n"
assert s.stats() == {{"starts": 2, "active": 0}}
assert sum(e["state"] == "FAILED" for e in s.history("old")) == 1
print(json.dumps({{"old":s.query("old")["state"], "new":s.query("new")["state"], "stats":s.stats(), "artifact":s.artifact("new").decode()}}))
'''
    result = subprocess.run([sys.executable, "-I", "-c", program], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout) == {"old": "FAILED", "new": "VERIFIED", "stats": {"starts": 2, "active": 0}, "artifact": "OK\n"}
    print("FRESH_B1_RECOVERY", origin, damage, result.stdout.strip())


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_without_resolution_authority_bad_history_still_blocks(tmp_path, damage):
    store, _journal, host, _runner, approval, grant, _ = setup_case(tmp_path, damage)
    before = (store.history("old"), store.stats())
    for denied in (None, replace(grant, expires_at=0), replace(grant, evidence_digest="0" * 64)):
        with pytest.raises(PermissionError):
            store.resolve("old", denied)
        assert (store.history("old"), store.stats()) == before
    assert store.query("old")["state"] == store.verify("old")["state"] == "UNKNOWN"
    with pytest.raises(ValueError, match="uncertain"):
        host.adopt("adopt", "round", approval)
    assert store.stats() == {"starts": 1, "active": 1}

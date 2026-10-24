"""Regression probes for final dispatch authorization and nonregular evidence."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from acp_conductor_runtime import Grant, Request, Store, SubprocessAdapter


@pytest.mark.parametrize("phase", ["fingerprint", "stdin"])
def test_expiry_during_preparation_never_calls_popen(tmp_path, monkeypatch, phase):
    from acp_conductor_runtime import process

    clock = [time.time()]
    adapter = SubprocessAdapter((sys.executable, "-c", "print('OK')"))
    store = Store(tmp_path / "state")
    request = Request("expiry", adapter.identity, "Return OK", "OK")
    attempt = store.submit("key", request, Grant(request.digest, request.adapter_id, clock[0] + 5))
    monkeypatch.setattr(time, "time", lambda: clock[0])
    original_fingerprint = adapter._fingerprint
    original_temporary_file = process.tempfile.TemporaryFile
    if phase == "fingerprint":
        def fingerprint():
            clock[0] += 10
            return original_fingerprint()
        monkeypatch.setattr(adapter, "_fingerprint", fingerprint)
    else:
        def temporary_file(*args, **kwargs):
            clock[0] += 10
            return original_temporary_file(*args, **kwargs)
        monkeypatch.setattr(process.tempfile, "TemporaryFile", temporary_file)
    starts = []
    original_popen = process.subprocess.Popen

    def observed_popen(*args, **kwargs):
        starts.append(clock[0])
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(process.subprocess, "Popen", observed_popen)
    result = store.run(attempt, adapter)
    assert starts == []
    assert result["state"] == "UNKNOWN"
    assert result["reason"] == "PermissionError"
    assert store.run(attempt, adapter)["state"] == "UNKNOWN"
    assert starts == []


@pytest.mark.parametrize("verified", [False, True])
def test_fifo_artifact_is_rejected_without_blocking(tmp_path, verified):
    store = Store(tmp_path / "state")
    adapter = SubprocessAdapter((sys.executable, "-c", "print('OK')"))
    request = Request("fifo", adapter.identity, "Return OK", "OK")
    attempt = store.submit("key", request, Grant(request.digest, request.adapter_id, time.time() + 60))
    row = store.run(attempt, adapter)
    if verified:
        row = store.verify(attempt)
        assert row["state"] == "VERIFIED"
    artifact = Path(row["artifact_path"])
    artifact.unlink()
    os.mkfifo(artifact, mode=0o600)
    code = '''
import json,sys
sys.path.insert(0,sys.argv[1])
from acp_conductor_runtime import Store
s=Store(sys.argv[2])
a=sys.argv[3]
print(json.dumps({'query':s.query(a)['state'],'verify':s.verify(a)['state']}))
'''
    result = subprocess.run([sys.executable, "-I", "-c", code,
                             str(Path(__file__).resolve().parents[1] / "src"),
                             str(store.root), attempt], check=False,
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["verify"] == "UNKNOWN"
    assert observed["query"] == ("UNKNOWN" if verified else "CANDIDATE")

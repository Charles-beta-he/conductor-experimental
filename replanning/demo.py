"""Explicit disposable local host. Proposal emitter is Python, NOT an LLM.

Each command is a separate authorized local stage; plan approval is not a Grant.
A cross-store interruption is not automatically repaired. No platform lifecycle.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from acp_conductor_composition import Binding, Flow
from acp_conductor_experiment import Task
from acp_conductor_runtime import Grant, Request, Store, SubprocessAdapter

from acp_conductor_replanning import Approval, Journal, Observation


def adapter(repaired):
    transform = "upper" if repaired else "lower"
    return SubprocessAdapter(
        (
            sys.executable,
            "-I",
            "-c",
            f'import json,sys;print(json.load(sys.stdin)["prompt"].{transform}())',
        )
    )


def request(task, seed):
    return Request(task, adapter(task == "repair").identity, seed, seed.upper(), timeout=3)


def load(root):
    config = json.loads((root / "host.json").read_text())
    journal = Journal(
        root / "plan.sqlite3", initial=(Task("broken"),), max_rounds=2, max_revisions=1
    )
    store = Store(root / "attempts", max_starts=config["max_starts"], max_running=1)
    return config, journal, store


def observations(journal, store):
    result = []
    for task, attempt in journal.snapshot()["bindings"].items():
        row = store.query(attempt)
        if row["task_id"] != task:
            raise ValueError("host attempt identity mismatch")
        result.append(Observation(task, attempt, row["state"]))
    return tuple(result)


def initialize(root, seed, max_starts):
    if not seed or seed.upper() == seed.lower():
        raise ValueError("seed must exercise case transformation")
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    fd = os.open(root / "host.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"seed": seed, "max_starts": max_starts}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    config, journal, store = load(root)
    req = request("broken", config["seed"])
    attempt = store.submit("m5-broken", req, Grant(req.digest, req.adapter_id, time.time() + 300))
    journal.bind("broken", attempt)
    store.run(attempt, adapter(False))
    assert store.verify(attempt)["state"] == "FAILED"


def propose(journal):
    reservation = journal.reserve("repair-round")
    if not reservation["created"]:
        # Never re-invoke proposal generation for an existing claim, even CLAIMED.
        return {"proposal_process_started": False, "round_state": reservation["state"]}
    raw = subprocess.check_output(
        (
            sys.executable,
            "-I",
            "-c",
            'import json;print(json.dumps({"version":1,"tasks":[{"task_id":"repair","dependencies":[]}]}))',
        ),
        timeout=3,
    )
    journal.offer("repair-round", raw)
    return {"proposal_process_started": True, "emitter": "local Python, not LLM"}


def adopt(journal, store):
    candidate = journal.reserve("repair-round")
    if candidate["state"] != "PROPOSED":
        raise ValueError("no completed proposal; do not regenerate")
    approval = Approval(candidate["base_digest"], candidate["candidate_digest"], time.time() + 60)
    return journal.adopt(
        "approve-repair", "repair-round", approval, observations(journal, store), now=time.time()
    )


def execute(config, journal, store):
    snap = journal.snapshot()
    if snap["revision"] != 1:
        raise PermissionError("unapproved plan")
    if any(o.state in {"UNKNOWN", "RUNNING"} for o in observations(journal, store)):
        raise ValueError("historical uncertain/running attempt; no new execution")
    req = request("repair", config["seed"])
    bindings = snap["bindings"]
    if "repair" in bindings:
        attempt = bindings["repair"]
    else:
        # A distinct runtime Grant is explicitly created only in this host stage.
        attempt = store.submit(
            "m5-repair", req, Grant(req.digest, req.adapter_id, time.time() + 300)
        )
        journal.bind("repair", attempt)
    runner = adapter(True)
    result = Flow(store, (Binding(Task("repair"), req, attempt),), {runner.identity: runner}).drive(
        max_ticks=3
    )
    return {"flow_status": result.status}


def readback(journal, store):
    rows = [store.query(attempt) for attempt in journal.snapshot()["bindings"].values()]
    return {
        "plan": journal.snapshot(),
        "attempts": [
            {k: row[k] for k in ("task_id", "attempt_id", "state", "started")} for row in rows
        ],
        "outputs": {
            r["task_id"]: store.artifact(r["attempt_id"]).decode()
            for r in rows
            if r["state"] == "VERIFIED"
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "propose", "adopt", "run", "read"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--seed", default="Conductor M5 Mixed")
    parser.add_argument("--max-starts", type=int, default=2)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.root, args.seed, args.max_starts)
    config, journal, store = load(args.root)
    extra = {}
    if args.command == "propose":
        extra = propose(journal)
    elif args.command == "adopt":
        extra = {"adopted": adopt(journal, store)}
    elif args.command == "run":
        extra = execute(config, journal, store)
    print(json.dumps({**readback(journal, store), **extra}, sort_keys=True))


if __name__ == "__main__":
    main()

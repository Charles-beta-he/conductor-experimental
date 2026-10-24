# SPDX-License-Identifier: Apache-2.0
"""Private child entry point. Secrets stay in memory; stdout is final text only."""
from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import sys
import traceback
from pathlib import Path

_PHASE = "startup"


def mark(phase):
    global _PHASE
    _PHASE = phase
    Path("worker-status.json").write_text(json.dumps({"phase": phase}))


def main():
    os.umask(0o077)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    root, provider, model = sys.argv[1:]
    request = json.load(sys.stdin)
    # Scope all inherited ambient workflow controls out of this child.
    for key in list(os.environ):
        if key.startswith(("HERMES_KANBAN_", "HERMES_TASK_", "HERMES_REVIEW_")):
            os.environ.pop(key)
    logging.disable(logging.CRITICAL)
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), \
            contextlib.redirect_stderr(sink):
        sys.path.insert(0, root)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        # Resolve via installed Hermes without copying or printing any credential.
        from hermes_cli import auth, config

        # Read-only credential reuse: no OAuth rotation, CLI recovery, auth writes
        # or profile-config migration. Missing/expired credentials must stop here.
        from acp_conductor_runtime.hermes import readonly_provider_inputs
        readonly_provider_inputs(auth, config)
        from hermes_cli.runtime_provider import resolve_runtime_provider
        mark("provider_resolution")
        runtime = resolve_runtime_provider(requested=provider, target_model=model)
        mode = runtime.get("api_mode")
        if mode not in {"chat_completions", "codex_responses"}:
            raise ValueError("unsupported provider execution mode")
        if runtime.get("provider") in {"moa", "copilot-acp", "codex-app-server"}:
            raise ValueError("native runtime forbidden")

        private_home = Path.cwd() / "hermes-home"
        private_home.mkdir(mode=0o700)
        os.environ["HERMES_HOME"] = str(private_home)
        os.environ["HERMES_ENABLE_PROJECT_PLUGINS"] = "0"
        os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
        os.environ["TERMINAL_CWD"] = str(Path.cwd())
        # Runtime defaults are deliberately not inherited from the live profile.
        safe = {"model": {"default": model, "provider": runtime["provider"],
                          "context_length": 128000},
                "agent": {"max_turns": 1}, "context": {"engine": "compressor"},
                "sessions": {"write_json_snapshots": False},
                "memory": {"memory_enabled": False, "user_profile_enabled": False},
                "compression": {"enabled": False}}
        config.load_config = lambda: copy.deepcopy(safe)
        config.load_config_readonly = lambda: copy.deepcopy(safe)
        from run_agent import AIAgent
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from acp_conductor_runtime.hermes import deny_tool_agent
        mark("agent_initialization")
        agent = deny_tool_agent(AIAgent)(
            model=model, provider=runtime["provider"], api_mode=mode,
            base_url=runtime.get("base_url"), api_key=runtime.get("api_key"),
            enabled_toolsets=[], disabled_toolsets=[], skip_context_files=True,
            skip_memory=True, load_soul_identity=False, session_db=None,
            save_trajectories=False, checkpoints_enabled=False, quiet_mode=True,
            verbose_logging=False, max_iterations=1, max_tokens=128,
            reasoning_config={"effort": "low"}, request_overrides={},
        )
        try:
            agent._persist_disabled = True
            agent._skip_mcp_refresh = True
            agent._session_json_enabled = False
            agent.tools = []
            agent.valid_tool_names = set()
            if agent.api_mode != mode:
                raise ValueError("provider mode changed")
            mark("conversation_started")
            result = agent.run_conversation(request["prompt"])
            mark("conversation_returned")
            if result.get("error") or result.get("tool_calls"):
                raise ValueError("Hermes did not return pure text")
            text = result.get("final_response")
            if type(text) is not str or not text.strip():
                raise ValueError("no Hermes text result")
        finally:
            agent.close()
    sys.stdout.write(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- never leak provider details to parent logs
        frames = [{"file": Path(f.filename).name, "line": f.lineno, "name": f.name}
                  for f in traceback.extract_tb(exc.__traceback__)]
        Path("worker-status.json").write_text(json.dumps({"phase": _PHASE,
            "error_type": type(exc).__name__, "frames": frames}))
        sys.stderr.write(type(exc).__name__)
        raise SystemExit(1) from None

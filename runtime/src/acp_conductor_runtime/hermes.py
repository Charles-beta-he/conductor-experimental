# SPDX-License-Identifier: Apache-2.0
"""Optional, version-bound local Hermes bridge; no Hermes imports in the core."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from .process import SubprocessAdapter


class ToolDenied(PermissionError):
    pass


def readonly_provider_inputs(auth, config):
    """Child-only guards for this supported Hermes revision; never touch live modules."""
    def forbidden(*args, **kwargs):
        raise ToolDenied("credential refresh/recovery/write forbidden in local experiment")

    config.load_config = config.load_config_readonly
    auth._read_codex_tokens = partial(auth._read_codex_tokens, _lock=False)
    auth.resolve_codex_runtime_credentials = partial(
        auth.resolve_codex_runtime_credentials, refresh_if_expiring=False)
    auth._refresh_codex_auth_tokens = forbidden
    auth._recover_codex_tokens_from_cli = forbidden
    auth._save_auth_store = forbidden


def check_text_only_payload(kwargs):
    if kwargs.get("tools") or kwargs.get("tool_choice") not in (None, "none"):
        raise ToolDenied("provider-native tools are forbidden")


def deny_tool_agent(base):
    """Defense in depth, including direct/concurrent dispatch and native runtime."""
    class TextOnlyAgent(base):
        def _deny(self, *args, **kwargs):
            raise ToolDenied("text-only adapter prohibits tool/native/delegate dispatch")

        _invoke_tool = _deny
        _execute_tool_calls = _deny
        _execute_tool_calls_concurrent = _deny
        _execute_tool_calls_sequential = _deny
        _dispatch_delegate_task = _deny
        _run_codex_app_server_turn = _deny

        def _try_activate_fallback(self, *args, **kwargs):
            return False

        def _interruptible_api_call(self, api_kwargs, **kwargs):
            check_text_only_payload(api_kwargs)
            return super()._interruptible_api_call(api_kwargs, **kwargs)

        def _interruptible_streaming_api_call(self, api_kwargs, **kwargs):
            check_text_only_payload(api_kwargs)
            return super()._interruptible_streaming_api_call(api_kwargs, **kwargs)

        def _build_system_prompt(self, *args, **kwargs):
            return ("You are a text-only worker. Follow the user request literally. "
                    "No tools, delegation, memory, files or external actions are available.")

    return TextOnlyAgent


class HermesAdapter(SubprocessAdapter):
    """Uses an existing local provider without embedding credentials or runtime paths."""
    def __init__(self, python: Path, runtime_root: Path, *, provider: str, model: str):
        root = Path(runtime_root).resolve(strict=True)
        if not provider or not model or not (root / "run_agent.py").is_file():
            raise ValueError("explicit runtime/provider/model required")
        wrapper = Path(__file__).with_name("_hermes_worker.py")
        # Freeze source, not private configuration/credentials. No source is copied.
        pinned = [wrapper, Path(__file__), root / "run_agent.py", root / "toolsets.py",
                  root / "hermes_constants.py"]
        for directory in ("agent", "hermes_cli", "tools"):
            pinned.extend(sorted((root / directory).rglob("*.py")))
        super().__init__((str(Path(python).absolute()), "-I", str(wrapper), str(root), provider, model),
                         pinned_files=tuple(pinned))

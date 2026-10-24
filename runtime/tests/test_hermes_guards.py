"""Offline regression of payload, fallback and read-only credential boundaries."""
from types import SimpleNamespace

import pytest

from acp_conductor_runtime.hermes import (
    ToolDenied,
    check_text_only_payload,
    deny_tool_agent,
    readonly_provider_inputs,
)


@pytest.mark.parametrize("payload", [
    {"tools": [{"type": "function"}]},
    {"tools": [{"type": "web_search"}]},
    {"tool_choice": "auto"},
    {"tool_choice": {"type": "function"}},
])
def test_provider_native_payload_denied(payload):
    with pytest.raises(ToolDenied):
        check_text_only_payload(payload)


@pytest.mark.parametrize("method", ["_interruptible_api_call", "_interruptible_streaming_api_call"])
def test_api_guard_precedes_base_call(method):
    class Base:
        calls = 0

        def _interruptible_api_call(self, payload, **kwargs):
            self.calls += 1
            return "text"

        _interruptible_streaming_api_call = _interruptible_api_call

    agent = deny_tool_agent(Base)()
    with pytest.raises(ToolDenied):
        getattr(agent, method)({"tools": [{"type": "web_search"}]})
    assert agent.calls == 0
    assert getattr(agent, method)({"tools": [], "tool_choice": "none"}) == "text"
    assert agent.calls == 1
    assert agent._try_activate_fallback("anything") is False


def test_credentials_are_read_only_without_secret_material():
    auth = SimpleNamespace(
        _read_codex_tokens=lambda **kw: kw,
        resolve_codex_runtime_credentials=lambda **kw: kw,
        _refresh_codex_auth_tokens=lambda: pytest.fail("refresh reached"),
        _recover_codex_tokens_from_cli=lambda: pytest.fail("CLI recovery reached"),
        _save_auth_store=lambda: pytest.fail("write reached"),
    )
    config = SimpleNamespace(
        load_config=lambda: pytest.fail("mutable config load reached"),
        load_config_readonly=lambda: {"readonly": True},
    )
    readonly_provider_inputs(auth, config)
    assert auth._read_codex_tokens() == {"_lock": False}
    assert auth.resolve_codex_runtime_credentials() == {"refresh_if_expiring": False}
    assert config.load_config() == {"readonly": True}
    for name in ("_refresh_codex_auth_tokens", "_recover_codex_tokens_from_cli", "_save_auth_store"):
        with pytest.raises(ToolDenied):
            getattr(auth, name)()

"""M2 Hermes denial paths; no model calls in unit tests."""
import pytest

from acp_conductor_runtime.hermes import deny_tool_agent


@pytest.mark.parametrize("name", ["_invoke_tool", "_execute_tool_calls",
    "_execute_tool_calls_concurrent", "_execute_tool_calls_sequential",
    "_dispatch_delegate_task", "_run_codex_app_server_turn"])
def test_all_dispatch_paths_are_closed(name):
    class FakeAgent:
        pass
    agent = deny_tool_agent(FakeAgent)()
    with pytest.raises(PermissionError):
        getattr(agent, name)("any", arbitrary="input")


def test_prompt_does_not_load_private_context():
    class FakeAgent:
        pass
    agent = deny_tool_agent(FakeAgent)()
    assert agent._build_system_prompt("private") == (
        "You are a text-only worker. Follow the user request literally. "
        "No tools, delegation, memory, files or external actions are available.")

import ast
from pathlib import Path

import pytest

import acp_conductor_experiment


def test_core_has_only_declared_stdlib_imports_and_no_io():
    source = Path(acp_conductor_experiment.__file__).read_text()
    tree = ast.parse(source)
    imports = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports == {"dataclasses", "re"}
    calls = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not calls & {"open", "exec", "eval", "compile", "__import__", "getattr"}
    assert "/Users/" not in source and ".hermes" not in source


@pytest.mark.parametrize("scenario", ["match", "corrupt", "missing"])
def test_real_local_file_readback(scenario):
    pytest.importorskip("acp_local_file_effect")
    pytest.importorskip("acp_local_file_verifier")
    from demo import demonstrate

    result = demonstrate(scenario)
    assert result["apply_result"] == "applied_unverified"
    assert result["effect_calls"] == 1
    assert result["after"] == "verify_request"
    assert result["lifecycle_promoted"] is False
    assert result["durable_store"] is False
    if scenario == "match":
        assert result["readback_verdict"] == "MATCH"
        assert result["verification_requested_state"] == "VERIFIED"
    else:
        assert result["readback_verdict"] in {"INCONCLUSIVE", "MISMATCH"}
        assert result["verification_requested_state"] is None


def test_unknown_projection_replay_makes_zero_effect_calls(monkeypatch):
    effect = pytest.importorskip("acp_local_file_effect")
    pytest.importorskip("acp_local_file_verifier")
    from demo import demonstrate

    def forbidden(*args, **kwargs):
        pytest.fail("UNKNOWN reached effect adapter")

    monkeypatch.setattr(effect, "apply_local_file_payload", forbidden)
    monkeypatch.setattr(effect, "admit_local_file_target", forbidden)
    for _ in range(3):
        result = demonstrate("unknown")
        assert result["recommendation"] == "reconcile_required"
        assert result["effect_calls"] == 0

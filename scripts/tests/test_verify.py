# SPDX-License-Identifier: Apache-2.0
"""Negative tests for the deliberately narrow publication-boundary verifier."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("publication_verifier", Path(__file__).parents[1] / "verify.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@pytest.mark.parametrize("payload", [
    b"/" + b"Users/example/private/file", b"/" + b"home/example/private/file",
    b"-----" + b"BEGIN PRIVATE KEY-----", b"ghp_" + b"a" * 40,
    b"github_pat_" + b"a" * 50, b"sk-" + b"a" * 40,
    b"192." + b"168.42.10", b"10." + b"4.5.6", b"172." + b"16.2.3",
])
def test_common_patterns_reject_without_echoing_secret(payload):
    with pytest.raises(RuntimeError) as failure:
        verifier.scan("fixture", payload)
    assert payload.decode() not in str(failure.value)


def test_placeholders_and_environment_names_are_not_secrets():
    verifier.scan("fixture", b"OPENAI_API_KEY=YOUR_KEY; ghp_PLACEHOLDER; 127.0.0.1")


def test_missing_distribution_set_is_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="five wheels and sdists"):
        verifier.main(tmp_path)


def test_symlink_is_rejected(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("public")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "source_files", lambda: [link])
    with pytest.raises(RuntimeError, match="symlink"):
        verifier.main()

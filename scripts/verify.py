# SPDX-License-Identifier: Apache-2.0
"""Small publication-boundary check, not a complete secret/security/legal audit."""
from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ("", "runtime", "composition", "replanning", "resolution")
EXCLUDED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"}
PATTERNS = {
    "home-directory": rb"/(?:Users|home)/[A-Za-z0-9_.-]+/",
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "github-token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b",
    "provider-token": rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b",
    "private-lan": rb"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def scan(name, content):
    for kind, pattern in PATTERNS.items():
        require(re.search(pattern, content) is None, f"{kind} pattern in {name}; inspect privately")


def source_files():
    if (ROOT / ".git").exists():
        names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
        return sorted(ROOT / name.decode() for name in names if name)
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and not any(
        part in EXCLUDED or part.endswith(".egg-info") for part in p.relative_to(ROOT).parts
    ))


def main(dist=None):
    files = source_files()
    require(bool(files), "empty source inventory")
    inventory = []
    for path in files:
        relative = path.relative_to(ROOT)
        require(not path.is_symlink(), f"symlink outside source model: {relative}")
        require(not any(part in EXCLUDED or part in {".hermes", "evidence"} for part in relative.parts),
                f"excluded source path: {relative}")
        require(path.suffix not in {".db", ".sqlite", ".sqlite3", ".pyc"}, f"state file: {relative}")
        require(not path.name.startswith(".env"), f"environment file: {relative}")
        data = path.read_bytes()
        scan(str(relative), data)
        inventory.append((str(relative), hashlib.sha256(data).hexdigest()))
    packages = {}
    for component in COMPONENTS:
        directory = ROOT / component
        meta = tomllib.loads((directory / "pyproject.toml").read_text())["project"]
        require(meta["license"] == "Apache-2.0", f"license mismatch: {component}")
        require(set(meta["license-files"]) == {"LICENSE", "PROVENANCE.md"}, f"notices: {component}")
        require((directory / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes(), "license drift")
        require((directory / "PROVENANCE.md").read_bytes() == (ROOT / "PROVENANCE.md").read_bytes(),
                "provenance drift")
        packages[meta["name"]] = meta
    for meta in packages.values():
        for dependency in meta.get("dependencies", []):
            name, version = dependency.split("==")
            require(name in packages and packages[name]["version"] == version, "dependency closure")
    members = 0
    if dist:
        artifacts = sorted(Path(dist).iterdir())
        wheels = [p for p in artifacts if p.suffix == ".whl"]
        sdists = [p for p in artifacts if p.name.endswith(".tar.gz")]
        require(len(wheels) == len(sdists) == 5 and len(artifacts) == 10, "expected five wheels and sdists")
        seen_wheels, seen_sdists = set(), set()
        for artifact in artifacts:
            if artifact.suffix == ".whl":
                with zipfile.ZipFile(artifact) as archive:
                    entries = {n: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
                metadata = [data for name, data in entries.items() if name.endswith(".dist-info/METADATA")]
            else:
                with tarfile.open(artifact) as archive:
                    entries = {}
                    for entry in archive.getmembers():
                        require(not entry.issym() and not entry.islnk(), "archive link")
                        if entry.isfile():
                            entries[entry.name] = archive.extractfile(entry).read()
                metadata = [data for name, data in entries.items() if name.count("/") == 1 and name.endswith("/PKG-INFO")]
            require(len(metadata) == 1, f"metadata absent/ambiguous: {artifact.name}")
            meta = email.parser.BytesParser().parsebytes(metadata[0])
            name = meta["Name"]
            require(name in packages and meta["Version"] == packages[name]["version"], "artifact identity")
            require(meta["License-Expression"] == "Apache-2.0", "artifact license")
            seen = seen_wheels if artifact.suffix == ".whl" else seen_sdists
            require(name not in seen, "duplicate component artifact")
            seen.add(name)
            require(any(n.endswith("/LICENSE") and b == (ROOT / "LICENSE").read_bytes() for n, b in entries.items()),
                    f"missing license bytes: {artifact.name}")
            require(any(n.endswith("/PROVENANCE.md") and b == (ROOT / "PROVENANCE.md").read_bytes() for n, b in entries.items()),
                    f"missing provenance bytes: {artifact.name}")
            for name, data in entries.items():
                require(not Path(name).is_absolute() and ".." not in Path(name).parts, "unsafe archive path")
                scan(f"{artifact.name}:{name}", data)
                members += 1
    print(json.dumps({"source_files": len(files), "source_inventory_sha256": hashlib.sha256(
        json.dumps(inventory, separators=(",", ":")).encode()).hexdigest(), "packages": len(packages),
        "artifact_members_scanned": members, "pattern_hits": 0,
        "scope": "common-pattern and metadata checks only; not complete security/legal clearance"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    main(parser.parse_args().dist)

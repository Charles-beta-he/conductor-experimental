#!/usr/bin/env python3
"""Experimental source/build/install checks (Python 3.11+, stdlib driver).

The full pytest suite is SOURCE-BACKED, not installed-only. Separate isolated
processes check installed imports and execute the local recovery demo. No Hermes
worker main, model or provider is invoked. Development dependencies may use the
network; product wheels are installed offline. Evidence is retained outside the
source tree, including on failure. Run from any directory using this script's path.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile

PACKAGES = (".", "runtime", "composition", "replanning", "resolution")

# Importing _hermes_worker is safe: all Hermes/provider execution is in main(),
# protected by its __name__ guard. This probe imports it, NEVER executes main().
PROBE = r'''
import hashlib, importlib, json, pathlib, sys, sysconfig
roster = json.loads(pathlib.Path(sys.argv[1]).read_text())
sites = {pathlib.Path(sysconfig.get_path(k)).resolve() for k in ('purelib', 'platlib')}
records = []
for entry in roster:
    module = importlib.import_module(entry['module'])
    origin = pathlib.Path(module.__file__).resolve()
    if not any(origin.is_relative_to(site) for site in sites):
        raise RuntimeError('not installed site-packages: ' + str(origin))
    digest = hashlib.sha256(origin.read_bytes()).hexdigest()
    if digest != entry['sha256']:
        raise RuntimeError('installed/wheel/source mismatch: ' + entry['module'])
    records.append(dict(entry, origin=str(origin)))
pathlib.Path(sys.argv[2]).write_text(json.dumps(records, indent=2) + '\n')
print(json.dumps({'installed_module_count': len(records), 'origins': records}, indent=2))
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def pytest_report(path):
    cases = ET.parse(path).getroot().findall(".//testcase")
    skips = [case for case in cases if case.find("skipped") is not None]
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    return {
        "kind": "SOURCE-BACKED",
        "passed": len(cases) - len(skips) - failures - errors,
        "failures": failures, "errors": errors, "skipped": len(skips),
        "skip_reasons": dict(Counter(case.find("skipped").get("message", "") for case in skips)),
        "skips": [{"class": c.get("classname"), "name": c.get("name"),
                   "reason": c.find("skipped").get("message", "")} for c in skips],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, help="new or empty directory outside source")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    summary = {"status": "failed", "stage": "work-directory", "source": str(root),
               "commands": [], "suite_kind": "SOURCE-BACKED",
               "limitations": ["pytest uses source paths, including test subprocesses",
                               "no live Hermes, model/provider or external ACP integration",
                               "sdists are built, not rebuilt or installed"]}
    work = None
    try:
        candidate = (args.work_dir.expanduser().resolve() if args.work_dir else
                     Path(tempfile.mkdtemp(prefix="conductor-check-" )).resolve())
        require(not candidate.is_relative_to(root), "work directory must be outside source")
        require(not candidate.exists() or candidate.is_dir() and not any(candidate.iterdir()),
                "work directory must be new or empty; existing contents are never overwritten")
        candidate.mkdir(parents=True, exist_ok=True)
        work = candidate
        summary["work_dir"] = str(work)
        (work / "logs").mkdir()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("PYTHON") and k not in {"VIRTUAL_ENV", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}}
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")

        def run(stage, command, *, cwd=work, extra_env=None, checked=True):
            summary["stage"] = stage
            log = work / "logs" / f"{len(summary['commands']):02d}-{stage}.log"
            record = {"stage": stage, "command": [str(x) for x in command],
                      "cwd": str(cwd), "log": str(log)}
            summary["commands"].append(record)
            dump(work / "summary.json", summary)
            with log.open("w", encoding="utf-8") as output:
                output.write(json.dumps(record) + "\n")
                output.flush()
                result = subprocess.run(record["command"], cwd=cwd, env=env | (extra_env or {}),
                                        stdout=output, stderr=subprocess.STDOUT)
            record["returncode"] = result.returncode
            if checked:
                require(result.returncode == 0, f"{stage} failed ({result.returncode}); see {log}")
            return result.returncode, log

        summary["stage"] = "metadata"
        require((root / "requirements-dev.lock").is_file(), "missing requirements-dev.lock")
        projects = [tomllib.loads((root / p / "pyproject.toml").read_text())["project"]
                    for p in PACKAGES]
        summary["projects"] = [{k: p[k] for k in ("name", "version")} for p in projects]
        dist = work / "dist"
        for package in PACKAGES:
            run("build-" + ("root" if package == "." else package),
                [sys.executable, "-B", "-m", "build", "--no-isolation", "--outdir", dist, root / package])
        summary["stage"] = "archive-inventory"
        wheels = sorted(dist.glob("*.whl"))
        sdists = sorted(dist.glob("*.tar.gz"))
        require(len(wheels) == len(sdists) == 5, "expected exactly five wheels and five sdists")
        expected = {p['name'].replace('-', '_') + '-' + p['version'] for p in projects}
        require({w.name.split('-py')[0] for w in wheels} == expected, "wheel identity mismatch")
        require({s.name[:-7] for s in sdists} == expected, "sdist identity mismatch")
        summary["archives"] = [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                               for p in wheels + sdists]
        roster = []
        for package, project in zip(PACKAGES, projects):
            wheel = next(w for w in wheels if w.name.startswith(
                project['name'].replace('-', '_') + '-' + project['version'] + '-'))
            src = root / package / "src"
            with zipfile.ZipFile(wheel) as archive:
                sources = sorted(src.rglob("*.py"))
                require(set(archive.namelist()) & {str(p.relative_to(src)) for p in sources},
                        f"no Python source in {wheel.name}")
                require({n for n in archive.namelist() if n.endswith('.py')} ==
                        {p.relative_to(src).as_posix() for p in sources}, "wheel/source roster differs")
                for source in sources:
                    relative = source.relative_to(src)
                    require(archive.read(relative.as_posix()) == source.read_bytes(),
                            f"wheel/source mismatch: {relative}")
                    parts = list(relative.with_suffix("").parts)
                    if parts[-1] == "__init__":
                        parts.pop()
                    roster.append({"module": ".".join(parts), "source": str(source),
                                   "wheel": str(wheel),
                                   "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
        require(len(roster) == 14, "expected fourteen source modules")
        dump(work / "roster.json", roster)
        run("venv", [sys.executable, "-B", "-m", "venv", work / "venv"])
        python = work / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run("locked-dev-install", [python, "-I", "-m", "pip", "--isolated", "install",
                                   "--require-hashes", "-r", root / "requirements-dev.lock"])
        run("offline-wheel-install", [python, "-I", "-m", "pip", "--isolated", "install",
                                       "--no-index", "--no-deps", *wheels])
        run("pip-check", [python, "-I", "-m", "pip", "--isolated", "check"])
        code, _ = run("source-backed-pytest", [python, "-B", "-m", "pytest", "-ra",
                      "-o", "addopts=", "-o", f"cache_dir={work / 'pytest-cache'}",
                      "--basetemp", work / "pytest-tmp", "--junitxml", work / "pytest.xml",
                      *(root / p / "tests" for p in PACKAGES)], cwd=root,
                      extra_env={"PYTHONPATH": os.pathsep.join(str(root / p / "src") for p in PACKAGES)},
                      checked=False)
        summary["pytest"] = pytest_report(work / "pytest.xml")
        tests = summary["pytest"]
        require(code == 0 and tests["failures"] == tests["errors"] == 0, "source-backed pytest failed")
        require(tests["passed"] == 210 and tests["skipped"] == 4, "expected 210 passes and four ACP skips")
        require(all("test_composition" in s["class"] and
                    s["name"].startswith(("test_real_local_file_readback[",
                                           "test_unknown_projection_replay_makes_zero_effect_calls")) and
                    any(name in s["reason"] for name in ("acp_local_file_effect", "acp_local_file_verifier"))
                    for s in tests["skips"]), "unexpected skip outside external ACP integration")
        run("installed-imports", [python, "-I", "-B", "-c", PROBE,
                                  work / "roster.json", work / "installed-imports.json"])
        summary["installed_modules"] = json.loads((work / "installed-imports.json").read_text())
        # Inspect modules loaded by THIS demo in the same isolated process.
        # pip may have precompiled .pyc files; verbose .py-only log matching is
        # not an import-origin oracle. Do not import absent modules after demo.
        demo_probe = r'''
import hashlib, json, pathlib, runpy, sys, sysconfig
script, demo_root, roster_file, output = sys.argv[1:]
sys.argv = [script, demo_root]
runpy.run_path(script, run_name='__main__')
sites = {pathlib.Path(sysconfig.get_path(k)).resolve() for k in ('purelib', 'platlib')}
records = []
for entry in json.loads(pathlib.Path(roster_file).read_text()):
    module = sys.modules.get(entry['module'])
    if module is None:
        continue
    origin = pathlib.Path(module.__file__).resolve()
    if not any(origin.is_relative_to(site) for site in sites):
        raise RuntimeError('demo imported outside installed site-packages: ' + str(origin))
    if hashlib.sha256(origin.read_bytes()).hexdigest() != entry['sha256']:
        raise RuntimeError('demo installed/source bytes differ: ' + entry['module'])
    records.append(dict(entry, origin=str(origin)))
pathlib.Path(output).write_text(json.dumps(records, indent=2) + '\n')
'''
        run("installed-recovery-demo", [python, "-I", "-B", "-c", demo_probe,
            root / "resolution/demo.py", work / "demo-new-root",
            work / "roster.json", work / "demo-imports.json"])
        summary["stage"] = "demo-readback"
        demo = json.loads((work / "demo-new-root/result.json").read_text())
        require(demo["states"] == ["FAILED", "VERIFIED"] and
                demo["stats"] == {"starts": 2, "active": 0} and
                demo["artifact"].strip() == "REPAIRED" and demo["uncertain_adoption_rejected"] is True,
                "recovery demo result mismatch")
        history = [e["state"] for e in demo["failed_history"]]
        require("UNKNOWN" in history and history[-1] == "FAILED", "missing UNKNOWN -> FAILED history")
        require(demo["repair_history"][-1]["state"] == "VERIFIED", "repair not VERIFIED")
        demo_modules = json.loads((work / "demo-imports.json").read_text())
        required = {r["module"] for r in roster
                    if not r["module"].startswith(("acp_conductor_runtime", "acp_conductor_composition"))}
        require(required <= {r["module"] for r in demo_modules}, "demo installed import origins missing")
        summary.update(status="passed", stage="complete", demo=demo,
                       demo_installed_modules=demo_modules)
    except (Exception, KeyboardInterrupt) as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if work is not None:
            dump(work / "summary.json", summary)
        print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

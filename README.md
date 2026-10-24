# Conductor Experimental

An experimental, local-first Python toolkit for explicit task coordination, durable attempt records and evidence-bound failure recovery.

**Pre-alpha; not production-ready.** Released under Apache-2.0 as an experimental source project. The publishing rights holder has confirmed redistribution rights; see PROVENANCE.md. Public availability does not imply product acceptance or independent approval of the newer recovery implementation.

## What it does

- A pure DAG planner recommends next steps; it does not authorize or execute work.
- A durable local attempt store binds execution grants to requests and records artifacts.
- Fixed-plan composition and bounded plan admission keep execution and plan approval distinct.
- The version-isolated resolution store records explicit FAILED outcomes for qualifying UNKNOWN attempts. A repair requires an approved new plan/task and a separate execution grant; resolution cannot manufacture VERIFIED.
- Local POSIX subprocess execution and deterministic artifact verification make the boundaries executable.

The core package closure does not require Hermes. An optional, version-sensitive Hermes bridge is included in `runtime`; it needs a separately supplied Hermes installation and provider access. It is not installed or exercised by the default local example.

## Requirements

Python 3.11+, venv/ensurepip and a POSIX host. Development tools are hash-locked. CI smoke-tests Python 3.11.15 on Ubuntu 24.04 and Python 3.13.17 on macOS 15; this is not full cross-machine product acceptance. No Windows compatibility claim.

## Install from source

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-build-isolation --no-deps . ./runtime ./composition ./replanning ./resolution
python resolution/demo.py "$(mktemp -d)/new-demo-root"
```

The resolution example creates disposable local state and executes local child processes. It does not call a model or promote any external task lifecycle. Inspect operator-selected programs before granting execution.

## Tests

```sh
PYTHONPATH=src:runtime/src:composition/src:replanning/src:resolution/src python -m pytest tests runtime/tests composition/tests replanning/tests resolution/tests
```

For the canonical clean build/install check (creates disposable state; downloads only locked development tools):

```sh
python scripts/check.py
```

The complete pytest suite above is source-backed, including subprocess tests that explicitly add source paths. The canonical command reports it separately from an isolated installed-wheel module probe and real installed recovery demo. It does not mislabel all pytest cases as installed-only validation.

The legacy root `demo.py` and four root integration cases additionally need the separate `acp_local_file_effect` and `acp_local_file_verifier` packages. They are not bundled here; those cases skip when unavailable. This is distinct from the standalone `resolution/demo.py` and its tests. A skipped external integration is not a passed owner-completion test.

## Architecture

| Directory | Responsibility |
| --- | --- |
| `src` | Pure proposal-only planner |
| `runtime` | Attempt persistence, local process adapter, optional Hermes bridge |
| `composition` | Fixed-DAG v1 composition |
| `replanning` | Bounded plan-admission journal |
| `resolution` | Isolated v2 store and host; includes the local 0.0.3 terminal-readback repair |

The v1 composition owner requires v1 store types; use the separate v2 host for the resolution store. The APIs are experimental, and these stores are not a canonical platform task owner.

## Known limitations

- Trusted local operators and trusted, non-detaching programs only. Not an OS sandbox, remote authentication service or rollback for external effects.
- No automatic replay of uncertain execution; a dead runner without durable closure can remain UNKNOWN.
- Deterministic verification is not general semantic or business acceptance.
- Native ACP owner completion is not integrated; digest-domain mismatch remains unresolved outside this project.
- A second real AI Agent runtime has not been accepted. Language subprocess probes do not establish this capability.
- Recovery has local execution evidence, but the newer repair has not received independent result approval. Prior review HOLD is not overturned by local tests.
- Sustained Beta usage, cross-machine support and complete release acceptance are not established.

See LICENSE, PROVENANCE.md, CONTRIBUTING.md and SECURITY.md. Package licenses declare Apache-2.0; that declaration does not replace confirmation of source rights.

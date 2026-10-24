# Contributing

This is an experimental pre-alpha source project. Use GitHub issues and pull requests for non-sensitive changes; no response-time or production-support promise is offered.

Use Python 3.11+ on a POSIX system. Follow README installation and test commands. Tests may create disposable directories, SQLite databases and owned child processes. Default checks must not require provider credentials or make real model calls. Optional legacy ACP leaf integrations are explicitly outside the core dependency closure.

Preserve request/grant/evidence binding, spent execution budgets, UNKNOWN no-replay, immutable task dependencies and separation between plan approval, execution grants, resolution and verification. A candidate artifact is not a verified result; a planner recommendation grants no execution authority.

For changes, include a failing regression before the fix, exercise restart and negative paths where relevant, and report skips and known failures separately. Do not commit environments, caches, artifacts, databases, provider config, local audit logs or credentials. Confirm redistribution rights for every contribution and retain applicable source notices.

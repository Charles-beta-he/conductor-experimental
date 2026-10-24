# Security scope

Pre-alpha experimental software. No production security-support or hostile-code-containment guarantee is offered.

Execution assumes a trusted local operator selecting trusted synchronous POSIX programs. Application grants are not OS isolation. Program effects may survive failure, timeout or termination. UNKNOWN is intentionally conservative; do not clear it by manipulating databases or rerunning under a different identity.

Do not expose the local APIs as an unauthenticated remote service. Keep attempt directories private and never use provider credentials in default examples/tests. The optional Hermes bridge needs a separately supplied runtime and operator-approved provider configuration; core installation does not audit that runtime or its provider's data handling.

Do not put credentials, private artifacts or sensitive reproduction data in public issues. A dedicated private vulnerability-reporting service has not been established; arrange a private channel with the maintainer before sending sensitive details.

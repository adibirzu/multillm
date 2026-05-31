# Security Policy

**Do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Public disclosure of an unpatched vulnerability puts every operator of MultiLLM Gateway at risk. The private disclosure process below exists so we can ship a fix and a coordinated advisory at the same time.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :grey_question: best-effort, no SLA |

Pre-1.0 versions receive security fixes on a best-effort basis only. Operators on pre-1.0 releases are strongly encouraged to upgrade.

## Reporting a Vulnerability

Report vulnerabilities via **GitHub Security Advisories**:

> <https://github.com/adibirzu/multillm/security/advisories/new>

When reporting, please include:

- A description of the vulnerability and the security impact (confidentiality, integrity, availability)
- Steps to reproduce (a proof-of-concept is ideal, but not required to file)
- Affected version(s) — output of `multillm --version` or the commit SHA
- Affected backend(s), if backend-specific
- The gateway log excerpt around the issue, with any tokens or credentials redacted
- Your suggested fix, if any

If your report includes credentials, secrets, internal hostnames, or other sensitive material, please scrub or redact them before submission. We will not store unredacted credentials.

## Response Expectations

| Stage | Target |
| ----- | ------ |
| Acknowledgement of receipt | 5 business days |
| Initial triage + severity assessment | 10 business days |
| Patch availability for confirmed critical vulnerabilities | 30 days from triage, where feasible |
| Coordinated public advisory + CVE | After patch is published and operators have a reasonable upgrade window |

We will keep you informed at each stage and credit you in the advisory unless you request otherwise.

## CVE Issuance

Confirmed vulnerabilities are assigned CVE identifiers through GitHub's CVE Numbering Authority. The CVE is published with the security advisory once a patched release is available and an operator upgrade window has elapsed.

## Out of Scope

The following are explicitly **not** in scope for this policy:

- Vulnerabilities in upstream LLM provider APIs (OpenAI, Anthropic, Google, etc.) — report to the upstream vendor
- Issues that require physical access to the host running the gateway
- Misconfigurations the operator is responsible for (e.g., publishing the gateway to the public internet without authentication, leaving `MULTILLM_API_KEY` unset)
- Findings in pre-release or development branches that have not been tagged for release

## Hall of Fame

Researchers credited in published advisories are listed in the release notes for the fixing version.

<!--
Thanks for opening a pull request against MultiLLM Gateway.

Please complete every section below. PRs that skip the Test Plan or Security Checklist
sections will be sent back without review.

If this is a draft, mark it as Draft via the GitHub UI rather than deleting sections.
-->

## Description

<!--
What does this PR change and why? One or two paragraphs. Link any design discussions.
For new backends, paste the relevant excerpt from the Backend request issue.
-->

## Linked Issue

<!-- Use the keyword "Closes" or "Refs" so GitHub auto-links. -->

Closes #

## Test Plan

<!--
List every category of test that exercises this change.
Check the box for each that applies and was actually added/updated in this PR.
A reviewer should be able to reproduce your verification from this list alone.
-->

- [ ] Unit tests added or updated for new logic
- [ ] Integration tests covering the new or changed endpoint(s)
- [ ] End-to-end test for the affected user flow (where applicable)
- [ ] Manual verification steps (paste the exact commands you ran):

```
```

- [ ] `pytest -v` passes locally
- [ ] Coverage for changed files is ≥ 80%

## Security Checklist

<!--
Every item must be checked or explicitly explained.
"N/A" is acceptable when truthful — please note why next to the box.
-->

- [ ] No hardcoded credentials, API keys, tokens, or wallet passwords in source
- [ ] No public IP addresses, internal hostnames, or tenancy IDs in logs, configs, or fixtures
- [ ] No personally identifying information (real names, emails, addresses) in code or fixtures
- [ ] All user-controlled input is validated at the system boundary
- [ ] Any SQL is parameterized (no string concatenation into queries)
- [ ] New endpoints have appropriate auth/authorization (or explicit `# noqa: auth` with rationale)
- [ ] `gitleaks` and `trufflehog` pre-commit hooks pass locally on this branch
- [ ] No `print()` statements or stray debug logging in production code paths

## Documentation

- [ ] README updated if user-facing behavior changed
- [ ] `docs/operations/` updated if operator behavior changed
- [ ] CHANGELOG entry added (or note that this is covered by the milestone changelog)

---

By submitting this PR, I agree to license my contribution under Apache 2.0 (see [LICENSE](../LICENSE)).

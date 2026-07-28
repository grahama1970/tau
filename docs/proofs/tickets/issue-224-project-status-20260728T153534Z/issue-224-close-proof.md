# Tau #224 Close Proof

Ticket: `grahama1970/tau#224`

## Disposition

Closed after integrating PR #227's machine-generated project-status runtime onto
current `main` and regenerating the checked-in status artifacts from live GitHub
state.

The reopen reason was valid: the first closure referred to PR #227 before its
files reached `main`. The current repair cherry-picks that single ticket commit,
refreshes the generated snapshot after #222 and #225 closed, and leaves #72/#180
open as parent work instead of treating them as completed.

## Commands Run

```bash
git merge-base --is-ancestor b26244912d797bfeac62213ddeb2769d674143c0 HEAD; echo $?
```

Result before integration:

```text
1
```

```bash
git cherry-pick b26244912d797bfeac62213ddeb2769d674143c0
```

Result:

```text
[main 98bbadf1] Add machine-generated authoritative project status + CI freshness gate (#224)
```

```bash
gh api repos/grahama1970/tau/branches/main/protection --jq \
  '{enforce_admins:.enforce_admins.enabled, required_status_checks:(.required_status_checks.contexts // [])}'
gh issue list --repo grahama1970/tau --state open --limit 100 --json number,title,labels
gh issue list --repo grahama1970/tau --state closed --limit 30 --json number,title,closedAt
```

Result used to regenerate `docs/status/github-snapshot.json`:

```text
required checks: ["uv run pytest -q","canonical-browser-proofs"]
enforce_admins: false
open critical issues: [224,223,221,180,72]
recently completed includes: [225,222,220,219,218,217,216,215,214,213]
```

```bash
uv run tau project-status build --out docs/status/CURRENT_STATE.json \
  --github-snapshot docs/status/github-snapshot.json
uv run tau project-status render docs/status/CURRENT_STATE.json \
  --out docs/status/CURRENT_STATE.md
uv run tau project-status verify --status docs/status/CURRENT_STATE.json \
  --github-snapshot docs/status/github-snapshot.json
```

Result:

```text
build: status PASS, github_freshness FRESH,
  semantic_content_digest sha256:977aec7ba040e4051e575b8b9d080e73ffec683884ee02751dcc5dbb3d5b22d2
render: status PASS, same semantic_content_digest
verify: status PASS, errors []
```

```bash
uv run tau project-status verify --status docs/status/CURRENT_STATE.json \
  --github-snapshot /tmp/tau-224-snapshot-mutated.json
```

Result after adding synthetic issue `999` to the snapshot without rebuilding:

```text
exit=1
status FAIL
errors include source_drift:github_snapshot
```

```bash
uv build --wheel --out-dir /tmp/tau-224-wheel-20260728T195516
uv run python - /tmp/tau-224-wheel-20260728T195516
```

Result:

```text
Successfully built /tmp/tau-224-wheel-20260728T195516/tau-0.1.0-py3-none-any.whl
Summary: A local-first, zero-trust DAG admission and supervision plane for goal-locked agent workflows, with a Pi-inspired coding surface as a secondary layer.
```

## Proof Artifacts

- `docs/status/github-snapshot.json`
- `docs/status/CURRENT_STATE.json`
- `docs/status/CURRENT_STATE.md`
- `docs/proofs/tickets/issue-224-project-status-20260728T153534Z/github-snapshot.json`
- `docs/proofs/tickets/issue-224-project-status-20260728T153534Z/CURRENT_STATE.json`
- `docs/proofs/tickets/issue-224-project-status-20260728T153534Z/CURRENT_STATE.md`
- `docs/proofs/tickets/issue-224-project-status-20260728T153534Z/closure-evidence.json`

## Scope

`mocked`: no

`live`: yes

`provider_live`: false

This proves the generated project-status runtime exists on current `main`, the
checked-in snapshot was refreshed from live GitHub issue/protection state, and
the freshness verifier accepts the regenerated artifacts. It does not prove #72,
#180, or #221 are complete.

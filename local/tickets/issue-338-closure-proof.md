# Issue #338 closure proof

What changed:
- Tau now fails closed when a DAG declares exact requested handlers but the handler nodes differ and `handler_fallback_authorized` is not true.
- Tau node sanity checks now include create-svg creator gates for `create_svg.variant_candidate.v1`, path/hash-backed `.svg` artifacts, `mocked=false`, `live=true`, no failure code, and Tau node-origin binding.
- Retained agentic eval coverage was added for exact handler substitution, prose-only SVG progress, mocked/non-live candidate receipts, local preview origin bypass, and one positive bound SVG candidate.

Validation:
- `uv run ruff check src/tau_coding/project_dag.py tests/test_project_dag.py scripts/agentic-eval-tau-create-svg-origin-gates.py` -> PASS.
- `uv run pytest --tau-suite=all tests/test_project_dag.py -q` -> 115 passed.
- `/home/graham/.pi/agent/skills/agentic-evals/run.sh run evals/tau_create_svg_origin_gates_agentic_eval.json --output local/agentic-evals/tau-create-svg-origin-gates-agentic-evals-report.json` -> READY, PASS=5, FAIL=0, trial_count=10, capability verdict PROVEN.
- Scoped worktree audit: `skills/best-practices-github-ticket/scripts/audit-worktrees.sh --repo /home/graham/workspace/experiments/tau --scope-path ... --json` -> ok=true, dirty_secondary=0 for issue #338 paths.

Proof boundary:
- Live Tau project-DAG executions and artifact readbacks are exercised by the retained agentic eval fixture.
- Browser provider quality and human visual acceptance are not claimed.

Commit:
- Tau main: e1305957c0df35e6a506a818255fe1784b807a8a
- Agent-skills triage-error catalog main: 815e40fee29cc7f894ca04ef02e2192e1323efcd

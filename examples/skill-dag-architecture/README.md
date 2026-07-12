# WebGPT Architecture Skill DAG

1. Replace `HUMAN_SUPPLIED_TAB_ID` and `HUMAN_SUPPLIED_CONVERSATION_URL` in
   `dag.json`.
2. Edit `review-request.md` and `accepted-architecture.yaml`.
3. Run:

```bash
uv run tau dag-run examples/skill-dag-architecture/dag.json
```

If Tau writes `/tmp/tau-skill-dag-architecture/webgpt/clarification-request.json`,
write the human answer to `/tmp/tau-skill-dag-architecture/answer.md` and rerun
the same command. Tau stops after three total rounds.

WebGPT review and UX Lab rendering are live skill calls. They do not prove that
the proposed architecture is correct or implemented.

# Skill Composition Basic

This example shows the first Tau/agent-skills composition layer: a read-only
capability registry.

Run the example:

```bash
examples/skill-composition-basic/run.sh /tmp/tau-skill-composition-basic
```

Or generate and validate the default registry directly:

```bash
uv run tau skill-capability-registry-default \
  --out /tmp/tau-skill-composition-basic/registry.json

uv run tau skill-capability-registry-validate \
  --registry /tmp/tau-skill-composition-basic/registry.json \
  --out /tmp/tau-skill-composition-basic/validation-receipt.json
```

The validation receipt is a registry proof only. It does not execute skills or
prove that a skill output is admissible.

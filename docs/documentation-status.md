# Tau Documentation Status Convention

Developer-facing Tau docs classify aspirational language so current support is not confused with roadmap or history.

Use this marker near a document or section:

```html
<!-- tau-doc-status: CURRENT_CONTRACT -->
```

Allowed values:

- `CURRENT_CONTRACT`: supported current behavior.
- `KNOWN_LIMITATION`: honest current limitation or non-goal.
- `EXPERIMENTAL`: implemented but not developer-stable.
- `ROADMAP`: planned or future work.
- `HISTORICAL_ADR`: preserved decision record, not current promise.
- `SECURITY_NONCLAIM`: caveat or threat-model boundary that must stay explicit.

`python scripts/check-doc-status.py` fails when primary developer docs introduce `planned`, `future`, `not yet`, `does not yet`, or `TODO` without one of these classifications.

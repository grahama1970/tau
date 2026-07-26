# Issue #148 Proof: Canonical Browser Proofs In CI

Ticket: https://github.com/grahama1970/tau/issues/148

## Result

The manual GitHub Actions workflow `.github/workflows/canonical-proofs.yml`
ran the five canonical browser proof scripts, built and checked the wheel,
ran the immutable-goal audit, generated an artifact manifest, read the
manifest back in CI, and uploaded the proof archive.

Run:

- Workflow: `Canonical Tau proofs`
- Run id: `30212217663`
- Run URL: https://github.com/grahama1970/tau/actions/runs/30212217663
- Job: `canonical-browser-proofs`
- Head SHA: `3b2f1fb867805467dfc2f4e281b21214d631266f`
- Conclusion: `success`
- Artifact: `tau-canonical-proofs-30212217663`

## CI Job Steps

All job steps reported `success`, including:

- `Run canonical browser proofs`
- `Run immutable-goal audit`
- `Write artifact checksums`
- `Read back artifact checksums`
- `Upload canonical proof artifacts`

## Downloaded Artifact Replay

Downloaded artifact root:

```text
/tmp/tau-issue-148-ci-artifacts-30212217663.FmhKdu/tau-canonical-proofs-30212217663
```

Local replay command:

```bash
artifact_dir=$(cat /tmp/tau-issue-148-artifact-dir.txt)
root="$artifact_dir/tau-canonical-proofs-30212217663"
uv run python - <<'PY' "$root"
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
manifest = json.loads((root / 'artifact-manifest.json').read_text())
audit = json.loads((root / 'audit/immutable-goal-audit.json').read_text())
missing = []
for record in manifest['artifacts']:
    path = root / record['path']
    actual = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != record['sha256']:
        missing.append((record['path'], record['sha256'], actual))
print(json.dumps({
    'manifest_status': manifest.get('status'),
    'manifest_schema': manifest.get('schema'),
    'manifest_artifact_count': manifest.get('artifact_count'),
    'audit_status': audit.get('status'),
    'audit_live': audit.get('live'),
    'audit_mocked': audit.get('mocked'),
    'source_ref': manifest.get('source_ref'),
    'sha_mismatch_count': len(missing),
}, indent=2, sort_keys=True))
if missing:
    raise SystemExit(missing[:3])
PY
```

Replay output:

```json
{
  "audit_live": true,
  "audit_mocked": false,
  "audit_status": "PASS",
  "manifest_artifact_count": 37,
  "manifest_schema": "tau.canonical_proofs_ci_artifact_manifest.v1",
  "manifest_status": "PASS",
  "sha_mismatch_count": 0,
  "source_ref": "3b2f1fb867805467dfc2f4e281b21214d631266f"
}
```

The downloaded artifact includes `wheel/.gitignore`, so the uploaded archive
matches the manifest's hidden-file entry.

## Implementation Commits

- `b89735d3` Add canonical proofs CI workflow
- `36586cb7` Fix canonical proofs artifact root
- `ce6d2ab9` Harden canonical browser proof prewarm
- `454f7dfc` Stabilize canonical browser proof observations
- `62924865` Report missing workflow help options
- `8f6497f6` Read workflow help from both output streams
- `32c53a2e` Expose workflow help audit preview
- `a012992b` Strip ANSI before audit help checks
- `239e7652` Use upload-safe canonical proof root
- `42bf2fc8` Handle transient SQLite viewer startup races
- `3b2f1fb8` Include generated hidden proof files in artifact

## Proof Boundary

- mocked: no
- live: yes
- provider/model calls: no
- exercised: GitHub Actions workflow dispatch, browser proof scripts, wheel
  build/check, immutable-goal audit, artifact manifest generation, CI manifest
  readback, artifact upload, local artifact download, local SHA replay
- not exercised: paid provider/model execution


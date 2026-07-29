#!/usr/bin/env bash
# verify_bundle.sh (#217): recompute every hash the manifest binds and exit
# non-zero on any drift. Run from inside the bundle directory.
set -euo pipefail
cd "$(dirname "$0")"
python3 - <<'PY'
import hashlib, json, sys
from pathlib import Path
manifest = json.loads(Path("BUILD_MANIFEST.json").read_text())
def digest(p):
    return "sha256:" + hashlib.sha256(Path(p).read_bytes()).hexdigest()
drift = []
for name, expected in manifest["bound_files"].items():
    actual = digest(name)
    if actual != expected:
        drift.append(f"{name}: {actual} != {expected}")
if drift:
    print("BUNDLE DRIFT:\n" + "\n".join(drift)); sys.exit(1)
print("bundle verified: all bound hashes match")
PY

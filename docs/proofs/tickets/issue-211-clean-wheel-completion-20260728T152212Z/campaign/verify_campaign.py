#!/usr/bin/env python3
"""Self-validating index for the #211 acceptance campaign bundle."""
import hashlib
import json
import sys
from pathlib import Path

d = Path(__file__).resolve().parent
idx = json.loads((d / "campaign-index.json").read_text())
drift = []
for name, expected in idx["bound_artifacts"].items():
    f = d / (name + ".json")
    actual = ("sha256:" + hashlib.sha256(f.read_bytes()).hexdigest()) if f.exists() else "MISSING"
    if actual != expected:
        drift.append(f"{name}: {actual} != {expected}")
if drift:
    print("CAMPAIGN DRIFT:")
    print("\n".join(drift))
    sys.exit(1)
print(f"campaign bundle verified: {len(idx['bound_artifacts'])} artifacts match")

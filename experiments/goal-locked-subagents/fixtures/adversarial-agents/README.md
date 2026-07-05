# Adversarial Agent Fixtures

This directory is reserved for adversarial Tau fixtures used by
`scripts/run-zero-trust-redteam.py`.

The first red-team suite builds its malicious inputs in code so it can call the
current Tau validators directly. Future fixtures should be added here only when
they are consumed by a real Tau gate and the red-team receipt records the
fixture path/hash.

Do not add fake agent transcripts as proof. A fixture is useful only when Tau
validates it and emits a blocked or passing receipt.

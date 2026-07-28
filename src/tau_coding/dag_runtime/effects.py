"""Accepted-effect identity, ownership, and reconciliation (contract 8.A3).

At-least-once-with-reconciliation, stated openly: exactly-once is claimed only
for database admission (uniqueness), never for external calls. The lifecycle:

    declare (intent row commits BEFORE the external call)
      -> acquire (transactional lease: owner_attempt_id, token, expiry,
         state_version - two workers can never both own an intent)
      -> succeeded (requires external evidence: target identity plus an
         operation id, response digest, or read-back result)
      -> accepted (only after the owning attempt's receipt admission)

A crash between the external call and `succeeded`/`accepted` leaves the row
where reconciliation finds it: `mark_uncertain_effects` moves stale-leased
intents to `uncertain` and the caller must BLOCK the run pending an
effect-type reconciliation handler. Effect types without a registered handler
must be declared ``manual_reconciliation_only`` at declaration time or the
declaration is refused - an unreconcilable effect is a design decision, never
a default.

Effect identity is `(effect_type, effect_scope, effect_key)` and survives new
runs; run/attempt ids are provenance columns only. Receipt identity (per
attempt) remains separate: many attempts may reference one logical effect.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.run_store import (
    DagRunLease,
    DagRunStoreError,
    SqliteDagRunStore,
)

EFFECT_STATES = ("intent", "succeeded", "accepted", "uncertain", "reconciled")

_EFFECTS_DDL = """
CREATE TABLE IF NOT EXISTS accepted_effects (
    effect_type TEXT NOT NULL,
    effect_scope TEXT NOT NULL,
    effect_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('intent', 'succeeded', 'accepted', 'uncertain', 'reconciled')
    ),
    state_version INTEGER NOT NULL DEFAULT 1,
    reconciliation TEXT NOT NULL CHECK (
        reconciliation IN ('handler', 'manual_reconciliation_only')
    ),
    owner_attempt_id TEXT,
    lease_token TEXT,
    lease_expires_at_ms INTEGER,
    declared_run_id TEXT NOT NULL,
    success_evidence_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (effect_type, effect_scope, effect_key)
);
"""


class EffectStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class EffectHandle:
    effect_type: str
    effect_scope: str
    effect_key: str
    lease_token: str
    state_version: int


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


class EffectLedger:
    """Store-backed accepted-effect lifecycle. One instance per run store."""

    def __init__(self, store: SqliteDagRunStore) -> None:
        self._store = store
        store._connection.executescript(_EFFECTS_DDL)

    def declare(
        self,
        lease: DagRunLease,
        *,
        effect_type: str,
        effect_scope: str,
        effect_key: str,
        reconciliation: str,
    ) -> None:
        """Commit the intent row. Must precede the external call.

        Idempotent for an existing row in any state - a redeclared identity is
        the same logical effect, not a new one.
        """

        if reconciliation not in ("handler", "manual_reconciliation_only"):
            raise EffectStoreError(
                "reconciliation must be 'handler' or 'manual_reconciliation_only'"
            )
        with self._store._transaction():
            self._store._assert_lease(lease)
            self._store._connection.execute(
                """INSERT INTO accepted_effects(
                    effect_type, effect_scope, effect_key, state, reconciliation,
                    declared_run_id, updated_at
                ) VALUES (?, ?, ?, 'intent', ?, ?, ?)
                ON CONFLICT(effect_type, effect_scope, effect_key) DO NOTHING""",
                (effect_type, effect_scope, effect_key, reconciliation,
                 lease.run_id, _now_iso()),
            )

    def acquire(
        self,
        lease: DagRunLease,
        *,
        effect_type: str,
        effect_scope: str,
        effect_key: str,
        owner_attempt_id: str,
        ttl_seconds: float = 60.0,
    ) -> EffectHandle | None:
        """Transactional ownership: exactly one worker wins; losers get None."""

        token = secrets.token_hex(16)
        expires = _now_ms() + int(ttl_seconds * 1000)
        with self._store._transaction():
            self._store._assert_lease(lease)
            cursor = self._store._connection.execute(
                """UPDATE accepted_effects
                   SET owner_attempt_id = ?, lease_token = ?,
                       lease_expires_at_ms = ?, state_version = state_version + 1,
                       updated_at = ?
                   WHERE effect_type = ? AND effect_scope = ? AND effect_key = ?
                     AND state IN ('intent', 'uncertain')
                     AND (owner_attempt_id IS NULL OR lease_expires_at_ms < ?)""",
                (owner_attempt_id, token, expires, _now_iso(),
                 effect_type, effect_scope, effect_key, _now_ms()),
            )
            if cursor.rowcount != 1:
                return None
            row = self._store._connection.execute(
                """SELECT state_version FROM accepted_effects
                   WHERE effect_type = ? AND effect_scope = ? AND effect_key = ?""",
                (effect_type, effect_scope, effect_key),
            ).fetchone()
            return EffectHandle(
                effect_type, effect_scope, effect_key, token, int(row["state_version"])
            )

    def mark_succeeded(
        self,
        lease: DagRunLease,
        handle: EffectHandle,
        *,
        evidence: dict[str, Any],
    ) -> None:
        """Record external success. Refused without external evidence."""

        required = {"target_identity"}
        proof_fields = {"operation_id", "response_digest", "read_back"}
        if not required.issubset(evidence) or not (proof_fields & set(evidence)):
            raise EffectStoreError(
                "success evidence requires target_identity plus one of "
                "operation_id/response_digest/read_back - a local assertion "
                "is not success"
            )
        self._transition(lease, handle, from_states=("intent",), to_state="succeeded",
                         evidence=evidence)

    def mark_accepted(self, lease: DagRunLease, handle: EffectHandle) -> None:
        self._transition(lease, handle, from_states=("succeeded",), to_state="accepted",
                         evidence=None)

    def mark_reconciled(self, lease: DagRunLease, handle: EffectHandle) -> None:
        self._transition(lease, handle, from_states=("uncertain",), to_state="reconciled",
                         evidence=None)

    def _transition(
        self,
        lease: DagRunLease,
        handle: EffectHandle,
        *,
        from_states: tuple[str, ...],
        to_state: str,
        evidence: dict[str, Any] | None,
    ) -> None:
        import json as _json

        placeholders = ",".join("?" for _ in from_states)
        with self._store._transaction():
            self._store._assert_lease(lease)
            cursor = self._store._connection.execute(
                f"""UPDATE accepted_effects
                    SET state = ?, state_version = state_version + 1,
                        success_evidence_json = COALESCE(?, success_evidence_json),
                        updated_at = ?
                    WHERE effect_type = ? AND effect_scope = ? AND effect_key = ?
                      AND lease_token = ? AND state IN ({placeholders})""",
                (to_state,
                 _json.dumps(evidence, sort_keys=True) if evidence else None,
                 _now_iso(), handle.effect_type, handle.effect_scope,
                 handle.effect_key, handle.lease_token, *from_states),
            )
            if cursor.rowcount != 1:
                raise EffectStoreError(
                    f"transition to {to_state} refused: lost lease or wrong state "
                    f"for {handle.effect_type}/{handle.effect_scope}/{handle.effect_key}"
                )

    def mark_uncertain_effects(self, lease: DagRunLease) -> list[dict[str, Any]]:
        """Reconciliation sweep: owned-but-unfinished effects become uncertain.

        Returns the rows moved; a non-empty return means the caller must BLOCK
        the run pending handler or manual reconciliation. Never guesses whether
        the external call happened - that is the handler's job.
        """

        with self._store._transaction():
            self._store._assert_lease(lease)
            rows = self._store._connection.execute(
                """SELECT effect_type, effect_scope, effect_key, reconciliation
                   FROM accepted_effects
                   WHERE state = 'intent' AND owner_attempt_id IS NOT NULL
                     AND lease_expires_at_ms < ?""",
                (_now_ms(),),
            ).fetchall()
            moved = []
            for row in rows:
                self._store._connection.execute(
                    """UPDATE accepted_effects
                       SET state = 'uncertain', state_version = state_version + 1,
                           updated_at = ?
                       WHERE effect_type = ? AND effect_scope = ? AND effect_key = ?""",
                    (_now_iso(), row["effect_type"], row["effect_scope"],
                     row["effect_key"]),
                )
                moved.append(dict(row))
            return moved

    def list_effects(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._store._connection.execute(
                "SELECT * FROM accepted_effects ORDER BY effect_type, effect_scope, effect_key"
            ).fetchall()
        ]


__all__ = [
    "EFFECT_STATES",
    "EffectHandle",
    "EffectLedger",
    "EffectStoreError",
    "DagRunStoreError",
]

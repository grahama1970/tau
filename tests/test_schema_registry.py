from __future__ import annotations

import pytest

from tau_coding.schema_registry import (
    SchemaVersionSkewError,
    parse_schema_id,
    require_schema_compatible,
    require_schema_in,
)


def test_parse_tau_schema_id_returns_name_and_version() -> None:
    parsed = parse_schema_id("tau.runtime_requirement.v12")

    assert parsed.namespace == "tau"
    assert parsed.name == "runtime_requirement"
    assert parsed.version == 12
    assert parsed.family == "tau.runtime_requirement"


def test_schema_acceptance_reports_same_family_version_skew() -> None:
    with pytest.raises(
        SchemaVersionSkewError,
        match=(
            r"schema version skew: expected=tau.runtime_requirement.v1 "
            r"actual=tau.runtime_requirement.v2"
        ),
    ):
        require_schema_compatible("tau.runtime_requirement.v2", "tau.runtime_requirement.v1")


def test_schema_acceptance_allows_registered_legacy_versions() -> None:
    require_schema_in(
        "tau.git_worktree_lease.v1",
        {"tau.git_worktree_lease.v1", "tau.git_worktree_lease.v2"},
        latest="tau.git_worktree_lease.v2",
    )

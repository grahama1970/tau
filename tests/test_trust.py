import json
from pathlib import Path

from tau_coding.paths import TauPaths
from tau_coding.trust import (
    ProjectTrustStore,
    ProjectTrustUpdate,
    get_project_trust_options,
    has_trust_requiring_project_resources,
)


def test_project_trust_store_inherits_and_clears_child_decisions(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / ".tau" / "trust.json")
    parent = tmp_path / "parent"
    child = parent / "project"
    child.mkdir(parents=True)

    assert store.get(child) is None
    store.set(parent, True)
    assert store.get(child) is True
    assert store.get_entry(child).path == parent.resolve()

    store.set(child, False)
    assert store.get(child) is False
    store.set_many((ProjectTrustUpdate(path=child, decision=None),))
    assert store.get(child) is True

    payload = json.loads(store.trust_path.read_text(encoding="utf-8"))
    assert payload == {str(parent.resolve()): True}


def test_project_trust_options_include_parent_and_untrust(tmp_path: Path) -> None:
    cwd = tmp_path / "workspace" / "project"
    cwd.mkdir(parents=True)

    options = get_project_trust_options(cwd)

    assert [option.label for option in options] == [
        "Trust",
        f"Trust parent folder ({cwd.parent.resolve()})",
        "Do not trust",
    ]
    assert options[1].updates[0].path == cwd.parent.resolve()
    assert options[1].updates[1].path == cwd.resolve()
    assert options[1].updates[1].decision is None


def test_detects_tau_and_agents_project_resources(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / "home" / ".tau", agents_home=tmp_path / "home" / ".agents")
    project = tmp_path / "project"
    project.mkdir()

    assert has_trust_requiring_project_resources(project, paths) is False

    paths.project_tau_dir(project).mkdir()
    (paths.project_tau_dir(project) / "AGENTS.md").write_text("project rules", encoding="utf-8")
    assert has_trust_requiring_project_resources(project, paths) is True

    (paths.project_tau_dir(project) / "AGENTS.md").unlink()
    paths.project_agents_skills_dir(project).mkdir(parents=True)
    assert has_trust_requiring_project_resources(project, paths) is True

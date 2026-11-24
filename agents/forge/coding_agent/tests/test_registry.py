import shutil
from collections import Counter

import pytest

from coding_agent.core.registry import (
    AGENT, DEFAULT_ROOT, DIVISIONS, PACKAGE_ROOT, ROLES, ROSTER, Registry, RegistryError, get_role,
    parse_front_matter,
)


def test_folders_define_the_whole_team():
    assert len(ROSTER) == 51
    assert Counter(r.division for r in ROSTER) == {
        "leadership": 6, "architecture": 6, "development": 11, "quality": 8,
        "security": 6, "devops": 8, "documentation": 6,
    }
    assert list(DIVISIONS) == ["leadership", "architecture", "development", "quality", "security", "devops",
                               "documentation"]
    for role in ROSTER:
        assert role.folder == DEFAULT_ROOT / role.division / role.id
        assert role.responsibility and role.instructions


def test_division_leads_and_agent_brief():
    assert {d.id: d.lead for d in DIVISIONS.values()} == {
        "leadership": "engineering_director", "architecture": "software_architect", "development": "tech_lead",
        "quality": "qa_lead", "security": "security_architect", "devops": "devops_engineer",
        "documentation": "technical_writer",
    }
    assert AGENT.lead == "engineering_director"
    assert dict(AGENT.attachments) == {"debugging_specialist": "development"}


def test_specialist_markdown_becomes_instructions_and_tools_load():
    pentest = get_role("penetration_testing_agent")
    assert pentest.requires_explicit_request and "Authorized scope only" in pentest.instructions
    auditor = get_role("code_security_auditor")
    assert auditor.has_tools and callable(auditor.tools().review)
    assert not get_role("python_developer").has_tools


def test_front_matter_values():
    data, body = parse_front_matter('---\nid: x\ntriggers: ["go ", "c#"]\norder: 3\nflag: true\n---\n# T\nbody',
                                    DEFAULT_ROOT)
    assert data == {"id": "x", "triggers": ["go ", "c#"], "order": 3, "flag": True}
    assert body == "# T\nbody"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "divisions"
    shutil.copytree(DEFAULT_ROOT, root)
    shutil.copy(PACKAGE_ROOT / "agent.md", tmp_path / "agent.md")
    return root


def test_copied_tree_loads(tree):
    assert len(Registry.load(tree).roles) == 51


def test_adding_a_specialist_is_just_a_folder(tree):
    folder = tree / "devops" / "terraform_engineer"
    folder.mkdir()
    (folder / "specialist.md").write_text(
        '---\nid: terraform_engineer\nname: Terraform Engineer\nkind: builder\n'
        'responsibility: Terraform modules\ntriggers: ["terraform"]\n---\n# Terraform Engineer\n- Modules\n',
        encoding="utf-8")
    registry = Registry.load(tree)
    assert registry.role("terraform_engineer").division == "devops"
    assert "terraform_engineer" in registry.division("devops").members


@pytest.mark.parametrize("mutate, message", [
    (lambda t: (t / "security" / "dependency_auditor" / "specialist.md").write_text("no front matter", encoding="utf-8"),
     "missing front matter"),
    (lambda t: (t / "security" / "dependency_auditor" / "specialist.md").write_text(
        "---\nid: wrong\nname: X\nkind: reviewer\nresponsibility: r\n---\n", encoding="utf-8"), "must match its folder"),
    (lambda t: (t / "security" / "dependency_auditor" / "specialist.md").write_text(
        "---\nid: dependency_auditor\nname: X\nkind: wizard\nresponsibility: r\n---\n", encoding="utf-8"),
     "unknown kind"),
    (lambda t: (t / "security" / "empty_folder").mkdir(), "without specialist.md"),
    (lambda t: (t / "security" / "division.md").write_text(
        "---\nid: security\nname: S\nlead: tech_lead\n---\n", encoding="utf-8"), "not a specialist in this division"),
    (lambda t: shutil.copytree(t / "security" / "dependency_auditor", t / "quality" / "dependency_auditor"),
     "appears in"),
])
def test_bad_folders_fail_loudly(tree, mutate, message):
    mutate(tree)
    with pytest.raises(RegistryError, match=message):
        Registry.load(tree)


def test_roles_have_valid_phase_and_tier():
    for role in ROLES.values():
        assert role.phase in {"analyze", "plan", "design", "build", "review"}
        assert role.tier in {"planning", "coding", "review"}

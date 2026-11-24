import shutil
from collections import Counter

import pytest

from coding_agent.core.registry import PACKAGE_ROOT, RegistryError, Tree, default_tree, parse_front_matter

TREE = default_tree()


def test_folders_define_the_whole_team():
    assert len(TREE.roster) == 51
    assert len(TREE.units) == 59  # agent + 7 divisions + 51 specialists
    assert Counter(r.division for r in TREE.roster) == {
        "leadership": 6, "architecture": 6, "development": 11, "quality": 8,
        "security": 6, "devops": 8, "documentation": 6,
    }
    assert [d.id for d in TREE.divisions] == ["leadership", "architecture", "development", "quality", "security",
                                              "devops", "documentation"]
    for role in TREE.roster:
        assert role.folder == PACKAGE_ROOT / "divisions" / role.division / role.id
        assert role.path == f"{role.division}/{role.id}"
        assert role.responsibility and role.instructions and role.level == "specialist"


def test_leads_attachments_and_lookup():
    assert {d.id: d.lead for d in TREE.divisions} == {
        "leadership": "engineering_director", "architecture": "software_architect", "development": "tech_lead",
        "quality": "qa_lead", "security": "security_architect", "devops": "devops_engineer",
        "documentation": "technical_writer",
    }
    assert TREE.root.lead == "engineering_director"
    assert dict(TREE.root.attachments) == {"debugging_specialist": "development"}
    assert TREE.get("python_developer") is TREE.get("development/python_developer")
    with pytest.raises(KeyError):
        TREE.get("wizard")


def test_unit_markdown_becomes_instructions_and_tools_load():
    pentest = TREE.get("penetration_testing_agent")
    assert pentest.requires_explicit_request and "Authorized scope only" in pentest.instructions
    auditor = TREE.get("code_security_auditor")
    assert auditor.has_tools and callable(auditor.tools().review)
    assert not TREE.get("python_developer").has_tools


def test_front_matter_values():
    data, body = parse_front_matter('---\nid: x\ntriggers: ["go ", "c#"]\norder: 3\nflag: true\n---\n# T\nbody',
                                    PACKAGE_ROOT)
    assert data == {"id": "x", "triggers": ["go ", "c#"], "order": 3, "flag": True}
    assert body == "# T\nbody"


# ---------------------------------------------------------------- tree copies
@pytest.fixture
def pkg(tmp_path):
    """A writable copy of the packaged unit tree."""
    root = tmp_path / "coding_agent"
    shutil.copytree(PACKAGE_ROOT / "divisions", root / "divisions")
    shutil.copy(PACKAGE_ROOT / "unit.md", root / "unit.md")
    return root


def write_unit(folder, front: str, body: str = "# Unit\nDoes things.") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "unit.md").write_text(f"---\n{front.strip()}\n---\n{body}\n", encoding="utf-8")


def test_copied_tree_loads(pkg):
    assert len(Tree.load(pkg).roster) == 51


def test_adding_a_specialist_is_just_a_folder(pkg):
    write_unit(pkg / "divisions" / "devops" / "terraform_engineer",
               'id: terraform_engineer\nname: Terraform Engineer\nkind: builder\nresponsibility: Terraform\n'
               'triggers: ["terraform"]')
    tree = Tree.load(pkg)
    assert tree.get("terraform_engineer").division == "devops"
    assert "terraform_engineer" in tree.get("devops").members


def test_a_specialist_can_become_a_team(pkg):
    db = pkg / "divisions" / "development" / "database_developer"
    write_unit(db / "postgres_developer", 'id: postgres_developer\nname: Postgres Developer\nkind: builder\n'
               'responsibility: PostgreSQL schemas and queries\ntriggers: ["postgres", "postgresql"]\n'
               'model: qwen2.5-coder:14b')
    write_unit(db / "qdrant_developer", 'id: qdrant_developer\nname: Qdrant Developer\nkind: builder\n'
               'responsibility: Qdrant collections\ntriggers: ["qdrant"]')
    (db / "examples").mkdir()  # non-unit folders inside a specialist are fine
    tree = Tree.load(pkg)
    team = tree.get("database_developer")
    assert team.is_team and team.members == ("postgres_developer", "qdrant_developer")
    postgres = tree.get("development/database_developer/postgres_developer")
    assert postgres.level == "specialist" and postgres.division == "development" and postgres.depth == 3
    assert tree.team_of(postgres) is team
    assert postgres not in tree.roster  # reached through its team, not picked by the Director


def test_models_are_inherited_downward(pkg):
    db = pkg / "divisions" / "development" / "database_developer"
    text = (db / "unit.md").read_text(encoding="utf-8").replace("kind: builder", "kind: builder\nmodel: team-model")
    (db / "unit.md").write_text(text, encoding="utf-8")
    write_unit(db / "postgres_developer", 'id: postgres_developer\nname: Postgres Developer\nkind: builder\n'
               'responsibility: PostgreSQL\nmodel: pg-model')
    write_unit(db / "qdrant_developer", 'id: qdrant_developer\nname: Qdrant Developer\nkind: builder\n'
               'responsibility: Qdrant')
    tree = Tree.load(pkg)
    assert tree.effective_model(tree.get("postgres_developer")) == ("pg-model", "development/database_developer/postgres_developer")
    assert tree.effective_model(tree.get("qdrant_developer")) == ("team-model", "development/database_developer")
    assert tree.effective_model(tree.get("python_developer")) == (None, None)  # falls back to the .env tier


@pytest.mark.parametrize("mutate, message", [
    (lambda p: (p / "divisions/security/dependency_auditor/unit.md").write_text("no front matter", encoding="utf-8"),
     "missing front matter"),
    (lambda p: write_unit(p / "divisions/security/dependency_auditor",
                          "id: wrong\nname: X\nkind: reviewer\nresponsibility: r"), "must match its folder"),
    (lambda p: write_unit(p / "divisions/security/dependency_auditor",
                          "id: dependency_auditor\nname: X\nkind: wizard\nresponsibility: r"), "'kind' must be one of"),
    (lambda p: (p / "divisions/security/empty_folder").mkdir(), "has no unit.md"),
    (lambda p: write_unit(p / "divisions/security", "id: security\nname: S\nlead: tech_lead"),
     "not a unit directly under"),
    (lambda p: shutil.copytree(p / "divisions/security/dependency_auditor", p / "divisions/quality/dependency_auditor"),
     "already used by"),
    (lambda p: write_unit(p / "divisions/security/dependency_auditor",
                          "id: dependency_auditor\nname: X\nkind: reviewer\nresponsibility: r\nendpoint: ftp://x"),
     "http"),
    (lambda p: write_unit(p / "divisions/security", "id: security\nname: S\nlead: security_architect\nkind: builder"),
     "only specialists have"),
])
def test_bad_units_fail_loudly(pkg, mutate, message):
    mutate(pkg)
    with pytest.raises(RegistryError, match=message):
        Tree.load(pkg)


def test_describe_reports_models_and_endpoints():
    node = TREE.describe(TREE.get("security"))
    auditor = next(u for u in node["units"] if u["id"] == "dependency_auditor")
    assert auditor["has_tools"] and auditor["effective_model"] == ".env review tier"
    assert auditor["endpoint"] is None and auditor["units"] == []

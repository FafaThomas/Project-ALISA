import json
from coding_agent.architecture_validator import ArchitectureDesignValidator
from coding_agent.engineering_spec import ArchitectureModule, Requirement
from coding_agent.espec_generator import EngineeringSpecificationGenerator


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        return self.payloads.pop(0)


def _module(module_id, name, responsibility, *paths, depends_on=()):
    return ArchitectureModule(
        module_id=module_id,
        name=name,
        responsibility=responsibility,
        depends_on=tuple(depends_on),
        file_paths=tuple(paths),
    )


def test_rejects_collapsed_domain_and_presentation_when_separation_is_required():
    requirement = Requirement(
        "REQ-001",
        "Keep game logic independently testable without Pygame rendering.",
    )
    modules = [
        _module(
            "MOD-001",
            "Root Repository",
            "Contains core game logic, rendering, entry point, dependencies, documentation, and automated tests",
            "game.py",
            "renderer.py",
            "main.py",
            "requirements.txt",
            "README.md",
            "tests/test_game.py",
        )
    ]

    report = ArchitectureDesignValidator().validate(
        modules,
        objective="Build a Snake game",
        requirements=[requirement],
    )

    assert not report.valid
    assert any("combines distinct responsibilities" in issue for issue in report.issues)
    assert any("omnibus responsibility" in issue for issue in report.issues)


def test_accepts_separated_runtime_modules_and_project_support_files():
    requirement = Requirement(
        "REQ-001",
        "Keep game logic independently testable without Pygame rendering.",
    )
    modules = [
        _module("MOD-001", "Game Logic", "Owns game state and rules", "game.py"),
        _module("MOD-002", "Renderer", "Owns Pygame presentation", "renderer.py", depends_on=("MOD-001",)),
        _module("MOD-003", "Application", "Coordinates startup and the game loop", "main.py", depends_on=("MOD-001", "MOD-002")),
        _module("MOD-004", "Tests", "Verifies game mechanics without a display", "tests/test_game.py", depends_on=("MOD-001",)),
        _module("MOD-005", "Project Support", "Maintains dependencies and user documentation", "requirements.txt", "README.md"),
    ]

    report = ArchitectureDesignValidator().validate(
        modules,
        objective="Build a Snake game",
        requirements=[requirement],
    )

    assert report.valid
    assert report.issues == ()


def test_rejects_same_file_owned_by_multiple_modules():
    modules = [
        _module("MOD-001", "Game", "Owns game rules", "game.py"),
        _module("MOD-002", "Application", "Coordinates startup", "game.py", "main.py"),
    ]

    report = ArchitectureDesignValidator().validate(modules)

    assert not report.valid
    assert any("assigned to multiple architecture modules" in issue for issue in report.issues)


def test_does_not_force_separation_when_task_does_not_request_it():
    modules = [
        _module("MOD-001", "Application", "Implements a small cohesive game application", "game.py", "renderer.py"),
    ]

    report = ArchitectureDesignValidator().validate(
        modules,
        objective="Build a small game",
    )

    assert report.valid


def test_architecture_quality_findings_are_warnings_not_stage_failures(tmp_path):
    # Design-quality findings no longer fail or retry the architecture stage.
    requirement = Requirement(
        "REQ-001",
        "Keep game logic independently testable without Pygame rendering.",
    )
    catch_all = {
        "architecture": [{
            "module_id": "MOD-001",
            "name": "Root Repository",
            "responsibility": "Contains game logic, rendering, entry point, dependencies, documentation, and tests",
            "depends_on": [],
            "file_paths": ["game.py", "renderer.py", "main.py", "requirements.txt", "README.md", "tests/test_game.py"],
        }]
    }
    client = FakeClient([catch_all])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2, diagnostics_dir=tmp_path)
    trace = []

    modules = generator._stage(
        "architecture",
        trace,
        "Design a modular architecture.",
        lambda data: generator._parse_and_validate_architecture(
            data, [requirement], objective="Build a Snake game"
        ),
    )

    assert len(modules) == 1
    assert trace[0].attempts == 1
    assert trace[0].success is True
    warnings = json.loads((tmp_path / "architecture_design_warnings.json").read_text(encoding="utf-8"))
    assert any("combines distinct responsibilities" in item or "omnibus" in item for item in warnings["warnings"])


def test_duplicate_architecture_paths_are_kept_in_the_first_module(tmp_path):
    # Regression: the burger shop spec failed because Menu.jsx and friends were listed under two modules.
    data = {
        "architecture": [
            {"module_id": "MOD-003", "name": "Frontend App", "responsibility": "Starts the React app",
             "depends_on": [], "file_paths": ["frontend/src/main.jsx", "frontend/src/Menu.jsx", "frontend/src/Cart.jsx"]},
            {"module_id": "MOD-004", "name": "Frontend Components", "responsibility": "Menu and cart components",
             "depends_on": ["MOD-003"], "file_paths": ["frontend/src/Menu.jsx", "frontend/src/Cart.jsx", "frontend/src/OrderForm.jsx"]},
        ]
    }
    generator = EngineeringSpecificationGenerator(FakeClient([]), diagnostics_dir=tmp_path)

    modules = generator._parse_and_validate_architecture(data, [Requirement("REQ-001", "Build the frontend.")])

    paths = {module.module_id: module.file_paths for module in modules}
    assert paths["MOD-003"] == ("frontend/src/main.jsx", "frontend/src/Menu.jsx", "frontend/src/Cart.jsx")
    assert paths["MOD-004"] == ("frontend/src/OrderForm.jsx",)
    repairs = json.loads((tmp_path / "architecture_path_dedupe.json").read_text(encoding="utf-8"))
    assert {item["path"] for item in repairs} == {"frontend/src/Menu.jsx", "frontend/src/Cart.jsx"}


def test_generation_persists_separate_architecture_and_implementation_plan_artifacts(tmp_path):
    payloads = [
        {"requirements": [{
            "requirement_id": "REQ-001",
            "description": "Create README.md",
            "kind": "functional",
            "acceptance_criteria": ["README.md documents setup"],
            "priority": "normal",
        }]},
        {"architecture": [{
            "module_id": "MOD-001",
            "name": "Project Documentation",
            "responsibility": "Owns project documentation",
            "depends_on": [],
            "file_paths": ["README.md"],
        }]},
        {"contracts": []},
        {"implementation_units": [{
            "unit_id": "UNIT-001",
            "title": "Create README",
            "target_path": "README.md",
            "kind": "documentation",
            "depends_on": [],
            "satisfies": ["REQ-001"],
            "implements": [],
            "acceptance_criteria": ["README.md documents setup"],
        }], "verification": [{
            "criterion_id": "VER-001",
            "description": "Review README setup documentation",
            "kind": "manual",
            "required": True,
            "command": [],
            "covers": ["REQ-001"],
        }]},
    ]
    generator = EngineeringSpecificationGenerator(
        FakeClient(payloads), diagnostics_dir=tmp_path
    )

    specification = generator.generate(
        task_id="artifact-test",
        objective="Create a README",
        workspace="D:/workspace",
    )

    architecture_artifact = (tmp_path / "architecture_design.json").read_text(encoding="utf-8")
    plan_artifact = (tmp_path / "implementation_plan.json").read_text(encoding="utf-8")
    assert '"phase": "architecture_design"' in architecture_artifact
    assert '"phase": "implementation_planning"' in plan_artifact
    assert '"status": "validated"' in plan_artifact
    assert specification.status.value == "validated"


def test_generic_scope_exclusion_does_not_trigger_architecture_separation():
    modules = [
        _module("MOD-001", "Game", "Implements cohesive game behavior and presentation", "game.py", "renderer.py"),
    ]

    report = ArchitectureDesignValidator().validate(
        modules,
        objective="Build a small game without adding database features",
    )

    assert report.valid

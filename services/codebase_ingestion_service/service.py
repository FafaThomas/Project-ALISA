from pathlib import Path
from collectors.workspace_collector import WorkspaceCollector
from collectors.source_collector import SourceCollector

class CodebaseIngestionService:

    def __init__(self):

        self.workspace = WorkspaceCollector()
        self.source = SourceCollector()

    def create_project(self, project_path: str | Path):

        workspace = self.workspace.collect(project_path)

        source_collection = self.source.collect(workspace)

        print()

        print("=" * 60)
        print(workspace.name)
        print("=" * 60)

        print()

        print(f"Source Files : {source_collection.total_files}")

        # ---------------------------------------------------------
        # Create output directory
        # ---------------------------------------------------------

        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)

        output_file = output_dir / "source_collection.json"

        # ---------------------------------------------------------
        # Save JSON
        # ---------------------------------------------------------

        output_file.write_text(
            source_collection.model_dump_json(indent=4),
            encoding="utf-8"
        )

        print()
        print(f"Saved to: {output_file.resolve()}")
        

    def update_project(self, project_path: str | Path):

        project_path = Path(project_path)

        print(f"Updating project: {project_path}")

    def rebuild_project(self, project_path: str | Path):

        project_path = Path(project_path)

        print(f"Rebuilding project: {project_path}")

    def delete_project(self, project_name: str):

        print(f"Deleting project: {project_name}")

    def get_project(self, project_name: str):

        print(f"Getting project: {project_name}")

    def list_projects(self):

        print("Listing projects...")
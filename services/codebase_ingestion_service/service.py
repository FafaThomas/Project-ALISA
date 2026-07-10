from pathlib import Path
import json

from collectors.workspace_collector import WorkspaceCollector
from collectors.source_collector import SourceCollector
from collectors.manifest_collector import ManifestCollector

from models.project_context import ProjectContext


class CodebaseIngestionService:

    def __init__(self):

        self.workspace = WorkspaceCollector()

        self.source = SourceCollector()

        self.manifest = ManifestCollector()

    def create_project(self, project_path: str | Path):

        workspace = self.workspace.collect(project_path)

        source_collection = self.source.collect(workspace)

        manifest_collection = self.manifest.collect(workspace)

        context = ProjectContext(

            workspace=workspace,

            source_collection=source_collection,

            manifest_collection=manifest_collection,

        )

        self.save_project_context(context)

        print()

        print("=" * 60)
        print(workspace.name)
        print("=" * 60)

        print()

        print(f"Source Files      : {context.source_collection.total_files}")

        print(f"Manifest Files    : {context.manifest_collection.total_documents}")

        print()

        return context

    def save_project_context(self, context: ProjectContext):

        output = Path("output")

        output.mkdir(exist_ok=True)

        output_file = output / "project_context.json"

        output_file.write_text(

            context.model_dump_json(

                indent=4

            ),

            encoding="utf-8"

        )

    def update_project(self, project_path: str | Path):

        print("Not implemented.")

    def rebuild_project(self, project_path: str | Path):

        print("Not implemented.")

    def delete_project(self, project_name: str):

        print("Not implemented.")

    def get_project(self, project_name: str):

        print("Not implemented.")

    def list_projects(self):

        print("Not implemented.")
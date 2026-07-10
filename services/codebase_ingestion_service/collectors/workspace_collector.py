from pathlib import Path

from models.workspace import Workspace


class WorkspaceCollector:

    def collect(self, project_path: str | Path) -> Workspace:

        project_path = Path(project_path).resolve()

        if not project_path.exists():
            raise FileNotFoundError(project_path)

        if not project_path.is_dir():
            raise NotADirectoryError(project_path)

        return Workspace(

            name=project_path.name,

            path=project_path,

        )
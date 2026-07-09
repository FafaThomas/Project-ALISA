from pathlib import Path


class WorkspaceCollector:

    def collect(self, workspace: str | Path):

        workspace = Path(workspace)

        print()

        print("=" * 60)
        print(workspace.name)
        print("=" * 60)

        return workspace
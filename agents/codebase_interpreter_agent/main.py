from scanner.workspace_scanner import WorkspaceScanner


PROJECT = r"D:\Organized Projects\For Portfolio\Project-ALISA"

scanner = WorkspaceScanner(PROJECT)

files = scanner.discover_python_files()

print("=" * 60)
print(f"Found {len(files)} Python files")
print("=" * 60)

for file in files:
    print(file)
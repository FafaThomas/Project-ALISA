"""Measure CodeIndexer wall time, throughput, and peak Python allocations.

Usage: python scripts/benchmark_index.py PATH_TO_WORKSPACE [--max-files 2000]
"""
from __future__ import annotations
import argparse
import json
import time
import tracemalloc
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from coding_agent.code_index import CodeIndexer
from coding_agent.workspace import Workspace

parser = argparse.ArgumentParser()
parser.add_argument("workspace", type=Path)
parser.add_argument("--max-files", type=int, default=2000)
parser.add_argument("--repeat", type=int, default=3)
args = parser.parse_args()
if args.repeat < 1:
    parser.error("--repeat must be at least 1")

indexer = CodeIndexer(Workspace(args.workspace), max_files=args.max_files)
runs = []
index = None
for run_number in range(args.repeat):
    tracemalloc.start()
    started = time.perf_counter()
    index = indexer.build()
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    runs.append({
        "run": run_number + 1,
        "elapsed_seconds": round(elapsed, 4),
        "peak_traced_memory_mb": round(peak / (1024 * 1024), 2),
        "files_per_second": round(index.indexed_files / elapsed, 2) if elapsed else None,
    })

result = {
    "workspace": str(args.workspace.resolve()),
    "runs": runs,
    "discovered_files": index.discovered_files,
    "indexed_files": index.indexed_files,
    "failed_files": index.failed_files,
    "languages": {language: sum(m.language == language for m in index.modules) for language in sorted({m.language for m in index.modules})},
    "relationships": len(index.relationships),
    "note": "Each run rebuilds the full index; this is not an incremental-index benchmark.",
}
print(json.dumps(result, indent=2))

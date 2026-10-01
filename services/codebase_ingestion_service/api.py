from pathlib import Path
import json
import shutil
import subprocess
import tempfile

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from service import CodebaseIngestionService


app = FastAPI(
    title="ALISA Codebase Ingestion Service",
    version="0.1.0",
)


class IngestRequest(BaseModel):
    repository_url: str
    ref: str = "main"


ingestion = CodebaseIngestionService()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/ingest")
def ingest(request: IngestRequest):
    work_dir = Path(tempfile.mkdtemp(prefix="alisa-ingest-"))

    try:
        project_dir = work_dir / "project"

        # Clone repository
        subprocess.run(
            [
                "git",
                "clone",
                "--branch",
                request.ref,
                "--single-branch",
                request.repository_url,
                str(project_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        # Persistent output location
        output_dir = STORAGE_ROOT / project_dir.name
        output_dir.mkdir(parents=True, exist_ok=True)

        # Run ingestion
        ingestion.create_project(
            project_dir,
            output_dir,
        )

        return {
            "status": "success",
            "repository": request.repository_url,
            "ref": request.ref,
            "output_path": str(output_dir),
            "files": [
                "parsed_documents.json",
                "dependency_graph.json",
                "call_graph.json",
                "project_context.json",
            ],
        }

    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Git clone failed: {e.stderr}",
        )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

        

import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from config import UPLOAD_DIR, MAX_UPLOAD_SIZE_BYTES, CHUNK_SIZE


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ALISA File Upload Service",
    description="Receives files over HTTP and stores them locally.",
    version="1.0.0",
)


def get_safe_filename(filename: str | None) -> str:
    """Extract a filename without accepting directory paths."""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    # Normalize Windows and Unix path separators.
    safe_name = Path(filename.replace("\\", "/")).name

    if safe_name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename.")

    return safe_name


async def save_upload(upload: UploadFile) -> dict:
    """Stream an uploaded file to the configured storage directory."""
    filename = get_safe_filename(upload.filename)
    destination = UPLOAD_DIR / filename
    bytes_written = 0

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Exclusive creation prevents accidentally overwriting existing files.
        with destination.open("xb") as output:
            while True:
                chunk = await upload.read(CHUNK_SIZE)

                if not chunk:
                    break

                bytes_written += len(chunk)

                if bytes_written > MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {MAX_UPLOAD_SIZE_BYTES}-byte limit.",
                    )

                output.write(chunk)

    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail=f"A file named '{filename}' already exists.",
        )

    except HTTPException:
        destination.unlink(missing_ok=True)
        raise

    except Exception:
        destination.unlink(missing_ok=True)
        logger.exception("Failed to save uploaded file: %s", filename)
        raise HTTPException(
            status_code=500,
            detail="Failed to save uploaded file.",
        )

    finally:
        await upload.close()

    logger.info("Saved %s (%d bytes)", filename, bytes_written)

    return {
        "filename": filename,
        "stored_path": str(destination),
        "size_bytes": bytes_written,
    }


@app.get("/health")
def health_check() -> dict:
    """Report service status."""
    return {
        "status": "healthy",
        "service": "file_upload_service",
    }


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """Upload one file."""
    result = await save_upload(file)

    return {
        "status": "success",
        **result,
    }


@app.post("/upload/batch")
async def upload_files(files: list[UploadFile] = File(...)) -> dict:
    """Upload multiple files in one request."""
    results = []

    for upload in files:
        try:
            result = await save_upload(upload)
            results.append({
                "status": "success",
                **result,
            })

        except HTTPException as exc:
            results.append({
                "status": "failed",
                "filename": upload.filename,
                "error": exc.detail,
                "status_code": exc.status_code,
            })

    failed_count = sum(
        result["status"] == "failed" for result in results
    )

    return {
        "status": (
            "success" if failed_count == 0
            else "partial_success" if failed_count < len(results)
            else "failed"
        ),
        "total_files": len(results),
        "successful_files": len(results) - failed_count,
        "failed_files": failed_count,
        "results": results,
    }
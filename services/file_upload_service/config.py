from pathlib import Path
import os


# Project-ALISA root directory
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Default storage location, configurable through an environment variable
UPLOAD_DIR = Path(
    os.getenv(
        "ALISA_UPLOAD_DIR",
        str(PROJECT_ROOT / "storage" / "uploads"),
    )
).expanduser().resolve()

# Maximum allowed size for an individual file: 100 MB
MAX_UPLOAD_SIZE_BYTES = int(
    os.getenv("ALISA_MAX_UPLOAD_SIZE", str(100 * 1024 * 1024))
)

# Read and write files in 1 MB chunks
CHUNK_SIZE = 1024 * 1024
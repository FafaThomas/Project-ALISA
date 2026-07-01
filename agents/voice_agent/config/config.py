from pathlib import Path
from dotenv import load_dotenv
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

# ==========================
# Hardware
# ==========================

DEVICE = os.getenv("DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("COMPUTE_TYPE", "float16")

# ==========================
# Models
# ==========================

WHISPER_MODEL = os.getenv(
    "WHISPER_MODEL",
    "large-v3-turbo"
)

XTTS_MODEL = os.getenv(
    "XTTS_MODEL",
    "tts_models/multilingual/multi-dataset/xtts_v2"
)

# ==========================
# Cache
# ==========================

CACHE_DIR = PROJECT_ROOT / "cache"

WHISPER_CACHE = CACHE_DIR / "whisper"
XTTS_CACHE = CACHE_DIR / "xtts"

WHISPER_CACHE.mkdir(parents=True, exist_ok=True)
XTTS_CACHE.mkdir(parents=True, exist_ok=True)
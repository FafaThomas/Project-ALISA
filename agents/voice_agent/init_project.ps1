# ==========================================
# ALISA Voice Agent Project Initializer
# ==========================================

Write-Host "Creating ALISA Voice Agent project structure..." -ForegroundColor Cyan

# -----------------------------
# Folders
# -----------------------------

$folders = @(
    "config",

    "core",

    "input",

    "stt",

    "tts",

    "models",

    "websocket",

    "utils",

    "tests",

    "cache",
    "cache\whisper",
    "cache\xtts",

    "logs",

    "audio",
    "audio\input",
    "audio\output",
    "audio\temp"
)

foreach ($folder in $folders) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}

# -----------------------------
# Python Packages
# -----------------------------

$packages = @(
    "config",
    "core",
    "input",
    "stt",
    "tts",
    "models",
    "websocket",
    "utils",
    "tests"
)

foreach ($package in $packages) {
    New-Item -ItemType File -Force "$package\__init__.py" | Out-Null
}

# -----------------------------
# Python Files
# -----------------------------

$files = @(

    # Root
    "main.py",
    "requirements.txt",
    ".env",
    ".gitignore",
    "README.md",

    # Config
    "config\config.py",
    "config\settings.yaml",

    # Core
    "core\pipeline.py",
    "core\events.py",
    "core\session.py",
    "core\state.py",

    # Input
    "input\microphone.py",
    "input\recorder.py",
    "input\vad.py",
    "input\streamer.py",

    # STT
    "stt\whisper.py",
    "stt\transcript.py",
    "stt\language.py",

    # TTS
    "tts\xtts.py",
    "tts\synthesizer.py",
    "tts\playback.py",

    # Models
    "models\whisper_manager.py",
    "models\xtts_manager.py",

    # WebSocket
    "websocket\client.py",
    "websocket\server.py",

    # Utils
    "utils\audio.py",
    "utils\logger.py",
    "utils\timers.py"
)

foreach ($file in $files) {
    New-Item -ItemType File -Force -Path $file | Out-Null
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host " Voice Agent project initialized!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""

tree /F
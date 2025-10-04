# =========================================
# ALISA AI Core Startup Script (PowerShell)
# =========================================

Write-Host "🚀 Starting ALISA AI Core..."

# Activate the virtual environment
& .\venv\Scripts\Activate.ps1

# Set environment variables (optional)
$env:PYTHONPATH = "$PSScriptRoot"

# Run the FastAPI server
Write-Host "✅ Virtual environment activated."
Write-Host "🧠 Launching FastAPI on http://127.0.0.1:5000"

uvicorn core.app:app --host 0.0.0.0 --port 5000 --reload

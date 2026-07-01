# ============================================
# Career Agent Project Initializer
# ============================================

Write-Host "Creating Career Agent project structure..."

# -------------------------
# Directories
# -------------------------

$directories = @(
    "app",
    "app\workers",
    "app\rag",
    "app\prompts",
    "app\tools",
    "app\models",
    "app\database",

    "knowledge",
    "knowledge\resumes",
    "knowledge\certifications",
    "knowledge\projects",
    "knowledge\github",
    "knowledge\employment",
    "knowledge\education",
    "knowledge\profile",

    "tests"
)

foreach ($dir in $directories) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

# -------------------------
# Files
# -------------------------

$files = @(

    # Root
    ".env",
    "README.md",
    "requirements.txt",
    "main.py",

    # App
    "app\__init__.py",
    "app\graph.py",
    "app\state.py",
    "app\config.py",
    "app\api.py",

    # Workers
    "app\workers\__init__.py",
    "app\workers\scout.py",
    "app\workers\parser.py",
    "app\workers\matcher.py",
    "app\workers\resume.py",
    "app\workers\cover_letter.py",
    "app\workers\apply.py",
    "app\workers\tracker.py",
    "app\workers\interview.py",

    # RAG
    "app\rag\__init__.py",
    "app\rag\ingest.py",
    "app\rag\retriever.py",
    "app\rag\vector_store.py",

    # Tools
    "app\tools\browser.py",
    "app\tools\linkedin.py",
    "app\tools\indeed.py",
    "app\tools\jobstreet.py",
    "app\tools\email.py",
    "app\tools\pdf.py",

    # Models
    "app\models\candidate.py",
    "app\models\company.py",
    "app\models\job.py",
    "app\models\application.py",
    "app\models\match.py",

    # Database
    "app\database\db.py",
    "app\database\schema.sql"
)

foreach ($file in $files) {

    if (!(Test-Path $file)) {
        New-Item -ItemType File -Path $file | Out-Null
    }
}

Write-Host ""
Write-Host "========================================"
Write-Host " Career Agent project initialized!"
Write-Host "========================================"
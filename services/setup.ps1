$root = "codebase_ingestion_service"

Write-Host ""
Write-Host "==============================================="
Write-Host " Codebase Ingestion Service Bootstrap"
Write-Host "==============================================="
Write-Host ""

#-------------------------------------------------------
# Directories
#-------------------------------------------------------

$dirs = @(

    $root,

    "$root\collectors",

    "$root\parsers",

    "$root\chunking",

    "$root\embeddings",

    "$root\storage",

    "$root\models",

    "$root\utils"

)

foreach($dir in $dirs){

    New-Item -ItemType Directory -Force -Path $dir | Out-Null

}

#-------------------------------------------------------
# Root Files
#-------------------------------------------------------

$files = @(

    "$root\main.py",

    "$root\service.py",

    "$root\config.py",

    "$root\README.md",

    "$root\requirements.txt",

    "$root\.gitignore"

)

#-------------------------------------------------------
# Collector Files
#-------------------------------------------------------

$files += @(

    "$root\collectors\__init__.py",

    "$root\collectors\workspace_collector.py",

    "$root\collectors\source_collector.py",

    "$root\collectors\dependency_collector.py",

    "$root\collectors\configuration_collector.py",

    "$root\collectors\documentation_collector.py",

    "$root\collectors\git_collector.py",

    "$root\collectors\environment_collector.py",

    "$root\collectors\build_collector.py"

)

#-------------------------------------------------------
# Parser Files
#-------------------------------------------------------

$files += @(

    "$root\parsers\__init__.py",

    "$root\parsers\language_detector.py",

    "$root\parsers\python_parser.py",

    "$root\parsers\javascript_parser.py",

    "$root\parsers\java_parser.py",

    "$root\parsers\csharp_parser.py"

)

#-------------------------------------------------------
# Chunking
#-------------------------------------------------------

$files += @(

    "$root\chunking\__init__.py",

    "$root\chunking\chunk_builder.py"

)

#-------------------------------------------------------
# Embeddings
#-------------------------------------------------------

$files += @(

    "$root\embeddings\__init__.py",

    "$root\embeddings\embedding_service.py"

)

#-------------------------------------------------------
# Storage
#-------------------------------------------------------

$files += @(

    "$root\storage\__init__.py",

    "$root\storage\metadata_store.py",

    "$root\storage\vector_store.py",

    "$root\storage\project_store.py"

)

#-------------------------------------------------------
# Models
#-------------------------------------------------------

$files += @(

    "$root\models\__init__.py",

    "$root\models\workspace.py",

    "$root\models\project.py",

    "$root\models\source_file.py",

    "$root\models\dependency.py",

    "$root\models\rag_document.py"

)

#-------------------------------------------------------
# Utils
#-------------------------------------------------------

$files += @(

    "$root\utils\__init__.py",

    "$root\utils\hashing.py",

    "$root\utils\filesystem.py"

)

foreach($file in $files){

    if(!(Test-Path $file)){

        New-Item -ItemType File -Path $file | Out-Null

    }

}

#-------------------------------------------------------
# .gitignore
#-------------------------------------------------------

@"
.venv/
__pycache__/
*.pyc
.vscode/
.idea/
.env
"@ | Set-Content "$root\.gitignore"

#-------------------------------------------------------
# Create Virtual Environment
#-------------------------------------------------------

Write-Host "Creating virtual environment..."

Push-Location $root

python -m venv .venv

Pop-Location

Write-Host ""
Write-Host "==============================================="
Write-Host " Bootstrap Complete!"
Write-Host "==============================================="
Write-Host ""
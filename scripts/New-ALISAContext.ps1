<#
.SYNOPSIS
    Builds a Claude-friendly, read-only map of the ALISA repository, prioritizing
    agents/forge/coding_agent.

.DESCRIPTION
    Run from any directory. The repository root is detected from -Root or the
    script's parent directory. Git-tracked files are preferred; if Git is not
    available, a filesystem scan is used.

    By default this creates:
      - ALISA_CONTEXT.md       concise project + Coding Agent orientation
      - ALISA_REPO_MAP.txt     directory/file inventory and Git state

    Use -IncludeSource to append source and test file contents for the Coding
    Agent. Secrets and common generated/cache directories are excluded.

.EXAMPLE
    .\scripts\New-ALISAContext.ps1

.EXAMPLE
    .\scripts\New-ALISAContext.ps1 -IncludeSource

.EXAMPLE
    .\scripts\New-ALISAContext.ps1 -Root "D:\Projects\Project-ALISA" -OutputDirectory ".\claude-context"
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$OutputDirectory = ".",
    [switch]$IncludeSource,
    [int]$MaxFileChars = 30000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    param([string]$RequestedRoot)
    if ($RequestedRoot) {
        $candidate = (Resolve-Path -LiteralPath $RequestedRoot).Path
    } else {
        $candidate = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    }
    if (-not (Test-Path -LiteralPath (Join-Path $candidate "README.MD"))) {
        throw "Could not identify ALISA repository root at '$candidate' (README.MD missing). Pass -Root."
    }
    return $candidate
}

function Get-RelativePath {
    param([string]$Base, [string]$FullName)
    $baseUri = [Uri]::new(($Base.TrimEnd('\') + '\'))
    $fileUri = [Uri]::new($FullName)
    return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($fileUri).ToString()).Replace('/', '\')
}

function Test-ExcludedPath {
    param([string]$RelativePath)
    $p = $RelativePath.Replace('/', '\')
    $excluded = @(
        '(^|\\)\.git(\\|$)',
        '(^|\\)\.venv(\\|$)',
        '(^|\\)venv(\\|$)',
        '(^|\\)__pycache__(\\|$)',
        '(^|\\)\.pytest_cache(\\|$)',
        '(^|\\)\.mypy_cache(\\|$)',
        '(^|\\)node_modules(\\|$)',
        '(^|\\)dist(\\|$)',
        '(^|\\)build(\\|$)',
        '(^|\\)\.ruff_cache(\\|$)',
        '(^|\\)site-packages(\\|$)',
        '(^|\\)\.idea(\\|$)',
        '(^|\\)\.vscode(\\|$)',
        '(^|\\)cache(\\|$)',
        '(^|\\)models(\\|$)',
        '(^|\\)checkpoints(\\|$)'
    )
    foreach ($pattern in $excluded) {
        if ($p -match $pattern) { return $true }
    }
    if ($p -match '(^|\\)\.env($|\.)' -or $p -match '(^|\\).*\.pem$' -or
        $p -match '(^|\\).*\.key$' -or $p -match '(^|\\).*credentials.*') {
        return $true
    }
    return $false
}

function Get-RepositoryFiles {
    param([string]$RepositoryRoot)

    $git = Get-Command git -ErrorAction SilentlyContinue
    $items = @()
    if ($git) {
        $raw = & git -C $RepositoryRoot ls-files 2>$null
        if ($LASTEXITCODE -eq 0 -and $raw) {
            $items = @($raw | ForEach-Object {
                Join-Path $RepositoryRoot $_
            } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
        }
    }

    if ($items.Count -eq 0) {
        $items = @(Get-ChildItem -LiteralPath $RepositoryRoot -File -Recurse -Force |
            ForEach-Object { $_.FullName })
    }

    $result = foreach ($item in $items) {
        $relative = Get-RelativePath -Base $RepositoryRoot -FullName $item
        if (-not (Test-ExcludedPath -RelativePath $relative)) {
            Get-Item -LiteralPath $item
        }
    }
    return @($result | Sort-Object FullName -Unique)
}

function Add-Section {
    param([System.Text.StringBuilder]$Builder, [string]$Heading)
    [void]$Builder.AppendLine("")
    [void]$Builder.AppendLine("## $Heading")
    [void]$Builder.AppendLine("")
}

$repoRoot = Resolve-RepoRoot -RequestedRoot $Root
$outDir = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $repoRoot $OutputDirectory
}
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$files = @(Get-RepositoryFiles -RepositoryRoot $repoRoot)
$relativeFiles = @($files | ForEach-Object {
    Get-RelativePath -Base $repoRoot -FullName $_.FullName
})

$codingAgentFiles = @($files | Where-Object {
    (Get-RelativePath -Base $repoRoot -FullName $_.FullName) -like 'agents\forge\coding_agent\*'
})
$codingAgentRelative = @($codingAgentFiles | ForEach-Object {
    Get-RelativePath -Base $repoRoot -FullName $_.FullName
})

$gitBranch = "unavailable"
$gitStatus = "Git status unavailable"
$gitHead = "unavailable"
if (Get-Command git -ErrorAction SilentlyContinue) {
    $branchResult = & git -C $repoRoot branch --show-current 2>$null
    if ($LASTEXITCODE -eq 0 -and $branchResult) { $gitBranch = ($branchResult -join "").Trim() }
    $statusResult = & git -C $repoRoot status --short 2>$null
    if ($LASTEXITCODE -eq 0) {
        $gitStatus = if ($statusResult) { $statusResult -join "`n" } else { "Clean working tree" }
    }
    $headResult = & git -C $repoRoot rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $headResult) { $gitHead = ($headResult -join "").Trim() }
}

$context = [System.Text.StringBuilder]::new()
[void]$context.AppendLine("# ALISA Repository Context")
[void]$context.AppendLine("")
[void]$context.AppendLine("> Generated by scripts/New-ALISAContext.ps1. This is an orientation snapshot; inspect live source before editing.")
[void]$context.AppendLine("")
[void]$context.AppendLine("- Repository: ``$repoRoot``")
[void]$context.AppendLine("- Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
[void]$context.AppendLine("- Git branch: ``$gitBranch``")
[void]$context.AppendLine("- Git HEAD: ``$gitHead``")
[void]$context.AppendLine("- Tracked/discovered files: $($files.Count)")
[void]$context.AppendLine("- Coding Agent files: $($codingAgentFiles.Count)")
Add-Section $context "Project purpose"
[void]$context.AppendLine("ALISA (Advanced Learning & Intelligent Systems Architecture) is a human-directed, modular multi-agent system. The user sets goals and approvals; specialist agents coordinate delegated work. Preserve human control, explicit permissions, evidence, and honest capability boundaries.")
Add-Section $context "Primary build focus: Forge Coding Agent"
[void]$context.AppendLine("Primary path: ``agents/forge/coding_agent/``")
[void]$context.AppendLine("The Coding Agent is a Python package with ``src/coding_agent/``, ``tests/``, and a ``coding-agent`` CLI. Its documented design includes multi-language indexing/parsing, typed dependency relationships, a knowledge graph, bounded context assembly, structured planning, human-approved implementation, guarded workspace writes, journals, recovery/rollback, and verification.")
[void]$context.AppendLine("")
[void]$context.AppendLine("Key areas to trace (verify current source and tests):")
[void]$context.AppendLine("- Parsing/indexing: ``code_index.py``, ``parsers/``, ``resolvers/``")
[void]$context.AppendLine("- Graph and impact: ``knowledge_graph.py``, ``implementation_graph.py``")
[void]$context.AppendLine("- Context: ``context_engine.py``, ``generation_context.py``")
[void]$context.AppendLine("- Requirements/specification/planning: ``engineering_spec.py``, ``espec_generator.py``, ``plan_specification.py``, ``planner.py``")
[void]$context.AppendLine("- Execution/orchestration: ``code_generator.py``, ``orchestrator.py``, ``coordinator.py``")
[void]$context.AppendLine("- Workspace/write safety: ``workspace.py``, ``workspace_writer.py``, ``write_guard.py``")
[void]$context.AppendLine("- Verification: ``command_runner.py``, ``verification*.py``, ``structural_verifier.py``")
[void]$context.AppendLine("- Audit/recovery: ``change_journal.py``, ``change_tracking.py``, ``task_journal.py``, ``recovery.py``, ``retry.py``")
[void]$context.AppendLine("- Interfaces: ``cli.py``, ``llm_client.py``, ``models.py``")
Add-Section $context "Claude working instructions"
[void]$context.AppendLine("1. Read the root ``CLAUDE.md``, root README, Development.MD, and Coding Agent README first.")
[void]$context.AppendLine("2. Inspect ``git status --short`` and preserve all existing user changes.")
[void]$context.AppendLine("3. Map the actual call path, contracts, callers, and tests before modifying code.")
[void]$context.AppendLine("4. Keep changes focused on the Coding Agent unless another component is required.")
[void]$context.AppendLine("5. Never bypass approval gates, workspace boundaries, write guards, command validation, journals, rollback, or verification.")
[void]$context.AppendLine("6. Add/update tests for success, failure, and safety cases; update docs when behavior changes.")
[void]$context.AppendLine("7. Do not commit, push, switch branches, merge, reset, or delete data unless explicitly asked.")
[void]$context.AppendLine("8. Report exact validation commands and truthful outcomes. Distinguish passed, failed, blocked, and not run.")
[void]$context.AppendLine("9. Never include secrets or .env contents in generated context.")
[void]$context.AppendLine("10. Prefer small, cohesive, backwards-compatible changes; ask when requirements are materially ambiguous.")
Add-Section $context "Useful commands"
[void]$context.AppendLine("Run from ``agents/forge/coding_agent/``:")
[void]$context.AppendLine('```powershell')
[void]$context.AppendLine('python -m pytest tests/test_espec_generator.py -q')
[void]$context.AppendLine('python -m pytest -q')
[void]$context.AppendLine('python -m pip install -e ".[test]"')
[void]$context.AppendLine('```')
Add-Section $context "Git state at generation time"
[void]$context.AppendLine('```text')
[void]$context.AppendLine($gitStatus)
[void]$context.AppendLine('```')
Add-Section $context "Coding Agent file inventory"
[void]$context.AppendLine('```text')
foreach ($path in $codingAgentRelative) { [void]$context.AppendLine($path) }
[void]$context.AppendLine('```')
Add-Section $context "Repository top-level inventory"
[void]$context.AppendLine('```text')
foreach ($path in ($relativeFiles | Where-Object { $_ -notmatch '\\' })) { [void]$context.AppendLine($path) }
[void]$context.AppendLine('```')

if ($IncludeSource) {
    Add-Section $context "Coding Agent source and tests"
    $sourceCandidates = @($codingAgentFiles | Where-Object {
        $rel = Get-RelativePath -Base $repoRoot -FullName $_.FullName
        ($rel -match '^agents\\forge\\coding_agent\\(src|tests)\\') -and
        ($_.Extension -in @('.py', '.toml', '.md'))
    })
    foreach ($file in $sourceCandidates) {
        $rel = Get-RelativePath -Base $repoRoot -FullName $file.FullName
        [void]$context.AppendLine("### ``$rel``")
        [void]$context.AppendLine('```')
        try {
            $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop
            if ($content.Length -gt $MaxFileChars) {
                $content = $content.Substring(0, $MaxFileChars) + "`n[TRUNCATED by MaxFileChars]"
            }
            [void]$context.AppendLine($content)
        } catch {
            [void]$context.AppendLine("[Could not read file: $($_.Exception.Message)]")
        }
        [void]$context.AppendLine('```')
        [void]$context.AppendLine("")
    }
}

$map = [System.Text.StringBuilder]::new()
[void]$map.AppendLine("ALISA REPOSITORY MAP")
[void]$map.AppendLine("===================")
[void]$map.AppendLine("Root: $repoRoot")
[void]$map.AppendLine("Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
[void]$map.AppendLine("Git branch: $gitBranch")
[void]$map.AppendLine("Git HEAD: $gitHead")
[void]$map.AppendLine("Files: $($files.Count)")
[void]$map.AppendLine("Coding Agent files: $($codingAgentFiles.Count)")
[void]$map.AppendLine("")
[void]$map.AppendLine("CODING AGENT FIRST")
[void]$map.AppendLine("------------------")
foreach ($path in $codingAgentRelative) { [void]$map.AppendLine($path) }
[void]$map.AppendLine("")
[void]$map.AppendLine("FULL REPOSITORY FILE INVENTORY")
[void]$map.AppendLine("------------------------------")
foreach ($path in $relativeFiles) { [void]$map.AppendLine($path) }
[void]$map.AppendLine("")
[void]$map.AppendLine("GIT STATUS --short")
[void]$map.AppendLine("-----------------")
[void]$map.AppendLine($gitStatus)

$contextPath = Join-Path $outDir "ALISA_CONTEXT.md"
$mapPath = Join-Path $outDir "ALISA_REPO_MAP.txt"
[IO.File]::WriteAllText($contextPath, $context.ToString(), [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($mapPath, $map.ToString(), [Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "ALISA context generated successfully." -ForegroundColor Green
Write-Host "Repository: $repoRoot"
Write-Host "Context:    $contextPath"
Write-Host "Repo map:   $mapPath"
Write-Host "Coding Agent files mapped: $($codingAgentFiles.Count)"
if ($IncludeSource) { Write-Host "Source mode: included (per-file cap: $MaxFileChars characters)" }
else { Write-Host "Source mode: inventory only (use -IncludeSource for source/test contents)" }

param(
    [switch]$SkipInstall,
    [switch]$Quick
)

$ErrorActionPreference = "Stop"

function Find-UsablePython {
    $candidates = @()

    $venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
    $venvPython = [System.IO.Path]::GetFullPath($venvPython)
    if (Test-Path $venvPython) { $candidates += $venvPython }

    $common = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($p in $common) {
        if (Test-Path $p) { $candidates += $p }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and ($cmd.Source -notmatch "WindowsApps\\python\.exe$")) {
        $candidates += $cmd.Source
    }

    $candidates = $candidates | Select-Object -Unique
    foreach ($py in $candidates) {
        try {
            $out = & $py -c "import sys; print(sys.version_info[0], sys.version_info[1])" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $py
            }
        } catch {
            continue
        }
    }

    return $null
}

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location $repoRoot

Write-Host "[1/4] Python runtime check..."
$python = Find-UsablePython
if (-not $python) {
    Write-Error @"
No usable Python runtime found.

Action:
1) Install Python 3.10+ from python.org (check 'Add python.exe to PATH')
2) Re-run: powershell -ExecutionPolicy Bypass -File scripts/verify_windows.ps1
"@
}

Write-Host "Using Python: $python"

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[2/4] Creating .venv..."
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment"
    }
} else {
    Write-Host "[2/4] .venv already exists"
}

if (-not $SkipInstall) {
    Write-Host "[3/4] Installing requirements..."
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed"
    }
} else {
    Write-Host "[3/4] Skipped dependency installation (--SkipInstall)"
}

Write-Host "[4/4] Running validations..."
if ($Quick) {
    & $venvPython -m pytest tests/ -q
} else {
    & $venvPython -m pytest tests/ -v
}
if ($LASTEXITCODE -ne 0) {
    throw "Pytest failed"
}

& $venvPython -c "import ast, pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('bot').rglob('*.py')]; print('syntax OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Syntax check failed"
}

$worktreeDirty = git status --short | Measure-Object | Select-Object -ExpandProperty Count
if ($worktreeDirty -eq 0) {
    Write-Host "📦 worktree 누적: 0개 (현재 깨끗)"
} else {
    Write-Host "📦 worktree 누적: $worktreeDirty개 (정리 필요)"
}

Write-Host "Validation complete: tests + syntax passed"

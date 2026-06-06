# Ghost-ALICE installer library: python_runtime
# Dot-sourced by install.ps1. Do not run directly.

function Test-Python311OrNewer {
    param([string]$PythonExe)
    if (-not $PythonExe) { return $false }
    & $PythonExe -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' *> $null
    return ($LASTEXITCODE -eq 0)
}

function Get-PythonVersionKey {
    param([string]$PythonExe)
    if (-not (Test-Python311OrNewer $PythonExe)) { return $null }
    $version = & $PythonExe -c 'import sys; print(f"{sys.version_info.major:03d}.{sys.version_info.minor:03d}.{sys.version_info.micro:03d}")' 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return $version
}

function Find-PythonExe {
    $candidates = @()
    foreach ($cmd in @("python3", "python")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) { $candidates += $found.Source }
    }
    $candidates += @(Get-Command "python3*" -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    $pythonRoots = @()
    if ($env:LOCALAPPDATA) { $pythonRoots += (Join-Path $env:LOCALAPPDATA "Programs\Python") }
    if ($env:ProgramFiles) { $pythonRoots += $env:ProgramFiles }
    if (${env:ProgramFiles(x86)}) { $pythonRoots += ${env:ProgramFiles(x86)} }
    foreach ($root in $pythonRoots) {
        if (-not $root) { continue }
        $candidates += @(Get-ChildItem -Path (Join-Path $root "Python*\python.exe") -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }

    $bestPath = $null
    $bestVersion = $null
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        $version = Get-PythonVersionKey $candidate
        if (-not $version) { continue }
        if (-not $bestVersion -or ([string]::CompareOrdinal($version, $bestVersion) -gt 0)) {
            $bestVersion = $version
            $bestPath = $candidate
        }
    }
    return $bestPath
}

function Install-PythonRuntime {
    Write-Info "Python 3.11+ not found; trying to install Python automatically." "Python 3.11+ not found; trying to install Python automatically."

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Info "winget install --id Python.Python.3 --exact" "winget install --id Python.Python.3 --exact"
        & $winget.Source install --id Python.Python.3 --exact --accept-package-agreements --accept-source-agreements
        return ($LASTEXITCODE -eq 0)
    }

    $choco = Get-Command choco -ErrorAction SilentlyContinue
    if ($choco) {
        Write-Info "choco install python -y" "choco install python -y"
        & $choco.Source install python -y
        return ($LASTEXITCODE -eq 0)
    }

    $scoop = Get-Command scoop -ErrorAction SilentlyContinue
    if ($scoop) {
        Write-Info "scoop install python" "scoop install python"
        & $scoop.Source install python
        return ($LASTEXITCODE -eq 0)
    }

    Write-Warn "No supported Python installer was found." "No supported Python installer was found."
    return $false
}

function Initialize-PythonRuntimeForInstall {
    $py = Find-PythonExe
    if ($py) { return $py }

    if (Install-PythonRuntime) {
        $py = Find-PythonExe
        if ($py) {
            Write-Ok "Python is ready" "Python is ready"
            return $py
        }
    }

    Write-Err "Python 3.11+ is required. Automatic setup did not produce a working Python 3.11+ runtime." "Python 3.11+ is required. Automatic setup did not produce a working Python 3.11+ runtime."
    Write-Info "On Windows, run winget install --id Python.Python.3 --exact, then rerun install.ps1." "On Windows, run winget install --id Python.Python.3 --exact, then rerun install.ps1."
    throw "Python 3.11+ is required - aborting installation"
}

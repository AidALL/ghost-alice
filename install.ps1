# Ghost-ALICE OS Installer (PowerShell 7.6 or newer)
# Supports: Claude Code, Codex
# Usage (PowerShell execution-policy safe wrapper):
#   .\install.cmd                                  # Install to detected AI tools
#   .\install.cmd --prompt-platform                # Select AI tool interactively before installing
#   .\install.cmd task-router verification-before-completion    # Install selected core skills only
#   .\install.cmd --platform claude                # Install all skills to Claude Code
#   .\install.cmd --platform codex                 # Install all skills to Codex
#   .\install.cmd --platform codex --visibility dynamic
#   .\install.cmd --update-source                  # Stash source checkout local changes and fast-forward
#   .\install.cmd --uninstall                      # Full uninstall for all detected Ghost-ALICE managed footprint
#   .\install.cmd --platform codex --uninstall skill-evolution  # Remove selected core skills from one platform
#   .\install.cmd --list                           # List available skills
#   .\install.cmd --status                         # Show current install status
#   .\install.cmd --help                           # Show every command and option
# Where .ps1 script execution is already allowed, .\install.ps1 takes the same arguments.
#
# This shell checks for PowerShell 7.6 or newer, sets UTF-8 console I/O, finds (or installs)
# Python 3.11+ and runs the installer cli, which owns the options, the commands and the
# install order. The file keeps Windows PowerShell 5.1 syntax, so that Windows
# PowerShell 5.1 reaches the version check below and prints the requirement.

function Assert-PowerShellBaseline {
    param($Version)
    if ($Version.Major -gt 7 -or ($Version.Major -eq 7 -and $Version.Minor -ge 6)) {
        return
    }
    [Console]::Error.WriteLine(("[ERROR] PowerShell 7.6 or newer is required (this is PowerShell {0})." -f $Version))
    [Console]::Error.WriteLine("  Install it: winget install --id Microsoft.PowerShell --exact")
    [Console]::Error.WriteLine("  Then re-run: .\install.cmd")
    exit 1
}

Assert-PowerShellBaseline -Version $PSVersionTable.PSVersion
$ErrorActionPreference = "Stop"

# ── UTF-8 console I/O ─────────────────────────────────────
# Best effort: a host without a console keeps its own encodings.
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $Utf8NoBom
try { [Console]::OutputEncoding = $Utf8NoBom } catch { $null = $_ }
try { [Console]::InputEncoding = $Utf8NoBom } catch { $null = $_ }
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# The installer names this command in its help and hints.
$env:GHOST_ALICE_INSTALL_COMMAND = ".\install.cmd"

# ── Messages for the Python runtime step only ─────────────
function Write-Info { param([string]$Message) Write-Host ("[INFO] " + $Message) -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host ("[OK] " + $Message) -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host ("[WARN] " + $Message) -ForegroundColor Yellow }
function Write-Err { param([string]$Message) [Console]::Error.WriteLine("[ERROR] " + $Message) }

# ── Python 3.11+ runtime ──────────────────────────────────
function Test-Python311OrNewer {
    param([string]$PythonExe)
    if (-not $PythonExe) { return $false }
    try {
        & $PythonExe -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' *> $null
    } catch {
        return $false
    }
    return ($LASTEXITCODE -eq 0)
}

function Get-PythonVersionKey {
    param([string]$PythonExe)
    if (-not (Test-Python311OrNewer $PythonExe)) { return $null }
    $versionCode = 'import sys; v=sys.version_info; print(str(v.major).zfill(3)+chr(46)+str(v.minor).zfill(3)+chr(46)+str(v.micro).zfill(3))'
    $version = & $PythonExe -c $versionCode 2>$null
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
    Write-Info "Python 3.11+ not found; trying to install Python automatically."

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Info "winget install --id Python.Python.3 --exact"
        & $winget.Source install --id Python.Python.3 --exact --accept-package-agreements --accept-source-agreements
        return ($LASTEXITCODE -eq 0)
    }

    $choco = Get-Command choco -ErrorAction SilentlyContinue
    if ($choco) {
        Write-Info "choco install python -y"
        & $choco.Source install python -y
        return ($LASTEXITCODE -eq 0)
    }

    $scoop = Get-Command scoop -ErrorAction SilentlyContinue
    if ($scoop) {
        Write-Info "scoop install python"
        & $scoop.Source install python
        return ($LASTEXITCODE -eq 0)
    }

    Write-Warn "No supported Python installer was found."
    return $false
}

function Initialize-PythonRuntimeForInstall {
    $py = Find-PythonExe
    if ($py) { return $py }
    if (Install-PythonRuntime) {
        $py = Find-PythonExe
        if ($py) {
            Write-Ok "Python is ready"
            return $py
        }
    }
    Write-Err "Python 3.11+ is required. Automatic setup did not produce a working Python 3.11+ runtime."
    Write-Info "On Windows, run winget install --id Python.Python.3 --exact, then rerun .\install.cmd."
    return $null
}

# ── Arguments ─────────────────────────────────────────────
function ConvertTo-InstallerArgument {
    param([object[]]$Arguments)
    # The parameter names of the earlier install.ps1, written PowerShell style, become the
    # cli flags; -Skills disappears and its skill names follow as they are. Every other
    # argument, the cli's own --flags included, passes through unchanged and in order.
    $PowerShellParameterFlags = @{
        "-Skills"           = ""
        "-Platform"         = "--platform"
        "-PromptPlatform"   = "--prompt-platform"
        "-Auto"             = "--auto"
        "-List"             = "--list"
        "-Status"           = "--status"
        "-Doctor"           = "--doctor"
        "-Uninstall"        = "--uninstall"
        "-Force"            = "--force"
        "-CleanupPending"   = "--cleanup-pending"
        "-Addon"            = "--addon"
        "-AddonSource"      = "--addon-source"
        "-AddonTag"         = "--addon-tag"
        "-AddonSkip"        = "--addon-skip"
        "-ListAddons"       = "--list-addons"
        "-AgentVisibility"  = "--visibility"
        "-Visibility"       = "--visibility"
        "-UpdateSource"     = "--update-source"
        "-SkipSourceHealth" = "--skip-source-health"
        "-DestinationFile"  = "--destination-file"
        "-Help"             = "--help"
    }
    $converted = @()
    foreach ($argument in $Arguments) {
        $text = [string]$argument
        $name = $text.TrimEnd(":")
        if ($text.StartsWith("-") -and -not $text.StartsWith("--") -and $PowerShellParameterFlags.ContainsKey($name)) {
            if ($PowerShellParameterFlags[$name]) { $converted += $PowerShellParameterFlags[$name] }
            continue
        }
        $converted += $text
    }
    return ,$converted
}

# ── Run the installer ─────────────────────────────────────
$python = Initialize-PythonRuntimeForInstall
if (-not $python) {
    exit 1
}
$installerArguments = ConvertTo-InstallerArgument -Arguments $args
& $python (Join-Path -Path $PSScriptRoot -ChildPath "_shared/installer/cli.py") @installerArguments
exit $LASTEXITCODE

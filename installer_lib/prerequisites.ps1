# Ghost-ALICE installer library: prerequisites
# Dot-sourced by install.ps1. Do not run directly.

function Test-PrerequisiteCommand {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Write-PrerequisiteManualNotice {
    param(
        [string]$DisplayName,
        [string]$WingetId,
        [string]$ManualUrl = ""
    )

    Write-Warn "$DisplayName was not found and automatic setup did not complete." "$DisplayName was not found and automatic setup did not complete."
    if ($ManualUrl) {
        Write-Warn ("For full capability, install {0}: {1}" -f $DisplayName, $ManualUrl) ("For full capability, install {0}: {1}" -f $DisplayName, $ManualUrl)
    }
    Write-Warn ("Windows winget: winget install --id {0} --exact" -f $WingetId) ("Windows winget: winget install --id {0} --exact" -f $WingetId)
}

function Install-ToolPrerequisite {
    param(
        [string]$ToolName,
        [string]$DisplayName,
        [string]$WingetId,
        [string]$ChocolateyPackage,
        [string]$ScoopPackage,
        [string]$ManualUrl = ""
    )

    if (Test-PrerequisiteCommand $ToolName) {
        return $true
    }

    Write-Info "$DisplayName not found; trying automatic prerequisite setup." "$DisplayName not found; trying automatic prerequisite setup."

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Info ("winget install --id {0} --exact" -f $WingetId) ("winget install --id {0} --exact" -f $WingetId)
        & $winget.Source install --id $WingetId --exact --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { return $true }
    }

    $choco = Get-Command choco -ErrorAction SilentlyContinue
    if ($choco) {
        Write-Info ("choco install {0} -y" -f $ChocolateyPackage) ("choco install {0} -y" -f $ChocolateyPackage)
        & $choco.Source install $ChocolateyPackage -y
        if ($LASTEXITCODE -eq 0) { return $true }
    }

    $scoop = Get-Command scoop -ErrorAction SilentlyContinue
    if ($scoop) {
        Write-Info ("scoop install {0}" -f $ScoopPackage) ("scoop install {0}" -f $ScoopPackage)
        & $scoop.Source install $ScoopPackage
        if ($LASTEXITCODE -eq 0) { return $true }
    }

    Write-PrerequisiteManualNotice -DisplayName $DisplayName -WingetId $WingetId -ManualUrl $ManualUrl
    return $false
}

function Install-Prerequisites {
    Initialize-PythonRuntimeForInstall | Out-Null

    [void](Install-ToolPrerequisite `
        -ToolName "git" `
        -DisplayName "Git" `
        -WingetId "Git.Git" `
        -ChocolateyPackage "git" `
        -ScoopPackage "git")

    [void](Install-ToolPrerequisite `
        -ToolName "node" `
        -DisplayName "Node.js" `
        -WingetId "OpenJS.NodeJS.LTS" `
        -ChocolateyPackage "nodejs-lts" `
        -ScoopPackage "nodejs-lts" `
        -ManualUrl "https://nodejs.org/en/download")
}

# Ghost-ALICE installer library: pwsh_runtime
# Dot-sourced by install.ps1. Do not run directly.

function Get-InstalledPwshVersion {
    $pwsh = Get-Command "pwsh" -ErrorAction SilentlyContinue
    if (-not $pwsh) {
        return $null
    }

    $output = & $pwsh.Source -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        return $null
    }

    $text = (@($output) | Select-Object -First 1).ToString().Trim()
    $version = $null
    if ([version]::TryParse($text, [ref]$version)) {
        return $version
    }
    return $null
}

function Get-WindowsPowerShellMsiArch {
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) {
        $arch = $env:PROCESSOR_ARCHITECTURE
    }
    $arch = [string]$arch
    switch ($arch) {
        "AMD64" { return "x64" }
        "x86" { return "x86" }
        "ARM64" { return "arm64" }
        default { throw "Unsupported Windows architecture for PowerShell MSI: $arch" }
    }
}

function Get-PowerShell74LtsInstalledProducts {
    $uninstallRoots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    $productCodePattern = "^\{[0-9A-Fa-f-]{36}\}$"
    $seen = @{}

    foreach ($root in $uninstallRoots) {
        $entries = @(Get-ItemProperty -Path $root -ErrorAction SilentlyContinue)
        foreach ($entry in $entries) {
            $displayName = [string]$entry.DisplayName
            if (-not $displayName -or $displayName -notmatch "^PowerShell\s+7(?:[\s\-\(]|$)") {
                continue
            }

            $version = $null
            $displayVersion = [string]$entry.DisplayVersion
            if (-not [version]::TryParse($displayVersion, [ref]$version)) {
                continue
            }
            if ($version.Major -ne 7 -or $version.Minor -ne 4) {
                continue
            }

            $productCode = [string]$entry.PSChildName
            if ($productCode -notmatch $productCodePattern) {
                $uninstallString = [string]$entry.UninstallString
                $match = [regex]::Match($uninstallString, "\{[0-9A-Fa-f-]{36}\}")
                if ($match.Success) {
                    $productCode = $match.Value
                }
            }

            if ($productCode -notmatch $productCodePattern) {
                Write-Warn ("PowerShell {0} 7.4.x install found but no MSI product code was available; skipping removal." -f $displayName) ("PowerShell {0} 7.4.x install found but no MSI product code was available; skipping removal." -f $displayName)
                continue
            }

            $dedupeKey = $productCode.ToUpperInvariant()
            if ($seen.ContainsKey($dedupeKey)) {
                continue
            }
            $seen[$dedupeKey] = $true

            [pscustomobject]@{
                DisplayName = $displayName
                DisplayVersion = $version
                ProductCode = $productCode
                RegistryPath = [string]$entry.PSPath
            }
        }
    }
}

function Test-RunningAsAdministrator {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]$identity
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Uninstall-PowerShell74LtsInstallations {
    $products = @(Get-PowerShell74LtsInstalledProducts)
    foreach ($product in $products) {
        Write-Info ("Removing existing PowerShell {0} LTS install: {1}" -f $product.DisplayVersion, $product.DisplayName) ("Removing existing PowerShell {0} LTS install: {1}" -f $product.DisplayVersion, $product.DisplayName)
        $msiArgs = @(
            "/x", $product.ProductCode,
            "/quiet",
            "/norestart"
        )
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArgs -Wait -PassThru
        if ($process.ExitCode -eq 3010) {
            Write-Warn ("PowerShell {0} removal completed and requires reboot." -f $product.DisplayVersion) ("PowerShell {0} removal completed and requires reboot." -f $product.DisplayVersion)
            continue
        }
        if ($process.ExitCode -ne 0) {
            throw ("PowerShell {0} removal failed with exit code {1}" -f $product.DisplayVersion, $process.ExitCode)
        }
        Write-Ok ("PowerShell {0} legacy LTS install removed." -f $product.DisplayVersion) ("PowerShell {0} legacy LTS install removed." -f $product.DisplayVersion)
    }
}

function Resolve-PowerShell76LtsReleaseAsset {
    $releaseUri = "https://api.github.com/repos/PowerShell/PowerShell/releases?per_page=100"
    $assetArch = Get-WindowsPowerShellMsiArch
    $releasePattern = "^v7\.6\.\d+$"
    $headers = @{
        "User-Agent" = "Ghost-ALICE installer"
        "Accept" = "application/vnd.github+json"
    }

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    } catch {
        $null = $_
    }

    $releaseResponse = Invoke-WebRequest -Uri $releaseUri -Headers $headers -UseBasicParsing -ErrorAction Stop
    $releases = $releaseResponse.Content | ConvertFrom-Json
    foreach ($release in @($releases)) {
        if ($release.prerelease -or $release.draft) { continue }
        if (-not ($release.tag_name -match $releasePattern)) { continue }
        $versionText = $release.tag_name.TrimStart("v")
        $assetName = "PowerShell-{0}-win-{1}.msi" -f $versionText, $assetArch
        foreach ($asset in @($release.assets)) {
            if ($asset.name -eq $assetName -and $asset.browser_download_url) {
                return [pscustomobject]@{
                    Version = [version]$versionText
                    Name = $asset.name
                    Url = $asset.browser_download_url
                }
            }
        }
    }

    throw "Could not resolve a PowerShell 7.6.x Windows MSI release asset from GitHub."
}

function Initialize-PwshLtsBaseline {
    if ($script:PwshLtsEnsureChecked) {
        return
    }
    $script:PwshLtsEnsureChecked = $true

    if ($env:GHOST_ALICE_TEST_SKIP_PWSH_LTS_BASELINE -eq "1") {
        return
    }

    if (-not (Test-Windows10OrNewer)) {
        return
    }

    try {
        $existingVersion = Get-InstalledPwshVersion
        if ($existingVersion -and $existingVersion -ge $script:PwshLtsBaselineVersion) {
            Write-Info ("PowerShell {0} detected; skipping {1}+ baseline install." -f $existingVersion, $script:PwshLtsBaselineVersion) ("PowerShell {0} detected; skipping {1}+ baseline install." -f $existingVersion, $script:PwshLtsBaselineVersion)
            return
        }

        if (-not (Test-RunningAsAdministrator)) {
            $detectedVersion = if ($existingVersion) { $existingVersion.ToString() } else { "not found" }
            Write-Warn `
                ("PowerShell {0}+ baseline setup skipped because the current shell is not elevated. Detected PowerShell: {1}. Run install.cmd from an elevated PowerShell to install the {2}.x baseline." -f $script:PwshLtsBaselineVersion, $detectedVersion, $script:PwshLtsReleaseLine) `
                ("PowerShell {0}+ baseline setup skipped because the current shell is not elevated. Detected PowerShell: {1}. Run install.cmd from an elevated PowerShell to install the {2}.x baseline." -f $script:PwshLtsBaselineVersion, $detectedVersion, $script:PwshLtsReleaseLine)
            return
        }

        Uninstall-PowerShell74LtsInstallations

        $existingVersion = Get-InstalledPwshVersion
        if ($existingVersion -and $existingVersion -ge $script:PwshLtsBaselineVersion) {
            Write-Info ("PowerShell {0} detected; skipping {1}+ baseline install." -f $existingVersion, $script:PwshLtsBaselineVersion) ("PowerShell {0} detected; skipping {1}+ baseline install." -f $existingVersion, $script:PwshLtsBaselineVersion)
            return
        }

        Write-Info ("PowerShell {0}+ not found; installing latest {1}.x MSI without changing the default shell." -f $script:PwshLtsBaselineVersion, $script:PwshLtsReleaseLine) ("PowerShell {0}+ not found; installing latest {1}.x MSI without changing the default shell." -f $script:PwshLtsBaselineVersion, $script:PwshLtsReleaseLine)
        $asset = Resolve-PowerShell76LtsReleaseAsset
        $downloadPath = Join-Path ([IO.Path]::GetTempPath()) $asset.Name

        Invoke-WebRequest -Uri $asset.Url -OutFile $downloadPath -UseBasicParsing -ErrorAction Stop
        $msiArgs = @(
            "/package", ('"{0}"' -f $downloadPath),
            "/quiet",
            "/norestart",
            "ADD_PATH=1",
            "USE_MU=1",
            "ENABLE_MU=1"
        )
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArgs -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw ("PowerShell {0} MSI install failed with exit code {1}" -f $asset.Version, $process.ExitCode)
        }

        Write-Ok ("PowerShell {0} baseline installed." -f $asset.Version) ("PowerShell {0} baseline installed." -f $asset.Version)
    } catch {
        Write-Warn `
            ("PowerShell {0}+ baseline setup failed; continuing with current PowerShell runtime: {1}" -f $script:PwshLtsBaselineVersion, $_.Exception.Message) `
            ("PowerShell {0}+ baseline setup failed; continuing with current PowerShell runtime: {1}" -f $script:PwshLtsBaselineVersion, $_.Exception.Message)
        return
    }
}

@{
    Severity = @(
        'Error',
        'Warning'
    )
    ExcludeRules = @(
        # install.ps1 is an interactive installer CLI; host output is intentional UI.
        'PSAvoidUsingWriteHost',

        # Public function names are local installer helpers, not exported cmdlets.
        'PSUseSingularNouns',

        # Installer state changes are already guarded by explicit flags and preflight checks.
        'PSUseShouldProcessForStateChangingFunctions'
    )
    Rules = @{
        PSUseCompatibleSyntax = @{
            Enable = $true
            TargetVersions = @(
                '5.1'
            )
        }
    }
}

# Keep PSUseApprovedVerbs and PSUseDeclaredVarsMoreThanAssignments enabled by omission.

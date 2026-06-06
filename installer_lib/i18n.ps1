# Ghost-ALICE installer library: i18n (internationalization)
# Dot-sourced by install.ps1. Do not run directly.

function T {
    param(
        [string]$PrimaryText,
        [string]$EnglishText = ""
    )
    if ($EnglishText) { return $EnglishText }
    return $PrimaryText
}

function Mark {
    param([ValidateSet("linked", "copied", "missing", "unknown", "family")] [string]$Kind)
    if ($LegacyAsciiConsole) {
        switch ($Kind) {
            "linked"  { return "+" }
            "copied"  { return "*" }
            "missing" { return "-" }
            "unknown" { return "x" }
            "family"  { return ">" }
        }
    }
    switch ($Kind) {
        "linked"  { return "●" }
        "copied"  { return "■" }
        "missing" { return "○" }
        "unknown" { return "×" }
        "family"  { return "▸" }
    }
}

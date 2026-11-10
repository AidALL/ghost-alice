"""Claude skill command text; each caller chooses its comparison contract."""


def skill_command_body(path: str) -> str:
    return f"@{path}\n\n$ARGUMENTS\n"


def command_body_matches(actual: str, expected: str, *, ignore_trailing_lf: bool = False) -> bool:
    """Compare exactly, or ignore only trailing LF for installed command files."""
    if ignore_trailing_lf:
        return actual.rstrip("\n") == expected.rstrip("\n")
    return actual == expected

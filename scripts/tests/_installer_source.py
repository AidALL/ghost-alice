from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def installer_bash_source() -> str:
    """Return combined source from install.sh plus every installer_lib/*.sh file.

    After module extraction, function definitions may live in install.sh or
    installer_lib/<name>.sh, so static tests for function existence/content should
    scan this combined source to preserve intent regardless of location.
    """
    parts = [(REPO_ROOT / "install.sh").read_text(encoding="utf-8")]
    lib_dir = REPO_ROOT / "installer_lib"
    if lib_dir.is_dir():
        for path in sorted(lib_dir.glob("*.sh")):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def installer_ps1_source() -> str:
    """Return combined source from install.ps1 plus every installer_lib/*.ps1 file.

    After PowerShell module extraction, function definitions may live in install.ps1
    or installer_lib/<name>.ps1, so static tests for function existence/content
    should scan this combined source to preserve intent regardless of location.
    install.ps1 and its modules use a UTF-8 BOM, so read with utf-8-sig to strip it.
    """
    parts = [(REPO_ROOT / "install.ps1").read_text(encoding="utf-8-sig")]
    lib_dir = REPO_ROOT / "installer_lib"
    if lib_dir.is_dir():
        for path in sorted(lib_dir.glob("*.ps1")):
            parts.append(path.read_text(encoding="utf-8-sig"))
    return "\n".join(parts)

"""Shared automatic dependency installer utility.

Shared by all skills. When a script entrypoint calls ensure_deps(), missing packages are installed automatically.

Cross-platform: supports Windows, macOS, and Linux.
- --break-system-packages: added only in PEP 668 environments on Linux/macOS
- sys.executable: uses the current interpreter without python3/python branching
- Paths: handled with os.path.join across platforms

Usage:
    # Method 1: call directly inside a script
    import sys, os
    sys.path.insert(0, os.path.join("<project root>", "_shared"))
    from auto_deps import ensure_deps
    ensure_deps(["openpyxl", "pyyaml", ("python-dotenv", "dotenv")])

    # Method 2: ask Claude through a SKILL.md instruction (see SKILL_INSTRUCTION below)

When the package name differs from the import name:
    ensure_deps([("python-dotenv", "dotenv"), ("Pillow", "PIL")])
    -> pip install python-dotenv, then verify with import dotenv
"""

import importlib
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Union


# Mapping for packages whose pip name differs from the import name.
KNOWN_ALIASES = {
    "python-dotenv": "dotenv",
    "python-pptx": "pptx",
    "python-docx": "docx",
    "Pillow": "PIL",
    "pyyaml": "yaml",
    "google-auth": "google.auth",
    "beautifulsoup4": "bs4",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
}


def _needs_break_system_packages() -> bool:
    """Return whether this is a PEP 668 externally managed environment.

    - Windows: always False because PEP 668 does not apply.
    - Linux/macOS: True when an EXTERNALLY-MANAGED marker exists.
    - venv/conda: False because the environment is isolated.
    """
    # Not needed inside venv/virtualenv/conda.
    if hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    ):
        return False

    # Windows is not subject to PEP 668.
    if platform.system() == "Windows":
        return False

    # Check for the EXTERNALLY-MANAGED marker.
    # Python 3.11+ commonly places it at /usr/lib/python3.x/EXTERNALLY-MANAGED.
    try:
        lib_dir = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        if (lib_dir / "EXTERNALLY-MANAGED").exists():
            return True
    except Exception:
        pass

    # Add the flag defensively for system Python even when the marker is absent.
    # Some distributions reject installs without exposing the marker.
    if platform.system() in ("Linux", "Darwin"):
        # Add it for system Python under /usr.
        if sys.executable.startswith(("/usr/bin", "/usr/local/bin")):
            return True

    return False


def _build_pip_cmd(install_target: str, quiet: bool = True) -> list[str]:
    """Build the pip install command for this platform."""
    cmd = [sys.executable, "-m", "pip", "install", install_target]

    if _needs_break_system_packages():
        cmd.append("--break-system-packages")

    if quiet:
        cmd.append("-q")

    return cmd


def _get_import_name(pkg: Union[str, tuple]) -> tuple[str, str]:
    """Return the (pip_name, import_name) tuple."""
    if isinstance(pkg, tuple):
        return pkg[0], pkg[1]
    return pkg, KNOWN_ALIASES.get(pkg, pkg)


def _is_installed(import_name: str) -> bool:
    """Return whether the package is installed."""
    try:
        # Handle submodules such as google.auth.
        top_level = import_name.split(".")[0]
        importlib.import_module(top_level)
        return True
    except ImportError:
        return False


def ensure_deps(
    packages: list[Union[str, tuple]],
    extras: dict[str, str] | None = None,
    quiet: bool = True,
) -> list[str]:
    """Install missing packages automatically.

    Args:
        packages: Required packages.
            - string: "openpyxl" -> pip install openpyxl
            - tuple: ("python-dotenv", "dotenv") -> pip install python-dotenv, import dotenv
        extras: pip extras mapping. {"markitdown": "pdf"} -> pip install 'markitdown[pdf]'
        quiet: Hide pip output when True.

    Returns:
        Package names that were actually installed.

    Examples:
        # Basic usage
        ensure_deps(["openpyxl", "pyyaml"])

        # pip name differs from import name
        ensure_deps([("python-dotenv", "dotenv"), ("Pillow", "PIL")])

        # extras
        ensure_deps(["markitdown"], extras={"markitdown": "pdf"})
    """
    extras = extras or {}
    installed = []

    for pkg in packages:
        pip_name, import_name = _get_import_name(pkg)

        if _is_installed(import_name):
            continue

        # Build the pip install command with automatic platform detection.
        if pip_name in extras:
            install_target = f"{pip_name}[{extras[pip_name]}]"
        else:
            install_target = pip_name

        cmd = _build_pip_cmd(install_target, quiet=quiet)

        try:
            print(f"Installing {pip_name}...")
            subprocess.check_call(cmd)
            installed.append(pip_name)

            # Re-check import availability after installation.
            importlib.invalidate_caches()
            if not _is_installed(import_name):
                print(f"Installed {pip_name}, but import {import_name} still failed")
        except subprocess.CalledProcessError as e:
            print(f"Failed to install {pip_name}: {e}")

    if installed:
        print(f"Installed: {', '.join(installed)}")

    return installed


def check_deps(packages: list[Union[str, tuple]]) -> dict[str, bool]:
    """Check package installation status without installing anything.

    Returns:
        {"openpyxl": True, "pyyaml": False, ...}
    """
    result = {}
    for pkg in packages:
        pip_name, import_name = _get_import_name(pkg)
        result[pip_name] = _is_installed(import_name)
    return result


# Instruction template for SKILL.md.
SKILL_INSTRUCTION = """
## Automatic Dependency Installation

Before running this skill's scripts, check dependencies and install any missing packages automatically.
`_shared/auto_deps.py` supports Windows, macOS(zsh), and Linux(bash/zsh).

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '_shared'))
from auto_deps import ensure_deps

# Packages required by this skill. Adjust as needed.
ensure_deps(["openpyxl", "pyyaml", ("python-dotenv", "dotenv")])
```

OS/environment behavior:
- --break-system-packages: added only in PEP 668 system Python environments
- venv/conda: omitted in isolated environments
- Windows: always omitted because PEP 668 does not apply

It can be called directly inside a script, or Claude can run it from the shell.
If installation fails, show the error message to the user and provide manual installation guidance.
""".strip()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check or install package dependencies")
    parser.add_argument("packages", nargs="+", help="Package names to check")
    parser.add_argument("--install", action="store_true", help="Install missing packages automatically")
    parser.add_argument("--verbose", action="store_true", help="Show pip output")
    args = parser.parse_args()

    if args.install:
        result = ensure_deps(args.packages, quiet=not args.verbose)
        if not result:
            print("All packages are already installed.")
    else:
        status = check_deps(args.packages)
        for pkg, ok in status.items():
            icon = "✅" if ok else "❌"
            print(f"  {icon} {pkg}")

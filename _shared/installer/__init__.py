"""Ghost-ALICE installer: the Python owner of the install, uninstall, repair,
update, status and doctor command flows.

The OS entry points (`install.sh`, `install.ps1`) only find or install Python
3.11+ and run `installer/cli.py`. Every decision after that lives here. The
modules call the existing Python entry points under `_shared/` and
`merge-companion/scripts/` through `runner.run_tool` with the same arguments
the shell passed, so their printed wording and exit codes stay as they are.

Dependencies: Python 3.11+ standard library. `lock` imports `install_lock`
and `report` imports `install_report_events` from `_shared`; nothing here
imports a hook runtime module.
"""

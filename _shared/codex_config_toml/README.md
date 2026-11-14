Codex configuration consumers use this package's TOML 1.1 syntax and single initial UTF-8 BOM policy. The package interprets syntax; each consumer owns file selection, edits and failure handling. Dependencies: Python 3.11+ standard library and bundled Tomli 2.4.0.

Tomli 2.4.0's Python source files, `py.typed` and MIT LICENSE in `_vendor/tomli/` are unmodified upstream files. Copyright (c) 2021 Taneli Hukkinen. This copy keeps Python 3.11+ installations independent of a package manager or network connection.

Source: https://pypi.org/project/tomli/2.4.0/ and https://github.com/hukkin/tomli/tree/2.4.0. The PyPI source archive `tomli-2.4.0.tar.gz` has SHA-256 `aa89c3f6c277dd275d8e243ad24f3b5e701491a860d5121f2cdd399fbb31fc9c`.

The source owner is `_shared/codex_config_toml/`. Maintainers edit only that source and generate `agent-security-scan/scripts/codex_config_toml/` for the standalone skill with the command below, from the repository root. The scanner's copy is not independently edited. Tests compare all package files and license bytes, excluding Python caches. The installer copies both complete directories through its existing shared-runtime and skill publication paths.

```python
from pathlib import Path
import shutil
source = Path('_shared/codex_config_toml')
destination = Path('agent-security-scan/scripts/codex_config_toml')
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
```

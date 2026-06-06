import json
import sys
from pathlib import Path

manifest_path, platform = sys.argv[1:]
payload = {
    "version": 1,
    "platform": platform,
    "entries": [],
}
path = Path(manifest_path)
path.parent.mkdir(parents=True, exist_ok=True)
if not path.exists():
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

import sys
from pathlib import Path

import pending_manifest

manifest_path, platform = sys.argv[1:]
payload = {
    "version": 1,
    "platform": platform,
    "entries": [],
}
path = Path(manifest_path)
pending_manifest.update_manifest(
    path,
    lambda target: None if target.exists() else payload,
    lambda data: data is not None,
)

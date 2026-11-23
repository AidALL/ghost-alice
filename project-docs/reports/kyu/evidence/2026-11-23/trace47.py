"""Repeat the 4-7 race (same steps as test_parallel_advance_claims_ready_item_once)
and print the full traceback of every PermissionError a worker thread receives."""
import collections
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

repo = Path(sys.argv[1])
runs = int(sys.argv[2]) if len(sys.argv) > 2 else 200
sys.path[:0] = [str(repo / 'tests')]
import test_autopilot_state as t  # noqa: E402  (sets up adapter import paths)

aps = t.aps
sites = collections.Counter()
first = {}
fails = 0
for n in range(runs):
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        t._write_run(run_dir, [t._item("race")])
        original = aps.write_work_items

        def slow(path, items):
            if Path(path).name == "tasks.jsonl":
                time.sleep(0.08)
            return original(path, items)

        aps.write_work_items = slow
        errors = []

        def worker():
            try:
                aps.advance_approved_run(run_dir)
            except Exception as exc:  # noqa: BLE001
                errors.append((exc, traceback.format_exc()))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        try:
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        finally:
            aps.write_work_items = original
        if errors:
            fails += 1
            for exc, tb in errors:
                frames = [ln.strip() for ln in tb.splitlines() if ln.strip().startswith('File ')]
                site = ' <- '.join(f.split('\\')[-1] for f in frames[-3:][::-1])
                sites[(type(exc).__name__, site)] += 1
                first.setdefault((type(exc).__name__, site), tb)
print(f'runs={runs} failed_runs={fails}')
for k, v in sites.most_common():
    print(v, k)
for k, tb in first.items():
    print('\n=== first traceback for', k[0])
    print('\n'.join(tb.splitlines()[-14:]))

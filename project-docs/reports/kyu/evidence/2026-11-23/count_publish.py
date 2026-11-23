"""Find tool calls in which the assistant itself ran `autopilot_completion.py publish`
(not pytest), with the shell tool, how the completion text was passed, and the result."""
import json
import re
import sys
from collections import Counter
from pathlib import Path

root = Path.home() / '.claude' / 'projects'
calls = {}
results = {}
for f in root.glob('*/*.jsonl'):
    for line in f.open(encoding='utf-8', errors='replace'):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get('message') or {}
        content = msg.get('content')
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'tool_use':
                cmd = (part.get('input') or {}).get('command') or ''
                if 'autopilot_completion.py' in cmd and re.search(r'\bpublish\b', cmd) and 'pytest' not in cmd \
                        and 'run_windows_tests' not in cmd and 'grep' not in cmd:
                    calls[part['id']] = (f.stem[:8], part.get('name'), cmd, rec.get('timestamp', '')[:10])
            elif part.get('type') == 'tool_result':
                c = part.get('content')
                text = c if isinstance(c, str) else ' '.join(x.get('text', '') for x in c or [] if isinstance(x, dict))
                results[part.get('tool_use_id')] = (bool(part.get('is_error')), text[:300])


def how(cmd):
    if '--completion-file -' in cmd or "--completion-file '-'" in cmd or '--completion-file "-"' in cmd:
        if '<<' in cmd:
            return 'stdin heredoc'
        if '|' in cmd:
            return 'stdin pipe'
        return 'stdin other'
    if '--completion-file' in cmd:
        return 'file'
    return 'reference-reuse/other'


tally = Counter()
for tid, (sess, tool, cmd, day) in calls.items():
    err, text = results.get(tid, (None, ''))
    m = re.search(r'completion publication rejected: ([^\n"]{0,70})', text)
    outcome = 'rejected: ' + m.group(1) if m else ('error' if err else 'ok')
    tally[(tool, how(cmd), outcome)] += 1
    if '-v' in sys.argv:
        print(day, sess, tool, how(cmd), '|', outcome)
print('real publish calls:', len(calls))
for k, v in tally.most_common():
    print(v, k)

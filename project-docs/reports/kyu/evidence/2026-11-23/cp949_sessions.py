import glob, json, os
from collections import Counter
out = Counter()
for f in glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl')):
    calls = {}
    for line in open(f, encoding='utf-8', errors='replace'):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        c = (r.get('message') or {}).get('content')
        if not isinstance(c, list):
            continue
        for p in c:
            if not isinstance(p, dict):
                continue
            if p.get('type') == 'tool_use':
                cmd = (p.get('input') or {}).get('command') or ''
                if 'completion_assessment.py' in cmd and ('prepare-response' in cmd or 'record-response' in cmd):
                    calls[p['id']] = 'PYTHONUTF8' in cmd
            elif p.get('type') == 'tool_result' and p.get('tool_use_id') in calls:
                cc = p.get('content')
                t = cc if isinstance(cc, str) else ' '.join(x.get('text', '') for x in cc or [] if isinstance(x, dict))
                out[(os.path.basename(f)[:8], 'UTF8' if calls[p['tool_use_id']] else 'default',
                     'cp949-error' if 'cp949' in t else 'ok/other')] += 1
for k, v in sorted(out.items()):
    print(v, k)

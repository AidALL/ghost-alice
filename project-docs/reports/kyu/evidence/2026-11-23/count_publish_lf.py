"""Count `autopilot_completion.py publish` invocations in teammates' Langfuse observations."""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

data = json.loads((Path(__file__).parent / 'lf_nonkyu.json').read_text(encoding='utf-8'))


def text(v):
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if v is not None else ''


users = Counter(o.get('userId') for o in data)
print('observations per user:', dict(users))
names = Counter((o.get('userId'), o.get('type'), (o.get('name') or '')[:40]) for o in data)
print('top observation names:')
for k, v in names.most_common(25):
    print(' ', v, k)

mention = defaultdict(Counter)
samples = defaultdict(list)
for o in data:
    inp, out = text(o.get('input')), text(o.get('output'))
    blob = inp + ' ' + out
    if 'autopilot_completion' not in blob:
        continue
    user = o.get('userId')
    is_cmd = bool(re.search(r'autopilot_completion\.py[^\n]{0,400}\bpublish\b', inp))
    test = 'pytest' in inp or 'run_windows_tests' in inp or 'unittest' in inp
    rej = re.search(r'completion publication rejected: ([^\n"\\]{0,70})', out)
    key = ('cmd' if is_cmd and not test else 'test' if test else 'mention', o.get('name', '')[:30],
           ('rejected: ' + rej.group(1)) if rej else '-')
    mention[user][key] += 1
    if is_cmd and not test and len(samples[user]) < 5:
        m = re.search(r'.{0,80}autopilot_completion\.py.{0,200}', inp, re.S)
        samples[user].append((o.get('startTime', '')[:16], o.get('name'), m.group(0) if m else '')[:330])

for user, c in mention.items():
    print('\n==', user)
    for k, v in c.most_common(30):
        print(' ', v, k)
    for s in samples[user]:
        print('  sample:', s)

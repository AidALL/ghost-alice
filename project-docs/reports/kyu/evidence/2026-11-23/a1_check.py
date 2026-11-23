"""Per jy session: inputs staged vs resolved, and Stop warnings by code.
Goal: are route-unavailable warnings concentrated in sessions whose inputs never resolved?"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

data = json.loads((Path(__file__).parent / 'lf_nonkyu.json').read_text(encoding='utf-8'))
t = lambda v: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if v is not None else ''
CODES = ['route-unavailable', 'criterion-unbound', 'response-assessment-required']

staged, resolved = Counter(), Counter()
warn = defaultdict(Counter)
seen = set()
stop_checks = Counter()
for o in data:
    if o.get('userId') != 'jy':
        continue
    sid = o.get('sessionId')
    name = o.get('name') or ''
    if 'native-input-staged' in name:
        staged[sid] += 1
    elif 'native-input-resolved' in name:
        resolved[sid] += 1
    elif re.search(r'Stop \S completion(?!\S)', name):
        key = (sid, (o.get('startTime') or '')[:19])
        if key in seen:
            continue
        seen.add(key)
        stop_checks[sid] += 1
        blob = t(o.get('output')) + t(o.get('metadata'))
        for c in CODES:
            if c in blob:
                warn[sid][c] += 1

rows = []
for sid in set(staged) | set(stop_checks):
    unresolved = staged[sid] - resolved[sid]
    rows.append((sid[:8] if sid else '-', staged[sid], resolved[sid], unresolved, stop_checks[sid],
                 warn[sid]['route-unavailable'], warn[sid]['criterion-unbound'], warn[sid]['response-assessment-required']))
rows.sort(key=lambda r: -r[5])
print('session  staged resolved unresolved checks route crit assess')
for r in rows:
    print('%-8s %6d %8d %10d %6d %5d %4d %6d' % r)
tot = lambda i, cond=lambda r: True: sum(r[i] for r in rows if cond(r))
print('\nsessions:', len(rows))
print('route-unavailable total:', tot(5))
print('  in sessions with unresolved inputs > 0:', tot(5, lambda r: r[3] > 0))
print('  in sessions with all inputs resolved   :', tot(5, lambda r: r[3] <= 0))
print('criterion-unbound total:', tot(6), ' / with unresolved>0:', tot(6, lambda r: r[3] > 0))
print('checks total:', tot(4), ' / with unresolved>0:', tot(4, lambda r: r[3] > 0))

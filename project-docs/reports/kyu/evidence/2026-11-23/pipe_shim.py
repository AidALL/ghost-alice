"""Receive stdin bytes exactly as the calling shell delivered them, record them,
and forward the same bytes to `autopilot_completion.py publish --completion-file -`.

argv[1]: JSON file holding {"args": [...], "cwd": "...", "env": {...}, "out": "..."}
"""
import json
import subprocess
import sys

spec = json.load(open(sys.argv[1], encoding='utf-8'))
data = sys.stdin.buffer.read()
result = subprocess.run(spec['args'], input=data, cwd=spec['cwd'], env=spec['env'], capture_output=True)
json.dump({'received': data.decode('utf-8', 'replace'), 'head_hex': data[:6].hex(' '),
           'crlf_count': data.count(b'\r\n'), 'lf_count': data.count(b'\n'),
           'non_ascii': sum(b > 127 for b in data),'exit': result.returncode,
           'stderr': result.stderr.decode('utf-8', 'replace')}, open(spec['out'], 'w', encoding='utf-8'))

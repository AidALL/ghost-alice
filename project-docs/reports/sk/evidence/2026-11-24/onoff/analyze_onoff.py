"""Compare Ghost-ALICE ON vs OFF benchmark runs and check that each condition was what it claims.

Usage: python analyze_onoff.py [--include-pilot] [--out report.md]
Reads <this folder>/on/runs/*/results.jsonl and <this folder>/off/runs/*/results.jsonl.

Condition check per answer session:
  hook_log   ~/.ghost-alice/session-logs/claude/<session>/strict-hook-output.jsonl exists (Ghost-ALICE hooks ran)
  io_trace   lines in ~/.ghost-alice/io-trace.jsonl whose "session" equals the session id
  markers    Ghost-ALICE wording in the answer ([focus:, interpreted-as, [completion-check], [partial-check])
Statistics: Wilson 95% intervals per rate and a two-sided Fisher exact test per 2x2 table.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
GA = Path.home() / '.ghost-alice'
MARKERS = ('[focus:', 'interpreted-as', '[completion-check]', '[partial-check]')


def load_runs(condition: str, include_pilot: bool) -> list[dict]:
    rows = []
    for results in sorted((HERE / condition / 'runs').glob('*/results.jsonl')):
        run_id = results.parent.name
        if 'pilot' in run_id and not include_pilot:
            continue
        for line in results.read_text(encoding='utf-8').splitlines():
            if line.strip():
                rows.append(dict(json.loads(line), run_id=run_id, condition=condition))
    return rows


def io_trace_counts(sessions: set[str]) -> Counter:
    counts: Counter = Counter()
    path = GA / 'io-trace.jsonl'
    if not path.exists() or not sessions:
        return counts
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            for sid in sessions:
                if sid in line:
                    counts[sid] += 1
                    break
    return counts


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (float('nan'), float('nan'))
    z, p = 1.96, k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2x2 table [[a, b], [c, d]]; rows = ON/OFF, columns = event/non-event."""
    row1, col1, n = a + b, a + c, a + b + c + d

    def prob(x: int) -> float:
        return math.comb(col1, x) * math.comb(n - col1, row1 - x) / math.comb(n, row1)

    observed = prob(a)
    lo, hi = max(0, row1 - (n - col1)), min(row1, col1)
    return min(1.0, sum(p for x in range(lo, hi + 1) if (p := prob(x)) <= observed * (1 + 1e-9)))


def summarize(rows: list[dict]) -> dict:
    graded = [r for r in rows if isinstance(r.get('grade'), dict) and 'verdict' in r['grade']]
    claims = [c for r in graded for c in r['grade']['claims']]
    verdicts = Counter(r['grade']['verdict'] for r in graded)
    u = [r for r in graded if r['category'] == 'unanswerable']
    f = [r for r in graded if r['category'] == 'false_premise']
    return dict(
        runs=len({r['run_id'] for r in rows}), answers=len(rows), graded=len(graded),
        answer_errors=sum(1 for r in rows if r.get('is_error')),
        verdicts=dict(verdicts),
        correct=verdicts.get('correct', 0),
        not_correct=len(graded) - verdicts.get('correct', 0),
        fabricated=verdicts.get('fabricated', 0),
        claims=len(claims),
        false_claims=sum(c['label'] == 'false' for c in claims),
        unverifiable_claims=sum(c['label'] == 'unverifiable' for c in claims),
        u_n=len(u), u_bad=sum(r['grade']['verdict'] in ('fabricated', 'incorrect') for r in u),
        f_n=len(f), f_bad=sum(r['grade']['verdict'] != 'correct' for r in f),
        answer_cost=round(sum(r.get('cost_usd') or 0 for r in rows), 2),
        grade_cost=round(sum((r.get('grade') or {}).get('cost_usd') or 0 for r in rows), 2),
        seconds=round(sum(r.get('seconds') or 0 for r in rows), 1),
        turns=sum(r.get('turns') or 0 for r in rows),
        tool_calls=sum(len(r.get('tools') or []) for r in rows),
    )


def condition_check(rows: list[dict]) -> dict:
    sessions = {r['session_id'] for r in rows if r.get('session_id')}
    traces = io_trace_counts(sessions)
    hook_logs = sum((GA / 'session-logs' / 'claude' / sid / 'strict-hook-output.jsonl').exists() for sid in sessions)
    markers = sum(any(m in (r.get('answer') or '') for m in MARKERS) for r in rows)
    return dict(sessions=len(sessions), with_hook_log=hook_logs,
                with_io_trace=sum(1 for sid in sessions if traces[sid]), answers_with_ga_markers=markers)


def pct(k: int, n: int) -> str:
    if not n:
        return '-'
    lo, hi = wilson(k, n)
    return f'{k}/{n} = {100 * k / n:.1f}% (95% {100 * lo:.1f}~{100 * hi:.1f}%)'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--include-pilot', action='store_true')
    parser.add_argument('--out', type=Path, default=HERE / 'onoff-report.md')
    args = parser.parse_args()
    data = {c: load_runs(c, args.include_pilot) for c in ('on', 'off')}
    s = {c: summarize(rows) for c, rows in data.items()}
    chk = {c: condition_check(rows) for c, rows in data.items()}

    tests = []
    for label, k_key, n_key in (('정답이 아닌 답 (부분·오답·지어냄·기권)', 'not_correct', 'graded'),
                                ('지어낸 답 (fabricated)', 'fabricated', 'graded'),
                                ('허위 주장 (false claim)', 'false_claims', 'claims'),
                                ('확인 불가 주장 (unverifiable claim)', 'unverifiable_claims', 'claims'),
                                ('답 없는 질문에 지어내거나 틀림', 'u_bad', 'u_n'),
                                ('틀린 전제를 바로잡지 못함', 'f_bad', 'f_n')):
        a, n1 = s['on'][k_key], s['on'][n_key]
        c, n2 = s['off'][k_key], s['off'][n_key]
        p = fisher_two_sided(a, n1 - a, c, n2 - c) if n1 and n2 else float('nan')
        tests.append((label, pct(a, n1), pct(c, n2), p))

    per_q = defaultdict(lambda: {'on': Counter(), 'off': Counter()})
    for cond, rows in data.items():
        for r in rows:
            if isinstance(r.get('grade'), dict) and 'verdict' in r['grade']:
                per_q[r['id']][cond][r['grade']['verdict']] += 1
    diff_q = {q: v for q, v in per_q.items()
              if v['on'].get('correct', 0) != sum(v['on'].values()) or v['off'].get('correct', 0) != sum(v['off'].values())}

    lines = ['# Ghost-ALICE 켬/끔 허위율 비교', '',
             f'- 대상: ghost-chat 정답지 벤치마크 (bench/questions.json), 시험 실행 {"포함" if args.include_pilot else "제외"}',
             '- 켬: 평소 Claude Code 설정 그대로. 끔: 같은 CLI에 --safe-mode (CLAUDE.md·스킬·훅·플러그인 제외).',
             '- 채점: 두 조건 모두 같은 격리 채점 세션 (사용자 설정·훅·도구 없음).', '',
             '## 조건 확인', '', '| 항목 | 켬 | 끔 |', '| --- | ---: | ---: |']
    for key, name in (('sessions', '답변 세션'), ('with_hook_log', 'Ghost-ALICE 훅 기록 있는 세션'),
                      ('with_io_trace', 'io-trace 기록 있는 세션'), ('answers_with_ga_markers', 'Ghost-ALICE 표기가 섞인 답')):
        lines.append(f'| {name} | {chk["on"][key]} | {chk["off"][key]} |')
    lines += ['', '## 결과', '', '| 지표 | 켬 | 끔 | 차이의 우연 확률 (Fisher p) |', '| --- | --- | --- | ---: |']
    for label, on_text, off_text, p in tests:
        lines.append(f'| {label} | {on_text} | {off_text} | {p:.3f} |')
    lines += ['', '| 항목 | 켬 | 끔 |', '| --- | ---: | ---: |']
    for key, name in (('runs', '회차'), ('graded', '채점된 답'), ('answer_errors', '답변 오류'), ('claims', '주장 수'),
                      ('answer_cost', '답변 사용량 ($)'), ('grade_cost', '채점 사용량 ($)'), ('seconds', '답변 시간 합계 (초)'),
                      ('turns', '대화 턴 합계'), ('tool_calls', '도구 호출 합계')):
        lines.append(f'| {name} | {s["on"][key]} | {s["off"][key]} |')
    lines += ['', '판정 분포: 켬 ' + json.dumps(s['on']['verdicts'], ensure_ascii=False) +
              ' / 끔 ' + json.dumps(s['off']['verdicts'], ensure_ascii=False), '',
              '## 한 번이라도 정답이 아니었던 문항', '', '| 문항 | 켬 | 끔 |', '| --- | --- | --- |']
    for q in sorted(diff_q):
        lines.append(f'| {q} | {dict(diff_q[q]["on"])} | {dict(diff_q[q]["off"])} |')
    if not diff_q:
        lines.append('| (없음) | | |')
    args.out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (args.out.with_suffix('.json')).write_text(json.dumps(dict(summary=s, check=chk, tests=[
        dict(metric=t[0], on=t[1], off=t[2], fisher_p=t[3]) for t in tests]), ensure_ascii=False, indent=1),
        encoding='utf-8')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

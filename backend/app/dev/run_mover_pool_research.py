"""Offline single-axis P25/P35/P50 study; writes only a new V1.2 artifact directory."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import mean, median
from types import SimpleNamespace
import socket

from app.backtest.mover_scanner_v1 import actionability as A, compare as C
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.handoff_study import described_from
from app.backtest.mover_scanner_v1.run import run_study
from app.core.exceptions import ResearchError
from app.research.prompt import ResearchPromptService

SIZES = (25, 35, 50)
VERSION = 'a-mover-scanner-v1.2'
OUT = 'data/runtime/research_reports/mover_scanner_v1_2'
# Conservative operational interpretation declared before measurement: no gate/session decline;
# retain V1.1's structural diversity bars AND at least its unique count and turnover minus 5pp.
GUARDS = dict(repeat_max=.50, turnover_absolute_min=.60, turnover_decline_max=.05,
              unique_at_least_baseline=True, top_symbol_session_share_max=.25,
              gate_session_decline_max=0.0)


def stats(values):
    return dict(avg=mean(values), median=median(values), min=min(values), max=max(values))


def variant_config(n):
    if n not in SIZES:
        raise ValueError('only P25/P35/P50 are authorized')
    return replace(MoverScannerConfig(), pool_size=n,
                   contract_version=MoverScannerConfig().contract_version if n == 25 else VERSION)


def rule_for(n):
    return replace(A.HandoffRule.current(variant_config(n)),
                   contract_version=A.CONTRACT_VERSION if n == 25 else VERSION)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_check(selection, config, count):
    # Controlled edge fixture: retain actual selected candidates, make the remaining pool
    # un-actionable. This tests the selection + payload + unchanged renderer, not truncation.
    ordered = list(selection.handoff)
    assert len(ordered) == 8
    pool = [replace(c, gap_pct=c.gap_pct if i < count else -abs(c.gap_pct))
            for i, c in enumerate(ordered)]
    result = A.select(selection.session_date, pool, selection.rule)
    observed = datetime(2026, 9, 15, 13, 15, tzinfo=timezone.utc)
    payload = A.handoff_payload(result, config, observed)
    rows = A.candidate_rows(result, observed)
    assert payload['candidate_count'] == count == len(rows)
    assert [r.rank for r in rows] == list(range(1, count + 1))
    candidates = [SimpleNamespace(symbol=r.symbol, rank=r.rank, score=r.score,
                  score_components_json=r.score_components) for r in rows]
    repository = SimpleNamespace(get_top8=lambda _: candidates)
    run = SimpleNamespace(id=1, trading_date=result.session_date, status='COMPLETED',
                          score_version=config.score_version)
    try:
        prompt = ResearchPromptService(repository).generate_top_for_run(run)
    except ResearchError:
        assert count == 0
        prompt = None
    else:
        assert count > 0
        assert f'Include exactly the same {count} symbols' in prompt
        assert f'ranks 1..{count}' in prompt
        for c in result.handoff:
            assert f'"symbol": "{c.symbol}"' in prompt
        for c in ordered[count:]:
            assert f'"symbol": "{c.symbol}"' not in prompt
    return dict(count=count, passed=True, fixture='controlled from real selected eight',
                payload=payload, rendered_prompt=prompt, zero_behavior='ResearchError/no prompt')


def main():
    repo = Path(__file__).resolve().parents[3]
    def deny(*args, **kwargs):
        raise RuntimeError('network forbidden in pool research')
    socket.socket.connect = deny
    socket.create_connection = deny
    protected = list((repo / 'data/runtime/research_reports/mover_scanner_v1').glob('*.json'))
    protected += list((repo / 'data/runtime/research_reports/mover_scanner_v1_1').glob('*.json'))
    protected += list((repo / 'backend/app/research').rglob('*.py'))
    before = {str(p.relative_to(repo)): digest(p) for p in protected}
    def progress(stage, done, total):
        if done == total or done % 10 == 0:
            print(stage, done, total, flush=True)
    # Normalization occurs over the eligible universe before the pool cut. Therefore a
    # single P50 scan contains the exact P35 and P25 pools, with bit-identical scores.
    study = run_study(repo, config=variant_config(50), progress=progress)
    assert len(study.scans) == 83
    assert str(study.scans[0].session_date) == '2026-05-18'
    assert str(study.scans[-1].session_date) == '2026-09-15'
    variants, selected_by_size, output_rows = {}, {}, {}
    original_pool = json.loads((repo / 'data/runtime/research_reports/mover_scanner_v1/mover_pool_rows.json').read_text())['rows']
    original_top = json.loads((repo / 'data/runtime/research_reports/mover_scanner_v1_1/handoff_top8_rows.json').read_text())['rows']
    reconstructed = [c.row('09:15 ET') for s in study.scans for c in s.pool if c.candidate_pool_rank <= 25]
    assert reconstructed == original_pool, 'P25 full pool must reproduce frozen V1 exactly'
    for n in SIZES:
        config, rule = variant_config(n), rule_for(n)
        selections = [A.select(s.session_date, [c for c in s.pool if c.candidate_pool_rank <= n], rule) for s in study.scans]
        selected_by_size[n] = selections
        rows = [r for s in selections for r in A.handoff_rows(s, config)]
        output_rows[n] = rows
        if n == 25:
            assert rows == original_top, 'P25 must reproduce frozen V1.1 exactly'
        sizes = [s.handoff_size for s in selections]
        picks = [(s.session_date, tuple(c.symbol for c in s.handoff)) for s in selections]
        described = {(s.session_date, c.symbol): described_from(c) for s in selections for c in s.handoff}
        quality = C.summarize_arm(f'P{n}', picks, described, study.addv_percentiles, study.market_caps).as_dict()
        counts = Counter(c.symbol for s in selections for c in s.handoff)
        maximum = max(counts.values())
        quality['most_repeated_symbols'] = sorted(k for k, v in counts.items() if v == maximum)
        quality['most_repeated_count'] = maximum
        quality['full_gate_pass_per_slot'] = sum(c.gate.both_pass for s in selections for c in s.handoff) / len(rows)
        deep = {}
        for label, lo, hi in [('1-25',1,25), ('26-35',26,35), ('36-50',36,50)]:
            group = [c for s in selections for c in s.handoff if lo <= c.candidate_pool_rank <= hi]
            deep[label] = dict(slot_count=len(group), share=len(group)/len(rows),
                median_total_score=median([c.total_score for c in group]) if group else None,
                median_gap=median([c.gap_pct for c in group]) if group else None,
                median_pm_rvol=median([c.pm_rvol for c in group]) if group else None,
                full_gate_pass_rate=mean([c.gate.both_pass for c in group]) if group else None)
        variants[n] = dict(discovery=stats([s.discovery_pool_size for s in selections]),
            actionable=stats([s.actionable_pool_size for s in selections]), output=stats(sizes),
            sessions=dict(output8=sizes.count(8), output5_7=sum(5 <= x <= 7 for x in sizes),
                          output1_4=sum(1 <= x <= 4 for x in sizes), output0=sizes.count(0)),
            ge5_rate=mean([x >= 5 for x in sizes]), ge7_rate=mean([x >= 7 for x in sizes]),
            eq8_rate=mean([x == 8 for x in sizes]), quality=quality, deep_rank=deep,
            per_session=[dict(session=str(s.session_date), discovery=s.discovery_pool_size,
                              actionable=s.actionable_pool_size, output=s.handoff_size) for s in selections])
    baseline = variants[25]['quality']
    for n, v in variants.items():
        q = v['quality']
        v['checks'] = dict(zero_sessions=v['sessions']['output0'] == 0,
            avg_output=v['output']['avg'] >= 7, median_output=v['output']['median'] >= 7,
            ge5=v['ge5_rate'] >= .85,
            diversity=q['repeat_ratio'] <= GUARDS['repeat_max'] and q['unique_symbols'] >= baseline['unique_symbols']
              and q['turnover'] >= max(GUARDS['turnover_absolute_min'], baseline['turnover']-GUARDS['turnover_decline_max'])
              and q['most_repeated_count']/83 <= GUARDS['top_symbol_session_share_max'],
            gate=q['both_pass_per_session'] >= baseline['both_pass_per_session'])
        v['passes'] = all(v['checks'].values())
    chosen = next((n for n in SIZES if variants[n]['passes']), None)
    report = dict(window=['2026-05-18','2026-09-15'], sessions=83, variants=variants,
        selected_pool_size=chosen, guards=GUARDS, source_hashes=before,
        cache=study.cache.__dict__, deep_rank_definition='candidate_pool_rank: participation pool order',
        network=0, pnl=0, production_change=0, prompt_change=0)
    out = repo / OUT
    out.mkdir(parents=True, exist_ok=True)
    def write(name, obj):
        (out/name).write_text(json.dumps(obj, indent=2, sort_keys=True, default=str)+'\n')
    if chosen is not None:
        config, rule = variant_config(chosen), rule_for(chosen)
        report['contract'] = dict(scanner=config.declaration(), handoff=rule.declaration(), checksum=rule.checksum)
        sample = next(s for s in selected_by_size[chosen] if s.handoff_size == 8)
        tests = [prompt_check(sample, config, count) for count in (8,5,1,0)]
        report['handoff_tests'] = [dict(count=t['count'], passed=t['passed']) for t in tests]
        write('handoff_render_tests.json', tests)
        write('scanner_contract.json', report['contract'])
        (out/'scanner_contract.sha256').write_text(rule.checksum+'\n')
    assert before == {str(p.relative_to(repo)): digest(p) for p in protected}
    report['frozen_sources_unchanged'] = True
    for n in SIZES:
        write(f'P{n}_top8_rows.json', output_rows[n])
    write('P50_pool_rows.json', [c.row('09:15 ET') for s in study.scans for c in s.pool])
    write('pool_size_report.json', report)
    print(json.dumps({n: {k:v for k,v in item.items() if k != 'per_session'} for n,item in variants.items()}, indent=2))
    print('SELECTED', chosen)

if __name__ == '__main__':
    main()

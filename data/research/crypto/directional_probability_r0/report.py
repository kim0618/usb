"""Render all results of the fixed experiment; no fits or selections."""
import json
from pathlib import Path
import pandas as pd

P=Path(__file__).resolve().parent
D=P.parents[3]/'docs/crypto/directional_probability_r0'
R=json.loads((P/'results.json').read_text())
C=json.loads((P/'direction_contrast.json').read_text())
F=pd.read_csv(P/'fold_metrics.csv');Y=pd.read_csv(P/'year_metrics.csv')
B=pd.read_csv(P/'probability_buckets.csv')
A=json.loads((P/'audit.json').read_text())

def f(x,d=3):return 'N/A' if x is None or pd.isna(x) else f'{x:.{d}f}'
def pct(x):return f'{x*100:.2f}%'
def ci(x,d=3):return '['+', '.join(f(v,d) for v in x)+']'
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,row))+' |' for row in rows])

out=['# Directional Probability Model R0 — 결과',
     '**판정: B. WEAK_DIRECTIONAL_MODEL.** 30m/1h에서 비용 보정 path 우위와 target 분포에 통계적 정보가 있으나, '+
     '순수 방향 구분과 비용 후 실행 가능한 threshold의 근거는 부족하다. C 판정 horizon은 없다. '+
     '이번 결과는 고정한 R0 모델군의 검증이며 모든 방향 모델의 가능성을 부정하는 결론은 아니다.',
     '새로운 운영 신호, PAPER/LIVE/UI 또는 production 구현은 없다. C1/C1x 미변경. commit/push 미수행. '+
     '2026-10-03 KST 작성. 이 보고서는 과거 검증이며 현재 BTC 가격의 실시간 예측을 제공하지 않는다.',
     '## 데이터·decision grid',
     '기존 Bybit BTCUSDT OHLC/mark/index/OI/funding, Binance USD-M·spot 참조를 사용했다. '+
     '원본 2021-02-01~2026-09-22, 연구 표본 2021-03-04부터, OOS 2022-01-01~2026-09-22. '+
     '**5분 decision grid**: OI의 5분 갱신 및 +5분 공개 지연 계약에 맞추었다. 미래 경로는 1분 high/low로 계산한다. '+
     'C1에 관계없이 모든 적격 시장 시점을 학습·평가했다.',
     f"전체 적격 5분 표본 {A['grid_rows']:,}개, 각 horizon의 OOS {R['horizons']['30']['n']:,}개. "+
     f"외부 참조가 5분 넘게 낡았던 최근 24h 구간에서 후보 {A['stale_excluded']:,}개를 제외했다. "+
     '최대 4h 미래 경로를 완전히 관측할 수 있는 공통 표본을 썼다. 평가 fold 끝 4h도 제거했다. '+
     '이 496,354개는 독립 거래 수가 아니다.',
     'PIT: 직전 마감봉, OI timestamp+5m, funding은 이미 정산된 값, Binance는 마감 후 관측된 close만 사용. '+
     'OI/funding 원본 재결합 불일치 0, OHLC 오류 0, feature truncation 3개 시점 모두 통과. '+
     '원본 publication latency·vendor revision의 완전한 재현은 아님. 440ms feed 지연과 실제 historical book은 재현하지 못한다.',
     '## Feature·모델·target 사전 고정',
     '20개 feature: 5/30/60/240m return÷RV24h, 4h range 위치, log RV1h/log RV24h와 비율, '+
     'OI 1h/4h/24h log 변화, settled funding, mark/index basis와 1h 변화, Binance perp/spot premium과 1h 변화, '+
     'Bybit−Binance·spot−perp 1h 상대 return÷RV24h, UTC 시각 sin/cos. C1은 feature에 넣지 않았다.',
     'U=다음 시가 대비 미래 최대 상승폭(bp), D=최대 하락폭의 양수 크기(bp). '+
     '**LONG favorable = U−D > LONG 실제 MARKET 비용**, SHORT는 D−U > SHORT 비용. '+
     '음의 funding으로 총비용이 음수가 되면 비교 비용은 0으로 floor. '+
     '이는 비용을 넘는 excursion dominance이며 endpoint 상승확률이나 실제 거래 승률이 아니다. '+
     '두 label은 서로 배타적이며 둘 다 false일 수 있다. LONG MFE=U/MAE 크기=D, SHORT는 그 반대다.',
     '단일 고정 ridge logistic(L2=10), fit-only median/MAD scaling. 별도 시간순 calibration 90일에서 '+
     'logit에 logistic calibration(L2=1)을 학습했다. 확률을 임의 점수에서 변환하지 않았다. '+
     '상수 baseline은 같은 calibration 구간의 발생률. Hyperparameter/model/feature sweep 없음.',
     'Target: 같은 feature로 log1p(U), log1p(D) ridge 회귀(L2=10), fit에서 정한 RV24h 3분위별 '+
     'calibration residual 경험분포로 CDF를 보정했다. **q50/q80**을 고정했으므로 target 도달확률의 '+
     '사전 주장은 **50%/20%**다. 고정 **±0.4%, ±0.8%** 도달확률도 별도 평가. 결과를 보고 percentile을 선택하지 않았다. '+
     '이것은 U/D 주변분포이며 완전한 시간순 path joint distribution은 아니다.',
     '가격 출력은 decision close×(1±target bp/10000), 실제 경로 평가는 next-open 기준. '+
     '각 표본의 target 가격·%·hit probability는 prediction parquet에 보존했다. 아래 target %는 전체 상태의 평균으로, '+
     '현재 가격에 적용할 고정 TP가 아니다.',
     '## Walk-forward·중복 표본',
     '2022-01부터 반기별 9개 expanding fold, 마지막 fold는 2026-09-23 직전 종료. '+
     'test 직전 90일은 calibration, 그 이전은 fit. fit/cal 및 cal/test 경계에 240분 purge. '+
     '학습·보정·평가가 시간순으로 분리되며 test label이 다음 fold로 넘어가지 않는다.',
     '주 단위 cluster를 fold별로 재표집한 bootstrap 2,000회. 일반 CI는 95%, 방향 Brier 개선·경제 진단에는 '+
     '4 horizon을 고려한 98.75% CI도 계산. UTC 4h 정시 한 phase의 비중복 샘플을 별도 검증. '+
     'cluster<20 또는 유효 재표집 자유도<10이면 CI는 N/A. 일주일 이상 regime 의존성은 남는다. '+
     '이 아카이브는 과거 연구에서 반복 사용되었으므로 **model-level OOS, untouched holdout 아님**.',
     '## LONG/SHORT calibration']
rows=[]
for h,v in R['horizons'].items():
    for side in ['LONG','SHORT']:
        p=v[side]['probability']
        rows.append([h,side,f(p['brier'],6),pct(p['bss']),f(p['auc']),pct(p['ece']),pct(p['mean_p']),pct(p['base_rate']),ci(p['cluster_ci']['brier_improvement']['ci9875'],6)])
out.append(table(['분','방향','Brier','BSS','AUC','ECE','평균 예측','실제 favorable','Brier 개선 98.75% CI'],rows))
out+=['BSS는 상수 발생률 baseline 대비 개선율. 30m/1h는 양방향 모두 9/9 fold, 5/5 year BSS 양수. '+
      '2h는 LONG 9/9, SHORT 7/9 fold 양수지만 개선폭 1% 미만. 4h SHORT는 4/9 fold·2/5 year만 양수. '+
      '30m/1h가 방향 및 target calibration 비교에서 가장 안정적이며, 30m이 상대적으로 우세했다. '+
      '이는 horizon의 진단 비교이지 전략 청산시간 선택이 아니다.',
      '![direction calibration](direction_calibration.png)',
      '전체 표본 bucket의 Spearman은 양방향·전 horizon에서 1.0이지만, 500개 이상 bucket만 포함한 기술통계다. '+
      '높은 확률 꼬리는 소표본이므로 이 단조성을 실전 threshold 근거로 확대하지 않는다.',
      '## 방향 정보와 변동폭 정보를 구분한 진단']
rows=[]
for h,v in C.items():
    rows.append([h,f(v['conditional_direction_auc']),f(v['marginal_probability_correlation']),
                 f(v['signed_excursion_rank_correlation']),f(v['chosen_direction_net_bp']),pct(v['sum_above_one_fraction'])])
out.append(table(['분','favorable 내 방향 AUC','P_LONG/P_SHORT 상관','방향 score/실제 U−D 순위상관','큰 확률 방향 net bp','확률합>1 비율'],rows))
out+=['추가 진단은 기존 예측을 읽기만 했으며 재학습하지 않았다. '+
      '어느 한 방향에 비용을 넘는 우위가 실제 있었던 표본에서 P_LONG−P_SHORT의 LONG/SHORT ranking AUC를 평가했다. '+
      '이 차이를 새 확률로 표시하지 않았다. 30m AUC 0.521, 4h 0.507은 방향 선택 정보가 매우 약함을 보여준다. '+
      '두 favorable 확률의 양의 상관과 거의 대칭인 MFE/MAE는 전체 Brier 개선의 일부가 '+
      '비용 대비 움직임 크기/중립 상태 구분에 의존한다는 해석과 일치한다. Feature ablation을 수행하지 않았으므로 '+
      '이를 인과적으로 분해했다고 주장하지 않는다.',
      '**결합확률 일관성 실패:** 배타적인 두 label에 별도 binary 모델을 사용해 약 0.7~1.0% 행에서 합이 1을 넘었다. '+
      '최대 합은 horizon에 따라 '+', '.join(f(C[str(h)]['max_probability_sum'],3) for h in (30,60,120,240))+'. '+
      '임의 정규화로 숨기지 않았다. 이 결과를 “LONG 63% / SHORT 37%” 같은 완성된 joint probability로 제공할 수 없다.',
      '## Probability bucket별 실제 성과',
      '각 표의 실제 favorable은 label 발생률이며 win은 endpoint MARKET 비용 후 양수 비율이다. '+
      'net/PF는 겹치는 가상 관측의 진단 통계이며 포트폴리오 손익이 아니다. '+
      'N/A CI는 cluster 부족, N/A PF는 관측 손실 부재를 뜻한다.']
for h in (30,60,120,240):
    out.append(f'### {h}분')
    rows=[]
    for _,b in B[B.horizon==h].iterrows():
        rows.append([b.side,b.bucket,f'{int(b.n):,}',pct(b.mean_p),pct(b.actual),
                     ci([b.actual_ci_low*100,b.actual_ci_high*100],2),f(b.net_bp),
                     ci([b.net_ci_low,b.net_ci_high]),f(b.pf),pct(b.win_rate),
                     f'{f(b.mean_mfe_bp)}/{f(b.mean_mae_bp)}'])
    out.append(table(['방향','bucket','N','예측','실제 favorable','실제 % 95% CI','net bp','net 95% CI','PF','win','MFE/MAE bp'],rows))
out+=['특히 30m LONG 50~60% bucket: 실제 favorable 47.66%, net −5.63bp. '+
      'SHORT 50~60%: 실제 46.44%, net −10.72bp. LONG 60% 이상 일부 관측은 양수이나 '+
      '겹치는 소수 행이고 주별 cluster가 부족해 threshold의 증거로 인정하지 않았다.',
      '## Target price calibration']
rows=[]
for h,v in R['horizons'].items():
    for side in ['LONG','SHORT']:
        t=v[side]['targets'];a,b=t['q50'],t['q80'];sign='+' if side=='LONG' else '−'
        rows.append([h,side,sign+f(a['mean_target_bp']/100,3)+'%',pct(a['actual_hit']),
                     ci(a['cluster_ci']['hit_rate']['ci95']),sign+f(b['mean_target_bp']/100,3)+'%',pct(b['actual_hit']),
                     ci(b['cluster_ci']['hit_rate']['ci95']),pct(a['pinball_skill'])+'/'+pct(b['pinball_skill']),
                     pct(a['below_forecast_cost_fraction'])+'/'+pct(b['below_forecast_cost_fraction'])])
out.append(table(['분','방향','평균 TP1 q50','실제 hit (주장50%)','95% CI','평균 TP2 q80','실제 hit (주장20%)','95% CI','pinball 개선 q50/q80','비용 이하 q50/q80'],rows))
out+=['Pinball 개선은 같은 calibration 기간의 무조건 quantile baseline 대비. '+
      '실제 target hit는 전 horizon에서 q50 약 49.5~50.3%, q80 약 19.8~20.2%다. '+
      '30m/1h의 모든 fold는 사전 5%p target 오차 범위 안이었다. '+
      '4h 일부 fold q50은 약 44%까지 떨어지므로 전체 평균만 보고 완벽한 조건부 calibration이라고 해석하지 않는다.',
      '![quantile coverage](quantile_coverage.png)']
rows=[]
for h,v in R['horizons'].items():
    for side in ['LONG','SHORT']:
        for th in (40,80):
            t=v[side]['targets'][f'fixed_{th}bp']
            rows.append([h,side,f'{th/100:.1f}%',pct(t['mean_p']),pct(t['actual_hit']),f(t['brier'],5),pct(t['bss']),pct(t['ece'])])
out.append(table(['분','방향','고정 target 크기','평균 예측 hit','실제 hit','Brier','BSS','ECE'],rows))
out+=['![target calibration](target_calibration.png)',
      'Target 크기별 조건부 hit와 MFE는 `target_ranges.csv`, 연도별 결과는 `target_year_metrics.csv`에 보존했다. '+
      'Target 도달은 stop보다 먼저 도달했다는 뜻도, 체결 또는 이익 실현이 보장된다는 뜻도 아니다. '+
      '비용보다 작은 target 또는 예상 순방향 차이는 실전 edge로 취급하지 않았다.',
      '## 비용 후 결과·PF·win·MFE/MAE',
      'Local D6-C MARKET 계약: 편도 taker 0.055%, 양쪽 실제 fill notional 부과. 합성 호가 spread 0.10 USDT/'+
      'tick 0.10, signed 실제 funding 정산(entry < T ≤ exit), stress는 round trip 2.525318bp 추가. '+
      'Base의 평균 총비용은 약 11bp. 원시 next-open→horizon-end-open 진단으로 실제 exit 전략을 만들지 않았다. '+
      '미래 funding은 실현 손익/label에만 사용했고 feature나 cost forecast에는 쓰지 않았다.']
rows=[]
for h,v in R['horizons'].items():
    for side in ['LONG','SHORT']:
        a=v[side]['all_economics']
        rows.append([h,side,f(a['gross_bp']),f(a['cost_bp']),f(a['net_bp']),ci(a['cluster_ci']['net_bp']['ci95']),
                     f(a['stress_net_bp']),f(a['pf']),ci(a['cluster_ci']['pf']['ci95']),pct(a['win_rate']),
                     f'{f(a["mean_mfe_bp"])}/{f(a["mean_mae_bp"])}'])
out.append(table(['분','방향','gross bp','비용 bp','net bp','net 95% CI','stress net bp','PF','PF 95% CI','win','MFE/MAE bp'],rows))
out+=['사전 고정 경제 진단: p≥0.60 **및** 예측 평균 MFE−평균 adverse가 '+
      '11bp+spread+최근 settled funding의 보수적 forecast+stress를 초과해야 한다. '+
      '후보 시점을 희소화해 새 전략을 만들지 않았으며, 아래는 이 단일 진단 조건의 표본 충분성 점검이다.']
rows=[]
for h,v in R['horizons'].items():
    for side in ['LONG','SHORT']:
        a=v[side]['edge'];n=v[side]['edge_nonoverlap']
        rows.append([h,side,a['n'],n['n'],f(n.get('net_bp')),f(n.get('pf')),
                     pct(n['win_rate']) if 'win_rate' in n else 'N/A',
                     n.get('cluster_ci',{}).get('ci_status','NO_SAMPLES')])
out.append(table(['분','방향','중복 전체 N','UTC4h 비중복 N','비중복 net bp','PF','win','CI 상태'],rows))
out+=['비중복 표본은 방향당 0~10개로 사전 최소 200개를 크게 밑돈다. '+
      '몇 건의 높은 수익으로 threshold를 정할 수 없다. 선택 방향을 단순히 더 높은 favorable 확률로 정한 전체 시장 '+
      '진단도 horizon별 −10.50/−10.38/−10.15/−11.12bp다.',
      '## 연도·fold 안정성']
rows=[]
for h in (30,60,120,240):
    for side in ['LONG','SHORT']:
        ff=F[(F.horizon==h)&(F.side==side)];yy=Y[(Y.horizon==h)&(Y.side==side)]
        rows.append([h,side,f'{int((ff.bss>0).sum())}/9',f'{int((yy.bss>0).sum())}/5',
                     *[pct(yy[yy.year==year].bss.iloc[0]) for year in (2022,2023,2024,2025,2026)],
                     f'{pct(ff.hit50.min())}~{pct(ff.hit50.max())}',f'{pct(ff.hit80.min())}~{pct(ff.hit80.max())}'])
out.append(table(['분','방향','BSS+ fold','BSS+ year','2022 BSS','2023','2024','2025','2026','fold q50 hit 범위','fold q80 hit 범위'],rows))
out+=['전체 fold별 probability/target 결과는 `fold_metrics.csv`, 연도별 probability 결과는 `year_metrics.csv`. '+
      '비중복 검증도 30m/1h의 양수 BSS를 유지했으나 비용 후 edge를 입증하지 못했다.',
      '## C1과 관계',
      '기존 C1 정의 및 고정 cutoff 재현: OOS state 18,498개 기준과 정확히 일치. '+
      'C1을 학습표본 필터나 feature로 사용하지 않았고, 기존 C1 episode 시작에 같은 frozen fold model을 적용했다. '+
      '외부 데이터 품질·경계 purge 후 각 horizon 1,600개 episode를 평가했다.']
rows=[]
for h,v in R['horizons'].items():
    c=v['c1'];q=c['episode_long_quantiles'];a=c['episode_economics']
    rows.append([h,pct(v['LONG']['probability']['mean_p']),pct(c['episode_long_mean']),
                 pct(c['episode_probability']['base_rate']),pct(q['0.1'])+'/'+pct(q['0.5'])+'/'+pct(q['0.9']),
                 pct(c['episode_p60_fraction']),f(a['net_bp']),ci(a['cluster_ci']['net_bp']['ci95'])])
out.append(table(['분','전체 평균 P_LONG','C1 평균 P_LONG','C1 실제 favorable','C1 P_LONG p10/p50/p90','C1 P_LONG≥60%','C1 net bp','95% CI'],rows))
out+=['C1은 전체 시장보다 LONG favorable 확률이 높은 특수 상태로 나타났지만, 대부분 높은 확신의 LONG은 아니었다. '+
      '30m에서 C1 평균 약 46.8% vs 전체 34.5%, 4h에서 49.0% vs 전체 44.2%. '+
      '이 label은 neither 상태를 포함하므로 50% 미만을 즉시 SHORT 판단으로 읽으면 안 된다. '+
      'C1의 기존 연구 가치를 이 결과로 폐기하거나 C1x exit와 동일 성과로 비교할 수 없다. '+
      'C1 fold별 내용은 `c1_fold_metrics.csv`와 `c1_episode_predictions.parquet`에 보존했다.',
      '## 판정·다음 단계',
      '**B. WEAK_DIRECTIONAL_MODEL.** 30m/1h/2h가 사전 약한 정보 조건을 충족했다. '+
      '30m/1h는 방향 주변확률·target calibration의 대부분 통계 조건을 충족하지만 경제 진단은 전부 실패했다. '+
      '4h는 추가로 방향 BSS 및 안정성이 부족했다. 배타 label의 확률합 오류, 약한 순방향 AUC, '+
      'high-probability tail의 부족·과신 때문에 USABLE_FOR_FORWARD_MODEL로 올릴 근거가 없다.',
      '다음 단계는 별도 승인된 연구에서 **LONG/SHORT/NEUTRAL의 일관된 joint probability와 target의 공동분포**를 '+
      '사전 등록하고 새 미사용 기간으로 확인하는 것이다. 방향 평가 baseline에서 변동성에 의한 neutral 구분과 '+
      '방향 자체의 개선을 분리해야 한다. 이번 데이터에서 feature를 더하거나 winner를 반복 선택하지 않는다. '+
      '현재 단계에서는 threshold 설정·PAPER/LIVE/UI 구현으로 진행하지 않고 STOP.',
      '## 재현·검증·변경 범위',
      'Branch `main`, HEAD `848a033026803fcd56beb3ca726d809d6763a58d`. 기존 working tree는 다수 수정/미추적 파일이 '+
      '있는 상태였으며 보존했다. 이번 작업은 `data/research/crypto/directional_probability_r0/`와 '+
      '`docs/crypto/directional_probability_r0/` 신규 연구 산출물만 추가했다. V1 운영 기능/V2 기능 구현 없음.',
      '테스트 명령: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=backend .venv/bin/python -m pytest -q '+
      'data/research/crypto/directional_probability_r0/test_r0.py backend/tests/crypto/test_btc_p1.py '+
      'backend/tests/crypto/test_c1_contract_provenance.py`.',
      '결과는 `tests.log`에 기록. 합성 학습 가능성/quantile 복원, path boundary, funding 부호, Decimal 호가 대조, '+
      '기존 E1 회계 대조, 결정성, sparse CI, PF edge case와 기존 회귀를 검증했다. 저장된 예측의 확률범위/'+
      'target 순서/중복/PIT/fold 분리도 검증했다.',
      '실행: `PYTHONPATH=backend OPENBLAS_NUM_THREADS=1 .venv/bin/python data/research/crypto/directional_probability_r0/run.py`. '+
      '재현 코드 제공이며 이번 결과 후 재학습은 수행하지 않았다. 최초 실행 후 보고 통계만 수정·재집계한 내역은 '+
      '[AUDIT_NOTES.md](AUDIT_NOTES.md)에 기록. 계약 및 원본 cache hash, fit code hash, 최종 artifact hash를 보존했다.',
      '[고정 계약](CONTRACT.md) · [방향 calibration PDF](direction_calibration.pdf) · '+
      '[target calibration PDF](target_calibration.pdf) · '+
      '[전체 수치 JSON](../../../data/research/crypto/directional_probability_r0/results.json)',
      '모든 원시 결과/CSV/parquet/log: `/home/tjd618/usb/data/research/crypto/directional_probability_r0/`.']
(D/'REPORT.md').write_text('\n\n'.join(out)+'\n')
print(D/'REPORT.md')

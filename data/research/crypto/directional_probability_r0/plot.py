"""Standalone research plots using the installed system matplotlib; no new dependency."""
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

P=Path(__file__).resolve().parent
D=P.parents[3]/'docs/crypto/directional_probability_r0'
results=json.loads((P/'results.json').read_text())
with (P/'probability_buckets.csv').open() as f: buckets=list(csv.DictReader(f))
fig,axes=plt.subplots(2,4,figsize=(16,8),sharex=True,sharey=True)
for col,h in enumerate((30,60,120,240)):
    for row,side in enumerate(('LONG','SHORT')):
        ax=axes[row,col];items=[b for b in buckets if b['horizon']==str(h) and b['side']==side and int(b['n'])>=500]
        color='#176B87' if row==0 else '#A63D40'
        ax.plot([float(b['mean_p']) for b in items],[float(b['actual']) for b in items],'-',color=color)
        missing=[b for b in items if not b['actual_ci_low']]
        ax.scatter([float(b['mean_p']) for b in missing],[float(b['actual']) for b in missing],marker='x',color=color)
        items=[b for b in items if b['actual_ci_low']]
        x=[float(b['mean_p']) for b in items];y=[float(b['actual']) for b in items]
        lower=[max(0,float(b['actual'])-float(b['actual_ci_low'])) for b in items]
        upper=[max(0,float(b['actual_ci_high'])-float(b['actual'])) for b in items]
        ax.plot([0,1],[0,1],'--',color='gray',linewidth=1)
        ax.errorbar(x,y,yerr=[lower,upper],marker='o',capsize=3,color=color)
        ax.set(title=f'{side}, {h}m',xlim=(0,.8),ylim=(0,.8),xlabel='Predicted favorable probability',ylabel='Observed favorable rate')
        ax.grid(alpha=.2)
fig.suptitle('R0 walk-forward direction calibration | weekly-cluster 95% CI | buckets n >= 500\nFavorable = cost-adjusted excursion dominance; not trade win probability')
fig.tight_layout(rect=(0,0,1,.92));fig.savefig(D/'direction_calibration.png',dpi=160);fig.savefig(D/'direction_calibration.pdf');plt.close(fig)
fig,axes=plt.subplots(2,4,figsize=(16,8))
for col,h in enumerate((30,60,120,240)):
    for row,side in enumerate(('LONG','SHORT')):
        ax=axes[row,col]
        for threshold,color in [(40,'#176B87'),(80,'#D98424')]:
            items=results['horizons'][str(h)][side]['targets'][f'fixed_{threshold}bp']['curve']
            items=[x for x in items if x['n']>=500]
            ax.plot([x['mean_predicted'] for x in items],[x['actual_rate'] for x in items],'o-',label=f'{threshold/100:.1f}% target',color=color)
        ax.plot([0,1],[0,1],'--',color='gray',linewidth=1)
        ax.set(title=f'{side}, {h}m',xlim=(0,1),ylim=(0,1),xlabel='Predicted hit probability',ylabel='Observed hit rate');ax.legend();ax.grid(alpha=.2)
fig.suptitle('R0 fixed-target calibration | complete market grid | buckets n >= 500\nTouch rates describe excursions, not realized exits')
fig.tight_layout(rect=(0,0,1,.92));fig.savefig(D/'target_calibration.png',dpi=160);fig.savefig(D/'target_calibration.pdf');plt.close(fig)
fig,axes=plt.subplots(1,2,figsize=(11,4))
for ax,side in zip(axes,('LONG','SHORT')):
    for q,color in [(50,'#176B87'),(80,'#D98424')]:
        ts=[results['horizons'][str(h)][side]['targets'][f'q{q}'] for h in (30,60,120,240)]
        y=[v['actual_hit'] for v in ts]
        lo=[y[i]-v['cluster_ci']['hit_rate']['ci95'][0] for i,v in enumerate(ts)]
        hi=[v['cluster_ci']['hit_rate']['ci95'][1]-y[i] for i,v in enumerate(ts)]
        ax.errorbar(range(4),y,yerr=[lo,hi],label=f'q{q} target',marker='o',capsize=3,color=color)
        ax.axhline(1-q/100,color=color,linestyle='--',alpha=.6)
    ax.set(title=side,xticks=range(4),xticklabels=['30m','1h','2h','4h'],ylim=(.15,.55),ylabel='Target hit rate');ax.legend();ax.grid(alpha=.2)
fig.suptitle('Preregistered target-quantile coverage | weekly-cluster 95% CI')
fig.tight_layout();fig.savefig(D/'quantile_coverage.png',dpi=160);plt.close(fig)

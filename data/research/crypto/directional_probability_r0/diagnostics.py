"""Read-only diagnostics of the one frozen fit; no model or threshold selection."""
import importlib.util
import json
from pathlib import Path
import numpy as np
import pandas as pd

P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('r0',P/'run.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
d=pd.concat([pd.read_parquet(f) for f in sorted(P.glob('predictions_fold*.parquet'))],ignore_index=True)
summary={};annual=[];contrast=[];c1=[]
for h in r.HORIZONS:
    z=d[(d.horizon==h)&d.is_grid].copy()
    delta=z.p_LONG-z.p_SHORT
    favorable=(z.y_LONG+z.y_SHORT)>0
    conditional=z[favorable].copy();score=delta[favorable].to_numpy()
    # Delta is a ranking score only. It is NOT relabelled as a calibrated probability.
    auc=r.MT.roc_auc(conditional.y_LONG.to_numpy(),score)
    chosen=np.where(delta>=0,z.net_LONG,z.net_SHORT)
    summary[str(h)]={'conditional_direction_auc':auc,'conditional_n':len(conditional),
                     'chosen_direction_net_bp':float(chosen.mean()),
                     'chosen_net_ci':r.ci(z,{'chosen_net_bp':chosen}),
                     'marginal_probability_correlation':float(z.p_LONG.corr(z.p_SHORT)),
                     'sum_above_one_fraction':float((z.p_LONG+z.p_SHORT>1).mean()),
                     'max_probability_sum':float((z.p_LONG+z.p_SHORT).max()),
                     'signed_excursion_rank_correlation':float(pd.Series(delta.to_numpy()).rank().corr(pd.Series((z.u-z.d).to_numpy()).rank()))}
    # Six fixed descriptive bins; this is not a sweep or an additional action rule.
    edges=[-1,-.1,-.05,0,.05,.1,1]
    for lo,hi in zip(edges[:-1],edges[1:]):
        part=z[(delta>=lo)&(delta<hi)]
        if not len(part):continue
        contrast.append({'horizon':h,'delta_low':lo,'delta_high':hi,'n':len(part),
                         'actual_LONG':float(part.y_LONG.mean()),'actual_SHORT':float(part.y_SHORT.mean()),
                         'signed_excursion_bp':float((part.u-part.d).mean()),
                         'long_net_bp':float(part.net_LONG.mean()),'short_net_bp':float(part.net_SHORT.mean())})
    z['year']=pd.to_datetime(z.ts,unit='ms',utc=True).dt.year
    for year,part in z.groupby('year'):
        for side in ('LONG','SHORT'):
            target=r.targets_metrics(part,side,False)
            annual.append({'horizon':h,'year':int(year),'side':side,
                           **{f'{k}_{metric}':v[metric] for k,v in target.items() if k.startswith('q') for metric in ('actual_hit','pinball_skill','mean_target_bp')}})
    for fold,part in d[(d.horizon==h)&d.c1_start].groupby('fold'):
        c1.append({'horizon':h,'fold':int(fold),'n':len(part),'mean_p_long':float(part.p_LONG.mean()),
                   'actual_long_favorable':float(part.y_LONG.mean()),'actual_short_favorable':float(part.y_SHORT.mean()),
                   'long_net_bp':float(part.net_LONG.mean())})
(P/'direction_contrast.json').write_text(json.dumps(summary,indent=2))
pd.DataFrame(contrast).to_csv(P/'direction_contrast_buckets.csv',index=False)
pd.DataFrame(annual).to_csv(P/'target_year_metrics.csv',index=False)
pd.DataFrame(c1).to_csv(P/'c1_fold_metrics.csv',index=False)
# Independent saved-artifact integrity checks, with no refit.
checks={'probability_bounds':bool(d[['p_LONG','p_SHORT']].ge(0).all().all() and d[['p_LONG','p_SHORT']].le(1).all().all()),
        'quantile_order':bool(((d.LONG_q50<=d.LONG_q80)&(d.SHORT_q50<=d.SHORT_q80)).all()),
        'cdf_order':bool(((d.LONG_p40>=d.LONG_p80)&(d.SHORT_p40>=d.SHORT_p80)).all()),
        'unique_decisions_per_horizon':not bool(d.duplicated(['horizon','ts']).any()),
        'mutually_exclusive_labels':bool((d.y_LONG+d.y_SHORT<=1).all()),
        'all_four_horizons_same_grid':len(set(d[d.is_grid].groupby('horizon').size()))==1}
assert all(checks.values()),checks
(P/'artifact_checks.json').write_text(json.dumps(checks,indent=2))
print(json.dumps(summary,indent=2))

import importlib.util
from pathlib import Path
from decimal import Decimal
import numpy as np
import pandas as pd

spec=importlib.util.spec_from_file_location('r0',Path(__file__).with_name('run.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def fixture():
    n=3000;ts=np.arange(n)*r.MIN
    p=10000+np.arange(n)*.1
    g={k:p.copy() for k in ('open','close','high','low','mark_close','index_close')}
    g.update(ts=ts,oi=np.ones(n)*100,funding_last=np.ones(n)*.0001,
             funding_ts=np.array([480*r.MIN]),funding_rate=np.array([.0001]))
    return g


def test_path_boundaries_cost_sign_and_funding():
    g=fixture();rows=np.array([478,479]);y=r.outcomes(g,rows,2)
    assert np.allclose(y['u'],(g['high'][rows+2]/g['open'][rows+1]-1)*1e4)
    old=y['u'].copy();g['high'][478]=1e9
    assert np.array_equal(r.outcomes(g,rows,2)['u'],old)
    nofund=dict(g,funding_rate=np.array([0.]))
    z=r.outcomes(nofund,rows,2)
    assert y['net_LONG'][0]<z['net_LONG'][0]
    assert y['net_SHORT'][0]>z['net_SHORT'][0]
    assert y['net_LONG'][1]==z['net_LONG'][1] # entry at settlement is not charged
    assert np.allclose(y['gross']-y['net_LONG'],y['cost_LONG'])
    assert not np.any(y['y_LONG']+y['y_SHORT']>1)


def test_book_matches_decimal_contract():
    from app.crypto.paper.historical import SyntheticBook
    p=np.array([100.,100.1,100.2,65000.1,65000.2,65000.3,100.15,100.25])
    bid,ask=r.sides(p)
    for i,v in enumerate(p):
        b,a=SyntheticBook().sides(Decimal(str(v)))
        assert abs(bid[i]-float(b[0][0]))<1e-8
        assert abs(ask[i]-float(a[0][0]))<1e-8


def test_probability_learns_and_is_deterministic():
    rng=np.random.default_rng(17);x=rng.normal(size=(6000,3))
    y=(rng.random(6000)<r.M.sigmoid(1.5*x[:,0])).astype(float)
    m=r.M.RidgeLogistic(l2=10).fit(x[:3000],y[:3000]);raw=m.predict(x[3000:4500])
    c=r.M.RidgeLogistic(l2=1,scale_inputs=False).fit(r.M.logit(raw)[:,None],y[3000:4500])
    pred=c.predict(r.M.logit(m.predict(x[4500:]))[:,None])
    assert r.MT.brier_skill(y[4500:],pred,np.full(1500,y[:3000].mean()))>.1
    assert np.array_equal(m.predict(x),r.M.RidgeLogistic(l2=10).fit(x[:3000],y[:3000]).predict(x))


def test_distribution_recovers_quantiles_and_probabilities():
    rng=np.random.default_rng(18);x=rng.normal(size=(12000,3));y=np.expm1(3+.3*x[:,0]+rng.normal(0,.3,12000))
    z=r.distribution(x[:6000],y[:6000],x[6000:9000],y[6000:9000],x[9000:],x[:6000,0],x[6000:9000,0],x[9000:,0])
    assert np.all(z['q80']>=z['q50']) and np.all(z['p40']>=z['p80'])
    assert abs((y[9000:]>=z['q50']).mean()-.5)<.04
    assert abs((y[9000:]>=z['q80']).mean()-.2)<.04
    assert abs(np.mean(z['mean']-y[9000:]))<1


def test_cluster_determinism():
    d=pd.DataFrame({'ts':np.arange(2000)*r.DAY,'fold':np.arange(2000)//1000})
    a=np.sin(np.arange(2000))
    assert r.ci(d,{'x':a})==r.ci(d,{'x':a})


def test_sparse_clusters_do_not_report_degenerate_confidence():
    d=pd.DataFrame({'ts':[0,r.DAY],'fold':[1,2]})
    result=r.ci(d,{'x':np.array([1.,2.])})
    assert result['ci_status']=='INSUFFICIENT_CLUSTERS'
    assert result['x']['ci95']==[None,None]


def test_no_losses_does_not_manufacture_finite_profit_factor():
    d=pd.DataFrame({'net_LONG':[1.,2.],'gross':[12.,13.],'u':[20.,30.],'d':[1.,1.]})
    assert r.economics(d,'LONG',False)['pf'] is None


def test_actual_archive_accounting_matches_existing_e1():
    from app.crypto.research import dataset
    g=dataset.load()
    d=pd.read_parquet('data/research/crypto/c1_exit/e1_trades_v1.parquet').iloc[::100]
    y=r.outcomes(g,d.signal_bar.to_numpy(),240)
    # Existing E1 reports bp on actual entry fill notional; R0 uses reference notional.
    expected=d.net_bp.to_numpy()*d.entry_fill.to_numpy()/d.entry_price.to_numpy()
    assert np.max(np.abs(expected-y['net_LONG']))<1e-6

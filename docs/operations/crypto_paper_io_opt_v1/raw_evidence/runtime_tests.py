import runpy,sys
from pathlib import Path
root=Path(__file__).resolve().parents[4]/'backend/tests/crypto'
prefixes=('test_paper','test_tape','test_hot','test_binance_live','test_chart','test_current_segment','test_live_')
extra={'test_crypto_d2.py','test_crypto_multisymbol.py','test_manual_ux_account_sharing.py','test_manual_ux_half_close.py','test_manual_ux_final_v1.py','test_polling_optimization_v1.py','test_pnl_breakdown.py','test_trade_preview.py','test_trade_candles.py','test_replay_determinism.py','test_multi_entry_markers.py','test_safe_sizing.py','test_safe_max_brackets.py','test_fee_scenarios.py'}
files=[str(p) for p in sorted(root.glob('test_*.py')) if (p.name.startswith(prefixes) or p.name in extra) and p.name!='test_chart_cache_expiry.py']
if not files:raise SystemExit(f'No crypto tests found in {root}')
sys.argv=['run_tests.py',*files,'-vv','--tb=short',*sys.argv[1:]]
runpy.run_path(str(Path(__file__).resolve().with_name('run_tests.py')),run_name='__main__')

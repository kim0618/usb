import runpy,sys
from pathlib import Path
root=Path(__file__).resolve().parents[4]/'backend/tests/crypto'
prefixes=('test_paper','test_tape','test_hot','test_binance_live','test_chart','test_current_segment','test_live_')
files=[str(p) for p in sorted(root.glob('test_*.py')) if p.name.startswith(prefixes)]
if not files:raise SystemExit(f'No crypto tests found in {root}')
sys.argv=['run_tests.py',*files,'-vv','--tb=short',*sys.argv[1:]]
runpy.run_path(str(Path(__file__).resolve().with_name('run_tests.py')),run_name='__main__')

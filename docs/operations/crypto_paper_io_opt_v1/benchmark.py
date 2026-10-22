#!/usr/bin/env python3
"""Local-only should_rotate benchmark; never reads a production tape or contacts a venue.

Run from the repository root with PYTHONPATH=backend and the project's Python environment.
Logical file reads and kernel block counters are deliberately reported separately.
/proc/self/io and strace are unavailable in this local sandbox; unobserved syscall counts are null.
Each child seeds a synthetic tape before measuring 30 append+fsync+rotation checks. Its threshold
is above the test sizes, so this measures the check, not gzip or rotation. Cold-start counting
is reported separately; cache-warm timings use a primed count.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time




def child(lines: int, mode: str) -> dict:
    from app.crypto.paper.segments import SegmentedTape
    row = json.dumps({'kind': 'MARKET', 'payload': {'ts_ms': 1790000000000,
                     'bids': [['1.0000', '100.0']] * 5, 'asks': [['1.0001', '100.0']] * 5,
                     'mark_price': '1.0000', 'last_price': '1.0000'}}, separators=(',', ':')).encode()+b'\n'
    with tempfile.TemporaryDirectory(prefix='usb-rotation-bench-') as tmp:
        tape = SegmentedTape(Path(tmp), segment_records=lines+100)
        tape.active_path.write_bytes(row*lines)
        check = (lambda: tape.active_records() >= tape.segment_records) if mode == 'baseline' else tape.should_rotate
        t0 = time.perf_counter_ns(); check(); cold_ms = (time.perf_counter_ns()-t0)/1e6
        with tape.active_path.open('ab', buffering=0) as writer:
            before = resource.getrusage(resource.RUSAGE_SELF)
            logical_read_bytes = pread_calls = 0
            real_pread = os.pread
            def counted_pread(fd, length, offset):
                nonlocal logical_read_bytes, pread_calls
                data = real_pread(fd, length, offset)
                logical_read_bytes += len(data); pread_calls += 1
                return data
            if mode == 'optimized': os.pread = counted_pread
            cpu0 = time.process_time_ns(); wall0 = time.perf_counter_ns()
            latencies=[]
            for i in range(30):
                writer.write(row); os.fsync(writer.fileno())
                t0=time.perf_counter_ns(); assert check() is False
                latencies.append((time.perf_counter_ns()-t0)/1e6)
                if mode == 'baseline': logical_read_bytes += (lines+i+1)*len(row)
            wall_ms=(time.perf_counter_ns()-wall0)/1e6;cpu_ms=(time.process_time_ns()-cpu0)/1e6;after=resource.getrusage(resource.RUSAGE_SELF)
            os.pread = real_pread
        return {'lines': lines, 'row_bytes': len(row), 'mode': mode, 'checks':30,
                'cold_check_ms':cold_ms,'check_mean_ms':sum(latencies)/len(latencies),
                'wall_ms':wall_ms,'cpu_ms':cpu_ms,'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'fsync_calls':30,'fsync_basis':'explicit harness calls; not kernel trace',
                 'logical_read_bytes':logical_read_bytes,
                'logical_read_basis':'full ASCII tape size per baseline scan; observed os.pread return bytes for optimized',
                'write_bytes':30*len(row),'write_calls_harness':30,
                'pread_calls':pread_calls if mode=='optimized' else None,
                'read_syscalls_total':None,'write_syscalls_total':None,
                'physical_read_bytes':None,'physical_write_bytes':None,
                'kernel_input_blocks':after.ru_inblock-before.ru_inblock,
                'kernel_output_blocks':after.ru_oublock-before.ru_oublock,
                'full_scans':tape.active_full_scans if mode=='optimized' else 31}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child',choices=['baseline','optimized']);parser.add_argument('--lines',type=int)
    args=parser.parse_args()
    if args.child:
        print(json.dumps(child(args.lines,args.child)));return
    rows=[]
    for lines in (1_000,10_000,50_000,100_000):
        for mode in ('baseline','optimized'):
            result=subprocess.check_output([sys.executable,__file__,'--child',mode,'--lines',str(lines)],text=True)
            rows.append(json.loads(result))
    print(json.dumps({'scope':'isolated synthetic local benchmark, warm page cache; no production stress',
                      'baseline':'runtime should_rotate expression: active_records() >= segment_records',
                      'results':rows},indent=2))

if __name__=='__main__':main()

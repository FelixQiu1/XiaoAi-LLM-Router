"""python tests/test_units.py —— 无 pytest 也能跑（断言失败抛异常）。"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_units as t

fns = [n for n in dir(t) if n.startswith("test_")]
failed = 0
for n in fns:
    try:
        getattr(t, n)()
        print(f"PASS {n}")
    except Exception:
        failed += 1
        print(f"FAIL {n}")
        traceback.print_exc()
sys.exit(1 if failed else 0)

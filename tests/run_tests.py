# -*- coding: utf-8 -*-
"""跑全部回归测试。用法：python tests/run_tests.py [文件名关键字 ...]"""
import os, re, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = sys.argv[1:]

names = sorted(f for f in os.listdir(HERE)
               if f.endswith(".py") and f.startswith("_") and f != "run_tests.py")
if KEYS:
    names = [n for n in names if any(k in n for k in KEYS)]

npass = nfail = 0
failed = []
for n in names:
    # 子进程 stdout 走管道时 Windows 默认用 GBK，打印 emoji 会 UnicodeEncodeError
    # 直接判成 FAIL —— 那是环境问题不是代码问题，所以这里强制 UTF-8。
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, os.path.join(HERE, n)], cwd=HERE,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env)
    out = (p.stdout or "") + (p.stderr or "")
    bad = ("Traceback" in out) or bool(re.search(r"\bFAIL\b", out)) or ("error:" in out)
    if bad:
        nfail += 1
        failed.append(n)
        print("FAIL  " + n)
        for l in out.splitlines():
            if re.search(r"Error|error|Traceback|File |line \d", l):
                print("      " + l.strip()[:140])
    else:
        npass += 1
        print("PASS  " + n)

print("\n%d passed, %d failed" % (npass, nfail))
if failed:
    print("failed:", ", ".join(failed))
sys.exit(1 if nfail else 0)

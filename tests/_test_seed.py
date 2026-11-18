# -*- coding: utf-8 -*-
import io, os, sys, shutil, time, json
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.path.insert(0, SRC_DIR)
import db as dbmod, engine as engmod

WORK = os.path.join(HERE, "_td2")
if os.path.isdir(WORK):
    shutil.rmtree(WORK)
os.makedirs(WORK)
cfg = json.load(io.open(os.path.join(SRC_DIR, "config.json"), encoding="utf-8"))
db = dbmod.get_db(WORK, "1")
eng = engmod.Engine(db, cfg)
# 造 5 家公司
for i in range(5):
    eng._create_company("1000%d" % i, "测试%d" % i)
rows = db.q("SELECT cid, ts, o, h, l, c FROM candles ORDER BY cid, ts")
by = {}
for r in rows:
    by.setdefault(r["cid"], []).append(r)
print("companies:", len(by))
for cid, s in list(by.items())[:3]:
    print("cid", cid, "n", len(s))
    for r in s[:4]:
        print("   ts=%d o=%s c=%s" % (r["ts"], r["o"], r["c"]))
    for r in s[-3:]:
        print("   ts=%d o=%s c=%s" % (r["ts"], r["o"], r["c"]))
    jumps = []
    for i in range(1, len(s)):
        if s[i-1]["c"]:
            r = (s[i]["c"] / s[i-1]["c"] - 1) * 100
            if abs(r) > 12:
                jumps.append((round(r,1), s[i-1]["c"], s[i]["c"]))
    print("   jumps>12%:", jumps)
comp = db.q("SELECT id,name,price,prev_close,open,high,low FROM companies")
for c in comp:
    print("  company", dict(c))
db.close()
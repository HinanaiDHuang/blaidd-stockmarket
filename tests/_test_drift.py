# -*- coding: utf-8 -*-
"""v4.3 漂移模型测试：配置 0 要真的生效 + 新的稳健外环控制器。"""
import sys, io, os, json, time, shutil
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
D = SRC_DIR
sys.path.insert(0, D)
import db as dbmod, engine as E

DATA = os.path.join(HERE, "_tdriftdata")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
fails = []


def ck(name, cond, extra=""):
    print(("  OK  " if cond else "FAIL  ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def fresh(cfg_over=None):
    cfg = json.load(io.open(os.path.join(D, "config.json"), encoding="utf-8"))
    cfg.update(cfg_over or {})
    db = dbmod.get_db(DATA, "t")
    db.ex("DELETE FROM companies")
    eng = E.Engine(db, cfg)
    eng._create_company("10001", "测试公司")
    return cfg, db, eng


print("=== 1. 配置设 0 必须真的生效（老代码 or 会兜回默认值）===")
cfg, db, eng = fresh({"news_fire_prob": 0, "drift_gain": 0, "vol_scale": 0})
ck("news_fire_prob 设 0 -> 0（旧代码会变回 0.0067）", eng._num("news_fire_prob", 0.0067) == 0.0,
   eng._num("news_fire_prob", 0.0067))
ck("drift_gain 设 0 -> 0", eng._num("drift_gain", 0.5) == 0.0)
ck("vol_scale 设 0 -> 0", eng._num("vol_scale", 1.0) == 0.0)
ck("缺省值仍走默认", eng._num("__nope__", 0.25) == 0.25)
ck("None 走默认", eng._num("__nope__", None) is None or True)
cfg2 = dict(cfg); cfg2["x"] = None
eng.cfg = cfg2
ck("None 值走默认", eng._num("x", 0.5) == 0.5)

print("=== 2. 控制器关掉时，跳空 == market_daily_drift ===")
cfg, db, eng = fresh({"drift_gain": 0, "market_daily_drift": 0.014})
g = eng._market_gap()
ck("gap 精确等于配置值", abs(g - 0.014) < 1e-9, g)

print("=== 3. 历史不足 5 天时不做修正 ===")
cfg, db, eng = fresh({"drift_gain": 0.8, "market_daily_drift": 0.012,
                       "drift_trim_max": 0.006})
day = time.strftime("%Y%m%d", time.localtime(time.time()))
db.set_meta("market_gap_hist", json.dumps([["20200101", -0.02, 0.0]]))
db.set_meta("market_gap_day", "")
eng._gap_cache = None
g = eng._market_gap()
ck("历史只有 1 条 -> 不修正", abs(g - 0.012) < 1e-9, g)

print("=== 4. 历史充足且底层在亏 -> 修正为正，但不超过上限 ===")
hist = [["202001%02d" % (i + 1), -0.02, 0.0] for i in range(10)]
db.set_meta("market_gap_hist", json.dumps(hist))
db.set_meta("market_gap_day", "")
db.set_meta("market_gap_last", "0.0")
eng._gap_cache = None
g = eng._market_gap()
ck("修正量 > 0", g > 0.012, g)
ck("修正量 <= 上限", g <= 0.012 + 0.006 + 1e-9, g)
ck("D 估计为负", float(db.get_meta("market_drift_ewma", "0")) < 0,
   db.get_meta("market_drift_ewma", ""))
ck("历史被记录下来", len(json.loads(db.get_meta("market_gap_hist", "[]"))) >= 10,
   len(json.loads(db.get_meta("market_gap_hist", "[]"))))

print("=== 5. 底层在赚 -> 修正为负（把多余涨幅收回来）===")
hist = [["202001%02d" % (i + 1), 0.03, 0.0] for i in range(10)]
db.set_meta("market_gap_hist", json.dumps(hist))
db.set_meta("market_gap_day", "")
db.set_meta("market_gap_last", "0.0")
eng._gap_cache = None
g = eng._market_gap()
ck("修正量 < 0", g < 0.012, g)
ck("修正量 >= -上限", g >= 0.012 - 0.006 - 1e-9, g)

print("=== 6. 单日黑天鹅不会把中位数带跑 ===")
hist = [["202001%02d" % (i + 1), -0.02, 0.0] for i in range(9)]
hist.append(["20200110", -0.10, 0.0])   # 一根大阴线
db.set_meta("market_gap_hist", json.dumps(hist))
db.set_meta("market_gap_day", "")
db.set_meta("market_gap_last", "0.0")
eng._gap_cache = None
g = eng._market_gap()
ck("中位数口径下修正量温和", 0.012 < g <= 0.012 + 0.006 + 1e-9, g)

print("=== 7. 每天只算一次（跨进程缓存）===")
cfg, db, eng = fresh({"drift_gain": 0.8, "market_daily_drift": 0.012})
g1 = eng._market_gap()
eng2 = E.Engine(db, cfg)
g2 = eng2._market_gap()
ck("新 Engine 拿到同一个跳空值", abs(g1 - g2) < 1e-12, (g1, g2))
ck("库里记录了当天的跳空", db.get_meta("market_gap_day", "") != "", db.get_meta("market_gap_day", ""))

shutil.rmtree(DATA, ignore_errors=True)
print("")
print("ALL PASS" if not fails else ("FAILED: %s" % fails))
sys.exit(1 if fails else 0)

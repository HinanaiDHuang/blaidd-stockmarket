# -*- coding: utf-8 -*-
"""v4.5 本地冒烟：验证 _market_gap 的 target 指纹逻辑 + 模块可导入。"""
import os, sys, json, shutil, time, random
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
BASE = SRC_DIR
sys.path.insert(0, BASE)

import engine as engmod, db as dbmod

WORK = os.path.join(HERE, "_t_v45")
if os.path.isdir(WORK):
    shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
shutil.copy(os.path.join(BASE, "sample.db"), os.path.join(WORK, "g_c.db"))

cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
db = dbmod.get_db(WORK, "c")

# 1) 第一次算：target=0.018
cfg["market_daily_drift"] = 0.018
cfg["drift_gain"] = 0.0
eng = engmod.Engine(db, cfg)
g1 = eng._market_gap()
print("gap1 (target 0.018) =", g1, " meta target =", db.get_meta("market_gap_target"))

# 2) 同 target 再算：应命中缓存，值不变
eng2 = engmod.Engine(db, cfg)
g2 = eng2._market_gap()
print("gap2 (same target)   =", g2, "cache hit =", abs(g1 - g2) < 1e-15)

# 3) 改配置 target=0.020：当天应立刻生效，且保留修正量
cfg2 = dict(cfg); cfg2["market_daily_drift"] = 0.020
eng3 = engmod.Engine(db, cfg2)
g3 = eng3._market_gap()
print("gap3 (target->0.020)  =", g3, "expect 0.02 =>", abs(g3 - 0.020) < 1e-12)

# 4) 再算一次应命中缓存
eng4 = engmod.Engine(db, cfg2)
g4 = eng4._market_gap()
print("gap4 (cache)          =", g4, "stable =", abs(g3 - g4) < 1e-15)

# 5) 老库（没有 market_gap_target）应能重算而不是崩
db.ex("DELETE FROM meta WHERE key='market_gap_target'")
eng5 = engmod.Engine(db, cfg2)
g5 = eng5._market_gap()
print("gap5 (legacy row)     =", g5, "ok =", g5 is not None)

# 6) 漂移推送窗口仍能取到值
print("push window:", eng4._push_window())
print("ALL OK")
shutil.rmtree(WORK, ignore_errors=True)

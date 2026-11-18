# -*- coding: utf-8 -*-
"""v4.5 回归：跨日快照 + 每日复盘取数。"""
import os, sys, json, shutil
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
BASE = SRC_DIR
sys.path.insert(0, BASE)
import db as dbmod, engine as engmod, features, market

WORK = os.path.join(HERE, "_t_v45_rep")
if os.path.isdir(WORK):
    shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
shutil.copy(os.path.join(BASE, "sample.db"), os.path.join(WORK, "g_c.db"))
cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
db = dbmod.get_db(WORK, "c")
eng = engmod.Engine(db, cfg)

DAY = "2026-11-17"
ok = True

# 1) 造一个「涨了 12%」的状态
db.ex("UPDATE companies SET price=112.0, prev_close=100.0, high=112.0, low=100.0")
# 2) 跨日瞬间做快照
eng._snapshot_day(DAY)
row = db.q1("SELECT * FROM daily_px WHERE day=? LIMIT 1", (DAY,))
print("snapshot price=%s prev=%s" % (row["price"], row["prev_close"]))
assert abs(row["price"] - 112.0) < 1e-6 and abs(row["prev_close"] - 100.0) < 1e-6, "snapshot wrong"
print("  [1] 快照内容 OK")

# 3) 模拟日切：price 和 prev_close 被刷成同一个值
db.ex("UPDATE companies SET price=prev_close, open=prev_close, high=prev_close, low=prev_close")

# 4) 过去的某一天复盘：应该用 daily_px，涨跌 ≈ +12%，涨停 1 家
p = features.daily_report(db, cfg, DAY)
print("  [2] 过去一天复盘: top_chg=%s limit_up=%s" % (p["top"][0]["chg"], p["limit_up"]))
assert abs(p["top"][0]["chg"] - 12.0) < 0.01, "past-day chg wrong: %s" % p["top"][0]["chg"]
assert p["limit_up"] >= 1, "limit_up should be >=1"
print("  [3] 过去一天取数 OK")

# 5) 当天复盘：必须现场算，不能返回缓存
p2 = features.daily_report(db, cfg)
print("  [4] 当天复盘: top_chg=%s (live)" % p2["top"][0]["chg"])
assert abs(p2["top"][0]["chg"]) < 0.01, "today should be live (0% after rollover)"

# 6) 当天连续两次调用不应写缓存行
cnt = db.q1("SELECT COUNT(*) c FROM daily WHERE day=?", (features.today(),))
print("  [5] 当天缓存行数 = %s (应为 0)" % cnt["c"])
assert cnt["c"] == 0, "today must not be cached"

# 7) 过去一天的缓存行应该在（复盘被固化）
cnt2 = db.q1("SELECT COUNT(*) c FROM daily WHERE day=?", (DAY,))
print("  [6] 过去一天缓存行数 = %s (应为 1)" % cnt2["c"])
assert cnt2["c"] == 1

print("ALL OK")
shutil.rmtree(WORK, ignore_errors=True)

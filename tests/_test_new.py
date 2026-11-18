# -*- coding: utf-8 -*-
import io, os, sys, shutil, sqlite3, time
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.path.insert(0, SRC_DIR)
import db as dbmod, market as mkt, features

WORK = os.path.join(HERE, "_tdata")
if os.path.isdir(WORK):
    shutil.rmtree(WORK)
os.makedirs(WORK)
shutil.copy(os.path.join(SRC_DIR, "sample.db"), os.path.join(WORK, "g_10001.db"))

db = dbmod.get_db(WORK, "10001")
cfg = dict(__import__("json").load(io.open(os.path.join(SRC_DIR, "config.json"), encoding="utf-8")))
cfg.setdefault("rename_cost", 3000.0)
cfg.setdefault("sabotage_daily_limit", 2)

me = "999000111"
other = "888000222"
mkt.ensure_user(db, me, "测试甲", 100000.0)
mkt.ensure_user(db, other, "测试乙", 100000.0)

# 造一家属于 me 的公司
db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,beta,drift,vol,anchor,created_at)"
      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (me, "测试甲", "电子", "测试甲电子", "6000001", 20.0, 20.0, 20.0, 20.0, 20.0, 1.0, 0.0, 0.002, 20.0, int(time.time())))
cid = db.q1("SELECT id FROM companies WHERE qq=? ORDER BY id DESC LIMIT 1", (me,))["id"]
tgt = db.q1("SELECT id FROM companies WHERE qq<>? ORDER BY id LIMIT 1", (me,))

print("== 1. 改名 ==")
print(features.company_rename(db, cfg, me, cid, "群友无敌电子")[0])
print(features.company_rename(db, cfg, me, cid, "群友无敌电子")[0])   # 冷却
print(features.company_rename(db, cfg, me, cid, "x")[0])               # 太短
print(features.company_rename(db, cfg, other, cid, "别人改名")[0])      # 不是我的
row = db.q1("SELECT name FROM companies WHERE id=?", (cid,))
print("  现在的公司名:", row["name"])

print("== 2. 搜股 ==")
print(features.search_text(db, cfg, me, "群友"))
print(features.search_text(db, cfg, me, "6000001")[:120])
print(features.search_text(db, cfg, me, "电子")[:200])
print(features.search_text(db, cfg, me, "不存在的票"))

print("== 3. 商业战 ==")
for i in range(4):
    okk, msg, info = features.company_sabotage(db, cfg, me, tgt["id"])
    print("  attempt", i+1, "->", msg.replace("\n", " | ")[:160])
    if not okk:
        break
okk, msg, info = features.company_sabotage(db, cfg, other, tgt["id"])
print("  别人搞:", okk, msg[:80])
print("  今日次数:", features.sabotage_count_today(db, me))
print("  ledger:", [(r["reason"], r["delta"]) for r in db.q("SELECT reason,delta FROM ledger WHERE reason LIKE '%商业战%' OR reason LIKE '%改名%'")])
print("  news:", [(r["title"], r["impact"]) for r in db.q("SELECT title,impact FROM news ORDER BY id DESC LIMIT 4")])
print("  目标股价:", db.q1("SELECT price, prev_close FROM companies WHERE id=?", (tgt["id"],)))
print("  我的现金:", db.q1("SELECT cash FROM users WHERE qq=?", (me,)))
db.close()
print("ALL NEW FEATURE TESTS DONE")
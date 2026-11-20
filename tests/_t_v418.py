# -*- coding: utf-8 -*-
"""v4.18 回归：低门槛炒股入口（快捷下单 + 每日定投）+ 试水仓防套利。

【这一版在做什么】
把「炒股」的门槛降下来：20 元也能开仓、5 秒完成一次有波动有盈亏的操作、
还能一键每日定投。门槛一低就必须配一道闸，否则就是刷分机器：

    每日任务「今日首单 125」+ 翻身任务「第一笔买入 200~1200」
    + 成就「第一笔交易 300」≈ 1325 元奖励
20 元的小单要是也能领这些，就是 66 倍套利。

所以保证金占用低于 200 元的单子标成「试水仓」(small=1)：
交易照常、能赚能亏，但不计入每日任务、翻身任务、交易类成就。

判定口径与位置是这次的重点：
  · 按**保证金**判定，不按成交额 —— 杠杆能把成交额放大 20 倍，
    20 元本金 × 20 倍 = 400 元成交额，看成交额就绕过去了；
  · 判定放在 market.place_order 核心里，不放 API 层 ——
    放 API 层的话网页手动下单那一路就绕过去了。
"""
import io
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import db as dbmod
import daily
import features
import market
import quick

WORK = os.path.join(HERE, "_t_v418")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
cfg = json.load(open(os.path.join(SRC_DIR, "config.json"), encoding="utf-8"))
db = dbmod.get_db(WORK, "c")
NOW = int(time.time())
FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, extra))
        FAILED.append(name)


def mk_user(qq, nick, cash):
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at,bankrupt) VALUES(?,?,?,?,?,?)",
          (qq, nick, float(cash), 0.0, NOW, 0))


def mk_company(name, ticker, price=100.0, prev_close=None, high=None, low=None):
    pc = float(prev_close) if prev_close is not None else float(price)
    hi = float(high) if high is not None else float(price)
    lo = float(low) if low is not None else float(price)
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
          "beta,drift,vol,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          ("9000", "庄家", "测试", name, ticker, float(price), pc, float(price), hi, lo,
           1.0, 0.0, 0.03, NOW))
    return int(db.q1("SELECT id FROM companies WHERE ticker=?", (ticker,))["id"])


def add_candles(cid, ts, vol):
    db.ex("INSERT OR REPLACE INTO candles(cid,ts,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)",
          (cid, ts, 1.0, 1.0, 1.0, 1.0, float(vol)))


print("== 1. 试水仓标记：按保证金判定，不按成交额 ==")
cid_a = mk_company("小盘股", "T001", price=10.0)
# 保证金 100 元（10 股 × 10 元 ÷ 1 倍）→ 试水仓
mk_user("1001", "小散", 100000.0)
ok, msg, info = market.place_order(db, cfg, "1001", cid_a, "long", 10, 1)
check("小单开仓成功", ok, msg)
check("小单被标成试水仓", bool(info and info.get("small") == 1), info)
row = db.q1("SELECT small FROM positions WHERE qq='1001' ORDER BY id DESC LIMIT 1")
check("positions.small 落库", int(row["small"]) == 1, row)
row = db.q1("SELECT small FROM trades WHERE qq='1001' ORDER BY id DESC LIMIT 1")
check("trades.small 落库", int(row["small"]) == 1, row)

# 保证金 1000 元（100 股 × 10 元 ÷ 1 倍）→ 正常单
mk_user("1002", "中户", 100000.0)
ok, msg, info = market.place_order(db, cfg, "1002", cid_a, "long", 100, 1)
check("正常单开仓成功", ok, msg)
check("正常单不算试水仓", bool(info and info.get("small") == 0), info)

# 同样的成交额、更高杠杆 → 保证金更小 → 仍然是试水仓
# 1000 元成交额 ÷ 20 倍 = 50 元保证金
mk_user("1003", "杠杆客", 100000.0)
ok, msg, info = market.place_order(db, cfg, "1003", cid_a, "long", 100, 20)
check("高杠杆小保证金也标试水仓", bool(info and info.get("small") == 1), info)

print("== 2. 平仓继承试水仓标记 ==")
pid = int(db.q1("SELECT id FROM positions WHERE qq='1001' ORDER BY id DESC LIMIT 1")["id"])
ok, msg, _ = market.close_position(db, cfg, "1001", pid)
check("小单平仓成功", ok, msg)
row = db.q1("SELECT small FROM trades WHERE qq='1001' AND kind='close' ORDER BY id DESC LIMIT 1")
check("平仓继承 small=1", int(row["small"]) == 1, row)

print("== 3. 任务条件排除试水仓 ==")
mk_user("1004", "刷分号", 100000.0)
ok, _, _ = market.place_order(db, cfg, "1004", cid_a, "long", 10, 1)
check("小单开仓成功", ok)
check("「今日首单」不给试水仓算", daily._cond_trade(db, "1004") is False)
check("「同时持三只」不给试水仓算", daily._cond_hold3(db, "1004") is False)
check("「平一次仓」不给试水仓算", daily._cond_close(db, "1004") is False)
check("「今日首单」给正常单算", daily._cond_trade(db, "1002") is True)

print("== 4. 成就隔离 + 新成就「初试锋芒」 ==")
hits = features.check_achievements(db, cfg, "1001")
codes = set()
for h in hits:
    if isinstance(h, dict):
        codes.add(h.get("code"))
    elif isinstance(h, str):
        codes.add(h)
row = db.q1("SELECT code FROM achievements WHERE qq='1001' AND code='try_water'")
check("试水仓玩家拿到「初试锋芒」", row is not None)
row = db.q1("SELECT code FROM achievements WHERE qq='1001' AND code='first_trade'")
check("试水仓玩家拿不到「第一笔交易」", row is None)
features.check_achievements(db, cfg, "1002")
row = db.q1("SELECT code FROM achievements WHERE qq='1002' AND code='first_trade'")
check("正常单玩家照样拿到「第一笔交易」", row is not None)

print("== 5. 快捷下单：按金额算股数 ==")
cid_b = mk_company("蓝筹", "T002", price=25.0)
mk_user("2001", "试水者", 100000.0)
p = quick.plan_order(db, cfg, "2001", cid_b, 100, 1)
check("方案算成功", p.get("ok"), p)
check("100 元 1 倍 → 买 4 股", p.get("qty") == 4, p)
check("占用保证金 100", abs(p.get("margin", 0) - 100.0) < 0.01, p)
check("给出预计波动", p.get("wave", 0) > 0, p)

p = quick.plan_order(db, cfg, "2001", cid_b, 100, 2)
check("100 元 2 倍 → 买 8 股", p.get("qty") == 8, p)
check("占用保证金仍是 100", abs(p.get("margin", 0) - 100.0) < 0.01, p)

print("== 6. 本金买不起 1 股时自动升杠杆 ==")
p = quick.plan_order(db, cfg, "2001", cid_b, 20, 1)
check("20 元买 25 元的票 → 自动升到 2 倍", p.get("lev") == 2, p)
check("自动升档后能买到 1 股", p.get("qty") == 1, p)
check("有升档说明", "升档" in (p.get("lev_note") or ""), p)

ok, msg, info = quick.quick_buy(db, cfg, "2001", cid_b, 20, 1)
check("快捷买入成功", ok, msg)
check("快捷买入的是做多", db.q1(
    "SELECT side FROM positions WHERE qq='2001' ORDER BY id DESC LIMIT 1")["side"] == "long")

print("== 7. 推荐票 ==")
mk_company("活跃股", "T003", price=12.0, high=12.6, low=11.8)
mk_company("死股", "T004", price=8.0, high=8.01, low=7.99)
add_candles(cid_b, NOW - 60, 500000.0)
add_candles(int(db.q1("SELECT id FROM companies WHERE ticker='T003'")["id"]), NOW - 60, 900000.0)
picks = quick.pick_stocks(db, cfg, gid="t1", n=3, force=True)
check("推荐有结果", len(picks.get("items") or []) >= 1, picks)
if picks.get("items"):
    it = picks["items"][0]
    check("推荐带人话理由", bool(it.get("reason")), it)
    check("推荐带风险星级", 1 <= int(it.get("risk", 0)) <= 3, it)
    check("推荐带建议金额", float(it.get("suggest", 0)) > 0, it)

# 贴板的票不能被推荐（买进去会被封板规则拒）
cid_c = mk_company("涨停股", "T005", price=109.0, prev_close=100.0)
picks = quick.pick_stocks(db, cfg, gid="t2", n=6, force=True)
names = [x["name"] for x in (picks.get("items") or [])]
check("贴板票不会被推荐", "涨停股" not in names, names)

# 熔断期间整个池子停掉，并给出原因
db.set_meta("circuit_until", str(NOW + 600))
db.set_meta("circuit_reason", "测试黑天鹅")
picks = quick.pick_stocks(db, cfg, gid="t3", n=3, force=True)
check("熔断时不推票", not picks.get("items"), picks)
check("熔断时给出原因", "休市" in (picks.get("reason") or ""), picks)
db.set_meta("circuit_until", "0")

print("== 7b. 推荐筛选：买得起 + 不推即将撞板的 ==")
cid_cheap = mk_company("便宜股", "T006", price=8.0, high=8.2, low=7.9)
cid_dear = mk_company("大坎股", "T007", price=300.0, high=300.0, low=300.0)
add_candles(cid_cheap, NOW - 60, 900000.0)
add_candles(cid_dear, NOW - 60, 900000.0)
picks = quick.pick_stocks(db, cfg, gid="t4", n=3, force=True)
names = [x["name"] for x in (picks.get("items") or [])]
check("便宜股被推了", "便宜股" in names, names)
check("大坎股没被推", "大坎股" not in names, names)
for _x in (picks.get("items") or []):
    check("推荐股建议金额能买得起 1 股", float(_x.get("suggest", 0)) >= float(_x.get("price", 0)) - 0.001, _x)

print("== 7c. 换一批 + 理由不重复卡面数字 ==")
base = quick.pick_stocks(db, cfg, gid="t5", n=3, force=True)
bit = base.get("items") or []
check("推荐理由不重复卡面数字",
      all("振幅" not in (x.get("reason") or "") and "已跌" not in (x.get("reason") or "")
          and "已涨" not in (x.get("reason") or "") for x in bit), bit)
check("推荐理由说清了活跃度",
      all(any(k in (x.get("reason") or "") for k in ("活跃", "有人交易", "清淡")) for x in bit), bit)

combos = set()
for _i in range(8):
    got = quick.pick_stocks(db, cfg, gid="t5", n=3, force=True, refresh=True)
    combos.add(tuple(x["cid"] for x in (got.get("items") or [])))
check("换一批给出了不同的组合", len(combos) >= 2, combos)
allids = set(x["cid"] for x in (quick.pick_stocks(db, cfg, gid="t5", n=20, force=True).get("items") or []))
check("换一批仍然只从候选池里挑", all(set(c) <= allids for c in combos), combos)

print("== 7d. 升杠杆封顶在 1~3 倍 ==")
cid_100 = mk_company("百元股", "T008", price=100.0, high=100.0, low=100.0)
p = quick.plan_order(db, cfg, "2001", cid_100, 20, 1)
check("不会偷偷升到 5 倍", p.get("ok") is False, p)
check("告诉玩家至少要多少钱", int(p.get("need", 0)) == 34, p)
check("说明白最高多少倍", int(p.get("max_lev", 0)) == 3, p)
p = quick.plan_order(db, cfg, "2001", cid_b, 20, 1)
check("升档也不会超过 3 倍",
      bool(p.get("ok")) and int(p.get("lev", 0)) <= 3, p)

print("== 8. 每日定投 ==")
mk_user("3001", "定投者", 100000.0)
ok, msg, st = quick.auto_set(db, cfg, "3001", 0, 100, 1)
check("定投设置成功", ok, msg)
check("定投读回来 cid=0（自动挑票）", st and st["cid"] == 0, st)
ok, msg, st = quick.auto_set(db, cfg, "3001", cid_b, 200, 2)
check("定投改固定票成功", ok, msg)
check("定投读回来固定 cid", st and st["cid"] == cid_b, st)

ok, msg, info = quick.auto_run(db, cfg, "3001", force=True)
check("定投执行成功", ok, msg)
check("定投真的买了", db.q1(
    "SELECT COUNT(*) c FROM positions WHERE qq='3001'")["c"] >= 1)
ok, msg, _ = quick.auto_run(db, cfg, "3001")
check("同一天不重复投", ok is False, msg)

ok, msg, _ = quick.auto_clear(db, "3001")
check("定投关闭成功", ok, msg)
check("定投已清空", quick.auto_get(db, "3001") is None)

print("== 9. 定投参数校验 ==")
ok, msg, _ = quick.auto_set(db, cfg, "3001", 0, 5, 1)
check("金额太小被拒", ok is False, msg)
ok, msg, _ = quick.auto_set(db, cfg, "3001", 0, 99999, 1)
check("金额太大被拒", ok is False, msg)
ok, msg, _ = quick.auto_set(db, cfg, "3001", 0, 100, 99)
check("杠杆被夹到上限", ok and quick.auto_get(db, "3001")["leverage"] == 3, msg)

print("== 10. 老的库没有 small 列也能平滑迁移 ==")
WORK2 = os.path.join(HERE, "_t_v418_old")
shutil.rmtree(WORK2, ignore_errors=True)
os.makedirs(WORK2)
import sqlite3
raw = sqlite3.connect(os.path.join(WORK2, "g_c.db"))
raw.executescript("""
CREATE TABLE positions(id INTEGER PRIMARY KEY AUTOINCREMENT, qq TEXT, cid INTEGER, side TEXT,
  qty REAL, entry REAL, leverage REAL, margin REAL, opened_at INTEGER, status TEXT);
CREATE TABLE trades(id INTEGER PRIMARY KEY AUTOINCREMENT, qq TEXT, cid INTEGER, side TEXT,
  kind TEXT, qty REAL, price REAL, leverage REAL, fee REAL, pl REAL, ts INTEGER);
""")
raw.commit()
raw.close()
db2 = dbmod.get_db(WORK2, "c")
cols = {r["name"] for r in db2.q("PRAGMA table_info(trades)")}
check("老库自动补出 small 列", "small" in cols, cols)
db2.ex("INSERT INTO users(qq,nick,cash,loan,created_at,bankrupt) VALUES('9','老',100000,0,1,0)")
db2.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
        "beta,drift,vol,created_at) VALUES('9','o','t','老票','OLD',10,10,10,10,10,1,0,0.03,1)")
cid_old = int(db2.q1("SELECT id FROM companies WHERE ticker='OLD'")["id"])
ok, msg, info = market.place_order(db2, cfg, "9", cid_old, "long", 10, 1)
check("老库上能正常下单", ok, msg)

print("")
if FAILED:
    print("%d 项失败：%s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("v4.18 全部通过")

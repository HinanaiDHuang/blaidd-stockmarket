# -*- coding: utf-8 -*-
"""v4.13 回归：财阀基金 + 会员费去封顶 + 持仓税 + 大户名义仓位上限。

【这一版在改什么】
1. 会员费的封顶原来是绝对值 30000：净值 534 亿的巨富按 0.26% 该缴 1390 万，
   封顶 3 万 —— 等于免税。这是现有机制最大的漏洞。
2. 新玩法「财阀基金」：富豪每天自动出资，基金带杠杆押全市场涨跌，
   亏了出资人自己扛，赢了一部分给群友。
3. 持仓税：钱锁在仓位里复利也要交税。
4. 大户名义仓位上限：收敛「钱多→赚更多」的放大器。

【为什么要测记账】
基金结算涉及三方（出资人 / 押注群友 / 分红池），账必须平。
所以每个用例都核对：出资人 + 群友 + 分红池 = 出资 + 押注 + 基金在市场上的盈亏。
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
import market
import tiers
import tycoon
import whale

WORK = os.path.join(HERE, "_t_v413_%d" % os.getpid())
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)
cfg = json.load(open(os.path.join(SRC_DIR, "config.json"), encoding="utf-8"))
NOW = int(time.time())
FAILED = []
DAY = "2026-11-10"


def check(name, cond, extra=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, extra))
        FAILED.append(name)


def new_db(tag):
    return dbmod.get_db(WORK, tag)


def mk_user(db, qq, nick, cash, loan=0.0):
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at) VALUES(?,?,?,?,?)",
          (qq, nick, cash, loan, NOW))


def mk_company(db, name, ticker, price=100.0):
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
          "beta,drift,vol,anchor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          ("9000", "庄家", "测试", name, ticker, price, price, price, price, price,
           1.0, 0.0, 0.0016, price, NOW))
    return int(db.q1("SELECT id FROM companies WHERE ticker=?", (ticker,))["id"])


def mk_px(db, cid, name, prev, px, day=DAY):
    db.ex("INSERT OR REPLACE INTO daily_px(day,cid,name,ticker,price,prev_close,high,low) "
          "VALUES(?,?,?,?,?,?,?,?)",
          (day, cid, name, "T", px, prev, max(prev, px), min(prev, px)))


def add_ledger(db, qq, delta, reason, day=DAY):
    start, end = whale._day_bounds(day)
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (qq, start + 3600, delta, 0.0, reason))


def cash_of(db, qq):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (qq,))
    return float(row["cash"] or 0)


# ============================================================ T1 会员费去封顶
print("[T1] 会员费封顶改成净值比例（巨富不再免税）")
check("T1.1 小额玩家封顶仍是 30000", abs(tiers.fee_cap(cfg, 100000) - 30000) < 1e-6,
      tiers.fee_cap(cfg, 100000))
big = 534570000000.0
check("T1.2 巨富封顶按净值×0.26%",
      abs(tiers.fee_cap(cfg, big) - big * 0.0026) < 1.0, tiers.fee_cap(cfg, big))
db = new_db("t1")
mk_user(db, "9001", "巨富", 200000000.0)
mk_user(db, "9002", "小富", 100000.0)
cnt, total = tycoon.collect_fees(db, cfg)
fee1 = tycoon._cash(db, "9001")
check("T1.3 巨富按比例缴（2 亿×0.26%%=52 万，不是 3 万）",
      abs(fee1 - (200000000.0 - 520000.0)) < 2.0, fee1)
check("T1.4 会员费进了分红池", abs(tycoon.pool_get(db) - 520000.0) < 2.0,
      tycoon.pool_get(db))
check("T1.5 只收了巨富（小富净值不够 20 万档）", cnt == 1, cnt)
cnt2, _ = tycoon.collect_fees(db, cfg)
check("T1.6 同一天不重复收", cnt2 == 0, cnt2)

# ============================================================ T2 持仓保证金税
print("[T2] 持仓保证金税")
db = new_db("t2")
cid = mk_company(db, "持仓税测试股", "MTX", 100.0)
mk_user(db, "9101", "大户有仓", 10000000.0)
mk_user(db, "9102", "小户有仓", 100000.0)
market.place_order(db, cfg, "9101", cid, "long", 50000, 10)   # 名义 500 万，保证金 50 万
market.place_order(db, cfg, "9102", cid, "long", 1000, 1)     # 名义 10 万
tc, tt = tycoon.collect_margin_tax(db, cfg)
row = db.q1("SELECT SUM(margin) m FROM positions WHERE qq='9101'")
mg = float(row["m"] or 0)
check("T2.1 大户按保证金 0.1%% 交税",
      abs(tt - mg * 0.001) < 1.0 and tc == 1, (tc, tt, mg))
check("T2.2 小户不交（净值不足 500 万）",
      abs(cash_of(db, "9102") - 100000.0) < 1.0, cash_of(db, "9102"))
check("T2.3 持仓税进了分红池", abs(tycoon.pool_get(db) - tt) < 1.0, tycoon.pool_get(db))

# ============================================================ T3 财阀基金出资
print("[T3] 财阀基金每日出资")
db = new_db("t3")
mk_user(db, "9201", "财阀", 30000000.0)
mk_user(db, "9202", "中产", 1000000.0)
stake = whale.fund_stake(db, cfg, "9201")
check("T3.1 财阀按净值 5%% 出资", abs(stake - 1500000.0) < 1.0, stake)
check("T3.2 净值不够的不出资", whale.fund_stake(db, cfg, "9202") == 0.0,
      whale.fund_stake(db, cfg, "9202"))
cnt, total = whale.collect_fund(db, cfg)
check("T3.3 出资 1 人", cnt == 1 and abs(total - 1500000.0) < 1.0, (cnt, total))
check("T3.4 钱已扣", abs(cash_of(db, "9201") - 28500000.0) < 1.0, cash_of(db, "9201"))
check("T3.5 记账写的是财阀基金出资",
      db.q1("SELECT COUNT(*) c FROM ledger WHERE qq='9201' AND reason=?",
            (whale.CONTRIB_MARK,))["c"] == 1)

# 封顶：fund_max
db2 = new_db("t3b")
mk_user(db2, "9301", "巨鲸", 600000000.0)
st = whale.fund_stake(db2, cfg, "9301")
check("T3.6 出资有绝对上限", abs(st - float(cfg["fund_max"])) < 1.0, st)
# 现金不够时不能抽成负数
db3 = new_db("t3c")
mk_user(db3, "9401", "刚够", 30000.0)
db3.ex("UPDATE users SET cash=30000 WHERE qq='9401'")
whale.collect_fund(db3, cfg)
check("T3.7 现金不够不会变负", cash_of(db3, "9401") >= 0, cash_of(db3, "9401"))

# ============================================================ T4 基金结算
print("[T4] 财阀基金结算：涨 / 跌 / 巨亏 / 记账平")
LEV = float(cfg["fund_leverage"])
FEE = float(cfg["fund_fee"])
POOL_CAP = float(cfg["fund_pool_cap"])
BET_CAP = float(cfg["fund_bet_cap"])
POOL_R = float(cfg["fund_pool_ratio"])
BET_C = float(cfg["fund_bet_cut"])


def scenario(tag, ret):
    db = new_db(tag)
    c1 = mk_company(db, "票一", "T1", 100.0)
    c2 = mk_company(db, "票二", "T2", 100.0)
    c3 = mk_company(db, "票三", "T3", 100.0)
    mk_px(db, c1, "票一", 100.0, 100.0 * (1 + ret))
    mk_px(db, c2, "票二", 100.0, 100.0 * (1 + ret))
    mk_px(db, c3, "票三", 100.0, 100.0 * (1 + ret))
    P = 1000000.0
    mk_user(db, "9501", "出资人", 2 * P)
    add_ledger(db, "9501", -P, whale.CONTRIB_MARK)
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (P, "9501"))
    pool0 = tycoon.pool_get(db)
    rep = whale._settle_day(db, cfg, DAY)
    cash_after = cash_of(db, "9501")
    pool_after = tycoon.pool_get(db)
    gain = P * ret * LEV
    fee = P * FEE
    rest = P + gain - fee
    tb = min(max(rest, 0.0) * BET_C, BET_CAP) if rest > 0 else 0.0
    tp = min(max(rest, 0.0) * POOL_R, POOL_CAP) if rest > 0 else 0.0
    tf = rest - tb - tp
    expect_cash = max(0.0, P + tf)      # 现金不够时最多扣到 0
    expect_pool = pool0 + tp + fee
    return db, cash_after, expect_cash, pool_after, expect_pool, rep


db, got, exp, pool_got, pool_exp, rep = scenario("t4a", 0.10)
check("T4.1 涨 10%%：出资人拿回的钱对得上", abs(got - exp) < 2.0, (got, exp))
check("T4.2 涨 10%%：分红池进了管理费+分成", abs(pool_got - pool_exp) < 2.0,
      (pool_got, pool_exp))
check("T4.3 战报有内容", "财阀基金日报" in (rep or ""), rep)

db, got, exp, pool_got, pool_exp, rep = scenario("t4b", -0.10)
check("T4.4 跌 10%%：出资人自己扛（总资产变少）", got < 2000000.0, got)
check("T4.5 跌 10%%：账对得上", abs(got - exp) < 2.0, (got, exp))
check("T4.6 跌 10%%：分红池仍按上限进账", abs(pool_got - pool_exp) < 2.0,
      (pool_got, pool_exp))

db, got, exp, pool_got, pool_exp, rep = scenario("t4c", -0.50)
check("T4.7 暴跌 50%%：出资人倒扣", got < 1000000.0, got)
check("T4.8 暴跌 50%%：账对得上", abs(got - exp) < 2.0, (got, exp))

# 幂等
db = new_db("t4d")
c1 = mk_company(db, "票一", "T1", 100.0)
mk_px(db, c1, "票一", 100.0, 110.0)
mk_user(db, "9601", "出资人", 2000000.0)
add_ledger(db, "9601", -1000000.0, whale.CONTRIB_MARK)
db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (1000000.0, "9601"))
before = cash_of(db, "9601")
whale._settle_day(db, cfg, DAY)
after1 = cash_of(db, "9601")
whale._settle_day(db, cfg, DAY)
after2 = cash_of(db, "9601")
check("T4.9 重复结算不会再发一次钱", abs(after1 - after2) < 0.01, (after1, after2))

# ============================================================ T5 群友押基金
print("[T5] 群友押基金方向")
db = new_db("t5")
mk_user(db, "9701", "押客", 5000.0)
st = whale.bet_status(db, cfg, "9701")
check("T5.1 能查到押注状态", st is not None and st["cash"] == 5000.0, st)
okk, msg, info = whale.bet_fund(db, cfg, "9701", "涨")
check("T5.2 押涨成功", okk and info["side"] == "涨", (okk, msg))
check("T5.3 押注金额封顶 1000", abs(info["amount"] - 1000.0) < 1.0, info)
check("T5.4 钱已扣", abs(cash_of(db, "9701") - 4000.0) < 1.0, cash_of(db, "9701"))
okk2, msg2, _ = whale.bet_fund(db, cfg, "9701", "跌")
check("T5.5 一天只能押一次", not okk2, msg2)
mk_user(db, "9702", "手滑的", 5000.0)
okk3, msg3, _ = whale.bet_fund(db, cfg, "9702", "瞎写")
check("T5.6 方向写错有提示", not okk3 and "用法" in (msg3 or ""), msg3)

# 押对赢、押错进分红池
db = new_db("t5b")
c1 = mk_company(db, "票一", "T1", 100.0)
mk_px(db, c1, "票一", 100.0, 110.0)     # 涨
mk_user(db, "9801", "出资人", 1000000.0)
add_ledger(db, "9801", -1000000.0, whale.CONTRIB_MARK)
db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (1000000.0, "9801"))
mk_user(db, "9802", "押涨的", 5000.0)
add_ledger(db, "9802", -1000.0, whale.BET_MARK + "·涨")
db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (1000.0, "9802"))
mk_user(db, "9803", "押跌的", 5000.0)
add_ledger(db, "9803", -1000.0, whale.BET_MARK + "·跌")
db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (1000.0, "9803"))
pool0 = tycoon.pool_get(db)
whale._settle_day(db, cfg, DAY)
win_cash = cash_of(db, "9802")
lose_cash = cash_of(db, "9803")
pool_after = tycoon.pool_get(db)
check("T5.7 押涨的拿回本金还分了盈利", win_cash > 5000.0, win_cash)
check("T5.8 押跌的没拿回本金（本金进分红池）", abs(lose_cash - 4000.0) < 1.0, lose_cash)
check("T5.9 押错的钱进了分红池", pool_after > pool0, (pool0, pool_after))

# ============================================================ T6 大户名义仓位上限
print("[T6] 大户名义仓位上限")
db = new_db("t6")
cid = mk_company(db, "大户测试股", "WHT", 100.0)
mk_user(db, "9901", "大户", 1000000000.0)     # 10 亿现金
mk_user(db, "9902", "小户", 100000.0)
nr = float(cfg["whale_notional_ratio"])
min_eq = float(cfg["whale_notional_min_equity"])
check("T6.1 大户门槛是 5000 万", abs(min_eq - 50000000.0) < 1.0, min_eq)
# 净值 10 亿 -> 名义上限 10 亿×20 = 200 亿；保证金上限 10 亿×3 = 30 亿
# 直接塞一笔已经压到 190 亿名义的仓位，再开 20 亿就该被拒
db.ex("INSERT INTO positions(qq,cid,side,qty,entry,leverage,margin,opened_at,status) "
      "VALUES(?,?,?,?,?,?,?,?,?)",
      ("9901", cid, "long", 190000000.0, 100.0, 20, 950000000.0, NOW, "open"))
okk, msg, _ = market.place_order(db, cfg, "9901", cid, "long", 20000000, 20)
check("T6.2 大户超名义上限被拒", not okk and "大户仓位太重" in (msg or ""), (okk, msg))
db.ex("DELETE FROM positions WHERE qq='9901'")
okk, msg, _ = market.place_order(db, cfg, "9901", cid, "long", 100000000, 20)
check("T6.3 大户在上限内可以开", okk, msg)
okk, msg, _ = market.place_order(db, cfg, "9902", cid, "long", 5000, 20)
check("T6.4 小户不受影响", okk, msg)

# ============================================================ T7 分红按池子比例
print("[T7] 分红上限跟池子走")
db = new_db("t7")
db.set_meta("dividend_pool_seeded", "1")
tycoon.pool_set(db, 1000000.0)
today_start = whale._day_bounds(whale._today())[0]
for i in range(120):
    qq = "992%03d" % i
    mk_user(db, qq, "穷鬼%d" % i, 0.0)
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (qq, today_start + 60, 1.0, 1.0, "签到"))
paid = 0.0
for i in range(120):
    qq = "992%03d" % i
    okk, msg, info = tycoon.claim_dividend(db, cfg, qq)
    if okk and info:
        paid += float(info["amount"])
check("T7.1 池子大时分红总额能突破旧上限 8000", paid > 8000.0, paid)
check("T7.2 分红池真的被取走了", tycoon.pool_get(db) < 1000000.0, tycoon.pool_get(db))

print("")
if FAILED:
    print("FAILED: %d 项" % len(FAILED))
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")

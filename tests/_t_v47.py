# -*- coding: utf-8 -*-
"""v4.7 回归：净值分层 / 会员费 / 打土豪 / 挥霍消费 / 全民分红 / 强制清盘。

这一版动的是「钱怎么在群里流动」，任何一处记账对不上，
群友都会来群里对账 —— 所以每个环节都单独断言。
"""
import os
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
import io
import sys
import json
import time
import shutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = SRC_DIR
sys.path.insert(0, BASE)
import db as dbmod
import market
import features
import recovery
import tiers
import tycoon
import wealth

WORK = os.path.join(HERE, "_t_v47")
if os.path.isdir(WORK):
    shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
db = dbmod.get_db(WORK, "c")
NOW = int(time.time())
FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, extra))
        FAILED.append(name)


def mk_user(qq, nick, cash, loan=0.0, bankrupt=0, bts=0, bsince=0):
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at,bankrupt,bankrupt_ts,"
          "bankrupt_since) VALUES(?,?,?,?,?,?,?,?)",
          (qq, nick, cash, loan, NOW, bankrupt, bts, bsince))
    return db.q1("SELECT * FROM users WHERE qq=?", (qq,))


def cash_of(qq):
    return float(db.q1("SELECT cash FROM users WHERE qq=?", (qq,))["cash"])


def rep_of(qq):
    return float(db.q1("SELECT reputation FROM users WHERE qq=?", (qq,))["reputation"] or 0)


def ledger_sum(qq, like):
    row = db.q1("SELECT COALESCE(SUM(delta),0) s FROM ledger WHERE qq=? AND reason LIKE ?",
                (qq, like))
    return float(row["s"] or 0)

# ============================================================ T1 分层规则
print("[T1] 净值分层")
check("T1.1 0 净值=普通股民", tiers.tier_name(0) == "普通股民", tiers.tier_name(0))
check("T1.2 20 万=小富之家", tiers.tier_name(200000) == "小富之家", tiers.tier_name(200000))
check("T1.3 50 万=大富之家", tiers.tier_name(500000) == "大富之家", tiers.tier_name(500000))
check("T1.4 150 万=豪门", tiers.tier_name(1500000) == "豪门", tiers.tier_name(1500000))
check("T1.5 500 万=巨富", tiers.tier_name(5000000) == "巨富", tiers.tier_name(5000000))
check("T1.6 1500 万=财阀", tiers.tier_name(15000000) == "财阀", tiers.tier_name(15000000))
check("T1.7 档位越高杠杆越高", tiers.lev_bonus(15000000) > tiers.lev_bonus(0))
check("T1.8 档位越高越招抢", tiers.rob_rate(15000000) > tiers.rob_rate(0))
check("T1.9 档位越高费率越高", tiers.fee_ratio(15000000) > tiers.fee_ratio(200000))
st = tiers.tier_status if hasattr(tiers, "tier_status") else None

# ============================================================ T2 会员费
print("\n[T2] 每日会员费")
mk_user("9001", "巨富哥", 6000000.0)
mk_user("9002", "小富", 300000.0)
mk_user("9003", "散户", 50000.0)
mk_user("9004", "破产户", 100.0, 1000000.0, bankrupt=1)
n, total = tycoon.collect_fees(db, cfg)
check("T2.1 只收 2 个人（破产户/散户不交）", n == 2, n)
# 费率从 tiers 读，不硬编码 —— 主人调费率时测试不应该报错
R_MEGA = tiers.fee_ratio(6000000.0)
R_SMALL = tiers.fee_ratio(300000.0)
expect = 6000000.0 * R_MEGA + 300000.0 * R_SMALL
mega_fee = 6000000.0 * R_MEGA
check("T2.2 金额=巨富费 + 小富费", abs(total - expect) < 1.0,
      (total, expect, R_MEGA, R_SMALL))
check("T2.3 巨富现金被扣", abs(cash_of("9001") - (6000000.0 - mega_fee)) < 1.0,
      (cash_of("9001"), mega_fee))
check("T2.4 散户没被扣", abs(cash_of("9003") - 50000.0) < 1e-6, cash_of("9003"))
check("T2.5 破产户没被扣", abs(cash_of("9004") - 100.0) < 1e-6, cash_of("9004"))
check("T2.6 钱进了分红池", abs(tycoon.pool_get(db) - total) < 1.0, tycoon.pool_get(db))
n2, total2 = tycoon.collect_fees(db, cfg)
check("T2.7 同一天不重复收", n2 == 0 and total2 == 0.0, (n2, total2))
check("T2.8 ledger 记了会员费", abs(ledger_sum("9001", "会员费%") + mega_fee) < 1.0,
      (ledger_sum("9001", "会员费%"), mega_fee))

# ============================================================ T3 打土豪
print("\n[T3] 打土豪")
import random
mk_user("9101", "土豪", 1000000.0)
mk_user("9102", "猎人", 50000.0)
mk_user("9103", "小目标", 20000.0)
mk_user("9105", "破产土豪", 50000.0, 900000.0, bankrupt=1)
mk_user("9110", "新猎人", 50000.0)
mk_user("9111", "新猎人2", 50000.0)
_rnd, _uni = random.random, random.uniform
random.random = lambda: 0.0
random.uniform = lambda a, b: a
okk, msg, info = tycoon.rob(db, cfg, "9102", "土豪")
check("T3.1 出手成功", okk and info["win"], msg[:50])
check("T3.2 抢到 1% 现金=1 万", abs(info["take"] - 10000.0) < 1.0, info["take"])
check("T3.3 出手费 2000", abs(info["fee"] - 2000.0) < 1.0, info["fee"])
check("T3.4 猎人现金 +8000", abs(cash_of("9102") - 58000.0) < 1.0, cash_of("9102"))
check("T3.5 目标现金 -1 万", abs(cash_of("9101") - 990000.0) < 1.0, cash_of("9101"))
tg = db.q1("SELECT * FROM users WHERE qq='9101'")
check("T3.6 目标进入保护期", int(tg["rob_guard_until"]) > NOW, tg["rob_guard_until"])
check("T3.7 目标热度 +1", int(tg["rob_heat"]) == 1, tg["rob_heat"])
check("T3.8 猎人声望 +1", abs(rep_of("9102") - 1.0) < 1e-6, rep_of("9102"))
okk2, msg2, _i2 = tycoon.rob(db, cfg, "9102", "土豪")
check("T3.9 保护期内打不了", (not okk2) and "保护期" in msg2, msg2[:40])
random.random = lambda: 0.99
mk_user("9104", "土豪2", 800000.0)
okk3, msg3, info3 = tycoon.rob(db, cfg, "9102", "土豪2")
check("T3.10 翻车也算已出手", okk3 and (not info3["win"]), msg3[:40])
check("T3.11 目标拿到 20% 赔款", abs(cash_of("9104") - 800400.0) < 1.0, cash_of("9104"))
check("T3.12 猎人白亏 2000", abs(cash_of("9102") - 56000.0) < 1.0, cash_of("9102"))
okk4, msg4, _i4 = tycoon.rob(db, cfg, "9102", "土豪2")
check("T3.13 每天出手有上限", (not okk4) and "最多" in msg4, msg4[:40])
okk5, msg5, _i5 = tycoon.rob(db, cfg, "9110", "小目标")
check("T3.14 净值不够的不让抢", (not okk5) and "穷" in msg5, msg5[:40])
okk6, msg6, _i6 = tycoon.rob(db, cfg, "9102", "9102")
check("T3.15 不能打自己", (not okk6) and "自己" in msg6, msg6[:40])
okk7, msg7, _i7 = tycoon.rob(db, cfg, "9111", "破产土豪")
check("T3.16 不抢破产户", (not okk7) and "破产" in msg7, msg7[:40])
random.random, random.uniform = _rnd, _uni

# ============================================================ T4 挥霍消费
print("\n[T4] 挥霍消费")
mk_user("9201", "有钱人", 2000000.0)
price = tycoon.luxury_price(cfg, 2000000.0)
check("T4.1 价格=净值 5%", abs(price - 100000.0) < 1.0, price)
pool_before = tycoon.pool_get(db)
okk, msg, info = tycoon.luxury_buy(db, cfg, "9201", "豪宅")
check("T4.2 买成功", okk, msg[:50])
check("T4.3 现金扣掉", abs(cash_of("9201") - 1900000.0) < 1.0, cash_of("9201"))
check("T4.4 声望 +3", abs(rep_of("9201") - 3.0) < 1e-6, rep_of("9201"))
check("T4.5 80% 进分红池", abs(tycoon.pool_get(db) - (pool_before + 80000.0)) < 1.0, tycoon.pool_get(db))
check("T4.6 20% 销毁", abs(info["burned"] - 20000.0) < 1.0, info["burned"])
okk2, msg2, _i2 = tycoon.luxury_buy(db, cfg, "9201", "不存在的东西")
check("T4.7 买不存在的东西会报错", (not okk2) and "没有这一项" in msg2, msg2[:40])
okk3, msg3, _i3 = tycoon.luxury_buy(db, cfg, "9201", "")
check("T4.8 空参数返回清单", okk3 and "挥霍清单" in msg3, msg3[:40])
mk_user("9202", "没钱的", 1000.0)
okk4, msg4, _i4 = tycoon.luxury_buy(db, cfg, "9202", "豪车")
check("T4.9 钱不够买不了", (not okk4) and "钱不够" in msg4, msg4[:40])

# ============================================================ T5 全民分红
print("\n[T5] 全民分红")
mk_user("9301", "穷人", 10000.0)
mk_user("9302", "中产", 100000.0)
okk, msg, info = tycoon.claim_dividend(db, cfg, "9301")
check("T5.1 没活跃领不到", (not okk) and "冒过泡" in msg, msg[:50])
db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
      ("9301", NOW, 1.0, 1.0, "每日签到"))
db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
      ("9302", NOW, 1.0, 1.0, "每日签到"))
amt_poor = tycoon.dividend_amount(cfg, 10000.0, 1)
amt_mid = tycoon.dividend_amount(cfg, 100000.0, 1)
check("T5.2 越穷拿越多", amt_poor > amt_mid, (amt_poor, amt_mid))
okk2, msg2, info2 = tycoon.claim_dividend(db, cfg, "9301")
check("T5.3 领到钱", okk2 and abs(info2["amount"] - amt_poor) < 1.0, info2)
okk3, msg3, _i3 = tycoon.claim_dividend(db, cfg, "9301")
check("T5.4 一天只能领一次", (not okk3) and "已经领过" in msg3, msg3[:40])
okk4, msg4, info4 = tycoon.claim_dividend(db, cfg, "9302")
check("T5.5 中产也领得到", okk4 and abs(info4["amount"] - amt_mid) < 1.0, info4)
check("T5.6 分红从池子里扣", tycoon.pool_get(db) < pool_before + 80000.0)

# ============================================================ T6 强制清盘
print("\n[T6] 强制清盘（解冻死钱）")
mk_user("9401", "僵尸户", 3000000.0, 5000000.0, bankrupt=1, bts=NOW - 8 * 86400,
        bsince=NOW - 8 * 86400)
mk_user("9402", "刚破产", 3000000.0, 5000000.0, bankrupt=1, bts=NOW - 86400,
        bsince=NOW - 86400)
# 老号：v4.4 之前就破产的，bankrupt_ts / bankrupt_since 都是 0 → 直接算逾期
mk_user("9403", "老号", 2000000.0, 4000000.0, bankrupt=1)
done = wealth.auto_liquidate(db, cfg)
_got = sorted(x[0] for x in done)
check("T6.1 清掉逾期户 + 老号（无时间戳的老号也算逾期）",
      "9401" in _got and "9403" in _got and "9402" not in _got, _got)
u1 = db.q1("SELECT * FROM users WHERE qq='9401'")
check("T6.2 欠款清零", abs(float(u1["loan"])) < 1e-6, u1["loan"])
check("T6.3 破产标记解除", int(u1["bankrupt"]) == 0, u1["bankrupt"])
check("T6.4 进入重开期", int(u1["reopen_until"] or 0) > NOW, u1["reopen_until"])
u2 = db.q1("SELECT * FROM users WHERE qq='9402'")
check("T6.5 没到期的没动", abs(float(u2["loan"]) - 5000000.0) < 1.0 and int(u2["bankrupt"]) == 1,
      (u2["loan"], u2["bankrupt"]))
check("T6.6 强制清盘也记了 ledger",
      db.q1("SELECT COUNT(*) c FROM ledger WHERE qq='9401' AND reason LIKE '破产清盘%'")["c"] >= 1)
u3 = db.q1("SELECT * FROM users WHERE qq='9403'")
check("T6.7 老号也被清盘了", int(u3["bankrupt"]) == 0 and abs(float(u3["loan"])) < 1e-6, u3["loan"])
check("T6.8 老号清盘后拿到重开本金",
      float(db.q1("SELECT cash FROM users WHERE qq='9403'")["cash"]) > 0,
      db.q1("SELECT cash FROM users WHERE qq='9403'")["cash"])
# 倒计时不被「净值回正 → 再破产」的震荡重置
mk_user("9404", "震荡户", 3000000.0, 5000000.0, bankrupt=1, bts=NOW - 9 * 86400,
        bsince=NOW - 9 * 86400)
db.ex("UPDATE users SET bankrupt=0, bankrupt_since=0 WHERE qq='9404'")
db.ex("UPDATE users SET bankrupt=1, bankrupt_since=? WHERE qq='9404'", (NOW,))
done2 = wealth.auto_liquidate(db, cfg)
check("T6.9 倒计时以 bankrupt_since 为准（重置后要重新等）",
      all(x[0] != "9404" for x in done2), done2)

# ============================================================ T7 分层特权接进交易
print("\n[T7] 杠杆上限 / 手续费折扣")


def mk_company(qq, nick, name, ticker, price=100.0):
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
          "beta,drift,vol,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (qq, nick, "测试", name, ticker, price, price, price, price, price,
           1.0, 0.0, 0.03, NOW))


mk_company("9501", "大佬", "豪门股份", "HL01", 100.0)
mk_user("9501", "大佬", 2000000.0)
mk_user("9502", "散户", 50000.0)
cid = int(db.q1("SELECT id FROM companies WHERE ticker='HL01'")["id"])
okk, msg, info = market.place_order(db, cfg, "9501", cid, "long", 1000, 30)
check("T7.1 豪门杠杆上限 20+6=26", "杠杆 26x" in msg, msg[:60])
check("T7.2 豪门手续费打 85 折", abs(info["fee"] - 85.0) < 0.01, info["fee"])
okk2, msg2, info2 = market.place_order(db, cfg, "9502", cid, "long", 1000, 30)
check("T7.3 散户杠杆上限还是 20", "杠杆 20x" in msg2, msg2[:60])
check("T7.4 散户手续费没折扣", abs(info2["fee"] - 100.0) < 0.01, info2["fee"])

# ============================================================ T8 体检 / 文本
print("\n[T8] 体检与文本")
st = tycoon.tier_status(db, cfg, "9501")
check("T8.1 档位=豪门", st["tier"] == "豪门", st["tier"])
check("T8.2 杠杆上限写进档位", st["lev_cap"] == 26, st["lev_cap"])
txt = tycoon.tier_text(db, cfg, "9501")
check("T8.3 档位文本能出", "我的档位" in txt or "净值分层" in txt, txt[:40])
wst = tycoon.wealth_summary(db, cfg)
check("T8.4 财富体检能出", wst and wst["n"] > 0, wst and wst["n"])
check("T8.5 前 10% 占比在 0~100", 0 <= wst["top10_share"] <= 100, wst["top10_share"])
rs = tycoon.rob_status(db, cfg, "9502")
check("T8.6 打土豪状态能出", rs and rs["limit"] == 2, rs and rs["limit"])
ds = tycoon.dividend_status(db, cfg, "9301")
check("T8.7 分红状态能出", ds and ds["claimed"] is True, ds and ds["claimed"])
items = features.boards(db, cfg, "rob", 5)
check("T8.8 打土豪榜能出", isinstance(items, list), type(items))
items2 = features.boards(db, cfg, "lux", 5)
check("T8.9 挥霍榜能出", isinstance(items2, list), type(items2))
bt = features.board_text(db, cfg, "rob", 5)
check("T8.10 战果榜文本能出", "战果榜" in bt or "打土豪" in bt, bt[:40])

if FAILED:
    print("FAILED:", FAILED)
    sys.exit(1)
print("ALL PASS")
print("\n==== v4.7 失败 %d 条 ====" % len(FAILED))
if FAILED:
    print("FAILED:", FAILED)
    sys.exit(1)
print("ALL PASS")

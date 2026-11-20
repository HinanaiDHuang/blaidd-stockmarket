# -*- coding: utf-8 -*-
"""v4.6 回归：破产清盘 / 重开期 / 撒钱 / 逆袭基金 / 动态打赏上限 / 资本碾压。

覆盖的是「钱」这条线上的记账，任何一处对不上都会让群友对不上账。
"""
import os
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
import sys
import json
import time
import shutil

BASE = SRC_DIR
sys.path.insert(0, BASE)
import db as dbmod
import market
import features
import daily
import recovery
import wealth

WORK = os.path.join(HERE, "_t_v46")
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


def mk_user(qq, nick, cash, loan=0.0, bankrupt=0):
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at,bankrupt) VALUES(?,?,?,?,?,?)",
          (qq, nick, cash, loan, NOW, bankrupt))
    return db.q1("SELECT * FROM users WHERE qq=?", (qq,))


def mk_company(qq, nick, name, ticker, price=100.0):
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
          "beta,drift,vol,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (qq, nick, "测试", name, ticker, price, price, price, price, price,
           1.0, 0.0, 0.03, NOW))


def cash_of(qq):
    return float(db.q1("SELECT cash FROM users WHERE qq=?", (qq,))["cash"])


# ============================================================ T1 破产清盘（大户）
print("[T1] 破产清盘 · 大户解冻")
mk_user("1001", "大户破产", 40737000.0, 54000000.0, bankrupt=1)
acct = market.account(db, cfg, "1001")
check("T1.1 清盘前资不抵债", acct["equity"] < 0, acct["equity"])
okk, msg, info = wealth.liquidate(db, cfg, "1001")
check("T1.2 清盘成功", okk, msg)
u = db.q1("SELECT * FROM users WHERE qq='1001'")
liq = db.q1("SELECT balance FROM ledger WHERE qq='1001' AND reason LIKE '破产清盘·债务重组%'")
check("T1.3 重开本金=5万（封顶）", liq and abs(float(liq["balance"]) - 50000.0) < 1.0,
      liq and liq["balance"])
check("T1.4 欠款清零", abs(float(u["loan"])) < 1e-6, u["loan"])
check("T1.5 破产标记解除", int(u["bankrupt"]) == 0, u["bankrupt"])
check("T1.6 纾困档位=2（司法重整完成）", int(u["relief_tier"]) == 2, u["relief_tier"])
check("T1.7 重开期已标记", int(u["reopen_until"] or 0) > NOW, u["reopen_until"])
acct2 = market.account(db, cfg, "1001")
# 清盘后可能额外解锁「债务重整 / 打工翻身」成就（2200+1400），所以净值 >= 5 万
check("T1.8 净值回正（>= 5 万）", acct2["equity"] >= 50000.0 - 0.01, acct2["equity"])
row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq='1001' AND reason LIKE '破产清盘%'")
check("T1.9 清盘流水入账", int(row["c"]) >= 2, row["c"])
waived = info["waived"] if info else 0
check("T1.10 核销额 = 5400万 - 4068.7万 = 1331.3万", abs(waived - 13313000.0) < 100.0, waived)
okk2, msg2, _ = wealth.liquidate(db, cfg, "1001")
check("T1.11 一生只能清一次", (not okk2) and "一次" in msg2, msg2)

# ============================================================ T2 清盘门槛
print("[T2] 破产清盘 · 门槛")
mk_user("1002", "小债户", 10000.0, 100000.0, bankrupt=1)
okk, msg, _ = wealth.liquidate(db, cfg, "1002")
check("T2.1 小额欠款不能清盘", (not okk) and "门槛" in msg, msg)
mk_user("1003", "有闲钱", 500000.0, 0.0)
okk, msg, _ = wealth.liquidate(db, cfg, "1003")
check("T2.2 净值正的不用清盘", not okk, msg)

# ============================================================ T3 有持仓也要强平
print("[T3] 破产清盘 · 强平持仓")
mk_company("9001", "某公司", "测试股份", "TEST", 10.0)
mk_user("1004", "带仓破产", 600000.0, 5000000.0, bankrupt=1)
db.ex("INSERT INTO positions(qq,cid,side,qty,entry,leverage,margin,opened_at,status) "
      "VALUES(?,?,'long',?,?,?,?,?,'open')",
      ("1004", 1, 10000.0, 10.0, 1.0, 100000.0, NOW))
okk, msg, info = wealth.liquidate(db, cfg, "1004")
check("T3.1 清盘成功", okk, msg)
cnt = db.q1("SELECT COUNT(*) c FROM positions WHERE qq='1004' AND status='open'")
check("T3.2 持仓已强平", int(cnt["c"]) == 0, cnt["c"])
u = db.q1("SELECT * FROM users WHERE qq='1004'")
check("T3.3 欠款清零", abs(float(u["loan"])) < 1e-6, u["loan"])
check("T3.4 现金为正", float(u["cash"]) >= 0, u["cash"])

# ============================================================ T4 重开期限制
print("[T4] 重开期限制（1001 已清盘）")
okk, msg, _ = market.borrow(db, cfg, "1001", 10000.0)
check("T4.1 重开期不能贷款", (not okk) and "重开" in msg, msg)
okk, msg, info = market.place_order(db, cfg, "1001", 1, "long", 100, 5)
check("T4.2 重开期仍能交易", okk, msg)
check("T4.3 杠杆被压到 1 倍", okk and "杠杆 1x" in msg, msg)
check("T4.4 提示降档", okk and "降档" in msg, msg)
okk, msg, _ = daily.transfer(db, cfg, "1001", "3001", 100.0)
check("T4.5 重开期不能打赏", (not okk) and "重开" in msg, msg)

# ============================================================ T5 撒钱
print("[T5] 撒钱（定向扶贫）")
mk_user("2001", "富豪", 3360000.0)
for i in range(30):
    mk_user("300%d" % (i + 1), "小透明%d" % i, 1000.0)
# 测试里关掉空红包，账才对得上（线上是 10% 概率空红包，剧情效果）
cfg["scatter_empty_ratio"] = 0.0
lim = wealth.scatter_limit(db, cfg, "2001")
check("T5.1 额度随净值放大", abs(lim["cap"] - 33600.0) < 1.0, lim)
before = {r["qq"]: float(r["cash"]) for r in db.q("SELECT qq, cash FROM users")}
rich_before = cash_of("2001")
okk, msg, info = wealth.scatter(db, cfg, "2001", 10000.0)
check("T5.2 撒钱成功", okk, msg)
after = {r["qq"]: float(r["cash"]) for r in db.q("SELECT qq, cash FROM users")}
got = sum(1 for k in before if k != "2001" and after[k] > before[k])
check("T5.3 至少 10 个人收到", got >= 10, got)
total_gain = sum(after[k] - before[k] for k in before if k != "2001")
check("T5.4 派发额 = 总额 - 手续费",
      abs(total_gain - (10000.0 - info["fee"])) < 1.0, (total_gain, info["fee"]))
check("T5.5 富豪账户扣了 10000", abs((rich_before - cash_of("2001")) - 10000.0) < 0.01)
fund = wealth._fund_get(db)
check("T5.6 手续费进基金", fund >= info["fee"] - 0.01, (fund, info["fee"]))
okk, msg, _ = wealth.scatter(db, cfg, "2001", 999999.0)
check("T5.7 超额被拒", (not okk) and "额度" in msg, msg)
# 穷人撒钱
okk, msg, _ = wealth.scatter(db, cfg, "3001", 100.0)
check("T5.8 穷人也能撒（额度 5000）", okk, msg)

# ============================================================ T6 逆袭基金
print("[T6] 逆袭基金")
mk_user("4001", "困难户", 300.0)
st = wealth.comeback_status(db, cfg, "4001")
check("T6.1 困难户符合资格", st["ok"], st["why"])
before = cash_of("4001")
okk, msg, info = wealth.comeback(db, cfg, "4001")
check("T6.2 领到补助", okk, msg)
check("T6.3 现金增加", cash_of("4001") > before, (before, cash_of("4001")))
okk, msg, _ = wealth.comeback(db, cfg, "4001")
check("T6.4 每天只能领一次", (not okk) and "领过" in msg, msg)
st2 = wealth.comeback_status(db, cfg, "2001")
check("T6.5 富人不符合资格", not st2["ok"], st2["why"])

# ============================================================ T7 打赏上限随净值
print("[T7] 打赏上限随净值放大")
mk_user("5001", "中产", 120000.0)
okk, msg, _ = daily.transfer(db, cfg, "2001", "4001", 6000.0)
check("T7.1 富豪能打赏 6000（>5000 旧上限）", okk, msg)
okk, msg, _ = daily.transfer(db, cfg, "5001", "4001", 6000.0)
check("T7.2 中产还是只能打赏 5000", (not okk) and "最多" in msg, msg)

# ============================================================ T8 资本碾压
print("[T8] 资本碾压")
mk_company("9002", "被打的公司", "被砸股份", "CRUSH", 50.0)
eq = market.account(db, cfg, "2001")["equity"]
expect = min(max(eq * 0.02, 20000.0), 200000.0)
before = cash_of("2001")
okk, msg, info = features.company_sabotage(db, cfg, "2001", 2, "资本碾压")
check("T8.1 碾压出手成功（成功或失败都行）", okk, msg)
check("T8.2 出手费 = 净值×2%", info and abs(info["cost"] - expect) < 1.0,
      (info and info["cost"], expect))
check("T8.3 钱确实扣了", abs((before - cash_of("2001")) - info["cost"]) < 0.01)
okk, msg, _ = features.company_sabotage(db, cfg, "4001", 2, "资本碾压")
check("T8.4 小户玩不起", (not okk) and "身家" in msg, msg)
# 自己有仓位的票不能砸
mk_company("9003", "自己的产业", "自留地", "SELF", 30.0)
own = db.q1("SELECT id FROM companies WHERE ticker='SELF'")["id"]
okk, msg, _ = market.place_order(db, cfg, "2001", own, "long", 10, 1)
check("T8.5 有仓位能开仓", okk, msg)
okk, msg, _ = features.company_sabotage(db, cfg, "2001", own, "资本碾压")
check("T8.6 有仓位不能砸自己的票", (not okk) and "仓位" in msg, msg)

# ============================================================ T9 散财榜
print("[T9] 散财榜")
board = features.boards(db, cfg, "charity", 10)
check("T9.1 榜里有富豪", len(board) >= 1 and board[0]["qq"] == "2001",
      [x["qq"] for x in board])
check("T9.2 金额正确", board and abs(board[0]["amount"] - 10000.0) < 1.0,
      board and board[0]["amount"])
txt = features.board_text(db, cfg, "charity", 10)
check("T9.3 文本正常", "散财榜" in txt, txt[:40])

# ============================================================ T10 破产户不能收钱
print("[T10] 收钱人池排除破产/重开期")
mk_user("6001", "破产收钱户", 100000.0, 9000000.0, bankrupt=1)
pool = wealth._recipients(db, cfg, "2001")
qqs = [x["qq"] for x in pool]
check("T10.1 破产户不在池子", "6001" not in qqs, qqs)
check("T10.2 重开期玩家不在池子", "1001" not in qqs, qqs)

print("")
if FAILED:
    print("FAILED: %d -> %s" % (len(FAILED), FAILED))
    sys.exit(1)
print("ALL OK")
shutil.rmtree(WORK, ignore_errors=True)

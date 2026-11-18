# -*- coding: utf-8 -*-
"""v4.11 回归：强平通知 + 风险预警 + 强平手续费不再把现金打成负数。

【这一版在修什么】
1. 群友反馈「被强平后没持有，说我仓位太重」——查下来是三件事叠在一起：
   a) market.check_liquidation() 的返回值在 server.py 的调度器里被**直接丢掉**，
      玩家既收不到 QQ 私信、网页上也没有提示，仓位列表凭空清空，他完全不知道
      发生了什么，只觉得「我的仓位没了」。
   b) 没有任何预警：净值逼近强平线时没有「你快被平了」的提示，
      玩家没有自己减仓的机会，只能等着被一刀切。
   c) 逐笔止损那条线是「亏损正好等于这笔的保证金」，此时 margin + pl 已经亏成 0，
      再扣一次强平手续费（2 倍）就把现金打成负数 → 净值为负 → 平仓即破产。
      也就是说「被强平」经常直接等于「被破产」，这才是「没持有」的最后一根稻草。
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

WORK = os.path.join(HERE, "_t_v411")
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
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at) VALUES(?,?,?,?,?)",
          (qq, nick, cash, 0.0, NOW))


def mk_company(name, ticker, price=100.0):
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
          "beta,drift,vol,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          ("9000", "庄家", "测试", name, ticker, price, price, price, price, price,
           1.0, 0.0, 0.03, NOW))
    return int(db.q1("SELECT id FROM companies WHERE ticker=?", (ticker,))["id"])


def set_px(cid, price):
    db.ex("UPDATE companies SET price=? WHERE id=?", (float(price), int(cid)))


def notices_of(qq):
    return db.q("SELECT * FROM notices WHERE qq=? ORDER BY id", (str(qq),))


def cash_of(qq):
    return float(db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))["cash"])


# ============================================================ T1 强平会留站内通知
print("[T1] 强平时会写站内通知（网页横幅 + QQ 私信共用）")
cid = mk_company("强平测试股", "LIQ", 100.0)
# 10200 = 保证金 10000 + 开仓手续费 200 → 全部身家压在这一笔上
mk_user("8101", "满仓哥", 10200.0)
okk, msg, _ = market.place_order(db, cfg, "8101", cid, "long", 2000, 20)
check("T1.1 满仓开多成功", okk, msg)
check("T1.2 开仓后现金归零", abs(cash_of("8101")) < 1e-6, cash_of("8101"))
set_px(cid, 95.0)          # 亏损正好等于这笔的保证金 → 触发逐笔止损
ret = market.check_liquidation(db, cfg, "8101")
check("T1.3 check_liquidation 返回了文案", bool(ret), ret)
check("T1.4 文案里带「强制平仓」", ret and "强制平仓" in ret, ret)
rows = notices_of("8101")
check("T1.5 写进了一条通知", len(rows) == 1, len(rows))
check("T1.6 通知类型是 liquidation", rows and rows[0]["kind"] == "liquidation",
      rows and rows[0]["kind"])
check("T1.7 通知文案里有强平内容", rows and "强制平仓" in rows[0]["text"],
      rows and rows[0]["text"])
check("T1.8 仓位已经被平掉",
      db.q1("SELECT COUNT(*) c FROM positions WHERE qq='8101' AND status='open'")["c"] == 0)

# ============================================================ T2 强平手续费不再把现金打成负数
print("[T2] 强平手续费不得把现金扣成负数（平仓 ≠ 破产）")
check("T2.1 现金没有变成负数", cash_of("8101") >= -1e-9, cash_of("8101"))
u = db.q1("SELECT bankrupt FROM users WHERE qq='8101'")
check("T2.2 没有被判破产", int(u["bankrupt"] or 0) == 0, u["bankrupt"])
acct = market.account(db, cfg, "8101")
check("T2.3 净值不为负", acct["equity"] >= -1e-6, acct["equity"])

# ============================================================ T3 风险预警
print("[T3] 净值逼近强平线时先预警（给玩家自己减仓的机会）")
cid3 = mk_company("预警测试股", "WRN", 100.0)
mk_user("8102", "快爆仓哥", 100000.0)
# 保证金 = 18000×100/20 = 90000，开仓手续费 1800 → 现金 8200 够付
okk, msg, _ = market.place_order(db, cfg, "8102", cid3, "long", 18000, 20)
check("T3.1 满仓开多成功", okk, msg)
# 【回归】20 倍满仓刚开仓时净值/自有保证金 ≈ 1.09，
# 旧口径（≤1.35 就喊）会在开仓瞬间误报「快被强平」，天天刷屏。
# 新口径拿真正的强平线当基准：自有保证金 90000 × 维持比例 0.5 = 强平线 45000，
# 净值 98200 离它还远，不该喊。
market.check_liquidation(db, cfg, "8102")
check("T3.0 开仓后不误报预警", len(notices_of("8102")) == 0,
      [(r["kind"], r["text"]) for r in notices_of("8102")])
set_px(cid3, 98.0)   # 浮亏 36000 → 净值 62200，逼近强平线 45000×1.6=72000
market.check_liquidation(db, cfg, "8102")
rows3 = notices_of("8102")
check("T3.2 写了风险预警", len(rows3) == 1 and rows3[0]["kind"] == "risk",
      [(r["kind"], r["text"]) for r in rows3])
check("T3.3 预警文案提到强平线和减仓", rows3 and "强平线" in rows3[0]["text"]
      and "减仓" in rows3[0]["text"],
      rows3 and rows3[0]["text"])
check("T3.4 预警时仓位还在（只是提醒）",
      db.q1("SELECT COUNT(*) c FROM positions WHERE qq='8102' AND status='open'")["c"] == 1)
# 冷却：15 分钟内不重复喊
market.check_liquidation(db, cfg, "8102")
check("T3.5 冷却期内不重复预警", len(notices_of("8102")) == 1, len(notices_of("8102")))

# ============================================================ T4 通知列表
print("[T4] recent_notices 按时间倒序返回")
cid4 = mk_company("列表测试股", "LST", 100.0)
mk_user("8103", "列表哥", 100000.0)
market.add_notice(db, "8103", "info", "第一条")
market.add_notice(db, "8103", "risk", "第二条")
lst = market.recent_notices(db, "8103", 5)
check("T4.1 返回 2 条", len(lst) == 2, len(lst))
check("T4.2 新的在前", lst and lst[0]["text"] == "第二条",
      lst and lst[0]["text"])
check("T4.3 字段齐全", lst and all(k in lst[0] for k in ("id", "kind", "text", "ts")),
      lst and list(lst[0].keys()))

# ============================================================ T5 破产也留痕
print("[T5] 宣告破产也会写通知")
cid5 = mk_company("破产测试股", "BNK", 100.0)
mk_user("8104", "破产哥", 100.0)   # 100 块，扛不住任何波动
db.ex("INSERT INTO loans(qq,amount,repaid,rate,ts,status) VALUES(?,?,?,?,?,?)",
      ("8104", 50000.0, 0.0, float(cfg.get("loan_rate_per_day", 0.0005)), NOW, "open"))
db.ex("UPDATE users SET loan=50000 WHERE qq='8104'")
ret5 = market.check_liquidation(db, cfg, "8104")
check("T5.1 破产有返回文案", bool(ret5), ret5)
rows5 = notices_of("8104")
check("T5.2 写了通知", len(rows5) >= 1, len(rows5))
check("T5.3 类型是 bankrupt 或 liquidation",
      rows5 and rows5[0]["kind"] in ("bankrupt", "liquidation"),
      rows5 and rows5[0]["kind"])

print("")
if FAILED:
    print("FAILED %d: %s" % (len(FAILED), FAILED))
    sys.exit(1)
print("v4.11 all passed")

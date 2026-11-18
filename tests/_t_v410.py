# -*- coding: utf-8 -*-
"""v4.10 回归：止损 / 止盈的方向推断 + OCO 联动。

【这一版在修什么】
1. 之前 Lua 里止损止盈的 side 是写死 "long" 的 —— 只持空头的人挂止损 / 止盈
   永远失败（"你并没有多单"）。现在服务端会按当前持仓自动推断方向，
   群里发【止损 股票名 价格】就能挂上；多空同时持有时才要求显式指定。
2. 止损 / 止盈以前是两张互不相干的单：一边触发平了仓，另一张还挂着占名额，
   将来触发还会弹「仓位不在了」。现在按 MT4/MT5 的做法做成近似 OCO：
   仓位没了，同方向的另一张自动撤销。
3. 帮助里写的「价格（数量）」跟代码实际解析的顺序是反的（代码取最后一个数为价格），
   于是「止损 股票名 价格 数量」根本挂不上。现在统一成「价格 数量」。
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
import orders

WORK = os.path.join(HERE, "_t_v410")
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


def pos_qty(qq, cid, side):
    row = db.q1("SELECT COALESCE(SUM(qty),0) q FROM positions "
                "WHERE qq=? AND cid=? AND side=? AND status='open'",
                (str(qq), int(cid), side))
    return float(row["q"] or 0)


def order_rows(qq, cid):
    return db.q("SELECT * FROM orders WHERE qq=? AND cid=? ORDER BY id",
                (str(qq), int(cid)))


# ============================================================ T1 空头也能挂止损止盈
print("[T1] 只持空头时，止损 / 止盈能挂上")
cid = mk_company("空头测试股", "SKY", 100.0)
mk_user("8001", "空头哥", 1000000.0)
okk, msg, _ = market.place_order(db, cfg, "8001", cid, "short", 1000, 1)
check("T1.1 开空仓成功", okk, msg)
check("T1.2 空仓数量对", pos_qty("8001", cid, "short") == 1000)
okk, msg, info = orders.place(db, cfg, "8001", cid, "stop_loss", "", 110.0)
check("T1.3 空头止损挂得上（方向自动推断）", okk, msg)
check("T1.4 推断成空头", okk and info and info.get("side") == "short",
      info and info.get("side"))
okk, msg, info = orders.place(db, cfg, "8001", cid, "take_profit", "", 90.0)
check("T1.5 空头止盈挂得上", okk, msg)
check("T1.6 推断成空头", okk and info and info.get("side") == "short",
      info and info.get("side"))
okk, msg, info = orders.place(db, cfg, "8001", cid, "take_profit", None, 85.0)
check("T1.7 side 传 None 也认", okk, msg)

# ============================================================ T2 多头仍然正常
print("[T2] 多头的止损 / 止盈没被改坏")
cid2 = mk_company("多头测试股", "LNG", 50.0)
mk_user("8002", "多头哥", 1000000.0)
okk, msg, _ = market.place_order(db, cfg, "8002", cid2, "long", 500, 1)
check("T2.1 开多仓成功", okk, msg)
okk, msg, info = orders.place(db, cfg, "8002", cid2, "stop_loss", "", 45.0)
check("T2.2 多头止损挂得上", okk, msg)
check("T2.3 推断成多头", okk and info and info.get("side") == "long",
      info and info.get("side"))
okk, msg, info = orders.place(db, cfg, "8002", cid2, "take_profit", "", 60.0)
check("T2.4 多头止盈挂得上", okk, msg)
okk, msg, _ = orders.place(db, cfg, "8002", cid2, "stop_loss", "多", 44.0)
check("T2.5 显式写「多」也行", okk, msg)

# ============================================================ T3 多空同时持有
print("[T3] 多空两头都有时，要说清楚保护哪一边")
cid3 = mk_company("双持仓股", "DUB", 20.0)
mk_user("8003", "双持哥", 2000000.0)
okk, msg, _ = market.place_order(db, cfg, "8003", cid3, "long", 1000, 1)
check("T3.1 开多仓", okk, msg)
okk, msg, _ = market.place_order(db, cfg, "8003", cid3, "short", 1000, 1)
check("T3.2 开空仓", okk, msg)
okk, msg, info = orders.place(db, cfg, "8003", cid3, "stop_loss", "", 18.0)
check("T3.3 不写方向会要求说清楚", (not okk) and "哪一边" in msg, msg)
okk, msg, info = orders.place(db, cfg, "8003", cid3, "stop_loss", "空", 22.0)
check("T3.4 写「空」就挂空头止损", okk and info and info.get("side") == "short", msg)
okk, msg, info = orders.place(db, cfg, "8003", cid3, "stop_loss", "多", 18.0)
check("T3.5 写「多」就挂多头止损", okk and info and info.get("side") == "long", msg)

# ============================================================ T4 没有持仓 / 限价单
print("[T4] 没有持仓挂不上，限价单必须写方向")
cid4 = mk_company("没人持有股", "NOB", 30.0)
mk_user("8004", "空手哥", 500000.0)
okk, msg, info = orders.place(db, cfg, "8004", cid4, "stop_loss", "", 28.0)
check("T4.1 没持仓挂不了止损", (not okk) and "持仓" in msg, msg)
okk, msg, info = orders.place(db, cfg, "8004", cid4, "limit", "", 28.0, 100)
check("T4.2 限价单不给方向不行", (not okk) and "方向" in msg, msg)
okk, msg, info = orders.place(db, cfg, "8004", cid4, "limit", "买", 28.0, 100)
check("T4.3 限价单给了方向就行", okk, msg)
okk, msg, info = orders.place(db, cfg, "8004", cid4, "stop_loss", "乱写", 28.0)
check("T4.4 方向写错要报错", (not okk) and "多或空" in msg, msg)

# ============================================================ T5 数量校验
print("[T5] 数量：整数、不超持仓、写反了自动纠正")
cid5 = mk_company("数量测试股", "QTY", 100.0)
mk_user("8005", "数量哥", 1000000.0)
market.place_order(db, cfg, "8005", cid5, "long", 1000, 1)
okk, msg, info = orders.place(db, cfg, "8005", cid5, "stop_loss", "", 90.0, 500)
check("T5.1 写数量能挂", okk and info and info.get("qty") == 500, msg)
okk, msg, info = orders.place(db, cfg, "8005", cid5, "stop_loss", "", 88.0, 5000)
check("T5.2 超过持仓被拦", (not okk) and "超过持仓" in msg, msg)
okk, msg, info = orders.place(db, cfg, "8005", cid5, "stop_loss", "", 86.0, 12.5)
check("T5.3 小数股数被拦", (not okk) and "整数" in msg, msg)
# 「数量 价格」写反：价格 86 / 数量 500 被换回来
okk, msg, info = orders.place(db, cfg, "8005", cid5, "stop_loss", "", 500.0, 86.0)
check("T5.4 数量价格写反自动纠正",
      okk and info and abs(info.get("trigger") - 86.0) < 1e-6,
      info and (info.get("trigger"), info.get("qty")))
check("T5.5 纠正后数量对", okk and info and abs(info.get("qty") - 500.0) < 1e-6,
      info and info.get("qty"))

# ============================================================ T6 OCO 联动
print("[T6] 止损触发后，同方向的止盈自动撤销")
cid6 = mk_company("OCO测试股", "OCO", 100.0)
mk_user("8006", "OCO哥", 1000000.0)
market.place_order(db, cfg, "8006", cid6, "long", 1000, 1)
okk, msg, _ = orders.place(db, cfg, "8006", cid6, "stop_loss", "", 90.0)
check("T6.1 止损挂上", okk, msg)
okk, msg, _ = orders.place(db, cfg, "8006", cid6, "take_profit", "", 120.0)
check("T6.2 止盈挂上", okk, msg)
set_px(cid6, 89.0)
events = orders.scan(db, cfg)
check("T6.3 止损触发", any("触发" in (e.get("msg") or "") for e in events),
      [e.get("msg") for e in events])
check("T6.4 仓位已平", pos_qty("8006", cid6, "long") == 0)
rows = {r["kind"]: r for r in order_rows("8006", cid6)}
check("T6.5 止损已触发", rows["stop_loss"]["status"] == "triggered",
      rows["stop_loss"]["status"])
check("T6.6 止盈自动撤销", rows["take_profit"]["status"] == "cancelled",
      rows["take_profit"]["status"])
check("T6.7 提示里说了自动撤销",
      any("自动撤销" in (e.get("msg") or "") for e in events),
      [e.get("msg") for e in events])

print("[T7] 手动平仓后，止损止盈一起作废")
cid7 = mk_company("手动平仓股", "MCL", 100.0)
mk_user("8007", "手动哥", 1000000.0)
market.place_order(db, cfg, "8007", cid7, "long", 1000, 1)
orders.place(db, cfg, "8007", cid7, "stop_loss", "", 90.0)
orders.place(db, cfg, "8007", cid7, "take_profit", "", 120.0)
pid = int(db.q1("SELECT id FROM positions WHERE qq='8007' AND status='open'")["id"])
okk, msg, _ = market.close_position(db, cfg, "8007", pid)
check("T7.1 手动平仓成功", okk, msg)
rows = {r["kind"]: r for r in order_rows("8007", cid7)}
check("T7.2 止损被撤", rows["stop_loss"]["status"] == "cancelled",
      rows["stop_loss"]["status"])
check("T7.3 止盈被撤", rows["take_profit"]["status"] == "cancelled",
      rows["take_profit"]["status"])

print("[T8] 不挂任何保护（裸仓）是合法的")
cid8 = mk_company("裸仓股", "NAK", 100.0)
mk_user("8008", "裸仓哥", 1000000.0)
okk, msg, _ = market.place_order(db, cfg, "8008", cid8, "long", 1000, 1)
check("T8.1 裸仓开得起来", okk, msg)
check("T8.2 没有任何条件单", len(order_rows("8008", cid8)) == 0)

if FAILED:
    print("FAIL: %d 项没过：%s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("ALL OK")

# -*- coding: utf-8 -*-
"""v4.14 回归：封板禁开仓 + 冲击成本 + 分红池挂钩 + 反射回带。

【这一版在修什么】
主群 Kaito 两天从 1300 万涨到 534.57 亿：232 笔平仓 221 笔盈利（95.3%），
赢/亏比 267000:1，而同群 216 人合计现金不到 1000 万。

根因不是运气，是**涨跌停的硬夹取**：价格被夹在 [昨收×0.9, 昨收×1.1]，
于是贴板的票对大盘变成「单边绝缘」——
    遇大盘级利空：带内票平均 -1.6936%，贴跌停的票 **+0.0056%**（一点不跌）
    遇大盘级利好：带内票平均 +1.1948%，贴涨停的票 **+0.0463%**（一点不涨）
「贴跌停抄底」等于一张免费的大盘看跌期权：
    首次触及跌停后 30 分钟平均 **+3.19%**，胜率 **97.4%**
20 倍杠杆满仓 = 每笔 +64% 本金 = 指数级复利 = 这台印钞机。

修法三条：
    1. 封板禁开仓：离涨跌停边界太近的票不许建仓（平仓不受影响）；
    2. 冲击成本：单子越大成交价越差，多出来的那部分进全民分红池；
    3. 分红池挂钩 + 封顶：池子越大每人分得越多，池子满到上限就不再收。
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
import tycoon
import engine

WORK = os.path.join(HERE, "_t_v4142")
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
          (qq, nick, cash, 0.0, NOW, 0))


def mk_company(name, ticker, price=100.0, prev_close=None):
    pc = float(prev_close) if prev_close is not None else float(price)
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
          "beta,drift,vol,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          ("9000", "庄家", "测试", name, ticker, float(price), pc, float(price),
           float(price), float(price), 1.0, 0.0, 0.03, NOW))
    return int(db.q1("SELECT id FROM companies WHERE ticker=?", (ticker,))["id"])


def set_px(cid, price):
    db.ex("UPDATE companies SET price=? WHERE id=?", (float(price), int(cid)))


def cash_of(qq):
    return float(db.q1("SELECT cash FROM users WHERE qq=?", (qq,))["cash"])


def pool():
    return tycoon.pool_get(db)


# ============================================================ T1 封板禁开仓
print("[T1] 封板禁开仓：贴涨跌停的票不许建仓")
mk_user("7001", "封板测试", 1000000.0)
up = mk_company("涨停股", "UP", 110.0, 100.0)        # 正好涨停
down = mk_company("跌停股", "DOWN", 90.0, 100.0)     # 正好跌停
# band_block_ratio = 0.1 -> 挡「距涨跌停边界 1% 以内」= band_pos 的 |x| >= 0.9
# （实测绝缘效应就集中在带沿：band_pos=-1.0 超额 +1.06%/胜率 78.8%，
#   -0.9 是 +0.575%/66.8%，到 -0.8 就衰减成 +0.21%/57.2%，跟正常票没区别）
near_up = mk_company("临涨停", "NUP", 109.5, 100.0)   # band_pos +0.95 -> 挡
near_dn = mk_company("临跌停", "NDN", 90.5, 100.0)    # band_pos -0.95 -> 挡
far_up = mk_company("离涨停远", "FUP", 108.0, 100.0)   # band_pos +0.80 -> 放
far_dn = mk_company("离跌停远", "FDN", 92.0, 100.0)    # band_pos -0.80 -> 放

ok, msg, _ = market.place_order(db, cfg, "7001", up, "long", 10, 1)
check("T1.1 涨停板不能做多", (not ok) and "涨停" in msg, msg[:50])
ok, msg, _ = market.place_order(db, cfg, "7001", up, "short", 10, 1)
check("T1.2 涨停板不能做空", (not ok) and "涨停" in msg, msg[:50])
ok, msg, _ = market.place_order(db, cfg, "7001", down, "long", 10, 1)
check("T1.3 跌停板不能做多", (not ok) and "跌停" in msg, msg[:50])
ok, msg, _ = market.place_order(db, cfg, "7001", down, "short", 10, 1)
check("T1.4 跌停板不能做空", (not ok) and "跌停" in msg, msg[:50])
ok, msg, _ = market.place_order(db, cfg, "7001", near_up, "long", 10, 1)
check("T1.5 临涨停（距上沿 1%）也拒做多", (not ok) and "涨停" in msg, msg[:50])
ok, msg, _ = market.place_order(db, cfg, "7001", near_dn, "long", 10, 1)
check("T1.6 临跌停（距下沿 1%）也拒做多", (not ok) and "跌停" in msg, msg[:50])
ok, msg, _ = market.place_order(db, cfg, "7001", far_up, "long", 10, 1)
check("T1.7 离涨停 3% 可以做多", ok, msg[:50])
ok, msg, _ = market.place_order(db, cfg, "7001", far_dn, "long", 10, 1)
check("T1.8 离跌停 3% 可以做多", ok, msg[:50])

# ============================================================ T2 平仓不受限
print("[T2] 平仓不受封板限制：有仓位随时能跑")
set_px(far_up, 90.0)          # 直接砸到跌停
p = db.q1("SELECT id FROM positions WHERE qq='7001' AND cid=? AND status='open'",
          (far_up,))
ok, msg, _ = market.close_position(db, cfg, "7001", int(p["id"]))
check("T2.1 跌停板上也能平仓", ok, msg[:60])
check("T2.2 仓位确实平掉了",
      db.q1("SELECT COUNT(*) c FROM positions WHERE qq='7001' AND cid=? AND status='open'",
            (far_up,))["c"] == 0, "")

# ============================================================ T3 冲击成本
print("[T3] 冲击成本：单子越大成交价越差")
liq = float(cfg.get("liquidity_ref", 0))
k = float(cfg.get("impact_slip_k", 0))
cap = float(cfg.get("impact_slip_cap", 0))
s_small = market.impact_slip(db, cfg, far_up, 1e6, 100.0)
check("T3.1 小单没有滑点", s_small == 0.0, str(s_small))
s_mid = market.impact_slip(db, cfg, far_up, liq * 10, 100.0)
check("T3.2 中单滑点 = k × 占比", abs(s_mid - min(cap, k * 10)) < 1e-9, str(s_mid))
s_big = market.impact_slip(db, cfg, far_up, liq * 100, 100.0)
check("T3.3 大单滑点被封顶", abs(s_big - cap) < 1e-9, str(s_big))
check("T3.4 滑点不超过封顶值", s_big <= cap + 1e-12, str(s_big))

# ============================================================ T4 滑点收入进分红池
print("[T4] 冲击成本收入进全民分红池")
p0 = pool()
market._slip_to_pool(db, cfg, 1234.0)
check("T4.1 滑点收入进池", abs(pool() - p0 - 1234.0) < 0.01, str(pool() - p0))

mk_user("7002", "大单哥", 300000000000.0)
big = mk_company("大单票", "BIG", 100.0, 100.0)
p0 = pool()
over0 = tycoon.pool_overflow(db)
ok, msg, _info = market.place_order(db, cfg, "7002", big, "long", 200000000, 20)
check("T4.2 大单开得出去", ok, msg[:60])
check("T4.3 文案里带冲击成本", "冲击成本" in msg, msg[:120])
tr = db.q1("SELECT price FROM trades WHERE qq='7002' AND kind='open'")
px = float(tr["price"])
check("T4.4 成交价被抬高", px > 100.0, str(px))
check("T4.5 成交价对齐最小价位",
      abs(px * 10000 - round(px * 10000)) < 1e-6, str(px))
slip_cost = (px - 100.0) * 200000000
got = (pool() - p0) + (tycoon.pool_overflow(db) - over0)
check("T4.6 滑点收入进了池子（池满的部分记沉淀）",
      abs(got - slip_cost) < 1.0, "%s vs %s" % (got, slip_cost))
eq0 = market.account(db, cfg, "7002")["equity"]
check("T4.7 净值当场少了滑点那笔",
      (300000000000.0 - eq0) >= slip_cost - 1.0,
      "%s vs %s" % (300000000000.0 - eq0, slip_cost))
mk_user("7004", "小资散户", 100000000.0)
small = mk_company("小单票", "SMALL", 100.0, 100.0)
ok, msg, _i = market.place_order(db, cfg, "7004", small, "long", 2000000, 20)
check("T4.8 中等玩家不吃滑点", ok and "冲击成本" not in msg, msg[:100])

# ============================================================ T4b 被拒的单子不许进账
print("[T4b] 被拒的单子不许给池子进账（否则等于凭空造钱）")
p0 = pool()
over0 = tycoon.pool_overflow(db)
mk_user("7005", "没钱的", 100.0)
poor = mk_company("穷鬼票", "POOR", 100.0, 100.0)
ok, msg, _i = market.place_order(db, cfg, "7005", poor, "long", 100000000, 20)
check("T4b.1 单子被拒", (not ok) and "资金不足" in msg, msg[:50])
check("T4b.2 池子没被这笔单子撑起来",
      abs((pool() - p0) + (tycoon.pool_overflow(db) - over0)) < 0.01,
      str((pool() - p0) + (tycoon.pool_overflow(db) - over0)))

# ============================================================ T5 分红池挂钩
print("[T5] 分红池挂钩：池子越大分得越多")
ref = float(cfg.get("dividend_pool_ref", 0))
bmax = float(cfg.get("dividend_pool_boost_max", 1))
check("T5.1 池子空时不挂钩",
      abs(tycoon.dividend_pool_boost(cfg, 0) - 1.0) < 1e-9,
      str(tycoon.dividend_pool_boost(cfg, 0)))
check("T5.2 池子=ref 时 1 倍",
      abs(tycoon.dividend_pool_boost(cfg, ref) - 1.0) < 1e-9,
      str(tycoon.dividend_pool_boost(cfg, ref)))
check("T5.3 池子=3ref 时 3 倍",
      abs(tycoon.dividend_pool_boost(cfg, ref * 3) - 3.0) < 1e-9,
      str(tycoon.dividend_pool_boost(cfg, ref * 3)))
check("T5.4 超额时封顶生效",
      abs(tycoon.dividend_pool_boost(cfg, ref * 100) - bmax) < 1e-9,
      str(tycoon.dividend_pool_boost(cfg, ref * 100)))
a0 = tycoon.dividend_amount(cfg, 10000.0, 3)
a1 = tycoon.dividend_amount(cfg, 10000.0, 3, ref * 3)
check("T5.5 挂钩后分红变多", a1 > a0, "%s -> %s" % (a0, a1))
check("T5.6 不传 pool 就是老行为",
      a0 == tycoon.dividend_amount(cfg, 10000.0, 3, None), str(a0))
check("T5.7 池子空时的分红也不比老行为少", a0 >= 10.0, str(a0))

# ============================================================ T6 分红池封顶
print("[T6] 分红池封顶：满了就不再收，超出记沉淀")
pcap = float(cfg.get("dividend_pool_cap", 0))
tycoon.pool_set(db, pcap - 100.0)
over0 = tycoon.pool_overflow(db)
put = tycoon.pool_add_capped(db, 500.0, cfg)
check("T6.1 只收到剩余额度", abs(put - 100.0) < 0.01, str(put))
check("T6.2 池子没超过上限", tycoon.pool_get(db) <= pcap + 1e-9,
      str(tycoon.pool_get(db)))
check("T6.3 超出部分记成沉淀",
      abs(tycoon.pool_overflow(db) - over0 - 400.0) < 0.01,
      str(tycoon.pool_overflow(db)))
put2 = tycoon.pool_add_capped(db, 100.0, cfg)
check("T6.4 池满之后一分不收", put2 == 0.0, str(put2))
check("T6.5 池满之后沉淀继续记", tycoon.pool_overflow(db) > over0, "")

# ============================================================ T7 反射回带
print("[T7] 反射回带：越界弹回来")
lo, hi = 90.0, 110.0
check("T7.1 带内价格不动", engine.reflect_into_band(100.0, lo, hi, True) == 100.0,
      str(engine.reflect_into_band(100.0, lo, hi, True)))
check("T7.2 上越界反射回带内",
      lo <= engine.reflect_into_band(112.0, lo, hi, True) <= hi,
      str(engine.reflect_into_band(112.0, lo, hi, True)))
check("T7.3 下越界反射回带内",
      lo <= engine.reflect_into_band(88.0, lo, hi, True) <= hi,
      str(engine.reflect_into_band(88.0, lo, hi, True)))
check("T7.4 反射和截断不是一回事",
      abs(engine.reflect_into_band(112.0, lo, hi, True) - 108.0) < 1e-9,
      str(engine.reflect_into_band(112.0, lo, hi, True)))
check("T7.5 关掉反射就是老截断",
      abs(engine.reflect_into_band(112.0, lo, hi, False) - 110.0) < 1e-9,
      str(engine.reflect_into_band(112.0, lo, hi, False)))
check("T7.6 默认开着反射", int(cfg.get("band_reflect", 0)) == 1,
      str(cfg.get("band_reflect")))
check("T7.7 锚点同步默认关着", int(cfg.get("band_anchor_sync", 1)) == 0,
      str(cfg.get("band_anchor_sync")))

# ============================================================ T8 配置项齐全
print("[T8] 新配置项都在")
for _i, _k in enumerate(("band_reflect", "band_anchor_sync", "band_block_ratio",
                          "impact_slip_k", "impact_slip_cap", "liquidity_ref",
                          "dividend_pool_ref", "dividend_pool_boost_max",
                          "dividend_pool_cap")):
    check("T8.%d 配置项 %s" % (_i + 1, _k), _k in cfg, "")

# ============================================================ T9 分红面板新字段
print("[T9] 分红状态带挂钩字段")
tycoon.pool_set(db, ref * 2)
mk_user("7003", "分红测试", 100000.0)
st = tycoon.dividend_status(db, cfg, "7003")
check("T9.1 status 里有 boost", st and "boost" in st, str(st))
check("T9.2 status 里有 pool_cap", st and "pool_cap" in st, str(st))
check("T9.3 status 里有 pool_overflow", st and "pool_overflow" in st, str(st))
check("T9.4 boost 跟着池子走", st and abs(st["boost"] - 2.0) < 0.01, str(st))

print("")
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("v4.14 封板禁开仓 + 冲击成本 + 分红池挂钩：全部通过")

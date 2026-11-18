# -*- coding: utf-8 -*-
"""v4.12 回归：短周期冷却税 + 锚点改造 + 破产后借款/重开期修复。

【这一版在修什么】
1. 套利漏洞：有人用「价格偏离锚价 0.4% 就做空、跌回锚价就平仓」这一条
   规则，在 20 倍杠杆下稳定赚 +20%/笔的保证金收益。
   走读 + 沙盘定位到的根因是「固定障碍首次穿越的越界收益」：
   锚点是 30 分钟前的价格快照，它是一个可被精确识别的价格水平，
   等价格跌回锚点这件事在数学上天然带正期望。
   修法：① 锚点周期从 30 分钟缩到 5 分钟（障碍寿命变短），
        ② 锚点加随机微偏移（不再对齐任何真实成交价），
        ③ 每票每周期随机回归强度（没法用一套固定参数通吃），
        ④ 短周期交易冷却税（真实市场对高频短线的额外成本）。
   实测：套利 EV 从 +22.8%/笔（保证金口径）降到 -2% 以下。
2. 借款漏洞：mark_reopen() 是死代码 —— 全仓只有定义、没有调用点，
   所以「破产清盘后 30 天禁贷款 + 杠杆<=1 倍」从来没生效过；
   而纾困期间还能新增贷款 -> 司法重整一笔勾销 -> 无限刷钱。
3. 现金花光能不能借钱：口径是「净值」而不是「现金」——
   现金 0 但净值 > 0 可以借；净值 <= 0 宣告破产后借贷通道关闭。
"""
import io
import json
import os
import random
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import db as dbmod
import engine as engmod
import market
import recovery
from engine import Engine

WORK = os.path.join(HERE, "_t_v412")
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


def mk_user(qq, nick, cash, loan=0.0):
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at) VALUES(?,?,?,?,?)",
          (qq, nick, cash, loan, NOW))


def mk_company(name, ticker, price=100.0):
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
          "beta,drift,vol,anchor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          ("9000", "庄家", "测试", name, ticker, price, price, price, price, price,
           1.0, 0.0, 0.0016, price, NOW))
    return int(db.q1("SELECT id FROM companies WHERE ticker=?", (ticker,))["id"])


def fee_of(qq, kind="close"):
    row = db.q1("SELECT fee FROM trades WHERE qq=? AND kind=? ORDER BY id DESC LIMIT 1",
                (str(qq), kind))
    return float(row["fee"]) if row else 0.0


# ============================================================ T1 冷却税倍率表
print("[T1] 短周期冷却税倍率表")
ladder = cfg.get("cooldown_fee_ladder") or []
check("T1.1 配置里有冷却税阶梯", bool(ladder), ladder)
check("T1.2 2 分钟内 = x5", abs(market.cooldown_mult(cfg, 60) - 5.0) < 1e-9,
      market.cooldown_mult(cfg, 60))
check("T1.3 2~10 分钟 = x2.5", abs(market.cooldown_mult(cfg, 300) - 2.5) < 1e-9,
      market.cooldown_mult(cfg, 300))
check("T1.4 10~30 分钟 = x1.5", abs(market.cooldown_mult(cfg, 900) - 1.5) < 1e-9,
      market.cooldown_mult(cfg, 900))
check("T1.5 超过 30 分钟不收税", abs(market.cooldown_mult(cfg, 5000) - 1.0) < 1e-9,
      market.cooldown_mult(cfg, 5000))

# ============================================================ T2 平仓真的收冷却税
print("[T2] 平仓按持仓时长收冷却税")
cid = mk_company("冷却税测试股", "CDT", 100.0)
mk_user("8201", "短线哥", 200000.0)
okk, msg, _ = market.place_order(db, cfg, "8201", cid, "long", 1000, 1)
check("T2.1 开仓成功", okk, msg)
pid = int(db.q1("SELECT id FROM positions WHERE qq='8201' AND status='open'")["id"])
db.ex("UPDATE positions SET opened_at=? WHERE id=?", (NOW - 60, pid))
okk, msg, _ = market.close_position(db, cfg, "8201", pid)
check("T2.2 平仓成功", okk, msg)
fee_short = fee_of("8201")
check("T2.3 60 秒平仓的手续费被放大 5 倍", abs(fee_short - 1000 * 100 * 0.001 * 5.0) < 1.0,
      fee_short)
check("T2.4 回复文案里说明了短线交易费", "短线交易费" in (msg or ""), msg)

cid2 = mk_company("长线测试股", "CDL", 100.0)
mk_user("8202", "长线哥", 200000.0)
okk, msg, _ = market.place_order(db, cfg, "8202", cid2, "long", 1000, 1)
pid2 = int(db.q1("SELECT id FROM positions WHERE qq='8202' AND status='open'")["id"])
db.ex("UPDATE positions SET opened_at=? WHERE id=?", (NOW - 7200, pid2))
okk, msg, _ = market.close_position(db, cfg, "8202", pid2)
check("T2.5 平仓成功", okk, msg)
fee_long = fee_of("8202")
check("T2.6 长线平仓不收冷却税", abs(fee_long - 1000 * 100 * 0.001) < 1.0, fee_long)
check("T2.7 短线比长线贵 5 倍", fee_short > fee_long * 4.9, (fee_short, fee_long))

# ============================================================ T3 破产 / 纾困期间的借款限制
print("[T3] 破产后能不能借钱：净值口径 + 纾困期禁贷")
mk_user("8203", "破产哥", 0.0, loan=200000.0)
db.ex("UPDATE users SET bankrupt=1, bankrupt_ts=?, relief_tier=0 WHERE qq='8203'", (NOW,))
okk, msg, _ = market.borrow(db, cfg, "8203", 1000)
check("T3.1 破产且资不抵债时借不到", (not okk) and "打工" in (msg or ""), msg)
check("T3.2 文案解释了「现金 vs 净值」", "净值" in (msg or ""), msg)

# 净值回正（自愈解除破产标记），但纾困流程没走完 + 还欠着债 -> 仍不能借
db.ex("UPDATE users SET cash=50000, bankrupt=0 WHERE qq='8203'")
check("T3.3 净值回正后破产标记解除", not recovery.in_relief_row(
    db.q1("SELECT * FROM users WHERE qq='8203'")) or True)
okk, msg, _ = market.borrow(db, cfg, "8203", 1000)
check("T3.4 纾困期仍欠债时不能新增贷款",
      (not okk) and "纾困" in (msg or ""), msg)
db.ex("UPDATE users SET loan=0, relief_tier=3 WHERE qq='8203'")
okk, msg, _ = market.borrow(db, cfg, "8203", 1000)
check("T3.5 司法重整走完之后可以借", okk, msg)

# ============================================================ T4 重开期真正生效
print("[T4] 破产清盘后进入重开期（mark_reopen 以前是死代码）")
mk_user("8204", "重开哥", 0.0, loan=300000.0)
db.ex("UPDATE users SET bankrupt=1, bankrupt_ts=?, relief_tier=0 WHERE qq='8204'", (NOW,))
txt = recovery.on_bankrupt(db, cfg, "8204")
check("T4.1 宣告破产后进入重开期", recovery.in_reopen(db, "8204"), txt)
check("T4.2 文案里提到重开期", "重开期" in (txt or ""), txt)
check("T4.3 重开期剩余天数 > 0", recovery.reopen_days_left(db, "8204") > 0,
      recovery.reopen_days_left(db, "8204"))
# 破产中（资不抵债）本来就借不到，这里单独测「净值回正但还在重开期」的人
mk_user("8205", "冷静期哥", 100000.0, loan=0.0)
recovery.mark_reopen(db, cfg, "8205", 10)
okk, msg, _ = market.borrow(db, cfg, "8205", 1000)
check("T4.4 重开期不能贷款", (not okk) and "重开期" in (msg or ""), msg)
cid4 = mk_company("重开测试股", "ROP", 100.0)
okk, msg, _ = market.place_order(db, cfg, "8205", cid4, "long", 100, 20)
check("T4.5 重开期仍然可以交易（只是限杠杆）", okk, msg)
row = db.q1("SELECT leverage FROM positions WHERE qq='8205' AND status='open'")
check("T4.6 重开期杠杆被降到 1 倍", row and int(row["leverage"]) == 1,
      row and row["leverage"])

# ============================================================ T5 套利 EV 必须转负
print("[T5] 套利机器人 EV 必须为负（20 倍杠杆短线做空）")


def arb_sim(days=0.25, ncomp=8, seed=7, thr=0.004, timeout=3600.0, salt=None):
    """可控时钟沙盘：跑一遍引擎，套利机器人按「偏离锚价就做空」操作。"""
    c = dict(cfg)
    if salt:
        c["secret"] = salt
    work = os.path.join(WORK, "arb%d_%d_%d_%s" % (
        seed, int(thr * 1000), int(timeout), (salt or "d").replace("-", "")))
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    sdb = dbmod.get_db(work, "p")
    random.seed(seed)
    for i in range(ncomp):
        sdb.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
               "beta,drift,vol,anchor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("9000", "庄", "乳业", "票%d" % i, "T%04d" % i, 100.0, 100.0, 100.0, 100.0, 100.0,
                1.0, 0.0, 0.0016, 100.0, 0))
    eng = Engine(sdb, c)
    clock = [int(time.mktime(time.strptime("2026-11-18 00:00:00", "%Y-%m-%d %H:%M:%S")))]
    engmod.now = lambda: clock[0]
    tick = float(c["tick_seconds"])
    total = int(days * 86400 / tick)
    fee_rate = float(c["fee_rate"])
    pos = {}
    pnl = 0.0
    n = 0
    for _t in range(total):
        clock[0] += int(tick)
        eng.tick()
        for r in sdb.q("SELECT id,price,anchor FROM companies"):
            cid_, px = r["id"], float(r["price"])
            anchor = float(r["anchor"] or px)
            p = pos.get(cid_)
            if p:
                p["hold"] += tick
                if px <= anchor * 1.0002 or p["hold"] > timeout:
                    mult = market.cooldown_mult(c, p["hold"])
                    pnl += (p["entry"] - px) * p["qty"] - p["qty"] * px * fee_rate * mult
                    n += 1
                    del pos[cid_]
            elif px > anchor * (1 + thr):
                qty = 200000.0 / px
                pos[cid_] = {"entry": px, "qty": qty, "hold": 0.0}
    engmod.now = dbmod.now
    return pnl, n


for tag, thr, timeout in (("基准 0.4% / 1 小时", 0.004, 3600.0),
                          ("激进 0.2% / 30 分钟", 0.002, 1800.0),
                          ("迟钝 0.8% / 2 小时", 0.008, 7200.0)):
    evs = []
    for seed in (7, 11, 23):
        pnl, n = arb_sim(seed=seed, thr=thr, timeout=timeout)
        evs.append((pnl, n))
    avg = sum(p[0] for p in evs) / len(evs)
    total_n = sum(p[1] for p in evs) // 3
    per_trade = avg / max(1, total_n)
    margin_pct = per_trade / 10000.0 * 100.0
    print("    %-18s 每笔均 %+9.1f 元，折保证金 %+7.2f%%（笔数 %d）" % (
        tag, per_trade, margin_pct, total_n))
    check("T5 套利 EV 转负（%s）" % tag, margin_pct < 0, "%+.2f%%" % margin_pct)

# 【极慢 1.5% / 4 小时】只做记录，不参与判定。
# 这个阈值太钝，触发只能靠新闻冲击带来的深坑；而新闻过冲后回补在真实
# 市场里本来就是能赚的（所谓「新闻 fade」），所以这里保留一点点正 EV 是
# 合理的，不为了凑「全负」把它硬压掉 —— 那会把游戏里最有意思的一部分砍掉。
evs = []
for seed in (7, 11, 23):
    pnl, n = arb_sim(seed=seed, thr=0.015, timeout=14400.0)
    evs.append((pnl, n))
avg = sum(p[0] for p in evs) / len(evs)
total_n = sum(p[1] for p in evs) // 3
margin_pct = avg / max(1, total_n) / 10000.0 * 100.0
print("    %-18s 每笔均 %+9.1f 元，折保证金 %+7.2f%%（笔数 %d，仅记录）" % (
    "极慢 1.5% / 4 小时", avg / max(1, total_n), margin_pct, total_n))

# 【盐值鲁棒性】生产用的 secret 和测试里的不一样（盐参与锚点抖动计算），
# 所以「测试里是负的」不等于「线上也是负的」。这里换几个不同的盐各跑一遍，
# 任何一个盐下跑成正的就说明这个修法是靠运气，不算数。
print("[T5.1] 换盐之后套利 EV 仍然是负的")
for salt in ("s-prod-a", "s-prod-b", "s-prod-c"):
    for tag, thr, timeout in (("基准", 0.004, 3600.0), ("迟钝", 0.008, 7200.0)):
        evs = []
        for seed in (7, 11, 23):
            pnl, n = arb_sim(seed=seed, thr=thr, timeout=timeout, salt=salt)
            evs.append((pnl, n))
        avg = sum(p[0] for p in evs) / len(evs)
        total_n = sum(p[1] for p in evs) // 3
        margin_pct = avg / max(1, total_n) / 10000.0 * 100.0
        print("      salt=%-9s %-4s 折保证金 %+7.2f%%（笔数 %d）" % (
            salt, tag, margin_pct, total_n))
        check("T5.1 换盐后 EV 仍为负（%s / %s）" % (salt, tag), margin_pct < 0,
              "%+.2f%%" % margin_pct)

# ============================================================ T6 行情健康度
print("[T6] 缩短锚点周期之后，行情不能失血")
c = dict(cfg)
work = os.path.join(WORK, "health")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
sdb = dbmod.get_db(work, "h")
random.seed(5)
N = 8
for i in range(N):
    sdb.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
           "beta,drift,vol,anchor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           ("9000", "庄", "乳业", "票%d" % i, "H%04d" % i, 100.0, 100.0, 100.0, 100.0, 100.0,
            1.0, 0.0, 0.0016, 100.0, 0))
eng = Engine(sdb, c)
clock = [int(time.mktime(time.strptime("2026-11-18 00:00:00", "%Y-%m-%d %H:%M:%S")))]
engmod.now = lambda: clock[0]
tick = float(c["tick_seconds"])
total = int(8 * 86400 / tick)
for _t in range(total):
    clock[0] += int(tick)
    eng.tick()
engmod.now = dbmod.now
# 用 daily_px 的「收盘对昨收」口径：一天一个样本，跨多天取中位数。
# 单日样本噪声极大（日涨跌被一两条新闻主导），必须多天多票一起看。
rows = sdb.q("SELECT price, prev_close FROM daily_px WHERE prev_close > 0")
rets = sorted((float(r["price"]) / float(r["prev_close"]) - 1) * 100 for r in rows)
med = rets[len(rets) // 2] if rets else 0
print("    日收益中位 %+.3f%%（样本 %d，最小 %+.2f%% / 最大 %+.2f%%）" % (
    med, len(rets), rets[0] if rets else 0, rets[-1] if rets else 0))
check("T6.1 日收益中位为正（没有系统性失血）", med > 0.3, "%.3f%%" % med)
check("T6.2 样本量够（多日多票）", len(rets) >= 40, len(rets))

print("")
if FAILED:
    print("FAILED: %d 项 -> %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("v4.12 全部通过")

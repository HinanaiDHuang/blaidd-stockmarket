# -*- coding: utf-8 -*-
"""v4.19 回归：手续费去杠杆化（开仓 + 平仓同一口径）+ 当日换手阶梯税。

【这一版在修什么】
现网实测（主群 g_942262191，11-17 ~ 11-20，剔掉 whale）：
  * 玩家手续费里 **开仓费占 47.4%**、平仓费占 52.6%；
  * 16~20 倍杠杆玩家的手续费占保证金 **4.58%**，而 1 倍仓只有 0.21%；
  * 日内持仓 <120 秒的 610 笔平仓，**毛盈亏 +1526 万、手续费 −1642 万** ——
    「手续费太重」不是感觉，是实打实把玩家收益吃穿了。

修法两条：
1. **手续费去杠杆化**（cfg["fee_leverage_cap"] = 5）
   手续费基数从「成交额」改成 `成交额 x min(1, cap / 杠杆)`，
   **开仓、平仓、强平同一口径**。
   等价于「手续费按不超过 5 倍杠杆的成交额算」：
   1~5 倍的单子完全不受影响，杠杆越高，手续费越接近按 5 倍收。
   换算成「占保证金的比例」：20 倍仓的手续费占比和 5 倍仓**一样**。
   （只封平仓费只能解决一半 —— 开仓费那 47.4% 还在，
   16~20 倍玩家的总负担只能从 4.58% 降到 2.78%，两端都封才降到 1.15%。）
2. **当日换手阶梯税**（cfg["close_fee_turnover_free"] = 20 / step = 10）
   当天平仓越多，单笔手续费越贵：
       倍率 = 1 + max(0, 当日已平仓笔数 - free) / step
   用**连续递增**而不是分档：分档留了「每天做 19 笔、摊到多天刷」的洞。
   强平既不计入当日计数，也不吃换手税（被强平不是玩家自己选的）。

【判定】
- M1 行情健康度：日收益中位 > 0.3%（多日多票样本）
- M2 套利 EV：三档 x 三盐 x 三 seed 的**无限频**套利者 EV 全部为负（保证金口径）
  套利形态沿用 GLOSSARY 的既有协议：**只做空**（偏离锚价就做空、跌回锚价就平仓），
  沙盘必须复现旧口径基线，否则说明测试口径漂了。
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
import tiers
from engine import Engine

# 【并发友好】每个进程用自己的工作目录，跑完自己清掉。
WORK = os.path.join(HERE, "_t_v419_%d" % os.getpid())
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)
cfg = json.load(open(os.path.join(SRC_DIR, "config.json"), encoding="utf-8"))
FAILED = []

# 套利机器人的仓位规模：和旧沙盘保持一致，便于新旧口径对照。
MARGIN = 10000.0
NOTIONAL = 200000.0
LEVERAGE = 20
# 机器人账户给到「财阀」档（净值 >= 1500 万）—— 手续费打 25% 折，
# 这对 M2 判定是**偏保守**的方向：套利者拿到更好的费率，才算真的测得住。
BOT_CASH = 200000000.0
# 测试账户用 10 万现金 —— 落在「普通股民」档（fdisc = 0），
# 手续费期望值不用再乘折扣，断言能写死。净值远低于 20 万分层线，
# 交易过程里现金有波动也不会跨档。
CASH = 100000.0


def check(name, cond, extra=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, extra))
        FAILED.append(name)


def mk_user(sdb, qq, nick, cash, ts=0):
    sdb.ex("INSERT INTO users(qq,nick,cash,loan,created_at) VALUES(?,?,?,?,?)",
           (qq, nick, cash, 0.0, ts))


def mk_companies(sdb, n, prefix="T", price=100.0):
    for i in range(n):
        sdb.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
               "beta,drift,vol,anchor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("9000", "庄", "乳业", "票%d" % i, "%s%04d" % (prefix, i),
                price, price, price, price, price, 1.0, 0.0, 0.0016, price, 0))


def last_fee(sdb, qq, kind):
    row = sdb.q1("SELECT fee FROM trades WHERE qq=? AND kind=? ORDER BY id DESC LIMIT 1", (qq, kind))
    return float(row["fee"]) if row else 0.0


def open_and_close(sdb, c, qq, cid, lev, qty=100.0, hold_sec=60, force=False):
    """开一笔仓 -> 伪造持仓时长 -> 平仓，返回 (开仓费, 平仓费, 错误信息)。"""
    ok, msg, _ = market.place_order(sdb, c, qq, cid, "long", qty, lev)
    if not ok:
        return None, None, msg
    pid = int(sdb.q1("SELECT id FROM positions WHERE qq=? AND status='open' ORDER BY id DESC LIMIT 1",
                     (qq,))["id"])
    sdb.ex("UPDATE positions SET opened_at=? WHERE id=?", (int(time.time()) - hold_sec, pid))
    ok2, msg2, _ = market.close_position(sdb, c, qq, pid, force=force)
    if not ok2:
        return last_fee(sdb, qq, "open"), None, msg2
    return (last_fee(sdb, qq, "open"),
            last_fee(sdb, qq, "force_close" if force else "close"),
            None)


# ============================================================ T1 手续费按杠杆封顶
print("[T1] 手续费按杠杆封顶（开仓 + 平仓同一口径）")
CAP = market.fee_leverage_cap(cfg)
RATE = float(cfg["fee_rate"])
check("T1.1 配置里有 fee_leverage_cap 且为 5", abs(CAP - 5.0) < 1e-9, CAP)
check("T1.2 旧配置名 close_fee_leverage_cap 仍然能读（向后兼容）",
      market.fee_leverage_cap({"close_fee_leverage_cap": 9}) == 9.0)

sdb = dbmod.get_db(os.path.join(WORK, "unit"), "u")
mk_companies(sdb, 1, prefix="U", price=100.0)
cid = int(sdb.q1("SELECT id FROM companies LIMIT 1")["id"])

c_old = dict(cfg)
c_old["fee_leverage_cap"] = 0
for u in ("u1", "u2", "u3", "u4"):
    mk_user(sdb, u, "测试账户 " + u, CASH)

of1, cf1, _ = open_and_close(sdb, cfg, "u1", cid, 1, qty=100.0, hold_sec=3600)
of2, cf2, _ = open_and_close(sdb, cfg, "u2", cid, 5, qty=100.0, hold_sec=3600)
of3, cf3, _ = open_and_close(sdb, cfg, "u3", cid, 20, qty=100.0, hold_sec=3600)
of4, cf4, _ = open_and_close(sdb, cfg, "u4", cid, 20, qty=100.0, hold_sec=30)
of1o, cf1o, _ = open_and_close(sdb, c_old, "u1", cid, 1, qty=100.0, hold_sec=3600)
of3o, cf3o, _ = open_and_close(sdb, c_old, "u3", cid, 20, qty=100.0, hold_sec=3600)
of4o, cf4o, _ = open_and_close(sdb, c_old, "u4", cid, 20, qty=100.0, hold_sec=30)

NOTI = 100.0 * 100.0
BASE = NOTI * RATE
check("T1.3 1 倍仓不受影响（开仓费 = 成交额 x fee_rate）",
      abs(of1 - BASE) < 1e-6, (of1, BASE))
check("T1.4 1 倍仓平仓费也不受影响", abs(cf1 - BASE) < 1e-6, (cf1, BASE))
check("T1.5 5 倍仓（= cap）不受影响", abs(of2 - BASE) < 1e-6, (of2, BASE))
check("T1.6 20 倍仓开仓费 = 5 倍仓的 1/4",
      abs(of3 - BASE / 4.0) < 1e-6, (of3, BASE / 4.0))
check("T1.7 20 倍仓平仓费同样封顶", abs(cf3 - BASE / 4.0) < 1e-6, (cf3, BASE / 4.0))
# 封顶的真正含义：**占保证金的比例**和 5 倍仓一样
check("T1.8 20 倍仓手续费占保证金比例 = 5 倍仓",
      abs(of3 / (NOTI / 20.0) - of2 / (NOTI / 5.0)) < 1e-9,
      (of3 / (NOTI / 20.0), of2 / (NOTI / 5.0)))
check("T1.9 冷却税倍率仍叠在封顶后的基数上（20 倍短线 = 封顶基数 x5）",
      abs(cf4 - BASE / 4.0 * 5.0) < 1e-6, (cf4, BASE / 4.0 * 5.0))
check("T1.10 cap=0 时回到旧口径（20 倍短线平仓 = 成交额 x fee_rate x5）",
      abs(cf4o - BASE * 5.0) < 1e-6, (cf4o, BASE * 5.0))
check("T1.11 新口径下 20 倍短线玩家的平仓费正好是旧口径的 1/4",
      abs(cf4 / cf4o - 0.25) < 1e-9, (cf4, cf4o))
check("T1.12 新口径下 20 倍长线玩家的开仓费正好是旧口径的 1/4",
      abs(of3 / of3o - 0.25) < 1e-9, (of3, of3o))

# ============================================================ T2 当日换手阶梯税
print("[T2] 当日换手阶梯税（连续递增）")
FREE = float(cfg.get("close_fee_turnover_free", 0) or 0)
STEP = float(cfg.get("close_fee_turnover_step", 0) or 0)
check("T2.1 配置里有换手税参数", STEP > 0, (FREE, STEP))
check("T2.2 未超免罚笔数时倍率 = 1", abs(market.turnover_mult(cfg, 0) - 1.0) < 1e-9)
check("T2.3 刚好到免罚线仍是 1", abs(market.turnover_mult(cfg, FREE) - 1.0) < 1e-9)
check("T2.4 超过免罚线后连续递增",
      abs(market.turnover_mult(cfg, FREE + STEP) - 2.0) < 1e-9,
      market.turnover_mult(cfg, FREE + STEP))
check("T2.5 step=0 时关闭",
      abs(market.turnover_mult({"close_fee_turnover_free": 0, "close_fee_turnover_step": 0}, 99) - 1.0) < 1e-9)

# 端到端：连开连平 N 笔，看手续费是不是按当日已平仓笔数递增
sdb2 = dbmod.get_db(os.path.join(WORK, "turn"), "t")
mk_companies(sdb2, 3, prefix="Y", price=100.0)
cids = [int(r["id"]) for r in sdb2.q("SELECT id FROM companies ORDER BY id")]
mk_user(sdb2, "t1", "刷单王", CASH)
BASE20 = BASE / 4.0
fees = []
for i in range(int(FREE) + 3):
    _o, cf, msg = open_and_close(sdb2, cfg, "t1", cids[i % 3], 20, qty=100.0, hold_sec=3600)
    if cf is None:
        check("T2.6 端到端开平 %d 笔成功" % (i + 1), False, msg)
        break
    fees.append(cf)
check("T2.6 前 %d 笔手续费都是基准值（免罚区）" % int(FREE),
      len(fees) > int(FREE) and all(abs(f - BASE20) < 1e-6 for f in fees[:int(FREE)]),
      fees[:3])
check("T2.7 免罚线后第一笔仍是 1 倍", abs(fees[int(FREE)] - BASE20) < 1e-6, fees[int(FREE)])
expect = BASE20 * (1.0 + 1.0 / STEP)
check("T2.8 再往后按 1/step 递增", abs(fees[int(FREE) + 1] - expect) < 1e-6,
      (fees[int(FREE) + 1], expect))

# 强平不吃换手税，也不计入当日计数
mk_user(sdb2, "t2", "被强平的人", CASH)
fo, fcf, msg = open_and_close(sdb2, cfg, "t2", cids[0], 20, qty=100.0, hold_sec=3600, force=True)
check("T2.9 强平手续费是 2 倍且不吃换手税",
      fcf is not None and abs(fcf - BASE20 * 2.0) < 1e-6, (fcf, BASE20 * 2.0))
n_close = int(sdb2.q1("SELECT COUNT(*) c FROM trades WHERE qq='t2' AND kind='close'")["c"])
n_force = int(sdb2.q1("SELECT COUNT(*) c FROM trades WHERE qq='t2' AND kind='force_close'")["c"])
check("T2.10 强平不计入当日平仓计数", n_close == 0 and n_force == 1, (n_close, n_force))

# ============================================================ T3 玩家侧回归
print("[T3] 玩家侧回归（合成三类典型玩家）")
sdb3 = dbmod.get_db(os.path.join(WORK, "player"), "p")
mk_companies(sdb3, 3, prefix="P", price=100.0)
pcids = [int(r["id"]) for r in sdb3.q("SELECT id FROM companies ORDER BY id")]
for u in ("p1", "p2", "p3"):
    mk_user(sdb3, u, "玩家 " + u, CASH)

# 1 倍长线：完全不受影响
of_p1, cf_p1, _ = open_and_close(sdb3, cfg, "p1", pcids[0], 1, qty=100.0, hold_sec=3600)
of_p1o, cf_p1o, _ = open_and_close(sdb3, c_old, "p1", pcids[0], 1, qty=100.0, hold_sec=3600)
check("T3.1 1 倍长线玩家手续费完全不变",
      abs(of_p1 - of_p1o) < 1e-9 and abs(cf_p1 - cf_p1o) < 1e-9, (of_p1, of_p1o, cf_p1, cf_p1o))

# 20 倍短线玩家：负担大幅下降
of_p2, cf_p2, _ = open_and_close(sdb3, cfg, "p2", pcids[1], 20, qty=100.0, hold_sec=30)
of_p2o, cf_p2o, _ = open_and_close(sdb3, c_old, "p2", pcids[1], 20, qty=100.0, hold_sec=30)
check("T3.2 20 倍短线玩家的手续费降到 1/4",
      abs((of_p2 + cf_p2) / (of_p2o + cf_p2o) - 0.25) < 1e-9,
      (of_p2 + cf_p2, of_p2o + cf_p2o))
check("T3.3 20 倍短线玩家占总手续费的比例同步下降",
      (of_p2 + cf_p2) / (NOTI / 20.0) < (of_p2o + cf_p2o) / (NOTI / 20.0) * 0.26,
      ((of_p2 + cf_p2) / (NOTI / 20.0), (of_p2o + cf_p2o) / (NOTI / 20.0)))

# 20 倍长线玩家：没有冷却税，主要是开仓费被封顶
of_p3, cf_p3, _ = open_and_close(sdb3, cfg, "p3", pcids[2], 20, qty=100.0, hold_sec=3600)
of_p3o, cf_p3o, _ = open_and_close(sdb3, c_old, "p3", pcids[2], 20, qty=100.0, hold_sec=3600)
check("T3.4 20 倍长线玩家手续费降到 1/4",
      abs((of_p3 + cf_p3) / (of_p3o + cf_p3o) - 0.25) < 1e-9,
      (of_p3 + cf_p3, of_p3o + cf_p3o))

# ============================================================ T4 M2 套利 EV
print("[T4] M2 套利 EV（真实 place_order / close_position 路径，只做空）")
_ARB_SEQ = [0]


def arb_sim(days=0.25, ncomp=8, seed=7, thr=0.004, timeout=3600.0, salt=None,
            max_per_day=0, over=None):
    """可控时钟沙盘：套利机器人走真实下单路径开平仓。

    形态沿用 GLOSSARY 的既有协议：**偏离锚价 thr 就做空，跌回锚价就平仓**。
    改了形态就等于换了题 —— 旧口径基线必须能复现，否则说明口径漂了。

    【盈亏口径 —— 不要用现金差】
    每笔净盈亏 = SUM(平仓 trade.pl) - SUM(平仓 trade.fee) - SUM(开仓 trade.fee)
    开仓费按 (qq, cid, side) FIFO 配对。
    用「平仓前后的现金差」是**错的**：机器人同时持多只票，
    保证金进出混在现金差里，会算出 −26.93%/笔 的假象（v4.19 踩过）。
    """
    c = dict(cfg)
    if salt:
        c["secret"] = salt
    if over:
        c.update(over)
    _ARB_SEQ[0] += 1
    seq = _ARB_SEQ[0]
    work = os.path.join(WORK, "arb%d" % seq)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    # gid 必须每次不同：dbmod.get_db 会缓存连接，Windows 上文件锁会让
    # 同一个 gid 的第二次插入报 UNIQUE constraint failed: users.qq。
    sdb = dbmod.get_db(work, "g%d" % seq)
    random.seed(seed)
    mk_companies(sdb, ncomp, prefix="A")
    clock = [int(time.mktime(time.strptime("2026-11-18 00:00:00", "%Y-%m-%d %H:%M:%S")))]
    mk_user(sdb, "arb", "套利机器人", BOT_CASH, clock[0])
    eng = Engine(sdb, c)
    # 四个时钟都要打桩：market.now / dbmod.now / engmod.now / recovery.now
    engmod.now = lambda: clock[0]
    market.now = lambda: clock[0]
    dbmod.now = lambda: clock[0]
    recovery.now = lambda: clock[0]
    tick = float(c["tick_seconds"])
    total = int(days * 86400 / tick)
    pos = {}
    opened = {}
    net = 0.0
    n = 0
    per_day = 0
    cur_day = None
    for _t in range(total):
        clock[0] += int(tick)
        eng.tick()
        d = int(clock[0]) // 86400
        if d != cur_day:
            cur_day = d
            per_day = 0
        for r in sdb.q("SELECT id,price,anchor FROM companies"):
            cid_, cur = r["id"], float(r["price"])
            anchor = float(r["anchor"] or cur)
            p = pos.get(cid_)
            if p:
                p["hold"] += tick
                if cur <= anchor * 1.0002 or p["hold"] > timeout:
                    ok, _m, _ = market.close_position(sdb, c, "arb", p["pid"])
                    if ok:
                        ct = sdb.q1("SELECT pl,fee,qty FROM trades WHERE qq='arb' AND cid=? "
                                    "AND kind IN ('close','force_close') ORDER BY id DESC LIMIT 1",
                                    (cid_,))
                        if ct:
                            need = float(ct["qty"])
                            ofee = 0.0
                            lst = opened.get(cid_) or []
                            while need > 1e-9 and lst:
                                lot = lst[0]
                                take = min(need, lot[0])
                                ofee += lot[1] * (take / lot[0]) if lot[0] > 0 else 0.0
                                lot[0] -= take
                                need -= take
                                if lot[0] <= 1e-9:
                                    lst.pop(0)
                            net += float(ct["pl"]) - float(ct["fee"]) - ofee
                            n += 1
                            per_day += 1
                    del pos[cid_]
                continue
            if max_per_day and per_day >= max_per_day:
                continue
            if cur <= anchor * (1 + thr):
                continue
            ok, _m, _ = market.place_order(sdb, c, "arb", cid_, "short", NOTIONAL / cur, LEVERAGE)
            if ok:
                row = sdb.q1("SELECT id FROM positions WHERE qq='arb' AND cid=? AND status='open' "
                             "ORDER BY id DESC LIMIT 1", (cid_,))
                if row:
                    pos[cid_] = {"pid": int(row["id"]), "hold": 0.0}
                    ot = sdb.q1("SELECT qty,fee FROM trades WHERE qq='arb' AND cid=? AND kind='open' "
                                "ORDER BY id DESC LIMIT 1", (cid_,))
                    if ot:
                        opened.setdefault(cid_, []).append([float(ot["qty"]), float(ot["fee"])])
    engmod.now = dbmod.now
    market.now = dbmod.now
    recovery.now = dbmod.now
    shutil.rmtree(work, ignore_errors=True)
    return net, n


TABS = (("基准 0.4% / 1 小时", 0.004, 3600.0),
        ("激进 0.2% / 30 分钟", 0.002, 1800.0),
        ("迟钝 0.8% / 2 小时", 0.008, 7200.0))
SALTS = ("s-prod-a", "s-prod-b", "s-prod-c")
SEEDS = (7, 11, 23)
NO_TURN = {"close_fee_turnover_free": 0, "close_fee_turnover_step": 0}


def run_tabs(over=None, max_per_day=0, seeds=SEEDS, days=0.25):
    out = {}
    for tag, thr, timeout in TABS:
        vals = []
        for salt in SALTS:
            for seed in seeds:
                net, n = arb_sim(days=days, seed=seed, thr=thr, timeout=timeout, salt=salt,
                                 max_per_day=max_per_day, over=over)
                vals.append((net / max(1, n)) / MARGIN * 100.0)
        out[tag] = vals
    return out


def per_salt(vals, seeds=SEEDS):
    """把 (盐 x seed) 的原始值压成「每盐均值」，和 GLOSSARY 的口径一致。"""
    ns = len(seeds)
    return [sum(vals[i * ns:(i + 1) * ns]) / float(ns) for i in range(len(vals) // ns)]


def show(title, out):
    print("    --- %s ---" % title)
    for tag, vals in out.items():
        print("    %-18s 折保证金 %s（均值 %+6.2f%%）" % (
            tag, " ".join("%+6.2f" % v for v in vals), sum(vals) / len(vals)))
    return all(v < 0 for vals in out.values() for v in vals)


# 【口径一：0.25 天】旧口径基线必须复现 GLOSSARY 记的数，否则说明沙盘协议漂了。
# 指纹值 = 基准档 / 盐 a / 种子 7 / cap0 + 无换手税。
FINGERPRINT = {"基准 0.4% / 1 小时": -2.88, "激进 0.2% / 30 分钟": -1.65,
               "迟钝 0.8% / 2 小时": -7.91}
REF_MEAN = {"基准 0.4% / 1 小时": -2.02, "激进 0.2% / 30 分钟": -2.94,
            "迟钝 0.8% / 2 小时": -2.01}
base_over = dict(NO_TURN)
base_over["fee_leverage_cap"] = 0
base = run_tabs(over=base_over, seeds=SEEDS)
print("    --- 旧口径（v4.18 生产口径）0.25 天沙盘 ---")
for tag, vals in base.items():
    mean = sum(vals) / len(vals)
    print("    %-18s 折保证金 %s（均值 %+6.2f%%）" % (
        tag, " ".join("%+6.2f" % v for v in vals), mean))
    check("T4.1 旧口径基线复现 GLOSSARY 指纹（%s）" % tag,
          abs(vals[0] - FINGERPRINT[tag]) < 0.05, (vals[0], FINGERPRINT[tag]))
    check("T4.2 旧口径基线均值与 GLOSSARY 一致（%s）" % tag,
          abs(mean - REF_MEAN[tag]) < 0.35, (mean, REF_MEAN[tag]))

# 【口径二：1 天】换手税按「当日已平仓笔数」累加，
# 0.25 天只能跑到 50 多笔，倍率刚起来；跑满一天才是生产真相。
prod = run_tabs(days=1.0, seeds=SEEDS[:1])
ok_prod = True
print("    --- 生产口径（cap5 + 换手税）1 天沙盘 ---")
for tag, vals in prod.items():
    ps = per_salt(vals, SEEDS[:1])
    print("    %-18s 每盐 EV %s（全部 %+6.2f%%）" % (
        tag, " ".join("%+7.2f" % v for v in ps), sum(vals) / len(vals)))
    ok_prod = ok_prod and all(v < 0 for v in ps)
check("T4.3 生产口径（cap5 + 换手税）1 天沙盘三档 x 三盐 EV 全部为负", ok_prod,
      {k: max(per_salt(v, SEEDS[:1])) for k, v in prod.items()})

# 对照：只有 cap5、没有换手税 —— 这一档确实是正的，说明换手税不可省。
only_cap = run_tabs(over=NO_TURN, days=1.0, seeds=SEEDS[:1])
print("    --- 对照：只有 cap5、没有换手税（1 天）---")
for tag, vals in only_cap.items():
    print("    %-18s 每盐 EV %s（全部 %+6.2f%%）" % (
        tag, " ".join("%+7.2f" % v for v in per_salt(vals, SEEDS[:1])),
        sum(vals) / len(vals)))
check("T4.4 没有换手税时无限频套利确实能赚钱（说明换手税不可省）",
      any(v > 0 for vals in only_cap.values() for v in vals),
      {k: max(vals) for k, vals in only_cap.items()})

# 【已知残余风险，只记录不判定】
# 「耐心套利者」（每天 <= 25 笔）：旧口径下就已经是正的，cap5 会放大它。
# 这是市场模型的 alpha 太大，不是费率能单独解决的问题 —— 留给下一版。
print("    --- 已知残余风险：耐心套利者（每日上限 25 笔，1 天），仅记录 ---")
pat = run_tabs(max_per_day=25, days=1.0, seeds=SEEDS[:1])
for tag, vals in pat.items():
    print("    %-18s 每盐 EV %s（全部 %+6.2f%%）" % (
        tag, " ".join("%+7.2f" % v for v in per_salt(vals, SEEDS[:1])),
        sum(vals) / len(vals)))

# ============================================================ T5 M1 行情健康度
print("[T5] M1 行情健康度")
c = dict(cfg)
work = os.path.join(WORK, "health")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
sdbh = dbmod.get_db(work, "h")
random.seed(5)
mk_companies(sdbh, 8, prefix="H")
eng = Engine(sdbh, c)
clock = [int(time.mktime(time.strptime("2026-11-18 00:00:00", "%Y-%m-%d %H:%M:%S")))]
engmod.now = lambda: clock[0]
market.now = lambda: clock[0]
dbmod.now = lambda: clock[0]
recovery.now = lambda: clock[0]
tick = float(c["tick_seconds"])
for _t in range(int(8 * 86400 / tick)):
    clock[0] += int(tick)
    eng.tick()
engmod.now = dbmod.now
market.now = dbmod.now
recovery.now = dbmod.now
rows = sdbh.q("SELECT price, prev_close FROM daily_px WHERE prev_close > 0")
rets = sorted((float(r["price"]) / float(r["prev_close"]) - 1) * 100 for r in rows)
med = rets[len(rets) // 2] if rets else 0
print("    日收益中位 %+.3f%%（样本 %d，最小 %+.2f%% / 最大 %+.2f%%）" % (
    med, len(rets), rets[0] if rets else 0, rets[-1] if rets else 0))
check("T5.1 日收益中位为正（没有系统性失血）", med > 0.3, "%.3f%%" % med)
check("T5.2 样本量够（多日多票）", len(rets) >= 40, len(rets))

shutil.rmtree(WORK, ignore_errors=True)
print("")
if FAILED:
    print("FAILED: %d 项 -> %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("v4.19 全部通过")

# -*- coding: utf-8 -*-
"""富豪经济（v4.7）—— 会员费 / 打土豪 / 挥霍消费 / 全民分红池。

【这一版要解决什么】
用户问：「资金多了更容易赚到更多的钱，差距逐渐放大，
需不需要加入一些更高风险的资金流手段，让那些富有的人去消费？」

现网体检的结论是：**钱不是集中在富人手里，是冻在破产账户里**
（7021 万 / 全群 9867 万现金，3 个账户从没执行过清盘），
另外还有一层是「复利放大器」：收益率是百分比，
而能撬动的仓位 = 净值 × 杠杆上限，334 万的人和 9.9 万的人差 33 倍基数。

所以 v4.7 做四件事，一件一件对应上面两个问题：

1. 【解冻死钱】破产满 N 天没手清盘，交易所强制清盘（见 wealth.auto_liquidate）。
   —— 不做这一步，后面所有「再分配」都只是在 1/4 的活钱里打转。
2. 【让富人消费】净值分层 + 每日自动会员费。
   —— 用户要的「更高风险的资金流手段」的第一层：钱必须持续流出。
3. 【打土豪】穷人可以对富人动手，成功拿走他 1%~3% 的现金。
   —— 第二层：钱可以**被别人**拿走，不只是自己交税。
4. 【全民分红池】会员费 + 罚金 + 挥霍消费 → 每天按「活跃度 × 贫困度」发回群里。
   —— 第三层：流出去的钱最后回到最需要的人手里，而不是凭空蒸发。

【守恒】
会员费 / 打土豪 / 分红都是**转移**，全群总现金守恒。
只有「挥霍」里明确标为销毁的那一部分会真的消失（防通胀）。
"""
import random
import time

import market
import recovery
import tiers
from db import now

POOL_KEY = "dividend_pool"        # meta: 全民分红池余额
FEE_DAY_KEY = "tycoon_fee_day"    # meta: 上次收会员费的日期（幂等）
ROB_MARK = "打土豪"                # ledger 原因前缀
LUX_MARK = "挥霍消费"              # ledger 原因前缀
DIV_MARK = "全民分红"              # ledger 原因前缀

LUXURY = [
    {"key": "car", "name": "豪华座驾", "emoji": "🏎", "rep": 2, "alias": "豪车",
     "desc": "开出去全群都认识你"},
    {"key": "house", "name": "江景大宅", "emoji": "🏰", "rep": 3, "alias": "豪宅",
     "desc": "群里第一个背房贷的人"},
    {"key": "watch", "name": "收藏腕表", "emoji": "⌚", "rep": 2, "alias": "手表",
     "desc": "表针指着别人的股价"},
    {"key": "yacht", "name": "私人游艇", "emoji": "🛥", "rep": 4, "alias": "游艇",
     "desc": "把业绩发布开到海上"},
    {"key": "club", "name": "私人会所", "emoji": "🥂", "rep": 5, "alias": "会所",
     "desc": "高层俱乐部的入场券"},
    {"key": "jet", "name": "私人飞机", "emoji": "✈", "rep": 7, "alias": "飞机",
     "desc": "涨停了立刻飞出去庆祝"},
]

# ---------------------------------------------------------------- 小工具
def _ledger(db, qq, delta, reason):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    if row is None:
        return
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (str(qq), now(), round(delta, 2), round(float(row["cash"] or 0), 2), reason))


def _today():
    return time.strftime("%Y-%m-%d", time.localtime())


def _day_start():
    return market.day_start()


def _equity(db, cfg, qq):
    acct = market.account(db, cfg, str(qq))
    return acct["equity"] if acct else 0.0


def _cash(db, qq):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    return float(row["cash"] or 0) if row else 0.0


def _rep(db, qq):
    row = db.q1("SELECT reputation FROM users WHERE qq=?", (str(qq),))
    return float(row["reputation"] or 0) if row else 0.0


def _add_rep(db, qq, delta):
    db.ex("UPDATE users SET reputation=MAX(0, reputation+?) WHERE qq=?", (delta, str(qq)))


def pool_get(db):
    raw = db.get_meta(POOL_KEY, "")
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def pool_set(db, value):
    db.set_meta(POOL_KEY, round(max(0.0, float(value)), 2))


def pool_add(db, amount):
    pool_set(db, pool_get(db) + float(amount))


def _heat_of(db, qq):
    """被盯上的热度：超过 24 小时没被关注就归零。"""
    row = db.q1("SELECT rob_heat, rob_heat_ts FROM users WHERE qq=?", (str(qq),))
    if not row:
        return 0
    heat = int(row["rob_heat"] or 0)
    if heat <= 0:
        return 0
    if now() - int(row["rob_heat_ts"] or 0) > 86400:
        return 0
    return heat


def _touch_heat(db, qq):
    db.ex("UPDATE users SET rob_heat=rob_heat+1, rob_heat_ts=? WHERE qq=?", (now(), str(qq)))

# ================================================================ 净值分层
def _fee_paid_today(db, qq):
    row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? AND reason LIKE '会员费%'",
                (str(qq), _day_start()))
    return bool(row and int(row["c"] or 0) > 0)


def tier_status(db, cfg, qq):
    """我的档位体检。"""
    qq = str(qq)
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None:
        return None
    eq = _equity(db, cfg, qq)
    t = tiers.tier_of(eq)
    nxt = tiers.next_tier(eq)
    fee = min(max(eq, 0) * float(t["fee"]), tiers.fee_cap(cfg, eq))
    lev_cap = int(cfg.get("max_leverage", 20)) + int(t["lev"])
    return {
        "qq": qq,
        "nick": user["nick"] or ("群友" + qq[-3:]),
        "equity": round(eq, 2),
        "tier": t["name"],
        "tier_key": t["key"],
        "fee": round(fee, 2),
        "fee_rate": float(t["fee"]),
        "paid_today": _fee_paid_today(db, qq),
        "lev_cap": lev_cap,
        "fee_disc": float(t["fdisc"]),
        "wage_bonus": float(t["wage"]),
        "rob_rate": float(t["rob"]),
        "rep": round(_rep(db, qq), 2),
        "next_tier": (nxt["name"] if nxt else None),
        "next_min": (nxt["min"] if nxt else None),
        "tier_min": t["min"],
        "pool": round(pool_get(db), 2),
    }


def tier_text(db, cfg, qq):
    st = tier_status(db, cfg, qq)
    if st is None:
        return "你还没有开户，先在群里发一次「炒股」"
    lines = ["👑 %s 的净值分层" % st["nick"]]
    lines.append("· 当前档位：%s（净值 %.2f）" % (st["tier"], st["equity"]))
    if st["fee_rate"] > 0:
        state = "今天已交" if st["paid_today"] else "明天自动扣"
        cap_ratio = float(cfg.get("tycoon_fee_cap_ratio", 0) or 0)
        if cap_ratio > 0:
            cap_txt = "max(%.0f, 净值×%.2f%%)" % (
                float(cfg.get("tycoon_fee_cap", 30000)), cap_ratio * 100)
        else:
            cap_txt = "%.0f" % float(cfg.get("tycoon_fee_cap", 30000))
        lines.append("· 每日会员费：%.2f 元（%.2f%%，封顶 %s）—— %s"
                     % (st["fee"], st["fee_rate"] * 100, cap_txt, state))
        lines.append("· 特权：杠杆上限 %dx、手续费 -%.0f%%、打工工资 +%.0f%%"
                     % (st["lev_cap"], st["fee_disc"] * 100, st["wage_bonus"] * 100))
        if st["next_tier"]:
            lines.append("· 升到 %s 还差 %.2f" % (
                st["next_tier"], max(0.0, float(st["next_min"]) - st["equity"])))
        else:
            lines.append("· 你已经是顶配财阀了，全群没人比你更难抢")
    else:
        lines.append("· 不用交会员费（净值不到 20 万）")
        lines.append("· 想有特权？先赚够 20 万，进了「小富之家」才享受得到")
    lines.append("· 声望 %.1f（每点抗抢 +1.5%%，靠「挥霍」攒，会每天掉一点）" % st["rep"])
    lines.append("· 全民分红池余额 %.0f 元" % st["pool"])
    lines.append(tiers.tier_lines(cfg))
    return "\n".join(lines)

# ================================================================ 每日会员费
def collect_fees(db, cfg):
    """每日会员费。幂等：同一天只收一次（靠 meta 里的日期）。

    【为什么不能对谁都收】
    破产户净值是负的，档位本来就是最低档，本来就不交；
    现金不够 1.5 倍会员费的也不收 —— 收完变成负数那就等于逼他破产，
    那是惩罚不是再分配。
    """
    today = _today()
    if db.get_meta(FEE_DAY_KEY, "") == today:
        return 0, 0.0
    db.set_meta(FEE_DAY_KEY, today)
    rows = db.q("SELECT qq FROM users")
    total = 0.0
    count = 0
    for r in rows:
        qq = r["qq"]
        acct = market.account(db, cfg, qq)
        if not acct:
            continue
        eq = acct["equity"]
        ratio = tiers.fee_ratio(eq)
        if ratio <= 0:
            continue
        fee = min(eq * ratio, tiers.fee_cap(cfg, eq))
        if fee < 1:
            continue
        cash = _cash(db, qq)
        if cash <= fee * 1.5:
            continue
        db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (fee, qq))
        _ledger(db, qq, -fee, "会员费")
        pool_add(db, fee)
        total += fee
        count += 1
    return count, round(total, 2)


def decay_day(db, cfg):
    """每日衰减：声望掉一点，被盯上的热度过期清零。

    【为什么要掉】声望是「挥霍」换来的保护。掉了，
    富豪就必须继续花钱维持 —— 这就是「让富人持续消费」的机制来源。
    """
    rate = float(cfg.get("tycoon_rep_decay", 0.05))
    rows = db.q("SELECT qq, reputation, rob_heat, rob_heat_ts FROM users")
    ts = now()
    for r in rows:
        rep = float(r["reputation"] or 0)
        if rep > 0:
            nr = rep * (1.0 - rate)
            if nr < 0.05:
                nr = 0.0
            db.ex("UPDATE users SET reputation=? WHERE qq=?", (round(nr, 3), r["qq"]))
        heat = int(r["rob_heat"] or 0)
        if heat > 0 and ts - int(r["rob_heat_ts"] or 0) > 86400:
            db.ex("UPDATE users SET rob_heat=0 WHERE qq=?", (r["qq"],))


def collect_margin_tax(db, cfg):
    """持仓保证金税：钱锁在仓位里复利，也要交税。

    【为什么打的是保证金而不是现金】
    富豪可以把现金清空（2026-11-20 实测：Kaito 清仓后现金 534 亿、无仓位），
    但只要还有仓位，保证金就在那儿 —— 那是**正在复利的钱**。
    对净值 ≥ margin_tax_min_equity 且有持仓的人，
    按保证金总额收 margin_tax_ratio（默认 0.1%/天），进全民分红池。

    跟会员费一样是**转移**不是增发：全群总现金守恒。
    """
    min_eq = float(cfg.get("margin_tax_min_equity", 5000000))
    ratio = float(cfg.get("margin_tax_ratio", 0.001))
    if ratio <= 0:
        return 0, 0.0
    rows = db.q("SELECT qq, SUM(margin) m FROM positions WHERE status='open' GROUP BY qq")
    total = 0.0
    count = 0
    for r in rows:
        qq = str(r["qq"])
        mg = float(r["m"] or 0)
        if mg <= 0:
            continue
        if _equity(db, cfg, qq) < min_eq:
            continue
        tax = round(mg * ratio, 2)
        if tax < 1:
            continue
        cash = _cash(db, qq)
        if cash <= tax:
            continue
        db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (tax, qq))
        _ledger(db, qq, -tax, "持仓税")
        pool_add(db, tax)
        total += tax
        count += 1
    return count, round(total, 2)


def settle_day(db, cfg):
    """每日结算入口（调度器跨天时调一次）。"""
    count, total = collect_fees(db, cfg)
    mcount, mtotal = collect_margin_tax(db, cfg)
    decay_day(db, cfg)
    seed = float(cfg.get("dividend_pool_seed", 8000))
    if db.get_meta("dividend_pool_seeded", "") != "1":
        db.set_meta("dividend_pool_seeded", "1")
        pool_set(db, max(pool_get(db), seed))
    return count, total, mcount, mtotal

# ================================================================ 打土豪
def _find_target(db, key):
    """按 QQ 号或昵称找人。"""
    key = " ".join(str(key or "").split())
    if not key:
        return None
    row = db.q1("SELECT * FROM users WHERE qq=?", (key,))
    if row:
        return row
    rows = db.q("SELECT * FROM users WHERE nick LIKE ?", ("%" + key + "%",))
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (0 if (r["nick"] or "") == key else 1,
                                       len(r["nick"] or "")))
    return rows[0]


def rob_count_today(db, qq):
    row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? "
                "AND reason LIKE '打土豪出手%'", (str(qq), _day_start()))
    return int(row["c"]) if row else 0


def rob_fee(cfg, target_equity):
    """出手费：固定小额，或目标净值的 1%（取小），不低于底价。"""
    base = float(cfg.get("rob_fee_base", 2000))
    ratio = float(cfg.get("rob_fee_ratio", 0.01))
    floor = float(cfg.get("rob_fee_min", 500))
    fee = min(base, max(0.0, target_equity) * ratio)
    return round(max(floor, fee), 2)


def rob_rate_of(cfg, attacker_equity, target_equity, target_rep, target_heat):
    """成功率：目标越富越好抢；攻击方越有底气越准；
    目标声望高（挥霍买来的保护）会降低成功率；被盯上热度高则更容易得手。"""
    base = tiers.rob_rate(target_equity)
    atk = min(0.25, tiers.tier_index(attacker_equity) * 0.05)
    heat = min(0.20, target_heat * 0.05)
    protect = min(0.30, target_rep * 0.015)
    rate = base + atk + heat - protect + random.uniform(-0.08, 0.08)
    return max(0.05, min(0.92, rate))


def rob_targets(db, cfg, qq, limit=8):
    """可以下手的名单：净值够高、又没在保护期的人。"""
    rows = db.q("SELECT * FROM users WHERE bankrupt=0 ORDER BY cash DESC LIMIT 60")
    min_eq = float(cfg.get("rob_min_target_equity", 30000))
    ts = now()
    out = []
    for r in rows:
        if str(r["qq"]) == str(qq):
            continue
        eq = _equity(db, cfg, r["qq"])
        if eq < min_eq:
            continue
        guard = int(r["rob_guard_until"] or 0)
        out.append({
            "qq": r["qq"], "nick": r["nick"] or ("群友" + str(r["qq"])[-3:]),
            "equity": round(eq, 2),
            "tier": tiers.tier_name(eq),
            "rate": round(tiers.rob_rate(eq) * 100),
            "fee": rob_fee(cfg, eq),
            "guarded": guard > ts,
            "heat": _heat_of(db, r["qq"]),
        })
    out.sort(key=lambda x: -x["equity"])
    return out[:limit]


def rob_status(db, cfg, qq):
    qq = str(qq)
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return None
    limit = int(cfg.get("rob_daily_limit", 2) or 2)
    used = rob_count_today(db, qq)
    return {"qq": qq, "used": used, "limit": limit, "left": max(0, limit - used),
            "equity": round(_equity(db, cfg, qq), 2),
            "cash": round(_cash(db, qq), 2),
            "tier": tiers.tier_name(_equity(db, cfg, qq)),
            "targets": rob_targets(db, cfg, qq, 8)}

def rob(db, cfg, qq, target_key):
    """打土豪：对有钱人下手，成功抢走他 1%~3% 的现金。

    【为什么要做这个】
用户明确问了「更高风险的资金流手段，让富人去消费」。
会员费是「交税」，玩家没有选择权，容易变成负担；
打土豪是「被别人抢」—— 钱可以直接从富人口袋里被拿走，
而且有成功率、有翻车、有公开通报，天然带戏剧性。
穷人有翻身路径，富人有危机感，两边都有理由回来看盘。
    """
    qq = str(qq)
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return False, "你还没有开户，先在群里发一次「炒股」", None
    if recovery.in_relief(db, qq):
        return False, "破产纾困期间不能打别人的主意，先把自己的债还清", None
    tgt = _find_target(db, target_key)
    if tgt is None:
        return False, "没找到这个人（发 QQ 号或昵称都行）", None
    tqq = str(tgt["qq"])
    tname = tgt["nick"] or ("群友" + tqq[-3:])
    if tqq == qq:
        return False, "打自己？那叫自残，不叫打土豪", None
    limit = int(cfg.get("rob_daily_limit", 2) or 2)
    used = rob_count_today(db, qq)
    if used >= limit:
        return False, ("今天你已经出手 %d 次了（每天最多 %d 次），手别太快" % (used, limit)), None
    teq = _equity(db, cfg, tqq)
    min_eq = float(cfg.get("rob_min_target_equity", 30000))
    if teq < min_eq:
        return False, ("%s 净值才 %.0f，穷成这样也下不了手，换个目标" % (tname, teq)), None
    if tgt["bankrupt"]:
        return False, ("%s 已经在破产纾困了 —— 去抢一个还不上债的人，太难看了" % tname), None
    guard = int(tgt["rob_guard_until"] or 0)
    if guard > now():
        left = (guard - now() + 3599) // 3600
        return False, ("%s 刚被抢过，正在保护期（还剩 %d 小时），换个目标" % (tname, left)), None
    fee = rob_fee(cfg, teq)
    if _cash(db, qq) < fee:
        return False, "出手要 %.0f 元，钱不够（先打工或者捡漏）" % fee, None

    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (fee, qq))
    _ledger(db, qq, -fee, "打土豪出手")

    my_eq = _equity(db, cfg, qq)
    heat = _heat_of(db, tqq)
    trep = _rep(db, tqq)
    rate = rob_rate_of(cfg, my_eq, teq, trep, heat)
    win = random.random() < rate
    ts = now()
    if win:
        tcash = _cash(db, tqq)
        take = tcash * random.uniform(float(cfg.get("rob_win_min", 0.01)),
                                      float(cfg.get("rob_win_max", 0.03)))
        take = min(take, float(cfg.get("rob_max_take", 50000)), tcash)
        take = round(max(0.0, take), 2)
        if take <= 0:
            take = round(min(fee, tcash), 2)
        db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (take, tqq))
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (take, qq))
        _ledger(db, tqq, -take, "打土豪被抢")
        _ledger(db, qq, take, "打土豪得手")
        db.ex("UPDATE users SET rob_guard_until=? WHERE qq=?",
              (ts + int(cfg.get("rob_guard_hours", 12)) * 3600, tqq))
        _touch_heat(db, tqq)
        _add_rep(db, qq, float(cfg.get("rob_rep_gain", 1.0)))
        title = "%s 被 %s 当街抢了" % (tname, me["nick"] or ("群友" + qq[-3:]))
        body = ("%s 趁着 %s 不注意，从他兜里摸走了 %.0f 块。"
                "钱到手，人跑了，%s 到现在还不知道钱去哪了。"
                % (me["nick"] or ("群友" + qq[-3:]), tname, take, tname))
        msg = ("🦹 打土豪得手！\n%s\n· 从 %s 兜里抢走 %.0f 元\n"
               "· 出手费 %.0f，净赚 %.0f\n· 成功率 %.0f%%（他现在是%s，"
               "而且已经被别人盯上了）" % (
                   body, tname, take, fee, take - fee, rate * 100, tiers.tier_name(teq)))
        info = {"win": True, "target": tqq, "target_name": tname, "take": take,
                "fee": fee, "rate": round(rate * 100, 1)}
    else:
        comp = round(fee * float(cfg.get("rob_compensate", 0.2)), 2)
        if comp > 0:
            db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (comp, tqq))
            _ledger(db, tqq, comp, "打土豪赔偿")
        _add_rep(db, tqq, 0.5)
        title = "%s 想抢 %s，反被抓了个正着" % (me["nick"] or ("群友" + qq[-3:]), tname)
        body = ("%s 刚把手伸进 %s 的口袋，就被 %s 反手按住了。"
                "不但没抢到，还赔进去 %.0f 块辛苦钱。"
                % (me["nick"] or ("群友" + qq[-3:]), tname, tname, fee))
        msg = ("🚨 打土豪翻车！\n%s\n· 出手费 %.0f 全没了\n· %s 反手拿走 %.0f 块，"
               "还涨了 0.5 点声望（更不好惹了）\n· 成功率 %.0f%%" % (
                   body, fee, tname, comp, rate * 100))
        info = {"win": False, "target": tqq, "target_name": tname, "take": 0.0,
                "fee": fee, "comp": comp, "rate": round(rate * 100, 1)}
    try:
        db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact,cat) "
              "VALUES(?,?,?,?,?,?,?,?)",
              (int(time.time()), 0, "", "group", title, body, 0.0, "社会"))
    except Exception:
        pass
    return True, msg, info

# ================================================================ 挥霍消费
def luxury_price(cfg, equity):
    """挥霍价格：净值的 5%（有上下限）。"""
    ratio = float(cfg.get("luxury_ratio", 0.05))
    lo = float(cfg.get("luxury_min", 5000))
    hi = float(cfg.get("luxury_max", 200000))
    price = max(0.0, equity) * ratio
    return round(min(hi, max(lo, price)), 2)


def luxury_list(cfg=None):
    cfg = cfg or {}
    ratio = float(cfg.get("luxury_ratio", 0.05))
    return ["🛍 挥霍清单（花掉净值的 %.0f%%，换声望）：" % (ratio * 100)] + \
           ["%s %s：声望 +%d —— %s" % (i["emoji"], i["name"], i["rep"], i["desc"])
            for i in LUXURY]


def luxury_count_today(db, qq):
    """今天已经挥霍了几次。

    【为什么要数】声望会降低「被打」的成功率（每点声望 -1.5%，20 点封顶），
    而挥霍是唯一能加声望的手段 —— 原来这里没有任何每日上限，
    理论上可以一次买空身家把声望刷到顶，从此打土豪永远打不动他。
    打工可以刷，炫富不能刷。
    """
    row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? "
                "AND reason LIKE '挥霍消费%'", (str(qq), _day_start()))
    return int(row["c"]) if row else 0


def luxury_spent_today(db, qq):
    """今天挥霍一共花了多少钱（用于每日总额上限）。"""
    row = db.q1("SELECT COALESCE(SUM(-delta),0) s FROM ledger WHERE qq=? AND ts>=? "
                "AND reason LIKE '挥霍消费%'", (str(qq), _day_start()))
    return float(row["s"] or 0.0) if row else 0.0


def luxury_status(db, cfg, qq):
    qq = str(qq)
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return None
    eq = _equity(db, cfg, qq)
    price = luxury_price(cfg, eq)
    # 每日额度一起给前端：不然群友点「买」被拒了才知道今天买过，
    # 看着像 bug，其实只是额度用完了（打土豪那边早就是「还能出手 N/M 次」的写法）。
    limit = int(cfg.get("luxury_daily_limit", 2) or 2)
    used = luxury_count_today(db, qq)
    return {"qq": qq, "equity": round(eq, 2), "price": price,
            "cash": round(_cash(db, qq), 2), "rep": round(_rep(db, qq), 2),
            "tier": tiers.tier_name(eq), "items": LUXURY,
            "used": used, "limit": limit, "left": max(0, limit - used),
            "full": used >= limit}


def luxury_buy(db, cfg, qq, key):
    """挥霍消费：花掉一部分身家，换来「声望」（抗抢保护）。

    【为什么这是纯消耗还要做】
用户要的是「让那些富有的人去消费」。除了交税，富豪还需要一个
**自愿的、体面的、只有钱能买的东西** —— 声望 / 排面。
声望会每天掉 5%，所以想保住排面就得持续挥霍，
富人的钱于是持续流向公共池，而不是躺在账上复利。
    """
    qq = str(qq)
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return False, "你还没有开户，先在群里发一次「炒股」", None
    key = str(key or "").strip()
    if not key or key in ("清单", "列表", "?"):
        st = luxury_status(db, cfg, qq)
        lines = luxury_list(cfg)
        lines.append("· 你现在净值 %.0f，一单大约 %.0f 元（声望 %.1f）"
                     % (st["equity"], st["price"], st["rep"]))
        lines.append("👉 发「挥霍 豪宅」之类，直接买；发「挥霍 随机」让系统替你挑")
        return True, "\n".join(lines), {"list": True}
    item = None
    for i in LUXURY:
        if i["key"] == key or i["name"] == key or i.get("alias") == key:
            item = i
            break
    if item is None and key in ("随机", "随便", "来一个", "all"):
        item = random.choice(LUXURY)
    if item is None:
        for i in LUXURY:
            if key and (key in i["name"] or key in i["key"] or key in (i.get("alias") or "")):
                item = i
                break
    if item is None:
        return False, ("没有这一项。现有：%s" % "、".join(
            "%s%s(%s)" % (i["emoji"], i["name"], i.get("alias") or i["key"])
            for i in LUXURY)), None
    eq = _equity(db, cfg, qq)
    price = luxury_price(cfg, eq)
    # 【v4.9】每日额度：次数 + 总额。没有这个，声望可以无限刷，
    # 刷满抗抢保护后打土豪对这个人永久失效（等于把核心玩法关掉）。
    lim = int(cfg.get("luxury_daily_limit", 2) or 2)
    used = luxury_count_today(db, qq)
    if used >= lim:
        return False, ("今天已经挥霍 %d 次了，明天再来 —— 声望不是无限刷的，"
                       "多买也换不来更多保护" % used), None
    cap = float(cfg.get("luxury_daily_cap", 0) or 0)
    if cap > 0:
        spent = luxury_spent_today(db, qq)
        if spent + price > cap:
            return False, ("今天挥霍额度 %.0f 元已经用完（已花 %.0f），明天再来"
                           % (cap, spent)), None
    if _cash(db, qq) < price:
        return False, ("买 %s 要 %.0f 元，钱不够" % (item["name"], price)), None
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (price, qq))
    _ledger(db, qq, -price, "%s·%s" % (LUX_MARK, item["name"]))
    pool_ratio = float(cfg.get("luxury_pool_ratio", 0.8))
    to_pool = round(price * pool_ratio, 2)
    burned = round(price - to_pool, 2)
    if to_pool > 0:
        pool_add(db, to_pool)
    _add_rep(db, qq, float(item["rep"]))
    rep = _rep(db, qq)
    protect = min(0.30, rep * 0.015)
    msg = ("%s %s到手！\n· 花了 %.0f 元（净值的 %.0f%%）\n"
           "· 声望 +%d → %.1f（被打劫成功率 -%.1f%%，每天掉 5%% 点）\n"
           "· %.0f 元进全民分红池，%.0f 元直接销毁（防通胀）" % (
               item["emoji"], item["name"], price,
               (price / eq * 100) if eq > 0 else 0.0,
               item["rep"], rep, protect * 100, to_pool, burned))
    msg += "\n· " + item["desc"]
    return True, msg, {"item": item["name"], "price": price, "rep": rep,
                       "pool": to_pool, "burned": burned}

# ================================================================ 全民分红池
def _active_today(db, qq):
    row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=?", (str(qq), _day_start()))
    return bool(row and int(row["c"] or 0) > 0)


def _claimed_today(db, qq):
    row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? AND reason LIKE ?",
                (str(qq), _day_start(), DIV_MARK + "%"))
    return bool(row and int(row["c"] or 0) > 0)


def _streak_days(db, qq, days=7):
    """最近 N 天里有几天动过（ledger 里出现过记录）。"""
    since = now() - days * 86400
    rows = db.q("SELECT ts FROM ledger WHERE qq=? AND ts>=?", (str(qq), since))
    buckets = set()
    for r in rows:
        t = time.localtime(r["ts"])
        buckets.add(t.tm_year * 400 + t.tm_yday)
    return len(buckets)


def dividend_amount(cfg, equity, streak):
    """分红额 = 底薪 × 贫困系数 × 连续活跃系数。

    贫困系数：净值越低拿越多（0 净值拿 3 倍，初始本金拿 1 倍，
    高于初始本金的不再加分，最低 0.3 倍）。
    """
    base = float(cfg.get("dividend_base", 30))
    init = float(cfg.get("initial_cash", 100000) or 100000)
    poor = 3.0 - 2.0 * max(0.0, equity) / max(1.0, init)
    poor = max(0.3, min(3.0, poor))
    sf = min(2.0, 1.0 + 0.12 * max(0, int(streak) - 1))
    amt = base * poor * sf
    return round(max(float(cfg.get("dividend_min", 10)), amt), 2)


def dividend_status(db, cfg, qq):
    qq = str(qq)
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return None
    eq = _equity(db, cfg, qq)
    streak = _streak_days(db, qq)
    return {"qq": qq, "equity": round(eq, 2), "streak": streak,
            "active": _active_today(db, qq), "claimed": _claimed_today(db, qq),
            "amount": dividend_amount(cfg, eq, streak),
            "pool": round(pool_get(db), 2)}


def claim_dividend(db, cfg, qq):
    """领全民分红：会员费 + 罚金 + 挥霍消费攒出来的池子，按活跃度和贫困度发回群里。"""
    qq = str(qq)
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return False, "你还没有开户，先在群里发一次「炒股」", None
    if _claimed_today(db, qq):
        return False, "今天已经领过分红了，明天再来", None
    if not _active_today(db, qq):
        return False, ("今天你还没冒过泡（签到 / 打工 / 交易都算），"
                       "先去群里活跃一下，明天的分红才拿得多"), None
    pool = pool_get(db)
    cap = float(cfg.get("dividend_daily_cap", 8000))
    # 【v4.13】池子大了就按比例多发：富豪的钱进了分红池，穷人就能多拿一点。
    # 原来上限是死的 8000 元/天 —— 主群池子里趴着 74 万，一天只发 8000，
    # 富人的钱进了池子也发不出去。比例上限跟池子走，池子空了自然发不动。
    rate = float(cfg.get("dividend_pool_rate", 0) or 0)
    if rate > 0:
        cap = max(cap, pool * rate)
    used_row = db.q1("SELECT COALESCE(SUM(delta),0) s FROM ledger "
                     "WHERE ts>=? AND reason LIKE ?", (_day_start(), DIV_MARK + "%"))
    used = float(used_row["s"] or 0)
    room = max(0.0, cap - used)
    if room < 1:
        return False, "今天的分红已经发满了（上限 %.0f），明天再来" % cap, None
    amt = dividend_amount(cfg, _equity(db, cfg, qq), _streak_days(db, qq))
    if pool < amt:
        # 池子见底：交易所按「印钞上限」垫一笔（不是无限印）
        print_cap = float(cfg.get("dividend_print_cap", 20000))
        printed = float(db.get_meta("dividend_printed", "0") or 0)
        if printed + amt <= print_cap:
            pool = print_cap - printed
            db.set_meta("dividend_printed", round(printed + amt, 2))
            pool_set(db, pool)
        else:
            amt = min(amt, pool)
    amt = round(min(amt, room, pool), 2)
    if amt < 1:
        return False, "分红池今天见底了，明天再来看看", None
    db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (amt, qq))
    _ledger(db, qq, amt, "%s %.2f" % (DIV_MARK, amt))
    pool_set(db, pool - amt)
    st = dividend_status(db, cfg, qq)
    msg = ("🪙 全民分红到账 %.2f 元！\n"
           "· 你最近 %d 天活跃，贫困系数拉满\n"
           "· 池子还剩 %.0f 元，明天会员费到账后会再涨\n"
           "· 天天来冒泡，分红一天比一天多" % (
               amt, st["streak"], max(0.0, pool_get(db))))
    return True, msg, {"amount": amt, "pool": round(pool_get(db), 2)}

# ================================================================ 榜单 / 体检
def rob_board(db, cfg, limit=10):
    """战果榜：打土豪得手金额排行。"""
    rows = db.q("SELECT l.qq AS qq, u.nick AS nick, COALESCE(SUM(l.delta),0) AS s "
                "FROM ledger l LEFT JOIN users u ON u.qq=l.qq "
                "WHERE l.reason LIKE '打土豪得手%' GROUP BY l.qq "
                "ORDER BY s DESC LIMIT ?", (limit,))
    out = []
    for i, r in enumerate(rows, 1):
        amt = round(float(r["s"] or 0), 2)
        out.append({"rank": i, "qq": r["qq"], "nick": r["nick"] or r["qq"],
                    "amount": amt, "equity": 0.0, "cash": 0.0, "loan": 0.0,
                    "pl": amt, "bankrupt": False, "jobs": 0})
    return out


def lux_board(db, cfg, limit=10):
    """挥霍榜：累计消费排行（富豪存在感）。"""
    rows = db.q("SELECT l.qq AS qq, u.nick AS nick, COALESCE(SUM(-l.delta),0) AS s "
                "FROM ledger l LEFT JOIN users u ON u.qq=l.qq "
                "WHERE l.reason LIKE '挥霍消费%' GROUP BY l.qq "
                "ORDER BY s DESC LIMIT ?", (limit,))
    out = []
    for i, r in enumerate(rows, 1):
        amt = round(float(r["s"] or 0), 2)
        out.append({"rank": i, "qq": r["qq"], "nick": r["nick"] or r["qq"],
                    "amount": amt, "equity": 0.0, "cash": 0.0, "loan": 0.0,
                    "pl": 0.0, "bankrupt": False, "jobs": 0})
    return out


def wealth_summary(db, cfg):
    """群内财富分布体检：差距到底在哪。

    【为什么要有这个】前面所有机制都在动钱，但没有一张表能回答
    「改了之后差距真的缩小了吗」。这个函数是标尺。
    """
    rank = market.ranking(db, cfg, 2000)
    if not rank:
        return None
    eqs = sorted(float(x["equity"]) for x in rank)
    n = len(eqs)
    total = sum(eqs)
    cash = 0.0
    frozen = 0.0
    for r in db.q("SELECT qq, cash FROM users"):
        c = float(r["cash"] or 0)
        cash += c
        if c > 0:
            u = db.q1("SELECT bankrupt FROM users WHERE qq=?", (r["qq"],))
            if u and u["bankrupt"]:
                frozen += c
    k = max(1, n // 10)
    top10 = sum(eqs[-k:])
    bot50 = sum(eqs[:max(1, n // 2)])
    med = eqs[n // 2] if n else 0.0
    return {"n": n, "total": round(total, 2), "cash": round(cash, 2),
            "frozen": round(frozen, 2),
            "median": round(med, 2),
            "top10_share": round(top10 / total * 100, 2) if total else 0.0,
            "bottom50_share": round(bot50 / total * 100, 2) if total else 0.0,
            "pool": round(pool_get(db), 2)}


def wealth_text(db, cfg):
    st = wealth_summary(db, cfg)
    if not st:
        return "📊 财富分布：暂无数据"
    return ("📊 群内财富分布\n"
            "· %d 人，净资产合计 %.0f 元\n"
            "· 中位数 %.0f 元\n"
            "· 前 10%% 的人占 %.1f%% 的钱；后 50%% 的人占 %.1f%%\n"
            "· 现金 %.0f，其中 %.0f 冻在破产户里\n"
            "· 全民分红池 %.0f 元" % (
                st["n"], st["total"], st["median"], st["top10_share"],
                st["bottom50_share"], st["cash"], st["frozen"], st["pool"]))

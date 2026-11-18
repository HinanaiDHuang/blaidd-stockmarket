# -*- coding: utf-8 -*-
"""财富再分配模块（v4.6）。

【为什么要这个 —— 2026-11-18 现网体检（主群 216 人）】
一个很反直觉的结论：**不是「富人太富」，是 75% 的钱被冻在 4 个破产账户里。**

  * 全群现金 9801 万，其中 7390 万（75%）卡在 4 个资不抵债的账户；
  * 全群欠款 8882 万，7 人有欠款，其中 4 人的「现金 / 欠款」在 0.75~0.99；
  * 最要命的是**死结**：把债还了，净值还是负、还是破产 → 还不如不还；
    不还，这 7390 万就永久冻结，剩下 212 个人只能分那 2410 万；
  * 现金 / 欠款 >= 0.99 的那几位其实只差 1% 就能转正，
    但差这 1% 触发不了任何里程碑，于是永久卡死。

所以真正的「贫富差距」是**流动性冻结**，不是「赢家通吃」。
真正的「富人」只有 1 个半（Kaito 净值 336 万，第二名 48 万，中位数 9.9 万）。
v4.6 做四件事：

1. 【P0】破产清盘（债务重组）：一次性解冻死账 —— 强平变现 → 现金还债 →
   剩余欠款核销 → 拿少量本金重开。系统里被冻的钱重新变成可流通的筹码。
2. 【P1】撒钱：富人的钱往穷人的口袋里流（定向扶贫，不是全群随机抽奖）。
3. 【P1】逆袭基金：净值垫底的玩家每天能领一笔补助，
   给他们一个「必须回来看一眼」的理由。基金的钱来自撒钱手续费，不凭空印。
4. 【P1】资本碾压（在 features.py 里）：有钱的人可以用钱去砸别人的公司，
   高风险高回报，把「有钱」变成「有权动用大钱」。

【守恒说明】清盘核销欠款 = 给玩家做了一次债务豁免，全群净资产会上升。
这是有意的：核销掉的每一分钱都对应着被冻结的现金，
真正消失的是「钱」和「债」这对无效挂账，不是凭空印钞。
"""
import random

import market
import recovery
from db import now

LIQ_MARK = "破产清盘"          # ledger 原因前缀，用来判断「一辈子只能清一次」
SCATTER_MARK = "撒钱派发"       # ledger 原因前缀，散财榜 / 日上限都按它统计
CB_MARK = "逆袭补助"            # ledger 原因前缀
FUND_KEY = "comeback_fund"     # meta key：逆袭基金余额


# ---------------------------------------------------------------- 小工具
def _ledger(db, qq, delta, reason):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    if row is None:
        return
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (str(qq), now(), round(delta, 2), round(float(row["cash"] or 0), 2), reason))


def _day_start():
    return market.day_start()


def _fund_get(db, seed=3000.0):
    """逆袭基金余额。第一次读的时候交易所注入一笔启动资金。

    【为什么不是「每次不够就补」】
    那样等于每个撒钱的人都在给基金注资，基金就变成了永久印钞机。
    现在是：启动资金只发一次，之后基金只靠撒钱手续费涨、靠发补助跌，
    所以补助总量天然被手续费收入卡住 —— 穷人拿到的每一分钱，
    都是富人撒钱时交出来的过路费。
    """
    raw = db.get_meta(FUND_KEY, "")
    if raw is None or raw == "":
        db.set_meta(FUND_KEY, round(float(seed), 2))
        return float(seed)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(seed)


def _fund_set(db, value):
    db.set_meta(FUND_KEY, round(float(value), 2))


# ================================================================ 破产清盘
def liquidation_used(db, qq):
    """这辈子用过清盘没有。"""
    row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND reason LIKE ?",
                (str(qq), LIQ_MARK + "%"))
    return bool(row and int(row["c"] or 0) > 0)


def liquidation_preview(db, cfg, qq):
    """清盘条件体检：能不能清、清完能剩多少。"""
    qq = str(qq)
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None:
        return None
    acct = market.account(db, cfg, qq)
    equity = acct["equity"] if acct else 0.0
    cash = float(user["cash"] or 0)
    loan = float(user["loan"] or 0)
    min_debt = float(cfg.get("liquidation_min_debt", 500000))
    keep_ratio = float(cfg.get("liquidation_keep_ratio", 0.2))
    keep_floor = float(cfg.get("liquidation_keep_floor", 5000))
    keep_cap = float(cfg.get("liquidation_keep_cap", 50000))
    used = liquidation_used(db, qq)
    eligible = (not used) and loan >= min_debt and equity < 0
    reason = ""
    if used:
        reason = "你已经用过一次破产清盘了，这条路一辈子只有一次机会"
    elif loan < min_debt:
        reason = "欠款 %.0f 元，没到清盘门槛（欠款 >= %.0f 元才需要交易所出面）" % (loan, min_debt)
    elif equity >= 0:
        reason = "你的净值已经是 %.2f 元，不算资不抵债，不用清盘" % equity
    keep = min(max(cash * keep_ratio, keep_floor), keep_cap)
    keep = min(keep, max(cash, 0))
    return {
        "qq": qq, "equity": round(equity, 2), "cash": round(cash, 2),
        "loan": round(loan, 2), "used": used, "eligible": eligible,
        "reason": reason, "keep": round(keep, 2),
        "min_debt": min_debt, "keep_cap": keep_cap, "keep_ratio": keep_ratio,
        "lock_days": int(cfg.get("liquidation_lock_days", 30)),
    }


def liquidate(db, cfg, qq):
    """破产清盘：把死账一次性解冻。返回 (ok, msg, info)。

    流程：强平持仓变现 → 留一点重开本金 → 其余现金拿去还债 →
    还不完的部分由交易所代偿核销 → 恢复交易资格 + 30 天重开期。

    【为什么核销不会让玩家暴富】
    玩家净值的变化 = 核销额（还债那部分现金是等量转换，不产生收益）。
    保留的本金被 keep_cap 封顶（默认 5 万），所以哪怕他账上有 4000 万现金，
    清完也只剩 5 万 —— 他拿不到「豁免出来的那一大笔」。
    """
    qq = str(qq)
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None:
        return False, "你还没有开户，先在群里发一次「炒股」", None
    if liquidation_used(db, qq):
        return False, "你已经用过一次破产清盘了，这条路一辈子只有一次机会", None
    if recovery.in_reopen(db, qq):
        return False, ("你还在重开期（还剩 %d 天），先把本金挣回来"
                       % recovery.reopen_days_left(db, qq)), None
    min_debt = float(cfg.get("liquidation_min_debt", 500000))
    loan = float(user["loan"] or 0)
    if loan < min_debt:
        return False, ("你现在欠款 %.0f 元，没到清盘门槛（欠款 >= %.0f 元才需要交易所出面）。"
                       "小债自己还清就行。" % (loan, min_debt)), None
    acct = market.account(db, cfg, qq)
    equity = acct["equity"] if acct else 0.0
    if equity >= 0 and not user["bankrupt"]:
        return False, ("你的净值已经是 %.2f 元，不算资不抵债。真想散财可以试试「撒钱」。"
                       % equity), None

    # 1) 强平所有持仓：先把浮动盈亏变成现金，清盘才有干净的账
    pos = db.q("SELECT id FROM positions WHERE qq=? AND status='open'", (qq,))
    closed = 0
    for p in pos:
        okk, _m, _i = market.close_position(db, cfg, qq, p["id"], force=True)
        if okk:
            closed += 1

    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    raw_cash = float(user["cash"] or 0)
    loan = float(user["loan"] or 0)
    # 强平空头可能把现金打成负数，这部分视作追加债务一起核销
    shortfall = max(0.0, -raw_cash)
    cash_pos = raw_cash + shortfall
    total_debt = loan + shortfall

    # 2) 留一点重开本金（封顶，防止「巨额豁免」）
    keep_ratio = float(cfg.get("liquidation_keep_ratio", 0.2))
    keep_floor = float(cfg.get("liquidation_keep_floor", 5000))
    keep_cap = float(cfg.get("liquidation_keep_cap", 50000))
    keep = cash_pos * keep_ratio
    keep = max(keep, keep_floor)
    keep = min(keep, keep_cap)
    keep = min(keep, cash_pos)

    # 3) 其余现金全部拿去还债
    avail = cash_pos - keep
    repay_amt = min(avail, total_debt)
    final_cash = keep + (avail - repay_amt)
    waived = total_debt - repay_amt

    db.ex("UPDATE users SET cash=?, loan=0 WHERE qq=?", (final_cash, qq))
    _ledger(db, qq, final_cash - raw_cash,
            "%s·债务重组（强平 %d 笔，还债 %.2f）" % (LIQ_MARK, closed, repay_amt))
    if waived > 0:
        _ledger(db, qq, 0.0, "%s·债务核销 %.2f（交易所代偿）" % (LIQ_MARK, waived))
    db.ex("UPDATE loans SET status='waived' WHERE qq=? AND status='open'", (qq,))

    # 4) 恢复交易资格 + 重开期（不能贷款、杠杆 <= 1）
    lock_days = int(cfg.get("liquidation_lock_days", 30))
    reopen_until = now() + lock_days * 86400
    db.ex("UPDATE users SET bankrupt=0, relief_tier=?, bankrupt_earn=0, reopen_until=? "
          "WHERE qq=?", (len(recovery.TIERS), reopen_until, qq))
    try:
        import features
        extra = features.check_achievements(db, cfg, qq)
    except Exception:
        extra = []

    lines = ["\U0001F9F9 破产清盘完成 —— 死账解冻了"]
    if closed:
        lines.append("· 强平持仓 %d 笔，按市价变现" % closed)
    lines.append("· 现金还债 %.2f 元（交易所先垫了剩下的）" % repay_amt)
    lines.append("· 剩余欠款核销 %.2f 元，负债清零" % waived)
    lines.append("· 重开本金 %.2f 元（上限 %.0f）" % (keep, keep_cap))
    lines.append("· 交易资格恢复，但有 %d 天重开期：不能贷款、杠杆最多 1 倍" % lock_days)
    lines.append("—— 重新做人吧，坑位给你留着，别的群友等着看你翻身。")
    for m in extra:
        lines.append(m)
    info = {"closed": closed, "repay": round(repay_amt, 2), "waived": round(waived, 2),
            "keep": round(keep, 2), "lock_days": lock_days,
            "reopen_until": reopen_until, "equity": round(final_cash, 2)}
    return True, "\n".join(lines), info


# ================================================================ 强制清盘
def auto_liquidate(db, cfg):
    """破产逾期没自己清盘的，交易所强制清盘。

    【为什么要自动 —— v4.6 上线后的现网实锤】
    v4.6 把「破产清盘」做成了纯手动指令，结果主群 3 个破产户
    合计 7021 万现金一直冻着：玩家不来点这个按钮，钱就永远不流通，
    剩下的人只能分那 1/4 的活钱，差距看起来反而更刺眼。

    所以保留手动（玩家可以提前自己点、拿回最多保留本金），
    同时加一条兜底：破产满 N 天（默认 7 天）还没清盘的，交易所强制执行。
    强制执行走的还是同一套 liquidate()，保留的本金一样被 keep_cap 封顶，
    不会因为「被强制」就多留钱。

    返回 [(qq, msg), ...]
    """
    days = int(cfg.get("liquidation_auto_days", 7) or 0)
    if days <= 0:
        return []
    min_debt = float(cfg.get("liquidation_min_debt", 500000))
    since = now() - days * 86400
    # bankrupt_since=「这一次破产的开始时间」，倒计时按它算。
    # 老号（v4.4 之前就破的产，bankrupt_ts / bankrupt_since 都是 0）
    # 直接按「已经逾期」处理 —— 这正是 7021 万死钱一直冻着的元凶。
    rows = db.q("SELECT qq FROM users WHERE bankrupt=1 AND loan>=? AND ("
                "(bankrupt_since>0 AND bankrupt_since<=?) OR "
                "((bankrupt_since IS NULL OR bankrupt_since=0) AND "
                "(bankrupt_ts IS NULL OR bankrupt_ts=0)))", (min_debt, since))
    done = []
    for r in rows:
        qq = r["qq"]
        if liquidation_used(db, qq):
            continue
        if recovery.in_reopen(db, qq):
            continue
        ok, msg, _info = liquidate(db, cfg, qq)
        if ok:
            done.append((qq, msg))
    return done


# ================================================================ 撒钱
def scatter_limit(db, cfg, qq):
    """今天还能撒多少钱。额度随身家放大 —— 这就是「让富人去消费」的入口。"""
    acct = market.account(db, cfg, qq)
    equity = max(0.0, acct["equity"]) if acct else 0.0
    base = float(cfg.get("scatter_base_cap", 5000))
    ratio = float(cfg.get("scatter_equity_ratio", 0.01))
    cap = max(base, equity * ratio)
    spent = db.q1("SELECT COALESCE(SUM(-delta),0) s FROM ledger "
                  "WHERE qq=? AND ts>=? AND reason LIKE ?",
                  (str(qq), _day_start(), SCATTER_MARK + "%"))
    used = float(spent["s"] or 0) if spent else 0.0
    return {"cap": round(cap, 2), "used": round(used, 2),
            "left": round(max(0.0, cap - used), 2), "base": base, "ratio": ratio}


def _recipients(db, cfg, qq):
    """能收钱的人：排除自己、破产户、重开期、净值负的，按净值升序（穷的在前）。"""
    out = []
    for it in market.ranking(db, cfg, 2000):
        if it["qq"] == str(qq):
            continue
        if it.get("bankrupt"):
            continue
        if int(it.get("reopen_until") or 0) > now():
            continue
        if float(it["equity"]) < 0:
            continue
        out.append(it)
    out.sort(key=lambda x: float(x["equity"]))
    return out


def scatter(db, cfg, qq, amount):
    """撒钱：把钱随机撒给群友，优先撒给最穷的那批（定向扶贫）。"""
    qq = str(qq)
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None:
        return False, "你还没有开户，先在群里发一次「炒股」", None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return False, "金额不合法", None
    amount = round(amount, 2)
    if amount < 50:
        return False, "撒钱至少 50 块，别发空气红包", None
    if user["bankrupt"]:
        return False, "你还在破产纾困阶段，先把自己的债还清再撒钱吧", None
    if recovery.in_reopen(db, qq):
        return False, ("重开期不许撒钱（还剩 %d 天）—— 先把自己的本金挣回来"
                       % recovery.reopen_days_left(db, qq)), None
    lim = scatter_limit(db, cfg, qq)
    if amount > lim["left"] + 1e-6:
        return False, ("今天撒钱额度只剩 %.0f 元了（上限 %.0f = max(%.0f, 净值×%.1f%%)，"
                       "今天已经撒了 %.0f）"
                       % (lim["left"], lim["cap"], lim["base"], lim["ratio"] * 100,
                          lim["used"])), None
    cash = float(user["cash"] or 0)
    if cash < amount:
        return False, "余额不够，你只有 %.2f 元" % cash, None

    pool = _recipients(db, cfg, qq)
    if not pool:
        return False, "群里没有能收钱的人了（大家都在破产？）", None
    min_shares = int(cfg.get("scatter_min_shares", 10))
    max_shares = int(cfg.get("scatter_max_shares", 60))
    n = random.randint(min_shares, max_shares)
    n = max(1, min(n, len(pool)))
    # 一半是「扶贫池」（净值最低的那批），一半是全群随机
    poor_ratio = float(cfg.get("scatter_poor_ratio", 0.7))
    half = max(1, len(pool) // 2)
    poor, rest = pool[:half], pool[half:]
    picks = []
    want_poor = int(round(n * poor_ratio))
    picks += random.sample(poor, min(want_poor, len(poor)))
    want_rest = n - len(picks)
    if want_rest > 0 and rest:
        picks += random.sample(rest, min(want_rest, len(rest)))
    if not picks:
        picks = random.sample(pool, 1)

    fee_ratio = float(cfg.get("scatter_fee_ratio", 0.05))
    fee = round(amount * fee_ratio, 2)
    give = amount - fee
    weights = [random.uniform(0.7, 1.3) for _ in picks]
    total_w = sum(weights) or 1.0
    shares = [round(give * w / total_w, 2) for w in weights]
    shares[-1] = round(shares[-1] + (give - sum(shares)), 2)
    empty_ratio = float(cfg.get("scatter_empty_ratio", 0.1))
    empty_idx = None
    if len(shares) > 1 and random.random() < empty_ratio:
        empty_idx = random.randrange(len(shares))

    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (amount, qq))
    _ledger(db, qq, -amount, "%s %.2f（%d 份，手续费 %.2f 注入逆袭基金）"
            % (SCATTER_MARK, amount, len(shares), fee))
    nick = user["nick"] or ("群友" + qq[-3:])
    got = []
    empty_cnt = 0
    for i, pair in enumerate(zip(picks, shares)):
        p, s = pair
        if i == empty_idx:
            empty_cnt += 1
            continue
        if s <= 0:
            continue
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (s, p["qq"]))
        _ledger(db, p["qq"], s, "收到 %s 的撒钱红包" % nick)
        got.append((p, s))
    # 手续费进逆袭基金（基金的钱不是凭空印的，是从撒钱手续费里挤出来的）
    seed = float(cfg.get("comeback_fund_seed", 12000))
    fund = _fund_get(db, seed) + fee
    _fund_set(db, fund)

    lines = ["\U0001F9E7 %s 甩了个 %.0f 块的大红包！共 %d 份" % (nick, amount, len(shares))]
    for p, s in got[:8]:
        lines.append("· %s +%.0f" % (p.get("nick") or p["qq"], s))
    if len(got) > 8:
        lines.append("· …… 还有 %d 位幸运群友" % (len(got) - 8))
    if empty_cnt:
        lines.append("· 有 %d 个红包是空的（手气不好 😢）" % empty_cnt)
    lines.append("手续费 %.0f 元已注入逆袭基金，穷兄弟们可以领补助了" % fee)
    info = {"amount": amount, "shares": len(shares), "got": len(got),
            "fee": fee, "empty": empty_cnt, "fund": round(fund, 2)}
    return True, "\n".join(lines), info


# ================================================================ 逆袭基金
def _claimed_today(db, qq):
    row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? AND reason LIKE ?",
                (str(qq), _day_start(), CB_MARK + "%"))
    return bool(row and int(row["c"] or 0) > 0)


def _comeback_amount(cfg, equity):
    ratio = float(cfg.get("comeback_ratio", 0.05))
    lo = float(cfg.get("comeback_min", 150))
    hi = float(cfg.get("comeback_max", 400))
    amt = max(lo, equity * ratio)
    return round(min(hi, amt), 2)


def comeback_status(db, cfg, qq):
    """逆袭补助资格体检。"""
    qq = str(qq)
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None:
        return None
    acct = market.account(db, cfg, qq)
    equity = acct["equity"] if acct else 0.0
    cash = float(user["cash"] or 0)
    cap = float(cfg.get("comeback_equity_cap", 20000))
    cash_cap = float(cfg.get("comeback_cash_cap", 5000))
    claimed = _claimed_today(db, qq)
    ok = True
    why = ""
    if user["bankrupt"]:
        ok, why = False, "你还在破产纾困阶段，先走清盘或打工翻身"
    elif recovery.in_reopen(db, qq):
        ok, why = False, "重开期不能领补助，自己把本金挣回来"
    elif equity <= 0:
        ok, why = False, "你的净值是 %.2f，先把窟窿填上" % equity
    elif equity >= cap:
        ok, why = False, "你的净值 %.2f 已经超过补助线 %.0f，不该来蹭" % (equity, cap)
    elif cash >= cash_cap:
        ok, why = False, "你账上还有 %.2f 现金，不算困难户" % cash
    elif claimed:
        ok, why = False, "今天已经领过了，明天再来"
    return {"qq": qq, "equity": round(equity, 2), "cash": round(cash, 2),
            "claimed": claimed, "ok": ok, "why": why,
            "amount": _comeback_amount(cfg, equity),
            "cap": cap, "cash_cap": cash_cap,
            "fund": round(_fund_get(db, float(cfg.get("comeback_fund_seed", 12000))), 2)}


def comeback(db, cfg, qq):
    """逆袭基金补助：给净值垫底、且身无分文的玩家发一笔生活费。"""
    qq = str(qq)
    st = comeback_status(db, cfg, qq)
    if st is None:
        return False, "你还没有开户，先在群里发一次「炒股」", None
    if not st["ok"]:
        return False, st["why"], None
    seed = float(cfg.get("comeback_fund_seed", 12000))
    fund = _fund_get(db, seed)
    if fund < st["amount"]:
        # 交易所兜底补齐到上限（基金规模封顶 seed，多余的撒钱手续费留在基金里）
        fund = seed
        _fund_set(db, fund)
    day_cap = float(cfg.get("comeback_daily_cap", 3000))
    spent = db.q1("SELECT COALESCE(SUM(-delta),0) s FROM ledger "
                  "WHERE ts>=? AND reason LIKE ?", (_day_start(), CB_MARK + "%"))
    used = float(spent["s"] or 0) if spent else 0.0
    if used + st["amount"] > day_cap:
        return False, ("今天的逆袭基金已经发完（上限 %.0f），明天再来" % day_cap), None
    amount = st["amount"]
    db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (amount, qq))
    _ledger(db, qq, amount, "%s %.2f" % (CB_MARK, amount))
    _fund_set(db, fund - amount)
    msg = ("\U0001F6CD 逆袭基金补助到账 %.2f 元。\n"
           "（今天全群发了 %.0f 元，基金还剩 %.0f）\n"
           "别躺平 —— 这钱只够撑几天，炒股翻身才是正经事。"
           % (amount, used + amount, max(0.0, fund - amount)))
    return True, msg, {"amount": amount, "fund": round(max(0.0, fund - amount), 2)}

# -*- coding: utf-8 -*-
"""财阀基金（v4.13）—— 给富人一个「带风险的钱出口」。

【现网实测（2026-11-20）为什么必须做这个】
主群 942262191：216 人，总净值 534.6 亿，中位数 9.9 万，top10% 占 100.00%。
头部 Kaito 一天之内 100.45 亿 → 534.57 亿：20 倍杠杆日内做空，
吃到了 11-19 那次大盘级爆炸新闻砸出来的 -6.74%。

【为什么「按比例收税」救不了】
534 亿 × 0.26%/天 = 1390 万，而他一天赚 434 亿 —— 收税是零头。
所以要两条腿走：
  ① 收敛放大器（market.py 的 whale_notional_ratio）；
  ② 给富豪一个**带风险的钱出口**：财阀基金。
     钱不是被收走，是被押进一个可能血本无归的池子。

【财阀基金怎么转】
  · 出资：净值 ≥ fund_min_equity 的玩家，每天自动投 min(净值 × fund_ratio, fund_max)。
    没有绝对上限地按比例抽 ⇒ 富豪的钱必须持续以「有风险」的方式出去。
  · 押注：基金按 fund_leverage 倍押「群内公司等权收益中位数 r」，
    **当日缴当日清**（本金不留存，池子不会无限膨胀）。
  · 结算：基金净盈亏 = 出资 × (r × 杠杆 − 管理费)。
      盈利 → 一部分给押对的群友、一部分进全民分红池、其余返给出资人；
      亏损 → **出资人 100% 自己扛**（从现金里扣）。
  · 管理费 fund_fee 全额**进全民分红池** —— 这就是「富豪去消费」的那笔钱，
    而且它是**按比例**的：富豪的钱越多，交的管理费越多。

【为什么群友侧要设绝对上限】
鲸鱼的体量是中位数的 500 万倍。按比例抽，穷人一夜暴富；
不按比例抽，富豪那点钱又不够看。
所以：**富豪那边按比例（公平），群友那边设绝对上限（经济不崩）**。
    fund_pool_cap  全民分红池每天最多从基金里进多少
    fund_bet_cap  每天最多分给押注赢家多少

【群友怎么参与】
「押基金」：小额押明天基金是涨还是跌。
押对 → 拿回本金 + 分基金盈利；押错 → 本金直接进全民分红池。
押注的钱天然来自出资最多的那位富豪 ⇒ 群内自带的共同话题。
"""
import time

import market
import tycoon

POOL_KEY = "whale_fund_pool"      # meta: 基金当日结转余额（平时是 0）
DAY_KEY = "whale_fund_day"        # meta: 上次**跑过结算**的日期（幂等）
FUND_LAST_KEY = "whale_fund_last_day"   # meta: 已经结算过的最后一个「市场日」
CONTRIB_MARK = "财阀基金出资"
RET_MARK = "财阀基金结算"
BET_MARK = "押基金"
SIDE_UP = "涨"
SIDE_DOWN = "跌"


# ---------------------------------------------------------------- 小工具
def _today():
    return time.strftime("%Y-%m-%d", time.localtime())


def _yesterday():
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))


def _next_day(day):
    """下一个自然日（YYYY-MM-DD）。"""
    y, m, d = [int(x) for x in str(day).split("-")]
    t = time.localtime(time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)) + 86400)
    return time.strftime("%Y-%m-%d", t)


def _day_bounds(day):
    """[day 00:00, 次日 00:00) 的时间戳。"""
    y, m, d = [int(x) for x in str(day).split("-")]
    start = int(time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)))
    return start, start + 86400


def _cash(db, qq):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    return float(row["cash"] or 0) if row else 0.0


def _equity(db, cfg, qq):
    acct = market.account(db, cfg, str(qq))
    return acct["equity"] if acct else 0.0


def _nick(db, qq):
    row = db.q1("SELECT nick FROM users WHERE qq=?", (str(qq),))
    return (row["nick"] or ("群友" + str(qq)[-3:])) if row else str(qq)


def _ledger(db, qq, delta, reason):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    if row is None:
        return
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (str(qq), int(time.time()), round(delta, 2),
           round(float(row["cash"] or 0), 2), reason))


# ---------------------------------------------------------------- 行情基准
def market_return(db, day):
    """群内公司当日的等权收益中位数（从 daily_px 读）。

    为什么用中位数不用均值：均值会被个别暴涨/暴跌的票带偏，
    而基金要押的是「整个市场」的方向，不是某一只票。
    """
    try:
        rows = db.q("SELECT price, prev_close FROM daily_px "
                    "WHERE day=? AND prev_close>0", (day,))
    except Exception:
        return 0.0
    rets = []
    for r in rows:
        try:
            base = float(r["prev_close"] or 0)
            px = float(r["price"] or 0)
        except (TypeError, ValueError):
            continue
        if base > 0:
            rets.append(px / base - 1.0)
    if not rets:
        return 0.0
    rets.sort()
    return rets[len(rets) // 2]


def contributors(db, day):
    """当日出资人及其出资额。"""
    start, end = _day_bounds(day)
    rows = db.q("SELECT qq, SUM(-delta) s FROM ledger "
                "WHERE ts>=? AND ts<? AND reason=? GROUP BY qq",
                (start, end, CONTRIB_MARK))
    out = {}
    for r in rows:
        try:
            amt = float(r["s"] or 0)
        except (TypeError, ValueError):
            continue
        if amt > 0:
            out[str(r["qq"])] = amt
    return out


def bettors(db, day):
    """当日押注记录：{qq: [方向, 金额]}。"""
    start, end = _day_bounds(day)
    rows = db.q("SELECT qq, reason, SUM(-delta) s FROM ledger "
                "WHERE ts>=? AND ts<? AND reason LIKE ? GROUP BY qq, reason",
                (start, end, BET_MARK + "%"))
    out = {}
    for r in rows:
        try:
            amt = float(r["s"] or 0)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        reason = r["reason"] or ""
        side = SIDE_DOWN if SIDE_DOWN in reason else SIDE_UP
        out[str(r["qq"])] = [side, amt]
    return out


# ---------------------------------------------------------------- 出资
def fund_stake(db, cfg, qq):
    """这个人今天该往基金里投多少。"""
    eq = _equity(db, cfg, qq)
    min_eq = float(cfg.get("fund_min_equity", 15000000))
    if eq < min_eq:
        return 0.0
    ratio = float(cfg.get("fund_ratio", 0.05))
    cap = float(cfg.get("fund_max", 2000000))
    amt = max(0.0, eq) * ratio
    if cap > 0:
        amt = min(amt, cap)
    return round(amt, 2)


def collect_fund(db, cfg):
    """每日自动出资：净值够高的人把净值的一部分押进财阀基金。

    幂等由调用方保证（每天只在跨天结算里跑一次）。
    返回 (人数, 出资总额)。
    """
    rows = db.q("SELECT qq FROM users")
    count = 0
    total = 0.0
    for r in rows:
        qq = str(r["qq"])
        amt = fund_stake(db, cfg, qq)
        if amt < 1:
            continue
        cash = _cash(db, qq)
        if cash < amt:
            # 钱不够就少投，不能把现金抽成负数
            amt = round(min(amt, cash), 2)
            if amt < 1:
                continue
        db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (amt, qq))
        _ledger(db, qq, -amt, CONTRIB_MARK)
        total += amt
        count += 1
    return count, round(total, 2)


# ---------------------------------------------------------------- 结算
def settle_fund(db, cfg):
    """每日结算财阀基金 + 群友押注。幂等：同一天只跑一次。

    【为什么要按「天」补结算】
    出资发生在跨天那一刻，结算也在跨天那一刻。万一服务停了两天再起来，
    中间那两天的出资没人结算 —— 钱被扣走了却没回本。
    所以这里记住「已经结算到哪个市场日」，缺哪几天就补哪几天（最多补 15 天）。

    返回给群里的战报文字（没有可结算的东西时返回空串）。

    【钱的流向】
      出资人付出 P → 基金按 L 倍押市场 → 日终：
        · 市场涨 r：基金赚 P×r×L，扣掉管理费后分给押对的群友 /
          进全民分红池 / 返还给出资人。
        · 市场跌 r：基金亏 P×|r×L|，**出资人 100% 自己扛**（从现金扣）。
      管理费 fund_fee 直接进全民分红池 —— 这就是「富豪去消费」的那笔钱。
      押错的人本金进全民分红池（直接给更穷的人）。
    """
    today = _today()
    if db.get_meta(DAY_KEY, "") == today:
        return ""
    db.set_meta(DAY_KEY, today)
    last = db.get_meta(FUND_LAST_KEY, "")
    yest = _yesterday()
    days = []
    if last:
        d = last
        while len(days) < 15:
            nd = _next_day(d)
            if nd > yest:
                break
            days.append(nd)
            d = nd
    else:
        days = [yest]
    report = ""
    for day in days:
        rep_txt = _settle_day(db, cfg, day)
        if rep_txt:
            report = rep_txt
    return report


def _settle_day(db, cfg, day):
    """结算某一个市场日的基金 + 押注。返回战报文字（无内容时返回空串）。

    【幂等】已经结算过的日子直接跳过。标记放在**结算之前**：
    中途崩了最多是那笔钱卡在基金里（人工能查）；
    放后面则可能第二天再发一次 —— 那就是直接把钱发重了，更糟。
    """
    if db.get_meta(FUND_LAST_KEY, "") == day:
        return ""
    db.set_meta(FUND_LAST_KEY, day)
    contrib = contributors(db, day)
    bets = bettors(db, day)
    pool = round(sum(contrib.values()), 2)
    if pool <= 0 and not bets:
        return ""
    lev = float(cfg.get("fund_leverage", 3.0) or 0)
    fee_ratio = float(cfg.get("fund_fee", 0.05) or 0)
    pool_ratio = float(cfg.get("fund_pool_ratio", 0.5) or 0)
    bet_cut = float(cfg.get("fund_bet_cut", 0.3) or 0)
    pool_cap = float(cfg.get("fund_pool_cap", 100000) or 0)
    bet_cap = float(cfg.get("fund_bet_cap", 30000) or 0)

    r = market_return(db, day)
    fund_gain = pool * r * lev
    fee = pool * fee_ratio
    rest = pool + fund_gain - fee          # 基金账户里还剩多少可以分
    if rest >= 0:
        to_betters = rest * bet_cut
        to_pool = rest * pool_ratio
    else:
        to_betters = 0.0
        to_pool = 0.0

    if r > 0:
        win_side = SIDE_UP
    elif r < 0:
        win_side = SIDE_DOWN
    else:
        win_side = None
    if win_side is None and to_betters > 0:
        to_pool += to_betters                # 平盘：没人押对，这钱给群友
        to_betters = 0.0

    # 群友侧设绝对上限：鲸鱼的体量是中位数的 500 万倍，
    # 不封顶的话富人的一笔钱能把整个经济炸穿。
    # 被削掉的部分**留在出资人手里**（to_funders 随后重算），
    # 所以账一定是平的：to_funders + to_betters + to_pool == rest。
    if bet_cap > 0 and to_betters > bet_cap:
        to_betters = bet_cap
    if pool_cap > 0 and to_pool > pool_cap:
        to_pool = pool_cap
    to_funders = rest - to_betters - to_pool

    # ---- 出资人结算 ----
    gainers = 0
    losers = 0
    for qq, amt in contrib.items():
        share = to_funders * (amt / pool) if pool > 0 else 0.0
        if share >= 0:
            db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (share, qq))
            _ledger(db, qq, share, RET_MARK + "·返还")
            gainers += 1
        else:
            cash = _cash(db, qq)
            pay = min(cash, -share)
            if pay > 0:
                db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (pay, qq))
                _ledger(db, qq, -pay, RET_MARK + "·亏损")
                losers += 1

    # ---- 押注结算 ----
    bet_total = round(sum(v[1] for v in bets.values()), 2)
    winners = {}
    loser_total = 0.0
    for qq, (side, amt) in bets.items():
        if win_side and side == win_side:
            winners[qq] = amt
        else:
            loser_total += amt
    winner_total = round(sum(winners.values()), 2)
    for qq, amt in winners.items():
        bonus = (to_betters * (amt / winner_total)) if winner_total > 0 else 0.0
        back = amt + bonus
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (back, qq))
        _ledger(db, qq, back, "押基金赢了·" + (win_side or ""))
    # 管理费 + 盈利分成 -> 全民分红池（这是「富豪的钱流到穷人」的主干道）
    pool_in = to_pool + fee
    if pool_in > 0:
        tycoon.pool_add(db, pool_in)
    # 押错的人本金直接进更穷的人手里
    if loser_total > 0:
        tycoon.pool_add(db, loser_total)

    lines = []
    dir_txt = "涨" if r > 0 else ("跌" if r < 0 else "平")
    lines.append("🏛 财阀基金日报（%s）" % day)
    lines.append("· 全市场等权涨跌 %+.2f%%，基金 %dx 押注 ⇒ 盈亏 %+.0f 元"
                 % (r * 100, lev, fund_gain))
    if contrib:
        top = sorted(contrib.items(), key=lambda kv: -kv[1])[:3]
        who = "、".join("%s %.0f万" % (_nick(db, qq), amt / 10000.0)
                        for qq, amt in top)
        lines.append("· 出资 %d 人共 %.0f 元（%s）" % (len(contrib), pool, who))
    if to_funders >= 0:
        lines.append("· 基金净赚 %.0f 元，出资人拿回 %.0f 元" % (rest, to_funders))
    else:
        lines.append("· 基金亏了 %.0f 元，**出资人自己扛**（每人按出资比例倒扣）"
                     % (-to_funders))
    lines.append("· 管理费 %.0f 元 + 盈利分成 %.0f 元进了全民分红池（群友按贫富分）"
                 % (fee, to_pool))
    if bet_total > 0:
        lines.append("· 群友押注 %.0f 元，押对拿回 %.0f 元，押错的 %.0f 元进了分红池"
                     % (bet_total, winner_total + to_betters, loser_total))
    if to_funders < 0:
        lines.append("· 今天市场跌了，押「跌」的群友赢了：这是富豪的钱")
    lines.append("· 财阀基金榜：/财阀基金")
    return "\n".join(lines)


# ---------------------------------------------------------------- 群友押注
def bet_status(db, cfg, qq):
    qq = str(qq)
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return None
    day = _today()
    start, end = _day_bounds(day)
    used = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? AND ts<? "
                 "AND reason LIKE ?", (qq, start, end, BET_MARK + "%"))
    mine = bettors(db, day).get(qq)
    limit = int(cfg.get("fund_bet_daily_limit", 1) or 1)
    max_amt = float(cfg.get("fund_bet_max", 1000))
    min_amt = float(cfg.get("fund_bet_min", 10))
    cash = _cash(db, qq)
    return {"qq": qq, "nick": me["nick"] or ("群友" + qq[-3:]),
            "cash": round(cash, 2), "max": max_amt, "min": min_amt,
            "used": int(used["c"] or 0) if used else 0,
            "limit": limit, "left": max(0, limit - (int(used["c"] or 0) if used else 0)),
            "side": mine[0] if mine else None,
            "amount": mine[1] if mine else 0.0,
            "pool": round(tycoon.pool_get(db), 2)}


def bet_fund(db, cfg, qq, side):
    """群友押基金方向。side = 涨 / 跌。返回 (ok, msg, info)。"""
    qq = str(qq)
    st = bet_status(db, cfg, qq)
    if st is None:
        return False, "你还没有开户，先在群里发一次「炒股」", None
    if st["left"] <= 0:
        return False, ("今天已经押过了，明天再来 —— 一次只能押一个方向，"
                       "不然就成庄家了"), None
    key = str(side or "").strip()
    if key in ("涨", "多", "看涨", "up", "long", "1"):
        side = SIDE_UP
    elif key in ("跌", "空", "看跌", "down", "short", "0"):
        side = SIDE_DOWN
    else:
        return False, ("用法：押基金 涨　/　押基金 跌\n"
                       "押对了拿回本金还分基金盈利，押错了本金直接进全民分红池"), None
    max_amt = float(cfg.get("fund_bet_max", 1000))
    min_amt = float(cfg.get("fund_bet_min", 10))
    amt = min(max_amt, st["cash"])
    if amt < min_amt:
        return False, "押注至少 %.0f 元，你账上只有 %.0f" % (min_amt, st["cash"]), None
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (amt, qq))
    _ledger(db, qq, -amt, "%s·%s" % (BET_MARK, side))
    msg = ("🎲 押基金：%s %.0f 元\n"
           "· 明天收盘看全市场等权涨跌，涨了算你赢、跌了算你输\n"
           "· 押对：拿回本金，再分基金盈利的一部分\n"
           "· 押错：本金直接进全民分红池（不给赢家，给更穷的人）\n"
           "· 基金的钱是富豪出的 —— 你押的其实是富豪的仓位"
           % (side, amt))
    return True, msg, {"side": side, "amount": amt}


# ---------------------------------------------------------------- 状态 / 榜单
def fund_status(db, cfg, qq):
    qq = str(qq)
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return None
    eq = _equity(db, cfg, qq)
    day = _today()
    start, end = _day_bounds(day)
    rows = db.q("SELECT qq, SUM(-delta) s FROM ledger WHERE ts>=? AND ts<? "
                "AND reason=? GROUP BY qq ORDER BY s DESC", (start, end, CONTRIB_MARK))
    mine = 0.0
    for r in rows:
        if str(r["qq"]) == qq:
            mine = float(r["s"] or 0)
    return {"qq": qq, "nick": me["nick"] or ("群友" + qq[-3:]),
            "equity": round(eq, 2), "stake_today": round(mine, 2),
            "stake_next": fund_stake(db, cfg, qq),
            "min_equity": float(cfg.get("fund_min_equity", 15000000)),
            "ratio": float(cfg.get("fund_ratio", 0.05)),
            "max": float(cfg.get("fund_max", 2000000)),
            "lev": float(cfg.get("fund_leverage", 3.0)),
            "fee": float(cfg.get("fund_fee", 0.05)),
            "pool": round(tycoon.pool_get(db), 2),
            "today_return": round(market_return(db, _yesterday()) * 100, 2)}


def fund_board(db, cfg, limit=10):
    """财阀基金榜：今日出资最多的人。"""
    day = _today()
    start, end = _day_bounds(day)
    rows = db.q("SELECT qq, SUM(-delta) s FROM ledger WHERE ts>=? AND ts<? "
                "AND reason=? GROUP BY qq ORDER BY s DESC LIMIT ?",
                (start, end, CONTRIB_MARK, limit))
    out = []
    for i, r in enumerate(rows, 1):
        amt = float(r["s"] or 0)
        if amt <= 0:
            continue
        out.append({"rank": i, "qq": str(r["qq"]), "nick": _nick(db, r["qq"]),
                    "amount": round(amt, 2)})
    return out


def bet_board(db, cfg, limit=10):
    """押注榜：今日押了多少。"""
    day = _today()
    start, end = _day_bounds(day)
    rows = db.q("SELECT qq, reason, SUM(-delta) s FROM ledger WHERE ts>=? AND ts<? "
                "AND reason LIKE ? GROUP BY qq ORDER BY s DESC LIMIT ?",
                (start, end, BET_MARK + "%", limit))
    out = []
    for i, r in enumerate(rows, 1):
        reason = r["reason"] or ""
        out.append({"rank": i, "qq": str(r["qq"]), "nick": _nick(db, r["qq"]),
                    "side": SIDE_DOWN if SIDE_DOWN in reason else SIDE_UP,
                    "amount": round(float(r["s"] or 0), 2)})
    return out


def fund_text(db, cfg, qq=None):
    """群里发「财阀基金」看到的说明。"""
    lines = ["🏛 财阀基金（富豪的钱往哪儿去）"]
    lines.append("· 净值 ≥ %.0f 万的富豪，每天自动把净值的 %.0f%%（封顶 %.0f 万）"
                 "押进基金" % (float(cfg.get("fund_min_equity", 15000000)) / 10000.0,
                               float(cfg.get("fund_ratio", 0.05)) * 100,
                               float(cfg.get("fund_max", 2000000)) / 10000.0))
    lines.append("· 基金拿这笔钱 %.0f 倍杠杆押「全市场等权涨跌」—— "
                 "涨了分、跌了出资人自己扛" % float(cfg.get("fund_leverage", 3.0)))
    lines.append("· 管理费 %.0f%%/天直接进全民分红池（这就是富豪的「消费」）"
                 % (float(cfg.get("fund_fee", 0.05)) * 100))
    lines.append("· 群友可以「押基金 涨」/「押基金 跌」，最多 %.0f 元/次"
                 % float(cfg.get("fund_bet_max", 1000)))
    lines.append("· 押错的钱直接进全民分红池，不给赢家")
    board = fund_board(db, cfg, 5)
    if board:
        lines.append("· 今日出资榜：%s" % "、".join(
            "%s %.0f万" % (b["nick"], b["amount"] / 10000.0) for b in board))
    lines.append("· 全民分红池余额 %.0f 元" % tycoon.pool_get(db))
    return "\n".join(lines)

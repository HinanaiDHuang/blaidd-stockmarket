# -*- coding: utf-8 -*-
"""v4.18 低门槛炒股入口：快捷下单（按金额一键买入）+ 每日定投。

【这一层要解决什么】
v4.17 之前的炒股入口对新手很不友好：
  · 得自己算「买多少股」—— 股价 25 元的票，钱包只有 200 元，到底能买几股？
    算错了不是被拒，就是剩一堆现金躺在账上；
  · 得自己在两百多只票里翻 —— 找不到该买哪一只；
  · 得记得每天手动操作 —— 于是又回到「只签到不炒股」。

所以加了「快捷模式」：选一只推荐票 → 选个金额档 → 点一下就成交（5 秒），
再加「每日定投」把这 5 秒也省掉。

【防套利：碎股小单不算数】
低门槛必须配一道闸，否则就是刷分机器：
  每日任务「今日首单 125」+ 翻身任务「第一笔买入 200~1200」+
  成就「第一笔交易 300」≈ 1325 元奖励。
20 元的小单要是也能领这些，就是 66 倍套利。
所以：保证金占用 < small_order_threshold（200 元）的单子标成「试水仓」(small=1)，
不计入每日任务、翻身任务、交易类成就 —— 但交易本身照常、能赚能亏，
另外给一个专属的一次性小成就「初试锋芒」当安慰奖。

【为什么按「保证金」而不是「成交额」判定】
杠杆能把成交额放大 20 倍：20 元本金 × 20 倍杠杆 = 400 元成交额，
看成交额就能绕过闸。门槛看的是「你真金白银押进去多少」，
所以判定放在 market.place_order 里按保证金算，手动下单同样绕不掉。

【金额的口径：投入的现金，不是成交额】
「我只有 100 元」是玩家脑子里的单位，所以金额 = 投入的本金：
    名义 = 金额 × 杠杆
    股数 = floor(名义 / 现价)
    占用保证金 = 成交额 / 杠杆
20 元的本金配 25 元的股价，1 倍杠杆买不到 1 股 —— 这时候自动把杠杆
提到刚好买得起 1 股的最低档（2 倍），并在界面上写清楚「2 倍才买得起」。
"""
import math
import random
import time

import market
import tiers

# 推荐票的缓存：按群缓存 60 秒。
# 前端每 2 秒轮询一次状态，推荐票要是每次都重算，
# 玩家会看到列表每 2 秒换一批 —— 根本没法下手。
_PICK_CACHE = {}
_PICK_TTL = 60.0

# 波动适配的理想区间：日内振幅 2.5% 附近最好玩
_IDEAL_AMP = 0.025


def now():
    return int(time.time())


def _cfgf(cfg, key, default):
    try:
        v = cfg.get(key, default)
    except AttributeError:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def amount_tiers(cfg):
    """金额档位（投入的本金，单位元）。"""
    raw = cfg.get("quick_amounts")
    if not raw:
        raw = [20, 50, 100, 200, 500]
    out = []
    for x in raw:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.append(v)
    out.sort()
    if not out:
        out = [20.0, 50.0, 100.0, 200.0, 500.0]
    return out


def band_pos(cfg, comp):
    """当前价在涨跌停带里的相对位置：1 = 涨停，-1 = 跌停，0 = 平盘。"""
    try:
        pc = float(comp["prev_close"] or 0)
        p = float(comp["price"] or 0)
        lim = _cfgf(cfg, "price_limit", 0.1)
    except (TypeError, ValueError, KeyError, IndexError):
        return 0.0
    if pc <= 0 or lim <= 0:
        return 0.0
    return (p / pc - 1.0) / lim


def band_block(cfg, comp):
    """贴板判定：跟 market.place_order 用同一套规则。

    返回 True = 现在不能建仓（涨停附近 / 跌停附近）。
    推荐票必须先过这一关，否则会出现「推荐给你，点进去买不了」。
    """
    ratio = _cfgf(cfg, "band_block_ratio", 0.0)
    if ratio <= 0:
        return False
    try:
        pc = float(comp["prev_close"] or 0)
        p = float(comp["price"] or 0)
        lim = _cfgf(cfg, "price_limit", 0.1)
    except (TypeError, ValueError, KeyError, IndexError):
        return False
    if pc <= 0 or lim <= 0:
        return False
    edge = lim * (1.0 - ratio)
    chg = p / pc - 1.0
    return chg >= edge or chg <= -edge


def _today_turnover(db, cid, start_ts):
    row = db.q1("SELECT SUM(v) s FROM candles WHERE cid=? AND ts>=?", (cid, start_ts))
    try:
        return float(row["s"] or 0.0)
    except (TypeError, ValueError, KeyError, IndexError):
        return 0.0


def _amp(comp):
    """日内振幅（今日最高 - 今日最低）/ 现价。"""
    try:
        hi = float(comp["high"] or 0)
        lo = float(comp["low"] or 0)
        p = float(comp["price"] or 0)
    except (TypeError, ValueError, KeyError, IndexError):
        return 0.0
    if p <= 0:
        return 0.0
    return max(0.0, (hi - lo) / p)


def _risk_stars(amp, chg):
    """风险星级：1 = 温和，2 = 中等，3 = 野。"""
    if amp < 0.015 and abs(chg) < 0.02:
        return 1
    if amp < 0.04 and abs(chg) < 0.06:
        return 2
    return 3


def _risk_text(stars):
    return {1: "温和", 2: "中等", 3: "较野"}.get(stars, "中等")


def pick_stocks(db, cfg, gid="", n=3, force=False, refresh=False):
    """推荐票：给「不会选股 / 懒得翻列表」的人一个能立刻下手的入口。

    返回 {"items": [...], "reason": "..."}。
    items 里每一条都带人话理由、风险星级和建议金额。

    refresh=True 是「换一批」：不重排全部分数，而是把前面那批好票像环一圈转一下。
    这样出来的仍然是活跃的好票，但顺序不同了 ——
    玩家看到的是「新选择」，而不是随机蹦出来的垃圾票。
    """
    n = max(1, min(6, int(n or 3)))
    key = str(gid or "0")
    if not force:
        hit = _PICK_CACHE.get(key)
        if hit and (now() - hit[0]) < _PICK_TTL:
            return hit[1]

    # 熔断（大盘级黑天鹅）期间整个池子都不能开仓，直接说清楚，别硬推。
    until, reason = market._circuit(db)
    if until:
        left = max(1, (until - now() + 59) // 60)
        out = {"items": [], "reason": "市场临时休市中（%s），约 %d 分钟后恢复开盘" % (reason, left)}
        _PICK_CACHE[key] = (now(), out)
        return out

    rows = db.q("SELECT id, name, ticker, industry, price, prev_close, high, low, vol "
                "FROM companies WHERE price > 0")
    blocked = _cfgf(cfg, "band_block_ratio", 0.0)
    edge = (1.0 - blocked) if blocked > 0 else 1.0
    cands = []
    for r in rows:
        bp = band_pos(cfg, r)
        if abs(bp) >= edge:
            continue
        cands.append((r, bp))
    # 【推荐池再收一道】别推「马上就要撞板」的票。
    # 撞板之后 market.place_order 会直接拒（封板禁开仓），
    # 让人点进去吃一个闭门羹，白折腾一轮。
    # 候选不够 3 只的时候才放宽回正常规则。
    safe = [x for x in cands if abs(x[1]) < edge * 0.75]
    if len(safe) >= n:
        cands = safe
    # 【再收一道】优先推「小额定投买得起」的票。
    # 500 元的票要 500 元本金才买得起 1 股 —— 那不是「试水」该推的东西。
    tiers = amount_tiers(cfg)
    cheap_cap = tiers[2] if len(tiers) >= 3 else tiers[-1]
    # 【最小档也要买得起】quick_min_amount × quick_max_leverage 是「最小金额档 + 满杠杆」
    # 能买到的最高股价 —— 推荐票先按这条筛，玩家点「20 元」时十有八九能买到 1 股。
    mini_amt = _cfgf(cfg, "quick_min_amount", 20.0)
    mini_lev = int(_cfgf(cfg, "quick_max_leverage", 3)) or 3
    mini_cap = mini_amt * mini_lev
    for cap in (mini_cap, cheap_cap):
        sub2 = [x for x in cands if float(x[0]["price"] or 0) <= cap]
        if len(sub2) >= n:
            cands = sub2
            break
    pool = []
    for r, bp in cands:
        try:
            price = float(r["price"])
            pc = float(r["prev_close"] or 0)
        except (TypeError, ValueError):
            continue
        chg = (price / pc - 1.0) if pc > 0 else 0.0
        amp = _amp(r)
        pool.append({
            "id": int(r["id"]), "name": r["name"], "ticker": r["ticker"],
            "industry": r["industry"], "price": price, "chg": chg, "amp": amp,
            "band": bp,
        })
    if not pool:
        out = {"items": [], "reason": "现在没有适合开仓的票，等行情缓一缓再来"}
        _PICK_CACHE[key] = (now(), out)
        return out

    start = features_day_start()
    turn = {}
    for p in pool:
        turn[p["id"]] = _today_turnover(db, p["id"], start)
    tmax = max(turn.values()) if turn else 1.0
    tmax = tmax if tmax > 0 else 1.0

    maxtier = tiers[-1]
    mintier = tiers[0]
    for p in pool:
        # 活跃度：今天的换手（成交额代理），0~1
        act = turn[p["id"]] / tmax
        p["act_ratio"] = act
        # 波动适配：振幅越贴近 2.5% 越好玩，太死和太野都扣分
        fit = 1.0 - min(1.0, abs(p["amp"] - _IDEAL_AMP) / _IDEAL_AMP)
        # 可买性：小额定投最在意的是「这个价我买得起吗」。
        # 20 元的票用最小档就能买到 1 股，500 元的票得 500 元起步 ——
        # 推荐给「我就想试一下」的人，自然该往前者偏。
        if p["price"] <= mintier:
            buy = 1.0
        elif p["price"] <= maxtier:
            buy = 1.0 - 0.6 * (p["price"] - mintier) / max(1e-9, maxtier - mintier)
        else:
            buy = 0.05
        # 一点点随机抖动：不要天天推同一批，也避免所有人买同一只
        jitter = random.random() * 0.06
        p["score"] = 0.38 * act + 0.34 * fit + 0.28 * buy + jitter
        p["act"] = act
        p["fit"] = fit

    pool.sort(key=lambda x: x["score"], reverse=True)
    # 【换一批】把前 12 名整体转一圈再取。
    # 不能直接 random.shuffle 全池 —— 那会把活跃票和垃圾票混在一起随机抽。
    if refresh and len(pool) > n:
        head = pool[:12]
        off = random.randrange(1, len(head)) if len(head) > 1 else 0
        pool = head[off:] + head[:off] + pool[12:]
    picks = pool[:n]
    items = []
    for idx, p in enumerate(picks):
        stars = _risk_stars(p["amp"], p["chg"])
        # 建议金额：挑一个刚好买得起 1 股的档位（往上取整到最近的档）
        want = p["price"]
        sug = maxtier
        for t in amount_tiers(cfg):
            if t >= want:
                sug = t
                break
        if p["price"] > maxtier:
            sug = maxtier
        reason = _pick_reason(p, stars, idx)
        items.append({
            "cid": p["id"], "name": p["name"], "ticker": p["ticker"],
            "industry": p["industry"], "price": round(p["price"], 4),
            "chg": round(p["chg"] * 100, 2), "amp": round(p["amp"] * 100, 2),
            "risk": stars, "risk_text": _risk_text(stars),
            "suggest": sug, "reason": reason,
        })
    out = {"items": items, "reason": "挑了 %d 只活跃度和波动都还行的票" % len(items)}
    _PICK_CACHE[key] = (now(), out)
    return out


def _pick_reason(p, stars, rank=0):
    """给推荐票写一句人话理由。

    「今天换手最活跃」这种话要是三只票都这么说，等于没说 ——
    成交额最高的本来就都排在前面，光看比例区分不开。
    所以「最活跃」只留给排第一的那只（rank==0），其余按比例分级。

    【只说「凭什么推它」，不重复卡片上已经有的数字】
    卡片第二行已经摆着涨跌幅和振幅了，理由里再抄一遍就是冗余 ——
    一张卡只有三行的位置，冗余占一行，就少一行真正有用的信息。
    所以这里只留两句：活跃度（凭什么推它）+ 买不买得起（对你有没有用）。
    """
    bits = []
    ratio = p.get("act_ratio", 0.0)
    if rank == 0 and ratio > 0.75:
        bits.append("今天换手最活跃")
    elif ratio > 0.55:
        bits.append("成交比较活跃")
    elif ratio > 0.2:
        bits.append("今天有人交易")
    else:
        bits.append("今天成交清淡")
    if p.get("price", 0) <= 30:
        bits.append("%d 元可买" % int(math.ceil(p["price"])))
    if not bits:
        bits.append("波动温和，适合小额试试")
    return " · ".join(bits)


def features_day_start():
    """今天 0 点的时间戳（算当天成交额用）。"""
    lt = time.localtime(now())
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


# ---------------------------------------------------------------- 快捷买入
def plan_order(db, cfg, qq, cid, amount, leverage):
    """算一笔快捷买入的方案（不落库）。

    口径：amount = 投入的本金；名义 = 本金 × 杠杆；股数 = floor(名义 / 现价)。
    返回 dict：ok / msg / qty / price / cost / margin / lev / wave / risk ...
    """
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "没选中股票"}
    comp = db.q1("SELECT * FROM companies WHERE id=?", (cid,))
    if comp is None:
        return {"ok": False, "msg": "没有这只股票"}
    price = float(comp["price"] or 0)
    if price <= 0:
        return {"ok": False, "msg": "这只票现在没有价格，等一下再试"}

    lo = _cfgf(cfg, "quick_min_amount", 20.0)
    hi = _cfgf(cfg, "quick_max_amount", 500.0)
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "金额填错了"}
    if amount < lo:
        amount = lo
    if amount > hi:
        amount = hi

    acct = market.account(db, cfg, str(qq))
    lev_cap = int(cfg.get("max_leverage", 20)) + (tiers.lev_bonus(acct["equity"]) if acct else 0)
    try:
        leverage = int(leverage)
    except (TypeError, ValueError):
        leverage = 1
    # 快捷模式只给小杠杆：门槛低，风险也要低。
    # 想要高杠杆的人去手动下单，那边有 1~20 倍滑杆和风险提示。
    qmax = int(_cfgf(cfg, "quick_max_leverage", 3)) or 3
    # 【预览得和真下的一致】破产重开期只能 1 倍（v4.6）。
    # 预览说 3 倍、下单被拒绝就是接口不对齐 —— 这里直接按 1 倍算。
    if market.recovery.in_reopen(db, str(qq)):
        qmax = 1
    want_lev = max(1, min(qmax, min(lev_cap, leverage)))

    nominal = amount * want_lev
    qty = int(nominal / price)
    lev_note = ""
    if qty < 1:
        # 本金买不起 1 股：把杠杆提到刚好买得起 1 股的最低档。
        # 【封顶就是 qmax】界面上一口价写着「快捷模式只给 1~3 倍」，
        # 这里偷偷升到 8 倍就是骗人 —— 升不上去就如实说，剩下交给玩家自己选。
        need = int(math.ceil(price / amount)) if amount > 0 else 999
        min_amt = int(math.ceil(price / qmax)) if qmax > 0 else int(price)
        if need > qmax:
            return {"ok": False, "need": min_amt, "max_lev": qmax,
                    "msg": "「%s」现价 %.2f 元，%d 元本金在 %d 倍杠杆下也买不了 1 股"
                           "（至少要 %d 元）—— 换个金额档，或者换只便宜点的票。" % (
                               comp["name"], price, int(amount), qmax, min_amt)}
        want_lev = need
        nominal = amount * want_lev
        qty = int(nominal / price)
        if qty < 1:
            return {"ok": False, "need": min_amt, "max_lev": qmax,
                    "msg": "「%s」现价 %.2f 元，%d 元本金买不了 1 股。" % (
                        comp["name"], price, int(amount))}
        lev_note = "这票 %.2f 元，%d 元本金要 %d 倍杠杆才买得起 1 股，已自动升档" % (
            price, int(amount), want_lev)

    cost = qty * price
    margin = cost / want_lev
    amp = _amp(comp)
    drift = _cfgf(cfg, "market_daily_drift", 0.018)
    vol = amp if amp > 0 else drift
    vol = max(0.005, min(0.10, vol))
    try:
        pc = float(comp["prev_close"] or 0)
        chg = (price / pc - 1.0) if pc > 0 else 0.0
    except (TypeError, ValueError):
        chg = 0.0
    fee = cost * _cfgf(cfg, "fee_rate", 0.001)
    stars = _risk_stars(amp, chg)
    return {
        "ok": True, "max_lev": qmax, "cid": cid, "name": comp["name"], "ticker": comp["ticker"],
        "qty": qty, "price": round(price, 4), "cost": round(cost, 2),
        "margin": round(margin, 2), "fee": round(fee, 2),
        "lev": want_lev, "lev_note": lev_note, "amount": round(amount, 2),
        "wave": round(cost * vol, 2), "vol": round(vol * 100, 2),
        "risk": stars, "risk_text": _risk_text(stars), "chg": round(chg * 100, 2),
    }


def quick_buy(db, cfg, qq, cid, amount, leverage):
    """快捷买入：按金额下单，5 秒完成一次有波动有盈亏的操作。

    返回 (ok, msg, info)。
    """
    p = plan_order(db, cfg, qq, cid, amount, leverage)
    if not p.get("ok"):
        return False, p.get("msg", "下不了单"), None
    okk, msg, info = market.place_order(db, cfg, qq, cid, "long", p["qty"], p["lev"])
    if not okk:
        return False, msg, None
    note = ""
    if p.get("lev_note"):
        note = "（%s）" % p["lev_note"]
    small = bool(info.get("small")) if isinstance(info, dict) else False
    tail = ""
    if small:
        tail = "　试水仓：能赚能亏，但不算今日任务和交易成就。"
    return True, "%s%s%s" % (msg, note, tail), {
        "cid": cid, "qty": p["qty"], "price": p["price"], "cost": p["cost"],
        "margin": p["margin"], "lev": p["lev"], "wave": p["wave"],
        "risk": p["risk"], "risk_text": p["risk_text"], "small": small,
    }


# ---------------------------------------------------------------- 每日定投
def auto_get(db, qq):
    row = db.q1("SELECT * FROM auto_invest WHERE qq=?", (str(qq),))
    if row is None:
        return None
    return {
        "cid": int(row["cid"]), "amount": round(float(row["amount"]), 2),
        "leverage": int(row["leverage"]), "created_at": int(row["created_at"]),
        "last_run": int(row["last_run"]), "last_day": row["last_day"],
        "active": int(row["active"]),
    }


def auto_set(db, cfg, qq, cid, amount, leverage):
    """设 / 改每日定投。cid=0 表示「每天自动挑一只」。"""
    qq = str(qq)
    try:
        cid = int(cid or 0)
    except (TypeError, ValueError):
        return False, "股票选错了", None
    if cid:
        comp = db.q1("SELECT id, name FROM companies WHERE id=?", (cid,))
        if comp is None:
            return False, "没有这只股票", None
    lo = _cfgf(cfg, "quick_min_amount", 20.0)
    hi = _cfgf(cfg, "quick_max_amount", 500.0)
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return False, "金额填错了", None
    if amount < lo:
        return False, "最少 %d 元" % int(lo), None
    if amount > hi:
        return False, "最多 %d 元" % int(hi), None
    qmax = int(_cfgf(cfg, "quick_max_leverage", 3)) or 3
    try:
        leverage = int(leverage or 1)
    except (TypeError, ValueError):
        leverage = 1
    leverage = max(1, min(qmax, leverage))
    db.ex("INSERT INTO auto_invest(qq,cid,amount,leverage,created_at,last_run,last_day,active) "
          "VALUES(?,?,?,?,?,0,'',1) ON CONFLICT(qq) DO UPDATE SET "
          "cid=excluded.cid, amount=excluded.amount, leverage=excluded.leverage, active=1",
          (qq, cid, amount, leverage, now()))
    st = auto_get(db, qq)
    if cid:
        comp = db.q1("SELECT name FROM companies WHERE id=?", (cid,))
        what = comp["name"] if comp else "这只票"
    else:
        what = "每天自动挑一只"
    return True, "定投已设好：每天 %d 元 · %d 倍 · %s" % (int(amount), leverage, what), st


def auto_clear(db, qq):
    """关掉定投。"""
    db.ex("DELETE FROM auto_invest WHERE qq=?", (str(qq),))
    return True, "每日定投已关掉", None


def auto_run(db, cfg, qq, force=False):
    """执行一次定投。同一天只跑一次。"""
    qq = str(qq)
    st = auto_get(db, qq)
    if not st or not st.get("active"):
        return False, "没设定投", None
    today = time.strftime("%Y-%m-%d")
    if st.get("last_day") == today and not force:
        return False, "今天已经投过了", None
    cid = st["cid"]
    if not cid:
        picks = pick_stocks(db, cfg, gid="auto", n=1, force=True)
        items = picks.get("items") or []
        if not items:
            db.ex("UPDATE auto_invest SET last_day=?, last_run=? WHERE qq=?",
                  (today, now(), qq))
            return False, "今天没有能买的票，定投先跳过", None
        cid = items[0]["cid"]
    okk, msg, info = quick_buy(db, cfg, qq, cid, st["amount"], st["leverage"])
    db.ex("UPDATE auto_invest SET last_day=?, last_run=?, cid=? WHERE qq=?",
          (today, now(), cid if okk else st["cid"], qq))
    if not okk:
        return False, msg, None
    comp = db.q1("SELECT name FROM companies WHERE id=?", (cid,))
    nm = comp["name"] if comp else "标的"
    text = "🔁 每日定投：%d 元买入 %s，%s" % (int(st["amount"]), nm, msg)
    try:
        market.add_notice(db, qq, "info", text)
    except Exception:
        pass
    return True, text, info


def auto_run_all(db, cfg, limit=200):
    """给调度器用：把这一群开了定投的人都跑一遍。"""
    rows = db.q("SELECT qq FROM auto_invest WHERE active=1")
    done = 0
    for r in rows:
        try:
            okk, _msg, _info = auto_run(db, cfg, r["qq"])
            if okk:
                done += 1
        except Exception:
            continue
        if done >= limit:
            break
    return done

"""交易系统：开平仓 / 加杠杆 / 强平 / 贷款 / 破产打工。"""
import json
import random
import time

import coupons
import recovery
import tiers
from db import now


def _circuit(db):
    """当前熔断状态。返回 (until_ts, reason)；没休市返回 (0, "")。"""
    try:
        until = int(db.get_meta("circuit_until", "0") or 0)
    except (TypeError, ValueError):
        until = 0
    if until <= now():
        return 0, ""
    try:
        reason = db.get_meta("circuit_reason", "") or "市场异动"
    except Exception:
        reason = "市场异动"
    return until, reason


def px(v):
    """价格展示精度：低价股多给几位小数。

    内部价格已经到 4 位精度（engine.price_precision），如果展示还只给 2 位，
    4 元档的股票在 K 线上就是一条直线 —— 数字后缀再标两位就够用了。
    """
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    a = abs(v)
    if a < 1:
        return round(v, 4)
    if a < 10:
        return round(v, 3)
    return round(v, 2)

def day_start(now_ts=None):
    """今天 0 点的时间戳（本地时区）。"""
    t = time.localtime(now_ts if now_ts is not None else now())
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


# ---------------------------------------------------------------- 用户
def ensure_user(db, qq, nick="", initial_cash=0.0):
    qq = str(qq)
    row = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if row is None:
        db.ex("INSERT INTO users(qq,nick,cash,loan,created_at) VALUES(?,?,?,?,?)",
              (qq, nick, float(initial_cash), 0.0, now()))
        row = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    elif nick and row["nick"] != nick:
        db.ex("UPDATE users SET nick=? WHERE qq=?", (nick, qq))
    return row


def sync_users(db, cfg, members):
    """给群成员开户（首次开户送固定初始资金）。"""
    for qq, nick in members:
        ensure_user(db, qq, nick, cfg["initial_cash"])


def _positions(db, qq, status="open"):
    """取持仓。

    用 LEFT JOIN：公司行万一没了（退群被清理、公司被误删），
    持仓也要照样算进净值，否则用户的保证金会凭空消失、净值算低甚至被判破产。
    退市的持仓按成本价计浮动盈亏（等于 0）。
    """
    return db.q("SELECT p.*, COALESCE(c.price, p.entry) AS price, "
                "COALESCE(c.name, '已退市') AS name, "
                "COALESCE(c.ticker, '') AS ticker, COALESCE(c.industry, '') AS industry "
                "FROM positions p LEFT JOIN companies c ON c.id=p.cid "
                "WHERE p.qq=? AND p.status=?", (str(qq), status))


def account(db, cfg, qq):
    """账户总览：现金 / 贷款 / 持仓市值 / 浮盈 / 净值。"""
    user = db.q1("SELECT * FROM users WHERE qq=?", (str(qq),))
    if user is None:
        return None
    # 空值兜底：cash/loan 任何一项是 NULL，round() 都会直接抛 TypeError，
    # 整个账户接口 500，前端整页白屏。这里统一按 0 处理。
    cash = float(user["cash"] or 0)
    loan = float(user["loan"] or 0)
    margin = 0.0
    unreal = 0.0
    pos_list = []
    for p in _positions(db, qq):
        sign = 1 if p["side"] == "long" else -1
        pl = (p["price"] - p["entry"]) * p["qty"] * sign
        margin += p["margin"]
        unreal += pl
        pos_list.append({
            "id": p["id"], "cid": p["cid"], "name": p["name"], "ticker": p["ticker"],
            "industry": p["industry"], "side": p["side"], "qty": p["qty"],
            "entry": px(p["entry"]), "price": px(p["price"]),
            "leverage": p["leverage"], "margin": round(p["margin"], 2),
            "pl": round(pl, 2), "pl_pct": round(pl / p["margin"] * 100, 2) if p["margin"] else 0.0,
            "opened_at": p["opened_at"],
        })
    # 净值 = 现金 + 保证金 + 浮动盈亏 - 欠款
    equity = cash + margin + unreal - loan
    return {
        "qq": user["qq"], "nick": user["nick"], "cash": round(cash, 2), "loan": round(loan, 2),
        "margin": round(margin, 2), "unreal": round(unreal, 2), "equity": round(equity, 2),
        "bankrupt": user["bankrupt"], "jobs": user["jobs"], "last_job": user["last_job"],
        "win": float(user["win"] or 0), "lose": float(user["lose"] or 0), "positions": pos_list,
        "total_pl": round(unreal + float(user["win"] or 0) - float(user["lose"] or 0), 2),
    }


def _ledger(db, qq, delta, reason):
    bal = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))["cash"]
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (str(qq), now(), round(delta, 2), round(bal, 2), reason))


# ---------------------------------------------------------------- 站内通知
NOTICE_KIND_LIQ = "liquidation"
NOTICE_KIND_BANKRUPT = "bankrupt"
NOTICE_KIND_RISK = "risk"
WARN_COOLDOWN = 900           # 风险预警冷却（秒）：15 分钟内不重复喊


def add_notice(db, qq, kind, text):
    """写一条站内通知。网页横幅和 QQ 私信共用同一份文案。"""
    if not text:
        return ""
    try:
        db.ex("INSERT INTO notices(qq,kind,text,ts) VALUES(?,?,?,?)",
              (str(qq), kind, text, now()))
    except Exception:
        return ""
    return text


def recent_notices(db, qq, limit=6):
    """最近几条站内通知（新的在前）。"""
    rows = db.q("SELECT id,kind,text,ts FROM notices WHERE qq=? ORDER BY id DESC LIMIT ?",
                (str(qq), int(limit)))
    return [{"id": r["id"], "kind": r["kind"], "text": r["text"], "ts": r["ts"]}
            for r in rows]


def _risk_notice(db, cfg, qq, acct, own_margin):
    """净值逼近维持线时先喊一嗓子（15 分钟冷却）。

    【为什么要预警】以前这条路上只有「已经强平」这一个动作：玩家来不及减仓，
    仓位被一次性清空，还没人告诉他为什么。预警给的是「自己动手」的机会。

    【v4.11 修正口径】原来拿「净值 / 自有保证金 ≤ 1.35」当触发条件。
    但 20 倍满仓开仓那一刻，这个比值就只有 1.09（手续费先吃掉一点），
    等于一开仓就喊「快被强平」，天天误报，玩家直接把它当噪音。
    现在改成拿**真正会强平的那两条线**当基准：
      1) 维持保证金强平线 = 自有保证金 × 维持比例（净值跌破它就全部平掉）
      2) 逐笔止损线 = 该笔浮亏 ≥ 该笔保证金（单笔被平掉）
    两边都算一遍「离被强平还差多远」，真的快到了才喊。
    """
    equity = float(acct["equity"])
    liq_line = float(own_margin) * float(cfg.get("maintenance_rate", 0.5))
    near_liq = liq_line > 0 and equity <= liq_line * 1.6
    near_stop = False
    stop_txt = ""
    for p in _positions(db, qq):
        margin = float(p["margin"] or 0)
        if margin <= 0:
            continue
        sign = 1 if p["side"] == "long" else -1
        pl = (p["price"] - p["entry"]) * p["qty"] * sign
        ratio = pl / margin          # 逐笔止损线在 -1
        if ratio <= -0.7:
            near_stop = True
            stop_txt = "「%s」浮亏已达该笔止损线的 %.0f%%" % (p["name"], -ratio * 100)
    if not (near_liq or near_stop):
        return ""
    row = db.q1("SELECT ts FROM notices WHERE qq=? AND kind='risk' ORDER BY id DESC LIMIT 1",
                (str(qq),))
    now_ts = int(time.time())
    if row and now_ts - int(row["ts"] or 0) < WARN_COOLDOWN:
        return ""
    bits = []
    if near_liq:
        bits.append("净值 %.0f 元，维持保证金强平线 %.0f 元（自有保证金 %.0f 元 × %.2f），"
                    "再亏 %.0f 元就会被一次性全部平掉"
                    % (equity, liq_line, float(own_margin),
                       float(cfg.get("maintenance_rate", 0.5)), max(0.0, equity - liq_line)))
    if near_stop:
        bits.append(stop_txt)
    text = ("⚠️ 风险预警：仓位太重了 —— %s。"
            "建议先减仓或平掉一部分，别等被强制平仓。" % "；".join(bits))
    return add_notice(db, qq, NOTICE_KIND_RISK, text)


# ---------------------------------------------------------------- 下单
def place_order(db, cfg, qq, cid, side, qty, leverage, price=None):
    """开仓：做多 / 做空，支持杠杆。返回 (ok, msg, info)。"""
    qq = str(qq)
    user = ensure_user(db, qq, "", cfg["initial_cash"])
    if user["bankrupt"]:
        # 【自愈】净值已经回正的直接放行 —— 别把人卡在「有钱不能交易」
        if recovery.sync_bankrupt(db, cfg, qq):
            user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
        else:
            return False, ("你已破产，暂时不能交易。去别人公司打工攒本钱，"
                           "破产期间打工上限翻倍、签到翻倍，攒够还能申请债务重整。"), None
    # v4.1：大盘级黑天鹅会触发临时休市（熔断）。休市期间只允许平仓，
    # 不允许开新仓 —— 跟真实市场一个脾气，也给群友一个共同话题。
    until, reason = _circuit(db)
    if until:
        left = max(1, (until - now() + 59) // 60)
        return False, "市场临时休市中（%s），约 %d 分钟后恢复开盘，先看看热闹" % (reason, left), None
    comp = db.q1("SELECT * FROM companies WHERE id=?", (cid,))
    if comp is None:
        return False, "没有这只股票", None
    if side not in ("long", "short"):
        return False, "方向只能是多或空", None
    try:
        qty = float(qty)
        leverage = int(leverage)
    except (TypeError, ValueError):
        return False, "数量或杠杆不合法", None
    if qty <= 0:
        return False, "数量要大于 0", None
    # 【v4.7】净值分层的特权：档位越高，杠杆上限越高。
    # 不是白送 —— 高档位每天自动交会员费（tycoon 模块收），钱必须持续流动。
    acct = account(db, cfg, qq)
    lev_cap = int(cfg["max_leverage"]) + (tiers.lev_bonus(acct["equity"]) if acct else 0)
    want_leverage = leverage
    leverage = max(cfg["min_leverage"], min(lev_cap, leverage))
    # 【v4.6】破产清盘后的「重开期」：杠杆最多 1 倍。
    # 清盘刚把一个走死的人拉回来，别让他转头就借一屁股债梭哈回去。
    reopen_note = ""
    if recovery.in_reopen(db, qq):
        if want_leverage > 1:
            reopen_note = "（重开期只能 1 倍杠杆，已自动降档）"
        leverage = 1

    price = float(price) if price else float(comp["price"])
    cost = qty * price
    margin = cost / leverage
    # 【v4.7】高档位手续费折扣（特权之一）
    disc = tiers.fee_discount(acct["equity"]) if acct else 0.0
    fee_full = cost * cfg["fee_rate"] * (1.0 - disc)
    # 先按「不打折」的钱判断够不够：不够就直接失败，
    # 免手续费券要等到真能成交时才扣 —— 之前是「先扣券再判余额」，
    # 下单失败（钱不够 / 熔断 / 票据过期）券就没了，玩家白丢一次券。
    if user["cash"] < margin + fee_full:
        need = margin + fee_full - user["cash"]
        return False, "资金不足，还差 %.2f（可去「贷款」借点）" % need, None
    # 【P0 漏洞修复】总保证金敞口必须受净值约束，不能只看现金。
    # 老逻辑只校验 cash：借来的钱进现金池后就能 100% 当保证金，
    # 于是「净值 1 万块的人」可以开出 7.1 亿名义仓位（2026-11-18 实锤）。
    # 现在按净值给一个总保证金上限：total_margin <= equity × max_margin_ratio。
    if acct:
        ratio = float(cfg.get("max_margin_ratio", 3.0))
        cap = max(0.0, acct["equity"]) * ratio
        if acct["margin"] + margin > cap:
            return False, ("总仓位太重了：净值 %.2f 最多压 %.2f 保证金（%.1f 倍上限），"
                           "你现在已经压了 %.2f，这笔还要 %.2f。"
                           "先平掉一部分仓位，或者等行情走一波。" % (
                               acct["equity"], cap, ratio, acct["margin"], margin)), None
    fee = 0.0 if coupons.use(db, qq, "fee_free", 1) else fee_full
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (margin + fee, qq))
    db.ex("INSERT INTO positions(qq,cid,side,qty,entry,leverage,margin,opened_at) VALUES(?,?,?,?,?,?,?,?)",
          (qq, cid, side, qty, price, leverage, margin, now()))
    db.ex("INSERT INTO trades(qq,cid,side,kind,qty,price,leverage,fee,pl,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
          (qq, cid, side, "open", qty, price, leverage, fee, 0.0, now()))
    _ledger(db, qq, -(margin + fee), "开仓 %s %s x%d" % ("买入" if side == "long" else "卖出",
                                                     comp["name"], leverage))
    return True, "已开仓：%s %s %d 股 @%.2f（杠杆 %dx，占用保证金 %.2f）%s" % (
        "买入" if side == "long" else "卖出", comp["name"], qty, price, leverage, margin,
        reopen_note), {
        "margin": round(margin, 2), "fee": round(fee, 2)}


def close_position(db, cfg, qq, pos_id, force=False, qty=None):
    """平仓。force=True 为强平；qty 指定数量时只平一部分。"""
    qq = str(qq)
    p = db.q1("SELECT * FROM positions WHERE id=? AND qq=? AND status='open'", (pos_id, qq))
    if p is None:
        return False, "没有这笔持仓", None
    comp = db.q1("SELECT * FROM companies WHERE id=?", (p["cid"],))
    if comp is None:
        # 公司行没了（退群/误删）——不能崩，按退市处理
        return False, "这只股票已经退市了（公司不在市场里），暂时没法平仓，请联系管理员处理", None
    price = float(comp["price"])
    total = float(p["qty"])
    qty = total if qty is None else float(qty)
    if qty <= 0 or qty > total:
        qty = total
    part = qty / total if total else 1.0
    sign = 1 if p["side"] == "long" else -1
    pl = (price - p["entry"]) * qty * sign
    # 强平手续费 2 倍（原来 3 倍）：被强平已经够肉痛了，
    # 再乘 3 会出现「借点钱炒股 → 平仓倒亏手续费」的体感。
    # 【v4.7】高档位手续费折扣（开仓 / 平仓都算）
    _acct = account(db, cfg, qq)
    disc = tiers.fee_discount(_acct["equity"]) if _acct else 0.0
    fee = qty * price * cfg["fee_rate"] * (1.0 - disc) * (2 if force else 1)
    if not force and coupons.use(db, qq, "fee_free", 1):
        fee = 0.0
    margin_out = p["margin"] * part
    # 【v4.11】强平手续费不得把现金扣成负数。
    # 逐笔止损那条线是「这笔的亏损正好等于它自己的保证金」，此时
    # margin_out + pl 已经被亏成 0，再扣一次强平手续费（2 倍）就会把现金打成负数
    # → 净值变负 → 平仓即破产（「被强平后没持有」的最后一根稻草）。
    # 现在：手续费最多吃掉「保证金 + 盈亏」剩下的部分，保证现金不为负。
    if force and margin_out + pl - fee < 0:
        fee = max(0.0, margin_out + pl)
    cash_delta = margin_out + pl - fee
    db.ex("UPDATE users SET cash=cash+?, win=win+?, lose=lose+? WHERE qq=?",
          (cash_delta, max(pl, 0.0), max(-pl, 0.0), qq))
    if qty >= total - 1e-9:
        db.ex("UPDATE positions SET status='closed' WHERE id=?", (pos_id,))
        # 这个方向上再没有仓位了 → 挂在它身上的止损/止盈一起作废（OCO）
        still = db.q1("SELECT COUNT(*) c FROM positions "
                      "WHERE qq=? AND cid=? AND side=? AND status='open'",
                      (qq, p["cid"], p["side"]))
        if not still or int(still["c"]) <= 0:
            import orders
            orders.cancel_protection(db, qq, p["cid"], p["side"])
    else:
        db.ex("UPDATE positions SET qty=?, margin=? WHERE id=?",
              (total - qty, p["margin"] - margin_out, pos_id))
    db.ex("INSERT INTO trades(qq,cid,side,kind,qty,price,leverage,fee,pl,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
          (qq, p["cid"], p["side"], "force_close" if force else "close", qty, price,
           p["leverage"], fee, pl, now()))
    _ledger(db, qq, cash_delta, ("强制平仓 " if force else "平仓 ") + comp["name"])
    tag = "强平" if force else "平仓"
    left = "" if qty >= total - 1e-9 else "（剩余 %.0f 股）" % (total - qty)
    return True, "%s%s%s，盈亏 %.2f（含手续费 %.2f）" % (tag, comp["name"], left, pl - fee, fee), {
        "pl": round(pl - fee, 2)}


# ---------------------------------------------------------------- 强平 / 利息
def check_liquidation(db, cfg, qq):
    """净值跌破维持线就强平；净值转负则破产。返回提示文案（没有则 None）。"""
    acct = account(db, cfg, qq)
    if acct is None:
        return None
    user = db.q1("SELECT * FROM users WHERE qq=?", (str(qq),))
    msgs = []
    # 1) 逐笔止损：单笔亏损超过自己的保证金就强平这一笔
    for p in _positions(db, qq):
        sign = 1 if p["side"] == "long" else -1
        pl = (p["price"] - p["entry"]) * p["qty"] * sign
        if p["margin"] > 0 and pl <= -p["margin"]:
            ok, msg, _ = close_position(db, cfg, qq, p["id"], force=True)
            if ok:
                msgs.append(msg)
    acct = account(db, cfg, qq)  # 逐笔止损可能已经平掉仓位，净值要重新算
    loan = float(user["loan"] or 0)
    # 【2) 维持保证金：只算自己倒的那部分。
    # 【为什么要减掉贷款】贷款先到账上，再拿它当保证金开仓，
    # 此时保证金里有一大块是借来的，而净值其实没变。老逻辑往往拿总保证金一比
    # ，结果开仓瞬间就触发强平（净亏才手续费）。
    # 【P1 漏洞修复】借款不能把「自有保证金」洗成 0。
    # 老口径 own_margin = margin - loan：一旦借的钱 >= 保证金，
    # own_margin 就是 0，`if own_margin > 0` 不成立 → 整条维持保证金规则
    # 直接失效，等于「借满仓 = 免强平」，只有等净值跌破 0 才会破产清算。
    # 现在给一个下限：至少把总保证金的 loan_shield_min 计入自有保证金。
    shield = float(cfg.get("loan_shield_min", 0.3))
    own_margin = max(acct["margin"] * shield, acct["margin"] - loan)
    # 【v4.11 风险预警】净值逼近维持线时先提醒一次（15 分钟冷却）。
    # 以前这条路上只有「已经强平」，玩家只能看着仓位凭空消失。
    if acct["margin"] > 0 and acct["equity"] > 0:
        _risk_notice(db, cfg, qq, acct, own_margin)
    if acct["margin"] > 0 and acct["equity"] <= own_margin * cfg["maintenance_rate"]:
        for p in _positions(db, qq):
            ok, msg, _ = close_position(db, cfg, qq, p["id"], force=True)
            if ok:
                msgs.append(msg)
        acct = account(db, cfg, qq)
    # 【3) 融资担保比例：现金 + 持仓市值 低于欠款 × 维持比例 → 融资爆仓。
    # 这一条是借款玩家的安全网：借来的钱当保证金时，持仓下跌到担保比例
    # 不足就强平，例外的跌幅还能接住（不会一下子直接破产）。
    if loan > 0:
        # 【担保物口径修正】原来 collateral = 现金 + Σ(数量 × 价格)，
        # 把「空头名义金额」也算成了担保物 —— 空头名义越大担保物越多，
        # 借钱做空几乎永远不会被强平（空头本来是负债，不是资产）。
        # 现在按真实口径算：
        #   现金 + 多头市值 + 空头锁定的保证金 − 空头浮亏
        # 多头路径和原来一致（保证金额 × 杠杆的票仍然扛得住），
        # 空头路径改成「涨上去就吃掉担保物」，跌了反而安全。
        long_value = 0.0
        short_margin = 0.0
        short_loss = 0.0
        for p in _positions(db, qq):
            qty = float(p["qty"])
            px = float(p["price"])
            if p["side"] == "long":
                long_value += qty * px
            else:
                short_margin += float(p["margin"] or 0)
                short_loss += max(0.0, (px - float(p["entry"])) * qty)
        collateral = acct["cash"] + long_value + short_margin - short_loss
        ratio = float(cfg.get("loan_maintenance_ratio", 1.2))
        if collateral < loan * ratio:
            for p in _positions(db, qq):
                ok, msg, _ = close_position(db, cfg, qq, p["id"], force=True)
                if ok:
                    msgs.append(msg)
            acct = account(db, cfg, qq)
    if acct["equity"] < 0 and not user["bankrupt"]:
        # 纾困流程已经开着就别重新宣告：否则每次 tick 都会把进度清零，
        # 还会重复减免债务，玩家永远攒不到司法重整。
        was_relief = recovery.in_relief(db, str(qq))
        db.ex("UPDATE users SET bankrupt=1 WHERE qq=?", (str(qq),))
        if was_relief:
            if msgs:
                text = "触发强制平仓：" + "；".join(msgs)
                add_notice(db, qq, NOTICE_KIND_LIQ, text)
                return text
            return None
        cut_msg = recovery.on_bankrupt(db, cfg, str(qq))
        acct = account(db, cfg, qq)   # 减免之后净值可能当场回正
        head = ("你被强制平仓并宣告破产：" + "；".join(msgs)) if msgs else "你的净值已经为负，宣告破产。"
        add_notice(db, qq, NOTICE_KIND_BANKRUPT, head)
        if acct["equity"] >= 0:
            recovery.sync_bankrupt(db, cfg, str(qq), acct["equity"])
            return head + "\n清算之后你的净值回正到 %.2f，已自动恢复交易资格。" % acct["equity"]
        if cut_msg:
            return (head + "\n" + cut_msg +
                    "\n之后靠打工翻身：破产期间打工上限翻倍、签到翻倍、贷款免息，"
                    "凑够钱还能申请债务重整。")
        return head + " 去别人公司打工吧。"
    # 【自愈】净值回正就解除破产标记。不然会出现「净值 15 万、欠款 0，
    # 顶上却挂着『你已破产，暂时不能交易』」的红条（v4.4 群友反馈）。
    if user["bankrupt"] and acct["equity"] >= 0:
        if recovery.sync_bankrupt(db, cfg, str(qq), acct["equity"]):
            return "你的净值已经回正到 %.2f，破产状态解除，可以重新交易了！" % acct["equity"]
    if msgs:
        text = "触发强制平仓：" + "；".join(msgs)
        add_notice(db, qq, NOTICE_KIND_LIQ, text)
        return text
    return None


def accrue_interest(db, cfg, qq):
    """按日息计提贷款利息（每个 tick 调一次）。"""
    user = db.q1("SELECT * FROM users WHERE qq=?", (str(qq),))
    if not user or user["loan"] <= 0:
        return
    if recovery.in_relief_row(user):
        # 纾困期间债务冻结，不计息（要还的也是翻身之后的事了）
        return
    if coupons.loan_waived(db, qq):
        return
    rate = cfg["loan_rate_per_day"]
    days = cfg["tick_seconds"] / 86400.0
    interest = user["loan"] * rate * days
    if interest <= 0:
        return
    acc = (user["interest_acc"] or 0.0) + interest
    # 【P2 漏洞修复】利息只计入负债，不能同时扣现金。
    # 老口径 loan+i 且 cash-i，而净值 = cash + margin + unreal - loan，
    # 一次利息把净值扣了两倍 —— 债主白拿 2 倍利息。
    db.ex("UPDATE users SET loan=loan+?, interest_acc=? WHERE qq=?",
          (interest, acc, str(qq)))
    # 攒够 1 块再记一笔流水，免得每 2 秒刷一条 0.00
    if acc >= 1.0:
        _ledger(db, qq, 0.0, "贷款利息累计 %.2f（计入负债）" % acc)
        db.ex("UPDATE users SET interest_acc=0 WHERE qq=?", (str(qq),))


# ---------------------------------------------------------------- 贷款
def borrow(db, cfg, qq, amount):
    qq = str(qq)
    user = ensure_user(db, qq, "", cfg["initial_cash"])
    if user["bankrupt"]:
        if not recovery.sync_bankrupt(db, cfg, qq):
            return False, "破产状态下不能贷款，先去打工翻身", None
        user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    # 【v4.6】重开期不许借钱 —— 刚被交易所救回来，别转头又借一屁股债梭哈。
    if recovery.in_reopen(db, qq):
        return False, ("重开期（还剩 %d 天）不能贷款 —— 先用自己的本金把窟窿填上"
                       % recovery.reopen_days_left(db, qq)), None
    acct = account(db, cfg, qq)
    if acct["equity"] <= 0:
        return False, "净值已经是负数了，先去打工翻身再来借钱", None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return False, "金额不合法", None
    if amount <= 0:
        return False, "金额要大于 0", None
    # 【P0 漏洞修复】贷款额度必须按「总负债」累计，不能按单笔算。
    # 老口径 limit = min(max_loan_abs, equity x max_loan_ratio) 里，
    # equity 已经扣掉了 loan —— 而借钱本身不改变 equity（cash +a、loan +a，
    # 净值纹丝不动），所以这个额度永远不下降，玩家可以反复借到 max_loan_abs。
    # 2026-11-18 实锤：Anyisdragon 一口气借了 90 笔、合计 3530 万
    # （其中 65 笔 50 万在 30 秒内借完），3250 万至今未还；
    # 借来的钱全拿去当保证金，开出了 7.1 亿名义的仓位。
    # 现在改成：总负债 <= 净值 x max_loan_ratio，单笔 <= max_loan_abs。
    debt_cap = max(0.0, acct["equity"]) * cfg["max_loan_ratio"]
    room = debt_cap - acct["loan"]
    if room <= 0:
        return False, ("负债已经到顶了（总负债上限 = 净值 %.2f × %s = %.2f，"
                       "你现在欠 %.2f）" % (acct["equity"], cfg["max_loan_ratio"],
                                       debt_cap, acct["loan"])), None
    limit = min(cfg["max_loan_abs"], room)
    if amount > limit:
        return False, ("最多能借 %.2f（总负债上限 = 净值 %.2f × %s = %.2f，"
                       "已欠 %.2f；单笔上限 %.0f）" % (
                           limit, acct["equity"], cfg["max_loan_ratio"], debt_cap,
                           acct["loan"], cfg["max_loan_abs"])), None
    db.ex("UPDATE users SET cash=cash+?, loan=loan+? WHERE qq=?", (amount, amount, qq))
    db.ex("INSERT INTO loans(qq,amount,repaid,rate,ts,status) VALUES(?,?,?,?,?,'open')",
          (qq, amount, 0.0, cfg["loan_rate_per_day"], now()))
    _ledger(db, qq, amount, "贷款")
    return True, "成功放款 %.2f，当前欠款 %.2f（日息 %.3f%%）" % (
        amount, acct["loan"] + amount, cfg["loan_rate_per_day"] * 100), {"loan": round(acct["loan"] + amount, 2)}


def repay(db, cfg, qq, amount):
    qq = str(qq)
    user = ensure_user(db, qq)
    acct = account(db, cfg, qq)
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return False, "金额不合法", None
    if amount <= 0:
        return False, "金额要大于 0", None
    if amount > acct["cash"]:
        return False, "现金不够，账上只有 %.2f" % acct["cash"], None
    pay = min(amount, acct["loan"])
    if pay <= 0:
        return False, "你现在没有欠款", None
    db.ex("UPDATE users SET cash=cash-?, loan=MAX(0, loan-?) WHERE qq=?", (pay, pay, qq))
    rows = db.q("SELECT * FROM loans WHERE qq=? AND status='open' ORDER BY ts", (qq,))
    left = pay
    for r in rows:
        if left <= 0:
            break
        take = min(left, r["amount"] - r["repaid"])
        db.ex("UPDATE loans SET repaid=repaid+?, status=CASE WHEN repaid+?>=amount THEN 'done' ELSE 'open' END WHERE id=?",
              (take, take, r["id"]))
        left -= take
    _ledger(db, qq, -pay, "还款")
    left = acct["loan"] - pay
    new_acct = account(db, cfg, qq)
    if user["bankrupt"] and new_acct["equity"] >= 0:
        if recovery.sync_bankrupt(db, cfg, qq, new_acct["equity"]):
            return True, "还款 %.2f，剩余欠款 %.2f。你的净值已经回正，重获交易资格！" % (pay, left), {"loan": round(left, 2)}
    return True, "还款 %.2f，剩余欠款 %.2f" % (pay, left), {"loan": round(left, 2)}


# ---------------------------------------------------------------- 打工
JOB_LINES = [
    "你在 {boss} 的{ind}车间拧了一整天螺丝，领到 {wage} 元工资。",
    "你被 {boss} 拉去{ind}仓库搬货，累到虚脱，拿到 {wage} 元。",
    "你在 {boss} 公司加班做表格，PPT 改到第 {k} 版，拿到 {wage} 元加班费。",
    "你替 {boss} 去谈客户，被甲方放了 3 个小时鸽子，最后拿到 {wage} 元。",
    "你在 {boss} 的{ind}店当一天临时工，被客人使唤，挣了 {wage} 元。",
    "你帮 {boss} 直播带货喊了 6 小时麦，嗓子哑了，拿到 {wage} 元坑位费。",
]

# 打工职级：干得越多，工龄越长，价钱越高，但老板也开始防着你摸鱼（冷却变长）。
# 目的很明确 —— 打工只能当应急兜底，不能当主收入，不然玩家只会打工不炒股。
JOB_LEVELS = [
    (0, "学徒", 1.0, 600),
    (5, "熟手", 1.2, 900),
    (20, "老师傅", 1.5, 1200),
    (50, "车间主任", 1.8, 1500),
]

JOB_EVENTS = [
    (0.22, "chicken", "老板看你今天卖力，临走给你塞了个鸡腿，多给了 {extra} 元。"),
    (0.12, "ghost", "你今天被安排去给老板送外卖，结果被客户鸽了，一分钱没拿到。"),
]


def job_level(jobs):
    """按打工次数算职级。"""
    lv = JOB_LEVELS[0]
    for item in JOB_LEVELS:
        if int(jobs or 0) >= item[0]:
            lv = item
    return lv


def job_today_income(db, qq):
    """今天打工已经挣了多少（用于每日上限）。"""
    row = db.q1("SELECT SUM(delta) s FROM ledger WHERE qq=? AND ts>=? AND reason LIKE '打工%'",
                (str(qq), day_start()))
    return float(row["s"] or 0.0) if row else 0.0


def job_cap_by_tier(cfg, equity):
    """打工日上限，按净值档位拉开。

    【为什么要拉开】之前日上限是死的 160 元，谁都一样，
    而档位特权只作用在「单次工资倍数」（最多 ×1.5）——
    结果低档位轻松吃满上限、高档位被硬砍平，
    打工反而成了中下层的最优解（无风险 + 稳定 + 次数多），
    炒股变成了副业，群里的模拟器最后变成打工模拟器。
    拉开之后：低档位几乎没得赚（想赚钱只能去炒股），
    高档位上限高一些、忙起来有正反馈，
    但相对他们的净值依然微不足道（财阀 1000 元 / 1500 万净值 = 0.006%）。
    """
    caps = cfg.get("job_cap_by_tier") or []
    try:
        idx = tiers.tier_index(equity)
    except Exception:
        idx = 0
    if 0 <= idx < len(caps):
        return float(caps[idx])
    return float(cfg.get("job_daily_cap", 160.0))


def job_status(db, cfg, qq):
    """打工面板状态：今天挣了多少、这一档的上限、还剩多少、职级与冷却。

    【为什么要单独一个接口】v4.9 把打工日上限按档位拉开了，
    但网页端还写着老文案「每天封顶 2000 元」，玩家看到的和实际算的对不上。
    这里把真实上限直接算给前端，别再硬编码。
    """
    qq = str(qq)
    acct = account(db, cfg, qq)
    eq = float(acct["equity"]) if acct else 0.0
    is_bankrupt = recovery.in_relief(db, qq)
    tier_cap = job_cap_by_tier(cfg, eq)
    cap = float(cfg.get("bankrupt_job_cap", 1200.0)) if is_bankrupt else tier_cap
    earned = job_today_income(db, qq)
    lv_name, mult, cooldown = job_level(acct["jobs"] if acct else 0)[1:]
    if is_bankrupt:
        cooldown = int(cooldown * 0.6)
    last = int(acct["last_job"] or 0) if acct else 0
    wait = 0
    if last and now() - last < cooldown:
        wait = cooldown - (now() - last)
    return {
        "earned": round(earned, 2),
        "cap": round(cap, 2),
        "left": round(max(0.0, cap - earned), 2),
        "tier": tiers.tier_name(eq),
        "tier_cap": round(tier_cap, 2),
        "bankrupt": bool(is_bankrupt),
        "level": lv_name,
        "cooldown": int(cooldown),
        "wait": int(wait),
        "ready": wait <= 0,
        "full": earned >= cap,
    }


def peek_news(db, cfg, qq):
    """花钱买「内幕消息」：提前知道下一条快讯的标的和方向。"""
    qq = str(qq)
    user = ensure_user(db, qq, "", cfg["initial_cash"])
    cost = cfg.get("peek_cost", 800)
    if user["cash"] < cost:
        return False, "钱包不够买内幕了（需要 %.0f 元）" % cost, None
    hint = db.get_meta("pending_news", "")
    if not hint:
        return False, "风平浪静，交易所门口连只苍蝇都没有，没有内幕可买", None
    pend = json.loads(hint)
    if pend.get("peeked"):
        return False, "这条内幕你已经买过了，等下一条吧", None
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (cost, qq))
    _ledger(db, qq, -cost, "购买内幕消息")
    if pend.get("scope") == "market":
        target = "大盘整体"
    elif pend.get("scope") == "industry":
        target = pend.get("ind", "?") + " 板块"
    else:
        row = db.q1("SELECT name FROM companies WHERE id=?", (pend.get("cid"),))
        target = row["name"] if row else "某家公司"
    # 坊间传闻只有五成准：不然高杠杆 + 准确信息 = 无限印钞
    true_dir = 1 if pend.get("impact", 0) >= 0 else -1
    rumor_dir = pend.get("rumor_dir")
    if rumor_dir is None:
        rumor_dir = true_dir if random.random() < 0.5 else -true_dir
    direction = "偏涨" if rumor_dir > 0 else "偏跌"
    pend["rumor_dir"] = rumor_dir
    db.set_meta("pending_news", json.dumps(dict(pend, peeked=1), ensure_ascii=False))
    return True, "你花 %.0f 元买到一条坊间传闻：「%s」%s（准确率约五成，别全信）" % (cost, target, direction), {
        "target": target, "direction": direction}


def do_job(db, cfg, qq):
    """去群里其他人公司打工赚钱。

    【平衡】打工是应急兜底，不是印钞机：
      - 冷却按职级 10~25 分钟（原来 30 秒，打几次就顶得上炒股一整天）；
      - 工资按雇主身家的 0.15% 算，且有每日上限（默认 160 元）；
      - 职级越高工资越高，但要熬更久；
      - 有小概率被鸽（白干）或加鸡腿（多拿）。
    想赚大钱只能去炒股 —— 波动、杠杆、爆仓都归你扛。
    """
    qq = str(qq)
    ensure_user(db, qq, "", cfg["initial_cash"])
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    level_name, mult, cooldown = job_level(user["jobs"])[1:]
    # 破产专属加成：只在纾困流程进行中生效，非破产玩家收益一点不变。
    is_bankrupt = recovery.in_relief(db, qq)
    if is_bankrupt:
        mult = mult * float(cfg.get("bankrupt_job_mult", 3.0))
        cooldown = int(cooldown * 0.6)
    if user["last_job"] and now() - user["last_job"] < cooldown:
        wait = cooldown - (now() - user["last_job"])
        return False, "老板说让你歇会儿，%d 秒后再来（当前职级：%s）" % (wait, level_name), None
    _acct0 = account(db, cfg, qq)
    daily_cap = job_cap_by_tier(cfg, _acct0["equity"] if _acct0 else 0.0)
    if is_bankrupt:
        daily_cap = float(cfg.get("bankrupt_job_cap", 1200.0))
    earned = job_today_income(db, qq)
    if earned >= daily_cap:
        return False, ("今天打工已经挣满 %.0f 元上限，明天再来（打工只是应急，炒股才是主业）"
                       % daily_cap), None
    others = db.q("SELECT qq, nick, cash FROM users WHERE qq<>? AND cash > 0", (qq,))
    # 【v4.7】打工工资的档位特权：高档位多拿一点（金额很小，聊胜于无）
    tier_wage = 1.0 + (tiers.wage_multi(_acct0["equity"]) if _acct0 else 0.0)
    if not others:
        return False, "全群的人都在破产，没人招得起你（先等等，或者去贷款）", None
    random.shuffle(others)
    employer = None
    wage = 0.0
    for o in others:
        w = max(cfg["wage_base"] * 0.5, min(cfg["wage_max"], cfg["wage_ratio"] * max(o["cash"], 1000)))
        w = round(w * mult * tier_wage, 2)
        if o["cash"] > w * 1.2:
            employer = o
            wage = w
            break
    if employer is None:
        return False, "老板们都在观望，没人敢雇你", None
    boss = employer["nick"] or ("群友" + employer["qq"][-3:])
    ind = random.choice(["车间", "仓库", "客服中心", "门店", "直播间", "后厨"])
    # 随机事件
    r = random.random()
    event = ""
    ghosted = False
    if r < JOB_EVENTS[0][0]:
        extra = round(wage * random.uniform(0.3, 0.8), 2)
        wage += extra
        event = JOB_EVENTS[0][2].format(extra=int(extra))
    elif r < JOB_EVENTS[0][0] + JOB_EVENTS[1][0]:
        event = JOB_EVENTS[1][2]
        wage = 0.0
        ghosted = True
    # 打工双倍券：抽到就能翻倍（被鸽白干的时候不消耗）
    if wage > 0 and coupons.job_doubled(db, qq):
        wage = round(wage * 2, 2)
        event = (event + " 🎉 双倍券生效，工资翻倍！").strip()
    # 每日上限封顶
    if earned + wage > daily_cap:
        wage = max(0.0, daily_cap - earned)
    if wage <= 0:
        # 【小修】被鸽（白干）时不能报「打工上限」—— 那是两回事，
        # 群友看到会以为今天白干了。而且白干也要占掉冷却，
        # 否则玩家可以疯狂重试，把 12% 的被鸽概率刷成 0。
        if ghosted:
            db.ex("UPDATE users SET last_job=? WHERE qq=?", (now(), qq))
            return False, event + " 白干一天，一分钱没赚到，明天再来。", None
        return False, ("今天打工已经挣满 %.0f 元上限，明天再来"
                       "（打工只是应急，炒股才是主业）" % daily_cap), None
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (wage, employer["qq"]))
    db.ex("UPDATE users SET cash=cash+?, jobs=jobs+1, last_job=? WHERE qq=?", (wage, now(), qq))
    _ledger(db, qq, wage, "打工工资")
    relief = recovery.earn(db, cfg, qq, wage)
    # 雇主这一笔也得记账，不然群友对不上账（原因别用「打工」开头，
    # 否则会被 job_today_income 当成打工收入统计进去）
    _ledger(db, employer["qq"], -wage, "支付工资给 " + (qq[-4:]))
    line = random.choice(JOB_LINES).format(boss=boss, ind=ind, wage=int(wage),
                                          k=random.randint(3, 18))
    acct = account(db, cfg, qq)
    if recovery.sync_bankrupt(db, cfg, qq, acct["equity"]):
        line += " 恭喜你净值回正，重获交易资格！"
    tail = " 本次收入 %.2f 元（职级 %s，今日已挣 %.0f/%.0f）。" % (
        wage, level_name, earned + wage, daily_cap)
    out = line + event + tail
    for m in relief:
        out += "\n" + m
    return True, out, {"wage": wage, "level": level_name}


# ---------------------------------------------------------------- 排行
def ranking(db, cfg, limit=20):
    """玩家榜。

    【为什么不用 account() 逐个算】老写法是「查一次 users，然后每个用户
    再查一次 users + 一次 positions」—— 一个 250 人的群就是 500 次 SQL，
    而榜单在网页每次刷新都会打一次（/api/state 里就有一份 top20）。
    现在改成两次查询：users 全量 + 未平仓持仓全量，内存里算净值。
    """
    rows = db.q("SELECT * FROM users")
    if not rows:
        return []
    pos_rows = db.q(
        "SELECT p.qq, p.margin, p.qty, p.entry, p.side, "
        "COALESCE(c.price, p.entry) AS price "
        "FROM positions p LEFT JOIN companies c ON c.id=p.cid "
        "WHERE p.status='open'")
    by_qq = {}
    for p in pos_rows:
        sign = 1 if p["side"] == "long" else -1
        pl = (float(p["price"]) - float(p["entry"])) * float(p["qty"]) * sign
        agg = by_qq.get(p["qq"])
        if agg is None:
            agg = [0.0, 0.0]
            by_qq[p["qq"]] = agg
        agg[0] += float(p["margin"])
        agg[1] += pl
    items = []
    for r in rows:
        margin, unreal = by_qq.get(r["qq"], (0.0, 0.0))
        cash = float(r["cash"] or 0)
        loan = float(r["loan"] or 0)
        equity = cash + margin + unreal - loan
        items.append({"qq": r["qq"], "nick": r["nick"] or r["qq"],
                      "equity": round(equity, 2), "cash": round(cash, 2),
                      "loan": round(loan, 2),
                      "pl": round(unreal + float(r["win"] or 0) - float(r["lose"] or 0), 2),
                      "bankrupt": r["bankrupt"], "jobs": r["jobs"],
                      "reopen_until": int(r["reopen_until"] or 0)})
    items.sort(key=lambda x: x["equity"], reverse=True)
    for i, it in enumerate(items, 1):
        it["rank"] = i
    return items[:limit]

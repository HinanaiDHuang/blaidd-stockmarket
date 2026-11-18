"""条件单：止损 / 止盈 / 限价挂单（同花顺那套）。

三种单：
  stop_loss   止损 —— 持仓方向和触发价「反向」时自动平掉
  take_profit 止盈 —— 持仓方向和触发价「同向」时自动平掉
  limit       限价 —— 价格到挂单价时自动开仓（买低于现价 / 卖高于现价）

引擎每个 tick 调一次 scan()，触发后直接改仓 + 记账，
再把事件抛给 server 去推群里。
"""
import time

import market

KIND_NAMES = {
    "stop_loss": "止损",
    "take_profit": "止盈",
    "limit": "限价",
}
MAX_ACTIVE = 10          # 每人最多挂多少张条件单
LIMIT_FAR = 0.35         # 限价单离现价最远能挂多远（±35%）


def _now():
    return int(time.time())


def _kind(key):
    k = str(key or "").strip().lower()
    if k in ("stop_loss", "sl", "止损", "止损单"):
        return "stop_loss"
    if k in ("take_profit", "tp", "止盈", "止盈单"):
        return "take_profit"
    if k in ("limit", "lp", "限价", "限价单", "挂单"):
        return "limit"
    return None


def _side(key):
    s = str(key or "").strip().lower()
    if s in ("long", "多", "做多", "buy", "买入"):
        return "long"
    if s in ("short", "空", "做空", "sell", "卖出"):
        return "short"
    return None


def active_count(db, qq):
    row = db.q1("SELECT COUNT(*) c FROM orders WHERE qq=? AND status='active'", (str(qq),))
    return int(row["c"]) if row else 0


def place(db, cfg, qq, cid, kind, side, trigger, qty=0, note=""):
    """挂一张条件单。返回 (ok, msg, info)。"""
    qq = str(qq)
    kind = _kind(kind)
    side = _side(side)
    if kind is None:
        return False, "单子类型只能是止损、止盈或限价", None
    if side is None:
        return False, "方向只能是多或空", None
    comp = db.q1("SELECT * FROM companies WHERE id=?", (int(cid),))
    if comp is None:
        return False, "没有这只股票", None
    try:
        trigger = float(trigger)
        qty = float(qty or 0)
    except (TypeError, ValueError):
        return False, "价格或数量不合法", None
    if trigger <= 0:
        return False, "触发价不合法", None
    cur = float(comp["price"])
    if kind == "limit":
        if side == "long" and trigger > cur:
            return False, "买入限价要低于现价（现价 %.3f）" % cur, None
        if side == "short" and trigger < cur:
            return False, "卖出限价要高于现价（现价 %.3f）" % cur, None
        if abs(trigger / cur - 1.0) > LIMIT_FAR:
            return False, "限价离现价太远了（最多 ±%d%%）" % int(LIMIT_FAR * 100), None
        if qty <= 0:
            return False, "限价单要写数量", None
    else:
        # 止损/止盈要跟持仓方向对得上：多头止损在下方、止盈在上方，空头反过来
        if side == "long":
            if kind == "stop_loss" and trigger >= cur:
                return False, "多头止损价要低于现价（现价 %.3f）" % cur, None
            if kind == "take_profit" and trigger <= cur:
                return False, "多头止盈价要高于现价（现价 %.3f）" % cur, None
        else:
            if kind == "stop_loss" and trigger <= cur:
                return False, "空头止损价要高于现价（现价 %.3f）" % cur, None
            if kind == "take_profit" and trigger >= cur:
                return False, "空头止盈价要低于现价（现价 %.3f）" % cur, None
        if qty < 0:
            return False, "数量不合法", None
        # 止损/止盈必须有对应的持仓才挂得上：没有仓位就永远等不到平仓，
        # 白占一个条件单名额，触发时还会被当成「仓位不在了」作废。
        hold = db.q1("SELECT COUNT(*) c FROM positions "
                     "WHERE qq=? AND cid=? AND side=? AND status='open'",
                     (qq, int(cid), side))
        if not hold or int(hold["c"]) <= 0:
            return False, "你并没有「%s」的%s，止损止盈挂不上去" % (
                comp["name"], "多单" if side == "long" else "空单"), None
    if active_count(db, qq) >= MAX_ACTIVE:
        return False, "你已经有 %d 张条件单了，先撤一张" % MAX_ACTIVE, None
    db.ex("INSERT INTO orders(qq,cid,kind,side,trigger,qty,status,created_at,note) "
          "VALUES(?,?,?,?,?,?,'active',?,?)",
          (qq, int(cid), kind, side, trigger, qty, _now(), str(note or "")[:40]))
    name = comp["name"]
    if kind == "limit":
        txt = "已挂限价单：%s %s %g 股 @ %g（到价自动成交）" % (
            name, "买入" if side == "long" else "卖出", qty, trigger)
    else:
        txt = "已挂%s单：%s %s @ %g（到价自动平仓）" % (
            KIND_NAMES[kind], name, "多单" if side == "long" else "空单", trigger)
    return True, txt, {"cid": int(cid), "kind": kind, "side": side,
                       "trigger": trigger, "qty": qty}


def list_orders(db, qq, limit=30, only_active=False):
    sql = ("SELECT o.*, c.name AS cname, c.price AS cprice FROM orders o "
           "LEFT JOIN companies c ON c.id=o.cid WHERE o.qq=?")
    if only_active:
        sql += " AND o.status='active'"
    sql += " ORDER BY o.id DESC LIMIT ?"
    rows = db.q(sql, (str(qq), int(limit)))
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "cid": r["cid"],
            "name": r["cname"] or ("#%d" % r["cid"]),
            "kind": r["kind"], "kind_name": KIND_NAMES.get(r["kind"], r["kind"]),
            "side": r["side"], "side_name": "多" if r["side"] == "long" else "空",
            "trigger": market.px(r["trigger"]),
            "qty": r["qty"],
            "status": r["status"],
            "status_name": {"active": "挂单中", "triggered": "已触发",
                            "cancelled": "已撤销"}.get(r["status"], r["status"]),
            "created_at": r["created_at"], "triggered_at": r["triggered_at"],
            "cur": market.px(r["cprice"]) if r["cprice"] is not None else 0,
        })
    return out


def cancel(db, qq, oid):
    cur = db.ex("UPDATE orders SET status='cancelled' WHERE id=? AND qq=? AND status='active'",
                (int(oid), str(qq)))
    return cur.rowcount > 0


def _close_positions(db, cfg, qq, cid, side, qty):
    """平掉匹配的持仓。qty=0 表示全平。返回 (提示列表, 未平掉的股数)。"""
    rows = db.q("SELECT * FROM positions WHERE qq=? AND cid=? AND side=? AND status='open' "
                "ORDER BY id", (str(qq), int(cid), side))
    msgs = []
    full = qty <= 0
    left = qty
    for p in rows:
        if not full and left <= 0:
            break
        take = float(p["qty"]) if full else min(float(p["qty"]), left)
        ok, msg, _ = market.close_position(db, cfg, qq, p["id"], qty=take)
        if ok:
            msgs.append(msg)
            left -= take
    remain = 0.0 if full else max(0.0, left)
    return msgs, remain


def scan(db, cfg):
    """行情推进后调用：检查所有条件单。返回本次触发的事件列表。"""
    rows = db.q("SELECT * FROM orders WHERE status='active'")
    events = []
    for o in rows:
        comp = db.q1("SELECT price, name FROM companies WHERE id=?", (o["cid"],))
        if comp is None:
            db.ex("UPDATE orders SET status='cancelled' WHERE id=?", (o["id"],))
            continue
        price = float(comp["price"])
        trig = float(o["trigger"])
        kind = o["kind"]
        side = o["side"]
        if kind == "stop_loss":
            hit = (price <= trig) if side == "long" else (price >= trig)
        elif kind == "take_profit":
            hit = (price >= trig) if side == "long" else (price <= trig)
        else:  # limit
            hit = (price <= trig) if side == "long" else (price >= trig)
        if not hit:
            continue

        db.ex("UPDATE orders SET status='triggered', triggered_at=? WHERE id=?",
              (_now(), o["id"]))
        qq = o["qq"]
        name = comp["name"]
        if kind == "limit":
            fill = min(price, trig) if side == "long" else max(price, trig)
            ok, msg, _ = market.place_order(db, cfg, qq, o["cid"], side,
                                            float(o["qty"] or 0), 1, price=fill)
            if not ok:
                db.ex("UPDATE orders SET status='cancelled' WHERE id=?", (o["id"],))
                events.append({"id": o["id"], "qq": qq, "cid": o["cid"], "name": name,
                               "kind": kind, "msg": "限价单没能成交：%s" % msg})
            else:
                events.append({"id": o["id"], "qq": qq, "cid": o["cid"], "name": name,
                               "kind": kind, "msg": "限价单成交：%s" % msg})
            continue

        msgs, remain = _close_positions(db, cfg, qq, o["cid"], side, float(o["qty"] or 0))
        if not msgs:
            # 仓位早没了（手动平过）——单子作废
            db.ex("UPDATE orders SET status='cancelled' WHERE id=?", (o["id"],))
            events.append({"id": o["id"], "qq": qq, "cid": o["cid"], "name": name,
                           "kind": kind, "msg": "%s%s单已作废（仓位不在了）" % (
                               name, KIND_NAMES.get(kind, ""))})
            continue
        tail = "（还剩 %g 股没平到）" % remain if remain > 0 else ""
        events.append({"id": o["id"], "qq": qq, "cid": o["cid"], "name": name,
                       "kind": kind, "side": side,
                       "msg": "触发%s：%s %s%s" % (KIND_NAMES.get(kind, "条件单"), name,
                                               "；".join(msgs), tail)})
    return events

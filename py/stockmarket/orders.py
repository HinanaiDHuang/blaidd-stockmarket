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
    if s in ("long", "多", "做多", "多头", "多单", "buy", "买", "买入"):
        return "long"
    if s in ("short", "空", "做空", "空头", "空单", "sell", "卖", "卖出"):
        return "short"
    return None


def _check_stop_price(kind, side, cur, price, qty, pos_qty):
    """校验一张止损 / 止盈单：价格方向要对得上，数量不能离谱。
    返回 (ok, 错误信息)。"""
    ok, err = _px_dir_ok(kind, side, cur, price)
    if not ok:
        return False, err
    return _qty_ok(qty, pos_qty)


def _px_dir_ok(kind, side, cur, price):
    """止损 / 止盈的价要跟持仓方向对得上：多头止损在下方、止盈在上方，空头反过来。"""
    if price <= 0:
        return False, "触发价不合法"
    if side == "long":
        if kind == "stop_loss" and price >= cur:
            return False, "多头止损价要低于现价（现价 %.3f）" % cur
        if kind == "take_profit" and price <= cur:
            return False, "多头止盈价要高于现价（现价 %.3f）" % cur
    else:
        if kind == "stop_loss" and price <= cur:
            return False, "空头止损价要高于现价（现价 %.3f）" % cur
        if kind == "take_profit" and price >= cur:
            return False, "空头止盈价要低于现价（现价 %.3f）" % cur
    return True, ""


def _qty_ok(qty, pos_qty):
    """要平的股数：整数，且不能超过持仓。qty=0 表示全仓。"""
    if qty > 0:
        if qty != int(qty):
            return False, "数量要整数股"
        if qty > pos_qty:
            return False, "你要平的 %g 股超过持仓 %g 股" % (qty, pos_qty)
    return True, ""


def _hold_sides(db, qq, cid):
    """这个人在这只票上现在持有哪些方向。返回 {"long"} / {"short"} / 两者 / 空集。"""
    rows = db.q("SELECT DISTINCT side FROM positions "
                "WHERE qq=? AND cid=? AND status='open'", (str(qq), int(cid)))
    return set(r["side"] for r in rows if r["side"] in ("long", "short"))


def _pos_qty(db, qq, cid, side):
    """某个方向上现在一共持有多少股。"""
    row = db.q1("SELECT COALESCE(SUM(qty),0) q FROM positions "
                "WHERE qq=? AND cid=? AND side=? AND status='open'",
                (str(qq), int(cid), side))
    return float(row["q"] or 0) if row else 0.0


def active_count(db, qq):
    row = db.q1("SELECT COUNT(*) c FROM orders WHERE qq=? AND status='active'", (str(qq),))
    return int(row["c"]) if row else 0


def place(db, cfg, qq, cid, kind, side, trigger, qty=0, note=""):
    """挂一张条件单。返回 (ok, msg, info)。

    side 传空串 / None = 不指定方向：
      - 止损 / 止盈：按当前持仓自动推断（只有多头 / 只有空头都挂得上；
        多空两头同时持有才要求显式指定）—— 对齐 MT4/MT5「SL/TP 挂在持仓上」的语义。
      - 限价单是开仓单，必须显式给方向。
    """
    qq = str(qq)
    kind = _kind(kind)
    raw_side = "" if side is None else str(side).strip()
    side = _side(side)
    if kind is None:
        return False, "单子类型只能是止损、止盈或限价", None
    if side is None and raw_side:
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
        if side is None:
            return False, "限价单要写方向（买 / 卖）", None
        if side == "long" and trigger > cur:
            return False, "买入限价要低于现价（现价 %.3f）" % cur, None
        if side == "short" and trigger < cur:
            return False, "卖出限价要高于现价（现价 %.3f）" % cur, None
        if abs(trigger / cur - 1.0) > LIMIT_FAR:
            return False, "限价离现价太远了（最多 ±%d%%）" % int(LIMIT_FAR * 100), None
        if qty <= 0:
            return False, "限价单要写数量", None
    else:
        # 没写方向 → 看这个人在这只票上到底持着哪一边
        if side is None:
            sides = _hold_sides(db, qq, cid)
            if not sides:
                return False, "你并没有「%s」的持仓，先买入再挂止损止盈" % comp["name"], None
            if len(sides) > 1:
                return False, ("你「%s」多空两头都拿着，说清楚要保护哪一边"
                               "（例：止损 %s 空 18.5）" % (comp["name"], comp["name"])), None
            side = "long" if "long" in sides else "short"
        if qty < 0:
            return False, "数量不合法", None
        # 止损/止盈必须有对应的持仓才挂得上：没有仓位就永远等不到平仓，
        # 白占一个条件单名额，触发时还会被当成「仓位不在了」作废。
        pos_qty = _pos_qty(db, qq, cid, side)
        if pos_qty <= 0:
            return False, "你并没有「%s」的%s，止损止盈挂不上去" % (
                comp["name"], "多单" if side == "long" else "空单"), None
        # 价格方向 + 数量一起验
        dir_ok, dir_err = _px_dir_ok(kind, side, cur, trigger)
        qty_ok, qty_err = _qty_ok(qty, pos_qty)
        if dir_ok and qty_ok:
            pass
        elif not dir_ok and qty > 0:
            # 价落在持仓的反面 —— 多半是「数量 价格」写反了，换个摆法再试一次
            alt_ok, _ = _check_stop_price(kind, side, cur, qty, trigger, pos_qty)
            if alt_ok:
                trigger, qty = qty, trigger
            else:
                return False, dir_err, None
        elif not dir_ok:
            return False, dir_err, None
        else:
            return False, qty_err, None
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
        scope = "全仓" if qty <= 0 else "平 %g 股" % qty
        txt = "已挂%s单：%s %s @ %g（%s，到价自动平仓）" % (
            KIND_NAMES[kind], name, "多单" if side == "long" else "空单",
            trigger, scope)
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


def cancel_protection(db, qq, cid, side):
    """仓位没了，挂在它上面的止损/止盈一起作废。

    MT4/MT5 里 SL / TP 是绑在持仓上的一对（近似 OCO）：一边触发平仓，
    另一边自动消失。咱们以前是两张独立单 —— 平完仓另一张还挂着占名额，
    将来触发还会弹一句「仓位不在了」，纯粹添堵。
    """
    db.ex("UPDATE orders SET status='cancelled' WHERE qq=? AND cid=? AND side=? "
          "AND status='active' AND kind IN ('stop_loss','take_profit')",
          (str(qq), int(cid), side))


def _prot_count(db, qq, cid, side):
    """这个方向上还挂着几张止损/止盈。"""
    row = db.q1("SELECT COUNT(*) c FROM orders WHERE qq=? AND cid=? AND side=? "
                "AND status='active' AND kind IN ('stop_loss','take_profit')",
                (str(qq), int(cid), side))
    return int(row["c"]) if row else 0


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

        prot_before = _prot_count(db, qq, o["cid"], side)
        msgs, remain = _close_positions(db, cfg, qq, o["cid"], side, float(o["qty"] or 0))
        if not msgs:
            # 仓位早没了（手动平过）——单子作废
            db.ex("UPDATE orders SET status='cancelled' WHERE id=?", (o["id"],))
            events.append({"id": o["id"], "qq": qq, "cid": o["cid"], "name": name,
                           "kind": kind, "msg": "%s%s单已作废（仓位不在了）" % (
                               name, KIND_NAMES.get(kind, ""))})
            continue
        tail = "（还剩 %g 股没平到）" % remain if remain > 0 else ""
        if _prot_count(db, qq, o["cid"], side) < prot_before:
            tail = tail + "（同方向另一张止损/止盈单已自动撤销）"
        events.append({"id": o["id"], "qq": qq, "cid": o["cid"], "name": name,
                       "kind": kind, "side": side,
                       "msg": "触发%s：%s %s%s" % (KIND_NAMES.get(kind, "条件单"), name,
                                               "；".join(msgs), tail)})
    return events

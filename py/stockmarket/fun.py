"""趣味玩法：人气投票 / 每日抽奖 / 涨停竞猜。

三个玩法是同一个目的：给群友一个「每天都值得回来看一眼」的理由。
  vote     人气投票 —— 每天一票给群友的公司拉人气，票数会让股价轻微上浮
  lottery  每日抽奖 —— 每天一次免费抽奖，奖池有现金也有道具券
  guess    涨停竞猜 —— 每天猜一只票，收盘时看谁猜中了涨停
"""
import random
import time

import coupons
import features
import market

GUESS_REWARD = 2000.0        # 猜中涨停的奖金
GUESS_SOLO_BONUS = 3000.0    # 全市场只有一只涨停时的额外奖励
VOTE_BOOST = 0.0008          # 每票给股价的涨幅（0.08%）
VOTE_BOOST_MAX = 0.015       # 单只票靠投票最多抬 1.5%

# 抽奖池（累计阈值，顺序即概率从高到低）
LOTTERY_PRIZES = [
    (0.22, "cash", 40.0, "小钱钱"),
    (0.44, "cash", 100.0, "还行"),
    (0.58, "cash", 240.0, "不错"),
    (0.63, "cash", 600.0, "大手笔"),
    (0.79, "fee_free", 5.0, "免手续费券"),
    (0.90, "loan_free", 1.0, "免息券"),
    (1.01, "job_double", 2.0, "打工双倍券"),
]


def _day(ts=None):
    return time.strftime("%Y%m%d", time.localtime(ts if ts else time.time()))


def _yesterday():
    return _day(time.time() - 86400)


# ---------------------------------------------------------------- 人气投票
def cast_vote(db, cfg, qq, cid):
    """给某家公司投一票（每人每天一票）。"""
    qq = str(qq)
    day = _day()
    comp = db.q1("SELECT * FROM companies WHERE id=?", (int(cid),))
    if comp is None:
        return False, "没有这只股票", None
    if db.q1("SELECT qq FROM votes WHERE qq=? AND day=?", (qq, day)):
        return False, "今天已经投过一票啦，明天再来", None
    db.ex("INSERT INTO votes(qq,day,cid,ts) VALUES(?,?,?,?)",
          (qq, day, int(cid), int(time.time())))
    db.ex("INSERT INTO company_votes(cid,day,n) VALUES(?,?,1) "
          "ON CONFLICT(cid,day) DO UPDATE SET n=n+1", (int(cid), day))
    row = db.q1("SELECT n FROM company_votes WHERE cid=? AND day=?", (int(cid), day))
    n = int(row["n"]) if row else 1
    features._apply_impact(db, cfg, int(cid), min(VOTE_BOOST_MAX, n * VOTE_BOOST))
    return True, "已给「%s」投了一票（今日第 %d 票）" % (comp["name"], n), {
        "cid": int(cid), "votes": n, "name": comp["name"]}


def vote_status(db, cfg, qq):
    """我今天投票了没 + 今天投了多少票。"""
    day = _day()
    row = db.q1("SELECT cid FROM votes WHERE qq=? AND day=?", (str(qq), day))
    total = db.q1("SELECT COUNT(*) c FROM votes WHERE day=?", (day,))
    return {"day": day, "voted": bool(row), "cid": row["cid"] if row else 0,
            "total": int(total["c"]) if total else 0}


def vote_board(db, cfg, limit=10, day=None):
    """今日人气投票榜（按票数）。"""
    day = day or _day()
    rows = db.q("SELECT cv.cid, cv.n, c.name, c.ticker, c.price, c.prev_close, c.qq "
                "FROM company_votes cv JOIN companies c ON c.id=cv.cid "
                "WHERE cv.day=? ORDER BY cv.n DESC, c.name LIMIT ?", (day, int(limit)))
    items = []
    for i, r in enumerate(rows, 1):
        chg = (r["price"] / r["prev_close"] - 1.0) * 100 if r["prev_close"] else 0.0
        items.append({"rank": i, "cid": r["cid"], "name": r["name"], "ticker": r["ticker"],
                      "votes": int(r["n"]), "price": market.px(r["price"]),
                      "chg": round(chg, 2), "owner": r["qq"]})
    return items


def vote_text(db, cfg, qq, limit=10):
    st = vote_status(db, cfg, qq)
    items = vote_board(db, cfg, limit)
    lines = ["🗳 今日人气投票榜"]
    if not items:
        lines.append("（今天还没人投票，快去投一票把朋友的票拉起来）")
    for it in items:
        lines.append("%d. %s(%s) %d 票　%.3f %+.2f%%" % (
            it["rank"], it["name"], it["ticker"], it["votes"], it["price"], it["chg"]))
    lines.append("—— 今天一共 %d 票 ——" % st["total"])
    if st["voted"]:
        lines.append("你已经投过票了，明天再来（每人每天一票）")
    else:
        lines.append("发「投票 股票名」给你看好的公司投一票（每人每天一票）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 每日抽奖
def draw_lottery(db, cfg, qq):
    """每日免费抽奖。"""
    qq = str(qq)
    day = _day()
    if db.q1("SELECT qq FROM lottery WHERE qq=? AND day=?", (qq, day)):
        return False, "今天已经抽过啦，明天再来", None
    market.ensure_user(db, qq, "", cfg["initial_cash"])
    r = random.random()
    kind, amount, label = "cash", 0.0, "谢谢参与"
    for th, k, amt, lb in LOTTERY_PRIZES:
        if r < th:
            kind, amount, label = k, amt, lb
            break
    if kind == "cash":
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (amount, qq))
        features._ledger(db, qq, amount, "每日抽奖")
        text = "🎰 抽中了 %d 元现金！" % amount
    elif kind == "fee_free":
        coupons.grant(db, qq, "fee_free", int(amount))
        text = "🎰 抽中了「免手续费券」×%d，接下来 %d 笔交易不收手续费" % (int(amount), int(amount))
    elif kind == "loan_free":
        coupons.grant(db, qq, "loan_free", 1, days=coupons.LOAN_FREE_DAYS)
        text = "🎰 抽中了「免息券」，%d 天内贷款不计息" % coupons.LOAN_FREE_DAYS
    else:
        coupons.grant(db, qq, "job_double", int(amount))
        text = "🎰 抽中了「打工双倍券」×%d，打工时工资翻倍" % int(amount)
    db.ex("INSERT INTO lottery(qq,day,prize,amount,ts) VALUES(?,?,?,?,?)",
          (qq, day, kind, amount, int(time.time())))
    return True, text, {"kind": kind, "amount": amount, "label": label}


def lottery_status(db, cfg, qq):
    day = _day()
    row = db.q1("SELECT prize, amount, ts FROM lottery WHERE qq=? AND day=?", (str(qq), day))
    return {"day": day, "drawn": bool(row),
            "prize": row["prize"] if row else "",
            "amount": row["amount"] if row else 0}


def lottery_text(db, cfg, qq):
    st = lottery_status(db, cfg, qq)
    if st["drawn"]:
        return "🎰 今天已经抽过啦，明天再来～"
    return "🎰 每日抽奖（免费，每天一次）\n发「抽奖」来抽，奖池有现金和道具券。"


# ---------------------------------------------------------------- 涨停竞猜
def make_guess(db, cfg, qq, cid):
    """猜今天哪只股票会涨停（每人每天一票）。"""
    qq = str(qq)
    day = _day()
    comp = db.q1("SELECT * FROM companies WHERE id=?", (int(cid),))
    if comp is None:
        return False, "没有这只股票", None
    if db.q1("SELECT qq FROM guess WHERE qq=? AND day=?", (qq, day)):
        return False, "今天已经竞猜过了，明天再来", None
    db.ex("INSERT INTO guess(qq,day,cid,ts) VALUES(?,?,?,?)",
          (qq, day, int(cid), int(time.time())))
    return True, "🔮 已锁定：今天「%s」会涨停。收盘见真章！" % comp["name"], {
        "cid": int(cid), "name": comp["name"]}


def guess_status(db, cfg, qq):
    day = _day()
    row = db.q1("SELECT g.cid, c.name, c.ticker FROM guess g "
                "LEFT JOIN companies c ON c.id=g.cid WHERE g.qq=? AND g.day=?",
                (str(qq), day))
    mine = None
    if row:
        mine = {"cid": row["cid"], "name": row["name"] or ("#%d" % row["cid"]),
                "ticker": row["ticker"] or ""}
    hits = db.q("SELECT cid FROM limit_hits WHERE day=?", (day,))
    total = db.q1("SELECT COUNT(*) c FROM guess WHERE day=?", (day,))
    return {"day": day, "mine": mine, "limit_up_count": len(hits),
            "players": int(total["c"]) if total else 0}


def guess_text(db, cfg, qq):
    st = guess_status(db, cfg, qq)
    lines = ["🔮 今日涨停竞猜"]
    if st["mine"]:
        lines.append("你押的是：%s" % st["mine"]["name"])
    else:
        lines.append("你还没押，发「涨停竞猜 股票名」押一只（每人每天一票）")
    lines.append("目前已经涨停 %d 家，全群 %d 人参与了" % (st["limit_up_count"], st["players"]))
    lines.append("收盘后结算，猜中拿 %d 元" % int(GUESS_REWARD))
    return "\n".join(lines)


def settle_guess(db, cfg):
    """结算昨天的涨停竞猜。返回给群里播报的文案（没有就 None）。"""
    day = _yesterday()
    if db.get_meta("guess_settled_day", "") == day:
        return None
    db.set_meta("guess_settled_day", day)
    guesses = db.q("SELECT qq, cid FROM guess WHERE day=?", (day,))
    if not guesses:
        return None
    hit_rows = db.q("SELECT cid FROM limit_hits WHERE day=?", (day,))
    hit_ids = []
    for r in hit_rows:
        cid = int(r["cid"])
        if cid not in hit_ids:
            hit_ids.append(cid)
    names = []
    for cid in hit_ids:
        comp = db.q1("SELECT name FROM companies WHERE id=?", (cid,))
        names.append(comp["name"] if comp else "#%d" % cid)
    winners = [g for g in guesses if int(g["cid"]) in hit_ids]
    if not hit_ids:
        return "📉 昨日涨停竞猜：昨天市场一只涨停都没有，全员押错，惨 😂（%d 人参与）" % len(guesses)
    if not winners:
        return ("📉 昨日涨停竞猜：涨停的是「%s」，%d 人参与但没人押中，下次就是你 💸"
                % ("、".join(names), len(guesses)))
    solo = len(hit_ids) == 1
    reward = GUESS_REWARD + (GUESS_SOLO_BONUS if solo else 0)
    for g in winners:
        qq = g["qq"]
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (reward, qq))
        features._ledger(db, qq, reward, "涨停竞猜奖励")
    win_names = []
    for g in winners:
        comp = db.q1("SELECT name FROM companies WHERE id=?", (g["cid"],))
        win_names.append(comp["name"] if comp else "#%d" % g["cid"])
    tail = "（全场独中，额外+%d）" % int(GUESS_SOLO_BONUS) if solo else ""
    return ("🎉 昨日涨停竞猜结算：%d 人押中「%s」，每人拿 %.0f 元%s\n（%d 人参与）"
            % (len(winners), "、".join(win_names), reward, tail, len(guesses)))

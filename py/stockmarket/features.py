# -*- coding: utf-8 -*-
"""炒股模拟器 v2 玩法模块：签到 / 成就 / 提醒 / 榜单 / 公司互动 / 抄作业 / 每日复盘。

设计目标（2026-11-17）：
  * 打工不再是印钞机 —— 收益压到炒股的一个零头，逼玩家去承担波动；
  * 给玩家「每天必须回来」的理由 —— 签到连签、成就、提醒触发、每日复盘。
所有写操作都进 ledger，方便玩家自己核账。
"""
import json
import random
import time

import market
import recovery
import tiers
import tycoon


# ---------------------------------------------------------------- 工具
def today(now=None):
    return time.strftime("%Y-%m-%d", time.localtime(now or time.time()))


def yesterday(now=None):
    return time.strftime("%Y-%m-%d", time.localtime((now or time.time()) - 86400))


def day_start(now=None):
    t = time.localtime(now or time.time())
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def _ledger(db, qq, delta, reason):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    bal = row["cash"] if row else 0.0
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (str(qq), int(time.time()), round(delta, 2), round(bal, 2), reason))


# ---------------------------------------------------------------- 每日签到
CHECKIN_LINES = [
    "签到成功，今天也在盯着盘。",
    "签到打卡，又是元气满满的一天。",
    "签到成功，先看一眼大盘再决定买什么。",
    "签到成功，今天的行情还没开盘，先来蹭个热度。",
]

GRAND_TEXT = "🎉 连续签到满 {days} 天，触发「开门红大赏」！"


def do_checkin(db, cfg, qq):
    """每日签到。返回 (ok, msg, info)。"""
    qq = str(qq)
    market.ensure_user(db, qq, "", cfg["initial_cash"])
    row = db.q1("SELECT * FROM checkin WHERE qq=?", (qq,))
    day = today()
    if row and row["last_day"] == day:
        return False, "今天已经签到过啦，再来一次连签奖励就没了（离下个大奖还差 %d 天）" % _days_to_bonus(row["streak"]), \
               {"streak": row["streak"], "done": True}
    streak = (row["streak"] + 1) if (row and row["last_day"] == yesterday()) else 1
    best = max(streak, row["best"] if row else 0)
    total = (row["total"] + 1) if row else 1
    base = float(cfg.get("checkin_base", 600.0))
    if streak % 7 == 0:
        reward = float(cfg.get("checkin_grand", 5000.0))
        bonus = True
    else:
        # 连签奖励封顶倍数改成可配：原来固定 ×3，配合 600 的底薪等于
        # 「躺 18 天连签」就能天天拿 1800，比打一天工还多 —— 和「炒股才是主业」冲突。
        reward = base * (1.0 + 0.12 * (streak - 1))
        reward = min(reward, base * float(cfg.get("checkin_streak_cap", 2.5)))
        bonus = False
    if recovery.in_relief(db, qq):
        # 破产专属福利：签到奖励翻倍（只对纾困中的玩家生效）
        reward = round(reward * float(cfg.get("bankrupt_checkin_mult", 2.0)), 2)
    else:
        reward = round(reward, 2)
    db.ex("INSERT INTO checkin(qq,streak,best,total,last_day) VALUES(?,?,?,?,?) "
          "ON CONFLICT(qq) DO UPDATE SET streak=excluded.streak, best=excluded.best, "
          "total=excluded.total, last_day=excluded.last_day",
          (qq, streak, best, total, day))
    db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (reward, qq))
    _ledger(db, qq, reward, "每日签到")
    relief = recovery.earn(db, cfg, qq, reward)
    ach = check_achievements(db, cfg, qq)
    msg = random.choice(CHECKIN_LINES) + " 签到获得 %.2f 元，当前连签 %d 天。" % (reward, streak)
    if bonus:
        msg = (GRAND_TEXT.format(days=streak) + " 奖金 %.2f 元！" % reward) + \
              " 连签 %d 天，明天别断。" % streak
    if ach:
        msg += "\n" + "　".join(ach)
    for m in relief:
        msg += "\n" + m
    return True, msg, {"reward": reward, "streak": streak, "best": best, "total": total,
                       "bonus": bonus, "new_achievements": ach}


def _days_to_bonus(streak):
    return 7 - (streak % 7)



def checkin_status(db, cfg, qq):
    row = db.q1("SELECT * FROM checkin WHERE qq=?", (str(qq),))
    streak = row["streak"] if row else 0
    done = bool(row and row["last_day"] == today())
    return {"streak": streak, "best": row["best"] if row else 0,
            "total": row["total"] if row else 0, "done": done,
            "bonus_in": _days_to_bonus(streak)}


# ---------------------------------------------------------------- 成就
ACH_DEFS = [
    # —— 交易基本功 ——
    ("first_trade", "第一笔交易", 300.0, "你终于下手了"),
    ("first_profit", "第一桶金", 500.0, "有盈利了，继续"),
    ("first_loss", "交过学费", 200.0, "亏过钱也算学费"),
    ("first_short", "空军首秀", 400.0, "第一次做空"),
    ("short_win", "空头也赚", 900.0, "做空平仓赚到过钱"),
    ("trader10", "小有交易量", 700.0, "完成 10 笔平仓"),
    ("trader50", "老韭菜", 2000.0, "完成 50 笔平仓"),
    ("lever10", "杠杆玩家", 800.0, "用过 10 倍以上杠杆"),
    ("lever20", "赌狗本狗", 1600.0, "用过 20 倍杠杆"),
    ("holder5", "广撒网", 600.0, "同时持有 5 只以上股票"),
    ("holder10", "满仓战士", 1400.0, "同时持有 10 只以上股票"),
    ("diversify", "不把鸡蛋放一个篮子", 700.0, "持仓横跨 3 个行业"),
    ("industry5", "跨行业老手", 1500.0, "持仓横跨 5 个行业"),
    ("bigwin", "一把梭哈", 1500.0, "单笔盈利超过初始资金 10%"),
    ("bigloss", "梭哈翻车", 400.0, "单笔亏损超过初始资金 10%"),
    # —— 借贷 ——
    ("first_loan", "第一笔贷款", 200.0, "第一次借钱炒股"),
    ("repaid", "诚信还款", 900.0, "还清过全部欠款"),
    ("bigdebt", "负债累累", 700.0, "欠款一度超过 10 万"),
    ("debtfree", "债务重整", 2200.0, "通过司法重整一笔勾销欠款"),
    # —— 条件单 ——
    ("cond_place", "纪律执行者", 400.0, "挂过条件单"),
    ("cond_hit", "自动化交易", 1000.0, "条件单触发过"),
    # —— 社区玩法 ——
    ("voter", "投票达人", 500.0, "投过 5 票"),
    ("gambler", "欧皇", 300.0, "抽过 3 次每日抽奖"),
    ("limitcatch", "涨停预言家", 1300.0, "竞猜中过涨停"),
    ("promoter", "自来水", 400.0, "给自己公司加过热度"),
    ("dividend", "股东", 350.0, "收到过分红"),
    ("renamer", "改名专业户", 250.0, "改过公司名"),
    ("saboteur", "商业战家", 400.0, "坑过别人的公司"),
    # —— 日常 ——
    ("job10", "打工模范", 250.0, "打工 10 次"),
    ("worker50", "劳动模范", 900.0, "打工 50 次"),
    ("streak3", "连续三天", 300.0, "连续签到 3 天"),
    ("streak7", "一周不断", 850.0, "连续签到 7 天"),
    ("streak14", "半月坚持", 1600.0, "连续签到 14 天"),
    ("streak30", "月度全勤", 3000.0, "连续签到 30 天"),
    # —— 财富 & 逆袭 ——
    ("rich", "净值翻倍", 1200.0, "净值达到初始资金两倍"),
    ("rich3", "三倍股神", 2500.0, "净值达到初始资金三倍"),
    ("survivor", "绝地求生", 700.0, "破产后又翻身"),
    ("comeback", "打工翻身", 1400.0, "破产后靠打工把欠款还清了"),
]

ACH_NAME = {c[0]: c[1] for c in ACH_DEFS}


def _count(db, sql, args=()):
    """COUNT 查询小助手。"""
    row = db.q1(sql, args)
    return int(row["c"]) if row else 0


def check_achievements(db, cfg, qq):
    """检查并发放成就，返回新解锁的文案列表。"""
    qq = str(qq)
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None:
        return []
    have = set(r["code"] for r in db.q("SELECT code FROM achievements WHERE qq=?", (qq,)))
    pos = db.q("SELECT * FROM positions WHERE qq=? AND status='open'", (qq,))
    trades = db.q("SELECT * FROM trades WHERE qq=?", (qq,))
    industries = set()
    for p in pos:
        row = db.q1("SELECT industry FROM companies WHERE id=?", (p["cid"],))
        if row:
            industries.add(row["industry"])
    acct = market.account(db, cfg, qq) or {}
    eq = acct.get("equity", 0.0)
    init = float(cfg.get("initial_cash", 100000.0))
    ck = db.q1("SELECT * FROM checkin WHERE qq=?", (qq,))
    streak = ck["streak"] if ck else 0
    closed = [t for t in trades if t["kind"] in ("close", "force_close")]
    relief_tier = int(user["relief_tier"] or 0)
    hits = set()
    # —— 交易 ——
    if trades:
        hits.add("first_trade")
    if user["win"] > 0:
        hits.add("first_profit")
    if user["lose"] > 0:
        hits.add("first_loss")
    if any(t["side"] == "short" for t in trades):
        hits.add("first_short")
    if any(t["side"] == "short" and float(t["pl"]) > 0 for t in closed):
        hits.add("short_win")
    if len(closed) >= 10:
        hits.add("trader10")
    if len(closed) >= 50:
        hits.add("trader50")
    if any(float(t["leverage"]) >= 10 for t in trades):
        hits.add("lever10")
    if any(float(t["leverage"]) >= 20 for t in trades):
        hits.add("lever20")
    if eq >= init * 2:
        hits.add("rich")
    if eq >= init * 3:
        hits.add("rich3")
    if len(pos) >= 5:
        hits.add("holder5")
    if len(pos) >= 10:
        hits.add("holder10")
    if len(industries) >= 3:
        hits.add("diversify")
    if len(industries) >= 5:
        hits.add("industry5")
    if any(float(t["pl"]) >= init * 0.1 for t in trades):
        hits.add("bigwin")
    if any(float(t["pl"]) <= -init * 0.1 for t in trades):
        hits.add("bigloss")
    # —— 借贷 ——
    if _count(db, "SELECT COUNT(*) c FROM loans WHERE qq=?", (qq,)) >= 1:
        hits.add("first_loan")
    if _count(db, "SELECT COUNT(*) c FROM loans WHERE qq=? AND status='done'", (qq,)) >= 1:
        hits.add("repaid")
    if _count(db, "SELECT COUNT(*) c FROM loans WHERE qq=? AND amount>=100000", (qq,)) >= 1:
        hits.add("bigdebt")
    if relief_tier >= 2:
        hits.add("debtfree")
    if relief_tier >= 1 and float(user["loan"] or 0) <= 0:
        hits.add("comeback")
    # —— 条件单 ——
    if _count(db, "SELECT COUNT(*) c FROM orders WHERE qq=?", (qq,)) >= 1:
        hits.add("cond_place")
    if _count(db, "SELECT COUNT(*) c FROM orders WHERE qq=? AND status='triggered'", (qq,)) >= 1:
        hits.add("cond_hit")
    # —— 社区玩法 ——
    if _count(db, "SELECT COUNT(*) c FROM votes WHERE qq=?", (qq,)) >= 5:
        hits.add("voter")
    if _count(db, "SELECT COUNT(*) c FROM lottery WHERE qq=?", (qq,)) >= 3:
        hits.add("gambler")
    if _count(db, "SELECT COUNT(*) c FROM ledger WHERE qq=? AND reason='涨停竞猜奖励'", (qq,)) >= 1:
        hits.add("limitcatch")
    if _count(db, "SELECT COUNT(*) c FROM ledger WHERE qq=? AND reason LIKE '公司宣传%'", (qq,)) >= 1:
        hits.add("promoter")
    if _count(db, "SELECT COUNT(*) c FROM ledger WHERE qq=? AND reason LIKE '收到%分红'", (qq,)) >= 1:
        hits.add("dividend")
    if _count(db, "SELECT COUNT(*) c FROM ledger WHERE qq=? AND reason='公司改名费'", (qq,)) >= 1:
        hits.add("renamer")
    if _count(db, "SELECT COUNT(*) c FROM ledger WHERE qq=? AND reason LIKE '商业战出手%'", (qq,)) >= 1:
        hits.add("saboteur")
    # —— 日常 ——
    if int(user["jobs"] or 0) >= 10:
        hits.add("job10")
    if int(user["jobs"] or 0) >= 50:
        hits.add("worker50")
    if streak >= 3:
        hits.add("streak3")
    if streak >= 7:
        hits.add("streak7")
    if streak >= 14:
        hits.add("streak14")
    if streak >= 30:
        hits.add("streak30")
    if eq > 0 and (user["bankrupt"] or int(user["bankrupt_ts"] or 0) > 0):
        hits.add("survivor")
    msgs = []
    for code, name, reward, desc in ACH_DEFS:
        if code in hits and code not in have:
            db.ex("INSERT OR IGNORE INTO achievements(qq,code,ts,reward) VALUES(?,?,?,?)",
                  (qq, code, int(time.time()), reward))
            db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (reward, qq))
            _ledger(db, qq, reward, "成就奖励 " + name)
            msgs.append("🏅 解锁成就【%s】%s，奖励 %.0f 元" % (name, desc, reward))
    return msgs


def achievements_of(db, cfg, qq):
    qq = str(qq)
    rows = db.q("SELECT code, ts, reward FROM achievements WHERE qq=? ORDER BY ts", (qq,))
    got = set(r["code"] for r in rows)
    out = []
    for code, name, reward, desc in ACH_DEFS:
        out.append({"code": code, "name": name, "reward": reward, "desc": desc,
                    "got": code in got, "ts": next((r["ts"] for r in rows if r["code"] == code), 0)})
    return out


# ---------------------------------------------------------------- 价格提醒
def add_reminder(db, cfg, qq, cid, kind, price):
    qq = str(qq)
    comp = db.q1("SELECT * FROM companies WHERE id=?", (int(cid),))
    if comp is None:
        return False, "没有这只股票", None
    kind = "above" if str(kind).lower() in ("above", "up", "涨", "涨到", ">") else "below"
    try:
        price = float(price)
    except (TypeError, ValueError):
        return False, "价格不合法", None
    if price <= 0:
        return False, "价格不合法", None
    limit = float(cfg.get("price_limit", 0.1))
    cur = float(comp["price"])
    if kind == "above" and price < cur * (1 - limit):
        return False, "提醒价 %.2f 低于现价 %.2f 太多（跌停才到得了）" % (price, cur), None
    if kind == "below" and price > cur * (1 + limit):
        return False, "提醒价 %.2f 高于现价 %.2f 太多（涨停才到得了）" % (price, cur), None
    n = db.q1("SELECT COUNT(*) c FROM reminders WHERE qq=? AND hit=0", (qq,))
    if n and n["c"] >= 5:
        return False, "你已经有 5 条未触发的提醒了，先删一条再加", None
    db.ex("INSERT INTO reminders(qq,cid,kind,price,created_at) VALUES(?,?,?,?,?)",
          (qq, int(cid), kind, price, int(time.time())))
    return True, "已设置：%s %s %.2f" % (comp["name"], "涨到" if kind == "above" else "跌到", price), \
        {"cid": int(cid), "kind": kind, "price": price}


def list_reminders(db, qq, limit=20):
    rows = db.q("SELECT r.id, r.cid, r.kind, r.price AS tprice, r.hit, r.hit_at, r.created_at, "
                "c.name AS cname, c.price AS cprice FROM reminders r "
                "LEFT JOIN companies c ON c.id=r.cid "
                "WHERE r.qq=? ORDER BY r.hit, r.id DESC LIMIT ?", (str(qq), int(limit)))
    out = []
    for r in rows:
        out.append({"id": r["id"], "cid": r["cid"], "name": r["cname"] or ("#%d" % r["cid"]),
                    "kind": r["kind"], "price": market.px(r["tprice"]),
                    "cur": market.px(r["cprice"]) if r["cprice"] is not None else 0,
                    "hit": r["hit"], "hit_at": r["hit_at"], "created_at": r["created_at"]})
    return out


def del_reminder(db, qq, rid):
    cur = db.ex("DELETE FROM reminders WHERE id=? AND qq=?", (int(rid), str(qq)))
    return cur.rowcount > 0


def scan_reminders(db, cfg):
    """行情推进后调用：把到价的提醒标记为已触发。返回本次触发的列表。"""
    rows = db.q("SELECT r.id, r.qq, r.cid, r.kind, r.price AS tprice, c.price AS cprice, c.name AS cname "
                "FROM reminders r JOIN companies c ON c.id=r.cid WHERE r.hit=0")
    fired = []
    for r in rows:
        price = float(r["tprice"] or 0)
        cur = float(r["cprice"] or 0)
        hit = (r["kind"] == "above" and cur >= price) or (r["kind"] == "below" and cur <= price)
        if hit:
            db.ex("UPDATE reminders SET hit=1, hit_at=? WHERE id=?", (int(time.time()), r["id"]))
            fired.append({"id": r["id"], "qq": r["qq"], "cid": r["cid"], "name": r["cname"],
                          "kind": r["kind"], "price": price, "now": cur})
    return fired


def pending_alerts(db, qq, limit=10):
    """最近 24 小时内触发、还没被领走的提醒。"""
    rows = db.q("SELECT r.id, r.cid, r.kind, r.price AS tprice, r.hit_at, "
                "c.name AS cname, c.price AS cprice FROM reminders r "
                "LEFT JOIN companies c ON c.id=r.cid "
                "WHERE r.qq=? AND r.hit=1 AND r.hit_at > ? ORDER BY r.hit_at DESC LIMIT ?",
                (str(qq), int(time.time()) - 86400, int(limit)))
    return [{"id": r["id"], "cid": r["cid"], "name": r["cname"] or ("#%d" % r["cid"]),
             "kind": r["kind"], "price": market.px(r["tprice"]),
             "now": market.px(r["cprice"]) if r["cprice"] is not None else 0,
             "hit_at": r["hit_at"]} for r in rows]


# ---------------------------------------------------------------- 榜单
def _lose_board(rank, cfg, limit):
    """出局榜 + 亏钱榜。

    群友反馈「看不到出局或者亏完的人」——原来的榜只有富豪榜/盈亏榜，
    赢家有存在感，输家没有。这里补一张：已经破产清算的排前面（出局榜），
    后面是还在玩、但净值已经低于初始本金的（亏钱榜）。
    """
    init = float(cfg.get("initial_cash", 100000) or 100000)
    for it in rank:
        it["loss"] = round(init - float(it["equity"]), 2)
    out = sorted([x for x in rank if x.get("bankrupt")], key=lambda x: float(x["equity"]))
    los = sorted([x for x in rank
                  if not x.get("bankrupt") and float(x["equity"]) < init],
                 key=lambda x: float(x["equity"]))
    items = []
    for it in out[:limit]:
        it["tag"] = "out"
        items.append(it)
    for it in los[:limit]:
        it["tag"] = "loss"
        items.append(it)
    for i, it in enumerate(items, 1):
        it["rank"] = i
    return items


def boards(db, cfg, kind="rich", limit=10):
    """各类榜单。rich/profit/lose 按玩家，hot/limit 按个股。"""
    kind = (kind or "rich").lower()
    if kind in ("hot", "limit", "rise", "zhangting"):
        rows = db.q("SELECT id, name, ticker, industry, qq, price, prev_close, open, high, low "
                    "FROM companies")
        items = []
        for r in rows:
            chg = (r["price"] / r["prev_close"] - 1.0) * 100 if r["prev_close"] else 0.0
            items.append({"id": r["id"], "name": r["name"], "ticker": r["ticker"],
                          "industry": r["industry"], "price": market.px(r["price"]),
                          "chg": round(chg, 2), "high": market.px(r["high"]),
                          "low": market.px(r["low"])})
        if kind in ("limit", "zhangting"):
            lim = float(cfg.get("price_limit", 0.1)) * 100.0
            items = [x for x in items if x["chg"] >= lim - 0.3]
            items.sort(key=lambda x: x["chg"], reverse=True)
        else:
            items.sort(key=lambda x: x["chg"], reverse=True)
        for i, it in enumerate(items[:limit], 1):
            it["rank"] = i
        return items
    if kind in ("lose", "loss", "ku", "shichang", "bankrupt", "chujv"):
        return _lose_board(market.ranking(db, cfg, 1000), cfg, limit)
    if kind in ("charity", "scatter", "sancai"):
        # 散财榜：谁撒出去的钱最多。这张榜是给「有钱人」的存在感，
        # 也是给「穷人」看的希望 —— 富豪榜只让人焦虑，散财榜让人想参与。
        rows = db.q("SELECT l.qq AS qq, u.nick AS nick, "
                    "COALESCE(SUM(-l.delta),0) AS s FROM ledger l "
                    "LEFT JOIN users u ON u.qq=l.qq "
                    "WHERE l.reason LIKE '撒钱派发%' GROUP BY l.qq "
                    "ORDER BY s DESC LIMIT ?", (limit,))
        items = []
        for i, r in enumerate(rows, 1):
            amt = round(float(r["s"] or 0), 2)
            items.append({"rank": i, "qq": r["qq"], "nick": r["nick"] or r["qq"],
                          "equity": 0.0, "cash": 0.0, "loan": 0.0,
                          "pl": amt, "amount": amt, "bankrupt": False, "jobs": 0})
        return items
    if kind in ("rob", "daqiu", "dajie"):
        # 【v4.7】打土豪战果榜：谁从富豪兜里抢到过钱。
        return tycoon.rob_board(db, cfg, limit)
    if kind in ("lux", "huihuo", "shop"):
        # 【v4.7】挥霍榜：谁花得最阔（富豪存在感）。
        return tycoon.lux_board(db, cfg, limit)
    rank = market.ranking(db, cfg, 200)
    if kind in ("profit", "pl", "yingkui"):
        rank = sorted(rank, key=lambda x: x["pl"], reverse=True)
    for i, it in enumerate(rank, 1):
        it["rank"] = i
        it["tier"] = tiers.tier_name(it["equity"])
    return rank[:limit]


def board_text(db, cfg, kind="rich", limit=10):
    if kind in ("lose", "loss", "ku", "shichang", "bankrupt", "chujv"):
        return _lose_text(db, cfg, kind, limit)
    if kind in ("charity", "scatter", "sancai"):
        items = boards(db, cfg, kind, limit)
        lines = ["\U0001F9E7 散财榜（累计撒钱）"]
        if not items:
            return "\U0001F9E7 散财榜\n（还没有人撒过钱，富豪们该行动了）"
        for it in items:
            lines.append("%d. %s  撒出 %.0f 元" % (
                it["rank"], it.get("nick") or it["qq"], it["amount"]))
        lines.append("👉 发「撒钱 金额」把钱撒给最穷的那批群友")
        return "\n".join(lines)
    if kind in ("rob", "daqiu", "dajie"):
        items = boards(db, cfg, kind, limit)
        lines = ["🦹 打土豪战果榜（累计抢到的钱）"]
        if not items:
            return "🦹 打土豪战果榜\n（还没有人从富豪兜里抢到过钱 —— 你可以当第一个）"
        for it in items:
            lines.append("%d. %s  抢到 %.0f 元" % (
                it["rank"], it.get("nick") or it["qq"], it["amount"]))
        lines.append("👉 发「打土豪 昵称」试试手气（翻车也赔，但群里爱看这出戏）")
        return "\n".join(lines)
    if kind in ("lux", "huihuo", "shop"):
        items = boards(db, cfg, kind, limit)
        lines = ["🛍 挥霍榜（累计消费）"]
        if not items:
            return "🛍 挥霍榜\n（还没有人挥霍过 —— 有钱人的快乐还没人解锁）"
        for it in items:
            lines.append("%d. %s  花了 %.0f 元" % (
                it["rank"], it.get("nick") or it["qq"], it["amount"]))
        lines.append("👉 发「挥霍 豪宅」消费换声望，声望能降低被打劫的概率")
        return "\n".join(lines)
    title = {"rich": "群内富豪榜", "profit": "群内盈亏榜",
             "hot": "今日涨幅榜", "limit": "今日涨停榜"}.get(kind, "群内富豪榜")
    items = boards(db, cfg, kind, limit)
    lines = ["📊 " + title]
    if not items:
        return "📊 " + title + "\n（暂时还没有数据）"
    for it in items:
        if kind in ("hot", "limit"):
            lines.append("%d. %s(%s) %.2f  %+.2f%%" % (it["rank"], it["name"], it["ticker"],
                                                       it["price"], it["chg"]))
        else:
            lines.append("%d. %s  净值 %.2f  盈亏 %+.2f  %s%s" % (
                it["rank"], it.get("nick") or it["qq"], it["equity"], it["pl"],
                tiers.tier_name(it["equity"]),
                "  💀" if it.get("bankrupt") else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- 公司互动
def _lose_text(db, cfg, kind, limit):
    """出局榜 / 亏钱榜的文本版。"""
    items = boards(db, cfg, kind, limit)
    outs = [x for x in items if x.get("tag") == "out"]
    loss = [x for x in items if x.get("tag") == "loss"]
    lines = ["\U0001F480 出局榜（已破产清算）"]
    if not outs:
        lines.append("（暂时还没有人破产，大家都很稳）")
    for it in outs:
        lines.append("%d. %s  净值 %.2f  累计亏损 %.0f 元" % (
            it["rank"], it.get("nick") or it["qq"], it["equity"], it["loss"]))
    lines.append("\U0001F4B8 亏钱榜（还在玩）")
    if not loss:
        lines.append("（大家都还赚着，挺好）")
    for it in loss:
        lines.append("%d. %s  净值 %.2f  累计亏损 %.0f 元" % (
            it["rank"], it.get("nick") or it["qq"], it["equity"], it["loss"]))
    lines.append("\U0001F449 发「我的股票」看看自己还剩多少")
    return "\n".join(lines)


def company_boost(db, cfg, qq, cid):
    """给自己的公司花钱做宣传，股价拉升。"""
    qq = str(qq)
    comp = db.q1("SELECT * FROM companies WHERE id=? AND qq=?", (int(cid), qq))
    if comp is None:
        return False, "这不是你的公司", None
    # 【P1 漏洞修复】自己持股时不能给自己公司宣传。
    # 宣传是「直接把股价 x1.03」，等于白送 3% 的确定性涨幅。
    # 老板自己持有多头的话，宣传完立刻平仓就能把这 3% 变成纯利：
    # 2026-11-18 实锤 —— Anyisdragon 00:13:07 建多 2.5 万股，
    # 00:13:15 给自己公司宣传（+3%），00:13:33 平仓一把赚 10027 元。
    # 现在持股就禁宣传：想给群友抬轿子可以，抬完自己吃不行。
    held = db.q1("SELECT COUNT(*) c FROM positions "
                 "WHERE qq=? AND cid=? AND status='open'", (qq, int(cid)))
    if held and int(held["c"] or 0) > 0:
        return False, ("你自己还拿着「%s」的股票，不能一边持仓一边给自己抬轿子。"
                       "先把仓位平掉再来宣传。" % comp["name"]), None
    cost = float(cfg.get("boost_cost", 1500.0))
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None or user["cash"] < cost:
        return False, "宣传费 %.0f 元，钱不够" % cost, None
    n = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND reason LIKE '公司宣传%' AND ts>=?",
              (qq, day_start()))
    if n and n["c"] >= 3:
        return False, "今天已经宣传过 3 次了，明天再来", None
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (cost, qq))
    _ledger(db, qq, -cost, "公司宣传 " + comp["name"])
    lim = float(cfg.get("price_limit", 0.1))
    base = comp["prev_close"] or comp["price"]
    target = comp["price"] * 1.03
    hi = base * (1 + lim)
    newp = round(min(target, hi), 2)
    db.ex("UPDATE companies SET price=?, high=MAX(high,?) WHERE id=?", (newp, newp, comp["id"]))
    db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact) VALUES(?,?,?,?,?,?,?)",
          (int(time.time()), comp["id"], comp["ticker"], "company",
           "%s 花钱做了场大型营销，股价应声上涨" % comp["name"],
           "%s 宣布投放 1 亿广告费，老板亲自出镜代言。股价拉升 %.2f%%" % (
               comp["name"], (newp / comp["price"] - 1) * 100), 0.03))
    return True, "%s 宣传成功，花了 %.0f 元，股价 %.2f -> %.2f（%+.2f%%）" % (
        comp["name"], cost, comp["price"], newp, (newp / comp["price"] - 1) * 100), \
        {"price": newp}


def company_dividend(db, cfg, qq, cid, amount=None):
    """给自己的公司派息：自己掏钱，给持股的群友发钱。"""
    qq = str(qq)
    comp = db.q1("SELECT * FROM companies WHERE id=? AND qq=?", (int(cid), qq))
    if comp is None:
        return False, "这不是你的公司", None
    holders = db.q("SELECT DISTINCT qq FROM positions WHERE cid=? AND status='open'", (comp["id"],))
    if not holders:
        return False, "你的公司现在没人持股，派了也白派", None
    per = float(amount) if amount else float(cfg.get("dividend_per_holder", 600.0))
    total = per * len(holders)
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None or user["cash"] < total:
        return False, "派息需要 %.0f 元（%d 个股东 × %.0f），钱不够" % (total, len(holders), per), None
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (total, qq))
    _ledger(db, qq, -total, "公司派息 " + comp["name"])
    for h in holders:
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (per, h["qq"]))
        _ledger(db, h["qq"], per, "收到 %s 的分红" % comp["name"])
    db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact) VALUES(?,?,?,?,?,?,?)",
          (int(time.time()), comp["id"], comp["ticker"], "company",
           "%s 宣布派息，每位股东 %.0f 元" % (comp["name"], per),
           "%s 的老板太大方了，给 %d 位股东派了 %.0f 元。" % (comp["name"], len(holders), total), 0.0))
    return True, "%s 派息完成：%d 位股东各拿 %.0f 元，你花了 %.0f 元。" % (
        comp["name"], len(holders), per, total), {"per": per, "total": total}


# ---------------------------------------------------------------- 抄作业
def copy_trade(db, cfg, qq, target_qq, ratio=0.5):
    """抄作业：按目标用户持仓市值的一定比例复制他的仓位。"""
    qq = str(qq)
    target_qq = str(target_qq)
    if qq == target_qq:
        return False, "抄自己的作业有什么意义", None
    tgt = db.q1("SELECT * FROM users WHERE qq=?", (target_qq,))
    if tgt is None:
        return False, "没找到这位群友", None
    pos = db.q("SELECT p.*, c.price, c.name FROM positions p JOIN companies c ON c.id=p.cid "
               "WHERE p.qq=? AND p.status='open'", (target_qq,))
    if not pos:
        return False, "%s 现在空仓，没什么可抄" % (tgt["nick"] or target_qq), None
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return False, "你还没开户", None
    ratio = max(0.05, min(1.0, float(ratio)))
    total_target = sum(p["margin"] for p in pos)
    budget = min(me["cash"] * 0.5, float(cfg.get("initial_cash", 100000.0)) * ratio)
    # budget 以前算出来根本没用上：抄作业会按对方仓位的 ratio 全额下单，
    # 「最多花一半现金 / 初始资金 × ratio」这条限制形同虚设。
    # 现在按比例缩放，保证总额不超过 budget。
    total_want = total_target * ratio
    scale = min(1.0, budget / total_want) if total_want > 0 else 0.0
    lines = []
    done = 0
    for p in pos:
        want = p["margin"] * ratio * scale
        if want <= 0:
            continue
        price = p["price"]
        # 不按整手取整：对方仓位小的时候整手会直接归零，导致「抄不动」
        qty = int(want * p["leverage"] / price)
        if qty <= 0:
            continue
        okk, msg, _ = market.place_order(db, cfg, qq, p["cid"], p["side"], qty, p["leverage"])
        if okk:
            done += 1
            lines.append("%s %s %d 股 @%.2f" % ("做多" if p["side"] == "long" else "做空",
                                              p["name"], qty, price))
    if not done:
        return False, "对方仓位太小或者你钱不够，抄不动", None
    return True, "已抄 %s 的作业：\n%s" % (tgt["nick"] or target_qq, "\n".join(lines)), \
        {"count": done}


# ---------------------------------------------------------------- 每日复盘
def daily_report(db, cfg, day=None):
    """生成（或读取）某一天的复盘快照。

    【2026-11-18 修】两处：
    1. 当天不再读缓存、也不再写缓存。原来这里在「跨天那一 tick」就会给
       当天建一份快照，而那一刻引擎刚把 price 和 prev_close 刷成同一个值
       （v4.4 去掉开盘跳空之后），于是快照里的涨跌恒为 0、涨停恒为 0；
       更糟的是它被缓存了，之后一整天都返回这份废数据 ——
       群里的「今日复盘」涨跌幅榜永远是空的。
       现在当天一律现场算：此刻 prev_close 还是昨天的收盘，涨跌是对的。
    2. 过去的某一天改从 daily_px 取数（跨日瞬间存下的收盘快照），
       拿到的才是当天真正的「收盘对收盘」涨跌。
    """
    day = day or today()
    is_today = (day == today())
    snapshot = []
    if not is_today:
        # 过去的某一天：优先用跨日瞬间存下的收盘快照（engine._snapshot_day）。
        # 【2026-11-18 修】旧逻辑先读 daily 缓存行再取快照，导致 v4.4 之前
        # 写下的那份「涨跌恒为 0」的废快照盖住了真正的收盘数据。
        # 现在顺序反过来：有快照就算并刷新缓存，没有快照才回退缓存行。
        snapshot = db.q("SELECT name, ticker, price, prev_close FROM daily_px "
                        "WHERE day=? ORDER BY cid", (day,))
        if not snapshot:
            row = db.q1("SELECT * FROM daily WHERE day=?", (day,))
            if row:
                try:
                    return json.loads(row["payload"])
                except Exception:
                    pass
    # 现场算一份
    if is_today:
        companies = db.q("SELECT id, name, ticker, price, prev_close, high, low FROM companies")
    else:
        # 过去的某一天：用跨日瞬间存下的收盘快照（engine._snapshot_day）
        companies = snapshot
        if not companies:
            companies = db.q("SELECT id, name, ticker, price, prev_close, high, low FROM companies")
    ups = []
    downs = []
    limit_up = 0
    lim = float(cfg.get("price_limit", 0.1)) * 100.0
    for c in companies:
        chg = (c["price"] / c["prev_close"] - 1.0) * 100 if c["prev_close"] else 0.0
        item = {"name": c["name"], "ticker": c["ticker"], "price": market.px(c["price"]),
                "chg": round(chg, 2)}
        ups.append(item)
        if chg >= lim - 0.3:
            limit_up += 1
    ups.sort(key=lambda x: x["chg"], reverse=True)
    downs = sorted(ups, key=lambda x: x["chg"])
    rank = market.ranking(db, cfg, 10)
    if is_today:
        trades = db.q1("SELECT COUNT(*) c FROM trades WHERE ts>=?", (day_start(),))
    else:
        try:
            ts0 = int(time.mktime(time.strptime(day, "%Y-%m-%d") + (0, 0, 0)))
        except Exception:
            ts0 = day_start()
        trades = db.q1("SELECT COUNT(*) c FROM trades WHERE ts>=? AND ts<?", (ts0, ts0 + 86400))
    payload = {
        "day": day,
        "top": ups[:10],
        "bottom": downs[-10:][::-1],
        "limit_up": limit_up,
        "total": len(companies),
        "rank": [{"nick": r["nick"] or r["qq"], "equity": r["equity"], "pl": r["pl"]} for r in rank],
        "trades": trades["c"] if trades else 0,
        "created_at": int(time.time()),
    }
    if not is_today:
        db.ex("INSERT INTO daily(day,payload,created_at) VALUES(?,?,?) "
              "ON CONFLICT(day) DO UPDATE SET payload=excluded.payload, created_at=excluded.created_at",
              (day, json.dumps(payload, ensure_ascii=False), int(time.time())))
    return payload


def daily_text(db, cfg, day=None):
    p = daily_report(db, cfg, day)
    lines = ["📅 %s 收盘复盘" % p["day"],
             "全市场 %d 家公司，涨停 %d 家，今日成交 %d 笔" % (p["total"], p["limit_up"], p["trades"]),
             "—— 涨幅榜 ——"]
    for i, it in enumerate(p["top"][:5], 1):
        lines.append("%d. %s(%s) %.2f %+.2f%%" % (i, it["name"], it["ticker"], it["price"], it["chg"]))
    lines.append("—— 富豪榜 ——")
    for i, r in enumerate(p["rank"][:5], 1):
        lines.append("%d. %s 净值 %.2f（%+.2f）" % (i, r["nick"], r["equity"], r["pl"]))
    return "\n".join(lines)


# -*- coding: utf-8 -*-
"""群友 v4 需求：改名 / 搜股 / 商业战（坑害他人公司）。"""


# ---------------------------------------------------------------- 改名
RENAME_MIN = 2
RENAME_MAX = 12
BAD_CHARS = set(u'<>@/\\|"\'`\n\r\t#@')


def company_rename(db, cfg, qq, cid, new_name):
    """给自己的公司改名。

    * 只能改自己名下的公司
    * 改名费 configurable（默认 3000），24 小时内只能改一次
    * 名字 2~12 字，不能撞名
    * 改完写一条快讯，让群友看到
    """
    qq = str(qq)
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return False, "公司编号不对", None
    comp = db.q1("SELECT * FROM companies WHERE id=?", (cid,))
    if comp is None:
        return False, "找不到这家公司", None
    if str(comp["qq"]) != qq:
        return False, "这不是你的公司，想改名先自己开一家（指令：开户）", None
    name = u" ".join(str(new_name or "").split())
    if len(name) < RENAME_MIN or len(name) > RENAME_MAX:
        return False, u"公司名要 %d~%d 个字" % (RENAME_MIN, RENAME_MAX), None
    for ch in name:
        if ch in BAD_CHARS or ord(ch) < 32:
            return False, u"名字里有不能用的符号，换个朴素点的", None
    if name == comp["name"]:
        return False, u"新名字和现在一样", None
    dup = db.q1("SELECT id FROM companies WHERE name=? AND id<>?", (name, cid))
    if dup:
        return False, u"「%s」这个名字已经被人占了" % name, None
    cost = float(cfg.get("rename_cost", 3000.0))
    user = db.q1("SELECT cash FROM users WHERE qq=?", (qq,))
    if user is None or float(user["cash"]) < cost:
        return False, u"改名费 %.0f 元，钱不够" % cost, None
    last = db.q1("SELECT ts FROM ledger WHERE qq=? AND reason LIKE '公司改名%' "
                 "ORDER BY id DESC LIMIT 1", (qq,))
    if last and int(time.time()) - int(last["ts"]) < 86400:
        wait = 86400 - (int(time.time()) - int(last["ts"]))
        return False, u"刚改过名，%d 小时后再来" % (wait // 3600 + 1), None
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (cost, qq))
    _ledger(db, qq, -cost, u"公司改名费")
    old = comp["name"]
    db.ex("UPDATE companies SET name=? WHERE id=?", (name, cid))
    db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact) VALUES(?,?,?,?,?,?,?)",
          (int(time.time()), cid, comp["ticker"], "company",
           u"%s 公司更名为 %s" % (old, name),
           u"%s 正式将公司名称变更为「%s」，改名费 %.0f 元。群友：这名字谁取得" % (old, name, cost),
           0.0))
    return True, u"改名成功！你的公司现在叫「%s」（改名费 %.0f 元，24 小时内不能再改）" % (name, cost), \
        {"name": name, "old": old}


# ---------------------------------------------------------------- 搜股
def search_companies(db, cfg, qq, keyword, limit=15):
    """按名称 / 代码 / 行业搜股票。"""
    kw = u" ".join(str(keyword or "").split())
    if not kw:
        return []
    like = "%" + kw + "%"
    rows = db.q("SELECT id, name, ticker, industry, price, prev_close, qq FROM companies "
                "WHERE name LIKE ? OR ticker LIKE ? OR industry LIKE ?", (like, like, like))
    items = []
    for r in rows:
        chg = (r["price"] / r["prev_close"] - 1.0) * 100 if r["prev_close"] else 0.0
        items.append({"id": r["id"], "name": r["name"], "ticker": r["ticker"],
                      "industry": r["industry"], "price": market.px(r["price"]),
                      "chg": round(chg, 2), "mine": str(r["qq"]) == str(qq)})

    def score(x):
        if x["name"] == kw:
            rank = 0
        elif x["name"].startswith(kw):
            rank = 1
        elif kw in x["name"]:
            rank = 2
        elif x["ticker"].startswith(kw):
            rank = 3
        elif x["industry"] == kw:
            rank = 4
        elif kw in x["industry"]:
            rank = 5
        else:
            rank = 6
        return (rank, -abs(x["chg"]), x["id"])

    items.sort(key=score)
    return items[:max(1, int(limit or 15))]


def search_text(db, cfg, qq, keyword, limit=10):
    """群里「搜股」的文字版。"""
    items = search_companies(db, cfg, qq, keyword, limit)
    if not items:
        return u"🔍 没找到和「%s」有关的股票" % keyword
    lines = [u"🔍 搜索「%s」，共 %d 只：" % (keyword, len(items))]
    for it in items:
        mine = u" 🏠我的" if it["mine"] else ""
        lines.append(u"%s(%s) %.2f %+.2f%% [%s]%s" % (
            it["name"], it["ticker"], it["price"], it["chg"], it["industry"], mine))
    return "\n".join(lines)


# ---------------------------------------------------------------- 商业战
SABOTAGE_PLANS = [
    {"key": "rumor", "name": u"放黑料", "emoji": u"🗯", "cost": 2000, "rate": 0.55,
     "win": (-0.07, -0.03), "lose": (0.01, 0.03), "fine": 0,
     "win_story": u"{att} 不知从哪搞到了内部消息，{tgt} 的客户开始动摇",
     "lose_story": u"{att} 放的黑料被 {tgt} 拿出原始文件打了脸"},
    {"key": "poach", "name": u"挖墙脚", "emoji": u"🕴", "cost": 2500, "rate": 0.50,
     "win": (-0.05, -0.02), "lose": (0.00, 0.02), "fine": 500,
     "win_story": u"{att} 把 {tgt} 的大客户悄悄签走了",
     "lose_story": u"{att} 挖人不成，反被 {tgt} 顺手挖走了俩客户"},
    {"key": "short", "name": u"做空狙击", "emoji": u"📉", "cost": 3000, "rate": 0.58,
     "win": (-0.09, -0.05), "lose": (0.01, 0.04), "fine": 1500,
     "win_story": u"{att} 偷偷建仓做空，{tgt} 盘中跳水",
     "lose_story": u"{att} 的空头被 {tgt} 反手轧空，亏得比谁都惨"},
    {"key": "report", "name": u"举报财务造假", "emoji": u"🚨", "cost": 3500, "rate": 0.30,
     "win": (-0.12, -0.08), "lose": (-0.01, 0.01), "fine": 4000,
     "win_story": u"监管部门突然介入，{tgt} 被约谈",
     "lose_story": u"举报材料全是编的，{att} 反被监管罚款"},
    # 【v4.6】资本碾压：大额档。出手费不是固定值，而是按自己身家算
    # （净值的 2%，2 万~20 万封顶），成功率只有 45%，但砸下去是 -8%~-16%。
    # 给「有钱」这件事一个可以主动花出去、也可能血本无归的出口 ——
    # 钱只有流动起来才会产生故事，躺着只会变成数字。
    {"key": "crush", "name": u"资本碾压", "emoji": u"💣", "cost": 0, "rate": 0.45,
     "dynamic": True,
     "win": (-0.16, -0.08), "lose": (0.01, 0.03), "fine": 0,
     "win_story": u"{att} 带着大笔资金砸向 {tgt}，股价被强行打了下来",
     "lose_story": u"{att} 的钱打了水漂，{tgt} 反而趁机吸筹，股价不跌反涨"},
]


def _apply_impact(db, cfg, cid, impact):
    """把一个百分比冲击打到某只票上（带涨跌停保护）。"""
    row = db.q1("SELECT price, prev_close FROM companies WHERE id=?", (cid,))
    if not row:
        return None
    lim = max(0.01, min(0.5, float(cfg.get("price_limit", 0.1))))
    newp = max(0.5, row["price"] * (1 + impact))
    base = row["prev_close"] or row["price"]
    lo, hi = base * (1 - lim), base * (1 + lim)
    if lo <= row["price"] <= hi:
        newp = min(max(newp, lo), hi)
    newp = round(newp, int(cfg.get("price_precision", 4) or 4))
    db.ex("UPDATE companies SET price=?, high=MAX(high,?), low=MIN(low,?) WHERE id=?",
          (newp, newp, newp, cid))
    return newp


def sabotage_count_today(db, qq):
    # 只数「出手费」那几笔，罚单不能算成一次出手；
    # 「资本碾压」有自己的每日限额，不占普通商业战的次数。
    row = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? "
                "AND reason LIKE '商业战出手%' AND reason NOT LIKE '商业战出手·资本碾压%'",
                (str(qq), day_start()))
    return int(row["c"]) if row else 0


def find_company(db, keyword):
    """按名字 / 代码找一家公司（精确优先，其次包含）。"""
    kw = u" ".join(str(keyword or "").split())
    if not kw:
        return None
    row = db.q1("SELECT * FROM companies WHERE ticker=? OR name=? LIMIT 1", (kw, kw))
    if row:
        return row
    rows = db.q("SELECT * FROM companies WHERE name LIKE ? OR ticker LIKE ? LIMIT 12",
                ("%" + kw + "%", "%" + kw + "%"))
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (0 if r["name"] == kw else 1, len(r["name"])))
    return rows[0]


def company_sabotage(db, cfg, qq, cid, plan_key=None):
    """商业战：花 money 搞别人的公司。

    有成功率、也会失败反噬，失败一样出快讯（群友爱看剧情）。
    护栏：每日次数上限、费用不低、目标越热门越难搞。
    """
    qq = str(qq)
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return False, u"公司编号不对", None
    target = db.q1("SELECT * FROM companies WHERE id=?", (cid,))
    if target is None:
        return False, u"找不到这家公司", None
    if str(target["qq"]) == qq:
        return False, u"自己搞自己？那叫会计，不需要商业战", None
    # 【P0 漏洞修复】在这只票上有仓位时不能砸它。
    # 老逻辑只禁「砸自己名下的公司」，不禁「砸自己持有仓位的公司」——
    # 于是「先开空头 → 自己花钱砸盘 → 股价跌 → 平仓」成了确定性印钞机：
    # 成本 3000，成功率 58%，一次赚 5%~9% 的仓位。
    # 2026-11-18 实锤两例：
    #   Anyisdragon 00:03:59 开空白一川汽车…（实为早元熊光地产）
    #   00:04:15 自己砸盘 -5.3% → 00:04:32 平仓净赚 30656（成本 2000）；
    #   Kaito 00:46:03 开空 Anyisdragon服装 → 00:46:18 自己砸盘 -5.55%
    #   → 00:46:24 平仓净赚 378250（成本 3000）。
    held = db.q1("SELECT COUNT(*) c FROM positions "
                 "WHERE qq=? AND cid=? AND status='open'", (qq, cid))
    if held and int(held["c"] or 0) > 0:
        return False, (u"你在「%s」上还有仓位，不能一边持仓一边砸盘。"
                       u"这叫操纵市场，交易所盯着呢。" % target["name"]), None
    plans = SABOTAGE_PLANS
    if plan_key:
        # 群里说的可能是手段名（「资本碾压」），网页上传的是 key（crush），两种都认
        picked = [p for p in plans if p["key"] == plan_key or p["name"] == plan_key]
        if not picked:
            return False, u"没有这种手段", None
        plan = picked[0]
    else:
        # 随机出手不抽「资本碾压」—— 那一档要玩家点名、有钱才打得响
        plan = random.choice([p for p in plans if not p.get("dynamic")])
    if plan.get("dynamic"):
        # 【v4.6】资本碾压：出手费按自己身家算，越有钱砸得越狠。
        # 成功率只有 45%，砸下去是 -8%~-16%，失败则血本无归 + 对手上涨 ——
        # 这是给「有钱的人」一个可以主动花出去、也可能一把梭哈的出口。
        acct = market.account(db, cfg, qq)
        equity = max(0.0, acct["equity"]) if acct else 0.0
        min_eq = float(cfg.get("big_sabotage_min_equity", 50000))
        if equity < min_eq:
            return False, (u"资本碾压至少要有 %.0f 块身家才玩得转（你现在净值 %.0f）"
                           u"—— 先老老实实炒股攒钱。" % (min_eq, equity)), None
        cost = equity * float(cfg.get("big_sabotage_equity_ratio", 0.02))
        cost = max(cost, float(cfg.get("big_sabotage_min", 20000)))
        cost = min(cost, float(cfg.get("big_sabotage_max", 200000)))
        cost = round(cost, 2)
        big_limit = int(cfg.get("big_sabotage_daily_limit", 1) or 1)
        used_big = db.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? "
                         "AND reason LIKE '商业战出手·资本碾压%'", (qq, day_start()))
        if used_big and int(used_big["c"] or 0) >= big_limit:
            return False, u"今天你已经用过一次资本碾压了，明天再开炮", None
        plan = dict(plan)
        plan["cost"] = cost
    else:
        limit = int(cfg.get("sabotage_daily_limit", 2) or 2)
        used = sabotage_count_today(db, qq)
        if used >= limit:
            return False, u"今天已经出手 %d 次了（每日上限 %d 次），明天再算账" % (used, limit), None
    user = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if user is None or float(user["cash"] or 0) < plan["cost"]:
        return False, u"出手要 %.0f 元，钱不够" % plan["cost"], None
    if user["bankrupt"]:
        return False, u"破产纾困期间不能商业战，先把自己的债还清", None

    # 目标越热门越难搞：成交额越大成功率越低
    heat = db.q1("SELECT SUM(price*qty) s FROM trades WHERE cid=? AND ts>=?",
                 (cid, day_start()))
    turnover = float(heat["s"] or 0) if heat else 0.0
    if plan.get("dynamic"):
        # 资本碾压是大资金硬砸，人气高会稍微拖点后腿，但不至于打不动
        rate = plan["rate"] * max(0.60, 1.0 - turnover / 800000.0)
    else:
        rate = plan["rate"] * max(0.40, 1.0 - turnover / 400000.0)
    win = random.random() < rate

    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (plan["cost"], qq))
    _ledger(db, qq, -plan["cost"], u"商业战出手·%s" % plan["name"])

    att = db.q1("SELECT nick FROM users WHERE qq=?", (qq,))
    att_name = (att["nick"] if att and att["nick"] else u"群友" + qq[-3:])
    tgt_name = target["name"]

    if win:
        impact = random.uniform(plan["win"][0], plan["win"][1])
        newp = _apply_impact(db, cfg, cid, impact)
        story = plan["win_story"].format(att=att_name, tgt=tgt_name)
        title = u"%s 对 %s 出手成功" % (att_name, tgt_name)
        extra = 0
    else:
        impact = random.uniform(plan["lose"][0], plan["lose"][1])
        newp = _apply_impact(db, cfg, cid, impact)
        story = plan["lose_story"].format(att=att_name, tgt=tgt_name)
        title = u"%s 对 %s 出手翻车" % (att_name, tgt_name)
        extra = float(plan.get("fine", 0) or 0)
        if extra:
            user = db.q1("SELECT cash FROM users WHERE qq=?", (qq,))
            pay = min(extra, float(user["cash"]))
            db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (pay, qq))
            _ledger(db, qq, -pay, u"商业战罚单")

    db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact) VALUES(?,?,?,?,?,?,?)",
          (int(time.time()), cid, target["ticker"], "company", title, story, impact))
    db.ex("DELETE FROM news WHERE ts < ?", (int(time.time()) - 3600 * 6,))

    pct = (newp / target["price"] - 1) * 100 if target["price"] and newp else 0.0
    verb = u"得手" if win else u"翻车"
    msg = (u"%s %s %s：%s\n%s 股价 %.2f -> %.2f（%+.2f%%）\n"
           u"（成功率 %.0f%%，出手费 %.0f%s）" % (
               plan["emoji"], plan["name"], verb, story, tgt_name,
               target["price"], newp or 0, pct, rate * 100, plan["cost"],
               u"，罚单 %.0f" % extra if extra else u""))
    return True, msg, {"win": win, "cid": cid, "name": tgt_name, "price": newp,
                       "chg": round(pct, 2), "rate": round(rate * 100, 1),
                       "plan": plan["name"], "cost": plan["cost"], "fine": extra}


def sabotage_plans_text(cfg=None):
    cfg = cfg or {}
    lines = [u"🗡 商业战手段（指令：坑 昵称/代码 [手段]）："]
    for p in SABOTAGE_PLANS:
        if p.get("dynamic"):
            lines.append(u"%s %s：出手费 = 你的净值×%.0f%%（%s 万 ~ %s 万），基础成功率 %d%%" % (
                p["emoji"], p["name"],
                float(cfg.get("big_sabotage_equity_ratio", 0.02)) * 100,
                int(float(cfg.get("big_sabotage_min", 20000)) / 10000),
                int(float(cfg.get("big_sabotage_max", 200000)) / 10000),
                int(p["rate"] * 100)))
        else:
            lines.append(u"%s %s：出手费 %d 元，基础成功率 %d%%" % (
                p["emoji"], p["name"], p["cost"], int(p["rate"] * 100)))
    lines.append(u"目标越热门越难搞；失手会反噬；普通商业战每天最多 %d 次" % 2)
    lines.append(u"💣 资本碾压另算：每天 %d 次，失败钱全没"
                 % int(cfg.get("big_sabotage_daily_limit", 1) or 1))
    return "\n".join(lines)

# -*- coding: utf-8 -*-
"""炒股模拟器 v3 玩法：每日任务 / 回归礼包 / 快讯 / 人气榜 / 打赏 / 每日播报。

为什么做 v3
------------
v2 把「打工比炒股赚得多」修好了（打工收益压到炒股的 ~1%），
但线上真实数据暴露了更致命的问题：

    主群 216 个开户用户，全历史只有 254 笔成交、127 次持仓。
    平均每人一辈子下过 1 次单。签到 0 人、提醒 0 条、成就 4 个。

也就是说：**不是玩法不够多，是没有「今天必须回来」的理由。**
开完户、看完一眼盘，人就不回来了。

v3 的思路是给玩家造一个「日更仪式」，再把群友之间连起来：

  * 每日任务  每天随机 3 件小事（签到/成交/查盘/看快讯/打工/设提醒/持仓/平仓），
              做完自动发钱 —— 制造「今天还有事没干」的心理缺口。
  * 回归礼包  离开 3 天以上回来，自动发一笔红包 —— 把流失用户捞回来。
  * 快讯      行情引擎每天自动生成几千条新闻（现在全躺在 news 表里没人看），
              去重后挑最新的露出来 —— 给群里制造话题。
  * 人气榜    今日成交额榜 —— 制造「大家都在买」的 FOMO。
  * 打赏      群友之间直接送钱 —— 社交粘性。
  * 每日播报  早报/收盘复盘由服务端定时推到群里（见 pusher.py）。
"""
import hashlib
import random
import time

import features
import market
import recovery
from newsdata import BOMB_TAG, cat_mark

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _ledger(db, qq, delta, reason):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    bal = row["cash"] if row else 0.0
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (str(qq), int(time.time()), round(delta, 2), round(bal, 2), reason))


def _count(db, sql, qq, *args):
    try:
        row = db.q1(sql, (qq,) + args)
    except Exception:
        return 0
    return int(row["c"]) if row else 0


# ---------------------------------------------------------------- 每日任务
def _cond_checkin(db, qq):
    row = db.q1("SELECT last_day FROM checkin WHERE qq=?", (str(qq),))
    return bool(row and row["last_day"] == features.today())


def _cond_trade(db, qq):
    # 【v4.18】small=1 = 试水仓（保证金占用低于阈值的小单）。
    # 这类单不计入任务奖励 —— 否则 20 元小单就能刷「今日首单 125」+
    # 「翻身第一笔买入 200~1200」，变成 66 倍的刷分机器。
    return _count(db, "SELECT COUNT(*) c FROM trades WHERE qq=? AND ts>=? AND small=0", qq,
                  features.day_start()) > 0


def _cond_job(db, qq):
    return _count(db, "SELECT COUNT(*) c FROM ledger WHERE qq=? AND ts>=? AND reason LIKE '打工%'",
                  qq, features.day_start()) > 0


def _cond_remind(db, qq):
    return _count(db, "SELECT COUNT(*) c FROM reminders WHERE qq=? AND created_at>=?", qq,
                  features.day_start()) > 0


def _cond_hold3(db, qq):
    # 【v4.18】试水仓不计入「同时持三只」—— 免得 3 笔 20 元小单就凑齐任务。
    return _count(db, "SELECT COUNT(*) c FROM positions WHERE qq=? AND status='open' AND small=0",
                  qq) >= 3


def _cond_close(db, qq):
    # 【v4.18】试水仓平仓同样不计入。
    return _count(db, "SELECT COUNT(*) c FROM trades WHERE qq=? AND ts>=? AND kind='close' AND small=0",
                  qq, features.day_start()) > 0


def _cond_never(db, qq):
    return False


# (code, 名称, 奖励, 描述, 判定)
QUEST_DEFS = [
    ("view", "查一次盘", 45, "打开账户看一眼行情", _cond_never),
    ("checkin", "今日签到", 95, "发一次「签到」", _cond_checkin),
    ("trade", "今日首单", 125, "今天至少成交一笔", _cond_trade),
    ("news", "看消息面", 45, "看一眼今天的快讯", _cond_never),
    ("job", "打一次工", 95, "去别人公司打零工", _cond_job),
    ("remind", "设一条提醒", 80, "给自己关心的票设个价", _cond_remind),
    ("hold3", "同时持三只", 155, "手上同时拿三只票", _cond_hold3),
    ("close", "平一次仓", 115, "卖掉一笔持仓", _cond_close),
]
QUEST_MAP = {d[0]: d for d in QUEST_DEFS}
# 全勤奖默认值。实际发放走 config.json 的 quest_all_bonus，
# 这里只是「配置里没写」时的兜底，数值要跟 config 一致（180）。
QUEST_ALL_BONUS = 180.0


# ---------------------------------------------------------------- 翻身任务（只给中下层）
# 【为什么要有这一套】打工是应急兜底，但中下层每天打工上限也就一百多块，
# 而炒股一天的波动就是打工的十倍 —— 如果不给落后玩家一条「认真炒股」的捷径，
# 理性玩家只会永远打工，群里最后就剩一个打工模拟器。
# 翻身任务只发给中下层（净值低于 rise_equity_cap），奖励按他们自己的基数给，
# 而且**必须真的下单买**才拿得到 —— 把人从打工循环拉进炒股循环。
# 打工可以刷，炒股的方向不能刷。
RISE_DEFS = [
    ("rise_trade", "第一笔买入", 0.012, "今天真的下单买一笔（这才是翻身第一步）", _cond_trade),
    ("rise_close", "落袋为安", 0.006, "今天平掉一笔持仓", _cond_close),
    ("rise_checkin", "打个卡", 0.003, "发一次「签到」", _cond_checkin),
]
RISE_MAP = {d[0]: d for d in RISE_DEFS}


def rise_active(cfg, equity):
    """只有中下层（净值没到 rise_equity_cap）才激活翻身任务。"""
    cap = float(cfg.get("rise_equity_cap", 200000))
    return float(equity or 0) < cap


def rise_reward(cfg, equity, ratio):
    """翻身任务奖励：按净值给比例，但有上下限（光棍也别嫌少，巨鲸也别刷爆）。"""
    lo = float(cfg.get("rise_reward_min", 200))
    hi = float(cfg.get("rise_reward_max", 1200))
    return float(min(hi, max(lo, float(equity or 0) * float(ratio))))


def rise_state(db, cfg, qq):
    """翻身任务清单。净值上来了就不发了 —— 那是「翻身」完了的意思。"""
    qq = str(qq)
    acct = market.account(db, cfg, qq)
    eq = acct["equity"] if acct else 0.0
    if not rise_active(cfg, eq):
        return []
    day = features.today()
    rows = {r["code"]: r for r in db.q(
        "SELECT code, done, rewarded FROM quests WHERE qq=? AND day=?", (qq, day))}
    out = []
    for code, name, ratio, desc, cond in RISE_DEFS:
        row = rows.get(code)
        done = bool(row and row["done"])
        if not done:
            try:
                done = bool(cond(db, qq))
            except Exception:
                done = False
        out.append({"code": code, "name": name,
                    "reward": rise_reward(cfg, eq, ratio),
                    "desc": desc, "done": done,
                    "rewarded": bool(row and row["rewarded"])})
    return out


def rise_settle(db, cfg, qq):
    """结算翻身任务。"""
    qq = str(qq)
    day = features.today()
    msgs = []
    for st in rise_state(db, cfg, qq):
        if not st["done"] or st["rewarded"]:
            continue
        db.ex("INSERT OR IGNORE INTO quests(qq,day,code,done,rewarded,ts) VALUES(?,?,?,1,0,?)",
              (qq, day, st["code"], int(time.time())))
        reward = st["reward"]
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (reward, qq))
        _ledger(db, qq, reward, "翻身任务 " + st["name"])
        db.ex("UPDATE quests SET rewarded=1 WHERE qq=? AND day=? AND code=?",
              (qq, day, st["code"]))
        msgs.append("🚀 翻身任务【%s】%s，奖励 %.0f 元 —— 别打工了，去下单" %
                    (st["name"], st["desc"], reward))
    return msgs


def daily_codes(qq, day):
    """每个玩家每天的任务组合：固定带一个最简单的「查盘」，其余随机。

    用 md5 而不是内置 hash()，因为内置 hash 每个进程都带随机盐，
    多进程/重启后同一个人会拿到不同的任务组合。
    """
    qq = str(qq)
    seed = int(hashlib.md5(("%s|%s" % (qq, day)).encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed)
    others = [d[0] for d in QUEST_DEFS if d[0] != "view"]
    picked = rng.sample(others, 2)
    return ["view"] + picked


def quest_state(db, cfg, qq):
    """今天该做的任务 + 完成情况。"""
    qq = str(qq)
    day = features.today()
    rows = {r["code"]: r for r in db.q("SELECT code, done, rewarded FROM quests WHERE qq=? AND day=?",
                                       (qq, day))}
    out = []
    for code in daily_codes(qq, day):
        _, name, reward, desc, cond = QUEST_MAP[code]
        row = rows.get(code)
        done = bool(row and row["done"])
        if not done:
            try:
                done = bool(cond(db, qq))
            except Exception:
                done = False
        out.append({"code": code, "name": name, "reward": float(reward), "desc": desc,
                    "done": done, "rewarded": bool(row and row["rewarded"])})
    return out


def mark_quest(db, qq, code):
    """交互型任务（查盘/看快讯）由动作本身标记完成。"""
    qq = str(qq)
    day = features.today()
    db.ex("INSERT OR IGNORE INTO quests(qq,day,code,done,rewarded,ts) VALUES(?,?,?,1,0,?)",
          (qq, day, code, int(time.time())))
    db.ex("UPDATE quests SET done=1 WHERE qq=? AND day=? AND code=? AND done=0",
          (qq, day, code))


def settle_quests(db, cfg, qq):
    """结算今天已完成的任务，返回文案列表。"""
    qq = str(qq)
    day = features.today()
    msgs = []
    for st in quest_state(db, cfg, qq):
        if not st["done"] or st["rewarded"]:
            continue
        db.ex("INSERT OR IGNORE INTO quests(qq,day,code,done,rewarded,ts) VALUES(?,?,?,1,0,?)",
              (qq, day, st["code"], int(time.time())))
        reward = st["reward"]
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (reward, qq))
        _ledger(db, qq, reward, "完成任务 " + st["name"])
        db.ex("UPDATE quests SET rewarded=1 WHERE qq=? AND day=? AND code=?",
              (qq, day, st["code"]))
        msgs.append("🎯 完成任务【%s】%s，奖励 %.0f 元" % (st["name"], st["desc"], reward))
    # 满勤：三条全做完再给一笔
    states = quest_state(db, cfg, qq)
    if states and all(s["done"] for s in states):
        row = db.q1("SELECT rewarded FROM quests WHERE qq=? AND day=? AND code=?",
                    (qq, day, "__all__"))
        if not (row and row["rewarded"]):
            bonus = float(cfg.get("quest_all_bonus", QUEST_ALL_BONUS))
            db.ex("INSERT OR IGNORE INTO quests(qq,day,code,done,rewarded,ts) VALUES(?,?,?,1,0,?)",
                  (qq, day, "__all__", int(time.time())))
            db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (bonus, qq))
            _ledger(db, qq, bonus, "今日任务全勤")
            db.ex("UPDATE quests SET rewarded=1 WHERE qq=? AND day=? AND code=?",
                  (qq, day, "__all__"))
            msgs.append("🏅 今日任务全勤！额外奖励 %.0f 元" % bonus)
    # 翻身任务只给中下层，结算跟日常任务走同一个入口
    msgs.extend(rise_settle(db, cfg, qq))
    return msgs


def quests_text(db, cfg, qq):
    """群指令「任务」的文案。"""
    states = quest_state(db, cfg, qq)
    done = sum(1 for s in states if s["done"])
    lines = ["🎯 今日任务（%d/%d）" % (done, len(states))]
    for s in states:
        lines.append("%s %s　%s（%.0f 元）" % ("✅" if s["done"] else "⬜", s["name"],
                                               s["desc"], s["reward"]))
    lines.append("完成全部再加 %.0f 元全勤奖" % float(cfg.get("quest_all_bonus", QUEST_ALL_BONUS)))
    rise = rise_state(db, cfg, qq)
    if rise:
        lines.append("")
        lines.append("🚀 翻身任务（中下层专属，做完就能离开打工循环）")
        for s in rise:
            lines.append("%s %s　%s（%.0f 元）" % ("✅" if s["done"] else "⬜", s["name"],
                                                    s["desc"], s["reward"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- 回归礼包
def _days_between(d1, d2):
    try:
        import datetime
        a = datetime.datetime.strptime(d1, "%Y-%m-%d").date()
        b = datetime.datetime.strptime(d2, "%Y-%m-%d").date()
        return (b - a).days
    except Exception:
        return 0


def touch_activity(db, cfg, qq):
    """记录活跃时间；离开太久自动发回归礼包。返回文案或 None。"""
    qq = str(qq)
    now = int(time.time())
    day = features.today()
    row = db.q1("SELECT * FROM activity WHERE qq=?", (qq,))
    if row is None:
        db.ex("INSERT INTO activity(qq,last_seen,last_day,days) VALUES(?,?,?,1)",
              (qq, now, day))
        return None
    last_day = row["last_day"] or ""
    gap = _days_between(last_day, day) if last_day else 0
    msg = None
    need = int(cfg.get("comeback_days", 3))
    if gap >= need:
        base = float(cfg.get("comeback_base", 300.0))
        cap = float(cfg.get("comeback_max", 1500.0))
        bonus = min(cap, base * gap)
        if bonus > 0:
            db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (bonus, qq))
            _ledger(db, qq, bonus, "回归礼包")
            msg = "🎁 欢迎回来！离开了 %d 天，账户给你留了 %.0f 元零花钱。" % (gap, bonus)
    db.ex("UPDATE activity SET last_seen=?, last_day=?, days=days+1 WHERE qq=?", (now, day, qq))
    return msg


# ---------------------------------------------------------------- 快讯
def news_feed(db, cfg, limit=8):
    """最近的消息面快讯（同一个标题只保留最新的一条）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(20, limit))
    rows = db.q("SELECT n.ts, n.scope, n.title, n.body, n.impact, n.cid, n.cat, "
                "c.name AS cname, c.ticker, c.price, c.prev_close "
                "FROM news n LEFT JOIN companies c ON c.id=n.cid ORDER BY n.id DESC LIMIT ?",
                (limit * 6,))
    out = []
    seen = set()
    for r in rows:
        title = (r["title"] or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        chg = 0.0
        if r["price"] and r["prev_close"]:
            chg = (r["price"] / r["prev_close"] - 1.0) * 100
        # 行业/大盘级快讯影响一整片，别硬挂一只票的涨幅上去（会误导）
        is_company = (r["scope"] == "company")
        out.append({"ts": r["ts"], "scope": r["scope"], "title": title, "body": r["body"],
                    "impact": round(float(r["impact"] or 0) * 100, 2),
                    "cat": r["cat"] or "", "bomb": title.startswith(BOMB_TAG),
                    "name": r["cname"] if is_company else "", "ticker": r["ticker"],
                    "price": round(float(r["price"]), 2) if r["price"] else 0,
                    "chg": round(chg, 2) if is_company else 0})
        if len(out) >= limit:
            break
    return out


def news_text(db, cfg, limit=8):
    items = news_feed(db, cfg, limit)
    if not items:
        return "📰 快讯\n（暂时没有新消息面）"
    lines = ["📰 快讯 · 消息面"]
    for i, it in enumerate(items, 1):
        tag = {"company": "公司", "industry": "行业", "market": "大盘"}.get(it["scope"], "快讯")
        tail = ""
        if it["name"]:
            tail = "　%s %+.2f%%" % (it["name"], it["chg"])
        lines.append("%d.【%s】%s%s" % (i, tag, it["title"], tail))
    return "\n".join(lines)


def major_events(db, cfg, limit=6, since=None):
    """今日大事记：今天所有带 🔥 的爆炸性新闻。"""
    if since is None:
        since = features.day_start()
    try:
        limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        limit = 6
    rows = db.q("SELECT ts, title, body, impact, scope, cat FROM news "
                "WHERE ts>=? AND title LIKE ? ORDER BY ts DESC LIMIT ?",
                (since, BOMB_TAG + "%", limit))
    out = []
    for r in rows:
        out.append({"ts": r["ts"], "title": r["title"], "body": r["body"],
                    "impact": round(float(r["impact"] or 0) * 100, 2),
                    "scope": r["scope"], "cat": r["cat"] or ""})
    return out


def major_text(db, cfg, limit=5, since=None):
    """今日大事记（播报用）。没有大事返回空串。"""
    evs = major_events(db, cfg, limit, since)
    if not evs:
        return ""
    lines = ["🔥 今日大事记"]
    for i, e in enumerate(evs, 1):
        lines.append("%d. %s（%+.1f%%）" % (i, e["title"], e["impact"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- 人气榜
def hot_turnover(db, cfg, limit=10):
    """今日成交额榜 —— 谁在买，制造 FOMO。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(30, limit))
    start = features.day_start()
    rows = db.q("SELECT t.cid, SUM(t.qty*t.price) AS amt, COUNT(*) AS n FROM trades t "
                "WHERE t.ts>=? GROUP BY t.cid ORDER BY amt DESC LIMIT ?", (start, limit))
    out = []
    for i, r in enumerate(rows, 1):
        c = db.q1("SELECT name, ticker, price, prev_close FROM companies WHERE id=?", (r["cid"],))
        if c is None:
            continue
        chg = (c["price"] / c["prev_close"] - 1.0) * 100 if c["prev_close"] else 0.0
        out.append({"rank": i, "cid": r["cid"], "name": c["name"], "ticker": c["ticker"],
                    "amount": round(float(r["amt"] or 0), 2), "trades": int(r["n"] or 0),
                    "price": market.px(float(c["price"])), "chg": round(chg, 2)})
    return out


def hot_text(db, cfg, limit=10):
    items = hot_turnover(db, cfg, limit)
    if not items:
        return "🔥 今日人气榜\n（今天还没有人下单，榜上空空如也）"
    lines = ["🔥 今日人气榜（按成交额）"]
    for it in items:
        lines.append("%d. %s(%s) %.2f %+.2f%%　成交 %.0f 元 / %d 笔" % (
            it["rank"], it["name"], it["ticker"], it["price"], it["chg"],
            it["amount"], it["trades"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- 打赏
def _find_users(db, target):
    target = str(target or "").strip()
    if not target:
        return []
    if target.isdigit():
        row = db.q1("SELECT * FROM users WHERE qq=?", (target,))
        return [row] if row else []
    exact = db.q("SELECT * FROM users WHERE nick=? ORDER BY created_at", (target,))
    if exact:
        return exact
    return db.q("SELECT * FROM users WHERE nick LIKE ? ORDER BY created_at LIMIT 5",
                ("%" + target + "%",))


def transfer_quota(db, cfg, qq):
    """打赏额度：单笔上限 / 当日剩余 / 可用现金。给网页面板显示用。

    跟 transfer() 里算额度的是同一套公式，只是单独拎出来一份给界面用 ——
    界面要写「单笔最多多少」「今天还能给多少」，不能让人自己心算。
    """
    acct = market.account(db, cfg, qq)
    equity = max(0.0, acct["equity"]) if acct else 0.0
    eq_ratio = float(cfg.get("transfer_equity_ratio", 0.02))
    day_eq_ratio = float(cfg.get("transfer_daily_equity_ratio", 0.05))
    cap = max(float(cfg.get("transfer_max", 5000.0)), equity * eq_ratio)
    day_cap = max(float(cfg.get("transfer_daily_cap", 5000.0)), equity * day_eq_ratio)
    row = db.q1("SELECT COALESCE(SUM(-delta),0) s FROM ledger"
                " WHERE qq=? AND ts>=? AND reason LIKE '打赏%'",
                (str(qq), features.day_start()))
    used = float(row["s"] or 0) if row else 0.0
    return {
        "equity": round(equity, 2),
        "cash": round(float(acct["cash"]) if acct else 0.0, 2),
        "cap": round(cap, 2),
        "day_cap": round(day_cap, 2),
        "day_left": round(max(0.0, day_cap - used), 2),
        "eq_ratio": eq_ratio,
    }


def member_list(db, limit=60):
    """群友名单（网页打赏选人用）：昵称 / QQ / 净值，按净值从高到低。

    【为什么要名单】打赏是按昵称找人的，群里同名的话转账会直接报错让你报 QQ 号。
    网页上直接把名单列出来、点一下就填进输入框，少一事是一事。
    净值口径跟 market.account 一致：现金 - 欠款 + 保证金 + 浮动盈亏。
    """
    try:
        rows = db.q(
            "SELECT u.qq AS qq, u.nick AS nick,"
            " (u.cash - u.loan"
            "  + COALESCE(SUM(p.margin), 0)"
            "  + COALESCE(SUM((c.price - p.entry) * p.qty *"
            "      (CASE WHEN p.side = 'long' THEN 1 ELSE -1 END)), 0)) AS equity"
            " FROM users u"
            " LEFT JOIN positions p ON p.qq = u.qq AND p.status = 'open'"
            " LEFT JOIN companies c ON c.id = p.cid"
            " GROUP BY u.qq, u.nick, u.cash, u.loan"
            " ORDER BY equity DESC LIMIT ?", (int(limit),))
    except Exception:
        rows = []
    out = []
    for r in rows:
        out.append({"qq": r["qq"], "nick": r["nick"],
                    "equity": round(float(r["equity"] or 0), 2)})
    return out

def transfer(db, cfg, qq, target, amount):
    """群友之间打赏：自己掏钱给对方。"""
    qq = str(qq)
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return False, "金额不合法", None
    amount = round(amount, 2)
    if amount <= 0:
        return False, "金额不合法", None
    me = db.q1("SELECT * FROM users WHERE qq=?", (qq,))
    if me is None:
        return False, "你还没开户", None
    # 破产/纾困期间不能打赏：欠着一屁股债还给别人发红包，
    # 等于让纾困流程永远攒不到「司法重整」的 8 万块。
    if me["bankrupt"]:
        return False, "你还在破产纾困阶段，先把自己的债还清再打赏吧", None
    # 【v4.6】重开期不许请客 —— 刚被交易所救回来，先把自己的本金挣回来。
    if recovery.in_reopen(db, qq):
        return False, ("重开期（还剩 %d 天）不能请客 —— 先把自己的本金挣回来"
                       % recovery.reopen_days_left(db, qq)), None
    # 【v4.6】打赏额度随身家放大。
    # 这是最直接的一条扶贫通道：头部玩家请客，钱流到中下层；
    # 反过来，中下层还是 5000 元的额度，所以差距是被「消费」压缩的，
    # 不是被「没收」压缩的 —— 不会打击赚钱的积极性。
    acct = market.account(db, cfg, qq)
    equity = max(0.0, acct["equity"]) if acct else 0.0
    eq_ratio = float(cfg.get("transfer_equity_ratio", 0.02))
    day_eq_ratio = float(cfg.get("transfer_daily_equity_ratio", 0.05))
    cap = max(float(cfg.get("transfer_max", 5000.0)), equity * eq_ratio)
    day_cap = max(float(cfg.get("transfer_daily_cap", 5000.0)), equity * day_eq_ratio)
    if amount > cap:
        return False, ("单笔最多打赏 %.0f 元（你的净值 %.0f，单笔上限 = max(5000, 净值×%.0f%%)）"
                       % (cap, equity, eq_ratio * 100)), None
    cands = _find_users(db, target)
    if not cands:
        return False, "没找到叫「%s」的人" % str(target), None
    if len(cands) > 1:
        names = "、".join((c["nick"] or c["qq"]) for c in cands[:4])
        return False, "有多个叫「%s」的人（%s），请直接报 QQ 号" % (str(target), names), None
    to = cands[0]
    if to["qq"] == qq:
        return False, "自己给自己打赏有什么意思", None
    spent = db.q1("SELECT COALESCE(SUM(-delta),0) s FROM ledger WHERE qq=? AND ts>=? AND reason LIKE '打赏%'",
                  (qq, features.day_start()))
    if spent and spent["s"] + amount > day_cap:
        return False, "今天打赏额度用完了（上限 %.0f 元）" % day_cap, None
    my_cash = float(me["cash"] or 0)
    if my_cash < amount:
        return False, "余额不够，你只有 %.2f 元" % my_cash, None
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (amount, qq))
    _ledger(db, qq, -amount, "打赏给 %s" % (to["nick"] or to["qq"]))
    db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (amount, to["qq"]))
    _ledger(db, to["qq"], amount, "收到 %s 的打赏" % (me["nick"] or qq))
    msg = "💸 %s 打赏 %s %.0f 元，全群见证！" % (me["nick"] or qq, to["nick"] or to["qq"], amount)
    return True, msg, {"to": to["qq"], "to_nick": to["nick"], "amount": amount}


# ---------------------------------------------------------------- 每日播报
def _market_snapshot(db, cfg):
    comps = db.q("SELECT id, name, ticker, price, prev_close FROM companies")
    ups = downs = flat = 0
    limit_up = limit_down = 0
    lim = float(cfg.get("price_limit", 0.1)) * 100.0
    items = []
    for c in comps:
        chg = (c["price"] / c["prev_close"] - 1.0) * 100 if c["prev_close"] else 0.0
        if chg > 0.05:
            ups += 1
        elif chg < -0.05:
            downs += 1
        else:
            flat += 1
        if chg >= lim - 0.3:
            limit_up += 1
        elif chg <= -lim + 0.3:
            limit_down += 1
        items.append({"name": c["name"], "ticker": c["ticker"], "price": market.px(c["price"]),
                      "chg": round(chg, 2)})
    items.sort(key=lambda x: x["chg"], reverse=True)
    return {"total": len(comps), "up": ups, "down": downs, "flat": flat,
            "limit_up": limit_up, "limit_down": limit_down,
            "top": items[:3], "bottom": items[-3:][::-1]}


def brief_text(db, cfg):
    """每日早报。"""
    now = time.localtime()
    day = "%d月%d日 %s" % (now.tm_mon, now.tm_mday, WEEKDAYS[now.tm_wday])
    snap = _market_snapshot(db, cfg)
    users = db.q1("SELECT COUNT(*) c FROM users")
    signed = db.q1("SELECT COUNT(*) c FROM checkin WHERE last_day=?", (features.today(),))
    rank = market.ranking(db, cfg, 50)
    lines = ["📣 布莱泽早报 · " + day,
             "🟢 上涨 %d　🔴 下跌 %d　（平 %d）" % (snap["up"], snap["down"], snap["flat"]),
             "🚀 涨停 %d 家　💥 跌停 %d 家　共 %d 家上市公司" % (
                 snap["limit_up"], snap["limit_down"], snap["total"])]
    if snap["top"]:
        t = snap["top"][0]
        lines.append("🔥 最强：%s(%s) %+.2f%%" % (t["name"], t["ticker"], t["chg"]))
    if rank:
        lines.append("👑 群内首富：%s（净值 %.0f）" % (rank[0].get("nick") or rank[0]["qq"],
                                                rank[0]["equity"]))
    lines.append("✅ 今日已签到 %d 人（群内 %d 人）" % (
        signed["c"] if signed else 0, users["c"] if users else 0))
    major = major_text(db, cfg, 3)
    if major:
        lines.append(major.replace("\n", "\n　"))
    lines.append("👉 发「签到」领钱 ·「任务」看今日任务 ·「快讯」看消息面 ·「人气榜」看谁在买")
    return "\n".join(lines)


def close_text(db, cfg):
    """收盘复盘播报。"""
    now = time.localtime()
    day = "%d月%d日 %s" % (now.tm_mon, now.tm_mday, WEEKDAYS[now.tm_wday])
    snap = _market_snapshot(db, cfg)
    start = features.day_start()
    trades_today = db.q1("SELECT COUNT(*) c FROM trades WHERE ts>=?", (start,))
    best = db.q1("SELECT qq, SUM(pl) s FROM trades WHERE ts>=? GROUP BY qq ORDER BY s DESC LIMIT 1",
                 (start,))
    signed = db.q1("SELECT COUNT(*) c FROM checkin WHERE last_day=?", (features.today(),))
    quested = db.q1("SELECT COUNT(DISTINCT qq) c FROM quests WHERE day=? AND rewarded=1",
                     (features.today(),))
    rank = market.ranking(db, cfg, 50)
    lines = ["🌇 布莱泽收盘 · " + day]
    if snap["top"]:
        t = snap["top"][0]
        lines.append("📈 涨幅王：%s %+.2f%%" % (t["name"], t["chg"]))
    if snap["bottom"]:
        b = snap["bottom"][0]
        lines.append("📉 跌幅王：%s %+.2f%%" % (b["name"], b["chg"]))
    if best and best["s"]:
        u = db.q1("SELECT nick FROM users WHERE qq=?", (best["qq"],))
        lines.append("🏆 今日收益王：%s %+.0f 元" % ((u["nick"] if u else best["qq"]), best["s"]))
    if rank:
        lines.append("💰 群内首富：%s（净值 %.0f）" % (rank[0].get("nick") or rank[0]["qq"],
                                                rank[0]["equity"]))
    hot = hot_turnover(db, cfg, 1)
    if hot:
        lines.append("🔥 今日人气：%s（成交 %.0f 元）" % (hot[0]["name"], hot[0]["amount"]))
    lines.append("✅ 今日签到 %d 人 ｜ 🎯 完成任务 %d 人 ｜ 🔄 今日成交 %d 笔" % (
        signed["c"] if signed else 0, quested["c"] if quested else 0,
        trades_today["c"] if trades_today else 0))
    major = major_text(db, cfg, 5)
    if major:
        lines.append(major.replace("\n", "\n　"))
    lines.append("👉 发「今日复盘」看完整版 ·「人气榜」「涨幅榜」查排名")
    return "\n".join(lines)

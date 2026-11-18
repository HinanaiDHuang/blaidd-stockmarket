"""模拟行情引擎：随机游走 + 行业联动 + 随机新闻事件 + K 线聚合。

所有价格都是假的，别当真（也别当真到去炒股）。
交易制度：一根 K 线 = 一个「交易日」（默认 60 秒），涨跌幅限制 ±10%。
"""
import json
import math
import os
import random
import time

import db as dbmod
from newsdata import (NEWS_TEMPLATES, BOMB_TEMPLATES, CAT_MARKS, CAT_ORDER,
                      SCOPE_LABELS, BOMB_TAG, bombs_of, pick_bomb, cat_mark,
                      fmt_title)


def dbmod_backup(path, tag="auto"):
    return dbmod.backup_db(path, tag)

# 涨跌停带的容差（单位：元）。带边界按昨收算出来是无限小数，
# 而股价要取整到分，所以「顶在涨停位」的票会差出 1 分钱。
# 这 1 分钱如果被当成「已经在带外」，后面整段夹取就会被跳过。
LIM_EPS = 0.02

# 行业因子系数：同行业的股票同涨同跌。
# 以前 sector_ret 每 tick 都算了却没用在定价里（纯浪费），结果是
# 「XX 板块集体异动」只影响被点名的几只票，板块感完全没有。
# 系数 0.10 对应每 tick 约 1.4e-4 的板块波动，和大盘因子（beta×1.92e-4）
# 同一量级：板块会一起动，但不会盖过个股自己的故事。
SECTOR_COEF = 0.10

# 引擎自检计数：日切滚动次数 / 昨收重锚次数。
# 涨跌停只在一根 K 线内有效，所以一个自然日最多 +10%/-10%。
# 实测要是出现单日 ±20%，一定是这两个计数不对，先查它们。
_ENGINE_STATS = {"rollover": 0, "rebase": 0, "pullback": 0}

# 诊断累加器：只用于标定时拆解「每 tick 收益由哪一项贡献」。
# 默认关闭，生产环境零开销；沙盘脚本打开后按项读数。
ENGINE_DIAG = False
_ENGINE_ACC = {"market": 0.0, "sector": 0.0, "vol": 0.0, "extra": 0.0,
                "premium": 0.0, "regime": 0.0, "rev": 0.0, "clip": 0.0,
                "band": 0.0, "round": 0.0, "n": 0, "news": 0.0, "news_n": 0,
                  "news_raw": 0.0, "news_clip": 0.0, "push": 0.0}


def engine_acc():
    """返回诊断累加器的快照（原始累加值，未除以计数）。"""
    return dict(_ENGINE_ACC)


def reset_diag():
    """清零诊断累加器（只保留计数），用于逐 tick 对账。"""
    for k in _ENGINE_ACC:
        _ENGINE_ACC[k] = 0

# 行业设定：漂移、日波动、联动系数
INDUSTRIES = [
    ("乳业", 0.00002, 0.0016, 0.7),
    ("重工", 0.00003, 0.0022, 1.1),
    ("金融", 0.00001, 0.0019, 1.2),
    ("电子", 0.00006, 0.0028, 1.3),
    ("医药", 0.00004, 0.0021, 0.9),
    ("能源", 0.00003, 0.0025, 1.0),
    ("传媒", -0.00002, 0.0026, 1.2),
    ("机械", 0.00002, 0.0020, 1.0),
    ("化工", 0.00001, 0.0023, 1.1),
    ("地产", -0.00003, 0.0024, 1.2),
    ("农业", 0.00001, 0.0018, 0.8),
    ("食品", 0.00002, 0.0015, 0.7),
    ("物流", 0.00002, 0.0019, 1.0),
    ("软件", 0.00005, 0.0027, 1.3),
    ("旅游", -0.00001, 0.0024, 1.1),
    ("服装", 0.00001, 0.0020, 1.0),
    ("汽车", 0.00002, 0.0022, 1.1),
    ("白酒", 0.00002, 0.0017, 0.9),
]

# 新闻素材在 newsdata.py：120 条普通快讯（24 种题材）+ 44 条爆炸性新闻。
# 单独成文件是为了别把引擎逻辑埋掉。

TICKER_PREFIX = ["600", "000", "002", "300", "601", "603"]


def now():
    return int(time.time())


class Engine:
    """单个群的行情引擎。"""

    def __init__(self, db, cfg):
        self.db = db
        self.cfg = cfg

    # ---------- 公司与成员 ----------
    def sync_companies(self, members, reset=False):
        """按群成员名单维护上市公司。

        members: [(qq, nick), ...]，nick 用 QQ 昵称（不是群昵称）。
        返回 (新增, 移除, 保持)。

        【2026-11-17 事故后的行为】
        以前这里是「名单外的一律删库」。但成员名单是客户端（Lua 插件）传进来的，
        一次只覆盖群成员的一部分（接口限流、插件缓存不全、群列表被截断），
        就会把整群 215 家公司、14 万根 K 线、所有未平仓持仓一次性删光。
        现在这里只做「增」和「改昵称」，**绝不删**。
        真要清理退群用户留下的公司，走 prune_companies()，它有覆盖率校验和自动备份。
        """
        members = [(str(q), (n or "").strip()) for q, n in members]
        members = [(q, n) for q, n in members if q]
        rows = self.db.q("SELECT id, qq, nick FROM companies")
        exist = {r["qq"]: r for r in rows}
        added = 0
        for qq, nick in members:
            if qq in exist:
                if nick and exist[qq]["nick"] != nick:
                    self.db.ex("UPDATE companies SET nick=? WHERE qq=?", (nick, qq))
                continue
            self._create_company(qq, nick)
            added += 1
        removed = 0
        return added, removed, len(members) - added

    def prune_companies(self, members, cover_ratio=0.9):
        """清理「已经不在群里」的人留下的公司。

        这是**显式**操作，不会被 sync_companies 自动触发。三道保险：
          1) 名单必须覆盖现存公司数量的 cover_ratio 以上，否则直接拒绝；
          2) 只删「没有未平仓持仓、也没有成交记录」的公司；
          3) 删之前自动备份数据库。
        返回 (删除数, 说明)。
        """
        members = [(str(q), (n or "").strip()) for q, n in members]
        members = [(q, n) for q, n in members if q]
        keep = {q for q, _ in members}
        rows = self.db.q("SELECT id, qq FROM companies")
        if not rows:
            return 0, "没有可清理的公司"
        covered = sum(1 for r in rows if r["qq"] in keep)
        ratio = covered / float(len(rows))
        if ratio < cover_ratio:
            return 0, ("名单只覆盖了 %d/%d 家公司（%.0f%%），低于安全阈值 %.0f%%，拒绝清理"
                       % (covered, len(rows), ratio * 100, cover_ratio * 100))
        targets = []
        for r in rows:
            if r["qq"] in keep:
                continue
            cid = r["id"]
            busy = self.db.q1("SELECT COUNT(*) c FROM positions WHERE cid=? AND status='open'", (cid,))
            traded = self.db.q1("SELECT COUNT(*) c FROM trades WHERE cid=?", (cid,))
            if busy and busy["c"] > 0:
                continue
            if traded and traded["c"] > 0:
                continue
            targets.append(cid)
        if not targets:
            return 0, "没有可安全清理的公司"
        bak = dbmod_backup(self.db.path, "prune")
        for cid in targets:
            self.db.ex("DELETE FROM candles WHERE cid=?", (cid,))
            self.db.ex("DELETE FROM positions WHERE cid=?", (cid,))
            self.db.ex("DELETE FROM companies WHERE id=?", (cid,))
        return len(targets), "已清理 %d 家公司（备份：%s）" % (
            len(targets), os.path.basename(bak) if bak else "无")

    def _create_company(self, qq, nick):
        ind, drift, vol, beta = random.choice(INDUSTRIES)
        base = round(random.uniform(6.0, 60.0), 2)
        ticker = random.choice(TICKER_PREFIX) + "%04d" % random.randint(1, 9999)
        name = "%s%s" % (nick or ("股东" + qq[-3:]), ind)
        self.db.ex(
            "INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
            "beta,drift,vol,anchor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (qq, nick, ind, name, ticker, base, base, base, base, base,
             beta * random.uniform(0.8, 1.2), drift * random.uniform(-1, 1),
             vol * random.uniform(0.7, 1.4), base, now()))
        cid = self.db.q1("SELECT id FROM companies WHERE qq=?", (qq,))["id"]
        # 补一段历史 K 线，页面一打开就有图
        self._seed_history(cid, base)

    def _seed_history(self, cid, base):
        step = self.cfg["candle_seconds"]
        t = now()
        price = base
        rows = []
        n = 120
        for i in range(n):
            ts = t - (n - i) * step
            o = price
            price = max(0.5, price * (1 + random.gauss(0.0002, 0.012)))
            c = price
            h = max(o, c) * (1 + abs(random.gauss(0, 0.004)))
            l = min(o, c) * (1 - abs(random.gauss(0, 0.004)))
            rows.append((cid, ts, round(o, self._prec()), round(h, self._prec()),
                         round(l, self._prec()), round(c, self._prec()),
                         round(random.uniform(2e4, 9e5))))
        self.db.exmany("INSERT OR REPLACE INTO candles(cid,ts,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)", rows)
        self.db.ex("UPDATE companies SET price=?, prev_close=?, open=?, high=?, low=?, anchor=? "
                   "WHERE id=?",
                   (round(price, self._prec()), round(rows[-1][5], self._prec()),
                    round(rows[-1][2], self._prec()),
                    round(price * 1.02, self._prec()), round(price * 0.98, self._prec()),
                    round(price, self._prec()), cid))
        # 开局给每家公司配一条历史快讯，新闻页不至于空空如也
        if random.random() < 0.7:
            tpl, lo, hi, scope, cat = random.choice(NEWS_TEMPLATES)
            self._fire(self.db.q1("SELECT id, price, industry FROM companies WHERE id=?", (cid,)),
                       tpl, random.uniform(lo, hi), scope, cat=cat)

    # ---------- 行情推进 ----------
    def tick(self):
        """推进一个 tick：更新价格、聚合 K 线、可能触发新闻。"""
        rows = self.db.q("SELECT id, price, prev_close, beta, drift, vol, industry, anchor "
                         "FROM companies")
        if not rows:
            return
        lim = self._limit()
        vol_scale = self._num("vol_scale", 1.0)
        premium = self._num("market_premium", 0.0)
        floor_px = self._num("floor_price", 0.5)
        floor_rebound = self._num("floor_rebound", 0.0002)
        market_ret = random.gauss(0.0, 0.0016 * vol_scale)
        sector_ret = {}
        for r in rows:
            sector_ret.setdefault(r["industry"], random.gauss(0.0, 0.0014))
        mood, mood_label = self._regime()
        regime_drift = mood * self._num("regime_drift", 8e-07)
        # 收盘前把「当日漂移」平摊推完（原因见 _push_window 的注释）
        push_rate, push_ticks = self._push_window()
        drift_scale = self._num("drift_scale", 0.0)
        ticks_per_day = 86400.0 / max(0.5, self._num("tick_seconds", 2.0))

        for r in rows:
            # 个股自己的日漂移不在这里逐 tick 累加：
            # 逐 tick 加会被「按分取整」抹掉（1e-8 量级的每 tick 漂移，
            # 对一只 10 元的票来说连 0.01 分都不到，取整后直接消失），
            # 现在统一由收盘前的漂移推送窗口按 tick 摊开（见 _push_window）。
            diag_on = ENGINE_DIAG
            g_vol = random.gauss(0.0, r["vol"] * vol_scale)
            g_extra = random.gauss(0.0, 0.0009 * vol_scale)
            g_sector = SECTOR_COEF * sector_ret.get(r["industry"], 0.0)
            g_market = r["beta"] * market_ret
            raw_ret = (premium
                       + regime_drift
                       + g_market
                       + g_sector
                       + g_vol
                       + g_extra)
            if diag_on:
                _ENGINE_ACC["premium"] += premium
                _ENGINE_ACC["regime"] += regime_drift
                _ENGINE_ACC["market"] += g_market
                _ENGINE_ACC["sector"] += g_sector
                _ENGINE_ACC["vol"] += g_vol
                _ENGINE_ACC["extra"] += g_extra
            # 均值回归：往锚价（默认 30 分钟前的价）拉一点。
            # 没有这一项时，行情会因为新闻冲击 + 波动拖累长期单边失血，
            # 玩家只会越玩越亏，最后觉得「炒股是骗局」。
            anchor = r["anchor"] or r["price"]
            rev = self._num("reversion", 0.0005)
            # 【均值回归改成「加法回拉」，根治系统性失血】
            # 原来把回归塞进乘法的 ret 里：
            #     p' = p * (1 + eps - rev * (p/A - 1))
            # 这一步在数学上必然漏血。设 u = p/A - 1，平稳条件是
            #     E[u'] = E[u]  =>  E[u] = -E[u^2]
            # 而 E[u^2] > 0（u 不恒为 0），所以 E[u] < 0：
            # 价格会系统性地待在锚价下方，每 tick 漏一点，
            # 而且波动越大漏得越狠（E[u^2] ∝ sigma^2）。
            # 实测（24 票 / 12 天 / 收盘对收盘口径）：
            #     无新闻  日内 -0.35%/天
            #     有新闻  日内 -1.55%/天   <- 新闻只是把波动放大，漏血跟着放大
            # 这就是「玩几天必亏」的隐形杀手，也是之前怎么调参数都调不正的根因。
            #
            # 改成加法回拉（先按噪声更新，再把偏差直接拉回来）：
            #     p' = p * (1 + eps) + rev * (A - p)
            # 平稳时 E[p'] = E[p] + rev*(A - E[p])，取 E[p] = A 即为不动点，
            # 没有任何系统性偏置，波动再大也不漏血。
            pull = 0.0
            if rev > 0 and anchor > 0:
                pull = rev * (anchor - r["price"])
            ret = raw_ret
            if diag_on:
                _ENGINE_ACC["rev"] += pull / max(1e-9, r["price"])
            clipped = max(-0.035, min(0.035, ret))
            # 当日漂移（市场级 + 个股级）在收盘前摊到每个 tick 推完。
            # 摊开后的量级 ~1e-4，比 4 位小数的取整粒度大一个数量级，
            # 不会被取整吃掉 —— 这正是原来「每 tick 累加漂移」失灵的原因。
            if push_rate:
                push = (push_rate
                        + float(r["drift"] or 0.0) * drift_scale * ticks_per_day / push_ticks)
                clipped += push
                if diag_on:
                    _ENGINE_ACC["push"] += push
            # 地板价保护：价格贴到下限时给一点向上的动能。
            # 没有这一条时 max(floor, price*(1+ret)) 会把股价冻在地板价上，
            # K 线变成一条直线（群友反馈的「一条直线」就是它）。
            if r["price"] <= floor_px * 1.001 and floor_rebound > 0:
                clipped = max(clipped, floor_rebound)
            if diag_on:
                _ENGINE_ACC["clip"] += clipped - ret
            ret = clipped
            if diag_on:
                _ENGINE_ACC["n"] += 1
            new_price = round(max(floor_px, r["price"] * (1 + ret) + pull), self._prec())
            if diag_on:
                _ENGINE_ACC["round"] += round(max(floor_px, r["price"] * (1 + ret) + pull), self._prec()) \
                    - max(floor_px, r["price"] * (1 + ret))
            # 单个 tick 的幅度保护
            step_lim = max(0.1, lim)
            new_price = max(r["price"] * (1 - step_lim), min(r["price"] * (1 + step_lim), new_price))
            # 涨跌停：相对昨收最多 ±price_limit
            base = r["prev_close"] or r["price"]
            lo_lim, hi_lim = base * (1 - lim), base * (1 + lim)
            rebase = None
            # 【2026-11-17 事故修复】原来只要价格越出涨跌停带，就把昨收重锚到现价。
            # 于是「带」永远跟着价格走 —— 涨跌停形同虚设：
            #   · 涨：每次摸到上沿就把昨收抬 10%，可以无限往上蹭。
            #     线上某个大群当天全部 266 只票平均涨 +4.36%，
            #     大量个股顶在 +9.9% —— 群友说的「大盘莫名暴涨」就是它。
            #   · 跌：同理一路向下，11-17 群 765903215 四只票单日从 20 多块
            #     砸到 0.5 地板价（-90%），三只冻成直线 —— 「一条直线」的来源。
            # 现在改成：
            #   · 越界幅度 < 3 倍涨跌停限制 → 正常触板/插针，不重锚，
            #     直接把价格夹回带内：涨跌停真正生效，日涨跌硬性封在 ±10%；
            #   · 越界幅度 >= 3 倍限制 → 判定为脏数据（送转 / 旧库脏价 / 缺口跳空），
            #     这时才把昨收重锚到现价，让涨跌停从下一根开始重新生效。
            eps = self._eps(base)
            if r["price"] < lo_lim - eps or r["price"] > hi_lim + eps:
                off = (r["price"] / base - 1.0) if base > 0 else 0.0
                if abs(off) >= lim * 3:
                    _ENGINE_STATS["rebase"] += 1
                    rebase = r["price"]
                    base = rebase
                    lo_lim, hi_lim = base * (1 - lim), base * (1 + lim)
                else:
                    _ENGINE_STATS["pullback"] += 1
            # 夹取永远生效。带边界不取整、价取整到分，
            # 顶到边界上时会出现 1 分钱的溢出，老逻辑会把整段夹取跳过，
            # 股价从涨停位再跑 3.5%/tick，这是之前日波动 20%+ 的真正原因。
            banded = min(max(new_price, lo_lim), hi_lim)
            if diag_on:
                _ENGINE_ACC["band"] += (banded - new_price) / max(1e-9, r["price"])
            new_price = round(banded, self._prec())
            hi = max(r["price"], new_price)
            lo = min(r["price"], new_price)
            vol = random.uniform(1e4, 6e5)
            if rebase is None:
                self.db.ex(
                    "UPDATE companies SET price=?, high=MAX(high,?), low=MIN(low,?) WHERE id=?",
                    (new_price, hi, lo, r["id"]))
            else:
                self.db.ex(
                    # 重锚昨收时不要动 open：open 是「今日开盘价」，
                    # 被覆盖的话前端算出来的今日涨跌会莫名其妙。
                    "UPDATE companies SET price=?, prev_close=?, high=MAX(high,?), low=MIN(low,?) "
                    "WHERE id=?", (new_price, rebase, hi, lo, r["id"]))
            # 涨停记录：真摸到涨停板就记一笔，收盘时的「涨停竞猜」靠它结算。
            # rebase（昨收重锚）时不算数 —— 那不是正常涨停。
            if rebase is None and new_price >= hi_lim - self._eps(base, 2e-4):
                self._mark_limit_hit(r["id"])
            self._update_candle(r["id"], new_price, vol)

        if self.cfg.get("news_enable", True):
            self._maybe_news(rows)
        self._refresh_anchors()

    def engine_stats(self):
        """引擎自检计数（rollover=日切次数，rebase=昨收重锚次数）。"""
        return dict(_ENGINE_STATS)

    def _snapshot_day(self, day):
        """跨日瞬间把每只票的「收盘价 / 昨收」存进 daily_px。

        【为什么必须存】日切那一行会把 price 和 prev_close 刷成同一个值
        （开盘不跳空了），于是日切之后再算「今日涨跌」永远是 0%，
        群里的「今日复盘」涨跌幅榜、涨停家数全成了废数据。
        v4.4 去掉开盘跳空之后这个问题才暴露出来 ——
        老版本跳空完至少还有 +1.2% 能看。

        【为什么要 INSERT OR IGNORE】同一天会被 200 多家公司各触发一次
        跨日逻辑，只有第一次（所有票都还没日切）的那份快照是准的；
        后面的调用只会把已日切的票写成 0%，所以只能忽略不能覆盖。
        """
        try:
            self.db.ex("INSERT OR IGNORE INTO daily_px"
                       "(day,cid,name,ticker,price,prev_close,high,low) "
                       "SELECT ?, id, name, ticker, price, prev_close, high, low FROM companies",
                       (day,))
        except Exception as exc:
            print("[stockmarket] daily snapshot failed: %s" % exc, flush=True)

    def _mark_limit_hit(self, cid):
        """记一笔「今天这只涨到过涨停」。"""
        day = time.strftime("%Y%m%d", time.localtime(now()))
        try:
            self.db.ex("INSERT INTO limit_hits(cid,day,ts) VALUES(?,?,?) "
                       "ON CONFLICT(cid,day) DO NOTHING", (int(cid), day, now()))
        except Exception as exc:
            print("[stockmarket] limit_hits failed: %s" % exc, flush=True)

    def _regime(self):
        """当前市场情绪周期：黑天鹅之后市场会持续一段时间的涨/跌。"""
        raw = self.db.get_meta("market_regime", "")
        if not raw:
            return 0.0, ""
        try:
            d = json.loads(raw)
        except Exception:
            return 0.0, ""
        if int(d.get("until", 0)) <= now():
            self.db.set_meta("market_regime", "")
            return 0.0, ""
        return float(d.get("mood", 0.0)), d.get("label", "")

    def _set_regime(self, mood, hours, label):
        until = now() + int(max(0.5, float(hours or 1)) * 3600)
        self.db.set_meta("market_regime", json.dumps(
            {"mood": float(mood), "until": until, "label": label}, ensure_ascii=False))
        return label

    def _refresh_anchors(self):
        """每 anchor_refresh_seconds 秒把锚价挪到现价。

        锚价 = 上一次刷新时的价格，所以「锚价 vs 现价」天然就是
        「N 分钟前 vs 现在」，回归项就是一个 N 分钟周期的温和拉回。
        """
        try:
            step = int(self._num("anchor_refresh_seconds", 1800))
        except (TypeError, ValueError):
            step = 1800
        if step <= 0:
            return
        ts = now()
        try:
            last = int(self.db.get_meta("anchor_ts", "0") or 0)
        except (TypeError, ValueError):
            last = 0
        if ts - last < step:
            return
        self.db.set_meta("anchor_ts", str(ts))
        # 锚点跟随系数：1.0 = 完全跟随现价（老行为），
        # <1 时锚点只吸收一部分偏离，剩下的交给均值回归慢慢拉回。
        # 完全跟随的问题是：任何下跌都是永久的（锚点跟着跌，回归拉不回来），
        # 线上因此出现过单日 -90% 的暴跌。
        follow = self._num("anchor_follow", 1.0)
        if follow >= 0.999:
            self.db.ex("UPDATE companies SET anchor=price")
        else:
            self.db.ex("UPDATE companies SET anchor = anchor + ? * (price - anchor)", (follow,))
        self._prune_candles()

    def _prune_candles(self):
        """K 线只留最近一段。

        一根 K 线 60 秒、247 家公司 -> 一天 21 万行，不清理的话
        几个月就能把 SQLite 撑到几个 G，查询也会变慢。
        前端只取 300 根，留 2000 根绰绰有余。
        """
        try:
            keep = int(self._num("candle_keep", 2000))
        except (TypeError, ValueError):
            keep = 2000
        if keep <= 0:
            return
        step = int(self._num("candle_seconds", 60))
        cutoff = now() - keep * step
        self.db.ex("DELETE FROM candles WHERE ts < ?", (cutoff,))

    def _update_candle(self, cid, price, vol):
        step = self.cfg["candle_seconds"]
        ts = now() - now() % step
        row = self.db.q1("SELECT o,h,l,c,v FROM candles WHERE cid=? AND ts=?", (cid, ts))
        if row is None:
            # 新的一天：昨收 = 上一根收盘，开盘 = 当前价
            prev = self.db.q1("SELECT c, ts FROM candles WHERE cid=? AND ts<? ORDER BY ts DESC LIMIT 1",
                              (cid, ts))
            if prev:
                # 昨收/今开只在真正跨自然日时滚动。原来每分钟滚一次，
                # 等于涨跌停基准每分钟重置 -> 涨跌停形同虚设，今日涨跌幅恒为 0%。
                new_day = (time.localtime(prev["ts"]).tm_yday != time.localtime(ts).tm_yday)
                if new_day:
                    _ENGINE_STATS["rollover"] += 1
                    # 跨自然日：昨收 = 昨天最后一根收盘，今开 = 昨收（不跳空）
                    #
                    # 【为什么取消隔夜跳空 —— 涨跌停带是以昨收为中心的】
                    # 开盘跳空 +1.8% 会把价格中心抬到带的上半区：
                    #   上方空间 = 10% - 1.8% = 8.2%
                    #   下方空间 = 10% + 1.8% = 11.8%
                    # 新闻把价格顶上沿时，上沿比下沿近得多 -> 涨的行情被削、
                    # 跌的不被削 -> 系统性地「少涨」。实测（8 seed × 9 天，24 票）：
                    #     有新闻  日内 -2.30%/天（跳空 +2.16%，实际只有 -0.19%/天）
                    #     无新闻  日内 +0.11%/天（跳空 +1.81%，实际 +1.91%/天）
                    # 跳空本身无害，害的是「跳空 + 涨跌停带 + 新闻」三者叠加。
                    # 控制器还会雪上加霜：它看到日内亏钱就加大跳空
                    # -> 带子更窄 -> 日内亏更多 -> 跳空顶到上限，
                    # 实测目标 1.8% 时跳空被顶到 2.61%，市场实际只剩 +0.25%/天，
                    # 比打工（无风险 +0.16%/天 —— job_daily_cap=160 元 / 10 万本金）
                    # 还差，玩家自然不玩。
                    #
                    # 现在改成：开盘价 = 昨收（带子对称，不漏血），
                    # 当日漂移改为「收盘前几分钟平摊推完」（见 _push_window）。
                    #
                    # 锁死今天的漂移目标必须在改 prev_close 之前：
                    # 那一刻 price/prev_close-1 才是昨天的收盘对收盘收益。
                    self._market_gap()
                    # 跨日快照必须在这一刻做：下面这行会把 price 和
                    # prev_close 刷成同一个值，之后再算「今日涨跌」恒为 0。
                    self._snapshot_day(time.strftime("%Y-%m-%d", time.localtime(prev["ts"])))
                    self.db.ex("UPDATE companies SET price=?, prev_close=?, open=?, high=?, low=?, "
                               "anchor=? WHERE id=?",
                               (prev["c"], prev["c"], prev["c"], prev["c"], prev["c"], prev["c"], cid))
                    price = prev["c"]
                elif not self.cfg.get("day_rollover", True):
                    self.db.ex("UPDATE companies SET prev_close=?, open=? WHERE id=?", (prev["c"], price, cid))
            self.db.ex("INSERT INTO candles(cid,ts,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)",
                       (cid, ts, price, price, price, price, vol))
        else:
            self.db.ex("UPDATE candles SET h=MAX(h,?), l=MIN(l,?), c=?, v=v+? WHERE cid=? AND ts=?",
                       (price, price, price, vol, cid, ts))

    def _push_window(self):
        """收盘前的「漂移推送窗口」。

        返回 (每 tick 推的市场漂移, 窗口内 tick 数)；不在窗口内返回 (0.0, 1.0)。

        【为什么不用开盘跳空推漂移】见 _update_candle 里那段注释：
        跳空把价格中心顶到涨跌停带的上半区，新闻一涨就撞上沿被削，
        系统性地少涨。改成收盘前摊推，价格中心全天停在带子正中，
        上下对称，日内不再漏血。

        【摊到每个 tick 的量级要够大】
        股价按 4 位小数取整（见 _prec），每 tick 的漂移必须明显大于
        5e-5 的取整粒度，否则会被取整整段吃掉 —— 原来「每 tick 累加
        漂移」失灵就是因为量级只有 1e-8。300 秒 / 2 秒一 tick = 150 档，
        1.8% 的漂移摊下来是 1.2e-4，安全。

        【为什么起点要随机】
        漂移集中在收盘前推，如果起点固定，玩家只要在窗口前一分钟买、
        零点卖就能无风险吃掉整整一天的漂移。起点在窗口内随机
        （落库、全天不变），就没法稳定薅。
        """
        try:
            win = int(self._num("drift_push_seconds", 300))
        except (TypeError, ValueError):
            win = 300
        if win <= 0:
            return 0.0, 1.0
        lt = time.localtime(now())
        sec = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
        if sec < 86400 - win:
            # 不在窗口附近，别去查库（每 tick 都查会拖慢行情）
            return 0.0, 1.0
        start = self._push_start(win)
        if start is None or sec < start:
            return 0.0, 1.0
        tick_sec = max(0.5, self._num("tick_seconds", 2.0))
        n = max(1.0, (86400.0 - start) / tick_sec)
        return self._market_gap() / n, n

    def _push_start(self, win):
        """今天漂移推送的起点（当天第几秒）。当天第一次问的时候随机定下来并落库。"""
        day = time.strftime("%Y%m%d", time.localtime(now()))
        try:
            if self.db.get_meta("market_push_day", "") == day:
                v = int(float(self.db.get_meta("market_push_start", "") or 0))
                if v > 0:
                    return v
        except (TypeError, ValueError):
            pass
        # 起点落在 [86400-win, 86400-win/2]：
        # 至少留 win/2 秒把漂移推完，否则当天推不满。
        lo = max(0, 86400 - win)
        hi = max(lo, 86400 - win // 2)
        start = random.randint(lo, hi) if hi > lo else lo
        try:
            self.db.set_meta("market_push_day", day)
            self.db.set_meta("market_push_start", str(start))
        except Exception:
            pass
        return start

    def _num(self, key, default):
        """读配置里的数值。

        之前到处写 float(self.cfg.get(k, d) or d)，结果「把某项设成 0」
        根本关不掉 —— or 会把 0 当成空值，又兜回默认值。
        实测过：news_fire_prob 设 0 照样按默认速率发新闻（关不掉），
        导致一整轮消融实验两组配置跑出来一模一样，结论全废。
        这里统一成：只有 None / 非法值才用默认值，0 就是 0。
        """
        try:
            v = self.cfg.get(key, default)
        except Exception:
            return default
        if v is None:
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _prec(self):
        """价格内部精度（保留几位小数）。

        原来固定按「分」取整（round(..., 2)），可低价股一 tick 的随机波动
        只有 0.008 元上下，比 1 分还小 —— 取整后一半概率原地不动，
        于是 K 线拉出一条横线、成交量却照涨（用户截图里「Barrel地产 4.25」
        整段开高低收全是 4.25，量却有 951 万）。
        改成可配的 4 位精度后，tick 的微小变化会被如实记下来。
        """
        try:
            p = int(self.cfg.get("price_precision", 4))
        except (TypeError, ValueError):
            p = 4
        return max(0, min(6, p))

    def _eps(self, base, rel=1e-4, floor=1e-4):
        """涨跌停带的容差（按价格比例算，不能用固定值）。

        原来用固定 0.02 元：对 10 元的票是 0.2%，问题不大；
        但对 0.5 元的低价股就是 4% —— 低价股整天贴着「带外」晃，
        涨跌停形同虚设。改成比例容差：0.01% × 价格，最少 0.0001。
        """
        try:
            b = abs(float(base or 0))
        except (TypeError, ValueError):
            b = 0.0
        return max(floor, b * rel)

    def _market_gap(self):
        """市场级当日漂移（收盘前摊推）：把「长期漂移」和「单日黑天鹅」彻底分开。

        【为什么不能再拿「昨天的实际涨跌」直接反馈】
        昨天的涨跌里混着随机游走、新闻冲击、黑天鹅（单日 ±10%）、跳空本身。
        拿它当漂移信号，控制器会被单日噪声牵着跑 —— 实测就是：
        一次崩盘把 EWMA 带偏 → gap 一路顶到上限（+3.7%/天）打满 →
        市场既不涨也不跌，只是天天在缺口附近来回磨，还天天触板。

        【现在怎么做】
          1. 每天日切时记一条「市场等权日收益」（含当天跳空）；
          2. 用最近 N 天的中位数估底层漂移 D —— 中位数对单日崩盘免疫，
             单日 ±10% 的黑天鹅最多挪动中位数一点，不会把长期估计带跑；
          3. drift = 目标 + clamp(-gain × D, ±trim_max)：
             修正量很小、很慢，只负责兜底，不负责「制造」趋势。
        历史不够（少于 5 天）时直接用目标值，不猜。

        现在这个值不再用于「开盘跳空」，而是当日收盘前摊着推完
        （见 _push_window）。名字里的 gap 是历史遗留，别被误导。
        """
        day = time.strftime("%Y%m%d", time.localtime(now()))
        # 进程内缓存：同一 tick 里 200+ 家公司共用同一个跳空值。
        cache = getattr(self, "_gap_cache", None)
        if cache and cache[0] == day:
            return cache[1]
        target = self._num("market_daily_drift", 0.0)
        # 跨进程缓存：生产里每个 tick 都会新建一个 Engine，
        # 只靠实例缓存的话，修正量会被同一天反复更新（一天 200+ 次），
        # 控制器直接失效。所以「今天算过没有」也要落到库里。
        #
        # 【2026-11-18 修】把 target 一起当指纹存下来。
        # 原来只判「今天算过没有」，算出来的值连同当时的 target 一起
        # 冻在库里了 —— 运维改了 config.json 的 market_daily_drift，
        # 当天完全不生效。生产实测：配置写着 0.018，库里压着 0.012，
        # 当天收盘前推的还是 +1.2%，要等第二天 00:00 才切过来。
        # 现在 target 变了就当天重算，但只换目标值、不重跑历史估计：
        # 重跑会再往 market_gap_hist 里塞一条，把「昨天用的跳空值」
        # 记成今天上午那个，污染控制器的历史样本。
        try:
            if self.db.get_meta("market_gap_day", "") == day:
                used = self.db.get_meta("market_gap_target", "")
                cached = float(self.db.get_meta("market_gap_val", "") or 0.0)
                if used != "":
                    used_f = float(used)
                    if abs(used_f - target) < 1e-12:
                        # target 没变：直接用缓存，行为和以前完全一样
                        self._gap_cache = (day, cached)
                        return cached
                    # target 变了：保留控制器已经算好的修正量，只换目标
                    trim_max = self._num("drift_trim_max", 0.006)
                    corr = max(-trim_max, min(trim_max, cached - used_f))
                    gap = target + corr
                    self._write_gap(day, target, gap)
                    self._gap_cache = (day, gap)
                    return gap
        except (TypeError, ValueError):
            pass
        gain = self._num("drift_gain", 0.0)
        trim_max = self._num("drift_trim_max", 0.006)
        gap = target
        realized = 0.0
        if gain > 0:
            try:
                row = self.db.q1(
                    "SELECT AVG(price/prev_close - 1.0) AS r FROM companies "
                    "WHERE prev_close > 0")
                if row is not None and row["r"] is not None:
                    realized = float(row["r"])
            except Exception:
                realized = 0.0
            hist = self._gap_history(day, realized)
            d_est = 0.0
            if len(hist) >= 5:
                rs = sorted(float(h[1]) for h in hist)
                gs = sorted(float(h[2]) for h in hist)
                # 底层漂移 = 日收益中位数 - 跳空中位数
                d_est = rs[len(rs) // 2] - gs[len(gs) // 2]
            corr = -gain * d_est
            corr = max(-trim_max, min(trim_max, corr))
            gap = target + corr
            try:
                self.db.set_meta("market_drift_ewma", repr(d_est))
                self.db.set_meta("market_drift_last", repr(realized))
            except Exception:
                pass
        # 落库：生产里每个 tick 都新建 Engine，实例缓存没用，
        # 「今天算过没有 / 算出来多少」必须落到库里，漂移推送窗口
        # 才能在收盘前读到这个值（原来只在 gain>0 分支里写，
        # gain 配成 0 时整天读不到，推送窗口会拿不到漂移）。
        self._write_gap(day, target, gap)
        self._gap_cache = (day, gap)
        return gap

    def _write_gap(self, day, target, gap):
        """把「今天用的漂移值」和「算它时用的目标值」一起落库。

        market_gap_target 是 2026-11-18 新增的指纹字段：
        有了它，运维改 config.json 的 market_daily_drift 后当天就能生效
        （见 _market_gap 里的说明）。老库没有这个字段时，
        第一次访问会当成「需要重算」走一遍，行为退回到修复前的样子。
        """
        try:
            self.db.set_meta("market_gap_target", repr(target))
            self.db.set_meta("market_gap_last", repr(gap))
            self.db.set_meta("market_gap_day", day)
            self.db.set_meta("market_gap_val", repr(gap))
        except Exception:
            pass

    def _gap_history(self, day, realized):
        """最近 N 天的「市场日收益 / 当天跳空」记录，落在 meta 里。

        记的是「上一条日切时量到的昨日收益」+「昨天用的跳空值」，
        所以 D = 中位数(日收益) - 中位数(跳空) 就是去掉跳空后的底层漂移。
        """
        hist = []
        try:
            raw = self.db.get_meta("market_gap_hist", "")
            if raw:
                arr = json.loads(raw)
                if isinstance(arr, list):
                    hist = [h for h in arr
                            if isinstance(h, (list, tuple)) and len(h) >= 3]
        except Exception:
            hist = []
        hist = [h for h in hist if h[0] != day]
        try:
            prev_gap = float(self.db.get_meta("market_gap_last", "") or 0.0)
        except (TypeError, ValueError):
            prev_gap = 0.0
        hist.append([day, float(realized), prev_gap])
        n = int(self._num("drift_hist_days", 20))
        n = max(5, min(60, n))
        hist = hist[-n:]
        try:
            self.db.set_meta("market_gap_hist", json.dumps(hist, ensure_ascii=False))
        except Exception:
            pass
        return hist


    def _daily_gap(self, cid):
        """当日漂移合计 = 市场级（控制器给的）+ 个股自身日漂移。

        市场级由 _market_gap() 的外环控制器算出来，用来保证炒股长期跑赢打工
        （打工无风险 +0.16%/天 —— job_daily_cap=160 元 / 10 万本金；
          市场目标 +1.8%/天，标定实测 +1.51%/天）。
        个股日漂移 = drift × drift_scale × 每日 tick 数，
        让「有的股票天生涨得好」这件事真的存在（选股有讲究）。

        【注意】这个名字是历史遗留：以前这里是「隔夜跳空」，
        现在开盘不再跳空，这笔钱改成收盘前摊推（见 _push_window），
        但函数的算法和数值口径没变，调用方照旧用。
        """
        gap = self._market_gap()
        try:
            tick_sec = self._num("tick_seconds", 2.0)
        except (TypeError, ValueError):
            tick_sec = 2.0
        ticks_per_day = 86400.0 / max(0.5, tick_sec)
        try:
            row = self.db.q1("SELECT drift FROM companies WHERE id=?", (cid,))
            if row is not None:
                gap += (float(row["drift"] or 0.0)
                        * self._num("drift_scale", 0.0) * ticks_per_day)
        except Exception:
            pass
        return gap

    # ---------- 新闻 ----------
    def _maybe_news(self, rows):
        if not rows:
            return
        pend = self.db.get_meta("pending_news", "")
        if pend:
            # 已经备好一条内幕，等一个合适的时机引爆。
            # 原来每 tick 10% 概率引爆 = 每 20 秒一条，一天六万条，
            # 快讯列表全是垃圾，真正的大新闻根本看不见。现在改成可配的慢速快讯。
            prob = self._num("news_fire_prob", 0.0067)
            if random.random() <= prob:
                self.db.set_meta("pending_news", "")
                self._fire_pending(json.loads(pend), rows)
                self._prepare_news(rows)
            return
        self._prepare_news(rows)

    def _prepare_news(self, rows):
        """备一条待发快讯（玩家可以花钱提前知道方向）。

        绝大多数是普通快讯（120 条模板 / 24 种题材）；
        小概率（bomb_rate）抽到「爆炸性新闻」（52 条模板）：
        大盘级 28 条 / 行业级 14 条 / 个股级 10 条。
        爆炸性新闻一发能撼动整个市场，还会带出一段情绪周期，
        大盘级甚至直接触发临时休市（熔断）。
        """
        bomb_rate = self._num("bomb_rate", 0.0)
        if bomb_rate > 0 and random.random() < bomb_rate:
            bom = pick_bomb(rows)
            if bom:
                pend = {"bomb": bom, "tpl": bom["tpl"], "scope": bom["scope"],
                        "cat": bom.get("cat", ""),
                        "impact": round(random.uniform(bom["lo"], bom["hi"]), 6),
                        "peeked": 0}
                if bom["scope"] == "company":
                    pend["cid"] = bom.get("cid")
                elif bom["scope"] == "industry":
                    pend["ind"] = bom.get("ind")
                self.db.set_meta("pending_news", json.dumps(pend, ensure_ascii=False))
                return
        tpl, lo, hi, scope, cat = random.choice(NEWS_TEMPLATES)
        impact = random.uniform(lo, hi)
        pend = {"tpl": tpl, "impact": round(impact, 6), "scope": scope,
                "cat": cat, "peeked": 0}
        if scope == "company":
            pend["cid"] = random.choice(rows)["id"]
        elif scope == "industry":
            pend["ind"] = random.choice([r["industry"] for r in rows])
        self.db.set_meta("pending_news", json.dumps(pend, ensure_ascii=False))

    def _fire_bomb(self, pend, rows):
        """爆炸性新闻：全市场 / 行业 / 个股三档冲击 + 情绪周期 + 熔断。"""
        bom = pend.get("bomb") or {}
        scope = bom.get("scope", "market")
        impact = pend.get("impact", 0.0)
        tpl = pend.get("tpl", "")
        cat = pend.get("cat") or bom.get("cat", "")
        if scope == "company":
            cid = pend.get("cid") or bom.get("cid")
            target = self.db.q1("SELECT id FROM companies WHERE id=?", (cid,))
            if target is not None:
                self._fire(target, tpl, impact, "company", bomb=True, cat=cat)
        elif scope == "industry":
            ind = pend.get("ind") or bom.get("ind") or ""
            hit = [r for r in rows if r["industry"] == ind]
            self._fire_bulk("industry", tpl, impact, hit, ind, bomb=True, cat=cat)
        else:
            self._fire_bulk("market", tpl, impact, list(rows), "", bomb=True, cat=cat)
        label = self._set_regime(bom.get("mood", 0.0), bom.get("hours", 2),
                                 bom.get("label", "市场异动"))
        try:
            self.db.set_meta("market_regime_label", str(label))
        except Exception:
            pass
        # 大盘级黑天鹅：临时休市（熔断），让群友有个共同话题
        circuit = int(bom.get("circuit", 0) or 0)
        if circuit > 0 and scope == "market":
            self._trip_circuit(circuit, label)

    def _trip_circuit(self, minutes, reason):
        """临时休市：大盘级黑天鹅之后锁盘若干分钟。"""
        until = now() + int(max(1, minutes) * 60)
        self.db.set_meta("circuit_until", str(until))
        self.db.set_meta("circuit_reason", str(reason or "市场异动"))
        return until

    def circuit_state(self):
        """当前是否处于临时休市。返回 (until_ts, reason)；没休市返回 (0, "")。"""
        try:
            until = int(self.db.get_meta("circuit_until", "0") or 0)
        except (TypeError, ValueError):
            until = 0
        if until <= now():
            if until:
                try:
                    self.db.set_meta("circuit_until", "0")
                    self.db.set_meta("circuit_reason", "")
                except Exception:
                    pass
            return 0, ""
        try:
            reason = self.db.get_meta("circuit_reason", "") or ""
        except Exception:
            reason = ""
        return until, reason

    def market_mood(self):
        """当前市场情绪。返回 (mood, 情绪周期标签, 恐慌指数, 恐慌档位)。

        恐慌指数 = 50 - mood*38，再叠加最近 6 小时内的黑天鹅冲击。
        纯粹是给人看的情绪温度计，不参与任何结算。
        """
        mood, label = self._regime()
        try:
            rows = self.db.q("SELECT impact, scope FROM news WHERE ts>=? AND title LIKE ?",
                              (now() - 3600 * 6, BOMB_TAG + "%"))
        except Exception:
            rows = []
        shock = 0.0
        for r in rows:
            imp = float(r["impact"] or 0)
            shock += imp if r["scope"] == "market" else imp * 0.35
        fear = 50.0 - mood * 38.0 - shock * 120.0
        fear = max(0.0, min(100.0, fear))
        if fear >= 78:
            fl = "恐慌"
        elif fear >= 62:
            fl = "紧张"
        elif fear >= 45:
            fl = "偏谨慎"
        elif fear >= 30:
            fl = "平稳"
        else:
            fl = "亢奋"
        return mood, (label or ""), round(fear, 1), fl

    def _fire_pending(self, pend, rows):
        if pend.get("bomb"):
            self._fire_bomb(pend, rows)
            return
        tpl = pend["tpl"]
        impact = pend["impact"]
        scope = pend["scope"]
        cat = pend.get("cat", "")
        if scope == "company":
            target = self.db.q1("SELECT id FROM companies WHERE id=?", (pend.get("cid"),))
            if target is not None:
                self._fire(target, tpl, impact, scope, cat=cat)
            return
        if scope == "industry":
            ind = pend.get("ind") or ""
            hit = [r for r in rows if r["industry"] == ind]
        else:
            ind = ""
            hit = list(rows)
        self._fire_bulk(scope, tpl, impact, hit, ind, cat=cat)

    def _limit(self):
        """涨跌停幅度。config.json 的 price_limit，默认 10%。"""
        try:
            lim = self._num("price_limit", 0.1)
        except (TypeError, ValueError):
            lim = 0.1
        return max(0.01, min(0.5, lim))

    def _apply_impact(self, cid, impact):
        """快讯冲击落到某只票上（带涨跌停限制）。"""
        row = self.db.q1("SELECT price, prev_close FROM companies WHERE id=?", (cid,))
        if not row:
            return
        lim = self._limit()
        floor_px = self._num("floor_price", 0.5)
        newp = max(floor_px, row["price"] * (1 + impact))
        base = row["prev_close"] or row["price"]
        lo_lim, hi_lim = base * (1 - lim), base * (1 + lim)
        eps = self._eps(base)
        if row["price"] < lo_lim - eps or row["price"] > hi_lim + eps:
            off = (row["price"] / base - 1.0) if base > 0 else 0.0
            if abs(off) >= lim * 3:
                # 越界幅度 >= 3 倍涨跌停限制 → 脏数据（送转 / 旧库脏价 / 极端跳空），
                # 这时才把昨收重锚到现价，让涨跌停从下一根开始重新生效。
                base = row["price"]
                lo_lim, hi_lim = base * (1 - lim), base * (1 + lim)
            # 轻微越界：正常触板/插针，不重锚，直接夹回带内。
            # 原来的「越界就重锚」等于让涨跌停永久失效：
            # 跌的时候可以无限往下蹭（线上出现过单日 -90%），
            # 涨的时候可以无限往上蹭（线上 266 只票平均涨 +4.36%）。
        # 夹取永远生效，日涨跌硬性封在 ±price_limit 以内。
        newp = round(min(max(newp, lo_lim), hi_lim), self._prec())
        if ENGINE_DIAG:
            oldp = float(row["price"] or 0)
            if oldp > 0:
                _ENGINE_ACC["news"] += (newp / oldp - 1.0)
                _ENGINE_ACC["news_n"] += 1
                _ENGINE_ACC["news_raw"] += impact
                _ENGINE_ACC["news_clip"] += (newp / oldp - 1.0) - impact
        self.db.ex("UPDATE companies SET price=? WHERE id=?", (newp, cid))
        # K 线同步一下当前价格
        self._update_candle(cid, self.db.q1("SELECT price FROM companies WHERE id=?", (cid,))["price"], 0)

    def _fire_bulk(self, scope, tpl, impact, hit, ind="", bomb=False, cat=""):
        """行业/大盘级快讯：只落一条新闻，受影响公司写进 body。

        老逻辑按公司逐条插，215 家公司的大盘快讯一次刷 200+ 行，
        同一个标题在快讯列表里重复几十次。现在一条新闻 + 一份受影响名单。
        """
        ids = [r["id"] for r in hit]
        if not ids:
            return
        first = self.db.q1("SELECT ticker FROM companies WHERE id=?", (ids[0],))
        if not first:
            return
        if scope == "industry":
            title = tpl.format(name=ind, ind=ind, peer="同行", num=0, pct=0)
        else:
            title = tpl.format(name="大盘", ind="大盘", peer="同行", num=0, pct=0)
        names = []
        for cid in ids:
            nrow = self.db.q1("SELECT name FROM companies WHERE id=?", (cid,))
            if nrow and nrow["name"]:
                names.append(nrow["name"])
        body = "影响 %d 家公司：%s" % (len(ids), "、".join(names[:8]) + ("…" if len(names) > 8 else ""))
        if bomb:
            body = BOMB_TAG + " " + body
        title = fmt_title(scope, cat, title, bomb=bomb)
        self.db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact,cat) VALUES(?,?,?,?,?,?,?,?)",
                   (now(), ids[0], first["ticker"], scope, title, body, impact, cat or ""))
        for cid in ids:
            self._apply_impact(cid, impact)
        # 清理老新闻
        self.db.ex("DELETE FROM news WHERE ts < ?", (now() - 3600 * 6,))

    def _fire(self, row, tpl, impact, scope, bomb=False, cat=""):
        cid = row["id"]
        info = self.db.q1("SELECT name, ticker, industry FROM companies WHERE id=?", (cid,))
        if not info:
            return
        peers = [x["name"] for x in self.db.q("SELECT name FROM companies WHERE industry=? AND id<>? LIMIT 3",
                                              (info["industry"], cid))]
        peer = random.choice(peers) if peers else "隔壁公司"
        raw = tpl.format(name=info["name"], ind=info["industry"], peer=peer,
                       num=random.randint(3, 99), pct=round(abs(impact) * 100, 1))
        body = (BOMB_TAG + " " + raw) if bomb else raw
        title = fmt_title(scope, cat, raw, bomb=bomb)
        self.db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact,cat) VALUES(?,?,?,?,?,?,?,?)",
                   (now(), cid, info["ticker"], scope, title, body, impact, cat or ""))
        # 价格立刻反应
        self._apply_impact(cid, impact)
        # 清理老新闻
        self.db.ex("DELETE FROM news WHERE ts < ?", (now() - 3600 * 6,))


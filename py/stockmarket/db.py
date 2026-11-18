"""每群独立的 SQLite 数据库封装。

一个群 = 一个 db 文件（data/g_<群号>.db），群与群之间的行情、资金、持仓完全隔离。
"""
import os
import sqlite3
import threading
import time
import contextlib

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS companies(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    nick TEXT NOT NULL,
    industry TEXT NOT NULL,
    name TEXT NOT NULL,
    ticker TEXT NOT NULL,
    price REAL NOT NULL,
    prev_close REAL NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    beta REAL NOT NULL,
    drift REAL NOT NULL,
    vol REAL NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS candles(
    cid INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL, v REAL,
    PRIMARY KEY(cid, ts)
);

CREATE TABLE IF NOT EXISTS users(
    qq TEXT PRIMARY KEY,
    nick TEXT NOT NULL DEFAULT '',
    cash REAL NOT NULL DEFAULT 0,
    loan REAL NOT NULL DEFAULT 0,
    loan_ts INTEGER NOT NULL DEFAULT 0,
    bankrupt INTEGER NOT NULL DEFAULT 0,
    jobs INTEGER NOT NULL DEFAULT 0,
    win REAL NOT NULL DEFAULT 0,
    lose REAL NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    last_job INTEGER NOT NULL DEFAULT 0,
    bankrupt_earn REAL NOT NULL DEFAULT 0,
    relief_tier INTEGER NOT NULL DEFAULT 0,
    bankrupt_ts INTEGER NOT NULL DEFAULT 0,
    bankrupt_since INTEGER NOT NULL DEFAULT 0,
    reopen_until INTEGER NOT NULL DEFAULT 0,
    reputation REAL NOT NULL DEFAULT 0,
    rob_heat INTEGER NOT NULL DEFAULT 0,
    rob_heat_ts INTEGER NOT NULL DEFAULT 0,
    rob_guard_until INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS positions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    cid INTEGER NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry REAL NOT NULL,
    leverage REAL NOT NULL,
    margin REAL NOT NULL,
    opened_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS trades(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    cid INTEGER NOT NULL,
    side TEXT NOT NULL,
    kind TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    leverage REAL NOT NULL,
    fee REAL NOT NULL,
    pl REAL NOT NULL,
    ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS loans(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    amount REAL NOT NULL,
    repaid REAL NOT NULL DEFAULT 0,
    rate REAL NOT NULL,
    ts INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS news(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    cid INTEGER,
    ticker TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'company',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    impact REAL NOT NULL DEFAULT 0,
    cat TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ledger(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    ts INTEGER NOT NULL,
    delta REAL NOT NULL,
    balance REAL NOT NULL,
    reason TEXT NOT NULL
);

-- 每日签到（连签 streak 是活跃度的核心钩子）
CREATE TABLE IF NOT EXISTS checkin(
    qq TEXT PRIMARY KEY,
    streak INTEGER NOT NULL DEFAULT 0,
    best INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    last_day TEXT NOT NULL DEFAULT ''
);

-- 成就墙
CREATE TABLE IF NOT EXISTS achievements(
    qq TEXT NOT NULL,
    code TEXT NOT NULL,
    ts INTEGER NOT NULL,
    reward REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(qq, code)
);

-- 价格提醒（触发后由 Lua/页面领取）
CREATE TABLE IF NOT EXISTS reminders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    cid INTEGER NOT NULL,
    kind TEXT NOT NULL,
    price REAL NOT NULL,
    hit INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    hit_at INTEGER
);

-- 条件单（同花顺式：止损 / 止盈 / 限价挂单）
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    cid INTEGER NOT NULL,
    kind TEXT NOT NULL,
    side TEXT NOT NULL,
    trigger REAL NOT NULL,
    qty REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    triggered_at INTEGER,
    note TEXT NOT NULL DEFAULT ''
);

-- 人气投票（每天一票，给群友的公司拉人气）
CREATE TABLE IF NOT EXISTS votes(
    qq TEXT NOT NULL,
    day TEXT NOT NULL,
    cid INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY(qq, day)
);

CREATE TABLE IF NOT EXISTS company_votes(
    cid INTEGER NOT NULL,
    day TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(cid, day)
);

-- 每日抽奖
CREATE TABLE IF NOT EXISTS lottery(
    qq TEXT NOT NULL,
    day TEXT NOT NULL,
    prize TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    ts INTEGER NOT NULL,
    PRIMARY KEY(qq, day)
);

-- 涨停竞猜（每天一票，猜今天哪只会涨停）
CREATE TABLE IF NOT EXISTS guess(
    qq TEXT NOT NULL,
    day TEXT NOT NULL,
    cid INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY(qq, day)
);

-- 道具券（每日抽奖发的：免手续费 / 免息 / 打工双倍）
CREATE TABLE IF NOT EXISTS coupons(
    qq TEXT NOT NULL,
    kind TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    expire_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(qq, kind)
);

-- 涨停记录（每天谁真的摸到涨停板了 —— 涨停竞猜靠它结算）
CREATE TABLE IF NOT EXISTS limit_hits(
    cid INTEGER NOT NULL,
    day TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY(cid, day)
);

-- 每日收盘快照（群内「今日复盘」直接读它）
CREATE TABLE IF NOT EXISTS daily(
    day TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- 跨日瞬间的逐票收盘快照（2026-11-18 新增）
--
-- 【为什么需要它】日切那一行会把 price 和 prev_close 刷成同一个值：
--     UPDATE companies SET price=prev_close, prev_close=prev_close ...
-- 于是日切之后再算「今日涨跌」永远是 0%，「今日复盘」里的涨跌幅榜和
-- 涨停家数全是废数据。这张表在跨日之前把每只票的收盘价和昨收存下来，
-- 复盘就从它取数，拿到的才是当天真正的「收盘对收盘」涨跌。
CREATE TABLE IF NOT EXISTS daily_px(
    day TEXT NOT NULL,
    cid INTEGER NOT NULL,
    name TEXT,
    ticker TEXT,
    price REAL,
    prev_close REAL,
    high REAL,
    low REAL,
    PRIMARY KEY(day, cid)
);

-- 每日任务（v3：给玩家「今天还有事没干」的理由）
CREATE TABLE IF NOT EXISTS quests(
    qq TEXT NOT NULL,
    day TEXT NOT NULL,
    code TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    rewarded INTEGER NOT NULL DEFAULT 0,
    ts INTEGER NOT NULL,
    PRIMARY KEY(qq, day, code)
);

-- 玩家活跃度（v3：回归礼包判定）
CREATE TABLE IF NOT EXISTS activity(
    qq TEXT PRIMARY KEY,
    last_seen INTEGER NOT NULL DEFAULT 0,
    last_day TEXT NOT NULL DEFAULT '',
    days INTEGER NOT NULL DEFAULT 0
);

-- 推送日志（v3：提醒只推一次，早报每天一次）
CREATE TABLE IF NOT EXISTS push_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL DEFAULT '',
    gid TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    ts INTEGER NOT NULL
);

-- v4.11 站内通知（强平 / 破产 / 风险预警）。
-- 【为什么要补这张表】以前 check_liquidation 返回的提示文案在调度器里被直接丢掉，
-- 玩家既收不到私信、网页上也没有任何提示，仓位列表直接变空 ——
-- 群友原话「被强平后没持有，说我仓位太重」。这张表给「系统主动告诉玩家」一个落点。
CREATE TABLE IF NOT EXISTS notices(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'info',
    text TEXT NOT NULL DEFAULT '',
    ts INTEGER NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_quest_day ON quests(qq, day);
CREATE INDEX IF NOT EXISTS idx_notice_qq ON notices(qq, id);

CREATE INDEX IF NOT EXISTS idx_pos_qq ON positions(qq, status);
CREATE INDEX IF NOT EXISTS idx_trade_qq ON trades(qq, ts);
CREATE INDEX IF NOT EXISTS idx_news_ts ON news(ts);
CREATE INDEX IF NOT EXISTS idx_candle_ts ON candles(cid, ts);
CREATE INDEX IF NOT EXISTS idx_candle_only_ts ON candles(ts);
CREATE INDEX IF NOT EXISTS idx_reminder_qq ON reminders(qq, hit);
CREATE INDEX IF NOT EXISTS idx_order_qq ON orders(qq, status);
CREATE INDEX IF NOT EXISTS idx_vote_day ON company_votes(day, n);
CREATE INDEX IF NOT EXISTS idx_ach_qq ON achievements(qq);
"""

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_group(
    uid TEXT PRIMARY KEY,
    gid TEXT NOT NULL,
    ts INTEGER NOT NULL,
    self_gid TEXT,
    self_ts INTEGER
);

-- v4.2 登录口令表。
-- exp = 0 表示这是用户自己设的固定口令（长期有效）；
-- exp > 0 表示这是群里当面发的一次性口令，exp 是过期时间戳，用掉即焚。
CREATE TABLE IF NOT EXISTS user_pass(
    uid TEXT PRIMARY KEY,
    pwd TEXT NOT NULL,
    ts INTEGER NOT NULL,
    exp INTEGER NOT NULL DEFAULT 0
);
"""

_cache = {}
_lock = threading.RLock()


class GroupDB:
    """单个群的数据库句柄（线程安全）。"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        # batch 深度必须是**线程本地**的。
        # 用实例属性的话，调度器线程开着 batch（深度 1）时，
        # 网页线程来写数据就会误以为「还在 batch 里」而不提交，
        # 那笔订单要等下一次 tick 才落库 —— 玩家会看到「下单了但没成交」。
        self._tls = threading.local()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=20)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """轻量迁移：给已存在的库补新增字段。"""
        try:
            cols = {r["name"] for r in self.q("PRAGMA table_info(users)")}
            if "interest_acc" not in cols:
                self.ex("ALTER TABLE users ADD COLUMN interest_acc REAL NOT NULL DEFAULT 0")
        except sqlite3.Error:
            pass
        # v4.4：破产纾困进度（累计收入 / 已达成档位 / 破产时间）。老库补列。
        try:
            cols = {r["name"] for r in self.q("PRAGMA table_info(users)")}
            for col, decl in (("bankrupt_earn", "REAL NOT NULL DEFAULT 0"),
                              ("relief_tier", "INTEGER NOT NULL DEFAULT 0"),
                              ("bankrupt_ts", "INTEGER NOT NULL DEFAULT 0")):
                if col not in cols:
                    self.ex("ALTER TABLE users ADD COLUMN %s %s" % (col, decl))
        except sqlite3.Error:
            pass
        # v4.6：破产清盘后的「重开期」截止时间戳（不能贷款 / 杠杆<=1）。老库补列。
        try:
            cols = {r["name"] for r in self.q("PRAGMA table_info(users)")}
            if "reopen_until" not in cols:
                self.ex("ALTER TABLE users ADD COLUMN reopen_until INTEGER NOT NULL DEFAULT 0")
        except sqlite3.Error:
            pass
        # v4.7：富豪经济。reputation=挥霍换来的声望（抗抢保护），
        # rob_heat=被多少人盯上，rob_guard_until=刚被抢过的保护期。
        # bankrupt_since=「这一次破产从什么时候开始」，
        try:
            cols = {r["name"] for r in self.q("PRAGMA table_info(users)")}
            for col, decl in (("reputation", "REAL NOT NULL DEFAULT 0"),
                              ("rob_heat", "INTEGER NOT NULL DEFAULT 0"),
                              ("rob_heat_ts", "INTEGER NOT NULL DEFAULT 0"),
                              ("rob_guard_until", "INTEGER NOT NULL DEFAULT 0")):
                if col not in cols:
                    self.ex("ALTER TABLE users ADD COLUMN %s %s" % (col, decl))
        except sqlite3.Error:
            pass
        # v4.7：破产倒计时专用的 bankrupt_since。
        # 【为什么要单独开一列】bankrupt_ts 会被「净值回正 → 又跌回负数」
        # 的震荡刷新：2026-11-18 现网实测，Anyisdragon / 泡水 当天破产、
        # 当天又被清算，bankrupt_ts 被刷成「刚刚」，于是「破产满 N 天
        # 强制清盘」的倒计时永远从头开始，7000 多万死钱永远解不开。
        # bankrupt_since 只在「进入破产」时写一次、退出破产时清零，
        # 专门用来算强制清盘倒计时。
        try:
            cols = {r["name"] for r in self.q("PRAGMA table_info(users)")}
            if "bankrupt_since" not in cols:
                self.ex("ALTER TABLE users ADD COLUMN bankrupt_since INTEGER NOT NULL DEFAULT 0")
            # 补历史：还在破产中的，从已有的 bankrupt_ts 继承；
            # bankrupt_ts 也没有的老号（v4.4 之前）留 0，
            # 由 auto_liquidate 按「逾期」处理。
            self.ex("UPDATE users SET bankrupt_since=bankrupt_ts "
                    "WHERE bankrupt=1 AND bankrupt_since=0 AND bankrupt_ts>0")
        except sqlite3.Error:
            pass
        # v4.1：快讯题材（财报/监管/资金/国际…）。老库补列。
        try:
            cols = {r["name"] for r in self.q("PRAGMA table_info(news)")}
            if "cat" not in cols:
                self.ex("ALTER TABLE news ADD COLUMN cat TEXT NOT NULL DEFAULT ''")
        except sqlite3.Error:
            pass
        # v3.1：锚价（均值回归用）。老库补列并用现价初始化。
        try:
            cols = {r["name"] for r in self.q("PRAGMA table_info(companies)")}
            if "anchor" not in cols:
                self.ex("ALTER TABLE companies ADD COLUMN anchor REAL NOT NULL DEFAULT 0")
            self.ex("UPDATE companies SET anchor=price WHERE anchor IS NULL OR anchor<=0")
        except sqlite3.Error:
            pass

    # --- 基础操作 -----------------------------------------------------
    def q(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def q1(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def ex(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            if getattr(self._tls, "depth", 0) <= 0:
                self.conn.commit()
            return cur

    def exmany(self, sql, seq):
        with self.lock:
            cur = self.conn.executemany(sql, seq)
            if getattr(self._tls, "depth", 0) <= 0:
                self.conn.commit()
            return cur

    @contextlib.contextmanager
    def batch(self):
        """把一串写操作合成**一次**事务（一次 commit = 一次 fsync）。

        【为什么要这个】engine.tick() 每 tick 会对每只股票 UPDATE 一次、
        再写一次 K 线，原来每次 ex() 都单独 commit 一次 —— 一次 commit
        就是一次 fsync。二百多只股票一个 tick = 四五百次 fsync，而且全程
        握着这把**群级**锁，网页请求全被堵在门外。移动端就表现成
        「一直转圈 / 偶尔返回空响应」，群里还以为是接口挂了。

        现在一个 tick 只提交一次：fsync 次数降到 1/400，锁持有时间同步
        缩短，网页基本感觉不到 tick 的存在。
        """
        with self.lock:
            depth = getattr(self._tls, "depth", 0)
            self._tls.depth = depth + 1
            ok = False
            try:
                yield self
                ok = True
            finally:
                self._tls.depth = depth
                if depth == 0:
                    if ok:
                        self.conn.commit()
                    else:
                        try:
                            self.conn.rollback()
                        except sqlite3.Error:
                            pass

    # --- meta ---------------------------------------------------------
    def get_meta(self, key, default=None):
        row = self.q1("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.ex("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))

    def close(self):
        with self.lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass


def backup_db(path, tag="auto"):
    """删数据之前先给自己留一条后路。

    2026-11-17 的事故就是因为 sync_companies 按一份不完整的成员名单
    把整群的公司/K 线/持仓删了。现在所有破坏性操作前都先备份一份。
    返回备份文件路径（失败返回 None）。
    """
    import time
    try:
        bdir = os.path.join(os.path.dirname(path), "..", "backups")
        bdir = os.path.abspath(bdir)
        os.makedirs(bdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = os.path.basename(path)
        dst = os.path.join(bdir, "%s.%s_%s" % (base, tag, stamp))
        src = sqlite3.connect(path)
        out = sqlite3.connect(dst)
        src.backup(out)
        out.close()
        src.close()
        return dst
    except Exception as exc:
        print("[stockmarket] backup failed for %s: %s" % (path, exc))
        return None


def db_path(data_dir, gid):
    gid = str(gid).strip() or "0"
    safe = "".join(ch for ch in gid if ch.isalnum() or ch in "-_")
    if not safe:
        safe = "0"
    return os.path.join(data_dir, "g_%s.db" % safe)


def get_db(data_dir, gid):
    """取（并缓存）某个群的数据库句柄。"""
    path = db_path(data_dir, gid)
    with _lock:
        db = _cache.get(path)
        if db is None:
            db = GroupDB(path)
            _cache[path] = db
        return db


def get_registry(data_dir):
    """跨群的「用户 → 最近使用群」登记表。

    Dice! 私聊时传不上群号（msg.fromGroup 是空串，不是 nil），
    靠这张表把用户认回他最近玩过的那个群，私聊也能直接拿到交易页，
    不用等 QQ 对新好友的放行冷却。
    """
    path = os.path.join(data_dir, "registry.db")
    with _lock:
        db = _cache.get(path)
        if db is None:
            db = GroupDB(path)
            db.conn.executescript(REGISTRY_SCHEMA)
            _migrate_registry(db)
            db.conn.commit()
            _cache[path] = db
        return db


def _migrate_registry(db):
    """给跨群登记表补列：区分「本人操作过的群」和「被整群登记进来的群」。

    【为什么要分两个】原来 user_group 一个 uid 只有一行、一个 gid，
    而整群登记（群里任意一人发一次「炒股」，全群成员都登记进去）
    是无差别覆盖的 —— 于是在 A、B 两个群都有账户的人，
    会被「别人在另一个群发一次炒股」改判到那个群去。
    self_gid / self_ts 只记本人真正操作过的群，认群时优先用它。
    """
    try:
        cols = {r["name"] for r in db.q("PRAGMA table_info(user_group)")}
        for col, decl in (("self_gid", "TEXT"), ("self_ts", "INTEGER")):
            if col not in cols:
                db.ex("ALTER TABLE user_group ADD COLUMN %s %s" % (col, decl))
        # 存量数据：当前 gid 就是他被认回去的那个群，当成「本人操作过」，
        # 这样上线后行为跟今天一致，不会突然把谁改判到别的群。
        db.ex("UPDATE user_group SET self_gid=gid, self_ts=ts WHERE self_gid IS NULL")
    except sqlite3.Error:
        pass


def known_groups(data_dir):
    """列出已有群库（用于行情引擎统一 tick）。"""
    out = []
    if not os.path.isdir(data_dir):
        return out
    for name in sorted(os.listdir(data_dir)):
        if name.startswith("g_") and name.endswith(".db"):
            gid = name[2:-3]
            # 「0 号群」是老版本私聊 bug 的脏数据（把空串当群号造出来的假群）：
            # 里面只有空账户、没有行情，但它躺在 data 目录里，
            # known_groups 一扫就会把它当成一个真群 tick，白白耗资源，
            # 还会让 /health 的群列表里一直挂个 "0"。直接不认。
            if gid in ("", "0"):
                continue
            out.append(gid)
    return out


def now():
    return int(time.time())


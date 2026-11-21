# -*- coding: utf-8 -*-
"""个人借贷 P2P（v4.21）。

【为什么要有这条线】
银行的贷款是「无差别放水」：利率固定、额度按净值算、没人管你还不还。
群友们真正缺的是「熟人之间的一笔小钱」—— 我先借你五千，
你两周还我，利息多少咱们商量。这条线把「人情」变成了可玩的机制，
也让群友之间多了一件可以互相开口的事。

【核心口径（一句话）】
借出人承担真实风险，系统垫一半，借款人跑不掉。

三档：
  正常  到期还清 / 提前还  -> 出借人拿本金 + 利息；借款人信用 +3（提前还再 +1）
  逾期  过到期日还没还      -> 罚息照收（日息 x3，判给出借人）；信用 -10；进失信榜
  破产  资不抵债           -> 系统垫 50% 给债主，剩下 50% 不免除，
                              借款人进失信期，打工/签到收入的 30% 强制还债

【为什么 P2P 的债也算进 users.loan】
users.loan 是全站唯一的债务口径：强平、破产清算、计息、司法重整全都读它。
P2P 的债一旦进这个口径，这套已经调了两年的风控就自动复用了 ——
不用再写第二套，也就不会出现「银行贷会强平、P2P 贷不会」这种破洞。

【单位约定】
利率对外统一用「千分之」：0.5 = 万分之五 = 日息 0.05%。
（万分之一 = 0.1‰，千分之三 = 3‰。内部一律存小数率 0.0005。）
"""
import time

from db import now

# 还没还清的债：正常 / 逾期 / 违约（失信）
LIVE = ("open", "late", "default")


# ---------------------------------------------------------------- 小工具
def _cfg(cfg, key, default):
    """取配置项，缺省 / 脏值都退回 default。"""
    try:
        v = cfg.get(key, default)
    except AttributeError:
        return default
    if v is None:
        return default
    return v


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _col(row, name, default=None):
    """安全取列：老库里没有这列时返回 default（而不是 IndexError）。"""
    if row is None:
        return default
    try:
        return row[name]
    except (IndexError, KeyError):
        return default


def _ledger(db, qq, delta, reason):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    if row is None:
        return
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (str(qq), now(), round(_num(delta), 2), round(_num(row["cash"]), 2), reason))


def _nick(db, qq):
    row = db.q1("SELECT nick FROM users WHERE qq=?", (str(qq),))
    if row is None:
        return str(qq)
    return row["nick"] or str(qq)


def _rate_text(rate):
    rate = max(0.0, _num(rate))
    if rate <= 0:
        return "免息"
    perm = ("%.1f" % (rate * 10000)).rstrip("0").rstrip(".")
    return "日息 %.3f%%（万分之%s）" % (rate * 100, perm)


def _money(v):
    return "%.2f" % _num(v)


# ---------------------------------------------------------------- 基础查询
def _user(db, qq):
    return db.q1("SELECT * FROM users WHERE qq=?", (str(qq),))


def credit_of(db, qq):
    """信用分（0~100）。老号没有这列时按初始分算。"""
    row = _user(db, qq)
    if row is None:
        return 0
    v = _col(row, "credit")
    if v is None:
        return _int(_cfg({}, "p2p_credit_init", 60), 60)
    return _int(v, 0)


def _add_credit(db, qq, delta):
    """加 / 减信用分（0~100 夹紧）。delta 可以是负数 —— 逾期、违约要扣分。"""
    delta = _int(delta, 0)
    if delta == 0:
        return
    db.ex("UPDATE users SET credit=MAX(0, MIN(100, credit+?)) WHERE qq=?",
          (delta, str(qq)))


def _parts(row):
    """一笔债现在的 (未还本金, 未付利息, 未付罚息)。

    interest_paid / penalty_paid / repaid 三个字段的口径是「已经不用再还的
    部分」—— 既是真付掉的，也可能是破产清算时核销掉的。单独用 writeoff
    字段记下核销了多少，只用于展示。
    """
    principal = max(0.0, _num(_col(row, "amount")) - _num(_col(row, "repaid")))
    interest = max(0.0, _num(_col(row, "interest")) - _num(_col(row, "interest_paid")))
    penalty = max(0.0, _num(_col(row, "penalty")) - _num(_col(row, "penalty_paid")))
    return principal, interest, penalty


def outstanding_of(row):
    p, i, pe = _parts(row)
    return round(p + i + pe, 2)


def _borrower_rows(db, qq):
    return db.q("SELECT * FROM p2p_loans WHERE borrower_qq=? AND status IN "
                "('open','late','default') ORDER BY due_at, id", (str(qq),))


def outstanding(db, qq):
    """这个人在 P2P 体系里欠总额（= users.loan 里属于 P2P 的那部分）。"""
    total = 0.0
    for r in _borrower_rows(db, qq):
        total += outstanding_of(r)
    return round(total, 2)


def receivable(db, qq):
    """这个人作为债主还没收回的钱。

    【为什么不算进净值】债主的债权不是「已经到手的资产」—— 对方可能违约、
    可能已经破产。把它算进净值会让「放贷」看起来像「存款」，
    玩家就会无脑借出所有现金。风险必须留在净值之外才叫风险。
    """
    total = 0.0
    for r in db.q("SELECT * FROM p2p_loans WHERE lender_qq=? AND status IN "
                  "('open','late','default')", (str(qq),)):
        total += outstanding_of(r)
    return round(total, 2)


def in_default(db, qq):
    """在失信名单里吗（有一笔以上违约中的债）。"""
    row = _user(db, qq)
    if row is not None and _int(_col(row, "p2p_overdue"), 0):
        return True
    r = db.q1("SELECT COUNT(*) c FROM p2p_loans WHERE borrower_qq=? AND status='default'",
              (str(qq),))
    return bool(r and r["c"])


def sync_overdue(db, qq):
    """同步 users.p2p_overdue 标记。返回 1/0。"""
    flag = 1 if in_default(db, qq) else 0
    db.ex("UPDATE users SET p2p_overdue=? WHERE qq=?", (flag, str(qq)))
    return flag


def _count(db, sql, args=()):
    row = db.q1(sql, args)
    return int(row["c"]) if row else 0


# ---------------------------------------------------------------- 计息
def _auto_mature(db, cfg, qq, loan_id):
    """到期自动扣款：钱够就自动还掉，省得玩家忘记。

    【为什么准点还钱不算逾期】到期的那一刻系统就自动扣款了，
    扣的是「本金 + 到期前计提的正常利息」，那一点点罚息直接核销 ——
    否则会出现「明明按时还了，信用分却掉了」这种让人血压高的设计。

    返回 True 表示这笔已经自动结清。
    """
    row = db.q1("SELECT * FROM p2p_loans WHERE id=?", (loan_id,))
    if row is None:
        return False
    principal, interest, penalty = _parts(row)
    claim = round(principal + interest, 2)
    if claim <= 0:
        return False
    u = _user(db, qq)
    cash = _num(u["cash"]) if u else 0.0
    if cash < claim:
        return False
    if penalty > 0:
        _write_off(db, row, penalty)
        row = db.q1("SELECT * FROM p2p_loans WHERE id=?", (loan_id,))
    db.ex("UPDATE users SET cash=cash-?, loan=MAX(0, loan-?) WHERE qq=?",
          (claim, claim + penalty, qq))
    lender = _col(row, "lender_qq")
    db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (claim, lender))
    _ledger(db, lender, claim, "P2P收款（%s）" % _nick(db, qq))
    _ledger(db, qq, -claim, "P2P还款")
    _apply_payment(db, row, claim)
    db.ex("UPDATE p2p_loans SET status='repaid' WHERE id=?", (loan_id,))
    _add_credit(db, qq, _int(_cfg(cfg, "p2p_credit_repay", 3), 3))
    sync_overdue(db, qq)
    return True


def accrue(db, cfg, qq):
    """逐笔计提 P2P 利息与罚息。

    【为什么用绝对时间差】tick 是 2 秒一次，如果按 tick 累加，
    逾期的判定会出现「这一秒在到期点前面、下一秒在后面」的毛刺。
    这里按 [上次计提时间, 现在] 的绝对区间切两段（到期前 / 到期后），
    罚息只会从到期那一刻开始算，误差为零。

    【为什么违约后停止计提】违约的债已经进入失信期，罚息再滚下去就是
    永远还不完的死债（利滚利）。失信期的惩罚是「30% 收入强制还」，
    不是「罚息无限叠加」，所以 status='default' 的债不再计提。
    """
    import recovery
    import coupons
    qq = str(qq)
    user = _user(db, qq)
    if user is None:
        return
    # 纾困期间债务冻结（跟银行贷一个口径：雪上加霜没必要）；
    # 免息券同样冻结。冻结时仍然要把 accrued_at 推到当前，
    # 否则解冻后会一次性补上冻结期间的全部利息。
    freeze = recovery.in_relief_row(user) or coupons.loan_waived(db, qq)
    t = now()
    late_mult = _num(_cfg(cfg, "p2p_penalty_mult", 3.0), 3.0)
    def_days = _int(_cfg(cfg, "p2p_default_days", 7), 7)
    credit_late = _int(_cfg(cfg, "p2p_credit_late", -10), -10)
    credit_def = _int(_cfg(cfg, "p2p_credit_default", -20), -20)
    for r in _borrower_rows(db, qq):
        status = _col(r, "status") or "open"
        if status == "default":
            continue                       # 失信期债冻结
        last = _int(_col(r, "accrued_at")) or _int(_col(r, "ts"))
        if t <= last:
            continue
        if freeze:
            db.ex("UPDATE p2p_loans SET accrued_at=? WHERE id=?", (t, r["id"]))
            continue
        principal = _parts(r)[0]
        if principal <= 0:
            continue
        due = _int(_col(r, "due_at"))
        rate = _num(_col(r, "rate"))
        normal_sec = max(0, min(t, due) - last)
        late_sec = max(0, t - max(last, due))
        add_int = principal * rate * normal_sec / 86400.0
        add_pen = principal * rate * late_mult * late_sec / 86400.0
        db.ex("UPDATE p2p_loans SET interest=interest+?, penalty=penalty+?, accrued_at=? "
              "WHERE id=?", (round(add_int, 4), round(add_pen, 4), t, r["id"]))
        if add_int + add_pen > 0:
            # 利息只加负债、不扣现金（跟银行贷同口径，避免净值被扣两遍）
            db.ex("UPDATE users SET loan=loan+? WHERE qq=?",
                  (round(add_int + add_pen, 4), qq))
        if late_sec > 0 and status == "open":
            # 到期了：现金够就自动扣款（准点还钱不算逾期，不收罚息）
            if _auto_mature(db, cfg, qq, r["id"]):
                continue
            db.ex("UPDATE p2p_loans SET status='late' WHERE id=?", (r["id"],))
            _add_credit(db, qq, credit_late)
        if t > due + def_days * 86400 and status in ("open", "late"):
            db.ex("UPDATE p2p_loans SET status='default' WHERE id=?", (r["id"],))
            _add_credit(db, qq, credit_def)
            sync_overdue(db, qq)


# ---------------------------------------------------------------- 申请 / 决定
def expire_old(db, cfg):
    """把超时的待处理申请作废（默认 24 小时）。返回作废条数。"""
    ttl = _int(_cfg(cfg, "p2p_request_ttl_hours", 24), 24) * 3600
    n = db.ex("UPDATE p2p_requests SET status='expired', decided_at=? "
              "WHERE status='pending' AND created_at < ?", (now(), now() - ttl))
    return n or 0


def _find_lender(db, target_qq, target_name):
    """按 QQ 号或昵称找债主。返回 (row, 错误文案)。"""
    # Lua 侧可能传空串（"用昵称找"），Python 里空串是真值，必须先归一
    target_qq = str(target_qq).strip() if target_qq is not None else ""
    if target_qq:
        row = db.q1("SELECT * FROM users WHERE qq=?", (str(target_qq),))
        if row is None:
            return None, ("没找到 QQ %s 这个用户 —— 他得先在群里发过一次「炒股」开户"
                          % target_qq)
        return row, ""
    name = (target_name or "").strip()
    if not name:
        return None, "没说要找谁借：用法【申请借款 金额 @某某 天数】"
    exact = db.q("SELECT * FROM users WHERE nick=? ORDER BY created_at", (name,))
    if len(exact) == 1:
        return exact[0], ""
    if len(exact) > 1:
        names = "、".join((c["nick"] or c["qq"]) for c in exact[:4])
        return None, "有多个叫「%s」的人（%s），请直接 @ 对方" % (name, names)
    like = db.q("SELECT * FROM users WHERE nick LIKE ? ORDER BY created_at LIMIT 5",
                ("%" + name + "%",))
    if len(like) == 1:
        return like[0], ""
    if len(like) > 1:
        names = "、".join((c["nick"] or c["qq"]) for c in like[:4])
        return None, "有多个名字带「%s」的人（%s），请直接 @ 对方" % (name, names)
    return None, ("没找到叫「%s」的人 —— 他得先在群里发过一次「炒股」开户"
                  % name)


def _pair_today_count(db, a, b):
    """同一对今天已经成交过几笔（防对倒刷利息）。"""
    return _count(db, "SELECT COUNT(*) c FROM p2p_loans WHERE ts>=? AND "
                      "((lender_qq=? AND borrower_qq=?) OR (lender_qq=? AND borrower_qq=?))",
                  (_day_start(), str(a), str(b), str(b), str(a)))


def _day_start():
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def apply_for(db, cfg, qq, nick, amount, term_days, target_qq, target_name):
    """发起一笔借款申请。

    返回 (ok, 文案, info)。
    """
    import market
    import recovery
    qq = str(qq)
    expire_old(db, cfg)
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return False, "金额不合法", None
    if amount <= 0:
        return False, "金额要大于 0", None
    max_abs = _num(_cfg(cfg, "p2p_max_abs", 200000.0), 200000.0)
    if amount > max_abs:
        return False, ("单笔最多 %.0f 元 —— 个人借贷不是银行信贷，别一口吃成胖子"
                       % max_abs), None
    lo_term = _int(_cfg(cfg, "p2p_min_term_days", 1), 1)
    hi_term = _int(_cfg(cfg, "p2p_max_term_days", 30), 30)
    term = _int(term_days, 0)
    if term <= 0:
        term = _int(_cfg(cfg, "p2p_default_term_days", 7), 7)
    if term < lo_term or term > hi_term:
        return False, "期限只能 %d~%d 天" % (lo_term, hi_term), None
    # ---- 借入人资格 ----
    market.ensure_user(db, qq, nick or "", _num(_cfg(cfg, "initial_cash", 100000.0)))
    if recovery.in_reopen(db, qq):
        return False, ("重开期（还剩 %d 天）不能借钱 —— 先用自己的本金把本金挣回来"
                       % recovery.reopen_days_left(db, qq)), None
    if recovery.in_relief(db, qq):
        return False, "纾困 / 司法重整期间不能新增借款，先把自己的窟窿填上", None
    if in_default(db, qq):
        return False, "你还在失信名单里 —— 先把旧账还清，才能申请新借款", None
    min_credit = _int(_cfg(cfg, "p2p_credit_min_apply", 30), 30)
    cr = credit_of(db, qq)
    if cr < min_credit:
        return False, ("你的信用分只有 %d（申请门槛 %d）—— 按时还钱才能涨回来"
                       % (cr, min_credit)), None
    # ---- 债主 ----
    lender, err = _find_lender(db, target_qq, target_name)
    if err:
        return False, err, None
    lq = lender["qq"]
    if lq == qq:
        return False, "自己给自己借钱有意思吗", None
    if lender["bankrupt"] or recovery.in_relief(db, lq):
        return False, "%s 现在自己都自顾不暇，先找别人吧" % (lender["nick"] or lq), None
    if in_default(db, lq):
        return False, "%s 还在失信名单里，不能当债主" % (lender["nick"] or lq), None
    mx = _int(_cfg(cfg, "p2p_max_pending", 3), 3)
    if _count(db, "SELECT COUNT(*) c FROM p2p_requests WHERE lender_qq=? AND status='pending'",
              (lq,)) >= mx:
        return False, ("%s 手里已经压着 %d 条待处理的申请了，先别再添乱"
                       % (lender["nick"] or lq, mx)), None
    # 同一个债主只能有一条待处理申请 —— 否则债主那边「同意借款 小红」
    # 会撞上多条同名申请，不知道在答哪条。
    if _count(db, "SELECT COUNT(*) c FROM p2p_requests WHERE qq=? AND lender_qq=? "
              "AND status='pending'", (qq, lq)):
        return False, "你已经向 %s 申请过了，等他答复" % (lender["nick"] or lq), None
    if _count(db, "SELECT COUNT(*) c FROM p2p_requests WHERE qq=? AND status='pending'",
              (qq,)) >= mx:
        return False, "你已经有 %d 条申请没人理了，等一等" % mx, None
    if _pair_today_count(db, qq, lq):
        return False, ("你和 %s 今天已经有一笔了，明天再来（同一对每天只能借一次）"
                       % (lender["nick"] or lq)), None
    # ---- 债务上限：跟银行贷款共用「净值 x 倍数」----
    acct = market.account(db, cfg, qq)
    if acct is None:
        return False, "你还没有账户，先在群里发一次「炒股」开户", None
    equity = max(0.0, _num(acct["equity"]))
    ratio = _num(_cfg(cfg, "max_loan_ratio", 3.0), 3.0)
    debt_cap = equity * ratio
    room = debt_cap - _num(acct["loan"])
    if amount > room:
        return False, ("借不了这么多：你的总负债上限 = 净值 %.0f x %s = %.0f，"
                       "你现在已欠 %.0f，还差 %.0f 的空间"
                       % (equity, ratio, debt_cap, _num(acct["loan"]), max(0.0, room))), None
    # ---- 落库 ----
    desc = "借 %.0f 元 / %d 天 / 日息待定" % (amount, term)
    db.ex("INSERT INTO p2p_requests(qq,lender_qq,amount,term_days,rate,status,"
          "created_at,decided_at,msg) VALUES(?,?,?,?,0,'pending',?,NULL,?)",
          (qq, lq, amount, term, now(), desc))
    ttl = _int(_cfg(cfg, "p2p_request_ttl_hours", 24), 24)
    my = nick or qq
    msg = ("📨 借款申请已发出！\n"
           "你向 %s 申请借款 %.0f 元，期限 %d 天。\n"
           "等 %s 回复【同意借款 %s 利率】或【拒绝借款 %s】。\n"
           "（%d 小时没人处理就自动作废）"
           % (lender["nick"] or lq, amount, term, lender["nick"] or lq, my, my, ttl))
    return True, msg, {"lender": lq, "lender_nick": lender["nick"],
                       "amount": amount, "term": term}


def decide(db, cfg, lender_qq, nick, borrower_qq, borrower_name, rate_raw, accept):
    """债主答复借款申请：同意（放款）或拒绝。

    利率单位是「千分之」：0.5 = 万分之五 = 日息 0.05%。0 表示免息互助。
    """
    import market
    import recovery
    lender_qq = str(lender_qq)
    borrower_qq = str(borrower_qq).strip() if borrower_qq is not None else ""
    borrower_name = (str(borrower_name).strip() if borrower_name is not None else "")
    expire_old(db, cfg)
    rows = db.q("SELECT * FROM p2p_requests WHERE lender_qq=? AND status='pending' "
                "ORDER BY created_at DESC", (lender_qq,))
    if not rows:
        return False, "你名下没有待处理的借款申请", None
    cand = []
    for r in rows:
        if borrower_qq and r["qq"] == str(borrower_qq):
            cand = [r]
            break
        if not borrower_qq and borrower_name:
            if _nick(db, r["qq"]) == borrower_name:
                cand.append(r)
    if not cand:
        return False, "没找到对应的待处理申请", None
    # 同一个债主对同一个人的待处理申请只会有一条（apply_for 里拦过），
    # 万一有重复就取最新的一条，别把债主卡住。
    row = cand[0]
    bq = row["qq"]
    amount = _num(row["amount"])
    term = _int(row["term_days"])
    bnick = _nick(db, bq)
    lnick = nick or lender_qq
    if not accept:
        db.ex("UPDATE p2p_requests SET status='rejected', decided_at=? WHERE id=?",
              (now(), row["id"]))
        return True, ("❌ %s 拒绝了 %s 的 %.0f 元借款申请。"
                      % (lnick, bnick, amount)), {"status": "rejected"}
    # ---- 利率 ----
    try:
        rate = round(float(rate_raw) / 1000.0, 8)
    except (TypeError, ValueError):
        return False, ("利率没看懂：写「日息 x 千分之几」，比如 0.5 = 万分之五"
                       "（0.05%/天），0 表示免息互助"), None
    if rate < 0:
        return False, "利率不能是负数（0 表示免息互助）", None
    lo = _num(_cfg(cfg, "p2p_min_rate_per_day", 0.0001), 0.0001)
    hi = _num(_cfg(cfg, "p2p_max_rate_per_day", 0.003), 0.003)
    if rate > 0 and (rate < lo - 1e-9 or rate > hi + 1e-9):
        return False, ("日息只能在 %.3f%% ~ %.3f%% 之间（单位是千分之），0 表示免息互助"
                       % (lo * 100, hi * 100)), None
    # ---- 债主资格复查 ----
    lrow = _user(db, lender_qq)
    if lrow is None:
        return False, "你还没有账户", None
    if lrow["bankrupt"] or recovery.in_relief(db, lender_qq):
        return False, "你自己现在也自顾不暇，先把自己的窟窿填上", None
    if in_default(db, lender_qq):
        return False, "你还在失信名单里，不能放贷", None
    if _pair_today_count(db, bq, lender_qq):
        return False, "你们今天已经有一笔了，明天再来（同一对每天只能借一次）", None
    cash = _num(lrow["cash"])
    lratio = _num(_cfg(cfg, "p2p_lender_cash_ratio", 0.5), 0.5)
    if amount > cash * lratio:
        return False, ("你账上只有 %.0f 元，单笔最多放 %.0f 元（现金的 %.0f%%）"
                       % (cash, cash * lratio, lratio * 100)), None
    lacct = market.account(db, cfg, lender_qq)
    if lacct is not None and _num(lacct["equity"]) - amount < 0:
        return False, "放了这笔你的净值就变负了 —— 留点家底", None
    # ---- 借款人资格复查 ----
    acct = market.account(db, cfg, bq)
    if acct is None:
        return False, "对方还没有账户", None
    equity = max(0.0, _num(acct["equity"]))
    debt_cap = equity * _num(_cfg(cfg, "max_loan_ratio", 3.0), 3.0)
    if _num(acct["loan"]) + amount > debt_cap:
        return False, ("对方现在的负债已经到顶了（上限 %.0f），这笔放不下" % debt_cap), None
    # ---- 放款 ----
    t = now()
    due = t + term * 86400
    db.ex("UPDATE users SET cash=cash-? WHERE qq=?", (amount, lender_qq))
    db.ex("UPDATE users SET cash=cash+?, loan=loan+? WHERE qq=?", (amount, amount, bq))
    db.ex("INSERT INTO p2p_loans(request_id,lender_qq,borrower_qq,amount,rate,term_days,"
          "interest,interest_paid,penalty,penalty_paid,repaid,writeoff,ts,due_at,"
          "accrued_at,status) VALUES(?,?,?,?,?,?,0,0,0,0,0,0,?,?,?, 'open')",
          (row["id"], lender_qq, bq, amount, rate, term, t, due, t))
    db.ex("UPDATE p2p_requests SET status='accepted', decided_at=?, rate=? WHERE id=?",
          (t, rate, row["id"]))
    _ledger(db, lender_qq, -amount, "P2P放款给 %s" % bnick)
    _ledger(db, bq, amount, "P2P借款（%s 借给）" % lnick)
    total = amount * (1 + rate * term)
    msg = ("✅ %s 借给 %s %.0f 元，%s，%d 天到期，共还 %.2f 元。\n"
           "到期自动从 %s 的账户扣款；逾期每天罚息 x%s，信用分也要扣。"
           % (lnick, bnick, amount, _rate_text(rate), term, total, bnick,
              _num(_cfg(cfg, "p2p_penalty_mult", 3.0), 3.0)))
    return True, msg, {"borrower": bq, "borrower_nick": bnick, "amount": amount,
                       "rate": rate, "term": term, "due_at": due, "total": round(total, 2)}


# ---------------------------------------------------------------- 还款
def _apply_payment(db, row, amount):
    """把钱还到这一笔债上：罚息 -> 利息 -> 本金。返回 (罚息, 利息, 本金)。"""
    principal, interest, penalty = _parts(row)
    take_pen = min(amount, penalty)
    amount -= take_pen
    take_int = min(amount, interest)
    amount -= take_int
    take_pri = min(amount, principal)
    if take_pen or take_int or take_pri:
        db.ex("UPDATE p2p_loans SET penalty_paid=penalty_paid+?, interest_paid=interest_paid+?, "
              "repaid=repaid+? WHERE id=?",
              (take_pen, take_int, take_pri, row["id"]))
    return take_pen, take_int, take_pri


def distribute(db, cfg, qq, amount, borrower_log=True):
    """把钱摊到 P2P 债上。逾期的先还，然后按到期日。

    返回 (实际还掉, 明细列表)。
    """
    qq = str(qq)
    rows = _borrower_rows(db, qq)
    rows.sort(key=lambda r: (0 if (_col(r, "status") or "open") in ("default", "late") else 1,
                             _int(_col(r, "due_at")), _int(_col(r, "id"))))
    left = _num(amount)
    total = 0.0
    detail = []
    for r in rows:
        if left <= 0:
            break
        take_pen, take_int, take_pri = _apply_payment(db, r, left)
        got = take_pen + take_int + take_pri
        if got <= 0:
            continue
        left -= got
        total += got
        lender = _col(r, "lender_qq")
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (got, lender))
        _ledger(db, lender, got, "P2P收款（%s）" % _nick(db, qq))
        if borrower_log:
            _ledger(db, qq, -got, "P2P还款")
        after = outstanding_of(db.q1("SELECT * FROM p2p_loans WHERE id=?", (r["id"],)))
        if after <= 0.01:
            db.ex("UPDATE p2p_loans SET status='repaid' WHERE id=?", (r["id"],))
            early = now() < _int(_col(r, "due_at"))
            gain = _int(_cfg(cfg, "p2p_credit_repay", 3), 3)
            if early:
                gain += _int(_cfg(cfg, "p2p_credit_early", 1), 1)
            _add_credit(db, qq, gain)
            detail.append("还清 %s 的 %.0f 元（%s，信用 +%d）"
                          % (_nick(db, lender), _num(_col(r, "amount")),
                             "提前还清" if early else "按期还清", gain))
        else:
            detail.append("还给 %s %.2f 元（还欠 %.2f）"
                          % (_nick(db, lender), got, after))
    sync_overdue(db, qq)
    return round(total, 2), detail


def pay(db, cfg, qq, amount):
    """【还P2P】只还个人借贷那部分（不动银行贷款）。"""
    import market
    qq = str(qq)
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return False, "金额不合法", None
    if amount <= 0:
        return False, "金额要大于 0", None
    acct = market.account(db, cfg, qq)
    if acct is None:
        return False, "你还没有账户", None
    owe = outstanding(db, qq)
    if owe <= 0:
        return False, "你没有个人借贷的债要还", None
    cash = _num(acct["cash"])
    if amount > cash:
        return False, "现金不够，账上只有 %.2f" % cash, None
    amount = min(amount, owe)
    db.ex("UPDATE users SET cash=cash-?, loan=MAX(0, loan-?) WHERE qq=?",
          (amount, amount, qq))
    paid, detail = distribute(db, cfg, qq, amount)
    if paid <= 0:
        db.ex("UPDATE users SET cash=cash+?, loan=loan+? WHERE qq=?", (amount, amount, qq))
        return False, "没还成（可能账已经对不上了，麻烦找管理员看看）", None
    msg = "💸 还了 %.2f 元个人借贷。\n%s" % (paid, "\n".join(detail))
    left = outstanding(db, qq)
    if left <= 0:
        msg += "\n🎉 你的个人借贷全部还清，信用分涨了！"
    return True, msg, {"paid": paid, "left": left, "detail": detail}


def garnish(db, cfg, qq, income):
    """失信期强制还款：把收入的一部分直接拿去还 P2P 债。

    返回 (被强制扣掉的钱, 实际到手的钱)。
    """
    qq = str(qq)
    income = _num(income)
    if income <= 0:
        return 0.0, income
    if not in_default(db, qq):
        return 0.0, income
    ratio = _num(_cfg(cfg, "p2p_garnish_ratio", 0.3), 0.3)
    use = round(income * ratio, 2)
    if use <= 0:
        return 0.0, income
    db.ex("UPDATE users SET cash=cash-?, loan=MAX(0, loan-?) WHERE qq=?", (use, use, qq))
    distribute(db, cfg, qq, use, borrower_log=True)
    return use, round(income - use, 2)


# ---------------------------------------------------------------- 破产清算
def _write_off(db, row, amount):
    """核销一笔债（不付钱，只是把账划掉）：罚息 -> 利息 -> 本金。"""
    principal, interest, penalty = _parts(row)
    take_pen = min(amount, penalty)
    amount -= take_pen
    take_int = min(amount, interest)
    amount -= take_int
    take_pri = min(amount, principal)
    amount -= take_pri
    got = take_pen + take_int + take_pri
    if got > 0:
        db.ex("UPDATE p2p_loans SET penalty_paid=penalty_paid+?, interest_paid=interest_paid+?, "
              "repaid=repaid+?, writeoff=writeoff+? WHERE id=?",
              (take_pen, take_int, take_pri, got, row["id"]))
    return got


def on_borrower_bankrupt(db, cfg, qq):
    """借款人破产：系统垫一半给债主，剩下一半进失信期慢慢还。

    返回 (系统垫付总额, 借款人剩余 P2P 欠款)。
    """
    qq = str(qq)
    ratio = _num(_cfg(cfg, "p2p_bankrupt_cut_ratio", 0.5), 0.5)
    bailout = 0.0
    for r in _borrower_rows(db, qq):
        claim = outstanding_of(r)
        if claim <= 0:
            continue
        pay = round(claim * ratio, 2)
        if pay <= 0:
            continue
        _write_off(db, r, pay)
        lender = _col(r, "lender_qq")
        db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (pay, lender))
        _ledger(db, lender, pay, "P2P系统垫付（%s 破产）" % _nick(db, qq))
        bailout += pay
        db.ex("UPDATE p2p_loans SET status='default' WHERE id=?", (r["id"],))
    left = outstanding(db, qq)
    sync_overdue(db, qq)
    return round(bailout, 2), left


def on_lender_bankrupt(db, cfg, qq):
    """债主自己破产：他名下的债权按 50% 折算（风险对称）。

    【为什么对称】借款人破产时系统替他垫了一半，债主只损失一半；
    债主自己破产，他的债权在他自己的破产清算里同样打折，
    所以借款人这边也只承担一半 —— 不然「当债主」就成了无风险选项。

    返回核销总额。
    """
    qq = str(qq)
    ratio = _num(_cfg(cfg, "p2p_bankrupt_cut_ratio", 0.5), 0.5)
    written = 0.0
    rows = db.q("SELECT * FROM p2p_loans WHERE lender_qq=? AND status IN "
                "('open','late','default')", (qq,))
    for r in rows:
        claim = outstanding_of(r)
        if claim <= 0:
            continue
        cut = round(claim * ratio, 2)
        if cut <= 0:
            continue
        got = _write_off(db, r, cut)
        if got <= 0:
            continue
        bq = _col(r, "borrower_qq")
        db.ex("UPDATE users SET loan=MAX(0, loan-?) WHERE qq=?", (got, bq))
        _ledger(db, bq, 0.0, "债主破产：%s 的债权打折核销 %.2f" % (_nick(db, qq), got))
        written += got
    return round(written, 2)


# ---------------------------------------------------------------- 展示
def _due_text(due_at):
    left = due_at - now()
    if left <= 0:
        return "已到期"
    return "还剩 %d 天" % max(1, int(left / 86400) + 1)


def panel_text(db, cfg, qq):
    """【我的借贷】一屏看完。"""
    qq = str(qq)
    row = _user(db, qq)
    if row is None:
        return "你还没有账户 —— 先在群里发一次「炒股」开户。"
    owe = outstanding(db, qq)
    rec = receivable(db, qq)
    lines = ["📋 我的借贷"]
    cr = credit_of(db, qq)
    min_cr = _int(_cfg(cfg, "p2p_credit_min_apply", 30), 30)
    lines.append("⭐ 信用分：%d（申请门槛 %d）" % (cr, min_cr))
    lines.append("💰 现金 %s ｜ 总负债 %s" % (_money(row["cash"]), _money(row["loan"])))
    lines.append("📤 我借出的还没收回：%s" % _money(rec))
    lines.append("📥 我借入的还没还清：%s" % _money(owe))
    if in_default(db, qq):
        lines.append("⚠️ 你在【失信名单】里：打工 / 签到收入的 %d%% 会被强制拿去还债，"
                     "并且不能再申请新借款。"
                     % int(_num(_cfg(cfg, "p2p_garnish_ratio", 0.3), 0.3) * 100))
    # 我借入的
    borrows = _borrower_rows(db, qq)
    if borrows:
        lines.append("")
        lines.append("【我借入的】")
        for r in borrows:
            st = _col(r, "status") or "open"
            flag = {"late": "⚠️逾期", "default": "🚨违约"}.get(st, "")
            lines.append("· 欠 %s %.0f 元，%s，%d 天期，%s"
                         % (_nick(db, _col(r, "lender_qq")), _num(_col(r, "amount")),
                            _rate_text(_col(r, "rate")), _int(_col(r, "term_days")),
                            _due_text(_int(_col(r, "due_at")))))
            lines.append("   还欠 %s %s%s" % (_money(outstanding_of(r)), flag,
                                              "（已进失信期）" if st == "default" else ""))
    # 我借出的
    lents = db.q("SELECT * FROM p2p_loans WHERE lender_qq=? AND status IN "
                 "('open','late','default') ORDER BY due_at, id", (qq,))
    if lents:
        lines.append("")
        lines.append("【我借出的】")
        for r in lents:
            st = _col(r, "status") or "open"
            flag = {"late": "⚠️逾期", "default": "🚨违约"}.get(st, "")
            lines.append("· %s 借走 %.0f 元，%s，%d 天期，%s"
                         % (_nick(db, _col(r, "borrower_qq")), _num(_col(r, "amount")),
                            _rate_text(_col(r, "rate")), _int(_col(r, "term_days")),
                            _due_text(_int(_col(r, "due_at")))))
            lines.append("   没收回来 %s%s" % (_money(outstanding_of(r)), flag))
    # 待处理
    pin = db.q("SELECT * FROM p2p_requests WHERE lender_qq=? AND status='pending' "
               "ORDER BY created_at DESC", (qq,))
    pout = db.q("SELECT * FROM p2p_requests WHERE qq=? AND status='pending' "
                "ORDER BY created_at DESC", (qq,))
    if pin or pout:
        lines.append("")
        lines.append("【待处理】")
    for r in pin:
        ttl = max(0, _int(_cfg(cfg, "p2p_request_ttl_hours", 24), 24) * 3600
                  - (now() - _int(_col(r, "created_at"))))
        lines.append("· %s 想借你 %.0f 元，%d 天（还剩 %d 小时有效）"
                     % (_nick(db, _col(r, "qq")), _num(_col(r, "amount")),
                        _int(_col(r, "term_days")), int(ttl / 3600) + 1))
    for r in pout:
        lines.append("· 你向 %s 申请了 %.0f 元，%d 天，等对方答复"
                     % (_nick(db, _col(r, "lender_qq")), _num(_col(r, "amount")),
                        _int(_col(r, "term_days"))))
    if not borrows and not lents and not pin and not pout:
        lines.append("")
        lines.append("（还没有任何借贷记录。想借就发【申请借款 金额 @某某 天数】，"
                     "想放贷就等别人找你。）")
    return "\n".join(lines)


def default_list(db, cfg, limit=10):
    """【失信名单】公开榜。"""
    rows = db.q("SELECT borrower_qq, COUNT(*) c, COALESCE(SUM(amount-repaid),0) amt "
                "FROM p2p_loans WHERE status='default' GROUP BY borrower_qq "
                "ORDER BY amt DESC LIMIT ?", (int(limit),))
    out = []
    for r in rows:
        out.append({"qq": r["borrower_qq"], "nick": _nick(db, r["borrower_qq"]),
                    "count": int(r["c"]), "amount": round(_num(r["amt"]), 2)})
    return out


def default_text(db, cfg, limit=10):
    rows = default_list(db, cfg, limit)
    if not rows:
        return "📋 失信名单\n（目前一个人都没有，大家都很守约 👍）"
    lines = ["📋 失信名单（公开）"]
    for i, r in enumerate(rows, 1):
        lines.append("%d. %s（%s）—— %d 笔违约，欠 %s"
                     % (i, r["nick"], r["qq"], r["count"], _money(r["amount"])))
    lines.append("—— 失信期间 30% 的打工 / 签到收入会被强制用来还债。")
    return "\n".join(lines)


def web_panel(db, cfg, qq):
    """网页端借贷面板（给 static/app.js 用）。"""
    qq = str(qq)
    row = _user(db, qq)
    if row is None:
        return {"text": "你还没有账户 —— 先在群里发一次「炒股」开户。"}
    borrows = []
    for r in _borrower_rows(db, qq):
        p, i, pe = _parts(r)
        due = _int(_col(r, "due_at"))
        borrows.append({
            "id": r["id"],
            "lender": _col(r, "lender_qq"),
            "lender_nick": _nick(db, _col(r, "lender_qq")),
            "amount": round(_num(_col(r, "amount")), 2),
            "rate": _num(_col(r, "rate")),
            "rate_text": _rate_text(_col(r, "rate")),
            "term": _int(_col(r, "term_days")),
            "due_at": due,
            "days_left": max(0, (due - now()) // 86400),
            "principal": round(p, 2), "interest": round(i, 2), "penalty": round(pe, 2),
            "left": outstanding_of(r),
            "status": _col(r, "status") or "open",
        })
    lents = []
    for r in db.q("SELECT * FROM p2p_loans WHERE lender_qq=? AND status IN "
                  "('open','late','default') ORDER BY due_at, id", (qq,)):
        p, i, pe = _parts(r)
        due = _int(_col(r, "due_at"))
        lents.append({
            "id": r["id"],
            "borrower": _col(r, "borrower_qq"),
            "borrower_nick": _nick(db, _col(r, "borrower_qq")),
            "amount": round(_num(_col(r, "amount")), 2),
            "rate": _num(_col(r, "rate")),
            "rate_text": _rate_text(_col(r, "rate")),
            "term": _int(_col(r, "term_days")),
            "due_at": due,
            "days_left": max(0, (due - now()) // 86400),
            "principal": round(p, 2), "interest": round(i, 2), "penalty": round(pe, 2),
            "left": outstanding_of(r),
            "status": _col(r, "status") or "open",
        })
    pend_in = []
    for r in db.q("SELECT * FROM p2p_requests WHERE lender_qq=? AND status='pending' "
                  "ORDER BY created_at DESC", (qq,)):
        pend_in.append({"id": r["id"], "qq": r["qq"], "nick": _nick(db, r["qq"]),
                        "amount": round(_num(r["amount"]), 2), "term": _int(r["term_days"]),
                        "created_at": _int(r["created_at"])})
    pend_out = []
    for r in db.q("SELECT * FROM p2p_requests WHERE qq=? AND status='pending' "
                  "ORDER BY created_at DESC", (qq,)):
        pend_out.append({"id": r["id"], "lender": r["lender_qq"],
                         "lender_nick": _nick(db, r["lender_qq"]),
                         "amount": round(_num(r["amount"]), 2),
                         "term": _int(r["term_days"]),
                         "created_at": _int(r["created_at"])})
    return {
        "credit": credit_of(db, qq),
        "credit_min": _int(_cfg(cfg, "p2p_credit_min_apply", 30), 30),
        "in_default": in_default(db, qq),
        "garnish": _num(_cfg(cfg, "p2p_garnish_ratio", 0.3), 0.3),
        "penalty_mult": _num(_cfg(cfg, "p2p_penalty_mult", 3.0), 3.0),
        "owe": outstanding(db, qq),
        "receivable": receivable(db, qq),
        "borrows": borrows,
        "lents": lents,
        "pending_in": pend_in,
        "pending_out": pend_out,
        "defaults": default_list(db, cfg),
        "text": panel_text(db, cfg, qq),
    }


def daily_report(db, cfg):
    """跨天播报：到期提醒 + 逾期 / 失信情况。返回空串表示没什么要说的。"""
    t = now()
    lines = []
    soon = db.q("SELECT COUNT(*) c FROM p2p_loans WHERE status='open' "
                "AND due_at<=? AND due_at>?", (t + 86400, t))
    if soon and soon["c"]:
        lines.append("⏰ 有 %d 笔个人借贷将在 24 小时内到期，备好现金（到期自动扣款）。"
                     % soon["c"])
    late = db.q("SELECT COUNT(*) c FROM p2p_loans WHERE status='late'")
    if late and late["c"]:
        lines.append("⚠️ 目前有 %d 笔个人借贷已经逾期，罚息正按日息 x3 累积。"
                     % late["c"])
    bad = db.q("SELECT COUNT(*) c FROM p2p_loans WHERE status='default'")
    if bad and bad["c"]:
        lines.append("🚨 失信名单里有 %d 个人，30% 的打工 / 签到收入会被强制拿去还债。"
                     % bad["c"])
    return "\n".join(lines)

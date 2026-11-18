# -*- coding: utf-8 -*-
"""破产翻身系统（v4.4）。

【为什么要这个】
v4.3 之后群里的反馈是「破产之后完全没有游戏体验」——
破产只能打工，日上限 600，职级封顶 1.8 倍，欠款三十万基本还不完，
等于人直接退群。这个模块给破产玩家一条**看得见的、能预期的翻身路线**。

【三条线】
1. 破产清算：宣告破产那一刻，债务按 `bankrupt_cut_ratio` 打折（默认免 75%）。
   现实里的个人破产就是债务清零，这里保留 25% 当「社会债务」，
   免掉的部分不会凭空消失 —— 是交易所和清算组替你垫了。
2. 破产期福利（**只在纾困流程进行中生效，非破产玩家一分都不多拿**）：
   - 打工每日上限 160 → 1200
   - 打工工资额外 ×3
   - 每日签到奖励 ×3
   - 贷款不再计息（雪上加霜没必要）
3. 纾困里程碑：按「破产期间累计净收入」推进
   - 1.5 万 → 临时救助金 1 万
   - 3 万  → 司法重整：剩余债务一笔勾销 + 重启资金 3 万 + 恢复交易资格

【平衡】
破产的惩罚依旧很重：本金亏光、禁交易、只能打工重建。
非破产玩家的任何收益都不受影响。
"""
from db import now

# 纾困里程碑（need = 破产期间累计净收入）
TIERS = [
    {"need": 15000.0, "cash": 10000.0, "name": "临时救助金",
     "desc": "社区救助中心先垫 1 万块，别饿死",
     "announce": "🆘 你凑够了 1.5 万块纾困收入，拿到了【临时救助金】1 万元应急。"},
    {"need": 30000.0, "cash": 30000.0, "name": "司法重整",
     "desc": "债务一笔勾销，重启资金 3 万，重新开户",
     "announce": "⚖️ 你凑够了 3 万块纾困收入，【司法重整】通过：剩余债务一笔勾销，"
                "拿到 3 万重启资金，交易资格恢复！从零开始吧。"},
]


def _ledger(db, qq, delta, reason):
    row = db.q1("SELECT cash FROM users WHERE qq=?", (str(qq),))
    if row is None:
        return
    db.ex("INSERT INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          (str(qq), now(), round(delta, 2), round(row["cash"], 2), reason))


def _user(db, qq):
    return db.q1("SELECT * FROM users WHERE qq=?", (str(qq),))


def in_relief_row(user):
    """破产纾困流程进行中吗（只看 users 行，不额外查库）。

    【口径】资不抵债（bankrupt=1）时流程进行中；已经回血（净值 >= 0）但
    还欠着债、司法重整没走完时，流程**继续**进行。
    原因：v4.4 的文案写着「攒够收入还能申请债务重整」，要是「净值一回正
    就解除破产」，这条承诺永远兑现不了 —— 玩家攒不到 8 万就被踢出流程，
    之前攒的纾困进度也白搭。
    """
    if user is None:
        return False
    if user["bankrupt"]:
        return True
    if int(user["bankrupt_ts"] or 0) <= 0:
        return False          # 从没破产过
    if int(user["relief_tier"] or 0) >= len(TIERS):
        return False          # 司法重整已经走完
    return float(user["loan"] or 0) > 0   # 还欠着债才算还在纾困


def in_relief(db, qq):
    """破产纾困流程进行中吗（福利 + 里程碑都按这个口径算）。"""
    return in_relief_row(_user(db, qq))


# ---------------------------------------------------------------- 重开期
# 【v4.6】破产清盘之后给玩家的「冷静期」：债清了、资格恢复了，
# 但不能马上又借一屁股债去梭哈 —— 杠杆最多 1 倍，也不能贷款。
# 这是让清盘真正起到「重新做人」作用的关键约束。
def reopen_until(db, qq):
    """重开期截止时间戳（0 = 不在重开期）。"""
    row = db.q1("SELECT reopen_until FROM users WHERE qq=?", (str(qq),))
    if row is None:
        return 0
    try:
        return int(row["reopen_until"] or 0)
    except (TypeError, ValueError):
        return 0


def in_reopen(db, qq):
    """破产清盘后的「重开期」：不能贷款、杠杆最多 1 倍。"""
    return reopen_until(db, qq) > now()


def reopen_days_left(db, qq):
    """重开期还剩几天。"""
    until = reopen_until(db, qq)
    if until <= 0:
        return 0
    return max(0, int((until - now()) / 86400) + 1)


def mark_reopen(db, cfg, qq, days=None):
    """给玩家打上重开期标记，返回截止时间戳。"""
    if days is None:
        days = int(cfg.get("liquidation_lock_days", 30))
    until = now() + int(days) * 86400
    db.ex("UPDATE users SET reopen_until=? WHERE qq=?", (until, str(qq)))
    return until


def sync_bankrupt(db, cfg, qq, equity=None):
    """自愈：净值回正就把破产标记翻回去（禁交易的红条必须跟着净值走）。

    【为什么必须有这个】v4.4 上线后有群友截图：净值 15 万、可用 15 万、
    欠款 0，顶上却还挂着「你已破产，暂时不能交易」的红条。根因是
    bankrupt 是个黏性标记 —— 只有还款、打工、司法重整三处会清；而破产
    清算当场免掉 75% 债务、持仓回血后卖出、签到翻倍、成就奖励这些路径
    都会把净值重新推回正数，却没人负责把标记翻回去，人就被卡在
    「有钱但不能交易」的状态里出不来。

    口径统一成一句话：**资不抵债（净值 < 0）才算破产**，净值回正即恢复。
    返回 True 表示这次刚刚解除。
    """
    qq = str(qq)
    user = _user(db, qq)
    if user is None or not user["bankrupt"]:
        return False
    if equity is None:
        import market
        acct = market.account(db, cfg, qq)
        if acct is None:
            return False
        equity = acct["equity"]
    if equity < 0:
        return False
    # bankrupt_ts 是「破产时间戳」，老数据可能是 0，这里补一个，
    # 让「破产幸存」成就和纾困流程能认到这个人。
    # 破产结束 → bankrupt_since 清零。下次再破产就是「新的第一次」，
    # 强制清盘的 7 天宽限期重新开始算。
    db.ex("UPDATE users SET bankrupt=0, bankrupt_since=0, "
          "bankrupt_ts=CASE WHEN bankrupt_ts>0 THEN bankrupt_ts ELSE ? END WHERE qq=?",
          (now(), qq))
    try:
        import features
        features.check_achievements(db, cfg, qq)   # 「破产幸存」在解除这一刻解锁
    except Exception:
        pass
    return True


def tier_reached(earn):
    """当前已推进到第几档（0 = 一档都没到）。"""
    n = 0
    for i, t in enumerate(TIERS):
        if earn >= t["need"]:
            n = i + 1
    return n


def on_bankrupt(db, cfg, qq):
    """宣告破产时调用：做一次债务清算，并初始化纾困进度。"""
    qq = str(qq)
    user = _user(db, qq)
    if user is None:
        return None
    loan = float(user["loan"] or 0)
    cut_ratio = float(cfg.get("bankrupt_cut_ratio", 0.75))
    cut = 0.0
    if loan > 0 and cut_ratio > 0:
        cut = round(loan * min(1.0, cut_ratio), 2)
        db.ex("UPDATE users SET loan=loan-? WHERE qq=?", (cut, qq))
        db.ex("UPDATE loans SET status='waived' WHERE qq=? AND status='open'", (qq,))
        _ledger(db, qq, 0.0, "破产清算：债务减免 %.2f" % cut)
    # bankrupt_since=「这一次破产的开始时间」，只在第一次写，
    # 重复宣告破产不会刷新它（不然强制清盘的倒计时会被无限重置）。
    db.ex("UPDATE users SET bankrupt_earn=0, relief_tier=0, bankrupt_ts=?, "
          "bankrupt_since=CASE WHEN bankrupt_since>0 THEN bankrupt_since ELSE ? END "
          "WHERE qq=?", (now(), now(), qq))
    # 【v4.12】重开期标记以前是死代码 —— mark_reopen() 全仓只有定义、
    # 没有任何调用点，所以「破产清盘后不能贷款、杠杆最多 1 倍」这条
    # v4.6 就写好的规则从来没真正生效过。现在在这里接上：
    # 宣告破产 = 被强制清盘，清完之后给一个冷静期重新做人。
    # 已经在重开期里就别再往后推 —— 否则反复破产能把冷静期无限延长。
    reopen_txt = ""
    if not in_reopen(db, qq):
        try:
            days = int(cfg.get("reopen_days", 10))
        except (TypeError, ValueError):
            days = 10
        if days > 0:
            mark_reopen(db, cfg, qq, days)
            reopen_txt = ("\n重开期 %d 天：这期间不能贷款、杠杆最多 1 倍，"
                          "先靠打工和签到把本金挣回来。" % days)
    if cut > 0:
        return "清算组帮你减免了 %.2f 元债务，剩余欠款 %.2f。%s" % (cut, loan - cut, reopen_txt)
    return "欠款 %.2f 仍要还。%s" % (loan, reopen_txt)


def earn(db, cfg, qq, amount, note=""):
    """记一笔纾困收入（只有破产期间才累计）。返回触发的里程碑文案列表。"""
    qq = str(qq)
    if amount <= 0:
        return []
    if not in_relief(db, qq):
        return []
    db.ex("UPDATE users SET bankrupt_earn=bankrupt_earn+? WHERE qq=?", (float(amount), qq))
    return settle(db, cfg, qq)


def settle(db, cfg, qq):
    """检查纾困里程碑是否达标，返回要播报的文案列表。"""
    qq = str(qq)
    user = _user(db, qq)
    if user is None:
        return []
    total = float(user["bankrupt_earn"] or 0)
    tier = int(user["relief_tier"] or 0)
    msgs = []
    while tier < len(TIERS) and total >= TIERS[tier]["need"]:
        t = TIERS[tier]
        tier += 1
        cash = float(t["cash"])
        if cash > 0:
            db.ex("UPDATE users SET cash=cash+? WHERE qq=?", (cash, qq))
            _ledger(db, qq, cash, "纾困补助·" + t["name"])
        if tier >= 2:
            # 司法重整：剩余债务一笔勾销 + 恢复交易资格
            loan = float(user["loan"] or 0)
            if loan > 0:
                db.ex("UPDATE users SET loan=0 WHERE qq=?", (qq,))
                db.ex("UPDATE loans SET status='waived' WHERE qq=? AND status='open'", (qq,))
                _ledger(db, qq, 0.0, "司法重整：免除剩余债务 %.2f" % loan)
            db.ex("UPDATE users SET bankrupt=0 WHERE qq=?", (qq,))
        db.ex("UPDATE users SET relief_tier=? WHERE qq=?", (tier, qq))
        msgs.append(t["announce"])
    if msgs:
        # 里程碑可能同时解锁成就（延迟导入，避免 features <-> market 循环依赖）
        try:
            import features
            msgs.extend(features.check_achievements(db, cfg, qq))
        except Exception:
            pass
    return msgs


def status(db, cfg, qq):
    """破产 / 纾困进度总览（给网页和群命令用）。"""
    qq = str(qq)
    user = _user(db, qq)
    if user is None:
        return None
    total = float(user["bankrupt_earn"] or 0)
    tier = int(user["relief_tier"] or 0)
    nxt = None
    if tier < len(TIERS):
        t = TIERS[tier]
        nxt = {"need": t["need"], "left": max(0.0, t["need"] - total),
               "cash": t["cash"], "name": t["name"], "desc": t["desc"]}
    return {
        "bankrupt": bool(user["bankrupt"]),
        "relief": in_relief(db, qq),
        "loan": round(float(user["loan"] or 0), 2),
        "earn": round(total, 2),
        "tier": tier,
        "tier_max": len(TIERS),
        "next": nxt,
        "reopen_until": reopen_until(db, qq),
        "reopen_days": reopen_days_left(db, qq),
        "tiers": [{"need": t["need"], "cash": t["cash"], "name": t["name"], "desc": t["desc"],
                   "done": total >= t["need"]} for t in TIERS],
    }


def text(db, cfg, qq):
    """一句话版纾困进度（群命令用）。"""
    st = status(db, cfg, qq)
    if st is None:
        return "你还没有账户"
    if not st["relief"] and st["tier"] >= len(TIERS):
        return "✅ 你的破产重整已经全部完成，重新做人吧。"
    if not st["relief"]:
        return ("你目前不欠债（欠款 %.2f）。破产期间的纾困进度 %.0f 元，"
                "已达成 %d/%d 档。" % (st["loan"], st["earn"], st["tier"], st["tier_max"]))
    lines = ["🆘 破产纾困进度"]
    if not st["bankrupt"]:
        lines.append("✅ 你已经回血，交易资格恢复；纾困计划还在继续（还欠 %.2f）。" % st["loan"])
    if st.get("reopen_days"):
        lines.append("⏳ 重开期还剩 %d 天：期间不能贷款、杠杆最多 1 倍。" % st["reopen_days"])
    lines.append("累计打工等收入 %.0f 元，当前欠款 %.2f 元" % (st["earn"], st["loan"]))
    for i, t in enumerate(st["tiers"], 1):
        mark = "✅" if t["done"] else ("🔸" if i == st["tier"] + 1 else "⬜")
        lines.append("%s 第%d档【%s】%s（累计 %.0f）" % (mark, i, t["name"], t["desc"], t["need"]))
    if st["next"]:
        lines.append("再挣 %.0f 元就能解锁【%s】" % (st["next"]["left"], st["next"]["name"]))
    lines.append("—— 破产期间打工上限翻倍、签到翻倍，贷款不计息")
    return "\n".join(lines)

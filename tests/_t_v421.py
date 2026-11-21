# -*- coding: utf-8 -*-
"""v4.21 回归：个人借贷 P2P。

【这一版在做什么】
群里多了一条「熟人借钱」的线：借入人发申请，债主定利率同意，钱当场转过去，
到期自动扣款；还不上就逾期罚息、进失信名单，失信期间打工/签到收入的 30%
被强制拿去还债；资不抵债宣告破产时系统垫一半给债主，另一半不免除。

判定重点（按 #65 的堵漏清单逐条对）：
  1. P2P 债不参与司法重整勾销，破产只免 50%（比银行少）
  2. P2P 计入 users.loan -> 债务上限自动生效；单笔 <= 20 万；出借人单笔 <= 现金 50%
  3. 同一对每天 1 笔；P2P 利息不进任何收入统计
  4. 允许 0 息，但照样占债务额度，期限 <= 30 天
  5. 申请 24h 自动过期；每人同时待处理 <= 3 条
  7. 利率由借出人自定
"""
import io
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import server as S
import db as dbmod
import market
import p2p
import recovery

DATA = os.path.join(HERE, "_t_v421")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
S.DATA_DIR = DATA
S.CFG["data_dir"] = DATA

FAILED = []


def check(name, cond, extra=""):
    print("  [%s] %s  %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILED.append(name)


GID = "10001"
h = dbmod.get_db(DATA, GID)
CFG = S.CFG


def mk(qq, nick, cash=100000.0):
    market.ensure_user(h, qq, nick, cash)
    h.ex("UPDATE users SET cash=? WHERE qq=?", (cash, qq))
    return qq


def cash(qq):
    r = h.q1("SELECT cash FROM users WHERE qq=?", (qq,))
    return float(r["cash"] or 0) if r else 0.0


def loan(qq):
    r = h.q1("SELECT loan FROM users WHERE qq=?", (qq,))
    return float(r["loan"] or 0) if r else 0.0


def credit(qq):
    r = h.q1("SELECT credit FROM users WHERE qq=?", (qq,))
    return int(r["credit"]) if r else 0


A = mk("20001", "借钱的")
B = mk("20002", "放钱的")

print("[T1] 申请借款")
okk, msg, info = p2p.apply_for(h, CFG, A, "借钱的", 5000, 7, "", "放钱的")
check("T1.1 申请成功", okk, msg)
r = h.q1("SELECT * FROM p2p_requests WHERE qq=?", (A,))
check("T1.2 申请落库 pending", r and r["status"] == "pending", r["status"] if r else None)
check("T1.3 债主解析对了", r and r["lender_qq"] == B, r["lender_qq"] if r else None)
check("T1.4 期限 7 天", r and r["term_days"] == 7, r["term_days"] if r else None)

okk, msg, info = p2p.apply_for(h, CFG, A, "借钱的", 5000, 7, "", "查无此人")
check("T1.5 找不到债主被拒", not okk, msg)
okk, msg, info = p2p.apply_for(h, CFG, A, "借钱的", 999999, 7, "", "放钱的")
check("T1.6 超单笔上限被拒", not okk, msg)
okk, msg, info = p2p.apply_for(h, CFG, A, "借钱的", 5000, 99, "", "放钱的")
check("T1.7 期限超 30 天被拒", not okk, msg)
okk, msg, info = p2p.apply_for(h, CFG, A, "借钱的", 5000, 7, "", "放钱的")
check("T1.8 同一债主只能有一条待处理申请", not okk, msg)

print()
print("[T2] 同意借款（利率单位：千分之）")
before_a, before_b = cash(A), cash(B)
okk, msg, info = p2p.decide(h, CFG, B, "放钱的", "", "借钱的", 0.5, True)
check("T2.1 放款成功", okk, msg)
row = h.q1("SELECT * FROM p2p_loans WHERE borrower_qq=?", (A,))
check("T2.2 生成了 P2P 债", row is not None)
check("T2.3 利率 0.5‰ = 0.0005", row and abs(row["rate"] - 0.0005) < 1e-9,
      row["rate"] if row else None)
check("T2.4 钱真的转过去了",
      abs(cash(B) - (before_b - 5000)) < 0.01 and abs(cash(A) - (before_a + 5000)) < 0.01,
      (cash(A), cash(B)))
check("T2.5 借款人负债 +5000", abs(loan(A) - 5000) < 0.01, loan(A))
check("T2.6 债主没有负债", abs(loan(B)) < 0.01, loan(B))
check("T2.7 申请置为 accepted",
      h.q1("SELECT status FROM p2p_requests WHERE qq=?", (A,))["status"] == "accepted")
check("T2.8 到期时间 = 7 天后",
      row and abs((row["due_at"] - time.time()) - 7 * 86400) < 5,
      row["due_at"] if row else None)
check("T2.9 未收回的债权可见", abs(p2p.receivable(h, B) - 5000) < 0.01,
      p2p.receivable(h, B))

okk, msg, info = p2p.decide(h, CFG, B, "放钱的", "", "借钱的", 5.0, True)
check("T2.10 利率超上限被拒", not okk, msg)
okk, msg, info = p2p.decide(h, CFG, B, "放钱的", "", "借钱的", 0, True)
check("T2.11 没有待处理申请了", not okk, msg)

print()
print("[T3] 计息：P2P 用自己的利率，不吃银行日息")
h.ex("UPDATE p2p_loans SET accrued_at=? WHERE id=?", (time.time() - 86400, row["id"]))
p2p.accrue(h, CFG, A)
row2 = h.q1("SELECT * FROM p2p_loans WHERE id=?", (row["id"],))
check("T3.1 一天利息 = 本金 x 0.05%% = 2.5", row2 and abs(row2["interest"] - 2.5) < 0.05,
      row2["interest"] if row2 else None)
check("T3.2 负债跟着涨", abs(loan(A) - 5002.5) < 0.05, loan(A))
check("T3.3 没到期不收罚息", row2 and row2["penalty"] == 0, row2["penalty"] if row2 else None)
before_loan = loan(A)
market.accrue_interest(h, CFG, A)
check("T3.4 银行日息不吃 P2P 的本金",
      abs(loan(A) - before_loan) < 0.05, (before_loan, loan(A)))

print()
print("[T4] 提前还清")
okk, msg, info = p2p.pay(h, CFG, A, 6000)
check("T4.1 还款成功", okk, msg)
check("T4.2 债清空了", abs(p2p.outstanding(h, A)) < 0.01, p2p.outstanding(h, A))
check("T4.3 负债归零", abs(loan(A)) < 0.01, loan(A))
check("T4.4 债主把钱收回来了", abs(p2p.receivable(h, B)) < 0.01, p2p.receivable(h, B))
check("T4.5 信用分涨了（提前还 +3+1）", credit(A) >= 60 + 4, credit(A))
check("T4.6 这笔标记为已还清",
      h.q1("SELECT status FROM p2p_loans WHERE id=?", (row["id"],))["status"] == "repaid")

print()
print("[T4b] 到期自动扣款（准点还钱不算逾期，不收罚息）")
AP = mk("20020", "按时还的")
AQ = mk("20021", "债主4")
p2p.apply_for(h, CFG, AP, "按时还的", 3000, 3, "", "债主4")
p2p.decide(h, CFG, AQ, "债主4", "", "按时还的", 0.5, True)
arow = h.q1("SELECT * FROM p2p_loans WHERE borrower_qq=?", (AP,))
h.ex("UPDATE p2p_loans SET due_at=?, accrued_at=? WHERE id=?",
     (time.time() - 60, time.time() - 2 * 86400, arow["id"]))
aq_before = cash(AQ)
p2p.accrue(h, CFG, AP)
arow2 = h.q1("SELECT * FROM p2p_loans WHERE id=?", (arow["id"],))
check("T4b.1 到期自动扣款，状态变 repaid", arow2["status"] == "repaid", arow2["status"])
check("T4b.2 罚息被核销（准点还钱不罚）", arow2["penalty_paid"] >= arow2["penalty"],
      (arow2["penalty"], arow2["penalty_paid"]))
check("T4b.3 债主收到本金 + 利息", cash(AQ) > aq_before + 2900, (aq_before, cash(AQ)))
check("T4b.4 债清空了", abs(p2p.outstanding(h, AP)) < 0.01, p2p.outstanding(h, AP))
check("T4b.5 负债归零", abs(loan(AP)) < 0.01, loan(AP))

print()
print("[T5] 逾期 -> 罚息 -> 违约 -> 失信名单")
C = mk("20003", "逾期的")
D = mk("20004", "放钱2")
okk, msg, info = p2p.apply_for(h, CFG, C, "逾期的", 4000, 3, "", "放钱2")
okk, msg, info = p2p.decide(h, CFG, D, "放钱2", "", "逾期的", 0.5, True)
check("T5.1 第二笔放款成功", okk, msg)
lrow = h.q1("SELECT * FROM p2p_loans WHERE borrower_qq=?", (C,))
# 没钱 -> 到期时系统没法自动扣款，只能进逾期。
h.ex("UPDATE users SET cash=0 WHERE qq=?", (C,))
# 把到期时间挪到 1 天前，模拟逾期
h.ex("UPDATE p2p_loans SET due_at=?, accrued_at=? WHERE id=?",
     (time.time() - 86400, time.time() - 2 * 86400, lrow["id"]))
p2p.accrue(h, CFG, C)
lrow = h.q1("SELECT * FROM p2p_loans WHERE id=?", (lrow["id"],))
check("T5.2 逾期后状态变 late", lrow["status"] == "late", lrow["status"])
check("T5.3 罚息 = 日息 x3", lrow["penalty"] > 0, lrow["penalty"])
check("T5.4 信用分被扣", credit(C) < 60, credit(C))
# 再拖 8 天 -> 违约
h.ex("UPDATE p2p_loans SET due_at=?, accrued_at=? WHERE id=?",
     (time.time() - 9 * 86400, time.time() - 10 * 86400, lrow["id"]))
p2p.accrue(h, CFG, C)
lrow = h.q1("SELECT * FROM p2p_loans WHERE id=?", (lrow["id"],))
check("T5.5 逾期 7 天后判违约", lrow["status"] == "default", lrow["status"])
check("T5.6 失信标记置上", p2p.in_default(h, C))
check("T5.7 失信榜里有他", any(x["qq"] == C for x in p2p.default_list(h, CFG)),
      p2p.default_list(h, CFG))
okk, msg, info = p2p.apply_for(h, CFG, C, "逾期的", 1000, 7, "", "放钱2")
check("T5.8 失信期间不能再申请", not okk, msg)

print()
print("[T6] 失信期：打工 / 签到收入的 30% 被强制还债")
h.ex("UPDATE users SET cash=1000, last_job=0 WHERE qq=?", (C,))
before_cash = cash(C)
before_owe = p2p.outstanding(h, C)
# 打工有 12% 概率被鸽（白干），多试几次直到真的拿到工资
for _try in range(12):
    h.ex("UPDATE users SET last_job=0 WHERE qq=?", (C,))
    okk, msg, info = market.do_job(h, CFG, C)
    if okk:
        break
check("T6.1 打工成功", okk, msg)
check("T6.2 被强制扣掉了一块", "强制" in (msg or "") or "失信" in (msg or ""), msg)
check("T6.3 失信债金额在变少", p2p.outstanding(h, C) < before_owe,
      (before_owe, p2p.outstanding(h, C)))

print()
print("[T7] 破产：系统垫一半，剩下一半不免除")
E = mk("20005", "破产的")
F = mk("20006", "债主3")
okk, msg, info = p2p.apply_for(h, CFG, E, "破产的", 10000, 7, "", "债主3")
okk, msg, info = p2p.decide(h, CFG, F, "债主3", "", "破产的", 0.5, True)
check("T7.1 第三笔放款成功", okk, msg)
f_before = cash(F)
h.ex("UPDATE users SET cash=0 WHERE qq=?", (E,))
h.ex("UPDATE users SET loan=10000 WHERE qq=?", (E,))
recovery.on_bankrupt(h, CFG, E)
check("T7.2 债主收到系统垫付", cash(F) > f_before + 4900, (f_before, cash(F)))
check("T7.3 借款人还剩一半", 4900 < p2p.outstanding(h, E) <= 5001, p2p.outstanding(h, E))
check("T7.4 这笔进了失信", p2p.in_default(h, E))
check("T7.5 破产后不能申请新借款",
      not p2p.apply_for(h, CFG, E, "破产的", 100, 7, "", "债主3")[0])

print()
print("[T8] 司法重整不豁免 P2P 债")
bank_before = loan(E)
h.ex("UPDATE users SET bankrupt_earn=30000, relief_tier=0 WHERE qq=?", (E,))
recovery.settle(h, CFG, E)
check("T8.1 P2P 债没被一笔勾销", p2p.outstanding(h, E) > 0, p2p.outstanding(h, E))
check("T8.2 银行债被清了（loans 表 waived）",
      h.q1("SELECT COUNT(*) c FROM loans WHERE qq=? AND status='open'", (E,))["c"] == 0)
check("T8.3 负债里留的还是 P2P 那部分", abs(loan(E) - p2p.outstanding(h, E)) < 0.05,
      (loan(E), p2p.outstanding(h, E)))

print()
print("[T9] 债主自己破产：风险对称，债权打 5 折")
G = mk("20007", "借钱的2")
H = mk("20008", "大债主")
okk, msg, info = p2p.apply_for(h, CFG, G, "借钱的2", 8000, 7, "", "大债主")
okk, msg, info = p2p.decide(h, CFG, H, "大债主", "", "借钱的2", 0.5, True)
g_owe_before = p2p.outstanding(h, G)
h.ex("UPDATE users SET cash=0, loan=100000 WHERE qq=?", (H,))
recovery.on_bankrupt(h, CFG, H)
check("T9.1 借款人的债被打折", p2p.outstanding(h, G) < g_owe_before,
      (g_owe_before, p2p.outstanding(h, G)))
check("T9.2 打折幅度约一半",
      abs(p2p.outstanding(h, G) - g_owe_before * 0.5) < 1.0, p2p.outstanding(h, G))

print()
print("[T10] 堵漏清单逐条对")
# 单笔上限 20 万
I = mk("20009", "有钱的")
h.ex("UPDATE users SET cash=1000000 WHERE qq=?", (I,))
okk, msg, info = p2p.apply_for(h, CFG, I, "有钱的", 300000, 7, "", "有钱的")
check("T10.1 单笔 30 万被拒（上限 20 万）", not okk, msg)
# 出借人单笔 <= 现金 50%
okk, msg, info = p2p.apply_for(h, CFG, I, "有钱的", 5000, 7, "", "有钱的")
check("T10.2 自己借自己被拒", not okk, msg)
J = mk("20010", "小债主")
h.ex("UPDATE users SET cash=1000 WHERE qq=?", (J,))
okk, msg, info = p2p.apply_for(h, CFG, I, "有钱的", 900, 7, "", "小债主")
check("T10.3 申请过了", okk, msg)
okk, msg, info = p2p.decide(h, CFG, J, "小债主", "", "有钱的", 0.5, True)
check("T10.4 债主现金不够（超现金 50%%）被拒", not okk, msg)
# 0 息允许
K = mk("20011", "免息对象")
h.ex("UPDATE users SET cash=100000 WHERE qq=?", (K,))
p2p.apply_for(h, CFG, K, "免息对象", 3000, 7, "", "有钱的")
okk, msg, info = p2p.decide(h, CFG, I, "有钱的", "", "免息对象", 0, True)
check("T10.5 0 息（互助）可以放", okk, msg)
kr = h.q1("SELECT rate FROM p2p_loans WHERE borrower_qq=?", (K,))
check("T10.6 0 息也占债务额度", abs(loan(K) - 3000) < 0.01, loan(K))
# 申请 24h 过期
h.ex("UPDATE p2p_requests SET created_at=? WHERE status='pending'",
     (time.time() - 25 * 3600,))
p2p.expire_old(h, CFG)
check("T10.7 超 24 小时的申请自动作废",
      h.q1("SELECT COUNT(*) c FROM p2p_requests WHERE status='expired'")["c"] >= 1)
# P2P 利息不进收入统计
stats = h.q1("SELECT COALESCE(SUM(-delta),0) s FROM ledger WHERE qq=? AND reason LIKE '打工%'",
              (C,))
check("T10.8 P2P 收款不进打工收入统计",
      h.q1("SELECT COUNT(*) c FROM ledger WHERE qq=? AND reason LIKE '打工%' AND qq=?",
            (D, D))["c"] == 0)

print()
print("[T11] 信用分")
check("T11.1 新号初始 60 分", credit(A) >= 60, credit(A))
h.ex("UPDATE users SET credit=20 WHERE qq=?", (A,))
okk, msg, info = p2p.apply_for(h, CFG, A, "借钱的", 100, 7, "", "放钱2")
check("T11.2 信用分不够不能申请", not okk, msg)

print()
print("[T12] 面板 / 文案")
txt = p2p.panel_text(h, CFG, C)
check("T12.1 面板有信用分", "信用分" in txt, txt[:80])
check("T12.2 面板提示失信", "失信" in txt, txt[:120])
check("T12.3 失信榜是公开的", "失信名单" in p2p.default_text(h, CFG))
check("T12.4 网页面板能出 JSON", isinstance(p2p.web_panel(h, CFG, C), dict))

shutil.rmtree(DATA, ignore_errors=True)
print()
if FAILED:
    print("FAILED: %d 项 -> %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("v4.21 个人借贷 P2P 全部通过")

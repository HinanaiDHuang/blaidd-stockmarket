# -*- coding: utf-8 -*-
"""v4.9 回归：打工日上限按档位拉开 / 中下层翻身任务 / 挥霍每日额度 / 打土豪目标脱敏。

【这一版在修什么】
1. 打工日上限之前是死的 160 元（谁都一样），而档位特权只作用在单次工资倍数上，
   结果「打工」成了中下层的最优解 —— 无风险、稳定、次数多，炒股反而成了副业。
   现在日上限按档位拉开：普通股民 150 … 财阀 1000。
2. 光拉开上限还不够，得给中下层一条「认真炒股」的捷径：
   净值 20 万以下激活「翻身任务」，必须真的下单买 / 平仓 / 签到才拿得到奖励。
3. 挥霍消费原来没有任何每日上限 → 声望可以无限刷，刷满抗抢保护后
   打土豪对这个人永久失效。现在每天最多 2 次。
4. 打土豪目标列表原来公开别人精确现金余额（可批量摸底），现在不给了。
"""
import io
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import db as dbmod
import daily
import market
import tiers
import tycoon

WORK = os.path.join(HERE, "_t_v49")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
cfg = json.load(open(os.path.join(SRC_DIR, "config.json"), encoding="utf-8"))
db = dbmod.get_db(WORK, "c")
NOW = int(time.time())
FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, extra))
        FAILED.append(name)


def mk(qq, nick, cash, loan=0.0):
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at) VALUES(?,?,?,?,?)",
          (qq, nick, cash, loan, NOW))
    return db.q1("SELECT * FROM users WHERE qq=?", (qq,))


def cash_of(qq):
    return float(db.q1("SELECT cash FROM users WHERE qq=?", (qq,))["cash"])


def equity_of(qq):
    a = market.account(db, cfg, qq)
    return float(a["equity"]) if a else 0.0


def job_earn(qq):
    row = db.q1("SELECT COALESCE(SUM(delta),0) s FROM ledger WHERE qq=? AND reason LIKE '打工%'",
                (qq,))
    return float(row["s"] or 0)


# ============================================================ T1 打工日上限按档位
print("[T1] 打工日上限按档位拉开")
caps = cfg.get("job_cap_by_tier") or []
check("T1.1 配置里有 6 档上限", len(caps) == 6, str(caps))
check("T1.2 普通股民 150", market.job_cap_by_tier(cfg, 0) == 150,
      str(market.job_cap_by_tier(cfg, 0)))
check("T1.3 小富 220", market.job_cap_by_tier(cfg, 200000) == 220)
check("T1.4 大富 320", market.job_cap_by_tier(cfg, 500000) == 320)
check("T1.5 豪门 480", market.job_cap_by_tier(cfg, 1500000) == 480)
check("T1.6 巨富 700", market.job_cap_by_tier(cfg, 5000000) == 700)
check("T1.7 财阀 1000", market.job_cap_by_tier(cfg, 15000000) == 1000)
check("T1.8 档位越高上限越高（单调递增）",
      all(caps[i] < caps[i + 1] for i in range(len(caps) - 1)), str(caps))
check("T1.9 缺配置时回落到 job_daily_cap",
      market.job_cap_by_tier({}, 0) == float(cfg.get("job_daily_cap", 160.0)))

# 实际打工：普通股民一天最多挣 150
mk("7001", "打工仔", 100000.0)
for i in range(6):
    mk("71%02d" % i, "老板%d" % i, 200000.0)
for _ in range(40):
    db.ex("UPDATE users SET last_job=0 WHERE qq=?", ("7001",))
    market.do_job(db, cfg, "7001")
check("T1.10 普通档位打工当日封顶 150", abs(job_earn("7001") - 150) < 0.01,
      "实得 %.2f" % job_earn("7001"))
ok, msg, _ = market.do_job(db, cfg, "7001")
db.ex("UPDATE users SET last_job=0 WHERE qq=?", ("7001",))
ok2, msg2, _ = market.do_job(db, cfg, "7001")
check("T1.11 封顶后提示打工只是应急", (not ok2) and ("上限" in (msg2 or "")), str(msg2)[:60])

# 高档位上限确实更高
mk("7002", "大款", 6000000.0)
for _ in range(60):
    db.ex("UPDATE users SET last_job=0 WHERE qq=?", ("7002",))
    market.do_job(db, cfg, "7002")
check("T1.12 高档位打工上限更高（> 普通档位）", job_earn("7002") > job_earn("7001"),
      "高档 %.2f vs 普通 %.2f" % (job_earn("7002"), job_earn("7001")))

# ============================================================ T2 中下层翻身任务
print("[T2] 中下层翻身任务")
cap = float(cfg.get("rise_equity_cap", 200000))
check("T2.1 阈值 20 万", cap == 200000, str(cap))
check("T2.2 普通档位激活", daily.rise_active(cfg, 100000) is True)
check("T2.3 普通股民档上限内激活（19.9 万）", daily.rise_active(cfg, 199000) is True)
check("T2.4 升到小富之家就不再激活（20 万起）", daily.rise_active(cfg, 200000) is False)
check("T2.4b 净值 30 万不激活", daily.rise_active(cfg, 300000) is False)

mk("7003", "小透明", 100000.0)
st = daily.rise_state(db, cfg, "7003")
check("T2.5 中下层拿到 3 条翻身任务", len(st) == 3, str(len(st)))
codes = [s["code"] for s in st]
check("T2.6 包含「第一笔买入」", "rise_trade" in codes, str(codes))
trade_task = [s for s in st if s["code"] == "rise_trade"][0]
check("T2.7 奖励按净值给（10 万 → 1200）", trade_task["reward"] == 1200.0,
      str(trade_task["reward"]))
mk("7004", "光杆", 3000.0)
st2 = daily.rise_state(db, cfg, "7004")
r2 = [s for s in st2 if s["code"] == "rise_trade"][0]["reward"]
check("T2.8 低净值有下限保护（不低于 200）", r2 == 200.0, str(r2))
mk("7005", "土豪", 900000.0)
check("T2.9 高净值没有翻身任务", daily.rise_state(db, cfg, "7005") == [])

# 结算：标记完成后发钱，且只能领一次
before = cash_of("7003")
db.ex("INSERT OR IGNORE INTO quests(qq,day,code,done,rewarded,ts) VALUES(?,?,?,1,0,?)",
      ("7003", daily.features.today(), "rise_trade", NOW))
msgs = daily.rise_settle(db, cfg, "7003")
gain = cash_of("7003") - before
check("T2.10 完成任务发钱", gain == 1200.0, "到账 %.2f" % gain)
check("T2.11 有提示文案", any("翻身任务" in m for m in msgs), str(msgs)[:80])
msgs2 = daily.rise_settle(db, cfg, "7003")
check("T2.12 同一天不能重复领", cash_of("7003") - before == 1200.0 and msgs2 == [],
      str(msgs2)[:60])
row = db.q1("SELECT rewarded FROM quests WHERE qq=? AND day=? AND code=?",
            ("7003", daily.features.today(), "rise_trade"))
check("T2.13 任务标记为已发奖", int(row["rewarded"]) == 1)

# ============================================================ T3 挥霍每日额度
print("[T3] 挥霍消费每日额度")
mk("7006", "败家子", 2000000.0)
lim = int(cfg.get("luxury_daily_limit", 2))
check("T3.1 配置每日 2 次", lim == 2, str(lim))
ok1, m1, _ = tycoon.luxury_buy(db, cfg, "7006", "car")
ok2, m2, _ = tycoon.luxury_buy(db, cfg, "7006", "house")
ok3, m3, _ = tycoon.luxury_buy(db, cfg, "7006", "yacht")
check("T3.2 前两次能买", ok1 and ok2, str((m1, m2))[:80])
check("T3.3 第三次被拒", (not ok3), str(m3)[:80])
check("T3.4 提示语说明不能无限刷", "明天再来" in (m3 or ""), str(m3)[:60])
check("T3.5 计数正确", tycoon.luxury_count_today(db, "7006") == 2,
      str(tycoon.luxury_count_today(db, "7006")))
rep = float(db.q1("SELECT reputation FROM users WHERE qq=?", ("7006",))["reputation"] or 0)
check("T3.6 声望只加了 2 单的量（远低于刷满的 20）", rep < 20, "rep=%.1f" % rep)

# ============================================================ T4 打土豪目标脱敏
print("[T4] 打土豪目标列表脱敏")
mk("7007", "土豪2", 500000.0)
mk("7008", "打劫的", 50000.0)
tgt = tycoon.rob_targets(db, cfg, "7008", 8)
check("T4.1 能列出可打目标", len(tgt) >= 1, str(len(tgt)))
check("T4.2 目标里不再有 cash 字段", all("cash" not in t for t in tgt),
      str([k for t in tgt for k in t.keys()]))
check("T4.3 仍有净值/档位/成功率可用",
      all(("equity" in t and "tier" in t and "rate" in t) for t in tgt))

# ============================================================ T5 回归：老玩法没坏
print("[T5] 回归")
mk("7009", "普通用户", 100000.0)
acct = market.account(db, cfg, "7009")
check("T5.1 账户可开", acct is not None)
check("T5.2 日常任务仍正常", len(daily.quest_state(db, cfg, "7009")) == 3,
      str(len(daily.quest_state(db, cfg, "7009"))))
txt = daily.quests_text(db, cfg, "7009")
check("T5.3 任务文案带翻身任务区块", "翻身任务" in txt, txt[-80:])
check("T5.4 高净值用户的任务文案没有翻身任务",
      "翻身任务" not in daily.quests_text(db, cfg, "7005"))

# ============================================================ T6 打工面板状态
print("[T6] 打工面板状态（网页端动态显示）")
mk("7010", "打工仔", 50000.0)
st = market.job_status(db, cfg, "7010")
check("T6.1 有状态返回", isinstance(st, dict) and st.get("cap") is not None, str(st)[:80])
check("T6.2 档位上限跟 job_cap_by_tier 一致",
      abs(float(st["cap"]) - float(market.job_cap_by_tier(cfg, equity_of("7010")))) < 0.01,
      "%s vs %s" % (st.get("cap"), market.job_cap_by_tier(cfg, equity_of("7010"))))
check("T6.3 剩余 = 上限 - 已挣", abs(float(st["left"]) - max(0.0, float(st["cap"]) - float(st["earned"]))) < 0.01,
      str(st)[:120])
check("T6.4 档位名有值", bool(st.get("tier")), str(st.get("tier")))
check("T6.5 职级有值", bool(st.get("level")), str(st.get("level")))
check("T6.6 冷却可读", int(st.get("cooldown") or 0) > 0, str(st.get("cooldown")))
check("T6.7 还没打工就是 ready", st.get("ready") is True and int(st.get("wait") or 0) == 0,
      "ready=%s wait=%s" % (st.get("ready"), st.get("wait")))
mk("7011", "大款", 3000000.0)
st2 = market.job_status(db, cfg, "7011")
check("T6.8 档位越高上限越高", float(st2["cap"]) > float(st["cap"]),
      "%s vs %s" % (st2.get("cap"), st.get("cap")))
check("T6.9 没打工时 earned=0", float(st2["earned"]) == 0.0, str(st2.get("earned")))

print("")
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("v4.9 全部通过")

# -*- coding: utf-8 -*-
"""v4.8 网页端回归：新增的 4 个普通用户接口 + 新手引导 + 档位字段。

【为什么单独测】/api/g/* 走的是 admin 鉴权（回环直连），
而 /api/tycoon/* 走的是**群友自己的令牌**（get_ctx）——这是本次新增的
鉴权路径：以前网页端只有 admin 能调富豪经济，现在群友在网页上也能
打土豪 / 挥霍 / 领分红。这条路径必须有测试守着，别哪天改回 admin 鉴权
还不自知。
"""
import os
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
D = SRC_DIR
sys.path.insert(0, D)
import server as S
import db as dbmod
import engine as E
import tycoon
import features

DATA = os.path.join(HERE, "_t_v48_data")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
S.DATA_DIR = DATA
S.CFG["data_dir"] = DATA
GID = "10001"
handle = dbmod.get_db(DATA, GID)
eng = E.Engine(handle, S.CFG)

NOW = int(time.time())
# 三个层次的群友：豪门 / 普通股民 / 破产户
for qq, nick, cash, loan, bankrupt in (("8101", "土豪", 2000000.0, 0.0, 0),
                                       ("8102", "小民", 50000.0, 0.0, 0),
                                       ("8103", "困难户", 1000.0, 0.0, 0)):
    handle.ex("INSERT OR REPLACE INTO users(qq,nick,cash,loan,created_at,bankrupt) "
              "VALUES(?,?,?,?,?,?)", (qq, nick, cash, loan, NOW, bankrupt))

sess = S.app.test_client()
FAILS = []


def ck(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def tok(qq):
    return S.make_token(GID, qq)


def get(path, qq=None, **q):
    q.setdefault("g", GID)
    if qq:
        q.setdefault("u", qq)
        q.setdefault("t", tok(qq))
    return sess.get(path, query_string=q).get_json()


def post(path, body, qq=None):
    body = dict(body)
    body.setdefault("g", GID)
    if qq:
        body.setdefault("u", qq)
    return sess.post(path, json=body, headers={"X-Token": tok(qq)}).get_json()


print("=== 1. /api/tycoon/* 走群友令牌（不是 admin）===")
for path, key in (("/api/tycoon/rob", "targets"),
                  ("/api/tycoon/luxury", "items"),
                  ("/api/tycoon/dividend", "pool"),
                  ("/api/tycoon/wealth", "top10_share")):
    j = get(path, qq="8102")
    ok = j.get("code") == 200 and key in (j.get("data") or {}).get("st", {})
    ck("%-22s 普通用户令牌可访问" % path, ok, j.get("message"))

print("=== 2. 令牌不对就必须是 403（别裸奔）===")
for path in ("/api/tycoon/rob", "/api/tycoon/luxury",
             "/api/tycoon/dividend", "/api/tycoon/wealth"):
    j = sess.get(path, query_string={"g": GID, "u": "8102", "t": "1.badbadbadbad"}).get_json()
    ck("%-22s 错令牌被拒" % path, j.get("code") != 200, j.get("message"))
    j = sess.get(path, query_string={"g": GID, "u": "8102"}).get_json()
    ck("%-22s 无令牌被拒" % path, j.get("code") != 200, j.get("message"))

print("=== 3. POST 也要走令牌 ===")
j = post("/api/tycoon/luxury", {"item": "豪宅"}, qq="8101")
ck("挥霍 POST 用令牌能过", j.get("code") == 200, j.get("message"))
j = sess.post("/api/tycoon/luxury", json={"g": GID, "u": "8101", "item": "豪宅"},
              headers={"X-Token": "1.badbadbadbad"}).get_json()
ck("挥霍 POST 错令牌被拒", j.get("code") != 200, j.get("message"))

print("=== 4. tier_status 带 tier_min（前端要画升级进度条）===")
st = tycoon.tier_status(handle, S.CFG, "8101")
ck("tier_min 存在", st is not None and "tier_min" in st, st)
ck("tier_min 是本档门槛", st and st["tier_min"] == 1500000, st and st.get("tier_min"))
ck("next_min 是下一档门槛", st and st["next_min"] == 5000000, st and st.get("next_min"))
st2 = tycoon.tier_status(handle, S.CFG, "8102")
ck("小民档位门槛=0", st2 and st2["tier_min"] == 0, st2 and st2.get("tier_min"))
ck("小民下一档=小富之家", st2 and st2["next_tier"] == "小富之家", st2 and st2.get("next_tier"))

print("=== 5. 新手引导 5 步随真实数据走 ===")
ob = S._onboarding(handle, S.CFG, "8102")
ck("共 5 步", ob["total"] == 5, ob["total"])
ck("新号 0 步完成", ob["done"] == 0, ob["done"])
ck("未通关", ob["finished"] is False)
ck("下一步=看懂行情", ob["next"] and ob["next"]["key"] == "view", ob["next"])
ck("每步都带跳转目标", all(s.get("tab") for s in ob["steps"]), ob["steps"])
handle.ex("INSERT OR REPLACE INTO activity(qq,last_seen,last_day,days) VALUES(?,?,?,1)",
          ("8102", NOW, features.today()))
handle.ex("INSERT OR REPLACE INTO checkin(qq,streak,best,total,last_day) VALUES(?,1,1,1,?)",
          ("8102", features.today()))
ob = S._onboarding(handle, S.CFG, "8102")
ck("看过盘+签到 = 2 步", ob["done"] == 2, ob["done"])
ck("下一步变成买第一笔", ob["next"] and ob["next"]["key"] == "trade", ob["next"])
handle.ex("INSERT INTO trades(qq,cid,side,kind,qty,price,leverage,fee,pl,ts) "
          "VALUES('8102',1,'long','open',100,10.0,1.0,1.0,0.0,?)", (NOW,))
handle.ex("INSERT INTO orders(qq,cid,kind,side,trigger,qty,status,created_at) "
          "VALUES('8102',1,'stop_loss','long',9.0,0,'active',?)", (NOW,))
ob = S._onboarding(handle, S.CFG, "8102")
ck("买了+挂止损 = 4 步", ob["done"] == 4, ob["done"])
ck("只剩投票", ob["next"] and ob["next"]["key"] == "vote", ob["next"])

print("=== 6. /api/state 带 tier / onboarding / quests ===")
j = get("/api/state", qq="8102")
d = j.get("data") or {}
ck("state 200", j.get("code") == 200, j.get("message"))
ck("state 带 tier", isinstance(d.get("tier"), dict), d.get("tier"))
ck("state 带 onboarding", isinstance(d.get("onboarding"), dict), d.get("onboarding"))
ck("state 带 quests", isinstance(d.get("quests"), list), d.get("quests"))
ck("tier 里能拿到 tier_min", "tier_min" in (d.get("tier") or {}), d.get("tier"))

import wealth
import tiers as tiersmod

print("\n=== 7. 强制清盘宽限期 = 3 天（主人定的口径）===")
days = int(S.CFG.get("liquidation_auto_days", 0) or 0)
ck("宽限期是 3 天", days == 3, days)
NOW2 = int(time.time())
# 刚到期的（bankrupt_since = 2 天）与刚过期的（3 天）
for qq, days_ago in (("8201", 2), ("8202", 3)):
    handle.ex("INSERT OR REPLACE INTO users(qq,nick,cash,loan,created_at,bankrupt,bankrupt_since) "
              "VALUES(?,?,?,?,?,1,?)",
              (qq, "清盘测试", 1000000.0, 2000000.0, NOW2, NOW2 - days_ago * 86400))
done = wealth.auto_liquidate(handle, S.CFG)
_got = sorted(x[0] for x in done)
ck("刚满 3 天的被清盘", "8202" in _got, _got)
ck("只满 2 天的不动", "8201" not in _got, _got)
u8201 = handle.q1("SELECT * FROM users WHERE qq='8201'")
ck("未到期的仍然破产且欠款没动",
   int(u8201["bankrupt"]) == 1 and abs(float(u8201["loan"]) - 2000000.0) < 1.0,
   (u8201["bankrupt"], u8201["loan"]))

print("\n=== 8. 会员费费率（巨富 0.2%）===")
ck("巨富 = 0.20%", abs(tiersmod.fee_ratio(5000000.0) - 0.0020) < 1e-9,
   tiersmod.fee_ratio(5000000.0))
ck("财阀 = 0.26%", abs(tiersmod.fee_ratio(15000000.0) - 0.0026) < 1e-9,
   tiersmod.fee_ratio(15000000.0))
ck("财阀比巨富更贵", tiersmod.fee_ratio(15000000.0) > tiersmod.fee_ratio(5000000.0))
ck("普通群民仍然不交钱", tiersmod.fee_ratio(199999.0) == 0.0,
   tiersmod.fee_ratio(199999.0))

print("\n==== v4.8 失败 %d 条 ====" % len(FAILS))
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PASS")
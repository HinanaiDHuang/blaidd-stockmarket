# -*- coding: utf-8 -*-
"""v4.20 回归：网页「打赏群友」入口。

【这一版在做什么】
「打赏」这套逻辑（daily.transfer）本来就有了，但只有群指令那条路
（/api/g/transfer，走 admin key，插件在本机回环调用）。浏览器从公网
根本调不到那个接口 —— 于是网页上「打赏」这个功能等于不存在。

v4.20 补一条**走群友令牌**的路：/api/transfer
    GET  打开面板 -> 我的打赏额度 + 群友名单（选人用）
    POST 执行打赏 -> 跟群指令共用 daily.transfer，额度 / 破产限制 / 同名判定完全一致

判定：
  - GET 必须带令牌，令牌跟群号对不上要拒
  - 打赏成功：钱真的从 A 挪到 B，两边余额和流水都对
  - 单笔上限、日额度上限都要拦得住（不能因为多一个入口就多一条绕过）
  - 自己打赏自己 / 找不到人 / 同名多人 -> 都要拒
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
import server as S
import db as dbmod
import daily
import market

DATA = os.path.join(HERE, "_t_v420")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
S.DATA_DIR = DATA
S.CFG["data_dir"] = DATA
S.CFG["public_host"] = "127.0.0.1:9030"

FAILED = []


def check(name, cond, extra=""):
    print("  [%s] %s  %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILED.append(name)


GID = "10001"
handle = dbmod.get_db(DATA, GID)
market.ensure_user(handle, "20000", "群友0", 100000.0)
market.ensure_user(handle, "20001", "群友1", 100000.0)
market.ensure_user(handle, "20002", "同名甲", 100000.0)
market.ensure_user(handle, "20003", "同名甲", 100000.0)


def cash(qq):
    row = handle.q1("SELECT cash FROM users WHERE qq=?", (qq,))
    return float(row["cash"] or 0) if row else 0.0


sess = S.app.test_client()
r = sess.post("/api/session", json={"g": GID, "u": "20000", "nick": "群友0",
                                    "members": json.dumps([{"qq": "20000", "nick": "群友0"}])})
tok = r.get_json()["data"]["token"]
H = {"X-Token": tok}


def get(path):
    return sess.get(path, headers=H).get_json()


def post(path, body):
    return sess.post(path, headers=H, json=body).get_json()


print("[T1] GET /api/transfer —— 打开面板")
st = get("/api/transfer?g=%s&u=20000" % GID)
check("T1.1 返回 200", st["code"] == 200, st.get("message"))
d = st.get("data") or {}
check("T1.2 有 quota / members", "quota" in d and "members" in d, sorted(d.keys()))
q = d.get("quota") or {}
check("T1.3 quota 有单笔上限和当日剩余",
      q.get("cap") == 5000 and q.get("day_left") == 5000, q)
check("T1.4 quota 里写明可用现金", q.get("cash") == 100000.0, q.get("cash"))
ms = d.get("members") or []
check("T1.5 名单里有 4 个人", len(ms) == 4, len(ms))
check("T1.6 名单带昵称/QQ/净值",
      all(("qq" in m and "nick" in m and "equity" in m) for m in ms), ms[:2])
check("T1.7 名单按净值从高到低",
      all(ms[i]["equity"] >= ms[i + 1]["equity"] for i in range(len(ms) - 1)),
      [m["equity"] for m in ms])

print()
print("[T2] 令牌不对要拒")
rr = sess.get("/api/transfer?g=%s&u=20000" % GID)
check("T2.1 没有令牌被拒", rr.get_json()["code"] != 200, rr.get_json().get("message"))
rr = sess.get("/api/transfer?g=99999&u=20000", headers=H)
check("T2.2 令牌跟群号对不上被拒", rr.get_json()["code"] != 200,
      rr.get_json().get("message"))

print()
print("[T3] POST /api/transfer —— 真的打赏")
before0, before1 = cash("20000"), cash("20001")
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "群友1", "amount": 300})
check("T3.1 打赏成功", p["code"] == 200, p.get("message"))
check("T3.2 回执写明谁给谁多少",
      "群友0" in (p.get("message") or "") and "群友1" in (p.get("message") or "")
      and "300" in (p.get("message") or ""), p.get("message"))
check("T3.3 钱真的挪走了",
      abs(cash("20000") - (before0 - 300)) < 0.01
      and abs(cash("20001") - (before1 + 300)) < 0.01,
      (cash("20000"), cash("20001")))
check("T3.4 回执里带最新账户", isinstance(p.get("data", {}).get("me"), dict),
      type(p.get("data", {}).get("me")))
row = handle.q1("SELECT reason FROM ledger WHERE qq=? ORDER BY id DESC LIMIT 1", ("20001",))
check("T3.5 流水记了「收到打赏」", row and "打赏" in row["reason"],
      row["reason"] if row else None)

print()
print("[T4] 额度要拦得住")
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "群友1", "amount": 99999})
check("T4.1 超单笔上限被拒", p["code"] != 200, p.get("message"))
check("T4.2 提示里写清上限", "上限" in (p.get("message") or "") or "最多" in (p.get("message") or ""),
      p.get("message"))
# T3 已经打赏了 300，日额度 5000 还剩 4700 —— 一次性打满剩下的额度。
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "群友1", "amount": 4700})
check("T4.3 正好打满当日额度可以", p["code"] == 200, p.get("message"))
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "群友1", "amount": 100})
check("T4.4 当日额度用完后被拒", p["code"] != 200, p.get("message"))
q2 = (get("/api/transfer?g=%s&u=20000" % GID).get("data") or {}).get("quota") or {}
check("T4.5 面板上当日剩余变成 0", q2.get("day_left") == 0, q2)

print()
print("[T5] 各种该拒的要拒")
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "", "amount": 100})
check("T5.1 没写给谁被拒", p["code"] != 200, p.get("message"))
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "群友0", "amount": 100})
check("T5.2 自己打赏自己被拒", p["code"] != 200, p.get("message"))
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "查无此人", "amount": 100})
check("T5.3 找不到人被拒", p["code"] != 200, p.get("message"))
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "同名甲", "amount": 100})
check("T5.4 同名多人被拒", p["code"] != 200, p.get("message"))
check("T5.5 同名提示让报 QQ 号", "QQ" in (p.get("message") or ""), p.get("message"))
p = post("/api/transfer", {"g": GID, "u": "20000", "target": "群友1", "amount": "abc"})
check("T5.6 金额不合法被拒", p["code"] != 200, p.get("message"))

print()
print("[T6] 群指令那条路还在（/api/g/transfer，admin key）")
rr = sess.post("/api/g/transfer", json={"g": GID, "u": "20001", "nick": "群友1",
                                        "target": "群友0", "amount": 50})
check("T6.1 本机回环免 admin key 也能用", rr.get_json()["code"] == 200,
      rr.get_json().get("message"))
check("T6.2 群里那版打的钱跟网页版走同一本账",
      abs(cash("20000") - (95000.0 + 50)) < 0.01, cash("20000"))

print()
print("[T7] daily 层直接调用也一样（两个入口共用一套逻辑）")
okk, msg, info = daily.transfer(handle, S.CFG, "20002", "群友1", 50)
check("T7.1 daily.transfer 认 QQ 号", okk, msg)
check("T7.2 info 里带回对方 QQ", (info or {}).get("to") == "20001", info)

shutil.rmtree(DATA, ignore_errors=True)
print()
if FAILED:
    print("FAILED: %d 项 -> %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("v4.20 网页打赏 全部通过")
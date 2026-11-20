# -*- coding: utf-8 -*-
"""v4.13 网页端财阀基金面板：/api/fund 的 GET/POST 回归。

网页端走令牌（X-Token），群里那版走 /api/g/fund + admin key —— 两条路都要能跑。
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
import market

DATA = os.path.join(HERE, "_t_v413web")
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

sess = S.app.test_client()
r = sess.post("/api/session", json={"g": GID, "u": "20000", "nick": "群友0",
                                    "members": json.dumps([{"qq": "20000", "nick": "群友0"}])})
j = r.get_json()
tok = j["data"]["token"]
H = {"X-Token": tok}


def hit(method, path, **kw):
    if method == "GET":
        return sess.get(path, headers=H).get_json()
    return sess.post(path, headers=H, json=kw.get("json")).get_json()


print("[T1] GET /api/fund")
st = hit("GET", "/api/fund?g=%s&u=20000" % GID)
check("T1.1 返回 200", st["code"] == 200, st.get("message"))
d = st["data"]
check("T1.2 有 st / bet / text / board / bets",
      all(k in d for k in ("st", "bet", "text", "board", "bets")), sorted(d.keys()))
check("T1.3 st 里有净值和门槛",
      "equity" in d["st"] and "min_equity" in d["st"], d["st"].get("equity"))
check("T1.4 bet 里有押注上限",
      d["bet"]["max"] == 1000 and d["bet"]["left"] == 1, d["bet"])
check("T1.5 说明文字里有「财阀基金」", "财阀基金" in d["text"], d["text"][:40])

print()
print("[T2] 无令牌 / 错群号要拒绝")
rr = sess.get("/api/fund?g=%s&u=20000" % GID)
check("T2.1 没有令牌被拒", rr.get_json()["code"] != 200, rr.get_json().get("message"))
rr = sess.get("/api/fund?g=99999&u=20000", headers=H)
check("T2.2 令牌跟群号对不上被拒", rr.get_json()["code"] != 200,
      rr.get_json().get("message"))

print()
print("[T3] POST 押注")
p = hit("POST", "/api/fund", json={"g": GID, "u": "20000", "side": "涨"})
check("T3.1 押涨成功", p["code"] == 200, p.get("message"))
check("T3.2 回执里有金额", "1000" in (p.get("message") or ""), p.get("message"))
cash = handle.q1("SELECT cash FROM users WHERE qq='20000'")["cash"]
check("T3.3 钱被扣了 1000", abs(float(cash) - 99000.0) < 0.01, cash)
p2 = hit("POST", "/api/fund", json={"g": GID, "u": "20000", "side": "跌"})
check("T3.4 一天只能押一次", p2["code"] != 200, p2.get("message"))
st2 = hit("GET", "/api/fund?g=%s&u=20000" % GID)
check("T3.5 状态里显示已押方向", st2["data"]["bet"]["side"] == "涨",
      st2["data"]["bet"])
check("T3.6 押注榜里有我", any(b["qq"] == "20000" for b in st2["data"]["bets"]),
      st2["data"]["bets"])

print()
print("[T4] 方向写错要有提示")
# 20001 单独开一个会话（20000 今天已经押过了，测不出方向校验）
r2 = sess.post("/api/session", json={"g": GID, "u": "20001", "nick": "群友1",
                                     "members": json.dumps([{"qq": "20001", "nick": "群友1"}])})
H2 = {"X-Token": r2.get_json()["data"]["token"]}
p3 = sess.post("/api/fund", headers=H2,
               json={"g": GID, "u": "20001", "side": "横盘"}).get_json()
check("T4.1 方向错被拒", p3["code"] != 200, p3.get("message"))
check("T4.2 提示里有用法", "押基金" in (p3.get("message") or ""), p3.get("message"))

print()
print("[T5] 群里那版 /api/g/fund 还在（admin key 那条路）")
rr = sess.get("/api/g/fund?g=%s&u=20000" % GID)
check("T5.1 本机回环免 admin key 也能用", rr.get_json()["code"] == 200,
      rr.get_json().get("message"))
check("T5.2 群里那版也带 bet 状态", "st" in (rr.get_json().get("data") or {}),
      sorted((rr.get_json().get("data") or {}).keys()))

shutil.rmtree(DATA, ignore_errors=True)
print()
if FAILED:
    print("FAILED: %d 项 -> %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("v4.13 网页端 全部通过")

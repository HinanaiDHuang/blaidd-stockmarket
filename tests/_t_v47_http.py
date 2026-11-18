# -*- coding: utf-8 -*-
"""v4.7 HTTP 回归：五个新端点真的能被 Lua 调到。"""
import json
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
import os
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

DATA = os.path.join(HERE, "_t_v47_http")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
S.DATA_DIR = DATA
S.CFG["data_dir"] = DATA
GID = "10001"
handle = dbmod.get_db(DATA, GID)
eng = E.Engine(handle, S.CFG)

NOW = int(time.time())
for qq, nick, cash, loan, bankrupt in (("7101", "土豪", 2000000.0, 0.0, 0),
                                       ("7102", "猎人", 50000.0, 0.0, 0),
                                       ("7103", "困难户", 1000.0, 0.0, 0)):
    handle.ex("INSERT OR REPLACE INTO users(qq,nick,cash,loan,created_at,bankrupt) "
              "VALUES(?,?,?,?,?,?)", (qq, nick, cash, loan, NOW, bankrupt))
handle.ex("INSERT OR REPLACE INTO ledger(qq,ts,delta,balance,reason) VALUES(?,?,?,?,?)",
          ("7103", NOW, 1.0, 1.0, "每日签到"))

sess = S.app.test_client()
FAILS = []


def ck(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def get(path, **q):
    q.setdefault("g", GID)
    return sess.get(path, query_string=q).get_json()


def post(path, body):
    body = dict(body)
    body.setdefault("g", GID)
    return sess.post(path, json=body).get_json()


def cash(qq):
    return float(handle.q1("SELECT cash FROM users WHERE qq=?", (qq,))["cash"])


print("=== 1. /api/g/tier ===")
j = get("/api/g/tier", u="7101")
ck("GET 返回档位文本", j["code"] == 200 and "净值分层" in j["data"]["text"], j)
ck("档位=豪门", j["data"]["st"]["tier"] == "豪门", j["data"]["st"])
ck("杠杆上限 26", j["data"]["st"]["lev_cap"] == 26, j["data"]["st"]["lev_cap"])
j = get("/api/g/tier", u="9999")
ck("没开户会报错", j["code"] != 200, j.get("message"))

print("=== 2. /api/g/rob ===")
j = get("/api/g/rob", u="7102")
ck("GET 返回出手说明", j["code"] == 200 and "打土豪" in j["data"]["text"], j)
ck("目标列表里有土豪", any(t["qq"] == "7101" for t in j["data"]["st"]["targets"]),
   j["data"]["st"]["targets"])
before = cash("7101")
j = post("/api/g/rob", {"u": "7102", "nick": "猎人", "target": "土豪"})
ck("POST 出手成功", j["code"] == 200 and ("得手" in j["data"]["msg"] or "翻车" in j["data"]["msg"]),
   j.get("message"))
ck("豪rama 现金有变化", cash("7101") != before, (before, cash("7101")))
j = post("/api/g/rob", {"u": "7102", "nick": "猎人", "target": "不存在的人"})
ck("找不到人会报错", j["code"] != 200, j.get("message"))
j = post("/api/g/rob", {"u": "7102", "nick": "猎人", "target": "7102"})
ck("不能打自己", j["code"] != 200, j.get("message"))

print("=== 3. /api/g/luxury ===")
j = get("/api/g/luxury", u="7101")
ck("GET 返回挥霍清单", j["code"] == 200 and "挥霍清单" in j["data"]["text"], j)
before = cash("7101")
pool_before = tycoon.pool_get(handle)
j = post("/api/g/luxury", {"u": "7101", "nick": "土豪", "item": "豪宅"})
ck("POST 买成功", j["code"] == 200 and "到手" in j["data"]["msg"], j.get("message"))
ck("钱扣了", cash("7101") < before, (before, cash("7101")))
ck("池子涨了", tycoon.pool_get(handle) > pool_before,
   (pool_before, tycoon.pool_get(handle)))
j = post("/api/g/luxury", {"u": "7101", "nick": "土豪", "item": "不存在"})
ck("买不存在的会报错", j["code"] != 200, j.get("message"))

print("=== 4. /api/g/dividend ===")
j = get("/api/g/dividend", u="7103")
ck("GET 返回池子信息", j["code"] == 200 and "分红池" in j["data"]["text"], j)
before = cash("7103")
j = post("/api/g/dividend", {"u": "7103", "nick": "困难户"})
ck("POST 领到分红", j["code"] == 200 and "分红到账" in j["data"]["msg"], j.get("message"))
ck("钱到账了", cash("7103") > before, (before, cash("7103")))
j = post("/api/g/dividend", {"u": "7103", "nick": "困难户"})
ck("一天只能领一次", j["code"] != 200, j.get("message"))

print("=== 5. /api/g/wealth ===")
j = get("/api/g/wealth", u="7101")
ck("GET 返回财富分布", j["code"] == 200 and "财富分布" in j["data"]["text"], j)
ck("有头部占比数据", "top10_share" in j["data"]["st"], j["data"]["st"])

print("\n==== HTTP 失败 %d 条 ====" % len(FAILS))
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PASS")

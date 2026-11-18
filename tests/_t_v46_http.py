# -*- coding: utf-8 -*-
"""v4.6 HTTP 回归：三个新端点真的能被 Lua 调到。"""
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
import market
import engine as E

DATA = os.path.join(HERE, "_t_v46_http")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
S.DATA_DIR = DATA
S.CFG["data_dir"] = DATA
GID = "10001"
handle = dbmod.get_db(DATA, GID)
eng = E.Engine(handle, S.CFG)

# 一个有钱的、一个破产的、一个困难的
for qq, nick, cash, loan, bankrupt in (("7001", "富豪", 3360000.0, 0.0, 0),
                                       ("7002", "破产户", 40737000.0, 54000000.0, 1),
                                       ("7003", "困难户", 300.0, 0.0, 0)):
    handle.ex("INSERT OR REPLACE INTO users(qq,nick,cash,loan,created_at,bankrupt) "
              "VALUES(?,?,?,?,?,?)", (qq, nick, cash, loan, int(time.time()), bankrupt))


sess = S.app.test_client()
FAILS = []


def ck(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def get(path, **q):
    q.setdefault("g", GID)
    r = sess.get(path, query_string=q)
    return r.get_json()


def post(path, body):
    body = dict(body)
    body.setdefault("g", GID)
    r = sess.post(path, json=body)
    return r.get_json()


print("=== 1. /api/g/liquidation ===")
j = get("/api/g/liquidation", u="7002")
ck("GET 返回条件说明", j["code"] == 200 and "清盘" in j["data"]["text"], j)
ck("符合清盘条件", j["data"]["st"]["eligible"] is True, j["data"]["st"])
j = post("/api/g/liquidation", {"u": "7002", "nick": "破产户"})
ck("POST 执行清盘", j["code"] == 200 and "核销" in j["data"]["msg"], j.get("message"))
row = handle.q1("SELECT cash, loan, bankrupt FROM users WHERE qq='7002'")
ck("清盘后欠款清零", abs(float(row["loan"])) < 1e-6, row["loan"])
ck("清盘后破产标记解除", int(row["bankrupt"]) == 0, row["bankrupt"])
j = post("/api/g/liquidation", {"u": "7002", "nick": "破产户"})
ck("不能清第二次", j["code"] != 200, j.get("message"))

print("=== 2. /api/g/scatter ===")
j = get("/api/g/scatter", u="7001")
ck("GET 返回额度", j["code"] == 200 and "撒钱" in j["data"]["text"], j)
ck("额度随净值放大", abs(j["data"]["limit"]["cap"] - 33600.0) < 1.0, j["data"]["limit"])
before = {r["qq"]: float(r["cash"]) for r in handle.q("SELECT qq, cash FROM users")}
j = post("/api/g/scatter", {"u": "7001", "nick": "富豪", "amount": 2000})
ck("POST 撒钱成功", j["code"] == 200 and "红包" in j["data"]["msg"], j.get("message"))
after = {r["qq"]: float(r["cash"]) for r in handle.q("SELECT qq, cash FROM users")}
ck("穷人的钱变多了", after["7003"] > before["7003"], (before["7003"], after["7003"]))
j = post("/api/g/scatter", {"u": "7001", "nick": "富豪", "amount": 999999})
ck("超额被拒", j["code"] != 200, j.get("message"))

print("=== 3. /api/g/comeback ===")
j = get("/api/g/comeback", u="7003")
ck("GET 返回资格", j["code"] == 200 and "逆袭" in j["data"]["text"], j)
ck("困难户符合资格", j["data"]["st"]["ok"] is True, j["data"]["st"])
before = after["7003"]
j = post("/api/g/comeback", {"u": "7003", "nick": "困难户"})
ck("POST 领到补助", j["code"] == 200 and "到账" in j["data"]["msg"], j.get("message"))
after2 = float(handle.q1("SELECT cash FROM users WHERE qq='7003'")["cash"])
ck("现金增加", after2 > before, (before, after2))
j = post("/api/g/comeback", {"u": "7003", "nick": "困难户"})
ck("每天只能领一次", j["code"] != 200, j.get("message"))
j = get("/api/g/comeback", u="7001")
ck("富人不符合资格", j["data"]["st"]["ok"] is False, j["data"]["st"]["why"])

print("=== 4. 散财榜 ===")
j = get("/api/g/board", u="7001", kind="charity", n=10)
ck("榜里有富豪", j["code"] == 200 and j["data"]["items"]
    and j["data"]["items"][0]["qq"] == "7001", j["data"]["items"][:1])

print("")
if FAILS:
    print("FAILED: %d -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL OK")
shutil.rmtree(DATA, ignore_errors=True)

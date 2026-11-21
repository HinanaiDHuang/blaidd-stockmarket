# -*- coding: utf-8 -*-
"""v4.21 HTTP 回归：个人借贷 P2P 的 5 个群端点 + 网页面板。"""
import json
import os
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.path.insert(0, SRC_DIR)
import server as S
import db as dbmod
import market

DATA = os.path.join(HERE, "_t_v421_http")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
S.DATA_DIR = DATA
S.CFG["data_dir"] = DATA
GID = "10001"
handle = dbmod.get_db(DATA, GID)

FAILS = []


def ck(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def get(path, **q):
    q.setdefault("g", GID)
    return S.app.test_client().get(path, query_string=q).get_json()


def post(path, body):
    body = dict(body)
    body.setdefault("g", GID)
    return S.app.test_client().post(path, json=body).get_json()


# 三个用户：债主 / 借款人 / 旁观者
for qq, nick, cash in (("7001", "小红", 500000.0), ("7002", "小明", 80000.0),
                       ("7003", "围观群众", 30000.0)):
    market.ensure_user(handle, qq, nick, cash)
handle.ex("UPDATE users SET cash=500000 WHERE qq='7001'")
handle.ex("UPDATE users SET cash=80000 WHERE qq='7002'")

print("=== 1. 申请借款：Lua 用昵称找债主（target_qq 传空串）===")
j = post("/api/g/p2p_apply", {"u": "7002", "nick": "小明", "amount": 5000,
                             "term": 7, "target_qq": "", "target_name": "小红"})
ck("申请成功", j["code"] == 200, j.get("message"))
ck("回执提到债主", "小红" in j["data"]["msg"], j["data"]["msg"])
row = handle.q1("SELECT * FROM p2p_requests WHERE qq='7002'")
ck("落库一条 pending", row and row["status"] == "pending", row)
ck("金额 5000", row and abs(float(row["amount"]) - 5000) < 1e-6, row)

print("=== 2. 同一债主不能再收第二条申请（防重复）===")
j = post("/api/g/p2p_apply", {"u": "7002", "nick": "小明", "amount": 100,
                             "term": 7, "target_qq": "", "target_name": "小红"})
ck("第二次被拒", j["code"] != 200, j.get("message"))

print("=== 3. 同意借款（债主用昵称指认，rate 单位千分之）===")
j = post("/api/g/p2p_decide", {"u": "7001", "nick": "小红", "borrower_qq": "",
                               "borrower_name": "小明", "rate": 0.5, "accept": True})
ck("放款成功", j["code"] == 200, j.get("message"))
ck("回执带利率", "万分之5" in j["data"]["msg"], j["data"]["msg"])
b = handle.q1("SELECT cash, loan FROM users WHERE qq='7002'")
l = handle.q1("SELECT cash, loan FROM users WHERE qq='7001'")
ck("借款人到手 5000", abs(float(b["cash"]) - 85000) < 1e-6, b["cash"])
ck("借款人负债 +5000", abs(float(b["loan"]) - 5000) < 1e-6, b["loan"])
ck("债主扣款 5000", abs(float(l["cash"]) - 495000) < 1e-6, l["cash"])
row = handle.q1("SELECT * FROM p2p_loans WHERE borrower_qq='7002'")
ck("落库一笔 open 债", row and row["status"] == "open", row)
ck("利率 0.0005", row and abs(float(row["rate"]) - 0.0005) < 1e-9, row)

print("=== 4. 我的借贷 ===")
j = get("/api/g/p2p_info", u="7002")
ck("返回面板", j["code"] == 200 and "我的借贷" in j["data"]["text"], j.get("message"))
ck("面板列出债主", "小红" in j["data"]["text"], j["data"]["text"])
j = get("/api/g/p2p_info", u="7001")
ck("债主看得到出借", "我借出的" in j["data"]["text"], j["data"]["text"])

print("=== 5. 还P2P ===")
handle.ex("UPDATE users SET cash=90000 WHERE qq='7002'")
j = post("/api/g/p2p_repay", {"u": "7002", "nick": "小明", "amount": 1000})
ck("还款成功", j["code"] == 200, j.get("message"))
b = handle.q1("SELECT cash, loan FROM users WHERE qq='7002'")
ck("现金 -1000", abs(float(b["cash"]) - 89000) < 1e-6, b["cash"])
ck("负债 -1000", abs(float(b["loan"]) - 4000) < 1e-6, b["loan"])
j = post("/api/g/p2p_repay", {"u": "7002", "nick": "小明", "amount": 999999})
ck("没现金还不了", j["code"] != 200, j.get("message"))

print("=== 6. 失信榜 ===")
j = get("/api/g/p2p_defaults", u="7003")
ck("失信榜可读", j["code"] == 200 and "失信" in j["data"]["text"], j.get("message"))

print("=== 7. 网页面板 /api/p2p ===")
S.CFG["token_salt"] = S.CFG.get("token_salt", "test")
from server import make_token
tok = make_token(GID, "7002")
j = get("/api/p2p", u="7002", t=tok)
ck("网页面板可读", j["code"] == 200, j.get("message"))
d = j.get("data") or {}
ck("面板有借贷字段", isinstance(d, dict) and ("borrows" in d or "loans" in d), list(d.keys())[:8])

print("=== 8. 借出人自己破产 → 债打折（风险对称）===")
handle.ex("UPDATE users SET bankrupt=1, cash=0 WHERE qq='7001'")
j = post("/api/g/p2p_repay", {"u": "7002", "nick": "小明", "amount": 4000})
ck("还清成功", j["code"] == 200, j.get("message"))
b = handle.q1("SELECT loan FROM users WHERE qq='7002'")
ck("负债清零", abs(float(b["loan"])) < 1e-6, b["loan"])

print("=== 9. 网页端 POST（令牌鉴权）===")
from server import make_token
tok2 = make_token(GID, "7004")
tok3 = make_token(GID, "7005")
market.ensure_user(handle, "7004", "申请者", 60000.0)
market.ensure_user(handle, "7005", "债主", 400000.0)
handle.ex("UPDATE users SET cash=400000 WHERE qq='7005'")


def wpost(path, body, tok):
    body = dict(body)
    body.setdefault("g", GID)
    return S.app.test_client().post(path, json=body,
                                    headers={"X-Token": tok}).get_json()


j = wpost("/api/p2p/apply", {"u": "7004", "nick": "申请者", "amount": 3000,
                             "term": 5, "target_name": "债主"}, tok2)
ck("网页申请成功", j["code"] == 200, j.get("message"))
j = wpost("/api/p2p/decide", {"u": "7005", "nick": "债主", "borrower_qq": "7004",
                              "rate": 0.4, "accept": True}, tok3)
ck("网页放款成功", j["code"] == 200, j.get("message"))
b = handle.q1("SELECT cash, loan FROM users WHERE qq='7004'")
ck("借款人到手 3000", abs(float(b["cash"]) - 63000) < 1e-6, b["cash"])
j = wpost("/api/p2p/repay", {"u": "7004", "nick": "申请者", "amount": 1000}, tok2)
ck("网页还款成功", j["code"] == 200, j.get("message"))
j = wpost("/api/p2p/apply", {"u": "7004", "nick": "申请者", "amount": 3000,
                             "term": 5, "target_name": "债主"}, "badtoken")
ck("无效令牌被拒", j["code"] != 200, j.get("message"))

print("")
if FAILS:
    print("FAIL: %d 项" % len(FAILS))
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("v4.21 HTTP 全部通过")
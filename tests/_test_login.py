# -*- coding: utf-8 -*-
"""v4.2 口令登录链路测试：群里领一次性口令 -> 网页登录 -> 设固定口令。"""
import sys, io, os, json, time, shutil
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
D = SRC_DIR
sys.path.insert(0, D)
import server as S
import db as dbmod, market, engine as E

DATA = os.path.join(HERE, "_tlogindata")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
S.DATA_DIR = DATA
S.CFG["data_dir"] = DATA
S.CFG["public_host"] = "127.0.0.1:9030"

GID = "10001"
QQ = "20001"
handle = dbmod.get_db(DATA, GID)
eng = E.Engine(handle, S.CFG)
eng._create_company(QQ, "群友1")
market.ensure_user(handle, QQ, "群友1", 100000.0)

sess = S.app.test_client()
fails = []


def ck(name, cond, extra=""):
    print(("  OK  " if cond else "FAIL  ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


print("=== 1. 群里发「炒股」：登记 + 铸一次性口令 ===")
r = sess.post("/api/session", json={"g": GID, "u": QQ, "nick": "群友1",
                                    "members": [{"qq": QQ, "nick": "群友1"}]})
j = r.get_json()
ck("session 登记成功", j["code"] == 200, j.get("message"))

r = sess.post("/api/g/passcode", json={"g": GID, "u": QQ})
j = r.get_json()
ck("铸口令成功", j["code"] == 200 and j["data"].get("passcode"), j.get("message"))
code = j["data"]["passcode"]
ck("口令是 6 位数字", len(code) == 6 and code.isdigit(), code)
ck("返回站点地址", j["data"].get("site", "").startswith("http://"), j["data"].get("site"))

print("=== 2. 网页登录 ===")
r = sess.post("/api/login", json={"u": QQ, "pwd": "000000"})
j = r.get_json()
ck("错误口令被拒", j["code"] != 200, j.get("message"))

r = sess.post("/api/login", json={"u": QQ, "pwd": code})
j = r.get_json()
ck("正确口令登录成功", j["code"] == 200, j.get("message"))
tok = j["data"]["token"]
ck("拿到 30 天令牌", j["data"]["url"].find("#t=") > 0 and len(tok) > 20, tok[:24])
ck("返回昵称", j["data"].get("nick") == "群友1", j["data"].get("nick"))

print("=== 3. 令牌能用 ===")
r = sess.get("/api/state?g=%s&u=%s" % (GID, QQ), headers={"X-Token": tok})
j = r.get_json()
ck("新令牌能取到账户", j["code"] == 200 and j["data"]["me"]["qq"] == QQ, j.get("message"))

print("=== 4. 一次性口令用掉即焚 ===")
r = sess.post("/api/login", json={"u": QQ, "pwd": code})
j = r.get_json()
ck("同一个口令不能用第二次", j["code"] != 200, j.get("message"))

print("=== 5. 设固定口令 ===")
r = sess.post("/api/passcode", json={"g": GID, "u": QQ, "pwd": "myown888"},
              headers={"X-Token": tok})
j = r.get_json()
ck("设固定口令成功", j["code"] == 200, j.get("message"))

r = sess.post("/api/login", json={"u": QQ, "pwd": "myown888"})
j = r.get_json()
ck("固定口令能登录", j["code"] == 200, j.get("message"))

print("=== 6. 固定口令不会被群里的临时口令覆盖 ===")
r = sess.post("/api/g/passcode", json={"g": GID, "u": QQ})
j = r.get_json()
ck("识别为已有固定口令", j["code"] == 200 and j["data"].get("permanent") is True, j["data"])

r = sess.post("/api/login", json={"u": QQ, "pwd": "myown888"})
ck("固定口令仍然有效", r.get_json()["code"] == 200)

print("=== 7. 强制重置（忘了口令时）===")
r = sess.post("/api/g/passcode", json={"g": GID, "u": QQ, "force": True})
j = r.get_json()
ck("强制重置给出新口令", j["code"] == 200 and j["data"].get("passcode"), j.get("message"))
code2 = j["data"]["passcode"]

r = sess.post("/api/login", json={"u": QQ, "pwd": "myown888"})
ck("重置后旧口令失效", r.get_json()["code"] != 200, r.get_json().get("message"))

r = sess.post("/api/login", json={"u": QQ, "pwd": code2})
ck("新口令能登录", r.get_json()["code"] == 200, r.get_json().get("message"))

print("=== 8. 口令过期 ===")
r = sess.post("/api/g/passcode", json={"g": GID, "u": QQ, "force": True})
code2 = r.get_json()["data"]["passcode"]
reg = dbmod.get_registry(DATA)
reg.ex("UPDATE user_pass SET exp=? WHERE uid=?", (int(time.time()) - 5, QQ))
r = sess.post("/api/login", json={"u": QQ, "pwd": code2})
j = r.get_json()
ck("过期口令被拒", j["code"] != 200 and "过期" in (j.get("message") or ""), j.get("message"))

print("=== 9. 没登记过的 QQ ===")
r = sess.post("/api/login", json={"u": "99999999", "pwd": "123456"})
j = r.get_json()
ck("没登记的用户被拒", j["code"] != 200, j.get("message"))

print("=== 10. 失败锁定 ===")
for i in range(6):
    sess.post("/api/login", json={"u": QQ, "pwd": "badpwd"})
r = sess.post("/api/login", json={"u": QQ, "pwd": code2})
j = r.get_json()
ck("连错 5 次后被锁", j["code"] != 200 and ("太多" in (j.get("message") or "") or "频繁" in (j.get("message") or "")), j.get("message"))

print()
if fails:
    print("FAILED: %d" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")

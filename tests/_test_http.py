# -*- coding: utf-8 -*-
import sys, io, os, json, time, shutil
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
D = SRC_DIR
sys.path.insert(0, D)
import server as S
import db as dbmod, market, engine as E, features

DATA = os.path.join(HERE, "_t3data")
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
S.DATA_DIR = DATA
S.CFG["data_dir"] = DATA
S.CFG["public_host"] = "127.0.0.1:9030"

GID = "10001"
handle = dbmod.get_db(DATA, GID)
eng = E.Engine(handle, S.CFG)
for i in range(8):
    qq = str(20000 + i)
    eng._create_company(qq, "群友%d" % i)
    market.ensure_user(handle, qq, "群友%d" % i, 100000.0)

# 拿一个合法 token
sess = S.app.test_client()
r = sess.post("/api/session", json={"g": GID, "u": "20000", "nick": "群友0",
                                    "members": json.dumps([{"qq": "20000", "nick": "群友0"}])})
j = r.get_json()
print("session:", j["code"], j.get("message"))
tok = j["data"]["token"]
H = {"X-Token": tok}

def hit(method, path, **kw):
    if method == "GET":
        rr = sess.get(path, headers=H, **kw)
    else:
        rr = sess.post(path, headers=H, json=kw.get("json"))
    return rr.get_json()

# ---- 1) /api/state 里带市场状态
st = hit("GET", "/api/state?g=%s&u=20000" % GID)
print("state market:", st["data"].get("market"))
assert st["data"]["market"]["fear"] == 50.0

# ---- 2) 触发一次大盘黑天鹅
rows = handle.q("SELECT id, price, prev_close, beta, drift, vol, industry, anchor FROM companies")
bom = E.pick_bomb(rows, scope="market")
bom = [b for b in E.bombs_of("market") if b["circuit"]][0]
pend = {"bomb": bom, "tpl": bom["tpl"], "scope": "market", "cat": bom["cat"],
        "impact": -0.12, "peeked": 0}
eng._fire_bomb(pend, rows)
st = hit("GET", "/api/state?g=%s&u=20000" % GID)
mk = st["data"]["market"]
print("熔断后 market:", mk)
assert mk["circuit"] is True and mk["fear"] > 50

# ---- 3) 熔断期间下单被拒
o = hit("POST", "/api/order", json={"g": GID, "u": "20000", "cid": 1, "side": "long", "qty": 100, "leverage": 1})
print("熔断下单:", o["code"], o["message"])
assert o["code"] != 200 and "休市" in o["message"]

# ---- 4) 快讯接口带题材 / 大事记 / 市场
nw = hit("GET", "/api/g/news?g=%s&u=20000&n=5" % GID)
print("news market:", nw["data"]["market"]["circuit"], "| major:", len(nw["data"]["major"]))
print("第一条快讯:", nw["data"]["items"][0]["title"], "| cat:", nw["data"]["items"][0]["cat"], "| bomb:", nw["data"]["items"][0]["bomb"])
assert nw["data"]["major"], "大事记应该有内容"

# ---- 5) 搜股
sr = hit("GET", "/api/g/search?g=%s&u=20000&q=%s&n=5" % (GID, "群友"))
print("搜股:", [(i["name"], i["ticker"], i["industry"]) for i in sr["data"]["items"]][:3])
assert sr["data"]["items"]

# ---- 6) 改名
rn = hit("POST", "/api/company/rename", json={"g": GID, "u": "20000", "cid": 1, "name": "隔壁老王食品"})
print("改名:", rn["code"], rn.get("message"))
rn2 = hit("POST", "/api/company/rename", json={"g": GID, "u": "20000", "cid": 1, "name": "隔壁老王食品"})
print("再改:", rn2["code"], rn2.get("message"))
assert rn["code"] == 200 and rn2["code"] != 200

# ---- 7) 商业战（网页入口）
sb = hit("POST", "/api/company/sabotage", json={"g": GID, "u": "20000", "cid": 2, "plan": "放黑料"})
print("商业战:", sb["code"], sb.get("message"))
sb2 = hit("POST", "/api/company/sabotage", json={"g": GID, "u": "20000", "cid": 2, "plan": "做空狙击"})
print("商业战2:", sb2["code"], sb2.get("message"))
sb3 = hit("POST", "/api/company/sabotage", json={"g": GID, "u": "20000", "cid": 2, "plan": "做空狙击"})
print("商业战3(超上限):", sb3["code"], sb3.get("message"))
assert sb["code"] == 200 and sb3["code"] != 200

# ---- 8) 群里的商业战 GET（列手段）
pg = hit("GET", "/api/g/sabotage?g=%s&u=20000" % GID)
print("手段:", [(p["name"], p["cost"], p["rate"]) for p in pg["data"]["plans"]], "| 已用", pg["data"]["used"])

# ---- 9) 今日复盘带大事记
dl = hit("GET", "/api/daily?g=%s&u=20000" % GID)
print("daily keys:", list(dl["data"].keys())[:12])

print("\nALL HTTP OK")

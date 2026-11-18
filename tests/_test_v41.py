# -*- coding: utf-8 -*-
import sys, io, os, json, time, random, shutil
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, SRC_DIR)
import db as dbmod, engine as E, market, daily
from newsdata import BOMB_TAG

D = os.path.join(HERE, "_t2data")
shutil.rmtree(D, ignore_errors=True)
os.makedirs(D, exist_ok=True)
cfg = json.load(open(os.path.join(SRC_DIR, "config.json"), encoding="utf-8"))
h = dbmod.get_db(D, "g_test")
eng = E.Engine(h, cfg)
for i in range(12):
    qq = str(10000 + i)
    eng._create_company(qq, "群友%d" % i)
    market.ensure_user(h, qq, "群友%d" % i, 100000.0)
rows = h.q("SELECT id, price, prev_close, beta, drift, vol, industry, anchor FROM companies")

def fire(bom):
    pend = {"bomb": bom, "tpl": bom["tpl"], "scope": bom["scope"], "cat": bom["cat"],
            "impact": round(random.uniform(bom["lo"], bom["hi"]), 6), "peeked": 0}
    eng._fire_bomb(pend, rows)
    return h.q1("SELECT title, cat, scope FROM news ORDER BY id DESC LIMIT 1")

# 1) 全部 22 条大盘级炸弹逐条检查
print("=== 大盘级 22 条 ===")
crash_bombs = E.bombs_of("market")
n_circuit = 0
for b in crash_bombs:
    h.ex("DELETE FROM news")
    eng.db.set_meta("circuit_until", "0")
    r = fire(b)
    assert r["title"].startswith(BOMB_TAG), "缺火前缀: " + r["title"]
    assert "【" in r["title"], "缺题材: " + r["title"]
    until, reason = eng.circuit_state()
    if b["circuit"]:
        assert until > 0, "该熔断没熔断: " + b["label"]
        n_circuit += 1
    print("  %-14s %-6s 熔断%-3s | %s" % (b["label"], b["cat"], b["circuit"], r["title"][:52]))
print("熔断型大盘炸弹:", n_circuit, "/", len(crash_bombs))

# 2) 行业级 / 个股级
print("=== 行业级 / 个股级 ===")
for sc in ("industry", "company"):
    for b in [E.pick_bomb(rows, scope=sc) for _ in range(3)]:
        h.ex("DELETE FROM news")
        eng.db.set_meta("circuit_until", "0")
        r = fire(b)
        assert r["title"].startswith(BOMB_TAG)
        print("  %-8s %-14s %-6s | %s" % (sc, b["label"], b["cat"], r["title"][:60]))

# 3) 熔断 + 交易拦截 + 恐慌指数
h.ex("DELETE FROM news")
eng.db.set_meta("circuit_until", "0")
b = [x for x in E.bombs_of("market") if x["circuit"]][0]
fire(b)
until, reason = eng.circuit_state()
print("\n熔断:", bool(until), reason, "剩余", max(0, (until - int(time.time())) // 60 + 1), "分钟")
okk, msg, _ = market.place_order(h, cfg, "10000", 1, "long", 100, 1)
print("开仓被拦:", okk, "|", msg)
assert not okk and "休市" in msg
mood, label, fear, fl = eng.market_mood()
print("恐慌指数: %.1f (%s) regime=%s" % (fear, fl, label))
assert fear > 50

# 4) 普通快讯题材
h.ex("DELETE FROM news")
tpl, lo, hi, scope, cat = random.choice(E.NEWS_TEMPLATES)
eng._fire(h.q1("SELECT id, price, industry FROM companies WHERE id=1"), tpl, 0.01, scope, cat=cat)
r = h.q1("SELECT title, cat FROM news ORDER BY id DESC LIMIT 1")
print("普通快讯:", r["title"], "| cat:", r["cat"])

# 5) 大事记 / 播报
h.ex("DELETE FROM news")
fire(b)
print("major_text:", daily.major_text(h, cfg, 5).replace("\n", " | "))
print("brief:", daily.brief_text(h, cfg).replace("\n", " | ")[:420])
print("close:", daily.close_text(h, cfg).replace("\n", " | ")[:520])
print("\nALL OK")

# -*- coding: utf-8 -*-
"""v4.12.2 回归：网页端的小毛病一批。

【这一版在修什么】
1. 网页上挂止损 / 止盈时，前端下拉框永远发 side=long，导致 v4.10 做的
   「按持仓自动推断方向」在网页端等于没做 —— 只持空头的人挂不上止损。
   现在下拉多了「自动」项（value=""），后端收到空串就走自动推断。
2. 个股快讯的正文是标题原文的复制粘贴，同一句话在列表里显示两遍。
3. K 线下方 OHLC 栏长期显示「开 -- 高 -- 低 -- 收 --」。
4. 新手引导步骤号跳号（用原始下标而不是序号）。
5. 320px 小屏顶栏净值被 CSS 截成「106,68...」；「破产纥困」错别字。
"""
import io
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import db as dbmod
import engine as E
import market
import orders

WORK = os.path.join(HERE, "_t_v4122")
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


def mk_user(qq, nick, cash):
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at) VALUES(?,?,?,?,?)",
          (qq, nick, cash, 0.0, NOW))


def mk_company(name, ticker, price=100.0):
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
          "beta,drift,vol,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          ("9000", "庄家", "测试", name, ticker, price, price, price, price, price,
           1.0, 0.0, 0.03, NOW))
    return int(db.q1("SELECT id FROM companies WHERE ticker=?", (ticker,))["id"])


# ============================================================ T1 条件单「自动」方向
print("[T1] 网页端把方向留空（自动）时，后端按持仓推断")
cid = mk_company("自动方向股", "AUT", 100.0)
mk_user("8201", "空头哥", 1000000.0)
okk, msg, _ = market.place_order(db, cfg, "8201", cid, "short", 1000, 1)
check("T1.1 开空仓成功", okk, msg)
okk, msg, info = orders.place(db, cfg, "8201", cid, "stop_loss", "", 110.0)
check("T1.2 空头 + 自动方向止损挂得上", okk, msg)
check("T1.3 方向推断成空头", bool(okk and info and info.get("side") == "short"),
      info and info.get("side"))
okk, msg, info = orders.place(db, cfg, "8201", cid, "take_profit", "", 90.0)
check("T1.4 空头 + 自动方向止盈挂得上", okk, msg)
check("T1.5 方向推断成空头", bool(okk and info and info.get("side") == "short"),
      info and info.get("side"))

cid2 = mk_company("自动方向多头股", "AUT2", 100.0)
mk_user("8202", "多头哥", 1000000.0)
okk, msg, _ = market.place_order(db, cfg, "8202", cid2, "long", 1000, 1)
check("T1.6 开多仓成功", okk, msg)
okk, msg, info = orders.place(db, cfg, "8202", cid2, "stop_loss", "", 90.0)
check("T1.7 多头 + 自动方向止损挂得上", okk, msg)
check("T1.8 方向推断成多头", bool(okk and info and info.get("side") == "long"),
      info and info.get("side"))

# 限价单是开仓单，必须说清楚买还是卖
okk, msg, _ = orders.place(db, cfg, "8201", cid, "limit", "", 105.0, 100)
check("T1.9 限价单 + 自动方向被拒", (not okk) and ("限价" in msg), msg)
# 显式给错方向仍然是老行为（不会被自动推断覆盖）
okk, msg, _ = orders.place(db, cfg, "8201", cid, "stop_loss", "long", 110.0)
check("T1.10 空头却显式指定多头止损 -> 拒", (not okk) and ("多单" in msg), msg)

# ============================================================ T2 个股快讯正文不再重复标题
print("[T2] 个股快讯正文为空（标题里已经有一份了）")
eng = E.Engine(db, cfg)
row = db.q1("SELECT * FROM companies WHERE id=?", (cid,))
before = int(db.q1("SELECT COUNT(*) c FROM news")["c"])
eng._fire(row, "{name} 季报利润腰斩，{ind}板块跟着一起绿", -0.05, "company", cat="财报")
news = db.q("SELECT * FROM news ORDER BY id DESC LIMIT 1")
n = news[0]
check("T2.1 确实插了一条快讯", int(db.q1("SELECT COUNT(*) c FROM news")["c"]) == before + 1)
check("T2.2 个股快讯正文为空", (n["body"] or "") == "", repr(n["body"]))
check("T2.3 标题仍有内容", bool(n["title"]), repr(n["title"]))
check("T2.4 标题带题材前缀", "【财报】" in n["title"], repr(n["title"]))

print("[T2b] 行业 / 大盘快讯正文保留「影响了哪几家公司」")
hits = db.q("SELECT id,name FROM companies WHERE industry=? LIMIT 3", ("测试",))
before = int(db.q1("SELECT COUNT(*) c FROM news")["c"])
eng._fire_bulk("industry", "{name} 上游突然断供", -0.03, hits, "测试", cat="产业链")
n2 = db.q("SELECT * FROM news ORDER BY id DESC LIMIT 1")[0]
check("T2b.1 行业快讯正文有内容", bool(n2["body"]), repr(n2["body"]))
check("T2b.2 正文说明影响了几家公司", "影响" in (n2["body"] or ""), repr(n2["body"]))

# ============================================================ T3 前端静态自查
print("[T3] 前端文件自查")
ix = open(os.path.join(SRC_DIR, "static", "index.html"), encoding="utf-8").read()
js = open(os.path.join(SRC_DIR, "static", "app.js"), encoding="utf-8").read()
cs = open(os.path.join(SRC_DIR, "static", "style.css"), encoding="utf-8").read()

check("T3.1 条件单方向有「自动」选项", '<option value="">自动</option>' in ix)
check("T3.2 条件单默认选中自动（第一个 option）",
      re.search(r'<select id="condSide">\s*<option value="">自动</option>', ix) is not None)
check("T3.3 没有「纥困」错别字", ("纥困" not in ix) and ("纥困" not in js))
# 版本号会随版本推进，这里只校验「两处一致且不是旧版」，
# 不硬编码某个具体值（否则下一次改版本号就要改这里）。
_sv = re.findall(r"\?v=([0-9a-zA-Z]+)", ix)
check("T3.4 静态资源版本号一致且已更新",
      len(_sv) == 2 and _sv[0] == _sv[1] and _sv[0] > "20261119a", repr(_sv))
check("T3.5 顶栏大额去掉小数（1e4 分支）",
      'if (a >= 1e4) return Math.round(n).toLocaleString("zh-CN");' in js)
check("T3.6 OHLC 栏默认取最后一根 K 线", "ohlcBar" in js and "bars[bars.length - 1]" in js)
_cross = js[js.index("function updateCross("): js.index("function updateCross(") + 1600]
check("T3.7 updateCross 不再单独写 OHLC", '$("ohlc")' not in _cross)
check("T3.8 新手引导序号连续", "${k + 1}" in js and "n: i + 1" not in js)
check("T3.9 限价单 + 自动方向前端拦一道", 'kind === "limit" && !side' in js)
_bn = re.search(r"\.brand \.name\{[^}]*\}", cs)
check("T3.10 品牌名单行不折行",
      bool(_bn) and "white-space:nowrap" in _bn.group(0),
      _bn.group(0) if _bn else "(no .brand .name rule)")
check("T3.11 最近改动按钮热区放大", "min-height:30px" in cs)
check("T3.12 窄屏断点存在", "@media (max-width:360px)" in cs)
# HELP_HTML 在 app.js 里是 \\uXXXX 转义，先解码再查
_help = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), js)
check("T3.13 帮助补了短线交易费", "短线交易费" in _help)
check("T3.14 帮助补了破产冷静期", "冷静期" in _help)
check("T3.15 帮助补了方向自动", "选「自动」时按你的持仓判断" in _help)
check("T3.16 帮助补了贷款看净值", "贷款看的是" in _help)

print("")
if FAILED:
    print("FAILED: %d -> %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("ALL OK")

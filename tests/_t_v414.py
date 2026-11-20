# -*- coding: utf-8 -*-
"""v4.13.2 回归：挥霍消费的价格梯度。

起因：群友反馈「挥霍里好像价格都一样」。
根因：luxury_price() 只按净值算一个价，6 件排面品共用同一个价格，
      声望却差 3.5 倍 —— 排面根本没有梯度。
这一版给每件东西加了档次系数 mult，并把每一档的价格摆到台面上。
"""
import os
import io
import sys
import json
import time
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import db as dbmod
import tycoon

WORK = os.path.join(HERE, "_t_v414")
if os.path.isdir(WORK):
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
    db.ex("INSERT INTO users(qq,nick,cash,loan,created_at,bankrupt,bankrupt_ts,"
          "bankrupt_since) VALUES(?,?,?,?,?,?,?,?)",
          (qq, nick, cash, 0.0, NOW, 0, 0, 0))


def cash_of(qq):
    return float(db.q1("SELECT cash FROM users WHERE qq=?", (qq,))["cash"])


def rep_of(qq):
    return float(db.q1("SELECT reputation FROM users WHERE qq=?", (qq,))["reputation"] or 0)


print("[T1] 六件排面品的价格必须各不相同")
EQ = 2000000.0                      # 净值 200 万
items = tycoon.luxury_items(cfg, EQ)
prices = [i["price"] for i in items]
check("T1.1 六件都在", len(items) == 6, str(len(items)))
check("T1.2 价格不是全都一样", len(set(prices)) >= 5, str(prices))
check("T1.3 每件都有档次标签", all(i.get("tier") for i in items),
      str([i.get("tier") for i in items]))
check("T1.4 价格随档次递增", prices == sorted(prices), str(prices))
check("T1.5 最贵是最便宜的 10 倍", abs(prices[-1] / prices[0] - 10.0) < 0.01,
      str(prices))
check("T1.6 每件都有 mult", all("mult" in i for i in tycoon.LUXURY), "")

print("[T2] 基准价还是老规矩（净值 5%，有上下限）")
base = tycoon.luxury_base_price(cfg, 2000000.0)
check("T2.1 净值 200 万 -> 基准价 10 万", abs(base - 100000.0) < 1.0, str(base))
check("T2.2 老接口两参仍返回基准价（向后兼容）",
      abs(tycoon.luxury_price(cfg, 2000000.0) - 100000.0) < 1.0,
      str(tycoon.luxury_price(cfg, 2000000.0)))
check("T2.3 净值太低时按下限 5000 兜底",
      abs(tycoon.luxury_base_price(cfg, 1000.0) - 5000.0) < 0.01,
      str(tycoon.luxury_base_price(cfg, 1000.0)))
check("T2.4 净值太高时按上限 20 万封顶",
      abs(tycoon.luxury_base_price(cfg, 534570143618.72) - 200000.0) < 0.01,
      str(tycoon.luxury_base_price(cfg, 534570143618.72)))

print("[T3] 净值撞了上限，档次梯度不能被抹平")
whale = tycoon.luxury_items(cfg, 534570143618.72)
w_prices = [i["price"] for i in whale]
check("T3.1 巨富的清单也不是一个价", len(set(w_prices)) >= 5, str(w_prices))
check("T3.2 顶配 = 上限 × 5 = 100 万",
      abs(w_prices[-1] - 1000000.0) < 1.0, str(w_prices[-1]))
check("T3.3 入门 = 上限 × 0.5 = 10 万",
      abs(w_prices[0] - 100000.0) < 1.0, str(w_prices[0]))

print("[T4] 群指令的清单要写清楚每档多少钱")
lines = tycoon.luxury_list(cfg, 99000.0)
check("T4.1 清单有 7 行（标题 + 6 件）", len(lines) == 7, str(len(lines)))
check("T4.2 标题里有基准价说明", "基准价" in lines[0], lines[0])
body = "\n".join(lines)
check("T4.3 六档都带价格数字", sum(1 for l in lines[1:] if "元" in l) == 6, body)
check("T4.4 价格各不相同", len(set(l.split("：")[1] for l in lines[1:])) == 6, body)
check("T4.5 不带净值时也能出清单（老接口）",
      len(tycoon.luxury_list(cfg)) == 7, str(len(tycoon.luxury_list(cfg))))

print("[T5] 群指令的「挥霍」本体")
mk_user("9401", "中产", 500000.0)
okk, msg, info = tycoon.luxury_buy(db, cfg, "9401", "")
check("T5.1 空参数出清单", okk and "挥霍清单" in msg, msg[:60])
check("T5.2 清单里写了你的净值和基准价", "基准价" in msg and "顶配" in msg, msg[-120:])
check("T5.3 返回体带 list 标记", info.get("list") is True, str(info))

print("[T6] status 要把每档价格交给网页面板")
st = tycoon.luxury_status(db, cfg, "9401")
check("T6.1 status 有 items", len(st.get("items") or []) == 6, str(st.get("items")))
check("T6.2 每项都带 price", all("price" in i for i in st["items"]), "")
check("T6.3 有 min_price / max_price",
      abs(st["min_price"] - st["items"][0]["price"]) < 0.01
      and abs(st["max_price"] - st["items"][-1]["price"]) < 0.01,
      str((st.get("min_price"), st.get("max_price"))))
check("T6.4 price 字段仍是基准价（老前端不炸）",
      abs(st["price"] - st["base"]) < 0.01, str(st.get("price")))

print("[T7] 买顶配：账要平")
pool_before = tycoon.pool_get(db)
cash_before = cash_of("9401")
okk, msg, info = tycoon.luxury_buy(db, cfg, "9401", "飞机")
price = info.get("price") if info else 0
check("T7.1 买成功", okk, msg[:60])
check("T7.2 价格 = 基准价 × 5",
      abs(price - tycoon.luxury_base_price(cfg, 500000.0) * 5.0) < 1.0, str(price))
check("T7.3 现金确实少了这一笔", abs(cash_before - cash_of("9401") - price) < 0.01,
      str(cash_before - cash_of("9401")))
check("T7.4 80% 进全民分红池",
      abs(tycoon.pool_get(db) - (pool_before + price * 0.8)) < 1.0,
      str(tycoon.pool_get(db) - pool_before))
check("T7.5 20% 销毁", abs(info["burned"] - price * 0.2) < 1.0, str(info.get("burned")))
check("T7.6 声望 +7", abs(rep_of("9401") - 7.0) < 1e-6, str(rep_of("9401")))
check("T7.7 消息里写了档次", "顶配" in msg, msg[:80])
check("T7.8 消息里写了花了多少", ("元" in msg), msg[:120])

print("[T8] 钱不够要说人话")
mk_user("9402", "月光族", 3000.0)
okk, msg, _i = tycoon.luxury_buy(db, cfg, "9402", "飞机")
check("T8.1 买不了", not okk, msg[:60])
check("T8.2 提示里有「钱不够」", "钱不够" in msg, msg[:80])
check("T8.3 提示里带档次", "档" in msg, msg[:80])
check("T8.4 提示里说还差多少", "还差" in msg, msg[:120])

print("[T9] 随机挥霍不该把穷光蛋卡住")
mk_user("9403", "打工仔", 20000.0)
okk, msg, info = tycoon.luxury_buy(db, cfg, "9403", "随机")
check("T9.1 随机能买成（不会挑到买不起的档）", okk, msg[:60])
check("T9.2 买的是买得起的档位（不是拖顶配）",
      info and info.get("price") <= 20000 and info.get("tier") != "顶配",
      str(info.get("price") if info else None))
check("T9.3 每档都能卖得起", info and info.get("price", 0) > 0,
      str(info))

print("[T10] 每日次数上限仍然生效")
mk_user("9404", "败家子", 10000000.0)
o1, m1, _ = tycoon.luxury_buy(db, cfg, "9404", "豪车")
o2, m2, _ = tycoon.luxury_buy(db, cfg, "9404", "豪宅")
o3, m3, _ = tycoon.luxury_buy(db, cfg, "9404", "游艇")
check("T10.1 前两次能买", o1 and o2, str((m1, m2))[:80])
check("T10.2 第三次被拒", (not o3) and "明天再来" in (m3 or ""), str(m3)[:80])
check("T10.3 只买了两单", tycoon.luxury_count_today(db, "9404") == 2,
      str(tycoon.luxury_count_today(db, "9404")))

print("[T11] 网页端要能看到档次和价格")
app = io.open(os.path.join(SRC_DIR, "static", "app.js"), encoding="utf-8").read()
check("T11.1 面板显示每档价格", "it.price" in app, "")
check("T11.2 面板显示档次", "it.tier" in app, "")
check("T11.3 面板显示基准价区间", "st.min_price" in app and "st.max_price" in app, "")
check("T11.4 买不起的按钮置灰", "\\u94b1\\u4e0d\\u591f" in app, "")

print("")
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("v4.13.2 挥霍价格梯度：全部通过")

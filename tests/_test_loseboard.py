# -*- coding: utf-8 -*-
"""v4.3 出局榜 / 亏钱榜测试。"""
import os, sys, time, shutil, json
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
BASE = SRC_DIR
sys.path.insert(0, BASE)
import db as dbmod
import features

work = os.path.join(HERE, "_losetest")
if os.path.isdir(work):
    shutil.rmtree(work)
os.makedirs(work)
cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
db = dbmod.get_db(work, "L")
now = int(time.time())
init = float(cfg["initial_cash"])
# 富豪：净资产 300000
db.ex("INSERT INTO users(qq,nick,cash,created_at) VALUES(?,?,?,?)", ("1", "大牛", 300000, now))
# 平庸：净资产 100000（正好持平，不该出现在亏钱榜）
db.ex("INSERT INTO users(qq,nick,cash,created_at) VALUES(?,?,?,?)", ("2", "中间人", init, now))
# 亏钱：净资产 60000
db.ex("INSERT INTO users(qq,nick,cash,created_at) VALUES(?,?,?,?)", ("3", "亏哥", 60000, now))
# 出局：破产 + 净资产 -5000
db.ex("INSERT INTO users(qq,nick,cash,loan,bankrupt,created_at) VALUES(?,?,?,?,?,?)",
      ("4", "老输家", 0, 5000, 1, now))

items = features.boards(db, cfg, "lose", 10)
assert len(items) == 2, "应只有出局 + 亏钱两人，实际 %d: %s" % (len(items), items)
assert items[0]["tag"] == "out", "出局榜应排前面"
assert items[1]["tag"] == "loss"
assert items[0]["qq"] == "4" and items[1]["qq"] == "3"
assert items[0]["loss"] == 105000.0, items[0]["loss"]
assert items[1]["loss"] == 40000.0, items[1]["loss"]
print("[OK] 榜单分组与亏损额正确")
for it in items:
    print("   %d. %s tag=%s 净值=%.2f 亏=%.0f bankrupt=%s" % (
        it["rank"], it["nick"], it["tag"], it["equity"], it["loss"], it["bankrupt"]))

txt = features.board_text(db, cfg, "lose", 10)
assert "出局榜" in txt and "亏钱榜" in txt
assert "老输家" in txt and "亏哥" in txt
assert "大牛" not in txt and "中间人" not in txt
print("\n----- 群消息版 -----")
print(txt)
print("[OK] 文本版只出现出局 + 亏钱的人")

# 别名
for k in ("lose", "loss", "ku", "shichang", "bankrupt", "chujv"):
    assert len(features.boards(db, cfg, k, 10)) == 2, k
print("[OK] 所有别名可用")

shutil.rmtree(work, ignore_errors=True)
print("\nALL LOSE BOARD OK")

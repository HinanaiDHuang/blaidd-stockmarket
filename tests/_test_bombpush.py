# -*- coding: utf-8 -*-
"""v4.2 黑天鹅播报测试：熔断/恐慌指数拼装 + 推送去重 + at=all 降级。"""
import os, sys, json, shutil, time
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
import engine as engmod

work = os.path.join(HERE, "_bombtest")
if os.path.isdir(work):
    shutil.rmtree(work)
os.makedirs(work)
cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
db = dbmod.get_db(work, "T")
for i in range(12):
    db.ex("INSERT INTO users(qq,nick,cash,created_at) VALUES(?,?,?,?)",
          (str(10000 + i), "u%d" % i, 100000, int(time.time())))
for i in range(30):
    db.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,beta,drift,vol,created_at,anchor)"
          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          ("10000", "u%d" % (i % 12), "电子" if i % 2 else "传媒", "C%d" % i, "00000%d" % i,
           10.0, 10.0, 10.0, 10.0, 10.0, 1.0, 0.0, 0.02, int(time.time()), 10.0))
eng = engmod.Engine(db, cfg)

sent = []


class FakePusher(object):
    def send_group(self, gid, text, at_qq=None):
        sent.append((gid, text, at_qq))
        return True


# --- 1. 大盘级黑天鹅 ---
eng._trip_circuit(15, "地缘冲突")
eng._set_regime(-0.8, 6, "地缘冲突")
db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact,cat) VALUES(?,?,?,?,?,?,?,?)",
      (int(time.time()), 1, "000001", "market",
       "\U0001F525 【国际】\U0001F310 中东战事突然升级", "影响 30 家公司：A、B、C", -0.14, "国际"))
db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact,cat) VALUES(?,?,?,?,?,?,?,?)",
      (int(time.time()), 2, "000002", "industry",
       "\U0001F525 【技术】\u2699\uFE0F 医药行业关键技术国产化突破", "影响 15 家公司", -0.08, "技术"))
db.ex("INSERT INTO news(ts,cid,ticker,scope,title,body,impact,cat) VALUES(?,?,?,?,?,?,?,?)",
      (int(time.time()), 3, "000003", "company",
       "\U0001F525 【监管】\u2696\uFE0F C3 财务造假实锤", "C3 财务造假实锤", -0.45, "监管"))

import server as srv
srv.CFG = cfg
srv.PUSHER = FakePusher()
srv._maybe_push_bombs("T", db)
assert len(sent) == 3, "应推 3 条，实际 %d" % len(sent)
mkt = [s for s in sent if "紧急快讯" in s[1]]
ind = [s for s in sent if "行业异动" in s[1]]
cmp_ = [s for s in sent if "个股爆雷" in s[1]]
assert len(mkt) == 1 and len(ind) == 1 and len(cmp_) == 1
assert mkt[0][2] == "all", "大盘级应 at=all，实际 %r" % (mkt[0][2],)
assert ind[0][2] is None and cmp_[0][2] is None
print("[OK] 三种黑天鹅播报格式")
print("----- 大盘级 -----")
print(mkt[0][1])
print("----- 行业级 -----")
print(ind[0][1])
print("----- 个股级 -----")
print(cmp_[0][1])
assert "临时休市" in mkt[0][1], "应含熔断提示"
assert "恐慌指数" in mkt[0][1]
assert "情绪周期" in mkt[0][1]

# --- 2. 去重：再跑一次不应该重复推 ---
sent.clear()
srv._maybe_push_bombs("T", db)
assert len(sent) == 0, "重复推送，实际 %d" % len(sent)
print("[OK] 同一条黑天鹅只播报一次")

# --- 3. 用户数不足的群不推 ---
sent.clear()
db.ex("DELETE FROM users WHERE qq NOT IN (SELECT qq FROM users LIMIT 3)")
srv._maybe_push_bombs("T", db)
assert len(sent) == 0, "冷群不该推，实际 %d" % len(sent)
print("[OK] 冷群不播报")

# --- 4. at=all 被拒时降级成普通消息 ---
class RejectPusher(object):
    def send_group(self, gid, text, at_qq=None):
        sent.append((gid, text, at_qq))
        return at_qq is None


sent.clear()
srv.PUSHER = RejectPusher()
for i in range(12):
    db.ex("INSERT OR IGNORE INTO users(qq,nick,cash,created_at) VALUES(?,?,?,?)",
          (str(10000 + i), "u%d" % i, 100000, int(time.time())))
db.ex("DELETE FROM push_log")
db.set_meta("bomb_push_cursor", "0")
srv._maybe_push_bombs("T", db)
mkt2 = [s for s in sent if "紧急快讯" in s[1]]
assert len(mkt2) == 2, "应先 at=all 失败再降级，共 2 次发送，实际 %d" % len(mkt2)
assert mkt2[0][2] == "all" and mkt2[1][2] is None
print("[OK] at=all 被拒时自动降级")

shutil.rmtree(work, ignore_errors=True)
print("\nALL BOMB PUSH OK")

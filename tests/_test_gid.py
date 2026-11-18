# -*- coding: utf-8 -*-
"""v4.9.4 回归：私聊认群（登记表区分「本人操作」与「整群登记」）+ 假群 0 不再被当成真群。

【这一版在修什么】
Dice! 私聊时给不出群号（msg.fromGroup 是空串），服务端靠跨群登记表
`user_group` 把用户认回他最近玩过的那个群。原来的表一个 uid 只有一行、
一个 gid，而「整群登记」（群里任意一人发一次「炒股」，全群成员都登记）
是无差别覆盖的 —— 于是同时在 A、B 两个群有账户的人，
会被「别人在另一个群发一次炒股」改判过去，改判权在别人手里。
现在 self_gid / self_ts 只记本人真正操作过的群，认群时优先用它。
另外「0 号群」是老私聊 bug 留下的假群，现在 known_groups 不再认它。
"""
import io
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import db as dbmod
import server

WORK = os.path.join(HERE, "_t_gid")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, extra))
        FAILED.append(name)


def fresh(tag):
    """一个独立的 data 目录（registry.db 按路径缓存，得换个路径）。"""
    d = os.path.join(WORK, tag)
    os.makedirs(d, exist_ok=True)
    server.DATA_DIR = d
    return d


# ============================================================ T9 认群
print("[T9] 私聊认群：整群登记不能把人改判到别的群")

d = fresh("t9a")
server._remember_group("u1", "A")
check("T9.1 本人操作后私聊认得到那个群",
      server._resolve_gid("", "u1") == "A", server._resolve_gid("", "u1"))

server._remember_members("B", [("u1", "甲"), ("u2", "乙")])
check("T9.2 别人在 B 群发一次炒股，不改判 u1",
      server._resolve_gid("", "u1") == "A", server._resolve_gid("", "u1"))
check("T9.3 但 u1 在 B 群确实被登记了（gid 字段跟着变）",
      dbmod.get_registry(d).q1(
          "SELECT gid FROM user_group WHERE uid=?", ("u1",))["gid"] == "B")

check("T9.4 只被整群登记、没自己操作过的人，仍能认到群（回退）",
      server._resolve_gid("", "u2") == "B", server._resolve_gid("", "u2"))

check("T9.5 完全没登记过的人 → None（服务端会提示先在群里发一次）",
      server._resolve_gid("", "nobody") is None)

server._remember_group("u3", "C")
check("T9.6 本人操作后 self_gid 被记下来",
      dbmod.get_registry(d).q1(
          "SELECT self_gid FROM user_group WHERE uid=?", ("u3",))["self_gid"] == "C")

check("T9.7 群号明确时直接用群号，不查登记表",
      server._resolve_gid("888", "nobody") == "888")

# ============================================================ T10 假群 0
print("")
print("[T10] 「0 号群」不再被当成真群")
d2 = fresh("t10")
for gid in ("0", "", "123", "456"):
    with open(os.path.join(d2, "g_%s.db" % gid), "wb") as f:
        f.write(b"")
groups = dbmod.known_groups(d2)
check("T10.1 known_groups 不含 '0'", "0" not in groups, groups)
check("T10.2 known_groups 不含空串", "" not in groups, groups)
check("T10.3 真群照常列出", "123" in groups and "456" in groups, groups)

# ============================================================ T11 老库迁移
print("")
print("[T11] 老库（没有 self_gid 列）自动补列，且行为不变")
d3 = fresh("t11")
old = os.path.join(d3, "registry.db")
con = sqlite3.connect(old)
con.execute("CREATE TABLE IF NOT EXISTS user_group("
            "uid TEXT PRIMARY KEY, gid TEXT NOT NULL, ts INTEGER NOT NULL)")
con.execute("INSERT INTO user_group(uid,gid,ts) VALUES('old1','X',100)")
con.execute("INSERT INTO user_group(uid,gid,ts) VALUES('old2','Y',100)")
con.commit()
con.close()
# 把缓存清掉，模拟服务重启后重新打开这个老库
dbmod._cache.pop(old, None)
reg = dbmod.get_registry(d3)
cols = {r["name"] for r in reg.q("PRAGMA table_info(user_group)")}
check("T11.1 老库自动补上 self_gid 列", "self_gid" in cols, cols)
check("T11.2 老库自动补上 self_ts 列", "self_ts" in cols, cols)
check("T11.3 存量行的 self_gid = 原来的 gid（行为跟今天一致）",
      reg.q1("SELECT self_gid FROM user_group WHERE uid=?", ("old1",))["self_gid"] == "X")
server.DATA_DIR = d3
check("T11.4 迁移后老用户私聊认得到原来的群",
      server._resolve_gid("", "old1") == "X", server._resolve_gid("", "old1"))

print("")
if FAILED:
    print("FAILED: %d" % len(FAILED))
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("ALL OK")

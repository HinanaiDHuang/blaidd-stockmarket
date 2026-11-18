# -*- coding: utf-8 -*-
"""v4.9.5 回归：网页端「换群」—— 多群用户切账户。

【这一版在加什么】
一个 QQ 常常同时在好几个群里炒股。以前网页只认链接里那一个群，
想看另一个群的账户只能回群里再发一次「炒股」拿新链接，太绕。
现在网页上多一条「换群」下拉：服务端给他签一个目标群的新令牌。

【安全边界】
只允许切到「这个 QQ 在那个群本来就有账户」的群 ——
那些人本来就在群里、发一句「炒股」就能自己拿到该群的链接和令牌，
按账户授权不构成提权；没有账户的群一律不给换。
"""
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import db as dbmod
import market
import server

WORK = os.path.join(HERE, "_t_groups")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
server.DATA_DIR = WORK
server.CFG["data_dir"] = WORK
server.CFG["public_host"] = "127.0.0.1:9030"

GA = "10001"          # u1、u2 都在
GB = "10002"          # 只有 u1
GC = "10003"          # 谁都没有（u1 没账户）

for gid in (GA, GB):
    h = dbmod.get_db(WORK, gid)
    market.ensure_user(h, "u1", "甲", 100000.0)
    market.ensure_user(h, "u2", "乙", 100000.0)
# u2 从 B 群退掉了（那里没有他的账户）
dbmod.get_db(WORK, GB).ex("DELETE FROM users WHERE qq='u2'")
# C 群：谁都没有账户（但库文件在，known_groups 要能看见它）
dbmod.get_db(WORK, GC)

FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, extra))
        FAILED.append(name)


sess = server.app.test_client()
TOK_A = server.make_token(GA, "u1", 3600)
H = {"X-Token": TOK_A}


def get_json(path, headers=None):
    r = sess.get(path, headers=headers or {})
    return r.status_code, r.get_json()


def post_json(path, body, headers=None):
    r = sess.post(path, headers=headers or {}, json=body)
    return r.status_code, r.get_json()


print("[T12] 换群：能列出我有账户的群")
code, j = get_json("/api/g/mygroups?g=%s&u=u1" % GA, H)
check("T12.1 鉴权通过返回 200", code == 200, code)
gids = sorted(x["gid"] for x in (j.get("data") or {}).get("groups", []))
check("T12.2 只列出我有账户的群（A、B），没有 C", gids == [GA, GB], gids)
cur = [x for x in j["data"]["groups"] if x.get("cur")]
check("T12.3 当前群标记正确", len(cur) == 1 and cur[0]["gid"] == GA, cur)
check("T12.4 每项都带 gid 字段", all("gid" in x and "name" in x
                                   for x in j["data"]["groups"]), j["data"]["groups"])

code, j = get_json("/api/g/mygroups?g=%s&u=u1" % GA)
check("T12.5 没令牌 → 403", code == 403, code)
code, j = get_json("/api/g/mygroups?g=%s&u=u1" % GA, {"X-Token": "1.2"})
check("T12.6 假令牌 → 403", code == 403, code)

code, j = get_json("/api/g/mygroups?g=%s&u=u2" % GA,
                   {"X-Token": server.make_token(GA, "u2", 3600)})
g2 = sorted(x["gid"] for x in (j.get("data") or {}).get("groups", []))
check("T12.7 只在 A 群有账户的人只看到 A", g2 == [GA], g2)

print("")
print("[T13] 换群：令牌按「群 + 用户」重签")
code, j = post_json("/api/g/switch", {"g": GA, "u": "u1", "to": GB}, H)
check("T13.1 切到有账户的群 → 200", code == 200, (code, j))
d = j.get("data") or {}
NEW = d.get("t")
check("T13.2 返回了新令牌", bool(NEW))
check("T13.3 新令牌对目标群有效", server.check_token(GB, "u1", NEW))
check("T13.4 新令牌对原群无效（换群必须换令牌）",
      not server.check_token(GA, "u1", NEW))
check("T13.5 令牌不能给别人用", not server.check_token(GB, "u2", NEW))
check("T13.6 返回的群号是目标群", d.get("g") == GB, d.get("g"))

code, j = post_json("/api/g/switch", {"g": GA, "u": "u1", "to": GC}, H)
check("T13.7 切到没有账户的群 → 403", code == 403, (code, j))

code, j = post_json("/api/g/switch", {"g": GA, "u": "u1", "to": GB},
                    {"X-Token": "1.2"})
check("T13.8 没令牌不能换群 → 403", code == 403, code)

code, j = post_json("/api/g/switch", {"g": GA, "u": "u1", "to": GA}, H)
check("T13.9 切回当前群 → same，原样还令牌", j.get("data", {}).get("same") is True,
      j.get("data"))
check("T13.10 same 时令牌不变",
      j.get("data", {}).get("t") == TOK_A, j.get("data", {}).get("t"))

print("")
print("[T14] 换群：拿新令牌能正常读写目标群")
code, j = get_json("/api/state?g=%s&u=u1" % GB, {"X-Token": NEW})
check("T14.1 新令牌能读目标群的数据", code == 200, code)
check("T14.2 读出来的是目标群的账户",
      j.get("data", {}).get("me", {}).get("cash") == 100000.0,
      j.get("data", {}).get("me"))

code, j = get_json("/api/state?g=%s&u=u1" % GA, {"X-Token": NEW})
check("T14.3 新令牌读不了原群", code == 403, code)

print("")
print("[T15] 换群：私聊认群跟着切")
server._remember_group("u1", GA)
server._remember_group("u1", GB)
check("T15.1 切群后私聊认到新群", server._resolve_gid("", "u1") == GB,
      server._resolve_gid("", "u1"))
reg = dbmod.get_registry(WORK)
check("T15.2 登记表 self_gid 更新了",
      reg.q1("SELECT self_gid FROM user_group WHERE uid=?", ("u1",))["self_gid"] == GB)

print("")
print("[T16] 辅助函数")
check("T16.1 _user_groups 只含有账户的群",
      sorted(server._user_groups("u1")) == [GA, GB], server._user_groups("u1"))
check("T16.2 别人没账户的群不出现",
      GC not in server._user_groups("u1"), server._user_groups("u1"))
check("T16.3 空 uid 返回空表", server._user_groups("") == [])
check("T16.4 群名取不到时返回空串（前端退回显示群号）",
      server._group_name(GC) == "", server._group_name(GC))

print("")
if FAILED:
    print("FAILED: %d" % len(FAILED))
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("ALL OK")

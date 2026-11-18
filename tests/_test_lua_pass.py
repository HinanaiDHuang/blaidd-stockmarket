# -*- coding: utf-8 -*-
"""v4.2 Lua 测试：群里发「炒股」当场给一次性口令 / 重置口令。"""
import json, os, tempfile, io, sys
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from lupa import LuaRuntime

SRC = os.path.normpath(os.path.join(HERE, "..", "plugin", "炒股.lua"))
lua = LuaRuntime(unpack_returned_tuples=True)
g = lua.globals()


def to_lua(obj):
    if isinstance(obj, dict):
        t = lua.table()
        for k, v in obj.items():
            t[k] = to_lua(v)
        return t
    if isinstance(obj, (list, tuple)):
        t = lua.table()
        for i, v in enumerate(obj, 1):
            t[i] = to_lua(v)
        return t
    return obj


SESSION = {"code": 200, "message": "ok", "data": {
    "url": "http://YOUR_SERVER_IP:9030/?g=10001&u=555#t=123.abc",
    "token": "123.abc", "g": "10001", "u": "555"}}
PASSCODE = {"code": 200, "message": "ok", "data": {
    "passcode": "654321", "exp": 9999999999, "ttl": 600,
    "u": "555", "g": "10001", "site": "http://YOUR_SERVER_IP:9030/",
    "permanent": False}}

g["__TEMP"] = tempfile.gettempdir()
g["__SESSION_LUA"] = to_lua(SESSION)
g["__PASSCODE_LUA"] = to_lua(PASSCODE)
lua.execute(r"""
package.preload["json"] = function()
  return { decode = function(d)
    return table.remove(__DECODE_QUEUE, 1)
  end }
end
__DECODE_QUEUE = {}
__CALLLOG = ""
__PAYLOADLOG = ""
__SENTLOG = ""
function getGroupConf(g, k)
  if k == "members" then return {"111", "222", "333"} end
  return nil
end
function getUserConf(q, k) return "昵称" .. tostring(q) end
function sendMsg(msg, group, user)
  __SENTLOG = __SENTLOG .. "\n---SENT---" .. tostring(msg)
end
__LAST_URL = nil
os.execute = function(cmd)
  __LAST_URL = cmd
  __CALLLOG = __CALLLOG .. "\n" .. cmd
  local out = cmd:match('-o%s+"([^"]+)"')
  local pay = cmd:match('--data%-binary%s+"@([^"]+)"')
  local paytext = ""
  if pay then
    local f = io.open(pay, "r")
    if f then paytext = f:read("*a") f:close() end
  end
  __PAYLOADLOG = __PAYLOADLOG .. "\n" .. paytext
  local body = "{}"
  if cmd:find("/api/g/passcode", 1, true) then
    table.insert(__DECODE_QUEUE, __PASSCODE_LUA)
    body = "__PASSCODE_JSON__"
  else
    table.insert(__DECODE_QUEUE, __SESSION_LUA)
    body = "__SESSION_JSON__"
  end
  if out then
    local f = io.open(out, "w")
    if f then f:write(body) f:close() end
  end
  return true
end
""")
g["__SESSION_JSON__"] = json.dumps(SESSION, ensure_ascii=False)
g["__PASSCODE_JSON__"] = json.dumps(PASSCODE, ensure_ascii=False)
lua.execute(open(SRC, encoding="utf-8").read())
print("[syntax] OK")

fails = []


def ck(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def run(expr):
    return lua.execute("return " + expr)


def calls():
    return (g["__CALLLOG"] or "").split("\n")


def payloads():
    return [x for x in (g["__PAYLOADLOG"] or "").split("\n") if x.strip()]


def reset():
    g["__CALLLOG"] = ""
    g["__PAYLOADLOG"] = ""
    g["__SENTLOG"] = ""


print("\n=== 1. 群里发「炒股」：当场给一次性口令 ===")
reset()
r = run('chaogu({fromGroup="10001", fromQQ="555", fromMsg="炒股"})')
text = str(r)
print(text)
ck("回执里有站点地址", "http://YOUR_SERVER_IP:9030/" in text)
ck("回执里有口令", "654321" in text)
ck("回执说明口令一次性", "只能用一次" in text)
ck("回执提醒 30 天免登录", "30 天" in text)
ck("调了 /api/session", any("/api/session" in c for c in calls()))
ck("调了 /api/g/passcode", any("/api/g/passcode" in c for c in calls()))
pay = payloads()
ck("passcode 请求带 uid", '"u":"555"' in pay[-1].replace(" ", ""), pay[-1])
ck("passcode 请求带 force=false", '"force":false' in pay[-1].replace(" ", ""), pay[-1])
ck("群路径不再由 Dice 私信直链（改由服务端 send_private_msg 投递）",
   (g["__SENTLOG"] or "").count("---SENT---") == 0, g["__SENTLOG"])
ck("回执指引私聊拿链接", "私聊我" in text)
ck("回执提醒可以设自己的登录口令", "登录口令" in text)

print("\n=== 2. 已经有固定口令 ===")
PERM = dict(PASSCODE)
PERM["data"] = dict(PASSCODE["data"])
PERM["data"]["permanent"] = True
PERM["data"].pop("passcode")
g["__PASSCODE_LUA"] = to_lua(PERM)
g["__PASSCODE_JSON__"] = json.dumps(PERM, ensure_ascii=False)
reset()
r = run('chaogu({fromGroup="10001", fromQQ="555", fromMsg="炒股"})')
text = str(r)
print(text)
ck("提示已设固定口令", "固定口令" in text)
ck("不泄露新口令", "654321" not in text)
ck("告诉怎么重置", "重置口令" in text)
g["__PASSCODE_LUA"] = to_lua(PASSCODE)
g["__PASSCODE_JSON__"] = json.dumps(PASSCODE, ensure_ascii=False)

print("\n=== 3. 私聊发「炒股」：直接给链接 ===")
reset()
r = run('chaogu({fromGroup="", fromQQ="555", fromMsg="炒股"})')
text = str(r)
print(text)
ck("私聊给的是直链", "#t=123.abc" in text)
ck("私聊不铸口令", not any("/api/g/passcode" in c for c in calls()))

print("\n=== 4. 「炒股 公开」：链接直接发群里 ===")
reset()
r = run('chaogu({fromGroup="10001", fromQQ="555", fromMsg="炒股 公开"})')
text = str(r)
ck("公开模式给直链", "#t=123.abc" in text)
ck("公开模式不铸口令", not any("/api/g/passcode" in c for c in calls()))

print("\n=== 5. 「重置口令」 ===")
reset()
r = run('reset_passcode({fromGroup="10001", fromQQ="555", fromMsg="重置口令"})')
text = str(r)
print(text)
ck("给出新口令", "654321" in text)
ck("说明旧口令失效", "旧口令已失效" in text)
pay = payloads()
ck("重置请求带 force=true", '"force":true' in pay[-1].replace(" ", ""), pay[-1])

print("\n=== 6. 铸口令失败要有兜底 ===")
g["__PASSCODE_LUA"] = to_lua({"code": 500, "message": "boom", "data": None})
g["__PASSCODE_JSON__"] = json.dumps({"code": 500, "message": "boom", "data": None})
reset()
r = run('chaogu({fromGroup="10001", fromQQ="555", fromMsg="炒股"})')
text = str(r)
print(text)
ck("失败时提示私聊", "私聊我" in text)
g["__PASSCODE_LUA"] = to_lua(PASSCODE)
g["__PASSCODE_JSON__"] = json.dumps(PASSCODE, ensure_ascii=False)

print("\n=== 7. 私聊重置口令 ===")
reset()
r = run('reset_passcode({fromGroup="", fromQQ="555", fromMsg="重置口令"})')
text = str(r)
ck("私聊也能重置", "654321" in text, text)

print()
if fails:
    print("FAILED: %d" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")

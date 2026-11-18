# -*- coding: utf-8 -*-
"""离线跑炒股.lua 的「亏损榜」新指令。"""
import json, os, tempfile, io, sys
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from lupa import LuaRuntime

SRC = os.path.normpath(os.path.join(HERE, "..", "plugin", "炒股.lua"))
lua = LuaRuntime(unpack_returned_tuples=True)
g = lua.globals()
RESP = {"code": 200, "message": "ok", "data": {}}


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


g["__RESP"] = to_lua(RESP)
g["__TEMP"] = tempfile.gettempdir()
lua.execute(r"""
package.preload["json"] = function()
  return { decode = function(d) return __RESP end }
end
__SENT = {}
function getGroupConf(g, k)
  if k == "members" then return {"111", "222", "333"} end
  return nil
end
function getUserConf(q, k) return "昵称" .. tostring(q) end
function sendMsg(msg, group, user)
  table.insert(__SENT, {msg = msg, group = group, user = user})
end
__LAST_PAYLOAD = nil
__LAST_URL = nil
__JSON_TEXT = "{}"
os.execute = function(cmd)
  __LAST_URL = cmd
  local out = cmd:match('-o%s+"([^"]+)"')
  local pay = cmd:match('--data%-binary%s+"@([^"]+)"')
  if pay then
    local f = io.open(pay, "r")
    if f then __LAST_PAYLOAD = f:read("*a") f:close() end
  end
  if out then
    local f = io.open(out, "w")
    if f then f:write(__JSON_TEXT) f:close() end
  end
  return true
end
""")
g["__JSON_TEXT"] = json.dumps(RESP, ensure_ascii=False)
lua.execute(open(SRC, encoding="utf-8").read())
print("[syntax] OK")


def setresp(obj):
    g["__RESP"] = to_lua(obj)
    g["__JSON_TEXT"] = json.dumps(obj, ensure_ascii=False)


MSG = {"fromGroup": "10001", "fromUser": "111", "fromNick": "群友1"}
g["__MSG"] = to_lua(MSG)

# ---- 指令注册 ----
reg = {}
for name in ("亏损榜", "亏钱榜", "出局榜", "破产榜", "输家榜"):
    fn = lua.execute("return msg_order['%s']" % name)
    assert fn == "stock_board_lose", "%s -> %s" % (name, fn)
    reg[name] = fn
print("[OK] 指令注册:", reg)

# ---- 出局 + 亏钱两个人 ----
setresp({"code": 200, "message": "ok", "data": {"kind": "lose", "g": "10001", "items": [
    {"rank": 1, "qq": "4", "nick": "老输家", "equity": -5000.0, "pl": -5000.0,
     "bankrupt": True, "loss": 105000.0, "tag": "out"},
    {"rank": 2, "qq": "3", "nick": "亏哥", "equity": 60000.0, "pl": -40000.0,
     "bankrupt": False, "loss": 40000.0, "tag": "loss"},
]}})
out = str(lua.execute("return stock_board_lose(__MSG)"))
print("\n[亏损榜输出]")
print("    " + out.replace("\n", "\n    "))
assert "出局榜" in out and "亏钱榜" in out, out
assert "老输家" in out and "亏哥" in out
assert "105000" in out and "40000" in out
assert "👉" in out, "箭头 emoji 坏了: " + out
print("[OK] 分区/金额/emoji 正确")

# ---- 只有亏钱的人 ----
setresp({"code": 200, "message": "ok", "data": {"items": [
    {"rank": 1, "qq": "3", "nick": "亏哥", "equity": 60000.0, "pl": -40000.0,
     "bankrupt": False, "loss": 40000.0, "tag": "loss"},
]}})
out2 = str(lua.execute("return stock_board_lose(__MSG)"))
assert "暂时还没有人破产" in out2, out2
print("[OK] 空出局榜有兜底文案")

# ---- 一条都没有 ----
setresp({"code": 200, "message": "ok", "data": {"items": []}})
out3 = str(lua.execute("return stock_board_lose(__MSG)"))
assert "暂时还没有数据" in out3, out3
print("[OK] 空榜单兜底")

print("\nALL LUA LOSE BOARD OK")

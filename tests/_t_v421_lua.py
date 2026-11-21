# -*- coding: utf-8 -*-
"""v4.21 Lua 测试：个人借贷 P2P 的 6 条群命令（解析 + 拼包 + 报错文案）。"""
import json, os, sys, io, tempfile
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from lupa import LuaRuntime

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "plugin", "炒股.lua"))
lua = LuaRuntime(unpack_returned_tuples=True)
g = lua.globals()
FAILED = []


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


g["__TEMP"] = tempfile.gettempdir()
lua.execute(r"""
package.preload["json"] = function()
  return { decode = function(d) return table.remove(__DECODE_QUEUE, 1) end }
end
__DECODE_QUEUE = {}
__PAYLOAD = ""
__URL = ""
function getGroupConf(g, k)
  if k == "members" then return {"111", "222", "333"} end
  return nil
end
function getUserConf(q, k)
  local s = tostring(k)
  if s:find("nick#") then
    if q == "111" then return "小明" end
    return nil
  end
  return "昵称" .. tostring(q)
end
os.execute = function(cmd)
  __URL = cmd
  local out = cmd:match('-o%s+"([^"]+)"')
  local pay = cmd:match('--data%-binary%s+"@([^"]+)"')
  if pay then
    local f = io.open(pay, "r")
    if f then __PAYLOAD = f:read("*a") f:close() end
  end
  table.insert(__DECODE_QUEUE, __RESP_LUA)
  if out then
    local f = io.open(out, "w")
    if f then f:write("__RESP_JSON__") f:close() end
  end
  return true
end
""")

RESP = {"code": 200, "message": "ok", "data": {"msg": "服务端回执", "text": "面板文字"}}


def run(cmd, resp=None, group="9001", qq="555"):
    g["__RESP_LUA"] = to_lua(resp if resp is not None else RESP)
    lua.execute("__PAYLOAD = ''")
    lua.execute("__URL = ''")
    msg = lua.table()
    msg["fromMsg"] = cmd
    msg["fromQQ"] = qq
    msg["fromGroup"] = group
    msg["chatType"] = 2
    key = cmd.split()[0]
    order = lua.globals()["msg_order"]
    hname = order[key] if order is not None else None
    fn = lua.globals()[hname] if hname else None
    if fn is None:
        return None, {}, ""
    out = fn(msg)
    payload = lua.execute("return __PAYLOAD")
    url = lua.execute("return __URL")
    return out, (json.loads(payload) if payload else {}), (url or "")


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


lua.execute(open(SRC, encoding="utf-8").read())

print("[T13] 申请借款")
out, pl, url = run("申请借款 5000 @小明 7")
check("返回服务端文案", out == "服务端回执", repr(out))
check("金额 5000", pl.get("amount") == 5000, pl)
check("期限 7", pl.get("term") == 7, pl)
check("债主 QQ", pl.get("target_qq") == "111", pl)
check("债主昵称", pl.get("target_name") == "小明", pl)
check("打到 /api/g/p2p_apply", "/api/g/p2p_apply" in url, url)

out, pl, _ = run("申请借款 1.5万 @小明")
check("1.5万 = 15000", pl.get("amount") == 15000, pl)
check("缺期限用 0（服务端走默认）", pl.get("term") == 0, pl)

out, pl, _ = run("申请借款 3k [CQ:at,qq=999] 10")
check("CQ 码取 QQ", pl.get("target_qq") == "999", pl)
check("CQ 码无昵称", pl.get("target_name") == "", pl)
check("3k = 3000", pl.get("amount") == 3000, pl)

out, pl, _ = run("申请借款 5000")
check("没指定债主会拦", out and "要向谁借" in out, repr(out))
check("拦下时没发包", pl == {}, pl)

out, pl, _ = run("申请借款 abc @小明")
check("金额非法会拦", out and "没看懂要借多少钱" in out, repr(out))

print("[T14] 同意/拒绝借款")
out, pl, url = run("同意借款 小红 0.5")
check("accept=true", pl.get("accept") is True, pl)
check("利率 0.5", pl.get("rate") == 0.5, pl)
check("借款人昵称", pl.get("borrower_name") == "小红", pl)
check("打到 /api/g/p2p_decide", "/api/g/p2p_decide" in url, url)

out, pl, _ = run("同意借款 @小明 0.8")
check("CQ/裸 @ 解析出 QQ", pl.get("borrower_qq") == "111", pl)

out, pl, _ = run("拒绝借款 小红")
check("accept=false", pl.get("accept") is False, pl)
check("拒绝不用填利率", pl.get("rate") == 0, pl)

out, pl, _ = run("同意借款 小红")
check("缺利率会拦", out and "还没填利率" in out, repr(out))
check("缺利率没发包", pl == {}, pl)

out, pl, _ = run("同意借款")
check("空参数给用法", out and "用法" in out, repr(out))

print("[T15] 我的借贷 / 还P2P / 失信榜")
out, pl, url = run("我的借贷")
check("走 GET p2p_info", "/api/g/p2p_info" in url, url)
check("返回面板文字", out == "面板文字", repr(out))

out, pl, url = run("还P2P 5000")
check("金额 5000", pl.get("amount") == 5000, pl)
check("打到 /api/g/p2p_repay", "/api/g/p2p_repay" in url, url)

out, pl, url = run("还P2P 1万")
check("1万 = 10000", pl.get("amount") == 10000, pl)

out, pl, _ = run("还P2P")
check("没金额给用法", out and "用法" in out, repr(out))

out, pl, url = run("失信榜")
check("走 GET p2p_defaults", "/api/g/p2p_defaults" in url, url)

print("[T16] 失败文案透传")
out, pl, _ = run("我的借贷", resp={"code": 400, "message": "还没找到你所在的群", "data": {}})
check("code!=200 拼 message", out and "还没找到你所在的群" in out, repr(out))

print("")
if FAILED:
    print("FAIL: %d 项" % len(FAILED))
    for f in FAILED:
        print("   -", f)
    sys.exit(1)
print("v4.21 Lua 全部通过")
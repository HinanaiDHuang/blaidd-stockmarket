# -*- coding: utf-8 -*-
"""v4.10 Lua 回归：群里发【止损 / 止盈 / 限价】时参数怎么被解析。

盯的是三件事：
1. 方向可以显式写（多 / 空 / 买 / 卖），不写就传空串让服务端按持仓推断 ——
   之前是写死 long，只持空头的人永远挂不上止损。
2. 参数顺序统一成「股票名 [方向] 价格 [数量]」（跟同花顺一致），
   之前代码取最后一个数为价格，帮助里写的「价格 数量」根本挂不上。
3. 解析不出来的时候给的是人话提示，而不是把一堆乱码丢给服务端。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
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


ORDER_OK = {"code": 200, "message": "ok", "data": {"msg": "已挂单", "g": "10001"}}
g["__ROUTES"] = to_lua([["/api/g/order", ORDER_OK]])

lua.execute(r"""
package.preload["json"] = function()
  return { decode = function(d) return table.remove(__DECODE_QUEUE, 1) end }
end
__DECODE_QUEUE = {}
__CALLLOG = ""
__PAYLOADLOG = ""
function getGroupConf(g, k) if k == "members" then return {"111", "222"} end return nil end
function getUserConf(q, k) return "昵称" .. tostring(q) end
function sendMsg(msg, group, user) end
os.execute = function(cmd)
  __CALLLOG = __CALLLOG .. "\n" .. cmd
  local out = cmd:match('-o%s+"([^"]+)"')
  local pay = cmd:match('--data%-binary%s+"@([^"]+)"')
  local paytext = ""
  if pay then
    local f = io.open(pay, "r")
    if f then paytext = f:read("*a") f:close() end
  end
  __PAYLOADLOG = __PAYLOADLOG .. "\n" .. paytext
  for i = 1, #__ROUTES do
    local r = __ROUTES[i]
    if cmd:find(r[1], 1, true) then
      table.insert(__DECODE_QUEUE, r[2])
      break
    end
  end
  if out then
    local f = io.open(out, "w")
    if f then f:write("{}") f:close() end
  end
  return true
end
""")
lua.execute(open(SRC, encoding="utf-8").read())
print("[syntax] 炒股.lua 加载 OK")

FAILS = []


def ck(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def run(expr):
    return lua.execute("return " + expr)


def reset():
    g["__PAYLOADLOG"] = ""
    g["__CALLLOG"] = ""


def payloads():
    raw = (g["__PAYLOADLOG"] or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def call_stop(cmd, text):
    reset()
    r = str(run('stock_stop_loss({fromGroup="10001", fromQQ="555", fromMsg="%s"})' % text))
    return r, payloads()


def call_take(text):
    reset()
    r = str(run('stock_take_profit({fromGroup="10001", fromQQ="555", fromMsg="%s"})' % text))
    return r, payloads()


def call_limit(text):
    reset()
    r = str(run('stock_limit({fromGroup="10001", fromQQ="555", fromMsg="%s"})' % text))
    return r, payloads()


print("\n=== 1. 不写方向 = 交服务端按持仓推断 ===")
r, p = call_stop("止损", "止损 平安银行 18.5")
ck("返回服务端的话", "已挂单" in r, r[:40])
ck("股票名对", p and p.get("name") == "平安银行", p)
ck("方向留空", p and p.get("side") == "", p and repr(p.get("side")))
ck("价格 18.5", p and abs(float(p.get("trigger")) - 18.5) < 1e-9, p)
ck("数量 0（全仓）", p and float(p.get("qty")) == 0, p)
ck("类型是止损", p and p.get("kind") == "stop_loss", p)

print("\n=== 2. 显式写方向 ===")
r, p = call_stop("止损", "止损 平安银行 空 19.2")
ck("方向=空", p and p.get("side") == "short", p)
ck("价格 19.2", p and abs(float(p.get("trigger")) - 19.2) < 1e-9, p)
ck("股票名里没有方向词", p and p.get("name") == "平安银行", p)
r, p = call_stop("止损", "止损 平安银行 多 18.5")
ck("方向=多", p and p.get("side") == "long", p)
r, p = call_stop("止损", "止损 平安银行 做空 19.2")
ck("「做空」也认", p and p.get("side") == "short", p)
r, p = call_stop("止损", "止损 平安银行 空头 19.2")
ck("「空头」也认", p and p.get("side") == "short", p)

print("\n=== 3. 价格 + 数量（同花顺的顺序）===")
r, p = call_stop("止损", "止损 平安银行 18.5 500")
ck("价格 18.5", p and abs(float(p.get("trigger")) - 18.5) < 1e-9, p)
ck("数量 500", p and abs(float(p.get("qty")) - 500) < 1e-9, p)
r, p = call_stop("止损", "止损 平安银行 空 19.2 500")
ck("带方向时价格 19.2", p and abs(float(p.get("trigger")) - 19.2) < 1e-9, p)
ck("带方向时数量 500", p and abs(float(p.get("qty")) - 500) < 1e-9, p)
ck("带方向时 side=short", p and p.get("side") == "short", p)

print("\n=== 4. 「全仓」关键词 ===")
r, p = call_stop("止损", "止损 平安银行 18.5 全仓")
ck("全仓 = 数量 0", p and float(p.get("qty")) == 0, p)
ck("全仓时价格还在", p and abs(float(p.get("trigger")) - 18.5) < 1e-9, p)

print("\n=== 5. 止盈 ===")
r, p = call_take("止盈 平安银行 空 9.5 200")
ck("类型是止盈", p and p.get("kind") == "take_profit", p)
ck("方向=空", p and p.get("side") == "short", p)
ck("价格 9.5", p and abs(float(p.get("trigger")) - 9.5) < 1e-9, p)
ck("数量 200", p and abs(float(p.get("qty")) - 200) < 1e-9, p)

print("\n=== 6. 限价单（开仓，方向必须写）===")
r, p = call_limit("限价 平安银行 买 18.5 1000")
ck("方向=多", p and p.get("side") == "long", p)
ck("价格 18.5", p and abs(float(p.get("trigger")) - 18.5) < 1e-9, p)
ck("数量 1000", p and abs(float(p.get("qty")) - 1000) < 1e-9, p)
r, p = call_limit("限价 平安银行 卖 19.5 1000")
ck("方向=空", p and p.get("side") == "short", p)

print("\n=== 7. 参数不对要给人话提示 ===")
r, p = call_stop("止损", "止损 平安银行")
ck("缺价格给用法", "用法" in r, r[:60])
ck("缺价格不发请求", p is None, p)
r, p = call_stop("止损", "止损 平安银行 abc")
ck("价格不是数字给提示", "价格不是数字" in r, r[:60])
r, p = call_stop("止损", "止损 平安银行 1 2 3")
ck("参数太多给提示", "参数太多" in r, r[:60])
r, p = call_limit("限价 平安银行 涨 18.5 1000")
ck("限价方向写错给提示", "方向" in r, r[:60])
r, p = call_limit("限价 平安银行 买 18.5")
ck("限价缺数量给用法", "用法" in r, r[:60])

print("\n=== 8. 条件单列表里的用法说明 ===")
reset()
r = str(run('stock_orders({fromGroup="10001", fromQQ="555", fromMsg="条件单"})'))
ck("用法里写了方向", "[多/空]" in r, r[-160:])

if FAILS:
    print("\nFAIL: %d 项没过：%s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("\nALL OK")

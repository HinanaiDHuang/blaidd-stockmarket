# -*- coding: utf-8 -*-
"""v4.7 Lua 回归：档位 / 打土豪 / 挥霍 / 分红 / 财富分布 / 新榜单 + 指令注册。"""
import json
import os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
import os
import sys
import tempfile
import io

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


TIER = {"code": 200, "message": "ok", "data": {
    "text": "👑 土豪 的净值分层\n· 当前档位：豪门（净值 2000000.00）",
    "st": {"tier": "豪门", "lev_cap": 26}, "g": "10001"}}
ROB_GET = {"code": 200, "message": "ok", "data": {
    "text": "🦹 打土豪（花钱抢有钱人的现金）\n· 你今天还能出手 2 次",
    "st": {"left": 2, "limit": 2, "targets": []}, "g": "10001"}}
ROB_POST = {"code": 200, "message": "ok", "data": {
    "msg": "🦹 打土豪得手！\n· 从 土豪 兜里抢走 10000 元", "g": "10001"}}
LUX_GET = {"code": 200, "message": "ok", "data": {
    "text": "🛍 挥霍清单（花掉净值的 5%，换声望）：\n🏰 江景大宅：声望 +3",
    "st": {"price": 100000, "rep": 0.0}, "g": "10001"}}
LUX_POST = {"code": 200, "message": "ok", "data": {
    "msg": "🏰 江景大宅到手！\n· 花了 100000 元\n· 声望 +3", "g": "10001"}}
DIV_GET = {"code": 200, "message": "ok", "data": {
    "text": "🪙 全民分红池\n· 池子余额 80000 元\n· 你今天可以领 89.40 元",
    "st": {"pool": 80000, "amount": 89.4}, "g": "10001"}}
DIV_POST = {"code": 200, "message": "ok", "data": {
    "msg": "🪙 全民分红到账 89.40 元！", "g": "10001"}}
WEALTH = {"code": 200, "message": "ok", "data": {
    "text": "📊 群内财富分布\n· 216 人，净资产合计 25350007 元\n· 前 10% 的人占 41.2% 的钱",
    "st": {"top10_share": 41.2, "n": 216}, "g": "10001"}}
BOARD = {"code": 200, "message": "ok", "data": {
    "items": [{"rank": 1, "qq": "7102", "nick": "猎人", "equity": 50000, "pl": 12000,
               "amount": 12000, "tier": "普通股民", "bankrupt": False}], "g": "10001"}}
g["__ROUTES"] = to_lua([
    ["/api/g/tier?", TIER],
    ["/api/g/rob?", ROB_GET],
    ["/api/g/rob", ROB_POST],
    ["/api/g/luxury?", LUX_GET],
    ["/api/g/luxury", LUX_POST],
    ["/api/g/dividend?", DIV_GET],
    ["/api/g/dividend", DIV_POST],
    ["/api/g/wealth?", WEALTH],
    ["/api/g/board?", BOARD],
])

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


def payloads():
    return [x for x in (g["__PAYLOADLOG"] or "").split("\n") if x.strip()]


def reset():
    g["__PAYLOADLOG"] = ""
    g["__CALLLOG"] = ""

print("\n=== 1. 我的档位 ===")
reset()
r = str(run('stock_tier({fromGroup="10001", fromQQ="555", fromMsg="我的档位"})'))
ck("返回档位说明", "净值分层" in r, r[:40])
ck("走了 GET /api/g/tier", "/api/g/tier" in (g["__CALLLOG"] or ""))

print("\n=== 2. 打土豪 ===")
reset()
r = str(run('stock_rob({fromGroup="10001", fromQQ="555", fromMsg="打土豪"})'))
ck("不带目标返回说明", "打土豪" in r and "2 次" in r, r[:50])
reset()
r = str(run('stock_rob({fromGroup="10001", fromQQ="555", fromMsg="打土豪 土豪"})'))
ck("带目标返回结果", "得手" in r, r[:40])
pay = " ".join(payloads())
ck("请求体带 target=土豪", '"target":"土豪"' in pay, pay[:120])
ck("走了 POST", "--data-binary" in (g["__CALLLOG"] or ""))

print("\n=== 3. 挥霍 ===")
reset()
r = str(run('stock_luxury({fromGroup="10001", fromQQ="555", fromMsg="挥霍"})'))
ck("不带参数返回清单", "挥霍清单" in r, r[:40])
reset()
r = str(run('stock_luxury({fromGroup="10001", fromQQ="555", fromMsg="挥霍 豪宅"})'))
ck("带物品返回结果", "到手" in r, r[:40])
pay = " ".join(payloads())
ck("请求体带 item=豪宅", '"item":"豪宅"' in pay, pay[:120])

print("\n=== 4. 全民分红 ===")
reset()
r = str(run('stock_dividend({fromGroup="10001", fromQQ="555", fromMsg="领分红"})'))
ck("直接领取", "到账" in r, r[:40])
reset()
r = str(run('stock_dividend({fromGroup="10001", fromQQ="555", fromMsg="分红 查询"})'))
ck("带查询看池子", "分红池" in r, r[:40])

print("\n=== 5. 财富分布 ===")
reset()
r = str(run('stock_wealth({fromGroup="10001", fromQQ="555", fromMsg="财富分布"})'))
ck("返回分布", "财富分布" in r, r[:50])

print("\n=== 6. 新榜单 ===")
reset()
r = str(run('stock_board_rob({fromGroup="10001", fromQQ="555", fromMsg="战果榜"})'))
ck("战果榜", "战果榜" in r and "抢到" in r, r[:60])
reset()
r = str(run('stock_board_lux({fromGroup="10001", fromQQ="555", fromMsg="挥霍榜"})'))
ck("挥霍榜", "挥霍榜" in r and "花了" in r, r[:60])

print("\n=== 7. 指令注册表 ===")
for key in ("我的档位", "档位", "富豪俱乐部", "俱乐部", "会员费",
            "打土豪", "打劫", "抢钱", "劫富",
            "挥霍", "消费", "排面", "买豪宅",
            "领分红", "分红", "全民分红", "分红池",
            "财富分布", "贫富差距", "战果榜", "打劫榜", "挥霍榜"):
    v = run('msg_order["%s"]' % key)
    ck("msg_order[%s] 已注册" % key, v is not None and str(v) != "nil", v)

print("")
if FAILS:
    print("FAILED: %d -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL OK")

# -*- coding: utf-8 -*-
"""v4.6 Lua 回归：破产清算 / 撒钱 / 逆袭补助 / 资本碾压 / 散财榜。

顺带把整个 炒股.lua 加载一遍 —— 语法错在这一步就会炸。
"""
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


def j(d):
    return json.dumps(d, ensure_ascii=False)


g["__TEMP"] = tempfile.gettempdir()
LIQ_GET = {"code": 200, "message": "ok", "data": {
    "text": "🧹 破产清盘\n✅ 符合条件，可以申请清盘", "g": "10001"}}
LIQ_POST = {"code": 200, "message": "ok", "data": {
    "msg": "🧹 破产清盘完成 —— 死账解冻了", "g": "10001"}}
SCAT_GET = {"code": 200, "message": "ok", "data": {
    "text": "🧧 撒钱\n今天还能撒 30000 元", "g": "10001"}}
SCAT_POST = {"code": 200, "message": "ok", "data": {
    "msg": "🧧 富豪 甩了个 1000 块的大红包！共 12 份", "g": "10001"}}
CB_GET = {"code": 200, "message": "ok", "data": {
    "text": "🛍 逆袭基金\n👉 你符合条件，今天可领 150.00 元", "g": "10001"}}
CB_POST = {"code": 200, "message": "ok", "data": {
    "msg": "🛍 逆袭基金补助到账 150.00 元。", "g": "10001"}}
SAB = {"code": 200, "message": "ok", "data": {
    "msg": "💣 富豪 对 被砸股份 出手成功\n股价 50.00 -> 44.00（-12.00%）",
    "g": "10001"}}
BOARD = {"code": 200, "message": "ok", "data": {
    "items": [{"rank": 1, "qq": "2001", "nick": "富豪", "equity": 0, "pl": 10000,
               "amount": 10000, "bankrupt": False}], "g": "10001"}}
g["__ROUTES"] = to_lua([
    ["/api/g/liquidation?", LIQ_GET],
    ["/api/g/liquidation", LIQ_POST],
    ["/api/g/scatter?", SCAT_GET],
    ["/api/g/scatter", SCAT_POST],
    ["/api/g/comeback?", CB_GET],
    ["/api/g/comeback", CB_POST],
    ["/api/g/sabotage", SAB],
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


print("\n=== 1. 破产清算 ===")
reset()
r = str(run('stock_liquidation({fromGroup="10001", fromQQ="555", fromMsg="破产清算"})'))
ck("查条件返回说明", "破产清盘" in r, r[:40])
reset()
r = str(run('stock_liquidation({fromGroup="10001", fromQQ="555", fromMsg="破产清算 确认"})'))
ck("确认后执行清盘", "死账解冻" in r, r[:40])
ck("走了 POST /api/g/liquidation",
   "/api/g/liquidation" in (g["__CALLLOG"] or "") and "--data-binary" in (g["__CALLLOG"] or ""))

print("\n=== 2. 撒钱 ===")
reset()
r = str(run('stock_scatter({fromGroup="10001", fromQQ="555", fromMsg="撒钱"})'))
ck("不带参数返回额度", "撒钱" in r and "30000" in r, r[:40])
reset()
r = str(run('stock_scatter({fromGroup="10001", fromQQ="555", fromMsg="撒钱 1000"})'))
ck("撒钱成功返回消息", "大红包" in r, r[:40])
pay = " ".join(payloads())
ck("请求体带 amount=1000", '"amount":1000' in pay, pay[:80])
reset()
r = str(run('stock_scatter({fromGroup="10001", fromQQ="555", fromMsg="撒钱 abc"})'))
ck("非数字给用法提示", "用法" in r, r[:40])

print("\n=== 3. 逆袭基金 ===")
reset()
r = str(run('stock_comeback({fromGroup="10001", fromQQ="555", fromMsg="逆袭补助"})'))
ck("查询资格", "逆袭基金" in r, r[:40])
reset()
r = str(run('stock_comeback_claim({fromGroup="10001", fromQQ="555", fromMsg="领补助"})'))
ck("领取补助", "到账" in r, r[:40])

print("\n=== 4. 资本碾压 ===")
reset()
r = str(run('stock_crush({fromGroup="10001", fromQQ="555", fromMsg="资本碾压 被砸股份"})'))
ck("碾压出手", "出手成功" in r, r[:40])
pay = " ".join(payloads())
ck("请求体带 plan=资本碾压", "资本碾压" in pay, pay[:120])
reset()
r = str(run('stock_crush({fromGroup="10001", fromQQ="555", fromMsg="资本碾压"})'))
ck("不带目标给用法", "用法" in r, r[:40])

print("\n=== 5. 散财榜 ===")
reset()
r = str(run('stock_board_charity({fromGroup="10001", fromQQ="555", fromMsg="散财榜"})'))
ck("榜单文本", "散财榜" in r and "富豪" in r, r[:60])

print("\n=== 6. 指令注册表 ===")
for key in ("破产清算", "清盘", "债务重组", "撒钱", "发红包", "请客", "散财",
            "逆袭补助", "领补助", "资本碾压", "碾压", "散财榜"):
    v = run('msg_order["%s"]' % key)
    ck("msg_order[%s] 已注册" % key, v is not None and str(v) != "nil", v)

print("")
if FAILS:
    print("FAILED: %d -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL OK")

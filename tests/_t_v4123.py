# -*- coding: utf-8 -*-
"""v4.12.3 回归：网页少折腾服务端 + 新群名即时补拉 + 小屏细节。

【这一版在修什么】
1. 网页四个轮询无视「页面切到后台」，用户切走标签页/锁屏后仍在每 3/5/8/10 秒
   打接口。服务端读限流是 240 次/分**按 QQ 号分桶**，同一个人多开两个群的
   网页就可能撞上限弹「刷新太频繁」。
2. get() 自带 2 次重试，一次失败实际打 3 次；碰上限流就是「越打越慢、
   越慢越打」的雪崩。现在连续失败后加冷却退避。
3. 新群要等下一次半小时的全量刷新才显示群名 —— 期间在「换群」下拉里
   就是一串数字，看着像坏了。现在缺名字就排队，后台几秒内补拉。
4. 小屏：顶栏标签字号偏小、品牌名在 320px 下挤到按钮、「回到最新」点击区偏矮。
"""
import io
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(HERE, "..", "py", "stockmarket"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, SRC_DIR)
import db as dbmod
import server

WORK = os.path.join(HERE, "_t_v4123")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)

# 把服务端的数据目录指到临时目录，别碰真库
server.DATA_DIR = WORK
cfg = json.load(open(os.path.join(SRC_DIR, "config.json"), encoding="utf-8"))
NOW = int(time.time())
FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, extra))
        FAILED.append(name)


# ============================================================ T1 群名即时补拉
print("[T1] 群名「缺了就排队、几秒内补拉」")


class _FakeBot(object):
    def __init__(self, outer):
        self.outer = outer

    def call(self, api, params, timeout=4):
        self.outer.calls.append((api, params))
        return {"data": {"group_name": "测试群名"}}


class _FakePusher(object):
    def __init__(self, connected=True):
        self.connected = connected
        self.calls = []
        self.bot = _FakeBot(self)


def reset_queue():
    with server._gname_lock:
        server._gname_pending.clear()
        server._gname_try_at.clear()


# --- 1.1 非数字 gid 直接忽略
reset_queue()
server._queue_group_name("abc")
server._queue_group_name("")
server._queue_group_name(None)
check("T1.1 非法 gid 不入队", len(server._gname_pending) == 0,
      repr(sorted(server._gname_pending)))

# --- 1.2 数字 gid 入队
reset_queue()
server._queue_group_name("123456789")
check("T1.2 数字 gid 入队", "123456789" in server._gname_pending,
      repr(sorted(server._gname_pending)))

# --- 1.3 5 分钟节流：同一个群不重复排队
reset_queue()
server._queue_group_name("123456789")
server._queue_group_name("123456789")
server._queue_group_name("123456789")
check("T1.3 同群 5 分钟内不重复排队", len(server._gname_pending) == 1,
      repr(sorted(server._gname_pending)))

# --- 1.4 不同群各自入队
reset_queue()
server._queue_group_name("111")
server._queue_group_name("222")
check("T1.4 不同群各自入队", len(server._gname_pending) == 2,
      repr(sorted(server._gname_pending)))

# --- 1.5 节流到期后可以再排（把上次时间往前拨）
reset_queue()
server._queue_group_name("123456789")
with server._gname_lock:
    server._gname_try_at["123456789"] = time.time() - 301
server._queue_group_name("123456789")
check("T1.5 超过 5 分钟后可以再排", len(server._gname_pending) == 1)

# --- 1.6 _group_name 拿到名字时返回名字，且不入队
reset_queue()
reg = dbmod.get_registry(server.DATA_DIR)
reg.set_meta("gname_777", "%d|%s" % (NOW, "有名字的群"))
name = server._group_name("777")
check("T1.6 有缓存时返回群名", name == "有名字的群", repr(name))
check("T1.7 有缓存时不入队", len(server._gname_pending) == 0,
      repr(sorted(server._gname_pending)))

# --- 1.8 没有缓存时返回空串 + 入队
reset_queue()
name = server._group_name("888")
check("T1.8 没缓存时返回空串", name == "", repr(name))
check("T1.9 没缓存时排队补拉", "888" in server._gname_pending,
      repr(sorted(server._gname_pending)))

# --- 1.10 _fetch_group_names 真的去拉并写缓存
reset_queue()
fake = _FakePusher(connected=True)
server.PUSHER = fake
got = server._fetch_group_names(["555"])
check("T1.10 拉到 1 个群名", got == 1, str(got))
check("T1.11 打的是 get_group_info", fake.calls and fake.calls[0][0] == "get_group_info",
      repr(fake.calls))
check("T1.12 群名已写进缓存", server._group_name("555") == "测试群名",
      repr(server._group_name("555")))

# --- 1.13 推送通道没连上时不发请求
fake2 = _FakePusher(connected=False)
server.PUSHER = fake2
got = server._fetch_group_names(["666"])
check("T1.13 通道未连接时不打 OneBot", got == 0 and not fake2.calls,
      "got=%d calls=%d" % (got, len(fake2.calls)))

# --- 1.14 拉失败不炸
class _BadPusher(object):
    connected = True
    class _B(object):
        def call(self, api, params, timeout=4):
            raise RuntimeError("boom")
    bot = _B()


server.PUSHER = _BadPusher()
got = server._fetch_group_names(["777"])
check("T1.14 拉失败不抛异常", got == 0, str(got))
server.PUSHER = _FakePusher(connected=True)

# ============================================================ T2 /api/candles 兼容 limit
print("[T2] /api/candles 同时认 n 和 limit")
client = server.app.test_client()
GID = "t4123"
UID = "90001"
tok = server.make_token(GID, UID, 3600)

# 造一点 K 线
h = dbmod.get_db(server.DATA_DIR, GID)
h.ex("INSERT INTO companies(qq,nick,industry,name,ticker,price,prev_close,open,high,low,"
     "beta,drift,vol,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
     ("9000", "庄", "测试", "测试公司", "T4123", 10.0, 10.0, 10.0, 10.0, 10.0,
      1.0, 0.0, 0.03, NOW))
cid = int(h.q1("SELECT id FROM companies WHERE ticker=?", ("T4123",))["id"])
for i in range(120):
    h.ex("INSERT INTO candles(cid,ts,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)",
         (cid, NOW - 120 * 60 + i * 60, 10.0, 10.5, 9.5, 10.1, 100))

q = "g=%s&u=%s&t=%s&cid=%d" % (GID, UID, tok, cid)
r1 = client.get("/api/candles?%s&n=40" % q)
d1 = r1.get_json()
r2 = client.get("/api/candles?%s&limit=40" % q)
d2 = r2.get_json()
n1 = len(d1.get("data") or [])
n2 = len(d2.get("data") or [])
check("T2.1 n=40 返回 40 根", r1.status_code == 200 and n1 == 40, "n=%d" % n1)
check("T2.2 limit=40 返回 40 根", r2.status_code == 200 and n2 == 40, "n=%d" % n2)
check("T2.3 两种写法结果一致", d1.get("data") == d2.get("data"))
r3 = client.get("/api/candles?%s&limit=5" % q)
d3 = r3.get_json()
check("T2.4 小于下限被夹到 30", len(d3.get("data") or []) == 30,
      str(len(d3.get("data") or [])))
r4 = client.get("/api/candles?%s&n=99999" % q)
d4 = r4.get_json()
check("T2.5 大于上限被夹到 1000", len(d4.get("data") or []) == 120,
      str(len(d4.get("data") or [])))

# ============================================================ T3 前端静态自查
print("[T3] 前端文件自查")
ix = open(os.path.join(SRC_DIR, "static", "index.html"), encoding="utf-8").read()
js = open(os.path.join(SRC_DIR, "static", "app.js"), encoding="utf-8").read()
cs = open(os.path.join(SRC_DIR, "static", "style.css"), encoding="utf-8").read()
srv = open(os.path.join(SRC_DIR, "server.py"), encoding="utf-8").read()

# --- 版本号：css / js 两处一致且不是旧版
_v = re.findall(r"\?v=([0-9a-zA-Z]+)", ix)
check("T3.1 静态资源版本号两处一致", len(_v) == 2 and _v[0] == _v[1], repr(_v))
check("T3.2 版本号已往上走", bool(_v) and _v[0] > "20261119a", repr(_v))

# --- 页面隐藏时挂起轮询
check("T3.3 pollEvery 里判断 document.hidden", "document.hidden" in js)
_poll = js[js.index("function pollEvery("): js.index("function pollEvery(") + 700]
check("T3.4 隐藏时不发请求（提前 return）",
      "if (document.hidden)" in _poll and "return;" in _poll)
check("T3.5 挂起时短周期重试", "setTimeout(tick, 2000)" in _poll)

# --- 恢复可见立刻补一次
check("T3.6 监听 visibilitychange", 'addEventListener("visibilitychange"' in js)
_vis = js[js.index('addEventListener("visibilitychange"'):][:400]
check("T3.7 恢复可见时全量刷新一次",
      "refreshState" in _vis and "refreshCandles" in _vis and "refreshBoard" in _vis
      and "refreshFun" in _vis)

# --- 失败冷却
check("T3.8 有冷却状态变量", "_netCoolUntil" in js and "_netFail" in js)
_get = js[js.index("function get(path, extra)"): js.index("function get(path, extra)") + 1400]
check("T3.9 冷却只在网络错/429/5xx 时加", "st === 429" in _get and "st >= 500" in _get)
check("T3.10 403 不触发冷却", "!st || st === 429 || st >= 500" in _get
      and "st === 403" not in _get)
check("T3.11 冷却有上限 30s", "30000" in _get)
check("T3.12 成功一次就清零", "_netFail = 0" in _get)

# --- 小屏细节
check("T3.13 品牌名超长省略", "text-overflow:ellipsis" in
      re.search(r"\.brand \.name\{[^}]*\}", cs).group(0))
_lbl = re.findall(r"\.w-label\{font-size:([\d.]+)px\}", cs)
check("T3.14 顶栏标签字号都 >= 9.5px",
      bool(_lbl) and all(float(x) >= 9.5 for x in _lbl), repr(_lbl))
_btn = re.search(r"#btnLatest\{[^}]*\}", cs)
check("T3.15 回到最新按钮点击区 >= 30px",
      bool(_btn) and "min-height:30px" in _btn.group(0))

# --- 公告
check("T3.16 公告首条是 v4.12.3", '"ver": "v4.12.3"' in srv)
check("T3.17 公告提到后台暂停", "后台" in srv and "v4.12.3" in srv)

print("")
if FAILED:
    print("FAILED: %d -> %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("ALL OK")
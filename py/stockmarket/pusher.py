# -*- coding: utf-8 -*-
"""OneBot v11 推送客户端 —— 让炒股模块能主动往 QQ 群里发消息。

背景
----
炒股模块一直没有推送通道：价格提醒、每日复盘只能「随叫随到」，
用户不发指令就永远看不到。llbot 的 ob11 HTTP 是关着的，改配置要重启整条链路。

但其实不需要走那条路：**SnowLuma（llbot 桥）本身就是一个完整的 OneBot v11 实现**，
127.0.0.1:15800 是它的 WebSocket 服务端，Dice! 核心就是以 WS 客户端身份连上去发动作的。
我们用同样的方式再连一个客户端，就能直接下发 send_group_msg，
不动 llbot 配置、不动 SnowLuma 代码、不碰 AstrBot 那条链路。

安全边界
--------
* 只发消息（send_group_msg / send_private_msg）和只读查询；
  绝不调用任何删除类动作。
* 连不上就静默降级，推送失败绝不影响主服务（行情、交易照常）。
* 每条推送都记 push_log，保证「提醒只推一次、早报每天一次」。
"""
import json
import threading
import time

try:
    import websocket
except ImportError:  # 没装 websocket-client 就不推了，服务照常跑
    websocket = None


class OneBot:
    """一条到 OneBot v11 WS 服务端的连接。"""

    def __init__(self, url="ws://127.0.0.1:15800/"):
        self.url = url
        self.app = None
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.pending = {}
        self.seq = 0
        self.last_error = ""
        # 【v4.17】连接成功回调列表：调用方可以在这里挂「连上了就干点什么」
        # （炒股那边用它做「重连后立刻补刷群名」—— 以前只能等 30 分钟的定时刷新）。
        self.on_connect = []

    # ---------------- 连接 ----------------
    def _on_open(self, ws):
        self.ready.set()
        print("[stockmarket] pusher connected -> %s" % self.url, flush=True)
        # 【v4.17】通知调用方：通道通了（首次连接或掉线重连都算）
        for cb in list(self.on_connect):
            try:
                cb()
            except Exception as exc:
                print("[stockmarket] pusher on_connect callback error:", exc, flush=True)

    def _on_error(self, ws, err):
        self.last_error = str(err)
        self.ready.clear()

    def _on_close(self, ws, code, msg):
        self.ready.clear()

    def _on_message(self, ws, message):
        try:
            j = json.loads(message)
        except Exception:
            return
        if not isinstance(j, dict):
            return
        echo = j.get("echo")
        if not echo:
            return  # 事件我们用不上，直接丢
        with self.lock:
            ev = self.pending.pop(str(echo), None)
        if ev:
            ev[1] = j
            ev[0].set()

    def run_forever(self):
        if websocket is None:
            raise RuntimeError("websocket-client not installed")
        self.app = websocket.WebSocketApp(
            self.url, on_open=self._on_open, on_error=self._on_error,
            on_close=self._on_close, on_message=self._on_message)
        # 注意：max_size 是构造参数，不是 run_forever 的参数（老版本 websocket-client 会直接报错）
        self.app.run_forever(ping_interval=30, ping_timeout=10)

    # ---------------- 动作 ----------------
    def call(self, action, params=None, timeout=10):
        """发一个动作并等回包（带 echo 配对）。"""
        if not self.ready.is_set() or self.app is None:
            return None
        with self.lock:
            self.seq += 1
            tag = "p%d_%d" % (self.seq, int(time.time() * 1000) % 100000)
        ev = [threading.Event(), None]
        with self.lock:
            self.pending[tag] = ev
        try:
            self.app.send(json.dumps({"action": action, "params": params or {}, "echo": tag}))
        except Exception as exc:
            with self.lock:
                self.pending.pop(tag, None)
            self.last_error = str(exc)
            return None
        if not ev[0].wait(timeout):
            with self.lock:
                self.pending.pop(tag, None)
            return None
        resp = ev[1] or {}
        if resp.get("status") != "ok":
            self.last_error = json.dumps(resp, ensure_ascii=False)[:200]
            return resp
        return resp

    def send_group(self, gid, text, at_qq=None):
        segs = []
        if at_qq:
            segs.append({"type": "at", "data": {"qq": str(at_qq)}})
        segs.append({"type": "text", "data": {"text": text}})
        return self.call("send_group_msg", {"group_id": int(gid), "message": segs})

    def send_private(self, uin, text):
        segs = [{"type": "text", "data": {"text": text}}]
        return self.call("send_private_msg", {"user_id": int(uin), "message": segs})


class Pusher:
    """后台线程：保持连接 + 失败自动重连。"""

    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.url = self.cfg.get("push_ws", "ws://127.0.0.1:15800/")
        self.enabled = bool(self.cfg.get("push_enable", True))
        self.bot = OneBot(self.url)
        self.stop = threading.Event()
        self.thread = None

    @property
    def connected(self):
        return self.bot.ready.is_set()

    def start(self):
        if not self.enabled or websocket is None:
            if websocket is None:
                print("[stockmarket] pusher disabled: websocket-client not installed", flush=True)
            else:
                print("[stockmarket] pusher disabled by config", flush=True)
            return
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        backoff = 3
        while not self.stop.is_set():
            try:
                self.bot.run_forever()
            except Exception as exc:
                print("[stockmarket] pusher loop error:", exc, flush=True)
            self.bot.ready.clear()
            if self.stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def shutdown(self):
        self.stop.set()

    # ---------------- 对外接口 ----------------
    def send_group(self, gid, text, at_qq=None, retries=2):
        """发群消息，失败自动重试。

        【为什么要重试】llbot 偶发返回 retcode 100
        「group message delivery was not confirmed: QQ returned no message receipt」
        —— 消息其实已经发到群里了，只是 QQ 没回消息 ID，llbot 就当失败。
        老逻辑一次失败就直接丢掉，结果价格提醒 / 条件单触发 / 黑天鹅播报
        凭空消失，用户完全不知道自己漏掉了什么。重试一次基本都能补上。
        """
        if not self.enabled or not self.connected:
            return False
        last = None
        for attempt in range(retries + 1):
            try:
                resp = self.bot.send_group(gid, text, at_qq)
            except Exception as exc:
                print("[stockmarket] push failed:", exc, flush=True)
                resp = None
            if resp and resp.get("status") == "ok":
                return True
            last = resp
            if attempt < retries:
                time.sleep(1.0 + attempt)
        print("[stockmarket] push rejected: %s" % json.dumps(last, ensure_ascii=False)[:200],
              flush=True)
        return False

    def send_private(self, uin, text, retries=2):
        """发私信，失败自动重试。

        【2026-11-18 修】原来这里失败是**完全静默**的（直接 return False，
        连日志都不打）。群友反馈「加完好友发【炒股】收不到链接」时，
        从日志里完全看不出是哪一步丢的 —— 现在失败一定打日志，
        并且重试两次（QQ 侧放行好友关系有延迟，第一次失败不代表送不到）。
        """
        if not self.enabled or not self.connected:
            return False
        last = None
        for attempt in range(retries + 1):
            try:
                resp = self.bot.send_private(uin, text)
            except Exception as exc:
                print("[stockmarket] private push failed:", exc, flush=True)
                resp = None
            if resp and resp.get("status") == "ok":
                return True
            last = resp
            if attempt < retries:
                time.sleep(1.0 + attempt)
        print("[stockmarket] private push rejected: %s" % json.dumps(last, ensure_ascii=False)[:200],
              flush=True)
        return False


def logged(db, qq, gid, kind, ref=""):
    """记一条推送日志；返回 True 表示这条之前没推过。"""
    row = db.q1("SELECT id FROM push_log WHERE kind=? AND ref=?", (kind, str(ref)))
    if row:
        return False
    db.ex("INSERT INTO push_log(qq,gid,kind,ref,ts) VALUES(?,?,?,?,?)",
          (str(qq or ""), str(gid or ""), kind, str(ref), int(time.time())))
    return True

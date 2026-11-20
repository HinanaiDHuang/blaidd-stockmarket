"""布莱泽模拟炒股 —— Flask 服务入口。

端口 9030。每个群一个独立数据库（data/g_<群号>.db）。
"""
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets as secrets_mod
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows 服务的 stdout 默认是 GBK，推送文案里有 emoji（🏛 🎲 🪙…），
# 一旦被 except 分支 print 出来就会 UnicodeEncodeError 把日志打挂。
# 这里统一改成 UTF-8 + replace，坏字符只会被替换掉，不会让服务崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import db as dbmod
import daily
import features
import market
import newsdata
import pusher
import coupons
import fun
import orders
import recovery
import wealth
import tycoon
import whale
from engine import Engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
    CFG = json.load(fp)


def _load_secrets():
    """Load signing keys: env vars > secrets.json (gitignored) > config.json (dev fallback)."""
    data = {}
    if os.path.exists(SECRETS_PATH):
        try:
            with open(SECRETS_PATH, "r", encoding="utf-8") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:
            pass
    for key, env_name in (("secret", "STOCKMARKET_SECRET"),
                          ("admin_key", "STOCKMARKET_ADMIN_KEY")):
        env_val = os.environ.get(env_name)
        if env_val:
            data[key] = env_val
    return data


def _ensure_secrets(data):
    """Generate missing keys and persist them to secrets.json so a fresh box self-bootstraps."""
    changed = False
    for key in ("secret", "admin_key"):
        if not data.get(key):
            data[key] = secrets_mod.token_hex(16)
            changed = True
    if changed:
        try:
            tmp = SECRETS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            os.replace(tmp, SECRETS_PATH)
        except Exception as exc:
            print("[stockmarket] secrets.json write failed: %s" % exc)
    return data


SECRETS = _ensure_secrets(_load_secrets())
CFG["secret"] = SECRETS["secret"]
CFG["admin_key"] = SECRETS["admin_key"]

# 本地调试可用环境变量覆盖：STOCKMARKET_DEV=1 开 dev 接口，
# STOCKMARKET_PUBLIC_HOST=127.0.0.1:9030 换链接里的地址
if os.environ.get("STOCKMARKET_DEV") == "1":
    CFG["dev_mode"] = True
if os.environ.get("STOCKMARKET_PUBLIC_HOST"):
    CFG["public_host"] = os.environ["STOCKMARKET_PUBLIC_HOST"]

DATA_DIR = os.path.join(BASE_DIR, CFG.get("data_dir", "data"))
os.makedirs(DATA_DIR, exist_ok=True)

# 推送器：连 OneBot v11 的 WS 服务端（SnowLuma 15800），让本模块能主动往群里发消息。
# 连不上不影响主服务，push_loop 里会持续重连。
PUSHER = pusher.Pusher(CFG)

app = Flask(__name__, static_folder=None)
# 令牌有效期（天）。链接会明文发在群里，窗口越短被冒用/泄漏的余地越小；
# 过期了在群里再发一次「炒股」就能拿到新链接。
TOKEN_TTL = 60 * 60 * 24 * int(CFG.get("token_ttl_days", 7))

# ---------------------------------------------------------------- 更新公告
# 网页右上角那个 「最近改动」按钮展开就是这份数据。
# 每次发版往里加一段（最新的放最前面）。
# 主人明说不要在群里发公告，那就把变更说清楚放网页上。
CHANGELOG = [
    {
        "ver": "v4.16",
        "date": "2026-11-20",
        "title": "服务换成 waitress + 端口独占：野进程抢不走线上流量了",
        "items": [
            "HTTP 服务从 Flask 自带的开发服务器换成 waitress（纯 Python 线程池）：开发服务器每来一个请求就往 stderr 写一行访问日志，18 天把 DicePyStock.err 撑到 41 MB，真正的报错埋在里面根本看不见；换掉之后全量访问日志直接消失，err 里只剩 waitress 自己的告警和异常",
            "并发改成固定 16 个工作线程（上限 1000 连接、单通道 120 秒超时），比开发服务器「一个连接一个线程」稳得多；SQLite 那边语义没变（群级 RLock + check_same_thread=False）",
            "慢请求（超过 1.5 秒）和 4xx/5xx 仍然会记进日志，但只打路径不打令牌，网页「最近改动」和日志里都不会漏密钥",
            "端口改成独占模式：Windows 上监听套接字默认允许别人抢同一个端口，之前就有人用 `cmd /c server.py` 手拉了一份野进程（跑的是旧代码），线上流量全被它接走，我们发了新版本却没生效；现在这种情况会直接起不来并报错，不会再「悄无声息地跑旧代码」",
            "服务器侧加了守护巡检：野生的 server.py 进程会被自动清掉，端口没人听时自动把服务重新拉起来",
            "顺手修了一个真 bug：K 线写入是「先查有没有、没有就插」两步，调度线程和新闻线程同时处理同一根 K 线时，第二个线程撞 UNIQUE 约束，这个异常一路冒到调度器，**整个群这一 tick 的后续处理全被中断**（线上 8 个群轮流报这个错）；现在整段在群级锁里做，不会再互相踩",
        ],
    },
    {
        "ver": "v4.14",
        "date": "2026-11-20",
        "title": "涨跌停不再是一台印钞机（Kaito 两天 1300 万 → 534 亿的根因）",
        "items": [
            "查清了主群 Kaito 两天从 1300 万涨到 534.57 亿的根因：232 笔平仓 221 笔盈利（95.3%），赢/亏比 267000:1 —— 不是他有多厉害，是涨跌停的硬夹取让贴板的票对大盘「单边绝缘」",
            "实测（266 只票 / 2 天 K 线）：遇大盘级利空，带内票平均 -1.69%，贴跌停的票 +0.0056%（一点不跌）；遇大盘级利好，带内 +1.19%，贴涨停的票 +0.046%（一点不涨）——「贴跌停抄底」等于一张免费的大盘看跌期权，首次触及跌停后 30 分钟平均 +3.19%、胜率 97.4%",
            "修法一：离涨跌停不到 1% 的股票暂时不能建仓（平仓随时能跑）。现网实测绝缘效应就集中在带沿：贴跌停的票 30 分钟超额 +1.06%、胜率 78.8%（贴涨停做空 -0.19%、胜率 43.0%），20 倍杠杆下每笔就是 +21% 保证金的稳赢机器",
            "修法二：涨跌停改成「反射」代替「截断」—— 越界多少往回弹多少，保住鞅性，边界上不再有单向漂移",
            "修法三：大单加冲击成本 —— 单子越接近把整只票当天的成交额吃掉，成交价越差（买贵 / 卖便宜），这部分直接进全民分红池；普通小单基本感觉不到",
            "修法四：全民分红池和每个人的分红额挂钩（池子越大分得越多，封顶 6 倍），池子有上限，满了以后多余的钱记成沉淀，不再是个只进不出的黑洞",
            "网页分红面板现在直接显示挂钩倍数、封顶倍数、池子上限和沉淀额；「怎么玩」补了「封板和大单」一节",
        ],
    },
    {
        "ver": "v4.13.2",
        "date": "2026-11-20",
        "title": "挥霍清单终于有价格梯度了",
        "items": [
            "原来 6 件排面品（座驾 / 腕表 / 大宅 / 游艇 / 会所 / 飞机）价格完全一样，声望却差 3.5 倍 —— 排面根本没有梯度，现在每件都有自己的档位价钱",
            "档位从「入门」到「顶配」，价格是基准价（净值的 5%）的 0.5 倍到 5 倍：同一份身家，清单从「一顿饭」一路排到「一套房」",
            "网页「挥霍」面板和群里的「挥霍」清单都直接标出每件东西多少钱、值多少声望，不再是一句「一单大约 X 元」",
            "买不起的东西按钮直接置灰并写明「钱不够」；随机挥霍也只在你买得起的档位里挑",
        ],
    },
    {
        "ver": "v4.13.1",
        "date": "2026-11-20",
        "title": "财阀基金的一处记账漏洞（首次结算前就修掉了）",
        "items": [
            "没人押对的时候，本来要分给赢家的那笔钱（每天上限 3 万）没有归属、直接蒸发了 —— 现在并进全民分红池",
            "修完之后基金的账严丝合缝：出资人拿到的 + 群友拿到的 + 分红池多出来的 = 基金在市场上的盈亏，一分不差",
            "补了 4 条回归测试专门盯着这笔钱，确认它不会再消失",
        ],
    },
    {
        "ver": "v4.13",
        "date": "2026-11-20",
        "title": "财阀基金：富豪的钱有了带风险的去处",
        "items": [
            "新玩法「财阀基金」：净值 1500 万以上的富豪，每天自动把净值的 5% 押进一个带 3 倍杠杆的市场赌局",
            "基金涨跌都算富豪的：市场跌了，出资人自己扛；市场涨了，三成分给押对的群友，五成和管理费一起进全民分红池",
            "群友可以「押基金 涨」/「押基金 跌」，一次最多 1000 元；押错的钱直接进全民分红池，不给赢家",
            "会员费的封顶不再是一刀切的 3 万：改成 max(3万, 净值×0.26%)，巨富不再免税",
            "新增持仓税：净值 500 万以上、仓位里的钱每天按保证金 0.1% 交税 —— 钱锁在仓位里复利也要交税",
            "分红池改成按余额比例发放：池子里趴着多少钱，每天就按比例发多少（原来上限写死 8000，池子再大也发不出去）",
            "大户仓位上限：净值 5000 万以上的账户，总名义仓位不超过「自有净资产」（净值 − 保证金）的 20 倍（原来能开到净值的 60~90 倍）",
            "网页「我的」页新增「财阀基金」面板：群友可以直接押涨/押跌，还能看今日出资榜和押注榜",
        ],
    },
    {
        "ver": "v4.12.3",
        "date": "2026-11-19",
        "title": "网页少折腾服务端，小屏再抠一点",
        "items": [
            "网页切到后台（切别的标签页、锁屏）时自动停掉刷新：回到前台立刻补一次最新的 —— 不用对着旧数据等下一轮",
            "网络抽风时网页会自己缓一缓再重试，不再越刷越慢、越慢越刷（也不容易撞上「刷新太频繁」）",
            "新加的群不用再等半小时才显示群名：第一次看到的是一串数字的话，几秒内就换成真群名",
            "小屏顶栏的「净值 / 可用 / 欠款 / 浮动」标签字号加大一点，320px 下不那么费眼",
            "320px 下顶栏品牌名太长会挤到按钮，现在改成超长自动省略",
            "「回到最新」按钮点击区加高到 30px，手机上好按一点",
        ],
    },
    {
        "ver": "v4.12.2",
        "date": "2026-11-19",
        "title": "网页小毛病一批：条件单方向、K 线 OHLC、新手引导",
        "items": [
            "网页上挂止损 / 止盈，方向可以选「自动」了：按你现在的持仓判断，多空两头都拿着才需要手动选（MT4 就是把 SL/TP 挂在持仓上的）",
            "K 线下面那行 OHLC 不再是「开 -- 高 -- 低 -- 收 --」：默认显示最后一根 K 线，指哪根显示哪根（MT4 / MT5 的老样子）",
            "桌面端鼠标划过 K 线就能出十字光标，不用点一下再看",
            "新手引导的步骤号不再跳号（原来会出现 1、2、4、5）",
            "小屏（320px）顶栏的净值不再被截成「106,68...」，一万以上自动去掉小数",
            "财经快讯里个股消息不再把标题重复抄一遍，每条少一行字",
            "「怎么玩」补上了新规则：短线交易费、贷款看净值、破产后 10 天冷静期",
        ],
    },
    {
        "ver": "v4.12",
        "date": "2026-11-19",
        "title": "短线交易费，以及借钱到底怎么算",
        "items": [
            "持仓越短手续费越高：2 分钟内 5 倍、10 分钟内 2.5 倍、30 分钟内 1.5 倍（真实市场对频繁交易本来就有额外成本）",
            "借钱看的是「净值」不是「现金」：手上还有浮盈就能借；资不抵债（净值≤0）才算真破产",
            "破产清盘后有 10 天冷静期：不能贷款、杠杆最多 1 倍（这条以前写了但没生效）",
            "纾困 / 司法重整期间不能新增贷款，堵掉了「破产→免债→再借」的刷钱漏洞",
        ],
    },
    {
        "ver": "v4.11",
        "date": "2026-11-18",
        "title": "快被强平时先给你提个醒",
        "items": [
            "净值逼近强平线会先收到提醒（15 分钟一条，不刷屏），还有时间自己减仓",
            "被强平会收到 QQ 私信，网页上也有横幅 —— 不会再「仓位凭空消失」",
            "逐笔止损不再把人直接送进破产：平仓 ≠ 破产",
            "网页偶尔一直转圈、或者弹出英文报错的，修好了；现在全是中文提示",
            "读限流从「按 IP」改成「按人」，一个运营商共享出口 IP 不会再把全群一起限流",
        ],
    },
    {
        "ver": "v4.10",
        "date": "2026-11-18",
        "title": "止损止盈不用再自己选多空了",
        "items": [
            "只持空头也能挂止损 / 止盈了：系统按你现在的持仓方向自动判断（MT4 / MT5 就是这么挂的）",
            "群里也支持方向词：多 / 空 / 买 / 卖 / 做多 / 做空 / 多头 / 空头 / 多单 / 空单",
            "参数顺序统一成「价格 数量」（跟同花顺一致），以前按「数量 价格」写挂不上",
            "一边触发平了仓，另一边自动撤销（OCO），不再留一张野单占名额",
        ],
    },
    {
        "ver": "v4.9.5",
        "date": "2026-11-18",
        "title": "网页上能换群了",
        "items": [
            "同时在好几个群玩的人，网页上多一条「换群」：点一下就切到另一个群的账户（钱、持仓、榜单各算各的）",
            "只在一个群玩的人看不到这一行，不占地方",
            "标签栏会吸顶了，滚到哪儿都能换页（以前得先滚回顶部）",
        ],
    },
    {
        "ver": "v4.9.4",
        "date": "2026-11-18",
        "title": "私聊认群不再被人改判",
        "items": [
            "同时在多个群玩的话，私聊发「炒股」认的是你自己最后动手的那个群，不会被别人发消息顶掉",
            "「0 号群」这个老版本留下的假群清掉了（里面没有资产，只是空壳）",
        ],
    },
    {
        "ver": "v4.9.3",
        "date": "2026-11-18",
        "title": "挥霍额度提前告诉你",
        "items": [
            "挥霍消费页面直接显示「今天还能挥霍 N / M 次」，不用点了被拒才知道",
            "额度用完时「买」按钮变灰并写明「今天没额度了」",
        ],
    },
    {
        "ver": "v4.9.2",
        "date": "2026-11-18",
        "title": "页面自己会更新了",
        "items": [
            "不用清缓存：服务端所有页面资源都是 no-cache，浏览器刷新就会自动核对版本",
            "不用等公告：页面开着不动也会自己发现「服务端更新了」并自动重载",
            "自动重载后会提示一句「已更新到最新版本」",
        ],
    },
    {
        "ver": "v4.9.1",
        "date": "2026-11-18",
        "title": "打工面板说实话",
        "items": [
            "打工面板改成按你的档位实时显示：当前职级、每日上限、今日已挣、还剩多少",
            "中下层会在打工面板里看到「翻身任务」提示，直接告诉你钱在哪儿",
            "帮助页补了翻身任务说明和新的打工上限规则",
        ],
    },
    {
        "ver": "v4.9",
        "date": "2026-11-18",
        "title": "打工不再压过炒股",
        "items": [
            "打工日上限按档位拉开：普通股民 150，小富 220，大富 320，豪门 480，巨富 700，财阀 1000",
            "中下层（净值 20 万以下）新增「翻身任务」：真的下单买、平仓、签到都能拿奖励",
            "挥霍消费加了每日额度（默认 2 次），声望不能再无限刷",
            "打土豪目标列表不再公开别人的现金余额",
        ],
    },
    {
        "ver": "v4.8",
        "date": "2026-11-18",
        "title": "网页端大改版",
        "items": [
            "底部导航从 6 页压成 5 页，18 个功能收进一个宫格里，点一下就展开",
            "新手引导：会告诉你现在该干什么，已完成的那一天可以一屏看完",
            "「我的」页多了档位、特权、升档进度条（已完成的不再重复占位）",
            "排行页的排行按钮改成横向滑动，不再占两行",
            "令牌过期会直接回登录页，不再留一屏全是 —— 的空壳",
            "网络不好时直接告诉你「网络断了」，不再显示英文错误",
            "巨富、财阀的每日会员费提档：巨富 0.14% → 0.20%，财阀 0.18% → 0.26%",
            "强制清盘的宽限期从 7 天压到 3 天，冻住的钱更早放回来",
        ],
    },
    {
        "ver": "v4.7",
        "date": "2026-11-18",
        "title": "富豪经济",
        "items": [
            "净值分档：超 20 万每天自动缴会员费，换杠杆上限、手续费打折、打工加成",
            "打土豪：花小钱抢有钱人的现金，有成功率，翻车了要赔",
            "挥霍消费：花钱换声望，声望越高越不容易被人抢",
            "全民分红：每天从公共池抱回一笔现金",
            "强制清盘：破产超过宽限期没余钱就强制卖掉持仓",
        ],
    },
    {
        "ver": "v4.6",
        "date": "2026-11-18",
        "title": "破产与回头路",
        "items": [
            "破产超过宽限期没余钱（现在 3 天），强制清盘把冻住的钱放回来",
            "新增「破产出局榜」，每个群都能看到谁清盘走的",
            "新增「散财榜」和「资本碾压」，可以回馈一些光头",
        ],
    },
    {
        "ver": "v4.5",
        "date": "2026-11-18",
        "title": "平衡性与链接修复",
        "items": [
            "重新标定打工、签到、赋金的产出，打工不再印钱",
            "修好「今日复盘」给出的是废数据的问题",
            "修好「加了布莱泽可能半小时才收到链接」的问题，现在是发「股票」当场给口令",
            "可以在网页里设一个固定口令，以后就不用每次找布莱泽要了",
        ],
    },
    {
        "ver": "v4.3",
        "date": "2026-11-17",
        "title": "注册制（链接不再得等半小时）",
        "items": [
            "发「股票」立刻拿到一个 10 分钟有效的口令，打开网页输入就能进",
            "口令只能用一次，用完你可以在网页里改成自己的固定口令",
            "一个口令再次发「股票」就能重新拿一个",
        ],
    },
]


# ---------------------------------------------------------------- 访问日志脱敏
# Werkzeug 的 access log 会把整条 URL（含 t=令牌）写进日志文件，这里把令牌和 QQ 打码。
class _RedactFilter(logging.Filter):
    _PAT = re.compile(r"([?&](?:t|u|admin|key|secret|token)=)[^&\s\"]+")

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if self._PAT.search(msg):
            record.msg = self._PAT.sub(lambda m: m.group(1) + "***", msg)
            record.args = ()
        return True


logging.getLogger("werkzeug").addFilter(_RedactFilter())


# ---------------------------------------------------------------- 工具
def ok(data=None, message="ok"):
    return jsonify({"code": 200, "message": message, "data": data})


def fail(message, code=400):
    resp = jsonify({"code": code, "message": message, "data": None})
    resp.status_code = code if 400 <= code < 600 else 400
    return resp


@app.after_request
def _api_no_store(resp):
    """API 响应一律 no-store。

    【为什么要这个】行情是实时的，任何中间层（运营商透明代理 / 浏览器
    缓存 / CDN）都不该插手。实测移动端偶尔会拿到一个空的或半截的响应体，
    前端 r.json() 直接炸成 "Unexpected end of JSON input"。
    """
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.before_request
def _mark_t0():
    """给每个请求打一个起始时间戳（after_request 里算耗时用）。"""
    request.environ["sm_t0"] = time.time()


@app.after_request
def _log_odd_requests(resp):
    """只记「有问题的请求」：状态码 >= 400，或者慢请求（超过 slow_request_ms）。

    【为什么不全记】
    Flask 自带的开发服务器会把**每个请求**都往 stderr 写一行访问日志。
    现网 8 个群几百人、网页每秒轮询 4 个接口，18 天就把 DicePyStock.err
    撑到 41 MB —— 真正的 Traceback 埋在几十万行访问日志里，磁盘也迟早被吃满。
    换成 waitress 之后开发服务器不走了，全量访问日志自然消失；
    这里补一条「只记异常」的，既保住现场又不吵。

    ⚠️ 只打 path，不打 query —— query 里带着 t= 令牌，不能进日志。
    """
    t0 = request.environ.get("sm_t0")
    dt = (time.time() - t0) if t0 else 0.0
    try:
        slow_ms = float(CFG.get("slow_request_ms", 1500) or 1500)
    except (TypeError, ValueError):
        slow_ms = 1500.0
    if resp.status_code >= 400 or dt * 1000.0 >= slow_ms:
        print("[stockmarket] %s %s g=%s %s %.0fms" % (
            resp.status_code, request.method, request.args.get("g", "-"),
            request.path, dt * 1000.0), flush=True)
    return resp


@app.errorhandler(Exception)
def _api_json_error(exc):
    """接口出错时也返回 JSON，别给前端一段 HTML。

    【为什么要这个】Flask 默认给 500 返回 HTML 页面。前端拿 HTML 去
    JSON.parse 会报 "Unexpected token '<'"，用户完全看不懂。
    现在出错也是标准 JSON，前端能直接把 message 念出来。
    """
    if request.path.startswith("/api/"):
        code = getattr(exc, "code", 500)
        if not isinstance(code, int) or not (400 <= code < 600):
            code = 500
        try:
            return fail("服务开小差了，请稍后再试", code)
        except Exception:
            return fail("服务开小差了，请稍后再试", 500)
    if isinstance(exc, HTTPException):
        return exc
    return "服务器内部错误", 500


def _sign(gid, uid, exp):
    raw = "%s|%s|%s" % (gid, uid, exp)
    return hmac.new(CFG["secret"].encode(), raw.encode(), hashlib.sha256).hexdigest()[:24]


def make_token(gid, uid, ttl=None):
    exp = int(time.time()) + int(ttl if ttl else TOKEN_TTL)
    return "%d.%s" % (exp, _sign(gid, uid, exp))


def check_token(gid, uid, token):
    if not token or "." not in token:
        return False
    try:
        exp, sig = token.split(".", 1)
        exp = int(exp)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(sig, _sign(gid, uid, exp))


def parse_members(raw):
    """解析 Lua 传来的成员名单：qq:nick|qq:nick。"""
    out = []
    if not raw:
        return out
    for chunk in raw.split("|"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        qq, nick = chunk.split(":", 1)
        qq = qq.strip()
        nick = urllib.parse.unquote(nick.strip())
        if qq.isdigit():
            out.append((qq, nick))
    return out


def get_ctx(gid, uid, token, need_user=True):
    gid = str(gid).strip()
    uid = str(uid).strip()
    if not gid or gid == "0":
        # 0 号群是私聊脏数据，别再往里写/读
        return None, None, fail("交易链接无效，请重新在群里发「炒股」", 403)
    # 令牌优先从 X-Token 头取：query 里的 t= 会被 access log 记成明文。
    token = (request.headers.get("X-Token") or token or "").strip()
    if not check_token(gid, uid, token):
        return None, None, fail("链接已过期或无效，请重新在群里发「炒股」", 403)
    handle = dbmod.get_db(DATA_DIR, gid)
    return handle, uid, None


def _is_loopback():
    """只认 TCP 对端地址，不信任 X-Forwarded-For / X-Real-IP 之类的头。"""
    remote = (request.remote_addr or "").strip()
    return remote in ("127.0.0.1", "::1", "localhost")


def check_admin(key):
    """Internal endpoints: trust loopback (the Dice! plugin lives on this box) or require admin key."""
    if _is_loopback():
        return True
    # 优先从 X-Admin 头取：query 里的 admin= 会被 access log 记成明文密钥。
    key = request.headers.get("X-Admin") or key or ""
    want = CFG.get("admin_key", "")
    if not want:
        return False
    try:
        return hmac.compare_digest(str(key or "").encode("utf-8"), want.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def _public_url(gid, uid, token):
    """公网链接。令牌放在 fragment(#t=) 里：浏览器不会把它发给服务器，
    所以既不会进 access log，也不会随 Referer 泄漏给第三方。"""
    return "http://%s/?g=%s&u=%s#t=%s" % (CFG["public_host"], gid, uid, token)


# ---------------------------------------------------------------- 群号解析（注册制）
def _resolve_gid(gid, uid):
    """把群号规整成一个真实存在的群。

    Dice! 私聊时 msg.fromGroup 是空串而不是 nil，老逻辑直接拿 "" 当群号，
    于是私聊会开出 g_0.db 这种假群账户（用户、持仓都串在一起）。
    这里：空/0 一律查登记表，认用户最近玩过的那个群；查不到返回 None。
    """
    gid = str(gid if gid is not None else "").strip()
    if gid and gid != "0":
        return gid
    uid = str(uid if uid is not None else "").strip()
    if not uid:
        return None
    row = dbmod.get_registry(DATA_DIR).q1(
        "SELECT gid, self_gid FROM user_group WHERE uid=?", (uid,))
    if not row:
        return None
    # 优先认「本人操作过」的群。整群登记只是「记他在这个群待过」，
    # 不该因为别人在另一个群发了消息就把他改判过去 —— 多群用户最常踩这个坑。
    return row["self_gid"] or row["gid"]


def _remember_group(uid, gid):
    """记一笔「这个 QQ 最近在哪个群炒股」，私聊时用来认群。"""
    dbmod.get_registry(DATA_DIR).ex(
        "INSERT INTO user_group(uid, gid, ts, self_gid, self_ts) VALUES(?,?,?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET gid=excluded.gid, ts=excluded.ts, "
        "self_gid=excluded.self_gid, self_ts=excluded.self_ts",
        (str(uid), str(gid), int(time.time()), str(gid), int(time.time())))


def _remember_members(gid, members):
    """把整群成员一次性登记进跨群表。

    注册制的关键：群里任意一个人发一次「炒股」，这一群人就都算注册过了，
    之后谁私聊布莱泽发「炒股」都能立刻认到群、拿到自己的交易页，
    不必自己先在群里发过一遍（省掉「等半小时」那一步）。
    """
    if not members:
        return
    now_ts = int(time.time())
    rows = [(str(qq), str(gid), now_ts) for qq, _nick in members]
    try:
        # 注意：这里只更新 gid/ts，不动 self_gid/self_ts ——
        # 整群登记只是「记他在这个群待过」，不是「他自己在玩这个群」。
        # 以前这里会把多群用户改判到别的群去（谁在另一个群发一次消息就改判谁）。
        dbmod.get_registry(DATA_DIR).exmany(
            "INSERT INTO user_group(uid, gid, ts) VALUES(?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET gid=excluded.gid, ts=excluded.ts",
            rows)
    except Exception as exc:
        print("[stockmarket] registry bulk update failed: %s" % exc)


# ---------------------------------------------------------------- 换群（多群用户）
# 一个 QQ 常常同时在好几个群里炒股。以前网页只能认链接里那一个群，
# 想看另一个群的账户只能回群里再发一次「炒股」拿新链接 —— 很别扭。
# 这里给他一个「换群」下拉：在网页上点一下，服务端给他签一个目标群的新令牌。
#
# 【授权边界】只允许切到「这个 QQ 在那个群本来就有账户」的群。
# 注册制的语义是：群里任意一人发过一次「炒股」，全群成员就算在这个群注册过
# （Lua 把成员名单报上来，服务端逐个开户），所以「某群 users 表里有他一行」
# 就等于「他是这个群的注册用户」。而这些人本来就在群里、发一句「炒股」
# 就能自己拿到该群的链接和令牌 —— 按账户授权不构成提权；
# 反过来，没有账户的群一律不给换。

# 【v4.12.3】群名「取不到就排队即时补拉」的队列。
# 【为什么要】以前新群只能等下一次半小时的全量刷新才显示名字 —— 用户新加的群
# 在网页「换群」下拉里就是一串数字，看着像坏了。半小时那次是给「群改名」用的，
# 不该让「新群第一次显示名字」也等这么久。
# 【怎么做】请求里发现没名字就把 gid 丢进队列，后台 2 秒一轮单独补拉；
# 同一个群 5 分钟内不重复排队，防止某个群一直拉不到时把 OneBot 打爆。
_gname_pending = set()
_gname_try_at = {}
_gname_lock = threading.Lock()


def _queue_group_name(gid):
    """没缓存到群名时排队，等后台即时补拉一次。"""
    gid = str(gid).strip()
    if not gid or not gid.isdigit():
        return
    now = time.time()
    with _gname_lock:
        if now - _gname_try_at.get(gid, 0) < 300:
            return
        _gname_try_at[gid] = now
        _gname_pending.add(gid)


def _fetch_group_names(gids):
    """真去 OneBot 拉群名写缓存，返回成功个数。"""
    got = 0
    for gid in gids:
        try:
            if not gid.isdigit() or not PUSHER.connected:
                continue
            resp = PUSHER.bot.call("get_group_info", {"group_id": int(gid)}, timeout=4)
            data = (resp or {}).get("data") or {}
            name = str(data.get("group_name") or "").strip()
            if not name:
                continue
            dbmod.get_registry(DATA_DIR).set_meta(
                "gname_%s" % gid, "%d|%s" % (int(time.time()), name))
            got += 1
        except Exception:
            continue
    return got


def _group_name(gid):
    """群名（只读缓存）。拿不到就返回空串，前端退回显示群号。

    【为什么不现取】群名要走一次 OneBot 往返，网络慢的时候能把网页请求
    拖住好几秒，而且第一个点开下拉的人要替所有人挨这一下。
    改成后台慢慢拉（见 _refresh_group_names），请求里只读缓存。

    【v4.12.3】拿不到名字时顺手排个队，后台几秒内补拉一次 —— 新群不用
    再干等半小时（见 _queue_group_name）。
    """
    gid = str(gid).strip()
    if not gid:
        return ""
    try:
        row = dbmod.get_registry(DATA_DIR).q1(
            "SELECT value FROM meta WHERE key=?", ("gname_%s" % gid,))
        if row and row["value"]:
            parts = row["value"].split("|", 1)
            if len(parts) == 2:
                return parts[1]
    except Exception:
        pass
    _queue_group_name(gid)
    return ""


def _refresh_group_names():
    """后台把群名全量拉一遍（OneBot 的 get_group_info），写进登记表缓存。

    群名会变（改名、退群），所以每半小时刷一次；拉失败就保留旧名，
    实在没有就显示群号 —— 群名只是好看，功能不靠它。
    """
    gids = dbmod.known_groups(DATA_DIR)
    if not gids:
        return
    # 服务刚起来时推送通道还在握手，等它连上再问
    for _ in range(20):
        if PUSHER.connected:
            break
        time.sleep(1)
    got = _fetch_group_names(gids)
    print("[stockmarket] group names refreshed: %d/%d" % (got, len(gids)), flush=True)


def _group_name_worker():
    """待办队列：缺名字的群优先补拉一次（不用等半小时的全量刷新）。"""
    while True:
        time.sleep(2.0)
        try:
            if not PUSHER.connected:
                continue
            with _gname_lock:
                pend = sorted(_gname_pending)
                _gname_pending.clear()
            if not pend:
                continue
            got = _fetch_group_names(pend)
            if got:
                print("[stockmarket] group names on-demand: %d/%d"
                      % (got, len(pend)), flush=True)
        except Exception as exc:
            print("[stockmarket] group name worker error:", exc, flush=True)


def _group_name_loop():
    while True:
        try:
            _refresh_group_names()
        except Exception as exc:
            print("[stockmarket] group name refresh error:", exc, flush=True)
        time.sleep(1800)

def _user_groups(uid):
    """这个 QQ 在哪些群已经开户（= 在那些群注册过）。"""
    uid = str(uid).strip()
    if not uid:
        return []
    out = []
    for gid in dbmod.known_groups(DATA_DIR):
        try:
            handle = dbmod.get_db(DATA_DIR, gid)
            if handle.q1("SELECT 1 FROM users WHERE qq=? LIMIT 1", (uid,)):
                out.append(gid)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- 限流
# 服务直接挂在公网 IP 上，接口要能防刷：单 IP 每分钟最多 N 次写操作。
_RL_BUCKETS = {}
_RL_LOCK = threading.Lock()


def _rate_ok(bucket, limit, window=60.0, ident=None):
    """滑动窗口限流；limit<=0 表示不限。

    【v4.11】ident 不为空时按「用户」分桶，而不是按 IP。
    国内手机卡的出口 IP 是全省共享的，一个运营商 NAT 后面可能站着几十个
    群友，他们加起来的刷新次数轻松冲过 240 次/分 → 全群集体 429，
    页面就一直转圈。令牌是 HMAC 验过签的，用 uid 分桶才公平。
    """
    return _rate_ok_key(bucket, limit, window, ident)


def _rate_ok_key(bucket, limit, window=60.0, ident=None):
    """限流内核。ident 给了就按它分桶，否则按来源 IP。"""
    if limit <= 0:
        return True
    now = time.time()
    key = (bucket, ident or (request.remote_addr or "-"))
    with _RL_LOCK:
        hits = [t for t in _RL_BUCKETS.get(key, ()) if now - t < window]
        if len(hits) >= limit:
            _RL_BUCKETS[key] = hits
            return False
        hits.append(now)
        _RL_BUCKETS[key] = hits
        if len(_RL_BUCKETS) > 2048:
            for stale in [k for k in _RL_BUCKETS
                          if not _RL_BUCKETS.get(k) or now - _RL_BUCKETS[k][-1] > window]:
                _RL_BUCKETS.pop(stale, None)
        return True


def _trade_guard():
    """写操作限流；返回 None 放行，否则返回错误响应。"""
    if not _rate_ok("trade", int(CFG.get("rate_limit_per_min", 60))):
        return fail("操作太频繁，稍等一下再试", 429)
    return None


def engine_of(handle):
    return Engine(handle, CFG)


def _market_state(handle):
    """市场状态：情绪周期 / 恐慌指数 / 熔断（临时休市）。

    前端顶部横幅、群里的「快讯」都用这一份数据。
    """
    try:
        eng = Engine(handle, CFG)
        mood, label, fear, fl = eng.market_mood()
        until, reason = eng.circuit_state()
    except Exception:
        return {"mood": 0.0, "regime": "", "fear": 50.0, "fear_label": "平稳",
                "circuit": False, "circuit_left": 0, "circuit_reason": ""}
    left = 0
    if until:
        left = max(1, (int(until) - int(time.time()) + 59) // 60)
    return {"mood": round(mood, 3), "regime": label, "fear": fear, "fear_label": fl,
            "circuit": bool(until), "circuit_left": left, "circuit_reason": reason}


def _onboarding(handle, cfg, uid):
    """新手引导进度。

    「怎么玩」只是一份静态说明书，新手真正缺的是「我现在该干哪一步、干了没」。
    这里按用户真实数据算 5 步，网页端把没做完的排在前面，点一下就跳过去。
    """
    uid = str(uid)
    steps = []

    row = handle.q1("SELECT COUNT(*) c FROM activity WHERE qq=?", (uid,))
    steps.append({"key": "view", "title": "看懂行情",
                  "desc": "去「行情」页看 K 线、涨跌幅和全部股票",
                  "done": bool(row and int(row["c"] or 0) > 0),
                  "tab": "market", "panel": ""})

    ci = features.checkin_status(handle, cfg, uid)
    steps.append({"key": "checkin", "title": "每日签到",
                  "desc": "签到拿现金，连签满 7 天有大赏",
                  "done": bool(ci.get("done")),
                  "tab": "me", "panel": "checkin"})

    row = handle.q1("SELECT COUNT(*) c FROM trades WHERE qq=?", (uid,))
    steps.append({"key": "trade", "title": "买第一笔",
                  "desc": "10 万初始资金，随便挑一只试试水",
                  "done": bool(row and int(row["c"] or 0) > 0),
                  "tab": "market", "panel": ""})

    row = handle.q1("SELECT COUNT(*) c FROM orders WHERE qq=?", (uid,))
    steps.append({"key": "order", "title": "挂个止损",
                  "desc": "条件单：跌到你的线自动跑，别硬扛",
                  "done": bool(row and int(row["c"] or 0) > 0),
                  "tab": "positions", "panel": ""})

    vote = fun.vote_status(handle, cfg, uid)
    steps.append({"key": "vote", "title": "给群友投票",
                  "desc": "每天一票，给人气公司抬股价",
                  "done": bool(vote.get("voted")),
                  "tab": "me", "panel": "vote"})

    done = sum(1 for s in steps if s["done"])
    nxt = None
    for s in steps:
        if not s["done"]:
            nxt = s
            break
    return {"steps": steps, "done": done, "total": len(steps),
            "finished": done >= len(steps), "next": nxt}


# ---------------------------------------------------------------- 页面
# ---------------------------------------------------------------- 前端版本
_STATIC_VER = {"v": "", "t": 0}


def static_version():
    """当前前端资源版本号（= static/index.html 里 ?v= 的值）。

    【为什么要有它】群里有人问「改了 UI 他们要清缓存吗」——
    答案是不用：服务端对 / 和 /static/* 都发 Cache-Control: no-cache，
    浏览器刷新就会回服务器确认（304=你那份还是最新，200=有更新）。
    但**一直开着的旧标签页**不会自己换 js/css（页面里的定时器只刷数据），
    群友会以为「改了没生效」。这个版本号让旧页面能自己发现
    「服务器上的前端已经变了」并自动重载，不用群友手动清缓存、不用发公告。

    单一事实来源就是 index.html 里的 ?v= —— 改前端时把它换掉，
    这里的版本号就跟着变，客户端一比对就知道自己落后了。
    """
    path = os.path.join(BASE_DIR, "static", "index.html")
    try:
        mtime = int(os.stat(path).st_mtime)
        if mtime != _STATIC_VER["t"]:
            with open(path, "r", encoding="utf-8") as fp:
                html = fp.read()
            m = re.search(r"[?&]v=([0-9A-Za-z_.\-]+)", html)
            _STATIC_VER["v"] = m.group(1) if m else ""
            _STATIC_VER["t"] = mtime
    except Exception:
        pass
    return _STATIC_VER["v"]


@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(os.path.join(BASE_DIR, "static"), filename)


@app.route("/health")
def health():
    data = {"time": int(time.time())}
    if _is_loopback():
        # 群号清单只给本机看，公网别泄露
        data["groups"] = dbmod.known_groups(DATA_DIR)
    return ok(data)


# ---------------------------------------------------------------- 入口
@app.route("/api/link")
def api_link():
    """Dice! 插件在服务器本地调用，拿到带令牌的公网链接。"""
    uid = request.args.get("u", "")
    nick = request.args.get("nick", "")
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    origin_g = str(request.args.get("g") or "").strip()
    gid = _resolve_gid(origin_g, uid)
    if not gid:
        return fail("还没找到你所在的群，先在群里发一次「炒股」再来找我")
    members = parse_members(request.args.get("members", ""))
    if not uid:
        return fail("缺少用户 QQ")
    handle = dbmod.get_db(DATA_DIR, gid)
    if members:
        eng = engine_of(handle)
        eng.sync_companies(members)
        market.sync_users(handle, CFG, members)
        _remember_members(gid, members)
    market.ensure_user(handle, uid, nick, CFG["initial_cash"])
    _remember_group(uid, gid)
    token = make_token(gid, uid)
    url = _public_url(gid, uid, token)
    # 服务端自己再私信一次链接：Dice! 那边的回复走群临时会话，
    # 新加好友的那一次大概率送不到（群友反馈「要等 30 分钟才收到」）。
    # 这条走 OneBot 的 send_private_msg，能拿到明确的 retcode，还能重试。
    if origin_g:
        _push_link_private(uid, gid, url)
    return ok({"url": url, "token": token, "g": gid, "u": uid})


def _push_link_private(uid, gid, url, min_gap=600):
    """服务端主动把交易页链接私信给本人，失败重试（pusher 里已经做了）。

    【为什么必须有服务端这一条路】
    Dice! 的 sendMsg 失败不抛异常也不返回 false，判不了送达与否；
    而且群上下文里发出来的私信走「群临时会话」，对方没先回过话就放行不了。
    群友的原话是「加了布莱泽后不能马上收到链接，要等 30 分钟再发消息才能收到」。
    现在服务端独立走 OneBot 的 send_private_msg：
      · 有明确的 retcode，失败会写进日志（终于能定位是哪一步丢的）；
      · 自动重试两次（QQ 放行好友关系有延迟，第一次失败不代表送不到）；
      · 10 分钟内不重复推，避免同一个人狂发【炒股】被刷私信。
    失败也不阻塞接口：链接还在 Lua 的回复里，用户群里发【炒股】拿口令
    照样能进。

    【只在「群里发起」时调】
    私聊场景 Dice 的回复本身就把链接带出去了，再推一遍等于同一个人连收
    两条一模一样的消息。私聊走的是另一条兜底：Lua 的回复里提示
    「没收到？去群里发一次【炒股】拿一次性口令」。
    """
    try:
        if not PUSHER.connected:
            return False
        reg = dbmod.get_registry(DATA_DIR)
        now_ts = int(time.time())
        try:
            last = int(reg.get_meta("link_push_%s" % uid, "0") or 0)
        except (TypeError, ValueError):
            last = 0
        if last and now_ts - last < min_gap:
            return False
        text = ("\U0001F4C8 布莱泽模拟炒股 · 群 %s\n"
                "打开下面链接交易（7 天内有效，链接等同账户凭证，别转发给别人）：\n%s"
                % (gid, url))
        if PUSHER.send_private(uid, text):
            reg.set_meta("link_push_%s" % uid, str(now_ts))
            return True
        print("[stockmarket] link private push failed uid=%s gid=%s" % (uid, gid), flush=True)
        return False
    except Exception as exc:
        print("[stockmarket] link private push error uid=%s %s" % (uid, exc), flush=True)
        return False


@app.route("/api/session", methods=["POST"])
def api_session():
    """Dice! 插件用 POST 建会话（成员名单可以很长，不受 URL 长度限制）。"""
    j = request.get_json(silent=True) or {}
    if not check_admin(j.get("admin", "")):
        return fail("非法请求", 403)
    uid = str(j.get("u", ""))
    nick = j.get("nick", "")
    if not uid:
        return fail("缺少用户 QQ")
    # 原始群号：空串 = 用户是私聊来的，非空 = 群里发起的。
    # 只有群里发起时才额外私信直链 —— 私聊场景 Dice 的回复里本来就有链接，
    # 再推一遍就是同一个人连着收两条一样的消息。
    origin_g = str(j.get("g") or "").strip()
    gid = _resolve_gid(origin_g, uid)
    if not gid:
        return fail("还没找到你所在的群，先在群里发一次「炒股」再来找我")
    members = []
    for m in (j.get("members") or []):
        if not isinstance(m, dict):
            continue
        mq = str(m.get("qq", "")).strip()
        if mq:
            members.append((mq, str(m.get("nick", ""))))
    handle = dbmod.get_db(DATA_DIR, gid)
    if members:
        eng = engine_of(handle)
        eng.sync_companies(members)
        market.sync_users(handle, CFG, members)
        _remember_members(gid, members)
    market.ensure_user(handle, uid, nick, CFG["initial_cash"])
    _remember_group(uid, gid)
    token = make_token(gid, uid)
    url = _public_url(gid, uid, token)
    if origin_g:
        _push_link_private(uid, gid, url)
    return ok({"url": url, "token": token, "g": gid, "u": uid})


# ---------------------------------------------------------------- 口令登录
# 群里发「炒股」时，QQ 侧的私信根本送不到（群上下文里的私信走「群临时会话」，
# 对方必须先在临时会话窗口回过话才放行），所以链接这条路对新手是死的。
# 改成：群里当场给一个一次性口令，用户自己在网页上输「QQ 号 + 口令」换令牌。
# 一次性口令：10 分钟有效、用掉即焚、只对那一个 QQ 有效；
# 固定口令：用户登录后在网页上自己设，之后长期有效。
PASS_TTL = 600                       # 一次性口令有效期（秒）
LOGIN_TOKEN_TTL = 60 * 60 * 24 * 30   # 登录后拿到的令牌有效期（30 天）
LOGIN_FAIL_MAX = 5                   # 口令错几次就锁
LOGIN_FAIL_LOCK = 900                # 锁多久（秒）
LOGIN_FAIL_LOCK_MIN = 60             # 第一次锁多久（秒），之后翻倍
LOGIN_FAIL_DECAY = 3600              # 超过这个时间的失败不计入累计
_LOGIN_FAIL = {}


def _site_url():
    return "http://%s/" % CFG["public_host"]


def _pwd_hash(uid, pwd):
    """口令只存散列。每个 QQ 加不同的盐，同一个口令在不同账号上散列不同。"""
    raw = "%s|%s|%s" % (CFG["secret"], uid, pwd)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pwd_row(uid):
    return dbmod.get_registry(DATA_DIR).q1(
        "SELECT * FROM user_pass WHERE uid=?", (str(uid),))


def _pwd_put(uid, pwd, exp):
    dbmod.get_registry(DATA_DIR).ex(
        "INSERT INTO user_pass(uid, pwd, ts, exp) VALUES(?,?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET pwd=excluded.pwd, ts=excluded.ts, exp=excluded.exp",
        (str(uid), _pwd_hash(uid, pwd), int(time.time()), int(exp)))


def _pwd_drop(uid):
    dbmod.get_registry(DATA_DIR).ex("DELETE FROM user_pass WHERE uid=?", (str(uid),))


def _fail_lock_left(uid):
    """这个 QQ 还被锁着就返回剩余秒数，没锁返回 0。

    失败计数不再因为「锁期到了」就清零 —— 原来一锁 15 分钟、到期清零、
    再错 5 次又锁 15 分钟，等于可以无限锁某个 QQ（定向 DoS）。
    现在锁时长按失败次数翻倍（60→120→240→480→900 封顶），
    计数只在一小时没再失败或登录成功时才归零。
    """
    now_ts = int(time.time())
    with _RL_LOCK:
        st = _LOGIN_FAIL.get(str(uid))
        if not st:
            return 0
        n, until, last = st
        if until and until > now_ts:
            return until - now_ts
        return 0


def _fail_bump(uid):
    now_ts = int(time.time())
    with _RL_LOCK:
        st = _LOGIN_FAIL.get(str(uid))
        n = 0
        if st:
            prev, _until, last = st
            if now_ts - int(last or 0) <= LOGIN_FAIL_DECAY:
                n = prev
        n += 1
        until = 0
        if n >= LOGIN_FAIL_MAX:
            span = LOGIN_FAIL_LOCK_MIN * (2 ** (n - LOGIN_FAIL_MAX))
            until = now_ts + min(LOGIN_FAIL_LOCK, int(span))
        _LOGIN_FAIL[str(uid)] = (n, until, now_ts)
    return n


@app.route("/api/g/passcode", methods=["POST"])
def api_g_passcode():
    """Dice! 插件调用：给某个 QQ 铸一个一次性口令，群里当面发给他。

    这是绕开「群临时会话送不到私信」的正解：口令只对这一个 QQ 有效，
    10 分钟过期、用掉即焚。已经有固定口令的用户不会被覆盖（覆盖了就废了）。
    """
    j = request.get_json(silent=True) or {}
    if not check_admin(j.get("admin", "")):
        return fail("非法请求", 403)
    uid = str(j.get("u", "")).strip()
    if not uid:
        return fail("缺少用户 QQ")
    gid = _resolve_gid(j.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群，请在群里先发一次「炒股」")
    row = _pwd_row(uid)
    if row and int(row["exp"] or 0) == 0 and not j.get("force"):
        return ok({"permanent": True, "u": uid, "g": gid, "site": _site_url()})
    code = "%06d" % secrets_mod.randbelow(1000000)
    exp = int(time.time()) + PASS_TTL
    _pwd_put(uid, code, exp)
    return ok({"passcode": code, "exp": exp, "ttl": PASS_TTL, "u": uid, "g": gid,
               "site": _site_url(), "permanent": False})


@app.route("/api/login", methods=["POST"])
def api_login():
    """QQ 号 + 口令 → 交易页令牌。公开接口，所以限流 + 失败锁定。"""
    j = request.get_json(silent=True) or {}
    uid = str(j.get("u", "")).strip()
    pwd = str(j.get("pwd", "")).strip()
    if not uid or not pwd:
        return fail("请填写 QQ 号和口令")
    if not _rate_ok("login", int(CFG.get("login_rate_per_min", 20))):
        return fail("尝试太频繁，等一分钟再来", 429)
    left = _fail_lock_left(uid)
    if left:
        return fail("口令错误次数太多，%d 分钟后再试" % max(1, (left + 59) // 60), 429)
    row = _pwd_row(uid)
    now_ts = int(time.time())
    if not row:
        _fail_bump(uid)
        return fail("口令不存在，请在群里发一次「炒股」重新领取", 403)
    exp = int(row["exp"] or 0)
    if exp and exp < now_ts:
        _pwd_drop(uid)
        _fail_bump(uid)
        return fail("口令已过期，请在群里发一次「炒股」重新领取", 403)
    if row["pwd"] != _pwd_hash(uid, pwd):
        _fail_bump(uid)
        return fail("口令不对", 403)
    # 过了：一次性口令用掉即焚，固定口令保留
    if exp:
        _pwd_drop(uid)
    else:
        dbmod.get_registry(DATA_DIR).ex(
            "UPDATE user_pass SET ts=? WHERE uid=?", (now_ts, uid))
    with _RL_LOCK:
        _LOGIN_FAIL.pop(uid, None)
    gid = _resolve_gid(j.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群，请在群里先发一次「炒股」", 403)
    handle = dbmod.get_db(DATA_DIR, gid)
    nick = str(j.get("nick", "") or "")
    urow = handle.q1("SELECT nick FROM users WHERE qq=?", (uid,))
    if urow and urow["nick"]:
        nick = urow["nick"]
    market.ensure_user(handle, uid, nick, CFG["initial_cash"])
    _remember_group(uid, gid)
    token = make_token(gid, uid, LOGIN_TOKEN_TTL)
    url = _public_url(gid, uid, token)
    return ok({"url": url, "token": token, "g": gid, "u": uid, "nick": nick})


@app.route("/api/passcode", methods=["POST"])
def api_passcode_set():
    """登录之后自己设固定口令，以后不用每次找 bot 领一次性口令。"""
    j = request.get_json(silent=True) or {}
    uid = str(j.get("u", "")).strip()
    gid = str(j.get("g", "")).strip()
    handle, uid, err = get_ctx(gid, uid, j.get("t", ""))
    if err:
        return err
    pwd = str(j.get("pwd", "")).strip()
    if len(pwd) < 4 or len(pwd) > 32:
        return fail("口令长度要 4~32 位")
    _pwd_put(uid, pwd, 0)
    return ok({"u": uid, "set": True})


@app.route("/api/g/mygroups", methods=["GET", "POST"])
def api_my_groups():
    """我在哪些群有账户 —— 网页端「换群」下拉用。"""
    gid = request.args.get("g", "")
    uid = request.args.get("u", "")
    token = request.args.get("t", "")
    if request.method == "POST":
        j = request.get_json(silent=True) or {}
        gid = j.get("g", gid) or gid
        uid = j.get("u", uid) or uid
        token = j.get("t", token) or token
    handle, uid, err = get_ctx(gid, uid, token)
    if err:
        return err
    cur = str(gid).strip()
    groups = _user_groups(uid)
    if cur and cur not in groups:
        groups = [cur] + groups
    items = [{"gid": g, "name": _group_name(g), "cur": g == cur} for g in groups]
    return ok({"groups": items, "cur": cur, "count": len(items)})


@app.route("/api/g/switch", methods=["POST"])
def api_switch_group():
    """换到我在另一个群的账户：给他签一个目标群的新令牌。"""
    if not _rate_ok("switch", int(CFG.get("switch_rate_per_min", 60))):
        return fail("切换太频繁，稍等一下再试", 429)
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    cur = str(j.get("g") or "").strip()
    target = str(j.get("to") or "").strip()
    if not target:
        return fail("没指定要切到哪个群")
    if target == cur:
        # 切回当前群：原样把令牌还给他，别白白让他重新登录
        tok = (request.headers.get("X-Token") or "").strip()
        return ok({"g": cur, "u": uid, "t": tok, "same": True})
    if target not in _user_groups(uid):
        return fail("你在那个群还没有账户，先去群里发一次「炒股」", 403)
    token = make_token(target, uid, LOGIN_TOKEN_TTL)
    # 换群 = 明确切到那个群去玩了，私聊认群也跟着切过去，
    # 免得他切了群又被旧群号拉回去。
    _remember_group(uid, target)
    return ok({"g": target, "u": uid, "t": token, "url": _public_url(target, uid, token)})


@app.route("/api/summary")
def api_summary():
    """纯文本账户摘要（给 Dice! 的「我的股票」指令用）。"""
    uid = request.args.get("u", "")
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("你还没开户，群里发「炒股」试试")
    handle = dbmod.get_db(DATA_DIR, gid)
    acct = market.account(handle, CFG, uid)
    if acct is None:
        return fail("你还没开户，群里发「炒股」试试")
    rank = market.ranking(handle, CFG, 50)
    my_rank = 0
    for r in rank:
        if r["qq"] == acct["qq"]:
            my_rank = r["rank"]
            break
    feats = features.check_achievements(handle, CFG, uid)
    # v3：查盘本身就算完成「查一次盘」任务；顺便结算任务 + 检测回归礼包
    daily.mark_quest(handle, uid, "view")
    notices = []
    back = daily.touch_activity(handle, CFG, uid)
    if back:
        notices.append(back)
    notices.extend(daily.settle_quests(handle, CFG, uid))
    return ok({"acct": acct, "rank": my_rank, "total": len(rank),
               "checkin": features.checkin_status(handle, CFG, uid),
               "alerts": features.pending_alerts(handle, uid),
               "new_achievements": feats,
               "quests": daily.quest_state(handle, CFG, uid),
               "rise": daily.rise_state(handle, CFG, uid),
               "market": _market_state(handle),
               "notices": notices})


# ---------------------------------------------------------------- 行情
def _company_rows(handle):
    rows = handle.q("SELECT * FROM companies ORDER BY id")
    out = []
    for r in rows:
        chg = (r["price"] / r["prev_close"] - 1) * 100 if r["prev_close"] else 0.0
        out.append({
            "id": r["id"], "name": r["name"], "nick": r["nick"], "ticker": r["ticker"],
            "industry": r["industry"],
            "qq": r["qq"], "price": market.px(r["price"]), "prev_close": market.px(r["prev_close"]),
            "open": market.px(r["open"]), "high": market.px(r["high"]), "low": market.px(r["low"]),
            "chg": round(chg, 2),
        })
    out.sort(key=lambda x: x["chg"], reverse=True)
    return out


def _news_rows(handle, limit=20):
    rows = handle.q("SELECT n.*, c.name FROM news n LEFT JOIN companies c ON c.id=n.cid "
                    "ORDER BY n.id DESC LIMIT ?", (limit,))
    return [{"id": r["id"], "ts": r["ts"], "ticker": r["ticker"], "name": r["name"] or "",
             "title": r["title"], "body": r["body"] or "", "impact": round(r["impact"] * 100, 2),
             "scope": r["scope"]}
            for r in rows]


def _trade_rows(handle, qq, limit=40):
    rows = handle.q("SELECT t.*, c.name, c.ticker FROM trades t LEFT JOIN companies c ON c.id=t.cid "
                    "WHERE t.qq=? ORDER BY t.id DESC LIMIT ?", (str(qq), limit))
    return [{"id": r["id"], "name": r["name"] or "已退市", "ticker": r["ticker"] or "",
             "side": r["side"], "kind": r["kind"], "qty": r["qty"],
             "price": market.px(r["price"]), "leverage": r["leverage"],
             "fee": round(r["fee"], 2), "pl": round(r["pl"], 2), "ts": r["ts"]}
            for r in rows]


def _ledger_rows(handle, qq, limit=40):
    rows = handle.q("SELECT * FROM ledger WHERE qq=? ORDER BY id DESC LIMIT ?", (str(qq), limit))
    return [{"ts": r["ts"], "delta": round(r["delta"], 2),
             "balance": round(r["balance"], 2), "reason": r["reason"]}
            for r in rows]


@app.route("/api/changelog")
def api_changelog():
    """「最近改动」公告。走群友令牌，不需要 admin。"""
    gid = request.args.get("g", "0")
    uid = request.args.get("u", "")
    token = request.args.get("t", "")
    handle, uid, err = get_ctx(gid, uid, token)
    if err:
        return err
    return ok({"items": CHANGELOG})


@app.route("/api/state")
def api_state():
    gid = request.args.get("g", "0")
    uid = request.args.get("u", "")
    token = request.args.get("t", "")
    handle, uid, err = get_ctx(gid, uid, token)
    if err:
        return err
    if not _rate_ok("read", int(CFG.get("read_limit_per_min", 240)), ident=uid):
        return fail("刷新太频繁，稍等一下再试", 429)
    cid = request.args.get("cid", type=int)
    n = request.args.get("n", 240, type=int)
    me = market.account(handle, CFG, uid)
    data = {
        "now": int(time.time()),
        "companies": _company_rows(handle),
        "news": _news_rows(handle),
        "me": me,
        "rank": market.ranking(handle, CFG, 20),
        "checkin": features.checkin_status(handle, CFG, uid),
        "alerts": features.pending_alerts(handle, uid),
        "notices": market.recent_notices(handle, uid),
        "market": _market_state(handle),
        "orders": orders.list_orders(handle, uid, 30),
        "coupons": coupons.active(handle, uid),
        "vote": fun.vote_status(handle, CFG, uid),
        "lottery": fun.lottery_status(handle, CFG, uid),
        "guess": fun.guess_status(handle, CFG, uid),
        "recovery": recovery.status(handle, CFG, uid),
        "tier": tycoon.tier_status(handle, CFG, uid),
        "job": market.job_status(handle, CFG, uid),
        "onboarding": _onboarding(handle, CFG, uid),
        "quests": daily.quest_state(handle, CFG, uid),
        "rise": daily.rise_state(handle, CFG, uid),
        "static_ver": static_version(),
        "candles": [],
        "cid": cid,
        "trades": _trade_rows(handle, uid),
        "ledger": _ledger_rows(handle, uid),
        "cfg": {
            "fee_rate": CFG["fee_rate"],
            "max_leverage": CFG["max_leverage"],
            "min_leverage": CFG["min_leverage"],
            "initial_cash": CFG["initial_cash"],
            "max_loan_abs": CFG["max_loan_abs"],
            "loan_rate_per_day": CFG["loan_rate_per_day"],
            "candle_seconds": CFG["candle_seconds"],
            "peek_cost": CFG.get("peek_cost", 800),
            "price_limit": CFG.get("price_limit", 0.1),
            "rename_cost": CFG.get("rename_cost", 3000),
            "sabotage_daily_limit": CFG.get("sabotage_daily_limit", 2),
            "cooldown_fee_ladder": CFG.get("cooldown_fee_ladder", []),
        },
    }
    if cid:
        rows = handle.q("SELECT ts,o,h,l,c,v FROM candles WHERE cid=? ORDER BY ts DESC LIMIT ?",
                        (cid, n))
        data["candles"] = [{"ts": r["ts"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]}
                           for r in reversed(rows)]
    return ok(data)


@app.route("/api/candles")
def api_candles():
    gid = request.args.get("g", "0")
    uid = request.args.get("u", "")
    token = request.args.get("t", "")
    handle, uid, err = get_ctx(gid, uid, token)
    if err:
        return err
    if not _rate_ok("read", int(CFG.get("read_limit_per_min", 240)), ident=uid):
        return fail("刷新太频繁，稍等一下再试", 429)
    cid = request.args.get("cid", type=int)
    # 【v4.12.3】n 和 limit 都认（外部脚本 / 手搓链接两种叫法都遇到过）
    n = request.args.get("n", None, type=int)
    if n is None:
        n = request.args.get("limit", 240, type=int)
    n = min(max(n, 30), 1000)
    rows = handle.q("SELECT ts,o,h,l,c,v FROM candles WHERE cid=? ORDER BY ts DESC LIMIT ?", (cid, n))
    return ok([{"ts": r["ts"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]}
               for r in reversed(rows)])


# ---------------------------------------------------------------- 交易
@app.route("/api/order", methods=["POST"])
def api_order():
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = market.place_order(handle, CFG, uid, j.get("cid"), j.get("side"),
                                        j.get("qty"), j.get("leverage", 1))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/close", methods=["POST"])
def api_close():
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = market.close_position(handle, CFG, uid, j.get("pid"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/borrow", methods=["POST"])
def api_borrow():
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = market.borrow(handle, CFG, uid, j.get("amount"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/repay", methods=["POST"])
def api_repay():
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = market.repay(handle, CFG, uid, j.get("amount"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/job", methods=["POST"])
def api_job():
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = market.do_job(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/peek", methods=["POST"])
def api_peek():
    """花钱买内幕消息：提前知道下一条快讯的标的和方向。"""
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = market.peek_news(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


# ---------------------------------------------------------------- v2 玩法
def _admin_ctx(uid, gid, nick=""):
    """给 Dice! 插件用的免令牌入口。

    插件在本机回环调用，走 check_admin 直接放行；群号缺省时按登记表认群
    （私聊时 Dice! 给的 fromGroup 是空串）。返回 (handle, uid, gid, err)。
    """
    uid = str(uid or "").strip()
    if not uid:
        return None, None, None, fail("缺少用户 QQ")
    gid = _resolve_gid(gid, uid)
    if not gid:
        return None, None, None, fail("还没找到你所在的群，先在群里发一次「炒股」")
    handle = dbmod.get_db(DATA_DIR, gid)
    if nick:
        market.ensure_user(handle, uid, nick, CFG["initial_cash"])
    return handle, uid, gid, None


@app.route("/api/g/checkin", methods=["POST"])
def api_g_checkin():
    """群里的「签到」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = features.do_checkin(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/board")
def api_g_board():
    """群里的榜单。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    kind = request.args.get("kind", "rich")
    limit = min(max(request.args.get("n", 10, type=int), 1), 50)
    return ok({"kind": kind, "g": gid, "items": features.boards(handle, CFG, kind, limit)})


@app.route("/api/g/daily")
def api_g_daily():
    """群里的今日复盘。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    return ok(features.daily_report(handle, CFG))


@app.route("/api/g/reminders")
def api_g_reminders():
    """群里的「我的提醒」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    return ok({"items": features.list_reminders(handle, uid),
               "alerts": features.pending_alerts(handle, uid)})


@app.route("/api/g/achievements")
def api_g_achievements():
    """群里的成就墙。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    feats = features.check_achievements(handle, CFG, uid)
    return ok({"items": features.achievements_of(handle, CFG, uid), "new": feats})


@app.route("/api/recovery")
def api_recovery():
    """破产纾困进度（Web 端）。"""
    gid = request.args.get("g", "0")
    uid = request.args.get("u", "")
    token = request.args.get("t", "")
    handle, uid, err = get_ctx(gid, uid, token)
    if err:
        return err
    return ok(recovery.status(handle, CFG, uid))


@app.route("/api/g/recovery")
def api_g_recovery():
    """群端：破产纾困进度。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    return ok({"status": recovery.status(handle, CFG, uid),
               "text": recovery.text(handle, CFG, uid)})


@app.route("/api/g/borrow", methods=["POST"])
def api_g_borrow():
    """群里的「贷款」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = market.borrow(handle, CFG, uid, j.get("amount"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/repay", methods=["POST"])
def api_g_repay():
    """群里的「还款」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = market.repay(handle, CFG, uid, j.get("amount"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/peek", methods=["POST"])
def api_g_peek():
    """群里的「买内幕消息」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = market.peek_news(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/job", methods=["POST"])
def api_g_job():
    """群里的「打工」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = market.do_job(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/quotes")
def api_g_quotes():
    """群里的行情速查（按名称/代码找股票，用来设置提醒）。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    rows = handle.q("SELECT id, name, ticker, industry, price FROM companies ORDER BY id")
    return ok([{"id": r["id"], "name": r["name"], "ticker": r["ticker"],
                "industry": r["industry"], "price": market.px(r["price"])} for r in rows])


@app.route("/api/company/rename", methods=["POST"])
def api_company_rename():
    """给自己的公司改名。"""
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = features.company_rename(handle, CFG, uid, j.get("cid"), j.get("name", ""))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/g/search")
def api_g_search():
    """群里的「搜股」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    kw = request.args.get("q", "")
    limit = min(max(request.args.get("n", 15, type=int), 1), 50)
    items = features.search_companies(handle, CFG, uid, kw, limit)
    return ok({"items": items, "text": features.search_text(handle, CFG, uid, kw, min(limit, 10))})


@app.route("/api/g/sabotage", methods=["GET"])
def api_g_sabotage_plans():
    """群里的「商业战」说明：有哪些手段、我今天还能出几次手。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    limit = int(CFG.get("sabotage_daily_limit", 2) or 2)
    used = features.sabotage_count_today(handle, uid)
    plans = [{"key": p["key"], "name": p["name"], "emoji": p["emoji"],
              "cost": p["cost"], "rate": int(p["rate"] * 100)}
             for p in features.SABOTAGE_PLANS]
    return ok({"plans": plans, "used": used, "limit": limit,
               "text": features.sabotage_plans_text()})


@app.route("/api/g/sabotage", methods=["POST"])
def api_g_sabotage():
    """群里的「商业战」：搞别人的公司。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    cid = j.get("cid")
    if cid in (None, "", 0) and j.get("target"):
        comp = features.find_company(handle, j.get("target"))
        if comp is None:
            return fail(u"没找到「%s」这家公司" % j.get("target"))
        cid = comp["id"]
    plan_key = j.get("plan")
    if plan_key:
        # 群里可以直接说中文手段名（「坑害 隔壁老王 做空狙击」）
        matched = [p for p in features.SABOTAGE_PLANS
                   if p["key"] == plan_key or p["name"] == plan_key]
        if matched:
            plan_key = matched[0]["key"]
    okk, msg, info = features.company_sabotage(handle, CFG, uid, cid, plan_key)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/rename", methods=["POST"])
def api_g_rename():
    """群里的「改名」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    cid = j.get("cid")
    if cid in (None, "", 0):
        row = handle.q1("SELECT id FROM companies WHERE qq=? ORDER BY id LIMIT 1", (uid,))
        if row is None:
            return fail(u"你还没有上市公司，先发「开户」")
        cid = row["id"]
    okk, msg, info = features.company_rename(handle, CFG, uid, cid, j.get("name", ""))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)

@app.route("/api/admin/push", methods=["POST"])
def api_admin_push():
    """运维用：手动往群里推一条消息。

    * text   直接要发的内容
    * kind   "brief" / "close" 时用服务自己生成的早报 / 收盘复盘
    需要 admin 密钥（本机回环调用免密钥）。
    """
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    gid = str(j.get("g", "")).strip()
    kind = str(j.get("kind", "")).strip()
    text = str(j.get("text", "")).strip()
    if not gid:
        return fail("缺少群号 g")
    handle = dbmod.get_db(DATA_DIR, gid)
    if kind in ("brief", "close"):
        text = daily.brief_text(handle, CFG) if kind == "brief" else daily.close_text(handle, CFG)
    if not text:
        return fail("缺少内容 text")
    if not PUSHER.connected:
        return fail("推送通道没连上（OneBot WS 未就绪）")
    if not PUSHER.send_group(gid, text, j.get("at")):
        return fail("推送失败（被 QQ 侧拒绝）")
    return ok({"gid": gid, "kind": kind or "text", "sent": True})


@app.route("/api/g/quests")
def api_g_quests():
    """群里的「今日任务」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    daily.mark_quest(handle, uid, "view")
    msgs = []
    back = daily.touch_activity(handle, CFG, uid)
    if back:
        msgs.append(back)
    msgs.extend(daily.settle_quests(handle, CFG, uid))
    return ok({"items": daily.quest_state(handle, CFG, uid),
               "rise": daily.rise_state(handle, CFG, uid), "msg": "\n".join(msgs),
               # 全勤奖金额由服务端下发：以前 Lua 里写死 800，
               # 平衡数值一改，文案就跟实际对不上了。
               "all_bonus": float(CFG.get("quest_all_bonus", daily.QUEST_ALL_BONUS))})


@app.route("/api/g/news")
def api_g_news():
    """群里的「快讯 / 消息面」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    if uid:
        daily.mark_quest(handle, uid, "news")
        daily.settle_quests(handle, CFG, uid)
    limit = min(max(request.args.get("n", 8, type=int), 1), 20)
    return ok({"items": daily.news_feed(handle, CFG, limit),
               "market": _market_state(handle),
               "major": daily.major_events(handle, CFG, 5)})


@app.route("/api/g/hot")
def api_g_hot():
    """群里的「人气榜」（今日成交额）。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    limit = min(max(request.args.get("n", 10, type=int), 1), 30)
    return ok({"items": daily.hot_turnover(handle, CFG, limit)})


@app.route("/api/g/transfer", methods=["POST"])
def api_g_transfer():
    """群里的「打赏 / 红包」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = daily.transfer(handle, CFG, uid, j.get("target", ""), j.get("amount"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


# ---------------------------------------------------------------- v4.6 财富再分配
def _liq_text(st):
    """破产清盘的条件说明（群命令用）。"""
    lines = ["\U0001F9F9 破产清盘（债务重组）"]
    if st.get("used"):
        lines.append("✅ 你已经清过一次盘了，这条路一辈子只有一次机会。")
        lines.append("现在只能老老实实打工 / 炒股翻身，或者去「逆袭补助」蹭点生活费。")
        return "\n".join(lines)
    lines.append("· 净值 %.2f 元 / 现金 %.2f 元 / 欠款 %.2f 元"
                 % (st["equity"], st["cash"], st["loan"]))
    if st.get("eligible"):
        lines.append("✅ 符合条件，可以申请清盘：")
        lines.append("· 强平持仓 → 现金还债 → 剩下欠款由交易所核销")
        lines.append("· 重开本金约 %.0f 元（上限 %.0f）" % (st["keep"], st["keep_cap"]))
        lines.append("· 交易资格恢复，但有 %d 天重开期：不能贷款、不能请客、杠杆最多 1 倍"
                     % st["lock_days"])
        lines.append("👉 发「破产清算」确认执行（一辈子只有一次，想清楚）")
    else:
        lines.append("❌ 现在还不能清盘：%s" % (st.get("reason") or ""))
        lines.append("（门槛：欠款 ≥ %.0f 元且资不抵债）" % st["min_debt"])
    return "\n".join(lines)


def _cb_text(st):
    """逆袭补助资格说明。"""
    lines = ["\U0001F6CD 逆袭基金"]
    lines.append("· 你的净值 %.2f 元 / 现金 %.2f 元" % (st["equity"], st["cash"]))
    lines.append("· 补助线：净值 < %.0f 且 现金 < %.0f，每日一次"
                 % (st["cap"], st["cash_cap"]))
    lines.append("· 基金余额 %.0f 元（来自群友撒钱的手续费，不是凭空印的）" % st["fund"])
    if st.get("ok"):
        lines.append("👉 你符合条件，今天可领 %.2f 元，发「逆袭补助」领取" % st["amount"])
    else:
        lines.append("· 现在领不了：%s" % (st.get("why") or ""))
    return "\n".join(lines)


@app.route("/api/g/liquidation", methods=["GET", "POST"])
def api_g_liquidation():
    """群里的「破产清盘 / 债务重组」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        st = wealth.liquidation_preview(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        return ok({"st": st, "text": _liq_text(st), "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = wealth.liquidate(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/scatter", methods=["GET", "POST"])
def api_g_scatter():
    """群里的「撒钱 / 请客」：把钱撒给最穷的那批群友。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        lim = wealth.scatter_limit(handle, CFG, uid)
        text = ("\U0001F9E7 撒钱（优先发给最穷的群友）\n"
                "今天还能撒 %.0f 元（上限 %.0f = max(%.0f, 净值×%.1f%%)，已撒 %.0f）\n"
                "用法：撒钱 金额　—— 撒出去的钱有 5%% 手续费进逆袭基金"
                % (lim["left"], lim["cap"], lim["base"], lim["ratio"] * 100, lim["used"]))
        return ok({"limit": lim, "text": text, "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = wealth.scatter(handle, CFG, uid, j.get("amount"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/comeback", methods=["GET", "POST"])
def api_g_comeback():
    """群里的「逆袭基金补助」：给身无分文的垫底玩家发生活费。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        st = wealth.comeback_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        return ok({"st": st, "text": _cb_text(st), "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = wealth.comeback(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/tier", methods=["GET"])
def api_g_tier():
    """群里的「我的档位 / 富豪俱乐部」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    st = tycoon.tier_status(handle, CFG, uid)
    if st is None:
        return fail("你还没有账户，先在群里发一次「炒股」")
    return ok({"st": st, "text": tycoon.tier_text(handle, CFG, uid), "g": gid})


@app.route("/api/g/fund", methods=["GET", "POST"])
def api_g_fund():
    """群里的「财阀基金」：富豪的高风险资金池 + 群友押方向。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        st = whale.fund_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        return ok({"st": st, "text": whale.fund_text(handle, CFG, uid),
                   "board": whale.fund_board(handle, CFG, 10),
                   "bets": whale.bet_board(handle, CFG, 10), "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    side = str(j.get("side", "") or "").strip()
    if not side:
        return fail("用法：押基金 涨　/　押基金 跌")
    okk, msg, info = whale.bet_fund(handle, CFG, uid, side)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/fund", methods=["GET", "POST"])
def api_fund():
    """财阀基金（网页端）。

    群里那版走 /api/g/fund —— Dice! 插件在本机，用 admin key。
    网页这边走令牌（X-Token），跟 /api/vote 一个套路。
    """
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        st = whale.fund_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        return ok({"st": st, "bet": whale.bet_status(handle, CFG, uid),
                   "text": whale.fund_text(handle, CFG, uid),
                   "board": whale.fund_board(handle, CFG, 10),
                   "bets": whale.bet_board(handle, CFG, 10)})
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    side = str(j.get("side", "") or "").strip()
    if not side:
        return fail("用法：押基金 涨 / 押基金 跌")
    okk, msg, info = whale.bet_fund(handle, CFG, uid, side)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info,
               "st": whale.fund_status(handle, CFG, uid),
               "bet": whale.bet_status(handle, CFG, uid),
               "board": whale.fund_board(handle, CFG, 10),
               "bets": whale.bet_board(handle, CFG, 10)}, msg)


@app.route("/api/g/rob", methods=["GET", "POST"])
def api_g_rob():
    """群里的「打土豪」：花小钱去抢有钱人的现金。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        st = tycoon.rob_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        lines = ["🦹 打土豪（花钱抢有钱人的现金）"]
        lines.append("· 你今天还能出手 %d 次（每天上限 %d 次）" % (st["left"], st["limit"]))
        lines.append("· 出手费 = 目标净值×1%（封顶 2000）；抢成功拿他现金的 1%~3%（封顶 5 万）")
        lines.append("· 翻车了出手费全没，对方还能反手拿走 20% 当赔款")
        lines.append("· 目标越有钱越好抢；但他挥霍攒的「声望」会保护他")
        if st["targets"]:
            lines.append("· 现在可以下手的目标：")
            for t in st["targets"]:
                lines.append("   %s（%s）净值 %.0f，成功率约 %d%%%s" % (
                    t["nick"], t["tier"], t["equity"], t["rate"],
                    "，保护期" if t["guarded"] else ""))
        else:
            lines.append("· 现在没有够格的目标（对方净值要 ≥ 3 万）")
        lines.append("👉 发「打土豪 昵称」下手")
        return ok({"st": st, "text": "\n".join(lines), "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = tycoon.rob(handle, CFG, uid, j.get("target"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/luxury", methods=["GET", "POST"])
def api_g_luxury():
    """群里的「挥霍消费」：花钱买声望（抗抢保护）。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        st = tycoon.luxury_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        _okk, text, _info = tycoon.luxury_buy(handle, CFG, uid, "")
        return ok({"st": st, "text": text, "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = tycoon.luxury_buy(handle, CFG, uid, j.get("item", ""))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/dividend", methods=["GET", "POST"])
def api_g_dividend():
    """群里的「全民分红」：会员费攒的钱按活跃度发回群里。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        st = tycoon.dividend_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        lines = ["🪙 全民分红池"]
        lines.append("· 池子余额 %.0f 元（富豪的会员费 + 挥霍消费 + 罚金攒的）" % st["pool"])
        lines.append("· 你今天领过：%s" % ("是" if st["claimed"] else "还没"))
        lines.append("· 今天活跃过：%s" % ("是" if st["active"] else "没有，先去冒个泡"))
        lines.append("· 你今天可以领 %.2f 元（连着 %d 天活跃，拿得更多）"
                     % (st["amount"], st["streak"]))
        lines.append("👉 发「领分红」来拿")
        return ok({"st": st, "text": "\n".join(lines), "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"), j.get("nick", ""))
    if err:
        return err
    okk, msg, info = tycoon.claim_dividend(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/wealth", methods=["GET"])
def api_g_wealth():
    """群里的「财富分布」体检：差距到底在哪。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    gid = _resolve_gid(request.args.get("g"), request.args.get("u", ""))
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    st = tycoon.wealth_summary(handle, CFG)
    if st is None:
        return fail("暂时还没有数据")
    return ok({"st": st, "text": tycoon.wealth_text(handle, CFG), "g": gid})


@app.route("/api/g/reminder", methods=["POST"])
def api_g_reminder():
    """群里的「设提醒」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"))
    if err:
        return err
    cid = j.get("cid")
    name = str(j.get("name") or "").strip()
    if cid is None and name:
        for r in handle.q("SELECT id, name, ticker FROM companies"):
            if name == r["name"] or name == r["ticker"]:
                cid = r["id"]
                break
    if cid is None:
        return fail("没找到这只股票，名字或代码对一下？")
    okk, msg, info = features.add_reminder(handle, CFG, uid, cid,
                                            j.get("kind", "above"), j.get("price"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/checkin", methods=["GET", "POST"])
def api_checkin():
    """每日签到（连签有大奖）。GET 看状态，POST 执行签到。"""
    guard = _trade_guard()
    if guard:
        return guard
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        return ok(features.checkin_status(handle, CFG, uid))
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = features.do_checkin(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/board")
def api_board():
    """榜单：rich 富豪 / profit 盈亏 / hot 涨幅 / limit 涨停。"""
    handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                               request.args.get("t"))
    if err:
        return err
    if not _rate_ok("read", int(CFG.get("read_limit_per_min", 240)), ident=uid):
        return fail("刷新太频繁，稍等一下再试", 429)
    kind = request.args.get("kind", "rich")
    limit = min(max(request.args.get("n", 10, type=int), 1), 50)
    items = features.boards(handle, CFG, kind, limit)
    return ok({"kind": kind, "items": items})


# ---------------------------------------------------------------- 富豪经济（网页端）
@app.route("/api/tycoon/rob", methods=["GET", "POST"])
def api_tycoon_rob():
    """打土豪：花小钱抢有钱人的现金。网页端和群指令走同一套逻辑。"""
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        st = tycoon.rob_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        return ok({"st": st})
    guard = _trade_guard()
    if guard:
        return guard
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = tycoon.rob(handle, CFG, uid, j.get("target"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info})


@app.route("/api/tycoon/luxury", methods=["GET", "POST"])
def api_tycoon_luxury():
    """挥霍消费：花掉一部分身家换声望（抗抢保护）。"""
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        st = tycoon.luxury_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        return ok({"st": st})
    guard = _trade_guard()
    if guard:
        return guard
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = tycoon.luxury_buy(handle, CFG, uid, j.get("item", ""))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info})


@app.route("/api/tycoon/dividend", methods=["GET", "POST"])
def api_tycoon_dividend():
    """全民分红：富豪交的会员费按活跃度发回群里。"""
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        st = tycoon.dividend_status(handle, CFG, uid)
        if st is None:
            return fail("你还没有账户，先在群里发一次「炒股」")
        return ok({"st": st})
    guard = _trade_guard()
    if guard:
        return guard
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = tycoon.claim_dividend(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info})


@app.route("/api/tycoon/wealth", methods=["GET"])
def api_tycoon_wealth():
    """财富分布体检：群里的钱到底怎么分。数据较重，按需再取。"""
    handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                               request.args.get("t"))
    if err:
        return err
    st = tycoon.wealth_summary(handle, CFG)
    if st is None:
        return fail("暂时还没有数据")
    return ok({"st": st})


@app.route("/api/achievements")
def api_achievements():
    handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                               request.args.get("t"))
    if err:
        return err
    feats = features.check_achievements(handle, CFG, uid)
    return ok({"items": features.achievements_of(handle, CFG, uid), "new": feats})


@app.route("/api/reminders", methods=["GET", "POST", "DELETE"])
def api_reminders():
    """价格提醒：设置 / 查询 / 删除。"""
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        return ok({"items": features.list_reminders(handle, uid),
                   "alerts": features.pending_alerts(handle, uid)})
    if request.method == "DELETE":
        j = request.get_json(silent=True) or {}
        guard = _trade_guard()
        if guard:
            return guard
        handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
        if err:
            return err
        if not features.del_reminder(handle, uid, j.get("id")):
            return fail("没有这条提醒")
        return ok({"msg": "已删除提醒"})
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = features.add_reminder(handle, CFG, uid, j.get("cid"),
                                            j.get("kind", "above"), j.get("price"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info}, msg)


@app.route("/api/company/boost", methods=["POST"])
def api_company_boost():
    """给自己的公司花钱做宣传，拉升股价。"""
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = features.company_boost(handle, CFG, uid, j.get("cid"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/company/dividend", methods=["POST"])
def api_company_dividend():
    """给自己的公司派息，给持股的群友发钱。"""
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = features.company_dividend(handle, CFG, uid, j.get("cid"), j.get("amount"))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/company/sabotage", methods=["POST"])
def api_company_sabotage():
    """商业战：花 money 搞别人的公司（网页入口）。"""
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    plan_key = j.get("plan")
    if plan_key:
        matched = [p for p in features.SABOTAGE_PLANS
                   if p["key"] == plan_key or p["name"] == plan_key]
        plan_key = matched[0]["key"] if matched else None
    okk, msg, info = features.company_sabotage(handle, CFG, uid, j.get("cid"), plan_key)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/copy", methods=["POST"])
def api_copy():
    """抄作业：复制群友的仓位。"""
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = features.copy_trade(handle, CFG, uid, j.get("qq"),
                                         j.get("ratio", 0.5))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "me": market.account(handle, CFG, uid)}, msg)


@app.route("/api/daily")
def api_daily():
    """今日复盘（收盘快照）。"""
    if not _rate_ok("read", int(CFG.get("read_limit_per_min", 240))):
        return fail("刷新太频繁，稍等一下再试", 429)
    handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                               request.args.get("t"))
    if err:
        return err
    return ok(features.daily_report(handle, CFG))


@app.route("/api/health")
def api_health():
    """数据自检：确认公司/持仓/成交没有孤儿引用（2026-11-17 事故后加的）。"""
    handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                               request.args.get("t"))
    if err:
        return err
    q = lambda sql: handle.q1(sql)["c"] if handle.q1(sql) else 0
    return ok({
        "group": request.args.get("g"),
        "companies": q("SELECT COUNT(*) c FROM companies"),
        "users": q("SELECT COUNT(*) c FROM users"),
        "candles": q("SELECT COUNT(*) c FROM candles"),
        "positions_open": q("SELECT COUNT(*) c FROM positions WHERE status='open'"),
        "positions_closed": q("SELECT COUNT(*) c FROM positions WHERE status='closed'"),
        "trades": q("SELECT COUNT(*) c FROM trades"),
        "orphan_positions": q("SELECT COUNT(*) c FROM positions p LEFT JOIN companies c "
                              "ON c.id=p.cid WHERE c.id IS NULL"),
        "orphan_trades": q("SELECT COUNT(*) c FROM trades t LEFT JOIN companies c "
                           "ON c.id=t.cid WHERE c.id IS NULL"),
        "orphan_candles": q("SELECT COUNT(*) c FROM candles cd LEFT JOIN companies c "
                            "ON c.id=cd.cid WHERE c.id IS NULL"),
        "orphan_users": q("SELECT COUNT(*) c FROM users u LEFT JOIN companies c "
                          "ON c.qq=u.qq WHERE c.id IS NULL"),
        "checkins": q("SELECT COUNT(*) c FROM checkin"),
    })


# ---------------------------------------------------------------- 本地演示
DEMO_MEMBERS = [
    ("10001", "沃沃沃沃沃"), ("10002", "布莱泽"), ("10003", "小唐"), ("10004", "肆月"),
    ("10005", "鱼鱼"), ("10006", "坏坏的QB"), ("10007", "哥哥"), ("10008", "鸡腿"),
    ("10009", "小助手"), ("10010", "奶龙"), ("10011", "_emby_"), ("10012", "晚安"),
]


@app.route("/api/dev/seed")
def api_dev_seed():
    """本地 MVP 用：造一批演示成员和一个演示用户。"""
    if not CFG.get("dev_mode"):
        return fail("dev_mode 已关闭", 403)
    gid = request.args.get("g", "demo")
    uid = request.args.get("u", "10001")
    handle = dbmod.get_db(DATA_DIR, gid)
    eng = engine_of(handle)
    eng.sync_companies(DEMO_MEMBERS)
    market.sync_users(handle, CFG, DEMO_MEMBERS)
    market.ensure_user(handle, uid, DEMO_MEMBERS[0][1], CFG["initial_cash"])
    for qq, nick in DEMO_MEMBERS:
        market.ensure_user(handle, qq, nick, CFG["initial_cash"])
    return ok({"group": gid, "members": len(DEMO_MEMBERS),
               "companies": len(handle.q("SELECT id FROM companies"))})


# ---------------------------------------------------------------- 条件单 / 趣味玩法
def _resolve_cid(handle, cid, name):
    """按 id 或公司名/代码找股票；找不到返回 None。"""
    try:
        cid = int(cid or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid and handle.q1("SELECT id FROM companies WHERE id=?", (cid,)):
        return cid
    if name:
        row = features.find_company(handle, name)
        if row:
            return int(row["id"])
    return None


@app.route("/api/orders", methods=["GET"])
def api_orders():
    """我的条件单。"""
    handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                               request.args.get("t"))
    if err:
        return err
    return ok({"items": orders.list_orders(handle, uid, 30)})


@app.route("/api/order_cond", methods=["POST"])
def api_order_cond():
    """挂条件单（止损 / 止盈 / 限价）。"""
    j = request.get_json(silent=True) or {}
    guard = _trade_guard()
    if guard:
        return guard
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    cid = _resolve_cid(handle, j.get("cid"), j.get("name"))
    if cid is None:
        return fail("没找到这只股票")
    okk, msg, info = orders.place(handle, CFG, uid, cid, j.get("kind"), j.get("side"),
                                  j.get("trigger"), j.get("qty", 0))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "items": orders.list_orders(handle, uid, 30)}, msg)


@app.route("/api/order_cond_cancel", methods=["POST"])
def api_order_cond_cancel():
    """撤销条件单。"""
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    if not orders.cancel(handle, uid, j.get("id")):
        return fail("这张单子已经不在了")
    return ok({"msg": "已撤销", "items": orders.list_orders(handle, uid, 30)}, "已撤销")


@app.route("/api/vote", methods=["GET", "POST"])
def api_vote():
    """人气投票。GET 看状态和榜单，POST 投一票。"""
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        return ok({"status": fun.vote_status(handle, CFG, uid),
                   "board": fun.vote_board(handle, CFG, 10)})
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    cid = _resolve_cid(handle, j.get("cid"), j.get("name"))
    if cid is None:
        return fail("没找到这只股票")
    okk, msg, info = fun.cast_vote(handle, CFG, uid, cid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "board": fun.vote_board(handle, CFG, 10),
               "status": fun.vote_status(handle, CFG, uid)}, msg)


@app.route("/api/lottery", methods=["GET", "POST"])
def api_lottery():
    """每日抽奖。"""
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        return ok({"status": fun.lottery_status(handle, CFG, uid)})
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    okk, msg, info = fun.draw_lottery(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info,
               "status": fun.lottery_status(handle, CFG, uid)}, msg)


@app.route("/api/guess", methods=["GET", "POST"])
def api_guess():
    """涨停竞猜。"""
    if request.method == "GET":
        handle, uid, err = get_ctx(request.args.get("g"), request.args.get("u"),
                                   request.args.get("t"))
        if err:
            return err
        return ok({"status": fun.guess_status(handle, CFG, uid)})
    j = request.get_json(silent=True) or {}
    handle, uid, err = get_ctx(j.get("g"), j.get("u"), j.get("t"))
    if err:
        return err
    cid = _resolve_cid(handle, j.get("cid"), j.get("name"))
    if cid is None:
        return fail("没找到这只股票")
    okk, msg, info = fun.make_guess(handle, CFG, uid, cid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info,
               "status": fun.guess_status(handle, CFG, uid)}, msg)


@app.route("/api/g/orders")
def api_g_orders():
    """群里的「我的条件单」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    uid = request.args.get("u", "")
    gid = _resolve_gid(request.args.get("g"), uid)
    if not gid:
        return fail("还没找到你所在的群")
    handle = dbmod.get_db(DATA_DIR, gid)
    return ok({"items": orders.list_orders(handle, uid, 30), "g": gid})


@app.route("/api/g/order", methods=["POST"])
def api_g_order():
    """群里的「挂条件单」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"))
    if err:
        return err
    cid = _resolve_cid(handle, j.get("cid"), j.get("name"))
    if cid is None:
        return fail("没找到这只股票，名字或代码对一下？")
    okk, msg, info = orders.place(handle, CFG, uid, cid, j.get("kind"), j.get("side"),
                                  j.get("trigger"), j.get("qty", 0))
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/order_cancel", methods=["POST"])
def api_g_order_cancel():
    """群里的「撤条件单」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"))
    if err:
        return err
    if not orders.cancel(handle, uid, j.get("id")):
        return fail("这张单子已经不在了")
    return ok({"msg": "已撤销", "g": gid}, "已撤销")


@app.route("/api/g/vote", methods=["GET", "POST"])
def api_g_vote():
    """群里的「投票 / 投票榜」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        return ok({"status": fun.vote_status(handle, CFG, uid),
                   "board": fun.vote_board(handle, CFG, 10), "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"))
    if err:
        return err
    cid = _resolve_cid(handle, j.get("cid"), j.get("name"))
    if cid is None:
        return fail("没找到这只股票，名字或代码对一下？")
    okk, msg, info = fun.cast_vote(handle, CFG, uid, cid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/lottery", methods=["GET", "POST"])
def api_g_lottery():
    """群里的「每日抽奖」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        return ok({"status": fun.lottery_status(handle, CFG, uid), "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"))
    if err:
        return err
    okk, msg, info = fun.draw_lottery(handle, CFG, uid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)


@app.route("/api/g/guess", methods=["GET", "POST"])
def api_g_guess():
    """群里的「涨停竞猜」。"""
    if not check_admin(request.args.get("admin", "")):
        return fail("非法请求", 403)
    if request.method == "GET":
        uid = request.args.get("u", "")
        gid = _resolve_gid(request.args.get("g"), uid)
        if not gid:
            return fail("还没找到你所在的群")
        handle = dbmod.get_db(DATA_DIR, gid)
        return ok({"status": fun.guess_status(handle, CFG, uid), "g": gid})
    j = request.get_json(silent=True) or {}
    handle, uid, gid, err = _admin_ctx(j.get("u"), j.get("g"))
    if err:
        return err
    cid = _resolve_cid(handle, j.get("cid"), j.get("name"))
    if cid is None:
        return fail("没找到这只股票，名字或代码对一下？")
    okk, msg, info = fun.make_guess(handle, CFG, uid, cid)
    if not okk:
        return fail(msg)
    return ok({"msg": msg, "info": info, "g": gid}, msg)

# ---------------------------------------------------------------- 黑天鹅播报
def _bomb_push_text(eng, row):
    """把一条黑天鹅快讯拼成能直接发到群里的播报。"""
    scope = row["scope"]
    impact = float(row["impact"] or 0)
    arrow = "\U0001F4C9" if impact < 0 else "\U0001F4C8"
    if scope == "market":
        head = "\U0001F6A8\U0001F6A8\U0001F6A8 紧急快讯：黑天鹅 \U0001F6A8\U0001F6A8\U0001F6A8"
    elif scope == "industry":
        head = "\u26A1 行业异动 \u26A1"
    else:
        head = "\U0001F4A3 个股爆雷 \U0001F4A3"
    lines = [head, row["title"]]
    if scope == "company":
        lines.append("%s 冲击 %+.1f%%" % (arrow, impact * 100))
    elif scope == "industry":
        lines.append("%s 板块 %+.1f%%" % (arrow, impact * 100))
    else:
        lines.append("%s 大盘 %+.1f%%" % (arrow, impact * 100))
    try:
        until, reason = eng.circuit_state()
        mood, regime, fear, fl = eng.market_mood()
    except Exception:
        until, reason, regime, fear, fl = 0, "", "", 50.0, "\u5E73\u7A33"
    if until and scope == "market":
        mins = max(1, int((int(until) - time.time()) // 60) + 1)
        lines.append("\u26D4 全市场临时休市（%s），约 %d 分钟后恢复开盘" % (reason or "\u5E02\u573A\u5F02\u52A8", mins))
    if regime:
        lines.append("\U0001F3AD 情绪周期：%s" % regime)
    lines.append("\U0001F321 当前恐慌指数 %d（%s）" % (int(round(fear)), fl))
    body = (row["body"] or "").strip()
    if body:
        lines.append(body[:120])
    lines.append("\U0001F449 发「快讯」看消息面 ·「我的股票」看持仓")
    return "\n".join(lines)


def _maybe_push_bombs(gid, handle):
    """黑天鹅一发生就往群里喊一嗓子（每条只推一次）。

    v4 之前黑天鹅只躺在网页快讯里，群里完全不知道发生了什么，
    「动荡整个市场」就只是后台的一个数字。现在直接播报 + 熔断提示，
    群友第一时间就能去抄底或者割肉，活跃度才拉得起来。
    """
    if not CFG.get("bomb_push_enable", True):
        return
    try:
        users = handle.q1("SELECT COUNT(*) c FROM users")
        if not users or users["c"] < int(CFG.get("bomb_push_min_users", 10)):
            return
        cursor = int(handle.get_meta("bomb_push_cursor", "0") or 0)
        # 只看最近 6 小时：服务重启后能把宕机期间漏掉的黑天鹅补播一次，
        # 但不会把陈年旧闻翻出来刷屏。
        rows = handle.q("SELECT id, ts, scope, title, body, impact FROM news "
                         "WHERE id > ? AND title LIKE ? AND ts >= ? ORDER BY id",
                         (cursor, newsdata.BOMB_TAG + "%", int(time.time()) - 3600 * 6))
        if not rows:
            # 没有黑天鹅就把游标推到最后一条快讯，避免每次 tick 全表扫
            top = handle.q1("SELECT MAX(id) m FROM news")
            if top and int(top["m"] or 0) > cursor:
                handle.set_meta("bomb_push_cursor", str(int(top["m"])))
            return
        eng = engine_of(handle)
        for row in rows:
            ref = "bomb:%s" % row["id"]
            at = "all" if (row["scope"] == "market" and CFG.get("bomb_push_at_all", True)) else None
            text = _bomb_push_text(eng, row)
            if not _push_once(handle, gid, "bomb", ref, text, at_qq=at) and at:
                # 有些 OneBot 实现不认 at=all，整条消息会被拒；退化成普通消息再试一次
                _push_once(handle, gid, "bomb", ref, text)
        handle.set_meta("bomb_push_cursor", str(int(rows[-1]["id"])))
    except Exception as exc:
        print("[stockmarket] bomb push error:", exc, flush=True)


# ---------------------------------------------------------------- 推送
_stop = threading.Event()
_ALERT_BUDGET = {}     # 每群 10 分钟内最多推 3 条提醒，防止刷屏


def _push_once(handle, gid, kind, ref, text, at_qq=None):
    """同一条推送只发一次；发成功了才记账，失败了下次还能补发。"""
    if handle.q1("SELECT id FROM push_log WHERE kind=? AND ref=?", (kind, str(ref))):
        return False
    if not PUSHER.send_group(gid, text, at_qq):
        return False
    handle.ex("INSERT INTO push_log(qq,gid,kind,ref,ts) VALUES(?,?,?,?,?)",
              ("", str(gid), kind, str(ref), int(time.time())))
    return True


def _push_alert(gid, handle, a):
    """价格提醒到点了，群里 @ 一下当事人。"""
    now = time.time()
    used = _ALERT_BUDGET.get(gid, [])
    used = [t for t in used if now - t < 600]
    if len(used) >= 3:
        _ALERT_BUDGET[gid] = used
        return False
    used.append(now)
    _ALERT_BUDGET[gid] = used
    text = "🔔 %s %s %.2f 了（现价 %.2f）\n👉 发「我的提醒」看详情" % (
        a["name"], "涨到" if a["kind"] == "above" else "跌到", a["price"], a["now"])
    return _push_once(handle, gid, "alert", "alert:%s" % a.get("id", ""), text, at_qq=a["qq"])


_ORDER_BUDGET = {}    # 每群 10 分钟内最多推 5 条条件单播报


def _push_order_event(gid, handle, ev):
    """条件单触发：群里 @ 当事人播报。"""
    now = time.time()
    used = [t for t in _ORDER_BUDGET.get(gid, []) if now - t < 600]
    if len(used) >= 5:
        _ORDER_BUDGET[gid] = used
        return False
    used.append(now)
    _ORDER_BUDGET[gid] = used
    text = "⚡ %s" % (ev.get("msg") or "条件单触发")
    return _push_once(handle, gid, "order", "order:%s" % ev.get("id", ""), text,
                      at_qq=ev.get("qq"))


def _maybe_push_daily():
    """到点了就把早报 / 收盘复盘推到群里。"""
    now = time.localtime()
    hh, mm = now.tm_hour, now.tm_min
    groups = CFG.get("push_groups", []) or []
    if not groups:
        return
    for gid in groups:
        try:
            handle = dbmod.get_db(DATA_DIR, gid)
            if not handle.q1("SELECT id FROM companies LIMIT 1"):
                continue
            users = handle.q1("SELECT COUNT(*) c FROM users")
            if not users or users["c"] < int(CFG.get("push_min_users", 20)):
                continue
            bh = int(CFG.get("push_brief_hour", 9))
            bm = int(CFG.get("push_brief_minute", 5))
            ch = int(CFG.get("push_close_hour", 15))
            cm = int(CFG.get("push_close_minute", 5))
            day = features.today()
            if hh == bh and mm >= bm:
                _push_once(handle, gid, "brief", "brief:%s" % day, daily.brief_text(handle, CFG))
            if hh == ch and mm >= cm:
                _push_once(handle, gid, "close", "close:%s" % day, daily.close_text(handle, CFG))
        except Exception as exc:
            print("[stockmarket] daily push error:", exc, flush=True)


def push_loop():
    while not _stop.is_set():
        try:
            _maybe_push_daily()
        except Exception as exc:
            print("[stockmarket] push loop error:", exc, flush=True)
        _stop.wait(60)


# ---------------------------------------------------------------- 行情调度
_stop = threading.Event()

_LIQ_PUSH = {}
_LIQ_PUSH_LOCK = threading.Lock()
_LIQ_PUSH_GAP = 90          # 同一个人的强平提醒最短间隔（秒），防刷屏


def _push_liq_notice(qq, text):
    """强平 / 破产提醒：QQ 私信推给本人。

    【为什么要单独推】以前 market.check_liquidation 的返回值在调度器里被直接丢掉，
    玩家既收不到私信、网页上也没有提示，仓位列表凭空清空 —— 群友原话
    「被强平后没持有，说我仓位太重」。这里补上「系统主动告诉他」这一步。
    """
    if not text:
        return False
    now_ts = int(time.time())
    with _LIQ_PUSH_LOCK:
        last = int(_LIQ_PUSH.get(str(qq), 0) or 0)
        if now_ts - last < _LIQ_PUSH_GAP:
            return False
        _LIQ_PUSH[str(qq)] = now_ts
    try:
        return PUSHER.send_private(qq, "【模拟炒股】" + text)
    except Exception:
        return False


def scheduler():
    while not _stop.is_set():
        for gid in dbmod.known_groups(DATA_DIR):
            # 【每群独立 try】原来整个 for 循环只包了一层 try：
            # 某个群抛异常（库损坏、行情文件被占用、某个字段 NULL），
            # 循环直接中断，排在它后面的群这一轮根本不会被 tick ——
            # 表现就是「有的群行情不动」。现在把 try 放进循环里，
            # 一个群坏掉不影响其它群继续跑。
            try:
                handle = dbmod.get_db(DATA_DIR, gid)
                if not handle.q1("SELECT id FROM companies LIMIT 1"):
                    continue
                engine_of(handle).tick()
                # 黑天鹅要立刻播报到群里：熔断、恐慌指数一出来就得让人知道
                _maybe_push_bombs(gid, handle)
                # 行情推进后扫一遍价格提醒，触发的标记待领取，并往群里喊一声
                for a in features.scan_reminders(handle, CFG):
                    _push_alert(gid, handle, a)
                # 条件单：到价自动平仓 / 成交，触发时在群里喊当事人
                for ev in orders.scan(handle, CFG):
                    _push_order_event(gid, handle, ev)
                # 跨天了就自动生成一份收盘快照，这样「今日复盘」随时有数据
                _today = features.today()
                if handle.get_meta("daily_day", "") != _today:
                    # 昨天的涨停竞猜在这里结算（昨天真正涨停的票存在 limit_hits）
                    guess_text = fun.settle_guess(handle, CFG)
                    if guess_text:
                        PUSHER.send_group(gid, guess_text)
                    # 复盘存的是「刚刚收完盘的那一天」，不是 _today：
                    # _today 是刚跨进来的新一天，引擎这一 tick 刚把
                    # price / prev_close 刷成同一个值（开盘不跳空了），
                    # 用它算出来涨跌恒为 0、涨停恒为 0 —— 复盘就成了废数据。
                    features.daily_report(handle, CFG, features.yesterday())
                    handle.set_meta("daily_day", _today)
                    # 【v4.7】跨天结算：收会员费 + 声望衰减 + 强制清盘。
                    # 放在 daily_day 落库之后，异常也不会影响行情推进。
                    try:
                        _fc, _ft, _mc, _mt = tycoon.settle_day(handle, CFG)
                        if _fc:
                            PUSHER.send_group(
                                gid,
                                "👑 会员费已自动扣除：%d 位大户共交 %.0f 元，"
                                "全部进了全民分红池。" % (_fc, _ft))
                        if _mc:
                            PUSHER.send_group(
                                gid,
                                "🏛 持仓税：%d 位大户的仓位共交 %.0f 元，"
                                "进了全民分红池（钱锁在仓位里复利也要交税）。" % (_mc, _mt))
                        for _qq, _m in wealth.auto_liquidate(handle, CFG):
                            PUSHER.send_group(gid, "⚖️ 交易所强制清盘\n" + _m)
                        # 【v4.13】财阀基金：先结算昨天（分钱给押对的群友、
                        # 亏损从出资人兜里扣），再收今天的新出资。
                        _fund_msg = whale.settle_fund(handle, CFG)
                        if _fund_msg:
                            PUSHER.send_group(gid, _fund_msg)
                        whale.collect_fund(handle, CFG)
                    except Exception as exc:
                        print("[stockmarket] settle_day error:", exc, flush=True)
                # 【v4.11 性能】只扫「有可能被强平 / 破产 / 计提利息」的人：
                #   有持仓的 + 有欠款的 + 现金已经为负的。
                # 以前是无脑 SELECT qq FROM users 全表扫，每个用户每 tick
                # 都要跑一遍 check_liquidation（每人 3~5 条 SQL），
                # 八个群几百号人加起来每秒上千次查询 —— 群级锁被它占满，
                # 网页请求排在后面，页面就一直转圈 / 偶尔空响应。
                # 漏掉谁：没有持仓、没有欠款、现金还是正数的用户，
                # 净值 = 现金 + 0 + 0 - 0 = 现金，不可能变负，
                # 既不会被强平也不会破产，扫了也是白扫。
                for u in handle.q(
                        "SELECT qq FROM users WHERE loan > 0 OR cash < 0 "
                        "UNION SELECT DISTINCT qq FROM positions WHERE status='open'"):
                    qq = u["qq"]
                    market.accrue_interest(handle, CFG, qq)
                    _liq_msg = market.check_liquidation(handle, CFG, qq)
                    if _liq_msg:
                        _push_liq_notice(qq, _liq_msg)
            except Exception as exc:  # 调度器不能因为单群异常停摆
                print("[stockmarket] scheduler error: group=%s %s" % (gid, exc), flush=True)
        _stop.wait(CFG["tick_seconds"])


def _patch_exclusive_bind():
    """Windows 上独占监听端口：别人抢不走 %d 的流量。

    【踩过的坑】现网曾经同时跑着三份 server.py —— nssm 托管的那份，
    外加两个用 `cmd /c server.py` 手拉起来的野进程。
    Werkzeug / waitress 默认都会给监听套接字设 SO_REUSEADDR，
    而在 Windows 上 SO_REUSEADDR 等于「允许别人来抢同一个端口」：
    野进程跑的是旧代码，却把线上流量全接走了，我们发了新版本却没生效，
    排查了大半天才发现线上根本不是我们部署的那份进程。

    换成 SO_EXCLUSIVEADDRUSE 之后：端口被我们占住，别人再 bind 会直接报
    WSAEADDRINUSE 起不来（而不是偷偷接管流量）——
    「抢端口」这件事从悄无声息变成一眼看见。

    非 Windows 平台保持 waitress 原样，不影响 Linux 部署。
    """
    if os.name != "nt":
        return
    try:
        import socket
        import waitress.wasyncore as _wa
    except Exception:
        return

    def _set_exclusive(self):
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        except OSError:
            pass

    try:
        _wa.dispatcher.set_reuse_addr = _set_exclusive
    except Exception:
        pass


def _serve():
    """起 HTTP 服务。

    【为什么不用 Flask 自带的开发服务器】
    `app.run()` 起的是 Werkzeug 开发服务器 —— 它每次启动都会喊
    "WARNING: This is a development server. Do not use it in a production deployment."，
    而且**每个 HTTP 请求都往 stderr 写一行访问日志**。
    现网 8 个群几百人、网页每秒轮询 4 个接口，18 天就把 DicePyStock.err
    撑到 41 MB：真正的 Traceback 埋在几十万行访问日志里，磁盘也迟早被吃满。

    换成 waitress（纯 Python 的 WSGI 服务器，Windows 上最省事）：
      · 并发走固定线程池，比「一个连接一个线程」的开发服务器稳
      · **不写逐请求访问日志** —— 那 41 MB 噪音直接消失
      · 没有那句 "development server" 警告
      · 真正要盯的只剩 waitress 自己打的 WARNING/ERROR（accept 异常、线程超时）

    SQLite 那边不用担心：db.GroupDB 用 `check_same_thread=False` + 群级 RLock，
    跟开发服务器「threaded=True」时一样是「一把锁一个群」，语义没变。

    waitress 没装 / 主人想回退时，自动退回 app.run —— 行为跟以前一模一样。
    回退开关：环境变量 `STOCKMARKET_SERVE=flask`（改完重启即可）。
    """
    host = "0.0.0.0"
    port = int(CFG.get("port", 9030) or 9030)
    mode = (os.environ.get("STOCKMARKET_SERVE") or "auto").strip().lower()
    if mode not in ("flask", "waitress"):
        mode = "auto"
    if mode != "flask":
        try:
            from waitress import serve
        except ImportError:
            if mode == "waitress":
                print("[stockmarket] waitress 没装，回退到 flask 开发服务器", flush=True)
        else:
            threads = int(CFG.get("serve_threads", 16) or 16)
            threads = max(2, min(64, threads))
            _patch_exclusive_bind()
            print("[stockmarket] serving with waitress on %s:%s (threads=%d)" % (
                host, port, threads), flush=True)
            try:
                serve(app, host=host, port=port, threads=threads,
                      connection_limit=1000, channel_timeout=120,
                      cleanup_interval=30, ident="blaidd-stockmarket")
            except OSError as exc:
                print("[stockmarket] 端口 %s 起不来：%s" % (port, exc), flush=True)
                print("[stockmarket] 多半有个野进程占着 %s（之前有人用 `cmd /c server.py` 手拉过一份，"
                      "跑的是旧代码）。把它杀掉再让 nssm 重启本服务即可。" % port, flush=True)
                raise
            return
    print("[stockmarket] serving with flask dev server on %s:%s" % (host, port), flush=True)
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    PUSHER.start()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=push_loop, daemon=True).start()
    # 群名后台预取（网页端「换群」下拉要用）
    threading.Thread(target=_group_name_loop, daemon=True).start()
    # 【v4.12.3】缺名字的群即时补拉（新群不用等半小时的全量刷新）
    threading.Thread(target=_group_name_worker, daemon=True).start()
    print("[stockmarket] listening on :%s  data=%s" % (CFG["port"], DATA_DIR), flush=True)
    _serve()

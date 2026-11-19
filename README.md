# 布莱泽模拟炒股 · Dice! 路线

Dice! 机器人里的「布莱泽模拟炒股」功能源码。**这条路和 AstrBot 那条线是两套东西**，
服务进程是 `DicePyStock`（NSSM），跑在你自己的 Windows Server 上，端口 `9030`。

> **本仓库是炒股功能的唯一权威源码仓库。**
> 协作信箱 / 架构真相在 [`HinanaiDHuang/blaidd-astrbot`](https://github.com/HinanaiDHuang/blaidd-astrbot)
> 的 `main` 分支 `workspace.md`；那个仓库里的 `codex/stockmarket-src` 分支只是**本仓库的镜像**，
> 改代码请以本仓库为准。

## 当前版本：v4.12.2

> **两个 Codex（家里 / 公司）之间的口径与协作约定在
> [`HinanaiDHuang/blaidd-astrbot`](https://github.com/HinanaiDHuang/blaidd-astrbot) 的
> [`GLOSSARY.md`](https://github.com/HinanaiDHuang/blaidd-astrbot/blob/main/GLOSSARY.md)（指标口径 / 参数基线表）
> 和 [`COLLAB.md`](https://github.com/HinanaiDHuang/blaidd-astrbot/blob/main/COLLAB.md)（并发互斥 / 交接清单）。
> 算「日收益中位」「套利 EV」这类数请按 GLOSSARY 的口径，两边算法必须一样。

| 版本 | 日期 | 做了什么 |
|---|---|---|
| v4.12.2 | 2026-11-19 | 网页端一批小毛病：条件单方向加「自动」（按持仓推断，v4.10 的能力网页端才算真开）；K 线 OHLC 栏默认显示最后一根（MT4/MT5 老样子）+ 桌面端悬停出十字光标；新手引导步骤号不再跳号；320px 小屏顶栏净值不再被截断、品牌名不再折行；个股快讯正文不再抄一遍标题；「怎么玩」补齐短线交易费 / 贷款看净值 / 破产冷静期；修「破产纥困」错别字 |
| v4.12 | 2026-11-19 | 短周期冷却税（持仓越短手续费越高，真实市场对高频短线的额外成本）；锚点周期 30 分钟→5 分钟 + 锚点随机偏移 + 每票每周期随机回归强度 + 抖动种子加服务端盐（堵「等价格跌回锚价」的确定性套利）；`reversion` 0.0012→0.0006 收口；借款口径改成看「净值」不「现金」；修 `mark_reopen()` 死代码 + 纾困期无限刷钱 |
| v4.11 | 2026-11-18 | 强平必私信 + 风险预警（按真正的强平线触发，别用「净值/自有保证金」）；行情事务化；前端空响应容错；读限流按验签后的 uid 分桶（原来按 IP，一个运营商 NAT 后面的群友一起 429） |
| v4.10 | 2026-11-18 | 止损/止盈按持仓方向自动推断，多空两头都有时用参数指定 |
| v4.9.3 | 2026-11-18 | 挥霍消费的每日额度在网页上直接显示（还能挥霍 N/M 次），额度用完按钮置灰 |
| v4.9.2 | 2026-11-18 | 前端版本自检：页面开着不动也会自己发现「服务端更新了」并自动重载；不用清缓存、不用发公告让群友刷新 |
| v4.9.1 | 2026-11-18 | 打工面板按档位实时显示（职级 / 日上限 / 已挣 / 剩余）；中下层在打工面板直接看到「翻身任务」引导；帮助页补翻身任务与新上限规则 |
| v4.9 | 2026-11-18 | 打工日上限按档位拉开（150→1000）；中下层「翻身任务」；挥霍每日额度；打土豪目标脱敏 |
| v4.8 | 2026-11-18 | 网页端大改版（5 tab + 18 功能宫格 + 新手引导 + 档位进度条）；会员费提档（巨富 0.14%→0.20%、财阀 0.18%→0.26%）；强制清盘宽限期 7 天→3 天；网页「最近改动」公告按钮 |
| v4.7 | 2026-11-18 | 富豪经济：净值分层会员费 / 打土豪 / 挥霍消费 / 全民分红池；解冻死钱（破产满 N 天强制清盘） |
| v4.6 | 2026-11-18 | 破产与回头路：破产出局榜 / 散财榜 / 资本碾压 |
| v4.5 | 2026-11-18 | 经济失衡标定（打工不再压过炒股）；修「今日复盘」废数据；修「加了好友 30 分钟才收到链接」 |
| v4.3 | 2026-11-17 | 注册制：发「股票」立刻拿 10 分钟口令，链接不再等半小时 |

## 目录结构（和服务器上的路径一一对应）

```
blaidd-stockmarket/
├── py/stockmarket/          →  C:\DiceDriver.LLOneBot\py\stockmarket\
│   ├── engine.py            行情引擎：tick、涨跌停、漂移控制器、跨日快照
│   ├── market.py            下单 / 撮合 / 账户 / 榜单
│   ├── orders.py            条件单（止损止盈）
│   ├── features.py          每日复盘、打工、签到、商业战、成就
│   ├── daily.py             每日任务
│   ├── fun.py               猜涨跌等小游戏
│   ├── coupons.py           手续费券
│   ├── recovery.py          破产纾困 / 司法重整
│   ├── wealth.py            破产清盘 / 撒钱 / 逆袭基金
│   ├── tiers.py             净值分层表（纯数据，无依赖）
│   ├── tycoon.py            富豪经济：会员费 / 打土豪 / 挥霍 / 分红池
│   ├── newsdata.py          新闻库 + 黑天鹅
│   ├── db.py                SQLite schema（含 daily_px 快照表）
│   ├── server.py            Flask HTTP API（含 CHANGELOG 常量 = 网页公告内容）
│   ├── pusher.py            OneBot 推送（群 / 私聊，带重试）
│   ├── config.json          全部可调参数
│   └── static/              网页（index.html / app.js / style.css）
├── plugin/炒股.lua          →  C:\DiceDriver.LLOneBot\Dice<你的BotQQ>\plugin\
├── tests/                   20 个回归测试（本地跑，不用连现网）
└── deploy/                  部署脚本
```

`secrets.json`（HMAC secret / admin key）**不入仓**，只在服务器上（已加进 `.gitignore`）。

## 跑测试

```powershell
cd tests
python run_tests.py            # 跑全部
python run_tests.py v48        # 只跑名字含 v48 的
```

需要 `flask` + `lupa`（Lua 测试用）。测试全在本地跑，**不连现网、不动生产库**。

## 部署（生产服务器）

1. 把 `py/stockmarket/` 和 `plugin/炒股.lua` 打成 zip（zip 里 Lua 用 ASCII 文件名 `chaogu.lua`，
   服务器端再覆盖回原 GBK 文件名）。
2. 上传到 `C:\AstrBot\deploy\stockmarket.zip`。
3. 服务器上跑 `deploy/deploy_stockmarket.ps1`：

   ```powershell
   .\deploy_stockmarket.ps1 `
     -Zip "C:\AstrBot\deploy\stockmarket.zip" `
     -BotQQ "你的BotQQ" `
     -PublicHost "你的服务器IP:9030" `
     -PushGroups "群号1,群号2" `
     -SkipLua
   ```

   脚本会：编译检查（用服务自己的 python 3.10）→ 全量备份 → 覆盖 → `nssm restart DicePyStock`
   → 打 `/health`；不健康**自动回滚**。

   参数说明：

   | 参数 | 作用 | 不传会怎样 |
   |---|---|---|
   | `-BotQQ` | 注入 `config.json` / Lua 里的 BotQQ 占位符 | 脚本 `exit 4` 直接中止 |
   | `-PublicHost` | 注入 `config.json` / Lua 里的服务器 IP 占位符 | 脚本 `exit 4` 直接中止 |
   | `-PushGroups` | 写入 `config.json` 的 `push_groups`（逗号分隔） | **保持包里的原样**；公开仓里这字段是空的，不传就会把群推送静默关掉 |
   | `-SkipLua` | 本次不碰 `plugin/炒股.lua` | 默认会一起覆盖 Lua（改 Lua 还要另外重启 Dice! 核心） |

   > ⚠️ `.ps1` 必须存成 **UTF-8 with BOM**。Windows PowerShell 5.1 在没有 BOM 时
   > 会按 GBK 解析，中文注释直接语法错，脚本一行都跑不起来。

## 热更新口径

| 改什么 | 要做什么 | 群友感知 |
|---|---|---|
| `py/stockmarket/*.py` | 重启 `DicePyStock` | 无感（服务秒起） |
| `py/stockmarket/static/*` | 重启 `DicePyStock` | **不用清缓存**：页面自动重载（css/js 有 `?v=` 版本号，页面会比对服务端的 `static_ver`） |
| `plugin/炒股.lua` | **重启 Dice! 核心** | 无感（几秒内恢复） |
| `config.json` | 重启 `DicePyStock` | 当天即生效（漂移目标有指纹校验） |

## 关键不变量（改代码前先看）

- **开盘不跳空**。跳空会把价格中心顶到涨跌停带的上半区，涨的行情被削、跌的不被削，
  系统性「少涨」。当日漂移改成**收盘前随机 300 秒窗口内摊推**（`drift_push_seconds`）。
- **涨跌停带以昨收为中心**。所以跨日那一行会把 `price` 和 `prev_close` 刷成同值，
  跨日瞬间必须先做 `daily_px` 快照，否则「今日复盘」涨跌恒为 0（v4.5 修的就是这个）。
- **价格 4 位小数**。每个 tick 的漂移量级必须 > 5e-5，否则被取整吃掉。
- **`market_daily_drift` 是「炒股长期跑赢打工」的旋钮**：
  打工无风险 +0.15%/天（普通股民日上限 150 元 / 10 万本金），市场目标 +1.8%/天。
  改它之后**当天生效**（`market_gap_target` 指纹字段，v4.5 加的）。
- **链接等同账户凭证**。只私信本人，群里只给一次性口令，兜底文案里绝不能出现链接。
- **改前端必须换 `index.html` 里的 `?v=`**。它是前端的单一事实来源：
  服务端 `/api/state` 回的 `static_ver` 就是从 `index.html` 里解析出来的 `?v=`，
  页面拿自己加载时的版本号一比就知道自己落后了，于是自动重载。
  忘了换 `?v=` = 群友永远看不到新 UI（且没有任何报错）。
- **`users.bankrupt_since` 只在进入破产时写一次**，退出破产时清零。
  `bankrupt_ts` 会被「净值回正 → 又跌回负数」的震荡刷新，导致强制清盘倒计时永远从头开始。
  改这块前务必看 `recovery.on_bankrupt` / `sync_bankrupt`。

## 编码提醒（血的教训）

**生成含中文的源码时，不要手写 `\uXXXX` 转义。**
v4.8 那一版就是手写转义打错位，导致网页「最近改动」弹层里的「财阀 / 布莱泽」显示成乱码，
群友直接可见。正确做法：直接写中文，用 UTF-8 无 BOM 落盘；或者用 `\N{...}` / `chr()` 由代码生成。

## 相关仓库

| 仓库 | 内容 |
|---|---|
| `HinanaiDHuang/blaidd-astrbot` | 主仓：协作信箱 `workspace.md`、AstrBot/logger 那一套部署脚本 |
| `HinanaiDHuang/blaidd-stockmarket` | **本仓库**：炒股功能源码（唯一权威） |

改动记得在主仓的 `workspace.md` 信箱段留一条记录（带时间 / 程序 / 模型 / 状态）。

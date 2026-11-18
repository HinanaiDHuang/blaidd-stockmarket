local json_ok, json = pcall(require, "json")
if not json_ok then
    local dk_ok, dk = pcall(require, "dkjson")
    if dk_ok then json = dk end
end

local function decode_safe(d)
    if not json then return nil end
    local ok, r = pcall(json.decode, d)
    return ok and r or nil
end

msg_order = {}

local API = "http://127.0.0.1:9030"
local BOT_QQ = "YOUR_BOT_QQ"
-- 站点地址以服务端返回的为准，这个只是兜底（服务端不可用时也能给个能点的地址）
local SITE_FALLBACK = "http://YOUR_SERVER_IP:9030/"
local CONFIG_PATHS = {
    "C:/DiceDriver.LLOneBot/py/stockmarket/secrets.json",
    "C:/DiceDriver.LLOneBot/py/stockmarket/config.json",
}

local function get_admin_key()
    for _, p in ipairs(CONFIG_PATHS) do
        local f = io.open(p, "r")
        if f then
            local c = f:read("*a")
            f:close()
            local j = decode_safe(c)
            if j and j.admin_key and j.admin_key ~= "" then
                return j.admin_key
            end
        end
    end
    return ""
end

local function json_escape(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\\"):gsub("\"", "\\\"")
    s = s:gsub("\r", "\\r"):gsub("\n", "\\n"):gsub("\t", "\\t")
    s = s:gsub("[\1-\31]", function(c)
        return string.format("\\u%04x", string.byte(c))
    end)
    return s
end

local function tmpdir()
    local d = os.getenv("TEMP")
    if not d or d == "" then d = "C:/Windows/Temp" end
    return d
end

local function curl_post(url, payload_path, resp_path, extra_header)
    local head = 'curl.exe -sS --max-time 30 -X POST -H "Content-Type: application/json"'
    if extra_header and extra_header ~= "" then
        head = head .. string.format(' -H "%s"', extra_header)
    end
    local cmd = string.format(
        '%s --data-binary "@%s" %s -o "%s"',
        head, payload_path:gsub("/", "\\"), url, resp_path:gsub("/", "\\"))
    os.execute(cmd)
end

local function curl_get(url, resp_path, extra_header)
    local head = 'curl.exe -sS --max-time 20'
    if extra_header and extra_header ~= "" then
        head = head .. string.format(' -H "%s"', extra_header)
    end
    local cmd = string.format(
        '%s "%s" -o "%s"',
        head, url, resp_path:gsub("/", "\\"))
    os.execute(cmd)
end

local function read_all(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local c = f:read("*a")
    f:close()
    return c
end

-- ------------------------------------------------------------ v2 玩法辅助
-- 群里直接玩的时候没有交易页令牌，走服务端免令牌入口（本机回环调用）。
local function json_encode(obj)
    local parts = {}
    for k, v in pairs(obj) do
        if type(v) == "number" then
            parts[#parts + 1] = string.format('"%s":%s', k, tostring(v))
        elseif type(v) == "boolean" then
            parts[#parts + 1] = string.format('"%s":%s', k, tostring(v))
        elseif type(v) == "string" then
            parts[#parts + 1] = string.format('"%s":"%s"', k, json_escape(v))
        end
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

local function api_post(path, obj)
    local td = tmpdir()
    local payload_path = td .. "\\stock_payload_" .. os.time() .. "_" .. math.random(1000, 9999) .. ".json"
    local resp_path = td .. "\\stock_resp_" .. os.time() .. "_" .. math.random(1000, 9999) .. ".json"
    local f = io.open(payload_path, "w")
    if not f then return nil end
    f:write(json_encode(obj))
    f:close()
    curl_post(API .. path, payload_path, resp_path, "X-Admin: " .. get_admin_key())
    local resp = read_all(resp_path)
    os.remove(payload_path)
    os.remove(resp_path)
    if not resp or resp == "" then return nil end
    return decode_safe(resp)
end

local function api_get(path)
    local td = tmpdir()
    local resp_path = td .. "\\stock_resp_" .. os.time() .. "_" .. math.random(1000, 9999) .. ".json"
    curl_get(API .. path, resp_path, "X-Admin: " .. get_admin_key())
    local resp = read_all(resp_path)
    os.remove(resp_path)
    if not resp or resp == "" then return nil end
    return decode_safe(resp)
end

-- 群号：群里就是群号，私聊是空串（服务端按登记表认群）
local function cur_gid(msg)
    local group = msg.fromGroup
    if group == nil then group = "" end
    return tostring(group)
end

local function cur_qq_nick(msg)
    local qq = tostring(msg.fromQQ)
    local nick = getUserConf(qq, "nick") or ""
    return qq, nick
end

-- 群成员 -> 上市公司（用 QQ 昵称，不是群昵称）
local function build_members(group, exclude_qq)
    local raw = getGroupConf(group, "members") or {}
    local parts = {}
    for _, qq in ipairs(raw) do
        qq = tostring(qq)
        if qq ~= "0" and qq ~= tostring(exclude_qq or "") then
            local nick = getUserConf(qq, "nick") or ""
            table.insert(parts, string.format('{"qq":"%s","nick":"%s"}', qq, json_escape(nick)))
        end
    end
    return "[" .. table.concat(parts, ",") .. "]"
end

-- 调 /api/session：登记这个 QQ，顺带把全群成员登记进去（群里任意一人发过就算）。
-- 返回服务端认回来的群号（私聊时服务端按登记表反查）。
local function call_session(gid, qq, nick, members_json)
    local td = tmpdir()
    local payload_path = td .. "\\stock_payload_" .. os.time() .. "_" .. math.random(1000, 9999) .. ".json"
    local resp_path = td .. "\\stock_resp_" .. os.time() .. "_" .. math.random(1000, 9999) .. ".json"
    local payload = string.format(
        '{"g":"%s","u":"%s","nick":"%s","members":%s}',
        tostring(gid or ""), tostring(qq), json_escape(nick), members_json)
    local f = io.open(payload_path, "w")
    if not f then return nil, "写临时文件失败" end
    f:write(payload)
    f:close()
    curl_post(API .. "/api/session", payload_path, resp_path, "X-Admin: " .. get_admin_key())
    local resp = read_all(resp_path)
    os.remove(payload_path)
    os.remove(resp_path)
    if not resp or resp == "" then return nil, "没响应" end
    local j = decode_safe(resp)
    if not j then return nil, "返回解析失败" end
    return j
end

-- 铸一个一次性口令：10 分钟有效、用掉即焚、只对这个 QQ 有效。
-- 这是绕开「群临时会话送不到私信」的正解——链接那条路对新手是死的，
-- 口令这条路一定能走通（用户自己在网页上输 QQ 号 + 口令换令牌）。
local function mint_passcode(gid, qq, force)
    local td = tmpdir()
    local payload_path = td .. "\\stock_payload_" .. os.time() .. "_" .. math.random(1000, 9999) .. ".json"
    local resp_path = td .. "\\stock_resp_" .. os.time() .. "_" .. math.random(1000, 9999) .. ".json"
    local payload = string.format(
        '{"g":"%s","u":"%s","force":%s}',
        tostring(gid or ""), tostring(qq), force and "true" or "false")
    local f = io.open(payload_path, "w")
    if not f then return nil end
    f:write(payload)
    f:close()
    curl_post(API .. "/api/g/passcode", payload_path, resp_path, "X-Admin: " .. get_admin_key())
    local resp = read_all(resp_path)
    os.remove(payload_path)
    os.remove(resp_path)
    if not resp or resp == "" then return nil end
    local j = decode_safe(resp)
    if not j or j.code ~= 200 or not j.data then return nil end
    return j.data
end

function chaogu(msg)
    local group = msg.fromGroup
    -- 私聊时 Dice! 给的是空串而不是 nil，两种都得按私聊处理
    local is_private = (group == nil or group == "")
    -- 链接等同账户凭证，默认只私信本人；「炒股 公开」才在群里发链接
    local _, rest = string.match(msg.fromMsg, "^(%S+)%s+(.+)$")
    local public = false
    if rest then
        rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
        public = (rest == "公开" or rest == "public")
    end
    local qq = tostring(msg.fromQQ)
    local nick = getUserConf(qq, "nick") or "{nick}"
    -- 私聊拿不到群成员名单，交服务端按登记表认群
    local members = is_private and "[]" or build_members(group, qq)

    local j, jerr = call_session(is_private and "" or tostring(group), qq, nick, members)
    if not j then
        return "炒股服务" .. (jerr or "没响应") .. "，请联系管理员检查 9030 端口"
    end
    if j.code ~= 200 or not j.data or not j.data.url then
        local emsg = j.message or "未知错误"
        if string.find(emsg, "还没找到你所在的群", 1, true) then
            return "你还没在群里开过户。\n" ..
                "先在群里发一次「炒股」就行（群里任意一位小伙伴发过也算，我按群成员名单登记），\n" ..
                "之后你私聊我发「炒股」，我立刻把交易页发你，不用等。"
        end
        return "炒股服务出错：" .. emsg
    end
    -- 服务端认回来的群号（私聊时这里才是对的）
    local gid = tostring(j.data.g or group or "")
    if is_private then
        -- 私聊里直接回链接。
        -- 【2026-11-18】补一条兜底：万一这条私聊没送达
        -- （QQ 对新好友的放行有延迟，群友反馈「加了布莱泽后不能马上收到链接，
        -- 要等 30 分钟再发消息才能收到」），还能走「在群里发【炒股】拿一次性口令」
        -- 这条路 —— 口令走群消息，一定送得到。
        -- 注意兜底文案里不能带上链接本身（链接等同账户凭证，不能漏进群里）。
        -- 另外服务端（server.py::_push_link_private）会独立再私信一次链接，
        -- 走 OneBot 的 send_private_msg，带 retcode 和重试。
        return string.format(
            "📈 布莱泽模拟炒股 · 群 %s\n" ..
            "初始资金 10 万，可以做多、做空、加杠杆，还能贷款和去群里打零工。\n" ..
            "打开下面链接交易（7 天内有效，链接等同账户凭证，别转发给别人）：\n%s\n" ..
            "（已经记住你在群 %s 的账户，之后私聊我发「炒股」随时能再拿）\n" ..
            "（没收到？去群里发一次「炒股」，我给你一个一次性口令，" ..
            "网页上填「QQ号 + 口令」也能进）",
            gid, j.data.url, gid)
    end
    if not public then
        -- 群里：当场给一个一次性口令。
        -- 群上下文里的私信在 QQ 侧走「群临时会话」，新加好友的那一次大概率送不到，
        -- 所以「私信链接」这条路对新手是死的；口令这条路一定能走通。
        local pc = mint_passcode(gid, qq, false)
        local site = (pc and pc.site) or SITE_FALLBACK
        if pc and pc.permanent then
            return string.format(
                "📈 布莱泽模拟炒股 · 群 %s\n" ..
                "交易页：%s\n" ..
                "你已经设过固定口令，在网页上填 QQ 号 + 你自己的口令就能进（30 天免登录）。\n" ..
                "口令忘了？在群里发「重置口令」，我给你换个临时的。",
                gid, site)
        end
        if pc and pc.passcode then
            -- 【2026-11-18】直链私信改由服务端发（server.py::_push_link_private）：
            -- Dice! 的 sendMsg 失败不抛异常也不返回 false，判不了送达与否，
            -- 而群上下文里的私信走「群临时会话」，新加好友的那一次送不到。
            -- 服务端那条走 OneBot 的 send_private_msg，能拿到 retcode、还能重试两次。
            -- 这里照旧给「一次性口令」这条一定可行的路。
            return string.format(
                "📈 布莱泽模拟炒股 · 群 %s\n" ..
                "交易页：%s\n" ..
                "登录填：你的 QQ 号 + 口令 %s（%d 分钟内有效，只能用一次）\n" ..
                "（口令一次性的，别发给别人；登录后 30 天内免登录）\n" ..
                "懒得输口令？私聊我发「炒股」，链接直接发你（没收到就再发一次）。\n" ..
                "（登录后去「钱包 → 登录口令」设一个自己的，之后 30 天免登录）",
                gid, site, pc.passcode, math.floor((pc.ttl or 600) / 60))
        end
        -- 铸口令失败也要给个能用的下一步
        return string.format(
            "📈 布莱泽模拟炒股 · 群 %s\n" ..
            "交易页：%s\n" ..
            "口令生成失败了，私聊我（QQ %s）发「炒股」，我立刻把交易页发你。",
            gid, site, BOT_QQ)
    end
    -- 「炒股 公开」：直接把链接发在群里
    return string.format(
        "📈 布莱泽模拟炒股 · 群 %s\n" ..
        "初始资金 10 万，可以做多、做空、加杠杆，还能贷款和去群里打零工。\n" ..
        "打开下面链接交易（7 天内有效，链接等同账户凭证，别转发给别人）：\n%s",
        gid, j.data.url)
end

-- 「重置口令」：忘了固定口令就在群里发这个，铸一个临时的顶上去。
function reset_passcode(msg)
    local group = msg.fromGroup
    if group == nil then group = "" end
    local is_private = (group == nil or group == "")
    local qq = tostring(msg.fromQQ)
    local nick = getUserConf(qq, "nick") or "{nick}"
    local members = is_private and "[]" or build_members(group, qq)
    local j, jerr = call_session(is_private and "" or tostring(group), qq, nick, members)
    if not j then
        return "炒股服务" .. (jerr or "没响应") .. "，请联系管理员检查 9030 端口"
    end
    if j.code ~= 200 or not j.data then
        return "炒股服务出错：" .. (j.message or "未知错误")
    end
    local gid = tostring(j.data.g or group or "")
    local pc = mint_passcode(gid, qq, true)
    if not pc or not pc.passcode then
        return "口令重置失败了，私聊我（QQ " .. BOT_QQ .. "）说一声。"
    end
    return string.format(
        "🔑 已重置你的登录口令\n" ..
        "交易页：%s\n" ..
        "登录填：你的 QQ 号 + 口令 %s（%d 分钟内有效，只能用一次）\n" ..
        "（旧口令已失效；登录后去「钱包 → 登录口令」设一个自己的，以后就不用再找我了）",
        (pc and pc.site) or SITE_FALLBACK, pc.passcode, math.floor((pc.ttl or 600) / 60))
end

function my_stock(msg)
    -- 私聊时 msg.fromGroup 是空串而不是 nil，留空让服务端按登记表认群
    local group = msg.fromGroup
    if group == nil then group = "" end
    local admin = get_admin_key()
    -- 同上：回环调用免密钥
    local td = tmpdir()
    local resp_path = td .. "\\stock_sum_" .. os.time() .. "_" .. math.random(1000, 9999) .. ".json"
    local url = string.format("%s/api/summary?g=%s&u=%s", API, tostring(group),
        tostring(msg.fromQQ))
    curl_get(url, resp_path, "X-Admin: " .. admin)
    local resp = read_all(resp_path)
    os.remove(resp_path)
    if not resp or resp == "" then
        return "炒股服务没响应，请联系管理员检查 9030 端口"
    end
    local j = decode_safe(resp)
    if not j then
        return "炒股接口返回解析失败"
    end
    if j.code ~= 200 or not j.data then
        return "查询失败：" .. (j.message or resp:sub(1, 120))
    end
    local a = j.data.acct
    local lines = {}
    table.insert(lines, string.format("📊 %s 的账户", a.nick ~= "" and a.nick or a.qq))
    table.insert(lines, string.format("净值 %.2f　可用 %.2f　欠款 %.2f", a.equity, a.cash, a.loan))
    table.insert(lines, string.format("浮动盈亏 %+.2f　总盈亏 %+.2f", a.unreal, a.total_pl))
    if a.bankrupt == 1 then
        table.insert(lines, "⚠️ 你已破产，只能去别人公司打工还债")
    end
    if #a.positions > 0 then
        table.insert(lines, "持仓：")
        for _, p in ipairs(a.positions) do
            table.insert(lines, string.format("  %s %s %d股 @%.2f 浮盈 %+.2f",
                p.side == "long" and "多" or "空", p.name, p.qty, p.entry, p.pl))
        end
    else
        table.insert(lines, "当前没有持仓")
    end
    if j.data.rank > 0 then
        table.insert(lines, string.format("群内排名：第 %d / %d 名", j.data.rank, j.data.total))
    end
    local ci = j.data.checkin
    if ci then
        if ci.done then
            table.insert(lines, string.format("签到：今天已签，连签 %d 天（再 %d 天拿大奖）", ci.streak, ci.bonus_in))
        else
            table.insert(lines, string.format("签到：还没签！发「签到」拿钱（当前连签 %d 天）", ci.streak))
        end
    end
    local alerts = j.data.alerts or {}
    for _, al in ipairs(alerts) do
        table.insert(lines, string.format("🔔 提醒触发：%s %s %.2f（现价 %.2f）",
            al.name, al.kind == "above" and "涨到" or "跌到", al.price, al.now))
    end
    local newach = j.data.new_achievements or {}
    for _, a in ipairs(newach) do
        table.insert(lines, a)
    end
    -- v3：回归礼包、任务结算这些「顺手发一下」的提示
    local notices = j.data.notices or {}
    for _, n in ipairs(notices) do
        table.insert(lines, n)
    end
    local qs = j.data.quests or {}
    if #qs > 0 then
        local qdone = 0
        for _, q in ipairs(qs) do
            if q.done then qdone = qdone + 1 end
        end
        table.insert(lines, string.format("今日任务 %d/%d（发「任务」看详情）", qdone, #qs))
    end
    table.insert(lines, "私聊我发「炒股」随时能再拿交易页链接（7 天有效）")
    return table.concat(lines, "\n")
end

-- ------------------------------------------------------------ 签到
function stock_checkin(msg)
    local qq, nick = cur_qq_nick(msg)
    local j = api_post("/api/g/checkin", { g = cur_gid(msg), u = qq, nick = nick })
    if not j then return "炒股服务没响应，请联系管理员检查 9030 端口" end
    if j.code ~= 200 then return "签到失败：" .. (j.message or "未知错误") end
    return j.data.msg
end

-- ------------------------------------------------------------ 榜单
local function board_lines(j, kind)
    local titles = {
        rich = "群内富豪榜", profit = "群内盈亏榜",
        hot = "今日涨幅榜", limit = "今日涨停榜",
        lose = "出局榜 · 亏钱榜",
        charity = "散财榜",
        rob = "打土豪战果榜", lux = "挥霍榜"
    }
    local lines = { "📊 " .. (titles[kind] or "群内榜单") }
    local items = (j.data and j.data.items) or {}
    if #items == 0 then
        table.insert(lines, "（暂时还没有数据）")
        return table.concat(lines, "\n")
    end
    if kind == "lose" then
        local out, loss = {}, {}
        for _, it in ipairs(items) do
            if it.tag == "out" then
                table.insert(out, it)
            else
                table.insert(loss, it)
            end
        end
        table.insert(lines, "💀 出局榜（已破产清算）")
        if #out == 0 then
            table.insert(lines, "（暂时还没有人破产，大家都很稳）")
        end
        for _, it in ipairs(out) do
            table.insert(lines, string.format("%d. %s  净值 %.2f  累计亏损 %.0f 元",
                it.rank, (it.nick ~= "" and it.nick or it.qq), it.equity, it.loss))
        end
        table.insert(lines, "💸 亏钱榜（还在玩）")
        if #loss == 0 then
            table.insert(lines, "（大家都还赚着，挺好）")
        end
        for _, it in ipairs(loss) do
            table.insert(lines, string.format("%d. %s  净值 %.2f  累计亏损 %.0f 元",
                it.rank, (it.nick ~= "" and it.nick or it.qq), it.equity, it.loss))
        end
        table.insert(lines, "👉 发「我的股票」看看自己还剩多少")
        return table.concat(lines, "\n")
    end
    if kind == "charity" then
        table.insert(lines, "🧧 散财榜（累计撒钱）")
        if #items == 0 then
            table.insert(lines, "（还没有人撒过钱，富豪们该行动了）")
            return table.concat(lines, "\n")
        end
        for _, it in ipairs(items) do
            table.insert(lines, string.format("%d. %s  撒出 %.0f 元",
                it.rank, (it.nick ~= "" and it.nick or it.qq), it.amount))
        end
        table.insert(lines, "👉 发「撒钱 金额」把钱撒给最穷的那批群友")
        return table.concat(lines, "\n")
    end
    if kind == "rob" then
        table.insert(lines, "🦹 打土豪战果榜（累计抢到的钱）")
        if #items == 0 then
            table.insert(lines, "（还没有人从富豪兜里抢到过钱 —— 你可以当第一个）")
            return table.concat(lines, "\n")
        end
        for _, it in ipairs(items) do
            table.insert(lines, string.format("%d. %s  抢到 %.0f 元",
                it.rank, (it.nick ~= "" and it.nick or it.qq), it.amount))
        end
        table.insert(lines, "👉 发「打土豪 昵称」试试手气")
        return table.concat(lines, "\n")
    end
    if kind == "lux" then
        table.insert(lines, "🛍 挥霍榜（累计消费）")
        if #items == 0 then
            table.insert(lines, "（还没有人挥霍过 —— 有钱人的快乐还没人解锁）")
            return table.concat(lines, "\n")
        end
        for _, it in ipairs(items) do
            table.insert(lines, string.format("%d. %s  花了 %.0f 元",
                it.rank, (it.nick ~= "" and it.nick or it.qq), it.amount))
        end
        table.insert(lines, "👉 发「挥霍 豪宅」消费换声望")
        return table.concat(lines, "\n")
    end
    for i, it in ipairs(items) do
        if kind == "hot" or kind == "limit" then
            table.insert(lines, string.format("%d. %s(%s) %.2f  %+.2f%%",
                i, it.name, it.ticker, it.price, it.chg))
        else
            local flag = it.bankrupt and "  💀" or ""
            local tier = it.tier or ""
            table.insert(lines, string.format("%d. %s  净值 %.2f  盈亏 %+.2f  %s%s",
                i, (it.nick ~= "" and it.nick or it.qq), it.equity, it.pl, tier, flag))
        end
    end
    return table.concat(lines, "\n")
end

function stock_board_rich(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/board?g=%s&u=%s&kind=rich&n=10", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "榜单查询失败：" .. (j.message or "") end
    return board_lines(j, "rich")
end

function stock_board_profit(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/board?g=%s&u=%s&kind=profit&n=10", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "榜单查询失败：" .. (j.message or "") end
    return board_lines(j, "profit")
end

function stock_board_hot(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/board?g=%s&u=%s&kind=hot&n=10", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "榜单查询失败：" .. (j.message or "") end
    return board_lines(j, "hot")
end

function stock_board_lose(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/board?g=%s&u=%s&kind=lose&n=10", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "榜单查询失败：" .. (j.message or "") end
    return board_lines(j, "lose")
end

function stock_board_limit(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/board?g=%s&u=%s&kind=limit&n=10", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "榜单查询失败：" .. (j.message or "") end
    return board_lines(j, "limit")
end

-- ------------------------------------------------------------ 今日复盘
function stock_daily(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/daily?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "复盘查询失败：" .. (j.message or "") end
    local d = j.data
    local lines = { string.format("📅 %s 收盘复盘", d.day),
        string.format("全市场 %d 家公司，涨停 %d 家，今日成交 %d 笔",
            d.total, d.limit_up, d.trades),
        "—— 涨幅榜 ——" }
    for i = 1, math.min(5, #(d.top or {})) do
        local t = d.top[i]
        table.insert(lines, string.format("%d. %s(%s) %.2f %+.2f%%", i, t.name, t.ticker, t.price, t.chg))
    end
    table.insert(lines, "—— 富豪榜 ——")
    for i = 1, math.min(5, #(d.rank or {})) do
        local r = d.rank[i]
        table.insert(lines, string.format("%d. %s 净值 %.2f（%+.2f）", i, r.nick, r.equity, r.pl))
    end
    return table.concat(lines, "\n")
end

-- ------------------------------------------------------------ 提醒
function stock_reminders(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/reminders?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "提醒查询失败：" .. (j.message or "") end
    local lines = { "🔔 我的价格提醒" }
    local items = (j.data and j.data.items) or {}
    if #items == 0 then
        table.insert(lines, "（还没设置提醒，发「提醒 股票名 涨到 30」试试）")
    end
    for _, it in ipairs(items) do
        table.insert(lines, string.format("%s %s %.2f　现价 %.2f%s",
            it.name, it.kind == "above" and "涨到" or "跌到", it.price, it.cur,
            it.hit == 1 and "  ✅已触发" or ""))
    end
    local alerts = (j.data and j.data.alerts) or {}
    if #alerts > 0 then
        table.insert(lines, "—— 最近触发 ——")
        for _, a in ipairs(alerts) do
            table.insert(lines, string.format("🔔 %s %s %.2f（现价 %.2f）",
                a.name, a.kind == "above" and "涨到" or "跌到", a.price, a.now))
        end
    end
    return table.concat(lines, "\n")
end

function stock_set_reminder(msg)
    local qq = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", "")
    if rest == "" then
        return "用法：提醒 股票名 涨到 30\n（股票名写公司名或代码都行，比如「甲老板食品」「6001234」）"
    end
    -- Lua 模式没有「或」分支，按空白切分自己解析
    local parts = {}
    for w in string.gmatch(rest, "%S+") do parts[#parts + 1] = w end
    local name, kind, price
    if #parts >= 3 then
        kind = parts[#parts - 1]
        price = parts[#parts]
        name = table.concat(parts, " ", 1, #parts - 2)
    elseif #parts == 2 then
        name, price = parts[1], parts[2]
        kind = "涨到"
    else
        return "用法：提醒 股票名 涨到 30"
    end
    local dir = "above"
    if kind == "跌到" or kind == "跌过" or kind == "below" then dir = "below" end
    local j = api_post("/api/g/reminder", {
        g = cur_gid(msg), u = qq, name = name, kind = dir, price = tonumber(price) or 0
    })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "设置失败：" .. (j.message or "") end
    return j.data.msg
end

-- ------------------------------------------------------------ 成就
function stock_ach(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/achievements?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "成就查询失败：" .. (j.message or "") end
    local items = (j.data and j.data.items) or {}
    local got, lines = 0, { "🏅 我的成就墙" }
    for _, a in ipairs(items) do
        if a.got then
            got = got + 1
            table.insert(lines, string.format("✅ %s（%s）", a.name, a.desc))
        end
    end
    if got == 0 then
        table.insert(lines, "（还没解锁成就，先去交易、签到、打工试试）")
    end
    table.insert(lines, string.format("已解锁 %d / %d 个", got, #items))
    return table.concat(lines, "\n")
end

-- ------------------------------------------------------------ 打工
function stock_job(msg)
    local qq, nick = cur_qq_nick(msg)
    local j = api_post("/api/g/job", { g = cur_gid(msg), u = qq, nick = nick })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "打工失败：" .. (j.message or "") end
    return j.data.msg
end

-- ------------------------------------------------------------ v3 今日任务
function stock_quests(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/quests?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应，请联系管理员检查 9030 端口" end
    if j.code ~= 200 then return "任务查询失败：" .. (j.message or "") end
    local items = (j.data and j.data.items) or {}
    local lines = { "🎯 今日任务" }
    local done = 0
    for _, it in ipairs(items) do
        local mark = "⬜"
        if it.done then
            mark = "✅"
            done = done + 1
        end
        table.insert(lines, string.format("%s %s（%s） +%.0f 元", mark, it.name, it.desc, it.reward))
    end
    local all_bonus = tonumber((j.data and j.data.all_bonus) or 180)
    table.insert(lines, string.format("完成 %d/%d，全部完成再拿 %.0f 元全勤奖", done, #items, all_bonus))
    local m = j.data and j.data.msg
    if m and m ~= "" then
        table.insert(lines, "——")
        table.insert(lines, m)
    end
    return table.concat(lines, "\n")
end

-- ------------------------------------------------------------ v3 快讯（消息面）
function stock_news(msg)
    local j = api_get(string.format("/api/g/news?g=%s&u=%s&n=8", cur_gid(msg), tostring(msg.fromQQ)))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "快讯查询失败：" .. (j.message or "") end
    local items = (j.data and j.data.items) or {}
    local mk = j.data and j.data.market
    local lines = { "📰 快讯 · 消息面" }
    -- 恐慌指数 + 熔断（临时休市）
    if mk then
        local extra = ""
        if mk.circuit then
            extra = string.format("　⛔ 临时休市（%s，约 %d 分钟）",
                mk.circuit_reason or "市场异动", mk.circuit_left or 0)
        end
        table.insert(lines, string.format("🌡 恐慌指数 %.0f（%s）%s",
            mk.fear or 50, mk.fear_label or "平稳", extra))
    end
    -- 今日大事记（爆炸性新闻）
    local major = (j.data and j.data.major) or {}
    if #major > 0 then
        table.insert(lines, "—— 今日大事记 ——")
        for i2, m in ipairs(major) do
            table.insert(lines, string.format("%d. %s（%+.1f%%）", i2, m.title, m.impact or 0))
        end
        table.insert(lines, "——")
    end
    if #items == 0 then
        table.insert(lines, "（暂时没有新消息）")
    end
    for i2, it in ipairs(items) do
        local tag = "快讯"
        if it.scope == "company" then
            tag = "公司"
        elseif it.scope == "industry" then
            tag = "行业"
        elseif it.scope == "market" then
            tag = "大盘"
        end
        local tail = ""
        if it.scope == "company" and it.name and it.name ~= "" then
            tail = string.format("　%s %+.2f%%", it.name, it.chg)
        end
        table.insert(lines, string.format("%d.【%s】%s%s", i2, tag, it.title, tail))
    end
    return table.concat(lines, "\n")
end

-- ------------------------------------------------------------ v3 人气榜
function stock_hot(msg)
    local j = api_get(string.format("/api/g/hot?g=%s&n=10", cur_gid(msg)))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "人气榜查询失败：" .. (j.message or "") end
    local items = (j.data and j.data.items) or {}
    local lines = { "🔥 今日人气榜（按成交额）" }
    if #items == 0 then
        table.insert(lines, "（今天还没人下单，榜上空空如也）")
    end
    for _, it in ipairs(items) do
        table.insert(lines, string.format("%d. %s(%s) %.2f %+.2f%%　成交 %.0f 元 / %d 笔",
            it.rank, it.name, it.ticker, it.price, it.chg, it.amount, it.trades))
    end
    return table.concat(lines, "\n")
end

-- ------------------------------------------------------------ v3 打赏
-- ------------------------------------------------------------ v4 改名 / 搜股 / 商业战
-- Dice! 的查询串要自己转义，中文关键词不转义会被 curl 吃掉。
local function url_encode(s)
    s = tostring(s or "")
    return (s:gsub("([^%w%-%_%.%~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end))
end

-- 改名：给自己的公司改名字（要钱，24 小时一次）
function stock_rename(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        return "用法：改名 你的新公司名\n（2~12 字，改名费 3000 元，24 小时一次）"
    end
    local j = api_post("/api/g/rename", { g = cur_gid(msg), u = qq, nick = nick, name = rest })
    if not j then return "炒股服务没响应，请联系管理员检查 9030 端口" end
    if j.code ~= 200 then return "改名失败：" .. (j.message or "") end
    return j.data.msg
end

-- 搜股：按名称 / 代码 / 行业找公司
function stock_search(msg)
    local qq = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        return "用法：搜股 关键词\n（例：搜股 电子　搜股 600　搜股 隔壁老王）"
    end
    local j = api_get(string.format("/api/g/search?g=%s&u=%s&q=%s&n=10",
        cur_gid(msg), qq, url_encode(rest)))
    if not j then return "炒股服务没响应，请联系管理员检查 9030 端口" end
    if j.code ~= 200 then return "搜索失败：" .. (j.message or "") end
    local items = (j.data and j.data.items) or {}
    local lines = { "🔎 搜股 · " .. rest }
    if #items == 0 then
        table.insert(lines, "（没找到，换个名字 / 代码 / 行业再试试）")
    end
    for i, it in ipairs(items) do
        local mine = ""
        if it.mine then mine = "　🏠我的" end
        table.insert(lines, string.format("%d. %s(%s) %.2f %+.2f%%　[%s]%s",
            i, it.name, it.ticker, it.price or 0, it.chg or 0, it.industry or "", mine))
    end
    return table.concat(lines, "\n")
end

-- 商业战：花钱搞别人的公司（有成功率，翻车也会上新闻）
function stock_sabotage(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    -- 不带目标时，列出所有手段和今天剩余次数
    if rest == "" then
        local j = api_get(string.format("/api/g/sabotage?g=%s&u=%s", cur_gid(msg), qq))
        if not j then return "炒股服务没响应，请联系管理员检查 9030 端口" end
        if j.code ~= 200 then return "商业战查询失败：" .. (j.message or "") end
        local d = j.data or {}
        local lines = { "🗡 商业战" }
        local plans = d.plans or {}
        for _, p in ipairs(plans) do
            table.insert(lines, string.format("%s %s：出手费 %d 元，成功率约 %d%%",
                p.emoji or "•", p.name or "", p.cost or 0, p.rate or 0))
        end
        table.insert(lines, string.format("今天已出手 %d/%d 次", d.used or 0, d.limit or 2))
        table.insert(lines, "用法：坑害 公司名（或代码）　也可以指定手段")
        table.insert(lines, "目标越热门越难搞；失手会反噬（罚单 + 对手上涨）")
        return table.concat(lines, "\n")
    end
    -- 允许在目标后面带手段名：坑害 隔壁老王 做空狙击
    local target = rest
    local plan = ""
    local plans_map = { ["放黑料"] = 1, ["挖墙脚"] = 1, ["做空狙击"] = 1,
                        ["做空"] = 1, ["举报财务造假"] = 1, ["举报"] = 1,
                        ["资本碾压"] = 1 }
    for w, _ in pairs(plans_map) do
        if rest:sub(-#w) == w then
            target = rest:gsub("%s*" .. w .. "$", "")
            plan = w
        end
    end
    target = target:gsub("^%s+", ""):gsub("%s+$", "")
    if target == "" then
        return "用法：坑害 公司名（或代码）"
    end
    local j = api_post("/api/g/sabotage", {
        g = cur_gid(msg), u = qq, nick = nick, target = target, plan = plan
    })
    if not j then return "炒股服务没响应，请联系管理员检查 9030 端口" end
    if j.code ~= 200 then return "出手失败：" .. (j.message or "") end
    return j.data.msg
end


function stock_transfer(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", "")
    if rest == "" then
        return "用法：打赏 群友昵称 金额\n（例：打赏 小明 100，全群可见）"
    end
    local parts = {}
    for w in string.gmatch(rest, "%S+") do parts[#parts + 1] = w end
    if #parts < 2 then
        return "用法：打赏 群友昵称 金额\n（例：打赏 小明 100，全群可见）"
    end
    local amount = parts[#parts]
    local target = table.concat(parts, " ", 1, #parts - 1)
    local j = api_post("/api/g/transfer", {
        g = cur_gid(msg), u = qq, nick = nick,
        target = target, amount = tonumber(amount) or 0
    })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "打赏失败：" .. (j.message or "") end
    return j.data.msg
end

-- ------------------------------------------------------------ v4.4 条件单
-- 解析「命令词 后面的参数」：按空白切分成数组
local function split_words(s)
    local out = {}
    for w in string.gmatch(s or "", "%S+") do out[#out + 1] = w end
    return out
end

-- 方向词：止损 / 止盈可以显式说保护哪一边，不说就按持仓自动推断
local SHORT_WORDS = {
    ["空"] = true, ["做空"] = true, ["空头"] = true, ["空单"] = true,
    ["卖"] = true, ["卖出"] = true, ["sell"] = true, ["short"] = true,
}
local LONG_WORDS = {
    ["多"] = true, ["做多"] = true, ["多头"] = true, ["多单"] = true,
    ["买"] = true, ["买入"] = true, ["buy"] = true, ["long"] = true,
}

local function dir_word(w)
    if not w then return nil end
    local t = string.lower(tostring(w))
    if SHORT_WORDS[t] then return "short" end
    if LONG_WORDS[t] then return "long" end
    return nil
end

-- 「全仓」= 不写数量
local ALL_WORDS = { ["全仓"] = true, ["全部"] = true, ["all"] = true }

local function all_word(w)
    if not w then return false end
    return ALL_WORDS[string.lower(tostring(w))] == true
end

-- 我的条件单
function stock_orders(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/orders?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应，请联系管理员检查 9030 端口" end
    if j.code ~= 200 then return "条件单查询失败：" .. (j.message or "") end
    local items = (j.data and j.data.items) or {}
    local lines = { "📋 我的条件单" }
    if #items == 0 then
        table.insert(lines, "（还没挂单）")
    end
    for _, it in ipairs(items) do
        local extra = ""
        if it.kind == "limit" then
            extra = string.format("挂单 %d 股", math.floor(it.qty or 0))
        elseif (it.qty or 0) > 0 then
            extra = string.format("平 %d 股", math.floor(it.qty))
        else
            extra = "全仓"
        end
        table.insert(lines, string.format("#%d [%s] %s %s %.3f　%s　现价 %.3f",
            it.id, it.kind_name or "", it.name or "", it.side_name or "",
            it.price or it.trigger or 0, extra, it.cur or 0))
    end
    table.insert(lines, "用法：止损 股票名 [多/空] 价格 [数量]　止盈 股票名 [多/空] 价格 [数量]　限价 股票名 买/卖 价格 数量")
    table.insert(lines, "撤单发「撤单 编号」，编号就是上面的 #数字")
    return table.concat(lines, "\n")
end

-- 挂条件单（止损 / 止盈 / 限价共用）
local function place_cond(msg, kind)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        return "用法：止损 股票名 [多/空] 价格 [数量]\n（例：止损 平安银行 空 19.2 500；不写数量就是全仓）"
    end
    local parts = split_words(rest)
    if kind == "limit" then
        -- 限价 股票名 买/卖 价格 数量
        if #parts < 4 then
            return "用法：限价 股票名 买/卖 价格 数量\n（例：限价 隔壁老王 买 18.5 1000）"
        end
        local name = table.concat(parts, " ", 1, #parts - 3)
        local dw = parts[#parts - 2]
        local price = tonumber(parts[#parts - 1])
        local qty = tonumber(parts[#parts])
        if price == nil or qty == nil then return "价格或数量不是数字" end
        local dir = dir_word(dw)
        if not dir then
            return "方向只能写「买」或「卖」（例：限价 隔壁老王 买 18.5 1000）"
        end
        local j = api_post("/api/g/order", {
            g = cur_gid(msg), u = qq, nick = nick, name = name,
            kind = "limit", side = dir, trigger = price, qty = qty
        })
        if not j then return "炒股服务没响应" end
        if j.code ~= 200 then return "挂单失败：" .. (j.message or "") end
        return j.data.msg
    end
    -- 止损 / 止盈：股票名 [方向] 价格 [数量]
    --   止损 平安银行 18.5      → 多头止损（方向由持仓推断）
    --   止损 平安银行 空 19.2   → 空头止损
    --   止损 平安银行 空 19.2 500 → 空头止损，只平 500 股
    local kw = (kind == "stop_loss") and "止损" or "止盈"
    if #parts < 2 then
        return "用法：" .. kw .. " 股票名 [多/空] 价格 [数量]\n（例：" .. kw .. " 平安银行 空 19.2 500；不写数量就是全仓）"
    end
    -- 尾部「全仓」= 不写数量
    if #parts > 0 and all_word(parts[#parts]) then table.remove(parts) end
    -- 尾部数字：1 个是价格，2 个是「价格 数量」（跟同花顺一个顺序）
    local nums = {}
    while #parts > 0 do
        local v = tonumber(parts[#parts])
        if v == nil then break end
        table.insert(nums, 1, v)
        table.remove(parts)
    end
    if #nums == 0 then
        return "价格不是数字（例：" .. kw .. " 平安银行 空 19.2）"
    end
    if #nums > 2 then
        return "参数太多：" .. kw .. " 股票名 [方向] 价格 [数量]"
    end
    local price = nums[1]
    local qty = (#nums >= 2) and nums[2] or 0
    -- 方向词：紧挨着价格前面那个（多 / 空）
    local dir = nil
    if #parts > 0 then dir = dir_word(parts[#parts]) end
    if dir and #parts > 0 then table.remove(parts) end
    local name = table.concat(parts, " ")
    if name == "" then
        return "要写股票名（例：" .. kw .. " 平安银行 空 19.2）"
    end
    local j = api_post("/api/g/order", {
        g = cur_gid(msg), u = qq, nick = nick, name = name,
        -- 不写方向就传空串，服务端按当前持仓自动推断（只有多头 / 只有空头都行）
        kind = kind, side = dir or "", trigger = price, qty = qty
    })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "挂单失败：" .. (j.message or "") end
    return j.data.msg
end

function stock_stop_loss(msg)
    return place_cond(msg, "stop_loss")
end

function stock_take_profit(msg)
    return place_cond(msg, "take_profit")
end

function stock_limit(msg)
    return place_cond(msg, "limit")
end

-- 撤条件单
function stock_cancel_order(msg)
    local qq = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    local id = tonumber(rest)
    if not id then
        return "用法：撤单 编号\n（编号在「条件单」列表里，就是 #数字）"
    end
    local j = api_post("/api/g/order_cancel", { g = cur_gid(msg), u = qq, id = id })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "撤单失败：" .. (j.message or "") end
    return j.data.msg
end

-- ------------------------------------------------------------ v4.4 趣味玩法
-- 人气投票
function stock_vote(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        return "用法：投票 股票名\n（给群友的公司拉人气，每票都会给它抬一点股价）"
    end
    local j = api_post("/api/g/vote", { g = cur_gid(msg), u = qq, nick = nick, name = rest })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "投票失败：" .. (j.message or "") end
    return j.data.msg
end

-- 人气投票榜
function stock_vote_board(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/vote?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "投票榜查询失败：" .. (j.message or "") end
    local board = (j.data and j.data.board) or {}
    local st = (j.data and j.data.status) or {}
    local lines = { "🗳 今日人气投票榜" }
    if #board == 0 then
        table.insert(lines, "（今天还没人投票，快去投一票）")
    end
    for i, it in ipairs(board) do
        table.insert(lines, string.format("%d. %s(%s) %d 票　%.3f %+.2f%%",
            it.rank or i, it.name or "", it.ticker or "", it.votes or 0,
            it.price or 0, it.chg or 0))
    end
    table.insert(lines, string.format("—— 今天一共 %d 票 ——", st.total or 0))
    if st.voted then
        table.insert(lines, "你已经投过票了，明天再来（每人每天一票）")
    else
        table.insert(lines, "发「投票 股票名」投你看好的一只")
    end
    return table.concat(lines, "\n")
end

-- 每日抽奖
function stock_lottery(msg)
    local qq, nick = cur_qq_nick(msg)
    local j = api_post("/api/g/lottery", { g = cur_gid(msg), u = qq, nick = nick })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "抽奖失败：" .. (j.message or "") end
    return j.data.msg
end

-- 涨停竞猜
function stock_guess(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        local g = api_get(string.format("/api/g/guess?g=%s&u=%s", cur_gid(msg), qq))
        if not g then return "炒股服务没响应" end
        if g.code ~= 200 then return "竞猜查询失败：" .. (g.message or "") end
        local st = (g.data and g.data.status) or {}
        local lines = { "🔮 今日涨停竞猜" }
        if st.mine then
            table.insert(lines, "你押的是：" .. (st.mine.name or ""))
        else
            table.insert(lines, "你还没押，发「涨停竞猜 股票名」押一只（每人每天一票）")
        end
        table.insert(lines, string.format("目前已涨停 %d 家，全群 %d 人参与",
            st.limit_up_count or 0, st.players or 0))
        table.insert(lines, "收盘后结算，猜中拿 2000 元")
        return table.concat(lines, "\n")
    end
    local j = api_post("/api/g/guess", { g = cur_gid(msg), u = qq, nick = nick, name = rest })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "竞猜失败：" .. (j.message or "") end
    return j.data.msg
end
-- ------------------------------------------------------------ v4.4 破产翻身
-- 破产纾困进度（欠款、打工累计、下一档还差多少）
function stock_recovery(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/recovery?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
    return (j.data and j.data.text) or "没有查询到你的记录"
end

-- 贷款 / 还款 / 内幕
local function stock_money(msg, kind)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    local amount = tonumber(rest)
    if not amount or amount <= 0 then
        if kind == "borrow" then
            return "用法：贷款 金额\n（最多能借净值的 3 倍，上限 50 万，日息 0.05%）"
        end
        return "用法：还款 金额\n（从现金里扣，现金不够就少还点）"
    end
    local path = "/api/g/borrow"
    if kind == "repay" then path = "/api/g/repay" end
    local j = api_post(path, { g = cur_gid(msg), u = qq, nick = nick, amount = amount })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then
        if kind == "borrow" then return "贷款失败：" .. (j.message or "") end
        return "还款失败：" .. (j.message or "")
    end
    return j.data.msg
end

function stock_borrow(msg)
    return stock_money(msg, "borrow")
end

function stock_repay(msg)
    return stock_money(msg, "repay")
end

-- 买内幕消息：提前知道下一条快讯的标的和方向（五成准）
function stock_peek(msg)
    local qq, nick = cur_qq_nick(msg)
    local j = api_post("/api/g/peek", { g = cur_gid(msg), u = qq, nick = nick })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "买不到内幕：" .. (j.message or "") end
    return j.data.msg
end

-- ------------------------------------------------------------ v4.6 财富再分配
-- 破产清盘 / 债务重组：把「还了也破产、不还就永久冻结」的死账一次性解冻
function stock_liquidation(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "确认" or rest == "确定" or rest == "yes" then
        local j = api_post("/api/g/liquidation", { g = cur_gid(msg), u = qq, nick = nick })
        if not j then return "炒股服务没响应" end
        if j.code ~= 200 then return "清盘失败：" .. (j.message or "") end
        return j.data.msg
    end
    local j = api_get(string.format("/api/g/liquidation?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
    return (j.data and j.data.text) or "没有查询到你的记录"
end

-- 撒钱：把钱撒给群里最穷的那批人（定向扶贫，也是富人的「消费」出口）
function stock_scatter(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        local j = api_get(string.format("/api/g/scatter?g=%s&u=%s", cur_gid(msg), qq))
        if not j then return "炒股服务没响应" end
        if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
        return (j.data and j.data.text) or ""
    end
    local amount = tonumber(rest)
    if not amount or amount <= 0 then
        return "用法：撒钱 金额\n（钱会优先发给群里最穷的那批群友，手续费进逆袭基金）"
    end
    local j = api_post("/api/g/scatter", { g = cur_gid(msg), u = qq, nick = nick, amount = amount })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "撒钱失败：" .. (j.message or "") end
    return j.data.msg
end

-- 逆袭基金：查询资格
function stock_comeback(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/comeback?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
    return (j.data and j.data.text) or "没有查询到你的记录"
end

-- 逆袭基金：领取
function stock_comeback_claim(msg)
    local qq, nick = cur_qq_nick(msg)
    local j = api_post("/api/g/comeback", { g = cur_gid(msg), u = qq, nick = nick })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "领不到：" .. (j.message or "") end
    return j.data.msg
end

-- 资本碾压：花大钱砸别人的公司（高风险高回报）
function stock_crush(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        return "用法：资本碾压 公司名（或代码）\n（出手费 = 你的净值×2%，2 万 ~ 20 万，成功率约 45%）\n（失败则钱全没，对手股价还涨）"
    end
    local j = api_post("/api/g/sabotage", {
        g = cur_gid(msg), u = qq, nick = nick, target = rest, plan = "资本碾压"
    })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "出手失败：" .. (j.message or "") end
    return j.data.msg
end

-- 散财榜
function stock_board_charity(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/board?g=%s&u=%s&kind=charity&n=10", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "榜单查询失败：" .. (j.message or "") end
    return board_lines(j, "charity")
end

-- ------------------------------------------------------------ v4.7 富豪经济
-- 我的档位：净值分层 + 会员费 + 特权
function stock_tier(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/tier?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
    return (j.data and j.data.text) or "没有查询到你的记录"
end

-- 打土豪：花小钱抢有钱人的现金（有成功率，翻车也上新闻）
function stock_rob(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        local j = api_get(string.format("/api/g/rob?g=%s&u=%s", cur_gid(msg), qq))
        if not j then return "炒股服务没响应" end
        if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
        return (j.data and j.data.text) or ""
    end
    local j = api_post("/api/g/rob", { g = cur_gid(msg), u = qq, nick = nick, target = rest })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "下手失败：" .. (j.message or "") end
    return j.data.msg
end

-- 挥霍消费：花钱买声望（声望能降低被打劫的概率，但每天会掉）
function stock_luxury(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "" then
        local j = api_get(string.format("/api/g/luxury?g=%s&u=%s", cur_gid(msg), qq))
        if not j then return "炒股服务没响应" end
        if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
        return (j.data and j.data.text) or ""
    end
    local j = api_post("/api/g/luxury", { g = cur_gid(msg), u = qq, nick = nick, item = rest })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "买不了：" .. (j.message or "") end
    return j.data.msg
end

-- 全民分红：会员费攒的钱，按活跃度和贫困度发回群里
function stock_dividend(msg)
    local qq, nick = cur_qq_nick(msg)
    local rest = string.match(msg.fromMsg or "", "^%s*[^%s]+%s+(.+)$") or ""
    rest = rest:gsub("^%s+", ""):gsub("%s+$", "")
    if rest == "查询" or rest == "查看" or rest == "池子" then
        local j = api_get(string.format("/api/g/dividend?g=%s&u=%s", cur_gid(msg), qq))
        if not j then return "炒股服务没响应" end
        if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
        return (j.data and j.data.text) or ""
    end
    local j = api_post("/api/g/dividend", { g = cur_gid(msg), u = qq, nick = nick })
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "领不到：" .. (j.message or "") end
    return j.data.msg
end

-- 财富分布体检：差距到底在哪
function stock_wealth(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/wealth?g=%s&u=%s", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "查询失败：" .. (j.message or "") end
    return (j.data and j.data.text) or ""
end

-- 战果榜：谁从富豪兜里抢到过钱
function stock_board_rob(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/board?g=%s&u=%s&kind=rob&n=10", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "榜单查询失败：" .. (j.message or "") end
    return board_lines(j, "rob")
end

-- 挥霍榜：谁花得最阔
function stock_board_lux(msg)
    local qq = cur_qq_nick(msg)
    local j = api_get(string.format("/api/g/board?g=%s&u=%s&kind=lux&n=10", cur_gid(msg), qq))
    if not j then return "炒股服务没响应" end
    if j.code ~= 200 then return "榜单查询失败：" .. (j.message or "") end
    return board_lines(j, "lux")
end

msg_order["炒股"] = "chaogu"
msg_order["重置口令"] = "reset_passcode"
msg_order["重置登录口令"] = "reset_passcode"
msg_order["忘记口令"] = "reset_passcode"
msg_order["股票"] = "chaogu"
msg_order["开户"] = "chaogu"
msg_order["注册"] = "chaogu"
msg_order["绑定"] = "chaogu"
msg_order["我的股票"] = "my_stock"
msg_order["股票行情"] = "my_stock"
msg_order["签到"] = "stock_checkin"
msg_order["打卡"] = "stock_checkin"
msg_order["每日签到"] = "stock_checkin"
msg_order["股神榜"] = "stock_board_rich"
msg_order["富豪榜"] = "stock_board_rich"
msg_order["排行榜"] = "stock_board_rich"
msg_order["盈亏榜"] = "stock_board_profit"
msg_order["涨幅榜"] = "stock_board_hot"
msg_order["涨停榜"] = "stock_board_limit"
msg_order["亏损榜"] = "stock_board_lose"
msg_order["亏钱榜"] = "stock_board_lose"
msg_order["出局榜"] = "stock_board_lose"
msg_order["破产榜"] = "stock_board_lose"
msg_order["输家榜"] = "stock_board_lose"
msg_order["今日复盘"] = "stock_daily"
msg_order["收盘复盘"] = "stock_daily"
msg_order["每日复盘"] = "stock_daily"
msg_order["我的提醒"] = "stock_reminders"
msg_order["提醒"] = "stock_set_reminder"
msg_order["设提醒"] = "stock_set_reminder"
msg_order["成就"] = "stock_ach"
msg_order["我的成就"] = "stock_ach"
msg_order["打工"] = "stock_job"
msg_order["任务"] = "stock_quests"
msg_order["今日任务"] = "stock_quests"
msg_order["每日任务"] = "stock_quests"
msg_order["快讯"] = "stock_news"
msg_order["新闻"] = "stock_news"
msg_order["消息面"] = "stock_news"
msg_order["人气榜"] = "stock_hot"
msg_order["交易榜"] = "stock_hot"
msg_order["打赏"] = "stock_transfer"
msg_order["改名"] = "stock_rename"
msg_order["公司改名"] = "stock_rename"
msg_order["搜股"] = "stock_search"
msg_order["搜索"] = "stock_search"
msg_order["查股"] = "stock_search"
msg_order["找股票"] = "stock_search"
msg_order["坑害"] = "stock_sabotage"
msg_order["搞他"] = "stock_sabotage"
msg_order["商业战"] = "stock_sabotage"
msg_order["坑公司"] = "stock_sabotage"
msg_order["红包"] = "stock_transfer"
-- v4.4 条件单 / 趣味玩法
msg_order["条件单"] = "stock_orders"
msg_order["我的条件单"] = "stock_orders"
msg_order["挂单"] = "stock_orders"
msg_order["止损"] = "stock_stop_loss"
msg_order["止盈"] = "stock_take_profit"
msg_order["限价"] = "stock_limit"
msg_order["撤单"] = "stock_cancel_order"
msg_order["投票"] = "stock_vote"
msg_order["投票榜"] = "stock_vote_board"
msg_order["人气投票"] = "stock_vote_board"
msg_order["抽奖"] = "stock_lottery"
msg_order["今日抽奖"] = "stock_lottery"
msg_order["涨停竞猜"] = "stock_guess"
msg_order["竞猜"] = "stock_guess"
msg_order["贷款"] = "stock_borrow"
msg_order["借钱"] = "stock_borrow"
msg_order["还款"] = "stock_repay"
msg_order["还钱"] = "stock_repay"
msg_order["内幕"] = "stock_peek"
msg_order["买内幕"] = "stock_peek"
msg_order["八卦"] = "stock_peek"
msg_order["翻身进度"] = "stock_recovery"
msg_order["我的状态"] = "stock_recovery"
msg_order["纾困进度"] = "stock_recovery"
msg_order["破产进度"] = "stock_recovery"
msg_order["破产清算"] = "stock_liquidation"
msg_order["清盘"] = "stock_liquidation"
msg_order["债务重组"] = "stock_liquidation"
msg_order["重整"] = "stock_liquidation"
msg_order["撒钱"] = "stock_scatter"
msg_order["发红包"] = "stock_scatter"
msg_order["请客"] = "stock_scatter"
msg_order["散财"] = "stock_scatter"
msg_order["逆袭补助"] = "stock_comeback"
msg_order["补助"] = "stock_comeback"
msg_order["领补助"] = "stock_comeback_claim"
msg_order["领取补助"] = "stock_comeback_claim"
msg_order["资本碾压"] = "stock_crush"
msg_order["碾压"] = "stock_crush"
msg_order["散财榜"] = "stock_board_charity"
msg_order["慈善榜"] = "stock_board_charity"
-- v4.7 富豪经济
msg_order["我的档位"] = "stock_tier"
msg_order["档位"] = "stock_tier"
msg_order["富豪俱乐部"] = "stock_tier"
msg_order["俱乐部"] = "stock_tier"
msg_order["会员费"] = "stock_tier"
msg_order["我的会员"] = "stock_tier"
msg_order["打土豪"] = "stock_rob"
msg_order["打劫"] = "stock_rob"
msg_order["抢钱"] = "stock_rob"
msg_order["劫富"] = "stock_rob"
msg_order["挥霍"] = "stock_luxury"
msg_order["消费"] = "stock_luxury"
msg_order["排面"] = "stock_luxury"
msg_order["买豪宅"] = "stock_luxury"
msg_order["领分红"] = "stock_dividend"
msg_order["分红"] = "stock_dividend"
msg_order["全民分红"] = "stock_dividend"
msg_order["分红池"] = "stock_dividend"
msg_order["财富分布"] = "stock_wealth"
msg_order["贫富差距"] = "stock_wealth"
msg_order["战果榜"] = "stock_board_rob"
msg_order["打劫榜"] = "stock_board_rob"
msg_order["挥霍榜"] = "stock_board_lux"

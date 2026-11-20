/* 布莱泽模拟炒股 —— 前端（无外部依赖，手机可用） */
(function () {
  "use strict";

  // 这个页面加载时用的前端版本（取自自身 script URL 的 ?v=）。
  // 服务端 /api/state 会回一个当前版本；两者不一致 = 这个页面是旧版，
  // 页面会自己重载一次（见 checkStaticVer），群友不用清缓存、不用等公告。
  var PAGE_VER = (function () {
    try {
      var src = document.currentScript && document.currentScript.src;
      if (!src) return "";
      var m = src.match(/[?&]v=([0-9A-Za-z_.\-]+)/);
      return m ? m[1] : "";
    } catch (e) { return ""; }
  })();

  // 令牌在 fragment(#t=) 里：浏览器不会把它发给服务器，就不会进服务端日志。
  // 老链接（?t=）仍然兼容。
  var Q = new URLSearchParams(location.search);
  var F = new URLSearchParams((location.hash || "").replace(/^#/, ""));
  var G = Q.get("g") || F.get("g") || "";
  var U = Q.get("u") || F.get("u") || "";
  var T = F.get("t") || Q.get("t") || "";

  // 登录态存在本机：群里领一次口令登录后，刷新页面、关掉再打开都还是登录态。
  // 令牌不放 URL 里，也不发给服务器（服务端只从 X-Token 头拿）。
  var LS_KEY = "sm_auth_v1";
  function loadAuth() {
    try {
      var raw = localStorage.getItem(LS_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }
  function saveAuth(a) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(a)); } catch (e) {}
  }
  function clearAuth() {
    try { localStorage.removeItem(LS_KEY); } catch (e) {}
  }
  /* 令牌过期 / 链接失效：整页回登录页。
     原来只在角落弹个 toast，页面还照旧渲染 —— 用户看到的是一屏
     「--」的空壳加一个错字提示，根本不知道要干什么。
     例外：本机还存着另一份没过期登录态（比如从群里旧链接点进来），
     那就用存的那份，别把人踢下线。 */
  function authExpired() {
    var saved = loadAuth();
    if (saved && saved.t && saved.t !== T && saved.g && saved.u) {
      G = saved.g; U = saved.u; T = saved.t;
      saveAuth({ g: G, u: U, t: T });
      try {
        history.replaceState(null, "", location.pathname + location.search +
          "#t=" + encodeURIComponent(T) + "&g=" + encodeURIComponent(G) + "&u=" + encodeURIComponent(U));
      } catch (e) {}
      location.reload();
      return;
    }
    clearAuth();
    showLogin();
  }

  if (!G || !U || !T) {
    var saved = loadAuth();
    if (saved && saved.g && saved.u && saved.t) {
      G = saved.g; U = saved.u; T = saved.t;
    }
  }
  if (G && U && T) { saveAuth({ g: G, u: U, t: T }); }

  if (!G || !U || !T) {
    showLogin();
    return;
  }

  // 群里发「炒股」时 QQ 侧的私信送不到（群临时会话），所以群里给的是
  // 一次性口令，用户在这里自己换令牌，不用加好友、不用等冷却。
  function showLogin() {
    document.getElementById("app").innerHTML =
      '<div class="login-wrap">' +
      '<div class="login-card">' +
      '<div class="login-logo">📈</div>' +
      '<div class="login-title">布莱泽模拟炒股</div>' +
      '<div class="login-sub">输入 QQ 号 + 口令就能进交易页</div>' +
      '<input id="lgQq" class="login-input" type="text" inputmode="numeric" autocomplete="username" placeholder="QQ 号">' +
      '<input id="lgPwd" class="login-input" type="password" autocomplete="current-password" placeholder="口令">' +
      '<button id="lgBtn" class="big-btn login-btn">进入交易页</button>' +
      '<div id="lgMsg" class="login-msg"></div>' +
      '<div class="login-tip">口令在哪？在群里发一次「炒股」，我当场给你一个（10 分钟内有效、只能用一次）。<br>登录后 30 天内免登录；口令忘了就在群里发「重置口令」。</div>' +
      '</div></div>';
    var btn = document.getElementById("lgBtn");
    var msg = document.getElementById("lgMsg");
    function submit() {
      var qq = (document.getElementById("lgQq").value || "").trim();
      var pwd = (document.getElementById("lgPwd").value || "").trim();
      if (!qq || !pwd) { msg.textContent = "QQ 号和口令都要填"; return; }
      btn.disabled = true;
      msg.textContent = "登录中…";
      fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ u: qq, pwd: pwd })
      }).then(function (r) {
        return r.text().then(function (txt) {
          if (!txt) throw new Error("服务没返回内容，稍后再试");
          try { return JSON.parse(txt); }
          catch (e) { throw new Error("服务返回异常，稍后再试"); }
        });
      }).then(function (j) {
        if (!j || j.code !== 200) throw new Error((j && j.message) || "登录失败");
        saveAuth({ g: j.data.g, u: j.data.u, t: j.data.token });
        location.reload();
      }).catch(function (e) {
        msg.textContent = e.message || "登录失败";
        btn.disabled = false;
      });
    }
    btn.addEventListener("click", submit);
    document.getElementById("lgPwd").addEventListener("keydown", function (e) {
      if (e.key === "Enter") submit();
    });
  }

  function $(id) { return document.getElementById(id); }

  var S = {
    companies: [], news: [], me: null, rank: [], trades: [], ledger: [],
    cfg: { fee_rate: 0.001, max_leverage: 20, min_leverage: 1, initial_cash: 100000,
           max_loan_abs: 500000, loan_rate_per_day: 0.0005, candle_seconds: 60 },
    cid: null, sel: null, side: "long", lev: 1,
    offset: 0, cross: null, tab: "market", candles: [], busy: false,
    checkin: null, alerts: [], notices: [], ach: [], board: [], boardKind: "rich",
    reminders: [], myCo: null, market: null, q: "",
    orders: [], coupons: [], vote: null, voteBoard: [], lottery: null, guess: null,
    recovery: null, tier: null, job: null, onboarding: null, quests: [], rise: [],
    rob: null, luxury: null, dividend: null, wealth: null,
    fund: null, fundBet: null, fundBoard: [], fundBets: [],
    openPanel: null, loaded: {}, msg: {}, changelog: null,
    groups: [], switching: false
  };

  /* ---------------------------------------------------------- 工具 */
  function fmt(n) {
    n = Number(n) || 0;
    return n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function px(v) {
    // 价格展示精度：低价股多给几位小数，免得 K 线上看出一条直线
    v = Number(v) || 0;
    var a = Math.abs(v);
    return v.toFixed(a < 1 ? 4 : (a < 10 ? 3 : 2));
  }
  function fmtInt(n) {
    return (Math.round(Number(n) || 0)).toLocaleString("zh-CN");
  }
  /* 顶栏格子窄：上百万直接写全会被 CSS 截成「4,549,150....」，
     换成「万 / 亿」才看得清。 */
  function fmtC(n) {
    n = Number(n) || 0;
    var a = Math.abs(n);
    if (a >= 1e8) return (n / 1e8).toFixed(2) + "\u4ebf";
    if (a >= 1e6) return (n / 1e4).toFixed(1) + "\u4e07";
    /* 一万以上不再带小数：顶栏格子本来就窄，320px 小屏上
       「106,681.24」会被 CSS 截成「106,68...」，玩家连自己有多少钱都看不见。 */
    if (a >= 1e4) return Math.round(n).toLocaleString("zh-CN");
    return fmt(n);
  }
  /* 挥霍价格：顶配一单就能到百万，写成「1000000」太长，改成万 / 亿。 */
  function fmtYuan(n) {
    n = Number(n) || 0;
    var a = Math.abs(n);
    if (a >= 1e8) return (n / 1e8).toFixed(2) + "\u4ebf";
    if (a >= 1e4) return (n / 1e4).toFixed(a >= 1e6 ? 0 : 1) + "\u4e07";
    return Math.round(n).toLocaleString("zh-CN");
  }
  function cls(n) { return n > 0 ? "up" : n < 0 ? "down" : "flat"; }
  function pct(n) { return (n > 0 ? "+" : "") + (Number(n) || 0).toFixed(2) + "%"; }
  function tm(ts) {
    var d = new Date(ts * 1000);
    function p(x) { return ("0" + x).slice(-2); }
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var toastTimer = null;
  function toast(msg, kind) {
    var el = $("toast");
    el.textContent = msg;
    el.className = "toast" + (kind ? " " + kind : "");
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 2800);
  }

  /* ---------------------------------------------------------- 接口 */
  function qs(extra) {
    var s = "g=" + encodeURIComponent(G) + "&u=" + encodeURIComponent(U);
    return extra ? s + "&" + extra : s;
  }
  function parse(r) {
      // 【v4.11】空 body / 半截 body 一律给中文提示。
      // 以前直接 r.json()：服务端偶发返回空 body 时，浏览器抛的是
      // "Failed to execute 'json' on 'Response': Unexpected end of JSON input"，
      // 群友只看到一串英文，完全不知道发生了什么。
      return r.text().then(function (txt) {
        if (!txt) {
          var e0 = new Error("服务没返回内容，稍后自动重试");
          e0.status = r.status || 0;
          e0.transient = true;
          throw e0;
        }
        var j;
        try {
          j = JSON.parse(txt);
        } catch (e) {
          var e1 = new Error("服务返回异常（HTTP " + r.status + "），稍后再试");
          e1.status = r.status;
          e1.transient = true;
          throw e1;
        }
      if (!j || j.code !== 200) {
        var err = new Error((j && j.message) || ("HTTP " + r.status));
        err.status = r.status;
        throw err;
      }
      return j.data;
    });
  }
  /* 手机上弱网 / 后台切回来时 fetch 会直接 reject，浏览器给的是英文
     "Failed to fetch"，用户看不懂。统一换成人话。 */
  function friendlyErr(e) {
    var m = (e && e.message) ? String(e.message) : "";
    if (!m || /failed to fetch|networkerror|load failed|network request failed/i.test(m)) {
      return "网络断了，稍后再试";
    }
    // 浏览器原生的 JSON 报错（空响应 / 半截响应）也换成人话
    if (/unexpected end of json|failed to execute 'json'|unexpected token/i.test(m)) {
      return "服务没返回内容，正在重试…";
    }
    return m;
  }
  /* 【v4.11】网络抖动 / 服务端偶发空响应时自己重试几次。
     以前一失败就把浏览器英文报错原样弹给群友
     （"Failed to execute 'json' on 'Response': Unexpected end of JSON input"），
     看着像网页坏了。现在先自愈，实在不行才提示一句人话。
     只给 GET 用 —— POST 绝不重试（怕一笔订单下两次）。 */
  function withRetry(fn, times, delay) {
    var n = 0;
    function step() {
      return fn().catch(function (e) {
        if (e && e.status === 403) throw e;      // 登录过期了，重试也没用
        if (n >= times) throw e;
        n++;
        return new Promise(function (r) { setTimeout(r, delay * n); }).then(step);
      });
    }
    return step();
  }
  /* 【v4.12.3】连续失败后给后续请求加冷却，别让轮询越打越劣。
     【为什么】get() 自带 2 次重试，一次失败实际会打 3 次；碰上服务端的
     读限流（240 次/分，按 QQ 号分桶）就成了「越打越慢、越慢越打」的雪崩。
     这里连续失败后按 2s/4s/6s… 拉长冷却（最多 30s），成功一次就清零。
     403（令牌过期）不算失败 —— 那是 authExpired() 的活，不应拖慢别的请求。 */
  var _netFail = 0;
  var _netCoolUntil = 0;
  function get(path, extra) {
    var cool = _netCoolUntil - Date.now();
    var ready = cool > 0
      ? new Promise(function (r) { setTimeout(r, cool); })
      : Promise.resolve();
    return ready.then(function () {
      return withRetry(function () {
        return fetch(path + "?" + qs(extra), {
          cache: "no-store",
          headers: { "X-Token": T }
        }).then(parse);
      }, 2, 1200);
    }).then(function (r) {
      _netFail = 0;
      _netCoolUntil = 0;
      return r;
    }).catch(function (e) {
      var st = e && e.status;
      if (!st || st === 429 || st >= 500) {
        _netFail = Math.min(_netFail + 1, 5);
        _netCoolUntil = Date.now() + Math.min(_netFail * 2000, 30000);
      }
      var err = new Error(friendlyErr(e));
      err.status = st;
      throw err;
    });
  }
  /* 后台轮询失败别刷屏：2 秒一次的刷新失败不是用户操作，
     弹一次「正在重试」就够了，别让人以为网页炸了。 */
  var lastNetToast = 0;
  function netHint(e) {
    var now = Date.now();
    if (now - lastNetToast < 20000) return;
    lastNetToast = now;
    toast(friendlyErr(e) || "连接有点不稳，正在重试…");
  }
  /* 轮询加一点抖动：整群人同一秒打开页面、同一秒轮询，
     服务端会被瞬间打爆（429 一大半就是这么来的）。 */
  /* 【v4.12.3】页面切到后台时挂起轮询。
     【为什么要】四个轮询加起来，一个页面每分钟要打 50+ 次接口；
     服务端的读限流是 240 次/分**按 QQ 号分桶**，同一个人多开两个群的
     网页就可能撞上上限弹「刷新太频繁」。而且用户切走的页面
     本来也看不见，拉了也是白拉（K 线最后一根还在动，切回来
     补一次就行）。
     【怎么做】document.hidden 时只挂起定时器、不发请求；
     visibilitychange 变回可见时立刻全量刷一次。 */
  function pollEvery(ms, fn, jitter) {
    var wait = function () { return ms + Math.floor(Math.random() * (jitter || 0)); };
    var timer = setTimeout(function tick() {
      if (document.hidden) {
        timer = setTimeout(tick, 2000);
        return;
      }
      fn();
      timer = setTimeout(tick, wait());
    }, Math.floor(Math.random() * (jitter || 0)));
  }
  function post(path, body, method) {
    return fetch(path, {
      method: method || "POST",
      headers: { "Content-Type": "application/json", "X-Token": T },
      body: JSON.stringify(Object.assign({ g: G, u: U }, body))
    }).then(parse).catch(function (e) {
      var err = new Error(friendlyErr(e));
      err.status = e && e.status;
      throw err;
    });
  }

  /* ---------------------------------------------------------- 图表 */
  var cv = $("chart"), ctx = cv.getContext("2d");
  var W = 0, H = 0, PAD_L = 6, PAD_R = 54, PAD_T = 8, PAD_B = 16;
  var VIEW = 90;

  function resizeCanvas() {
    var r = cv.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    W = r.width; H = r.height;
    cv.width = Math.max(1, Math.round(W * dpr));
    cv.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function window_() {
    var total = S.candles.length;
    if (!total) return { bars: [], start: 0, total: 0 };
    var n = Math.min(total, VIEW);
    var end = total - S.offset;
    if (end < n) end = n;
    if (end > total) end = total;
    return { bars: S.candles.slice(end - n, end), start: end - n, total: total };
  }

  function maAt(globalIdx, period) {
    if (globalIdx < period - 1 || globalIdx >= S.candles.length) return null;
    var sum = 0;
    for (var i = globalIdx - period + 1; i <= globalIdx; i++) sum += S.candles[i].c;
    return sum / period;
  }

  function draw() {
    if (!W || !H) resizeCanvas();
    ctx.clearRect(0, 0, W, H);
    var v = window_();
    var bars = v.bars;
    if (!bars.length) {
      ctx.fillStyle = "#6e7681";
      ctx.font = "13px -apple-system,sans-serif";
      ctx.fillText("暂无行情数据", 14, H / 2);
      return;
    }
    /* MT4/MT5 的老样子：光标没落在某根 K 线上时，OHLC 栏显示最后一根；
       落在哪根就跟哪根。以前这里永远是「开 -- 高 -- 低 -- 收 --」。 */
    var ohlcBar = (S.cross !== null && S.cross >= 0 && S.cross < bars.length)
      ? bars[S.cross] : bars[bars.length - 1];
    $("ohlc").textContent =
      "开 " + px(ohlcBar.o) + "　高 " + px(ohlcBar.h) + "　" +
      "低 " + px(ohlcBar.l) + "　收 " + px(ohlcBar.c);
    var plotW = W - PAD_L - PAD_R;
    var plotH = H - PAD_T - PAD_B;
    var volH = Math.max(24, plotH * 0.22);
    var priceH = plotH - volH - 4;

    var lo = Infinity, hi = -Infinity, maxV = 1;
    for (var i = 0; i < bars.length; i++) {
      if (bars[i].l < lo) lo = bars[i].l;
      if (bars[i].h > hi) hi = bars[i].h;
      if (bars[i].v > maxV) maxV = bars[i].v;
    }
    for (var j = 0; j < bars.length; j++) {
      var gi = v.start + j;
      var a = maAt(gi, 5), b = maAt(gi, 10);
      if (a !== null) { lo = Math.min(lo, a); hi = Math.max(hi, a); }
      if (b !== null) { lo = Math.min(lo, b); hi = Math.max(hi, b); }
    }
    if (hi <= lo) hi = lo + 1;
    var span = hi - lo;
    lo -= span * 0.07; hi += span * 0.07;

    var yOf = function (p) { return PAD_T + priceH - (p - lo) / (hi - lo) * priceH; };
    var bw = plotW / bars.length;
    var xOf = function (i) { return PAD_L + (i + 0.5) * bw; };

    /* 网格 + 价格刻度 */
    ctx.font = "10px -apple-system,sans-serif";
    ctx.textBaseline = "middle";
    for (var g = 0; g <= 4; g++) {
      var yy = PAD_T + (priceH / 4) * g;
      var pv = hi - (hi - lo) * (g / 4);
      ctx.strokeStyle = "#1c2128";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD_L, yy + 0.5);
      ctx.lineTo(PAD_L + plotW, yy + 0.5);
      ctx.stroke();
      ctx.fillStyle = "#6e7681";
      ctx.textAlign = "left";
      ctx.fillText(px(pv), PAD_L + plotW + 4, yy);
    }

    /* 成交量 */
    var vTop = PAD_T + priceH + 4;
    for (var k = 0; k < bars.length; k++) {
      var b = bars[k];
      var prev = k > 0 ? bars[k - 1].c : b.o;
      var up = b.c >= prev;
      var vh = (b.v / maxV) * (volH - 6);
      ctx.fillStyle = up ? "rgba(246,70,93,.55)" : "rgba(46,189,133,.55)";
      ctx.fillRect(xOf(k) - bw * 0.28, vTop + (volH - 6) - vh, Math.max(1, bw * 0.56), vh);
    }

    /* K 线 */
    var bodyW = Math.max(1.5, bw * 0.62);
    for (var m = 0; m < bars.length; m++) {
      var d = bars[m];
      var up2 = d.c >= d.o;
      var col = up2 ? "#f6465d" : "#2ebd85";
      var x = xOf(m);
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, yOf(d.h));
      ctx.lineTo(Math.round(x) + 0.5, yOf(d.l));
      ctx.stroke();
      var y1 = yOf(d.o), y2 = yOf(d.c);
      var top = Math.min(y1, y2);
      var hgt = Math.max(1, Math.abs(y2 - y1));
      ctx.fillStyle = col;
      ctx.fillRect(x - bodyW / 2, top, bodyW, hgt);
    }

    /* 均线 */
    function drawMA(period, color) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      var started = false;
      for (var q = 0; q < bars.length; q++) {
        var val = maAt(v.start + q, period);
        if (val === null) continue;
        var xx = xOf(q), yy = yOf(val);
        if (!started) { ctx.moveTo(xx, yy); started = true; } else { ctx.lineTo(xx, yy); }
      }
      ctx.stroke();
    }
    drawMA(5, "#d9a441");
    drawMA(10, "#4c8eda");

    /* 时间刻度 */
    ctx.fillStyle = "#6e7681";
    ctx.textAlign = "center";
    /* 标签按绘图区宽度均分，首尾贴边，避免叠字或被裁掉 */
    var plotW = Math.max(60, W - PAD_L - PAD_R);
    var nLab = Math.max(2, Math.min(5, Math.floor(plotW / 82)));
    for (var i = 0; i < nLab; i++) {
      var idx = Math.round((bars.length - 1) * i / (nLab - 1));
      var lbl = tm(bars[idx].ts);
      var hw = ctx.measureText(lbl).width / 2 + 2;
      var lx = xOf(idx);
      if (i === 0) lx = Math.max(hw, lx);
      if (i === nLab - 1) lx = Math.min(W - PAD_R - hw, lx);
      ctx.fillText(lbl, lx, H - PAD_B / 2);
    }

    /* 十字光标 */
    if (S.cross !== null && S.cross >= 0 && S.cross < bars.length) {
      var cx = xOf(S.cross);
      ctx.strokeStyle = "rgba(230,237,243,.35)";
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx + 0.5, PAD_T);
      ctx.lineTo(cx + 0.5, PAD_T + priceH);
      ctx.stroke();
      ctx.setLineDash([]);
      var cd = bars[S.cross];
      var cy = yOf(cd.c);
      ctx.strokeStyle = "rgba(230,237,243,.35)";
      ctx.beginPath();
      ctx.moveTo(PAD_L, cy + 0.5);
      ctx.lineTo(PAD_L + plotW, cy + 0.5);
      ctx.stroke();
      ctx.fillStyle = "#d9a441";
      ctx.fillRect(PAD_L + plotW + 1, cy - 8, PAD_R - 3, 16);
      ctx.fillStyle = "#0d1117";
      ctx.textAlign = "center";
      ctx.fillText(px(cd.c), PAD_L + plotW + PAD_R / 2, cy);
    }
  }

  function updateCross(x) {
    var v = window_();
    if (!v.bars.length) return;
    var plotW = W - PAD_L - PAD_R;
    var bw = plotW / v.bars.length;
    var i = Math.floor((x - PAD_L) / bw);
    if (i < 0) i = 0;
    if (i > v.bars.length - 1) i = v.bars.length - 1;
    S.cross = i;
    draw();
    var d = v.bars[i];
    var gi = v.start + i;
    var m5 = maAt(gi, 5), m10 = maAt(gi, 10);
    var box = $("crossInfo");
    box.hidden = false;
    box.innerHTML =
      "<div>" + tm(d.ts) + "</div>" +
      "<div>开 " + px(d.o) + "　高 " + px(d.h) + "</div>" +
      "<div>低 " + px(d.l) + "　收 " + px(d.c) + "</div>" +
      "<div>MA5 " + (m5 === null ? "--" : px(m5)) +
      "　MA10 " + (m10 === null ? "--" : px(m10)) + "</div>" +
      "<div>量 " + fmtInt(d.v) + "</div>";
  }

  /* 触摸：拖动平移，轻点看十字 */
  var dragging = false, dragX = 0, dragStart = 0, moved = false;
  cv.addEventListener("pointerdown", function (e) {
    dragging = true; moved = false; dragX = e.clientX; dragStart = S.offset;
    try { cv.setPointerCapture(e.pointerId); } catch (err) {}
  });
  cv.addEventListener("pointermove", function (e) {
    var rect = cv.getBoundingClientRect();
    if (dragging && e.buttons) {
      var dx = e.clientX - dragX;
      if (Math.abs(dx) > 4) moved = true;
      var v = window_();
      var bw = (W - PAD_L - PAD_R) / Math.max(1, v.bars.length);
      var no = dragStart + Math.round(-dx / bw);
      var maxOff = Math.max(0, S.candles.length - Math.min(S.candles.length, VIEW));
      if (no < 0) no = 0;
      if (no > maxOff) no = maxOff;
      S.offset = no;
      draw();
    } else if (e.pointerType === "mouse") {
      /* 桌面端悬停就能看开盘（手机上手指带一定是平移） */
      updateCross(e.clientX - rect.left);
    }
  });
  cv.addEventListener("pointerup", function (e) {
    if (dragging && !moved) {
      var rect = cv.getBoundingClientRect();
      updateCross(e.clientX - rect.left);
    }
    dragging = false;
  });
  cv.addEventListener("pointercancel", function () { dragging = false; });
  cv.addEventListener("pointerleave", function () { dragging = false; });

  window.addEventListener("resize", function () { resizeCanvas(); draw(); });

  /* ---------------------------------------------------------- 渲染 */
  function renderMarketBar() {
    var bar = $("marketBar");
    if (!bar) return;
    var m = S.market;
    if (!m) { bar.hidden = true; return; }
    var parts = [];
    parts.push("🌡 恐慌指数 " + Math.round(m.fear || 50) + "（" + (m.fear_label || "平稳") + "）");
    if (m.regime) parts.push("情绪周期：" + m.regime);
    if (m.circuit) {
      parts.push("⛔ 临时休市（" + (m.circuit_reason || "市场异动") + "），约 " +
        (m.circuit_left || 0) + " 分钟后恢复开盘");
    }
    bar.textContent = parts.join("　·　");
    bar.className = "market-bar" + (m.circuit ? " circuit" : ((m.fear || 50) >= 62 ? " hot" : ""));
    bar.hidden = false;
  }

  function selectedCompany() {
    for (var i = 0; i < S.companies.length; i++) {
      if (S.companies[i].id === S.cid) return S.companies[i];
    }
    return null;
  }

  function syncSabotageBtn() {
    var b = $("btnSabotage");
    if (!b) return;
    var c = selectedCompany();
    var mine = !!(S.myCo && c && S.myCo.id === c.id);
    b.hidden = !c || mine;
    var hint = $("sabotageHint");
    if (hint) {
      hint.textContent = (!c || mine)
        ? "商业战：对别人的公司下手（有失败率，会被报复）"
        : "商业战：搞「" + c.name + "」（有失败率，会被报复）";
    }
  }

  function renderOrders() {
    var box = $("condList");
    if (!box) return;
    if (!S.orders.length) {
      box.innerHTML = '<div class="empty">还没有条件单。挂一张止损单，亏到线就自动跑。</div>';
      return;
    }
    var h = "";
    for (var i = 0; i < S.orders.length; i++) {
      var o = S.orders[i];
      var tag = o.kind === "stop_loss" ? "sl" : (o.kind === "take_profit" ? "tp" : "lp");
      var qty = o.kind === "limit"
        ? ("买/卖 " + fmtInt(o.qty) + " 股")
        : (o.qty > 0 ? ("平 " + fmtInt(o.qty) + " 股") : "全仓");
      h += '<div class="cond' + (o.status !== "active" ? " done" : "") + '">' +
        '<span class="cond-tag ' + tag + '">' + esc(o.kind_name) + "</span>" +
        '<span class="cond-main"><span class="cond-title">' + esc(o.name) + " " +
        px(o.trigger) + " " + esc(o.side_name) + "</span>" +
        '<span class="cond-sub">' + esc(qty) + "　现价 " + px(o.cur) +
        "　" + esc(o.status_name) + "</span></span>" +
        (o.status === "active"
          ? '<button class="mini-btn" data-conddel="' + o.id + '">撤单</button>'
          : '<span class="cond-tag ok">' + esc(o.status_name) + "</span>") +
        "</div>";
    }
    box.innerHTML = h;
  }

  function renderCoupons() {
    var box = $("couponList");
    if (!box) return;
    if (!S.coupons.length) { box.innerHTML = '<div class="empty">还没有道具，去「我的」页抽一个</div>'; return; }
    var h = "";
    for (var i = 0; i < S.coupons.length; i++) {
      var c = S.coupons[i];
      h += '<span class="coupon">' + esc(c.name) + " ×" + c.value + "</span>";
    }
    box.innerHTML = h;
  }

  function renderVote() {
    var box = $("voteList");
    if (!box) return;
    var st = S.vote || {};
    var items = S.voteBoard || [];
    var h = "";
    if (!items.length) {
      h = '<div class="empty">今天还没人投票</div>';
    } else {
      for (var i = 0; i < items.length; i++) {
        var v = items[i];
        h += '<div class="vote"><span class="vote-no' + (v.rank <= 3 ? " top" : "") + '">' +
          v.rank + "</span>" +
          '<span class="vote-name">' + esc(v.name) + "</span>" +
          '<span class="r-sub">' + px(v.price) + " " + pct(v.chg) + "</span>" +
          '<span class="vote-n">' + v.votes + " 票</span></div>";
      }
    }
    h += '<div class="fun-hint">' + (st.voted
      ? "你已经投过票了，明天再来（每人每天一票）"
      : "选一只你看好的人气股投一票，每票都会给它抬一点股价") + "</div>";
    box.innerHTML = h;
  }

  function renderLottery() {
    var el = $("lotteryHint");
    if (!el) return;
    var st = S.lottery || {};
    el.textContent = st.drawn
      ? "今天已经抽过啦，明天再来～（奖池：现金 / 免手续费券 / 免息券 / 打工双倍券）"
      : "奖池里有现金，也有免手续费券、免息券、打工双倍券。每天一次，免费。";
    var btn = $("btnLottery");
    if (btn) {
      btn.textContent = st.drawn ? "🎰 今天已抽过" : "🎰 抽一个";
      btn.disabled = !!st.drawn;
      btn.className = "big-btn fun" + (st.drawn ? " done" : "");
    }
  }

  function renderGuess() {
    var el = $("guessInfo");
    if (!el) return;
    var st = S.guess || {};
    var mine = st.mine ? ("你押的是「" + st.mine.name + "」") : "你还没押，选一只押上";
    el.textContent = mine + "　·　目前已涨停 " + (st.limit_up_count || 0) +
      " 家，全群 " + (st.players || 0) + " 人参与　·　收盘后结算，猜中拿 2000 元";
    var btn = $("btnGuess");
    if (btn) {
      btn.disabled = !!st.mine;
      btn.textContent = st.mine ? "已押注" : "押这一只";
    }
  }
  function renderAccount() {
    var me = S.me;
    if (!me) return;
    $("wEquity").textContent = fmtC(me.equity);
    $("wCash").textContent = fmtC(me.cash);
    $("wLoan").textContent = fmtC(me.loan);
    var u = $("wUnreal");
    u.textContent = (me.unreal > 0 ? "+" : "") + fmtC(me.unreal);
    u.className = "w-val " + cls(me.unreal);
    $("bankruptBar").hidden = !me.bankrupt;
    renderNotices();
  }

  /* v4.11 强平 / 破产 / 风险预警横幅。
     以前强平这件事只有服务端知道，网页上什么都不显示，玩家只看到仓位列表变空
     （群友原话「被强平后没持有」）。这里把服务端记下来的通知直接摆出来。 */
  function renderNotices() {
    var box = $("noticeBar");
    if (!box) return;
    var list = (S.notices || []).filter(function (n) { return n && n.text; });
    if (!list.length) { box.hidden = true; box.innerHTML = ""; return; }
    var icon = { liquidation: "⚠️", bankrupt: "💀", risk: "🔔" };
    var cls = { liquidation: "lvq", bankrupt: "bkp", risk: "rsk" };
    box.innerHTML = list.map(function (n) {
      return '<div class="notice-item ' + (cls[n.kind] || "") + '">'
        + '<span class="notice-ic">' + (icon[n.kind] || "📣") + "</span>"
        + '<span class="notice-tx"></span>'
        + '<span class="notice-ts">' + tm(n.ts) + "</span></div>";
    }).join("");
    // 文本走 textContent，别拼 HTML（通知里有 % 和数字，拼 HTML 容易出错）
    var txs = box.querySelectorAll(".notice-tx");
    for (var i = 0; i < txs.length && i < list.length; i++) {
      txs[i].textContent = list[i].text;
    }
    box.hidden = false;
  }

  function renderQuote() {
    var c = null;
    for (var i = 0; i < S.companies.length; i++) {
      if (S.companies[i].id === S.cid) { c = S.companies[i]; break; }
    }
    if (!c) return;
    $("cName").textContent = c.name;
    $("cSub").textContent = c.ticker + " · " + c.industry + " · 老板 " + (c.nick || c.qq);
    $("cLast").textContent = px(c.price);
    var e = $("cChg");
    e.textContent = pct(c.chg) + "  (" + px(c.prev_close) + ")";
    e.className = "c-chg " + cls(c.chg);
  }

  function renderStocks() {
    var box = $("stockList");
    var q = (S.q || "").trim().toLowerCase();
    var list = S.companies;
    if (q) {
      list = [];
      for (var j = 0; j < S.companies.length; j++) {
        var x = S.companies[j];
        if ((x.name || "").toLowerCase().indexOf(q) >= 0 ||
            (x.ticker || "").toLowerCase().indexOf(q) >= 0 ||
            (x.industry || "").toLowerCase().indexOf(q) >= 0) list.push(x);
      }
    }
    if (!list.length) {
      box.innerHTML = S.companies.length
        ? '<div class="empty">没有匹配的公司</div>'
        : '<div class="empty">群里还没有人上市</div>';
      return;
    }
    var html = "";
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      html += '<div class="stock' + (c.id === S.cid ? " sel" : "") + '" data-cid="' + c.id + '">' +
        '<div class="s-left"><div class="s-name">' + esc(c.name) + "</div>" +
        '<div class="s-sub">' + esc(c.ticker) + " · " + esc(c.industry) + "</div></div>" +
        '<div class="s-right"><div class="s-price ' + cls(c.chg) + '">' + px(c.price) + "</div>" +
        '<div class="s-chg ' + cls(c.chg) + '">' + pct(c.chg) + "</div></div></div>";
    }
    box.innerHTML = html;
  }

  function renderPositions() {
    var box = $("posList");
    var ps = (S.me && S.me.positions) || [];
    if (!ps.length) { box.innerHTML = '<div class="empty">还没有持仓，去行情页开一单吧</div>'; }
    else {
      var html = "";
      for (var i = 0; i < ps.length; i++) {
        var p = ps[i];
        html += '<div class="pos">' +
          '<div class="pos-top"><span>' + esc(p.name) + "</span>" +
          '<span class="' + cls(p.pl) + '">' + (p.side === "long" ? "多" : "空") + " " + fmtInt(p.qty) + " @" + px(p.entry) + "</span></div>" +
          '<div class="pos-meta">杠杆 ' + fmtInt(p.leverage) + "x　保证金 " + fmt(p.margin) +
          "　现价 " + px(p.price) + "　浮动盈亏 <b class=\"" + cls(p.pl) + "\">" +
          (p.pl > 0 ? "+" : "") + fmt(p.pl) + "（" + pct(p.pl_pct) + "）</b></div>" +
          '<button class="mini-btn" data-close="' + p.id + '">平仓</button></div>';
      }
      box.innerHTML = html;
    }

    var tb = $("tradeList");
    if (!S.trades.length) { tb.innerHTML = '<div class="empty">暂无交易记录</div>'; }
    else {
      var h = "";
      for (var j = 0; j < S.trades.length; j++) {
        var t = S.trades[j];
        var kind = t.kind === "open" ? "开仓" : (t.kind === "force_close" ? "强平" : "平仓");
        var side = t.side === "long" ? "买入" : "卖出";
        h += '<div class="ledger"><span>' + tm(t.ts) + " " + kind + " " + side + " " +
          esc(t.name) + " " + fmtInt(t.qty) + " @" + px(t.price) + "</span>" +
          '<span class="' + (t.pl ? cls(t.pl) : "flat") + '">' + (t.pl ? (t.pl > 0 ? "+" : "") + fmt(t.pl) : "—") + "</span></div>";
      }
      tb.innerHTML = h;
    }
  }

  function renderNews() {
    var box = $("newsList");
    if (!S.news.length) { box.innerHTML = '<div class="empty">暂无快讯</div>'; return; }
    var h = "";
    for (var i = 0; i < S.news.length; i++) {
      var n = S.news[i];
      h += '<div class="news"><div class="news-t">' + esc(n.title) + "</div>" +
        (n.body ? '<div class="news-b">' + esc(n.body) + "</div>" : "") +
        '<div class="news-m">' + tm(n.ts) + " · " + esc(n.name || n.ticker) + " · 冲击 " +
        '<span class="' + cls(n.impact) + '">' + pct(n.impact) + "</span></div></div>";
    }
    box.innerHTML = h;
  }

  var BOARD_TITLE = { rich: "群内富豪榜", profit: "群内盈亏榜", hot: "今日涨幅榜", limit: "今日涨停榜", lose: "出局榜 · 亏钱榜", rob: "打土豪战果榜", lux: "挥霍消费榜" };

  function renderBoard() {
    var box = $("rankList");
    var title = $("boardTitle");
    if (title) title.textContent = BOARD_TITLE[S.boardKind] || "群内富豪榜";
    if (!S.board.length) {
      box.innerHTML = '<div class="empty">暂时还没有数据</div>';
      return;
    }
    var h = "";
    for (var i = 0; i < S.board.length; i++) {
      var r = S.board[i];
      if (S.boardKind === "hot" || S.boardKind === "limit") {
        h += '<div class="rank"><span class="r-no' + (r.rank <= 3 ? " top" : "") + '">' + r.rank + "</span>" +
          '<span class="r-name">' + esc(r.name) + '<span class="r-sub">' + esc(r.ticker) + " · " + esc(r.industry) + "</span></span>" +
          '<span class="r-val ' + cls(r.chg) + '">' + px(r.price) + '<span class="r-sub">' + pct(r.chg) + "</span></span></div>";
      } else if (S.boardKind === "lose") {
        h += '<div class="rank"><span class="r-no' + (r.rank <= 3 ? " top" : "") + '">' + r.rank + "</span>" +
          '<span class="r-name">' + esc(r.nick || r.qq) + (r.bankrupt ? "（破产出局）" : "") + "</span>" +
          '<span class="r-val ' + cls(r.pl) + '">' + fmt(r.equity) +
          '<span class="r-sub">亏 ' + fmt(r.loss) + "</span></span></div>";
      } else if (S.boardKind === "rob" || S.boardKind === "lux") {
        h += '<div class="rank"><span class="r-no' + (r.rank <= 3 ? " top" : "") + '">' + r.rank + "</span>" +
          '<span class="r-name">' + esc(r.nick || r.qq) + "</span>" +
          '<span class="r-val">' + fmtInt(r.amount || 0) +
          '<span class="r-sub">' + (S.boardKind === "rob" ? "抢到" : "花了") + "</span></span></div>";
      } else {
        h += '<div class="rank"><span class="r-no' + (r.rank <= 3 ? " top" : "") + '">' + r.rank + "</span>" +
          '<span class="r-name">' + esc(r.nick || r.qq) + (r.bankrupt ? "（破产）" : "") + "</span>" +
          '<span class="r-val ' + cls(r.pl) + '">' + fmt(r.equity) +
          '<span class="r-sub">' + (r.pl > 0 ? "+" : "") + fmt(r.pl) + "</span></span></div>";
      }
    }
    box.innerHTML = h;
  }

  function renderCheckin() {
    var ci = S.checkin || { streak: 0, best: 0, total: 0, done: false, bonus_in: 7 };
    if ($("ciStreak")) $("ciStreak").textContent = "连签 " + ci.streak + " 天";
    if ($("ciBest")) $("ciBest").textContent = "最长 " + ci.best + " 天 · 累计 " + ci.total + " 天";
    var bar = $("ciBar");
    if (bar) {
      var pctv = Math.min(100, (ci.streak % 7 || (ci.streak ? 7 : 0)) / 7 * 100);
      bar.innerHTML = "<i style='width:" + pctv + "%'></i>";
    }
    if ($("ciTip")) {
      $("ciTip").textContent = ci.done
        ? "今天已签到，明天再来（再连签 " + ci.bonus_in + " 天触发大奖）"
        : "今天还没签，连签满 7 天有大奖（还差 " + (7 - (ci.streak % 7)) + " 天）";
    }
    var b = $("btnCheckin");
    if (b) {
      b.disabled = !!ci.done;
      b.textContent = ci.done ? "今日已签到 ✓" : "立即签到";
      b.classList.toggle("done", !!ci.done);
    }
  }

  function renderAch() {
    var box = $("achGrid");
    if (!box) return;
    if (!S.ach.length) { box.innerHTML = '<div class="empty">还没有成就</div>'; return; }
    var h = "";
    for (var i = 0; i < S.ach.length; i++) {
      var a = S.ach[i];
      h += '<div class="ach' + (a.got ? " got" : "") + '">' +
        '<div class="ach-name">' + esc(a.name) + "</div>" +
        '<div class="ach-desc">' + esc(a.desc) + "</div>" +
        '<div class="ach-reward">' + fmtInt(a.reward) + " 元</div></div>";
    }
    box.innerHTML = h;
  }

  function renderReminders() {
    var box = $("remList");
    if (!box) return;
    if (!S.reminders.length) { box.innerHTML = '<div class="empty">还没有设置提醒</div>'; return; }
    var h = "";
    for (var i = 0; i < S.reminders.length; i++) {
      var r = S.reminders[i];
      h += '<div class="rem' + (r.hit ? " hit" : "") + '">' +
        '<span class="rem-main">' + esc(r.name) + " " + (r.kind === "above" ? "涨到" : "跌到") +
        " " + px(r.price) + '<span class="r-sub">现价 ' + px(r.cur) + "</span></span>" +
        (r.hit ? '<span class="rem-flag">已触发</span>' : "") +
        '<button class="mini-btn" data-remdel="' + r.id + '">删</button></div>';
    }
    box.innerHTML = h;
  }

  function renderMyCo() {
    var box = $("myCoBox");
    if (!box) return;
    if (!S.myCo) { box.innerHTML = '<div class="empty">你还没有上市公司</div>'; return; }
    var c = S.myCo;
    box.innerHTML = '<div class="myco-top"><span class="myco-name">' + esc(c.name) + "</span>" +
      '<span class="r-sub">' + esc(c.ticker) + " · " + esc(c.industry) + "</span>" +
      '<span class="myco-price ' + cls(c.chg) + '">' + px(c.price) + " " + pct(c.chg) + "</span></div>" +
      '<div class="myco-acts">' +
      '<button class="mini-btn" id="btnBoost">📣 给公司做宣传</button>' +
      '<button class="mini-btn" id="btnDividend">💸 给股东派息</button>' +
      '<button class="mini-btn" id="btnRename">✏️ 改公司名</button></div>' +
      '<div class="hint">宣传 = 自己掏钱拉股价；派息 = 自己掏钱给持股群友发钱，都是人情世故。' +
      '改名要花钱（' + fmtInt(S.cfg.rename_cost || 3000) + " 元），24 小时一次。</div>";
    if ($("btnBoost")) $("btnBoost").addEventListener("click", doBoost);
    if ($("btnDividend")) $("btnDividend").addEventListener("click", doDividend);
    if ($("btnRename")) $("btnRename").addEventListener("click", doRename);
  }

  function renderRank() {
    var box = $("rankList");
    if (!S.rank.length) { box.innerHTML = '<div class="empty">群里还没有人开户</div>'; return; }
    var h = "";
    for (var i = 0; i < S.rank.length; i++) {
      var r = S.rank[i];
      h += '<div class="rank"><span class="r-no' + (r.rank <= 3 ? " top" : "") + '">' + r.rank + "</span>" +
        '<span class="r-name">' + esc(r.nick) + (r.bankrupt ? "（破产）" : "") + "</span>" +
        '<span class="r-val ' + cls(r.pl) + '">' + fmt(r.equity) + "</span></div>";
    }
    box.innerHTML = h;
  }

  function renderRecovery() {
    var card = $("recoveryCard");
    var r = S.recovery;
    if (!r) { card.hidden = true; return; }
    var show = r.bankrupt || Number(r.loan) > 0 || Number(r.tier) > 0;
    card.hidden = !show;
    if (!show) return;
    $("recEarn").textContent = fmtInt(r.earn) + " 元";
    $("recLoan").textContent = fmtInt(r.loan) + " 元";
    var next = r.next;
    var pctv = 0;
    if (next) {
      pctv = Math.min(100, Math.max(0, (Number(r.earn) / Number(next.need)) * 100));
      $("recNext").innerHTML = r.relief
        ? ("再挣 <b>" + fmtInt(next.left) + " 元</b> 解锁【" + esc(next.name) + "】：" + esc(next.desc))
        : ("下一档【" + esc(next.name) + "】还差 " + fmtInt(next.left) + " 元");
    } else {
      pctv = 100;
      $("recNext").innerHTML = r.relief
        ? "所有纾困档位都达成了，债务已一笔勾销，交易资格马上恢复"
        : "✅ 纾困计划已全部完成，重新做人吧";
    }
    $("recBar").style.width = pctv + "%";
    var tiers = r.tiers || [];
    var h = "";
    for (var i = 0; i < tiers.length; i++) {
      var t = tiers[i];
      var kls = t.done ? "done" : (i === Number(r.tier) ? "now" : "");
      var mark = t.done ? "\u2713" : String(i + 1);
      h += '<div class="rec-tier ' + kls + '"><span class="dot">' + mark + "</span><span>" +
        '<span class="t-name">' + esc(t.name) + "</span>" +
        '<span class="t-desc"> ' + esc(t.desc) + "（累计 " + fmtInt(t.need) + " 元）</span></span></div>";
    }
    $("recTiers").innerHTML = h;
  }

  function renderLedger() {
    var box = $("ledgerList");
    if (S.me) {
      $("loanInfo").innerHTML =
        "净值 <b>" + fmt(S.me.equity) + "</b>　欠款 <b>" + fmt(S.me.loan) + "</b>　日息 " +
        (S.cfg.loan_rate_per_day * 100).toFixed(3) + "%　最高可贷 " +
        fmt(Math.min(S.cfg.max_loan_abs, Math.max(0, S.me.equity) * 3));
    }
    if (!S.ledger.length) { box.innerHTML = '<div class="empty">暂无流水</div>'; return; }
    var h = "";
    for (var i = 0; i < S.ledger.length; i++) {
      var l = S.ledger[i];
      h += '<div class="ledger"><span>' + tm(l.ts) + " " + esc(l.reason) + "</span>" +
        '<span class="' + cls(l.delta) + '">' + (l.delta > 0 ? "+" : "") + fmt(l.delta) + "</span></div>";
    }
    box.innerHTML = h;
  }

  function renderCalc() {
    var c = null;
    for (var i = 0; i < S.companies.length; i++) {
      if (S.companies[i].id === S.cid) { c = S.companies[i]; break; }
    }
    var oc = $("ordCur");
    if (oc) oc.textContent = c ? ("现价 " + px(c.price)) : "现价 --";
    if (!c) { $("calc").textContent = "预计保证金 --　手续费 --"; return; }
    var qty = Number($("qtyInput").value) || 0;
    var cost = qty * c.price;
    var margin = cost / S.lev;
    var fee = cost * S.cfg.fee_rate;
    var p1 = cost * 0.01 * S.lev;
    $("calc").innerHTML = "预计保证金 <b>" + fmt(margin) + "</b>　手续费 " + fmt(fee) +
      "　涨跌 1% ≈ <b class=\"" + (S.side === "long" ? "up" : "down") + "\">" +
      (S.side === "long" ? "+" : "-") + fmt(p1) + "</b>";
    renderCoolTax();
  }

  /* 短线交易费（冷却税）：持仓越短，手续费倍数越高。
     真实市场对高频短线本来就有额外成本（A 股 T+1、券商委托频率上限、
     程序化交易差异化收费、交易所异常交易监控），这里做成看得见的阶梯。 */
  function renderCoolTax() {
    var el = $("calcTax");
    if (!el) return;
    var lad = (S.cfg && S.cfg.cooldown_fee_ladder) || [];
    if (!lad.length) { el.textContent = ""; el.hidden = true; return; }
    var parts = [];
    for (var i = 0; i < lad.length; i++) {
      var sec = Number(lad[i][0]) || 0;
      var m = Number(lad[i][1]) || 1;
      var label = sec >= 3600 ? Math.round(sec / 3600) + " 小时"
                : (sec >= 60 ? Math.round(sec / 60) + " 分钟" : sec + " 秒");
      parts.push(label + "内 ×" + m);
    }
    el.innerHTML = "短线交易费：平仓时按持仓时长加收 —— " + parts.join(" / ") +
      "。持满 30 分钟以上免收。";
    el.hidden = false;
  }

  function renderAll() {
    renderMarketBar();
    renderAccount();
    renderQuote();
    renderStocks();
    renderPositions();
    renderNews();
    renderBoard();
    renderCheckin();
    renderAch();
    renderReminders();
    renderMyCo();
    renderLedger();
    renderCalc();
    renderOrders();
    renderCoupons();
    renderVote();
    renderLottery();
    renderGuess();
    renderRecovery();
    renderTier();
    renderOnboard();
    renderQuest();
    renderJob();
    syncSabotageBtn();
    $("btnPeek").textContent = "🔮 买内幕消息（" + (S.cfg.peek_cost || 800) + " 元）";
  }

  // 自动重载后，给群友一句提示
  (function () {
    try {
      var k = "sm_reload_notice";
      if (sessionStorage.getItem(k)) {
        sessionStorage.removeItem(k);
        setTimeout(function () { toast("已更新到最新版本"); }, 800);
      }
    } catch (e) {}
  })();

  // 换群成功后整页重载，这里补一句「切到哪个群了」
  (function () {
    try {
      var n = sessionStorage.getItem("sm_switch_notice");
      if (n) {
        sessionStorage.removeItem("sm_switch_notice");
        setTimeout(function () { toast("已切到「" + n + "」"); }, 700);
      }
    } catch (e) {}
  })();

  /* ---------------------------------------------------------- 数据 */
  function checkStaticVer(serverVer) {
    // 页面比服务器上的前端旧 -> 自己重载一次。
    // sessionStorage 做闸：万一重载后还是不一致（比如 CDN 缓存），不会无限刷。
    if (!serverVer || !PAGE_VER || serverVer === PAGE_VER) return false;
    var key = "sm_reload_" + serverVer;
    try {
      if (sessionStorage.getItem(key)) return false;
      sessionStorage.setItem(key, "1");
      sessionStorage.setItem("sm_reload_notice", "1");
    } catch (e) { return false; }
    try { location.reload(); } catch (e) { return false; }
    return true;
  }

  /* 【v4.11】上一轮还没回来就别再发一条 —— 慢网络下请求会堆成山，
     服务端被打满就更容易返回空响应，形成恶性循环。 */
  var _stateBusy = false;
  function refreshState() {
    if (_stateBusy) return Promise.resolve();
    _stateBusy = true;
    return get("/api/state").then(function (d) {
      if (checkStaticVer(d.static_ver)) return;
      S.companies = d.companies || [];
      S.news = d.news || [];
      S.me = d.me;
      S.rank = d.rank || [];
      S.trades = d.trades || [];
      S.ledger = d.ledger || [];
      S.checkin = d.checkin || S.checkin;
      S.alerts = d.alerts || [];
      S.notices = d.notices || [];
      S.market = d.market || S.market;
      S.orders = d.orders || [];
      S.coupons = d.coupons || [];
      S.vote = d.vote || S.vote;
      S.lottery = d.lottery || S.lottery;
      S.guess = d.guess || S.guess;
      S.recovery = d.recovery || S.recovery;
      S.tier = d.tier || null;
      S.job = d.job || null;
      S.onboarding = d.onboarding || null;
      S.quests = d.quests || [];
      S.rise = d.rise || [];
      if (d.cfg) S.cfg = d.cfg;
      var mine = null;
      for (var i = 0; i < S.companies.length; i++) {
        if (String(S.companies[i].qq) === String(U)) { mine = S.companies[i]; break; }
      }
      S.myCo = mine;
      fillRemCid();
      fillSel("voteCid", true);
      fillSel("guessCid", true);
      if (S.cid === null && S.companies.length) S.cid = S.companies[0].id;
      renderAll();
      if (!S.changelog) loadChg();
    }).catch(function (e) {
      if (e && e.status === 403) { authExpired(); return; }
      netHint(e);
    }).then(function () {
      _stateBusy = false;
    }, function () {
      _stateBusy = false;
    });
  }

  function refreshCandles() {
    if (S.cid === null) return Promise.resolve();
    return get("/api/candles", "cid=" + S.cid + "&n=300").then(function (rows) {
      S.candles = rows || [];
      var maxOff = Math.max(0, S.candles.length - Math.min(S.candles.length, VIEW));
      if (S.offset > maxOff) S.offset = maxOff;
      draw();
    }).catch(function () {});
  }

  /* ---------------------------------------------------------- 交互 */
  function selectCompany(cid) {
    S.cid = cid;
    S.offset = 0;
    S.cross = null;
    $("crossInfo").hidden = true;
    $("ohlc").textContent = "开 --　高 --　低 --　收 --";
    renderQuote();
    renderStocks();
    renderCalc();
    syncSabotageBtn();
    refreshCandles();
  }

  function doOrder(side) {
    if (S.busy) return;
    var qty = Number($("qtyInput").value);
    if (!qty || qty <= 0) { toast("先填一下买入数量"); return; }
    S.busy = true;
    post("/api/order", { cid: S.cid, side: side, qty: qty, leverage: S.lev })
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState().then(refreshCandles);
      })
      .catch(function (e) { toast(e.message); })
      .then(function () { S.busy = false; });
  }

  function doClose(pid) {
    post("/api/close", { pid: pid })
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState().then(refreshCandles);
      })
      .catch(function (e) { toast(e.message); });
  }

  function moneyAction(kind) {
    var v = Number($("loanInput").value);
    if (!v || v <= 0) { toast("先填个金额"); return; }
    post("/api/" + kind, { amount: v })
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState();
      })
      .catch(function (e) { toast(e.message); });
  }

  function doJob() {
    post("/api/job", {})
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState();
      })
      .catch(function (e) { toast(e.message); });
  }

  function doPeek() {
    post("/api/peek", {})
      .then(function (d) {
        toast(d.msg);
        var h = $("peekHint");
        h.innerHTML = "🔮 " + esc(d.msg);
        if (d.me) S.me = d.me;
        return refreshState();
      })
      .catch(function (e) { toast(e.message); });
  }

  function doCheckin() {
    post("/api/checkin", {})
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState();
      })
      .catch(function (e) { toast(e.message); });
  }

  function doBoost() {
    if (!S.myCo) { toast("你还没有上市公司"); return; }
    post("/api/company/boost", { cid: S.myCo.id })
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState();
      })
      .catch(function (e) { toast(e.message); });
  }

  function doDividend() {
    if (!S.myCo) { toast("你还没有上市公司"); return; }
    post("/api/company/dividend", { cid: S.myCo.id })
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState();
      })
      .catch(function (e) { toast(e.message); });
  }

  function doRename() {
    if (!S.myCo) { toast("你还没有上市公司"); return; }
    var nn = window.prompt("给公司起个新名字（2~12 字，改名费 " +
      fmtInt(S.cfg.rename_cost || 3000) + " 元，24 小时一次）", S.myCo.name);
    if (nn === null) return;
    nn = String(nn).trim();
    if (!nn) return;
    post("/api/company/rename", { cid: S.myCo.id, name: nn })
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState();
      })
      .catch(function (e) { toast(e.message); });
  }

  function doSabotage() {
    var c = selectedCompany();
    if (!c) { toast("先选一只股票"); return; }
    var plan = window.prompt("对「" + c.name + "」下手。\n选择手段（留空 = 随机）：\n" +
      "放黑料 / 挖墙脚 / 做空狙击 / 举报财务造假", "");
    if (plan === null) return;
    post("/api/company/sabotage", { cid: c.id, plan: String(plan).trim() })
      .then(function (d) {
        toast(d.msg);
        if (d.me) S.me = d.me;
        return refreshState();
      })
      .catch(function (e) { toast(e.message); });
  }

  function addReminder() {
    var cid = Number($("remCid").value);
    var price = Number($("remPrice").value);
    if (!cid || !price || price <= 0) { toast("先选股票再填价格"); return; }
    post("/api/reminders", { cid: cid, kind: $("remKind").value, price: price })
      .then(function (d) {
        toast(d.msg);
        $("remPrice").value = "";
        return refreshReminders();
      })
      .catch(function (e) { toast(e.message); });
  }

  function refreshReminders() {
    return get("/api/reminders").then(function (d) {
      S.reminders = d.items || [];
      S.alerts = d.alerts || [];
      renderReminders();
    }).catch(function () {});
  }

  function refreshAch() {
    return get("/api/achievements").then(function (d) {
      S.ach = d.items || [];
      renderAch();
    }).catch(function () {});
  }

  function refreshBoard() {
    return get("/api/board", "kind=" + S.boardKind + "&n=20").then(function (d) {
      S.board = d.items || [];
      renderBoard();
    }).catch(function () {});
  }

  function showDaily() {
    get("/api/daily").then(function (d) {
      var lines = ["📅 " + d.day + " 收盘复盘",
        "全市场 " + d.total + " 家公司，涨停 " + d.limit_up + " 家，今日成交 " + d.trades + " 笔",
        "—— 涨幅榜 ——"];
      for (var i = 0; i < Math.min(5, (d.top || []).length); i++) {
        var t = d.top[i];
        lines.push((i + 1) + ". " + t.name + "(" + t.ticker + ") " + px(t.price) + " " + pct(t.chg));
      }
      lines.push("—— 富豪榜 ——");
      for (var j = 0; j < Math.min(5, (d.rank || []).length); j++) {
        var r = d.rank[j];
        lines.push((j + 1) + ". " + r.nick + " 净值 " + fmt(r.equity) + "（" + (r.pl > 0 ? "+" : "") + fmt(r.pl) + "）");
      }
      var box = $("dailyBox");
      if (box) {
        box.innerHTML = lines.map(function (x) { return esc(x); }).join("<br>");
        box.hidden = false;
      }
      toast(lines[0]);
    }).catch(function (e) { toast(e.message); });
  }

  function fillRemCid() {
    var sel = $("remCid");
    if (!sel) return;
    // 只在股票列表真的变了才重建，否则每 2 秒重刷会打断用户正在点的下拉框
    var sig = S.companies.length + ":" + (S.companies.length ? S.companies[0].id : 0) +
      ":" + (S.companies.length ? S.companies[S.companies.length - 1].id : 0);
    if (sel.getAttribute("data-sig") === sig) return;
    sel.setAttribute("data-sig", sig);
    var cur = sel.value;
    var html = "";
    for (var i = 0; i < S.companies.length; i++) {
      var c = S.companies[i];
      html += '<option value="' + c.id + '">' + esc(c.name) + " " + px(c.price) + "</option>";
    }
    sel.innerHTML = html;
    if (cur) sel.value = cur;
    else if (S.cid) sel.value = S.cid;
  }

  /* ---------------------------------------------------------- 条件单 / 趣味玩法 */
  function addCond() {
    var kind = $("condKind").value;
    var side = $("condSide").value;
    var trigger = Number($("condTrigger").value);
    var qty = Number($("condQty").value) || 0;
    if (!trigger || trigger <= 0) { toast("先填触发价"); return; }
    if (kind === "limit" && qty <= 0) { toast("限价单要填数量"); return; }
    /* 限价单是开仓单，必须说清楚买还是卖；
       止损止盈留「自动」，后端会按你在这只票上的
       持仓方向自己判断（MT4 就是把 SL/TP 挂在持仓上的）。 */
    if (kind === "limit" && !side) { toast("限价单要选买还是卖"); return; }
    var body = { cid: S.cid, kind: kind, side: side, trigger: trigger, qty: qty };
    post("/api/order_cond", body).then(function (d) {
      toast(d.msg);
      $("condTrigger").value = "";
      $("condQty").value = "";
      S.orders = d.items || S.orders;
      renderOrders();
    }).catch(function (e) { toast(e.message); });
  }

  function cancelCond(id) {
    post("/api/order_cond_cancel", { id: id }).then(function (d) {
      toast(d.msg);
      S.orders = d.items || S.orders;
      renderOrders();
    }).catch(function (e) { toast(e.message); });
  }

  function castVote() {
    var cid = Number($("voteCid").value) || S.cid;
    post("/api/vote", { cid: cid }).then(function (d) {
      toast(d.msg);
      S.voteBoard = d.board || [];
      S.vote = d.status || S.vote;
      renderVote();
    }).catch(function (e) { toast(e.message); });
  }

  function drawLottery() {
    post("/api/lottery", {}).then(function (d) {
      toast(d.msg);
      S.lottery = d.status || S.lottery;
      renderLottery();
      return refreshState();
    }).catch(function (e) { toast(e.message); });
  }

  function makeGuess() {
    var cid = Number($("guessCid").value) || S.cid;
    post("/api/guess", { cid: cid }).then(function (d) {
      toast(d.msg);
      S.guess = d.status || S.guess;
      renderGuess();
    }).catch(function (e) { toast(e.message); });
  }

  function refreshFun() {
    return get("/api/vote").then(function (d) {
      if (d && d.status) S.vote = d.status;
      if (d && d.board) S.voteBoard = d.board;
      renderVote();
    }).catch(function () {});
  }

  function fillSel(selId, keepSig) {
    var sel = $(selId);
    if (!sel) return;
    var sig = S.companies.length + ":" + (S.companies.length ? S.companies[0].id : 0) +
      ":" + (S.companies.length ? S.companies[S.companies.length - 1].id : 0);
    if (keepSig && sel.getAttribute("data-sig") === sig) return;
    sel.setAttribute("data-sig", sig);
    var cur = sel.value;
    var html = "";
    for (var i = 0; i < S.companies.length; i++) {
      var c = S.companies[i];
      html += '<option value="' + c.id + '">' + esc(c.name) + " " + px(c.price) + "</option>";
    }
    sel.innerHTML = html;
    if (cur) sel.value = cur;
    else if (S.cid) sel.value = S.cid;
  }

  var HELP_HTML = [
    "<h4>\u2460 \u8fd9\u662f\u4ec0\u4e48</h4>",
    "<p>\u7fa4\u91cc\u6bcf\u4e2a\u4eba\u90fd\u6709\u81ea\u5df1\u7684<b>\u4e0a\u5e02\u516c\u53f8</b>\uff08\u540d\u5b57 + \u884c\u4e1a\uff09\u3002",
    "\u4f60\u505a\u591a\uff08\u4e70\u5165\uff09\u770b\u597d\u7684\u516c\u53f8\uff0c\u505a\u7a7a\uff08\u5356\u51fa\uff09\u522b\u4eba\u5bb6\u7684\uff0c\u8dcc\u4e86\u7b97\u4f60\u7684\u3002",
    "\u521d\u59cb\u8d44\u91d1 <b>10 \u4e07</b>\uff0c\u53ef\u4ee5\u52a0\u6760\u6746\uff08\u6700\u9ad8 20 \u500d\uff09\uff0c\u4e5f\u53ef\u4ee5\u8d37\u6b3e\uff08\u65e5\u606f\u6309\u65f6\u95f4\u8ba1\u63d0\uff09\u3002</p>",

    "<h4>\u2461 \u4e94\u4e2a\u9875\u9762\u5404\u7ba1\u4ec0\u4e48</h4>",
    "<p><b>\u884c\u60c5</b>\uff1aK \u7ebf + \u4e0b\u5355 + \u641c\u80a1\u7968\u3002<br>",
    "<b>\u4ea4\u6613</b>\uff1a\u6301\u4ed3 / \u6761\u4ef6\u5355\uff08\u6b62\u635f\u6b62\u76c8\u9650\u4ef7\uff09/ \u4ea4\u6613\u6d41\u6c34\u3002<br>",
    "<b>\u8d44\u8baf</b>\uff1a\u8d22\u7ecf\u5feb\u8baf\uff08\u53ef\u4ee5\u82b1 800 \u4e70\u5185\u5e55\u6d88\u606f\uff09\u3002<br>",
    "<b>\u6392\u884c</b>\uff1a\u5bcc\u8c6a / \u76c8\u4e8f / \u6da8\u5e45 / \u6da8\u505c / \u4e8f\u635f / \u6253\u571f\u8c6a / \u6325\u970d\u3002<br>",
    "<b>\u6211\u7684</b>\uff1a\u6240\u6709\u65e5\u5e38\u64cd\u4f5c\u90fd\u5728\u8fd9\u91cc\uff0c\u5b88\u4f4f\u300c\u6211\u7684\u300d\u9875\u3002</p>",

    "<h4>\u2462 \u65b0\u624b\u7b2c\u4e00\u5468\u600e\u4e48\u73a9</h4>",
    "<p>1\uff09\u5148\u53bb<b>\u6211\u7684 \u2192 \u6bcf\u65e5\u7b7e\u5230</b>\u62ff\u94b1\uff1b<br>",
    "2\uff09\u5728<b>\u884c\u60c5</b>\u9875\u6311\u4e00\u53ea\u70b9\u8fdb\u53bb\uff0c\u4e70 100 \u80a1\uff08\u522b\u4e00\u4e0a\u6765\u5c31\u4e0a\u6760\u6746\uff09\uff1b<br>",
    "3\uff09\u53bb<b>\u4ea4\u6613 \u2192 \u6761\u4ef6\u5355</b>\u6302\u4e00\u4e2a\u6b62\u635f\uff0c\u8dcc\u5230\u7ebf\u81ea\u52a8\u8dd1\uff1b<br>",
    "4\uff09\u6bcf\u5929\u5192\u4e2a\u6ce1\uff08\u7b7e\u5230 / \u4ea4\u6613 / \u6253\u5de5\u90fd\u7b97\uff09\uff0c\u6d3b\u8dc3\u5ea6\u4e0a\u53bb\u4e86\u624d\u80fd\u9886\u5206\u7ea2\u3002</p>",

    "<h4>\u2463 \u6bcf\u5929\u8be5\u5e72\u7684\u4e8b</h4>",
    "<p>\u2460 <b>\u7b7e\u5230</b>\uff08\u8fde\u7b7e\u6ee1 7 \u5929\u6709\u5927\u8d4f\uff09\uff1b\u2461 <b>\u62bd\u5956</b>\uff08\u514d\u8d39\uff0c\u62bd\u73b0\u91d1\u548c\u9053\u5177\u5238\uff09\uff1b",
    "\u2462 <b>\u4efb\u52a1</b>\uff08\u505a\u5b8c\u6709\u5956\u52b1\uff1b<b>翻身任务</b>\uff08净值 20 万以下专属，真下单买、平仓、签到都能拿钱\uff09\uff1b\u2463 <b>\u4eba\u6c14\u6295\u7968</b>\uff08\u6bcf\u5929\u4e00\u7968\uff09\uff1b",
    "\u2464 <b>\u6da8\u505c\u7ade\u731c</b>\uff08\u62bc\u4e00\u53ea\uff0c\u731c\u4e2d\u62ff 2000 \u5143\uff09\u3002</p>",

    "<h4>\u2464 \u8d5a\u94b1\u7684\u8def\u5b50</h4>",
    "<p><b>\u80a1\u7968</b>\u662f\u4e3b\u4e1a\uff0c\u6ce2\u52a8\u5927\uff1b<b>\u6253\u5de5</b>\u662f\u5e94\u6025\uff0c工资按雇主身家的比例结算，每日上限按你的档位走（普通股民 150，财阀 1000），职级越高冷却越长；",
    "<b>\u4f1a\u5458\u8d39</b>\u662f\u51c0\u503c\u8d8a\u9ad8\u6bcf\u5929\u4ea4\u8d8a\u591a\uff08\u4f46\u4f60\u4e5f\u62ff\u5230\u66f4\u591a\u7279\u6743\uff09\uff1b",
    "<b>\u5168\u6c11\u5206\u7ea2</b>\u628a\u5bcc\u8c6a\u4ea4\u7684\u4f1a\u5458\u8d39\u6309\u6d3b\u8dc3\u5ea6 + \u8d2b\u56f0\u5ea6\u53d1\u56de\u7fa4\u91cc\uff0c\u8d2b\u5b97\u62ff\u5f97\u591a\uff1b",
    "<b>\u6253\u571f\u8c6a</b>\u82b1\u5c0f\u94b1\u62a2\u5bcc\u8c6a\u7684\u73b0\u91d1\uff0c\u8d8a\u5bcc\u8d8a\u597d\u62a2\uff0c\u7ffb\u8f66\u5bf9\u65b9\u53cd\u62ff 20%\uff1b",
    "<b>\u6325\u970d</b>\u82b1\u51c0\u503c\u7684 5% \u6362\u58f0\u671b\uff0c\u58f0\u671b\u8ba9\u4f60\u66f4\u6297\u62a2\uff1b",
    "<b>\u77ed\u7ebf\u4ea4\u6613\u8d39</b>\uff1a\u6301\u4ed3\u8d8a\u77ed\u624b\u7eed\u8d39\u8d8a\u9ad8\uff082 \u5206\u949f\u5185 5 \u500d\u300110 \u5206\u949f\u5185 2.5 \u500d\u300130 \u5206\u949f\u5185 1.5 \u500d\uff09\uff0c\u60f3\u7701\u624b\u7eed\u8d39\u5c31\u522b\u6765\u56de\u5012\u817e\u3002</p>",

    "<h4>\u2465 \u6761\u4ef6\u5355\uff08\u540c\u82b1\u987a\u90a3\u5957\uff09</h4>",
    "<p><b>\u6b62\u635f</b>\uff1a\u4ef7\u8dcc\u5230\u4f60\u7684\u7ebf\u5c31\u81ea\u52a8\u5e73\u4ed3\uff1b<b>\u6b62\u76c8</b>\uff1a\u4ef7\u6da8\u5230\u7ebf\u5c31\u81ea\u52a8\u843d\u888b\uff1b",
    "<b>\u65b9\u5411</b>\u9009\u300c\u81ea\u52a8\u300d\u65f6\u6309\u4f60\u7684\u6301\u4ed3\u5224\u65ad\uff08\u591a\u7a7a\u4e24\u5934\u90fd\u6301\u7740\u624d\u9700\u8981\u624b\u52a8\u9009\uff09\uff1b",
    "<b>\u9650\u4ef7</b>\uff1a\u6302\u4e00\u4e2a\u4e70/\u5356\u7684\u5355\u5b50\uff0c\u5230\u4ef7\u81ea\u52a8\u6210\u4ea4\u3002\u6570\u91cf\u7559\u7a7a = \u5168\u4ed3\u5e73\u6389\uff0c\u9650\u4ef7\u5355\u5fc5\u987b\u586b\u6570\u91cf\u3002\u89e6\u53d1\u540e\u4f1a\u5728\u7fa4\u91cc @ \u4f60\u3002</p>",

    "<h4>\u2466 \u4ef7\u683c\u63d0\u9192 \u2260 \u6761\u4ef6\u5355</h4>",
    "<p>\u63d0\u9192\u53ea\u662f\u300c\u5230\u4ef7\u558a\u4f60\u4e00\u58f0\u300d\uff0c\u4e0d\u4f1a\u81ea\u52a8\u4ea4\u6613\uff1b\u60f3\u81ea\u52a8\u4ea4\u6613\u7528\u6761\u4ef6\u5355\u3002</p>",

    "<h4>\u2467 \u4e8f\u5149\u4e86\u600e\u4e48\u529e</h4>",
    "<p>\u53ef\u4ee5\u8d37\u6b3e\u63a5\u7740\u73a9\uff0c\u4e5f\u53ef\u4ee5\u53bb\u7fa4\u53cb\u516c\u53f8<b>\u6253\u5de5</b>\u3002\u51c0\u503c\u8dcc\u7834\u7ef4\u6301\u7ebf\u4f1a\u88ab\u5f3a\u5e73\uff0c\u51c0\u503c\u53d8\u8d1f\u5c31\u7834\u4ea7\u3002",
    "<b>\u8d37\u6b3e\u770b\u7684\u662f\u300c\u51c0\u503c\u300d\u4e0d\u662f\u300c\u73b0\u91d1\u300d</b>\uff1a\u624b\u4e0a\u8fd8\u6709\u6d6e\u76c8\u5c31\u80fd\u8d37\uff0c\u8d44\u4e0d\u62b5\u503a\uff08\u51c0\u503c\u22640\uff09\u624d\u7b97\u771f\u7834\u4ea7\uff1b",
    "<b>\u7834\u4ea7\u6e05\u76d8\u540e\u6709 10 \u5929\u51b7\u9759\u671f</b>\uff1a\u4e0d\u80fd\u8d37\u6b3e\u3001\u6760\u6746\u6700\u591a 1 \u500d\uff0c\u6512\u591f\u6536\u5165\u8fd8\u80fd\u7533\u8bf7\u503a\u52a1\u91cd\u6574\u3002</p>",

    "<h4>\u2468 \u7834\u4ea7\u4e86\u4e5f\u6709\u7ffb\u8eab\u8def</h4>",
    "<p>\u53ea\u6709<b>\u8d44\u4e0d\u62b5\u503a\uff08\u51c0\u503c\u53d8\u8d1f\uff09</b>\u624d\u7b97\u7834\u4ea7\uff0c\u51c0\u503c\u4e00\u56de\u6b63\u5c31\u81ea\u52a8\u6062\u590d\u4ea4\u6613\u3002",
    "\u7834\u4ea7\u671f\u95f4<b>\u6253\u5de5\u4e0a\u9650\u7ffb\u500d\u3001\u7b7e\u5230\u5956\u52b1\u7ffb\u500d\u3001\u8d37\u6b3e\u514d\u606f</b>\u3002",
    "\u7d2f\u8ba1\u6253\u5de5\u7b49\u6536\u5165 <b>3 \u4e07</b> \u2192 \u3010\u4e34\u65f6\u6551\u52a9\u91d1\u30111 \u4e07\uff1b",
    "\u7d2f\u8ba1 <b>8 \u4e07</b> \u2192 \u3010\u53f8\u6cd5\u91cd\u6574\u3011\uff0c\u5269\u4f59\u6b20\u6b3e\u4e00\u7b14\u52fe\u9500 + 3 \u4e07\u91cd\u542f\u8d44\u91d1\u3002",
    "\u6211\u7684\u9875\u5e95\u90e8\u300c\u7834\u4ea7\u7ebe\u56f0\u300d\u80fd\u770b\u5230\u8fdb\u5ea6\u6761\u3002</p>",

    "<h4>\u2469 \u5bcc\u8c6a\u4ff1\u4e50\u90e8\uff08\u51c0\u503c\u5206\u6863\uff09</h4>",
    "<p>\u666e\u901a\u80a1\u6c11 0 \u00b7 \u5c0f\u5bcc\u4e4b\u5bb6 20 \u4e07 \u00b7 \u5927\u5bcc\u4e4b\u5bb6 50 \u4e07 \u00b7 \u8c6a\u95e8 150 \u4e07 \u00b7 \u5de8\u5bcc 500 \u4e07 \u00b7 \u8d22\u9600 1500 \u4e07\u3002",
    "\u6863\u4f4d\u8d8a\u9ad8\uff1a\u6760\u6746\u4e0a\u9650\u8d8a\u9ad8\u3001\u624b\u7eed\u8d39\u8d8a\u4f4e\u3001\u6253\u5de5\u5de5\u8d44\u8d8a\u9ad8\uff0c\u4f46\u6bcf\u5929\u8981\u4ea4<b>\u4f1a\u5458\u8d39</b>\uff080.04%~0.18%\uff09\u3002",
    "\u4f1a\u5458\u8d39\u4e0d\u662f\u7f5a\u6b3e\uff0c\u662f\u4f60\u7559\u5728\u724c\u684c\u4e0a\u7684\u95e8\u7968\u8d39\u3002</p>",

    "<h4>\u246a \u7fa4\u91cc\u7684\u6307\u4ee4\uff08\u7f51\u9875\u7aef\u4e0d\u65b9\u4fbf\u65f6\u7528\uff09</h4>",
    "<p><b>\u80a1\u7968</b>\u3001<b>\u7b7e\u5230</b>\u3001<b>\u62bd\u5956</b>\u3001<b>\u6295\u7968 \u80a1\u7968\u540d</b>\u3001<b>\u6da8\u505c\u7ade\u731c \u80a1\u7968\u540d</b>\u3001<b>\u6211\u7684\u6761\u4ef6\u5355</b>\u3001",
    "<b>\u6b62\u635f \u80a1\u7968\u540d \u4ef7\u683c</b>\u3001<b>\u6b62\u76c8 \u80a1\u7968\u540d \u4ef7\u683c</b>\u3001<b>\u9650\u4ef7 \u80a1\u7968\u540d \u4e70/\u5356 \u4ef7\u683c \u6570\u91cf</b>\u3001",
    "<b>\u5bcc\u8c6a\u699c</b>\u3001<b>\u6da8\u505c\u699c</b>\u3001<b>\u4eca\u65e5\u590d\u76d8</b>\u3001<b>\u5feb\u8baf</b>\u3001",
    "<b>\u6211\u7684\u6863\u4f4d</b>\u3001<b>\u6253\u571f\u8c6a \u6635\u79f0</b>\u3001<b>\u6325\u970d \u5bcc\u8fdb</b>\u3001<b>\u9886\u5206\u7ea2</b>\u3001<b>\u8d22\u5bcc\u5206\u5e03</b>\u3001<b>\u8d22\u9600\u57fa\u91d1\uff08\u62bc\u6da8\u62bc\u8dcc\uff09</b>\u3002</p>"
  ].join("");

  function showHelp(on) {
    var m = $("helpModal");
    if (!m) return;
    if (on === undefined) on = m.hidden;
    if (on && !$("helpBody").innerHTML) $("helpBody").innerHTML = HELP_HTML;
    m.hidden = !on;
  }
  /* ---------------------------------------------------------- 「最近改动」公告
     主人说不要在群里发公告，那就把变更写在网页上：
     顶栏一个小钮钮，点开就能看到这几天加了什么。
     第一次打开会写一个记号，之后发平台再加一条就会重新亮起来。 */
  var CHG_SEEN_KEY = "sm_chg_seen";
  function loadChg() {
    get("/api/changelog").then(function (d) {
      S.changelog = (d && d.items) || [];
      var dot = $("chgDot");
      if (!dot) return;
      var ver = S.changelog.length ? S.changelog[0].ver : "";
      var seen = "";
      try { seen = localStorage.getItem(CHG_SEEN_KEY) || ""; } catch (e) {}
      if (ver && ver !== seen) dot.hidden = false;
    }).catch(function () {});
  }
  function renderChg(items) {
    var box = $("chgBody");
    if (!box) return;
    if (!items || !items.length) {
      box.innerHTML = `<div class="empty">暂时没有更新记录</div>`;
      return;
    }
    var h = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i] || {};
      h += `<div class="chg-item"><div class="chg-head">
              <span class="chg-ver">${esc(it.ver)}</span>
              <span class="chg-title">${esc(it.title)}</span>
              <span class="chg-date">${esc(it.date)}</span></div>`;
      var list = it.items || [];
      for (var j = 0; j < list.length; j++) {
        h += `<div class="chg-line"><i></i><span>${esc(list[j])}</span></div>`;
      }
      h += `</div>`;
    }
    box.innerHTML = h;
  }
  function showChg() {
    var m = $("chgModal");
    if (!m) return;
    m.hidden = false;
    var dot = $("chgDot");
    if (dot) dot.hidden = true;
    var ver = (S.changelog && S.changelog.length) ? S.changelog[0].ver : "";
    if (ver) { try { localStorage.setItem(CHG_SEEN_KEY, ver); } catch (e) {} }
    var box = $("chgBody");
    if (!box) return;
    if (S.changelog) { renderChg(S.changelog); return; }
    box.innerHTML = `<div class="empty">加载中…</div>`;
    get("/api/changelog").then(function (d) {
      S.changelog = (d && d.items) || [];
      renderChg(S.changelog);
    }).catch(function (e) {
      box.innerHTML = `<div class="empty">${esc(friendlyErr(e))}</div>`;
    });
  }
  $("btnChg").addEventListener("click", showChg);
  $("btnChgClose").addEventListener("click", function () { $("chgModal").hidden = true; });

  /* ---------------------------------------------------------- 我的页：宫格 + 手风琴 */
  /* 【为什么要宫格】功能从 12 个涨到 18 个，如果还是一张张卡片往下堆，
     手机上要滑三四屏才够得到「领分红」。现在全部收进 4 组宫格，
     点一下才展开那一个面板，一次只开一个。 */
  var TOOLS = [
    { g: "\u6bcf\u65e5\u5fc5\u505a", items: [
      { n: "\u6bcf\u65e5\u7b7e\u5230", i: "\ud83d\udcc5", p: "checkin" },
      { n: "\u6bcf\u65e5\u62bd\u5956", i: "\ud83c\udfb0", p: "lottery" },
      { n: "\u6bcf\u65e5\u4efb\u52a1", i: "\u2705", p: "quest" },
      { n: "\u6536\u76d8\u590d\u76d8", i: "\ud83d\udcca", p: "daily" }
    ]},
    { g: "\u641e\u94b1", items: [
      { n: "\u53bb\u6253\u5de5", i: "\ud83d\udcbc", p: "job" },
      { n: "\u8d37\u6b3e\u8fd8\u6b3e", i: "\ud83d\udcb0", p: "loan" },
      { n: "\u9886\u5206\u7ea2", i: "\ud83e\udd99", p: "dividend" },
      { n: "\u8d22\u5bcc\u5206\u5e03", i: "\u2696", p: "wealth" }
    ]},
    { g: "\u73a9\u6cd5", items: [
      { n: "\u4eba\u6c14\u6295\u7968", i: "\ud83d\uddf3", p: "vote" },
      { n: "\u6da8\u505c\u7ade\u731c", i: "\ud83c\udfaf", p: "guess" },
      { n: "\u6253\u571f\u8c6a", i: "\ud83e\uddb9", p: "rob" },
      { n: "\u6325\u970d\u6d88\u8d39", i: "\ud83d\uded5", p: "lux" },
      { n: "\u8d22\u9600\u57fa\u91d1", i: "\ud83c\udfdb", p: "fund" }
    ]},
    { g: "\u6211\u7684\u8d44\u4ea7", items: [
      { n: "\u6211\u7684\u516c\u53f8", i: "\ud83c\udfe2", p: "myco" },
      { n: "\u4ef7\u683c\u63d0\u9192", i: "\ud83d\udd14", p: "remind" },
      { n: "\u8d26\u6237\u6d41\u6c34", i: "\ud83d\udcdc", p: "ledger" },
      { n: "\u6210\u5c31\u5899", i: "\ud83c\udf96", p: "ach" },
      { n: "\u6211\u7684\u9053\u5177", i: "\ud83c\udf81", p: "coupon" },
      { n: "\u767b\u5f55\u53e3\u4ee4", i: "\ud83d\udd11", p: "pwd" }
    ]}
  ];

  function buildTools() {
    var host = $("toolHost");
    if (!host) return;
    var h = "";
    for (var gi = 0; gi < TOOLS.length; gi++) {
      var g = TOOLS[gi];
      // 4 个一组的排成 2x2，6 个一组的排成 3x2 —— 不留孤儿格子
      var cols = g.items.length >= 6 ? " g3" : " g2";
      h += `<div class="tool-group"><div class="tg-title">${esc(g.g)}</div><div class="tool-grid${cols}">`;
      for (var i = 0; i < g.items.length; i++) {
        var t = g.items[i];
        h += `<button class="tool-cell" type="button" data-panel="${t.p}">
              <span class="tc-ico">${t.i}</span>
              <span class="tc-name">${esc(t.n)}</span></button>`;
      }
      h += "</div></div>";
    }
    host.innerHTML = h;
    var cells = host.querySelectorAll(".tool-cell");
    for (var k = 0; k < cells.length; k++) {
      cells[k].addEventListener("click", function () {
        togglePanel(this.getAttribute("data-panel"));
      });
    }
  }

  function closePanel() {
    if (!S.openPanel) return;
    var p = $("pnl-" + S.openPanel);
    if (p) p.hidden = true;
    var cells = document.querySelectorAll(".tool-cell");
    for (var k = 0; k < cells.length; k++) {
      if (cells[k].getAttribute("data-panel") === S.openPanel) cells[k].classList.remove("open");
    }
    S.openPanel = null;
  }

  function togglePanel(pid) {
    if (!pid) return;
    if (S.openPanel === pid) { closePanel(); return; }
    closePanel();
    var panel = $("pnl-" + pid);
    if (!panel) return;
    var cell = document.querySelector(`.tool-cell[data-panel="${pid}"]`);
    var group = (cell && cell.closest) ? cell.closest(".tool-group") : null;
    var host = $("toolHost");
    if (group && host) host.insertBefore(panel, group.nextSibling);
    panel.hidden = false;
    S.openPanel = pid;
    if (cell) cell.classList.add("open");
    loadPanel(pid);
  }

  function loadPanel(pid) {
    if (pid === "rob") loadRob();
    else if (pid === "lux") loadLux();
    else if (pid === "dividend") loadDividend();
    else if (pid === "wealth") loadWealth();
    else if (pid === "fund") loadFund();
  }

  /* ---------------------------------------------------------- 富豪经济 */
  function loadRob() {
    get("/api/tycoon/rob").then(function (d) {
      S.rob = d.st; renderRob();
    }).catch(function (e) {
      var box = $("robBox");
      if (box) box.innerHTML = `<div class="empty">${esc(e.message || "\u52a0\u8f7d\u5931\u8d25")}</div>`;
    });
  }

  function loadLux() {
    get("/api/tycoon/luxury").then(function (d) {
      S.luxury = d.st; renderLux();
    }).catch(function (e) {
      var box = $("luxBox");
      if (box) box.innerHTML = `<div class="empty">${esc(e.message || "\u52a0\u8f7d\u5931\u8d25")}</div>`;
    });
  }

  function loadDividend() {
    get("/api/tycoon/dividend").then(function (d) {
      S.dividend = d.st; renderDividend();
    }).catch(function (e) {
      var box = $("dividendBox");
      if (box) box.innerHTML = `<div class="empty">${esc(e.message || "\u52a0\u8f7d\u5931\u8d25")}</div>`;
    });
  }

  function loadWealth() {
    get("/api/tycoon/wealth").then(function (d) {
      S.wealth = d.st; renderWealth();
    }).catch(function (e) {
      var box = $("wealthBox");
      if (box) box.innerHTML = `<div class="empty">${esc(e.message || "\u52a0\u8f7d\u5931\u8d25")}</div>`;
    });
  }

  /* ---------------------------------------------------------- 财阀基金（v4.13）
     富豪的钱往哪儿去：富豪按净值比例自动出资、3 倍杠杆押全市场，
     群友可以押方向。网页这边跟群里是同一套后端。 */
  function loadFund() {
    get("/api/fund").then(function (d) {
      S.fund = d.st;
      S.fundBet = d.bet;
      S.fundBoard = d.board || [];
      S.fundBets = d.bets || [];
      renderFund();
    }).catch(function (e) {
      var box = $("fundBox");
      if (box) box.innerHTML = `<div class="empty">${esc(e.message || "\u52a0\u8f7d\u5931\u8d25")}</div>`;
    });
  }

  function renderFund() {
    var box = $("fundBox");
    if (!box) return;
    var st = S.fund;
    if (!st) { box.innerHTML = `<div class="empty">\u52a0\u8f7d\u4e2d\u2026</div>`; return; }
    var bt = S.fundBet || {};
    var h = "";
    if (S.msg.fund) h += `<div class="ty-msg">${esc(S.msg.fund).replace(/\n/g, "<br>")}</div>`;
    h += `<div class="ty-line">\u4f60\u7684\u51c0\u503c <b>${fmtInt(st.equity)}</b> \u5143</div>`;
    h += `<div class="ty-line">\u5168\u6c11\u5206\u7ea2\u6c60 <b>${fmtInt(st.pool)}</b> \u5143`
       + `\uff08\u6628\u5929\u5168\u5e02\u573a\u7b49\u6743 `
       + `<b>${st.today_return >= 0 ? "+" : ""}${st.today_return}%</b>\uff09</div>`;
    if (st.equity >= st.min_equity) {
      h += `<div class="ty-line job-tip">\u4f60\u662f\u8d22\u9600\uff1a\u6bcf\u5929\u81ea\u52a8\u628a\u51c0\u503c\u7684 `
         + `${(st.ratio * 100).toFixed(0)}%\uff08\u6700\u591a ${fmtInt(st.max)} \u5143\uff09\u62bc\u8fdb\u57fa\u91d1`
         + `\uff0c\u4eca\u5929\u5df2\u6295 ${fmtInt(st.stake_today)} \u5143\u3002</div>`;
    } else {
      h += `<div class="ty-line">\u51c0\u503c\u8fbe <b>${fmtInt(st.min_equity)}</b> \u5143\u624d\u80fd\u7b79\u8d44`
         + `\u5efa\u57fa\u91d1\uff08\u73b0\u5728 ${fmtInt(st.equity)} \u5143\uff09\u3002</div>`;
    }
    h += `<div class="fun-hint">\u57fa\u91d1 ${st.lev} \u500d\u6760\u6746\u62bc\u300c\u5168\u5e02\u573a\u7b49\u6743`
       + `\u6da8\u8dcc\u300d\uff1a\u6da8\u4e86\u5206\u3001\u8dcc\u4e86\u51fa\u8d44\u4eba\u81ea\u5df1\u62b5\u3002`
       + `\u7ba1\u7406\u8d39 ${(st.fee * 100).toFixed(0)}% \u76f4\u63a5\u8fdb\u5168\u6c11\u5206\u7ea2\u6c60\u3002</div>`;

    /* 押注区：押对了拿回本金还分盈利，押错了本金进分红池 */
    h += `<div class="ty-sub">\u62bc\u4e00\u624b</div>`;
    if (bt.side) {
      h += `<div class="ty-line">\u4eca\u5929\u5df2\u62bc <b>${esc(bt.side)}</b> `
         + `${fmtInt(bt.amount)} \u5143\uff0c\u660e\u5929\u7ed3\u7b97\u3002</div>`;
    } else {
      h += `<div class="vote-add">`
         + `<button class="mini-btn primary" type="button" data-fund="up">\u62bc\u6da8</button>`
         + `<button class="mini-btn danger" type="button" data-fund="down">\u62bc\u8dcc</button>`
         + `<span class="r-sub">\u6700\u591a ${fmtInt(bt.max || 0)} \u5143/\u6b21\uff0c\u4eca\u5929 ${bt.left || 0} \u6b21</span>`
         + `</div>`;
      h += `<div class="fun-hint">\u4f60\u62bc\u7684\u662f\u5bcc\u7684\u4ed3\u4f4d\uff1a\u62bc\u5bf9\u4e86\u62ff\u56de\u672c\u91d1`
         + `\u8fd8\u5206\u57fa\u91d1\u76c8\u5229\uff0c\u62bc\u9519\u4e86\u672c\u91d1\u76f4\u63a5\u8fdb\u5168\u6c11\u5206\u7ea2\u6c60\u3002</div>`;
    }

    if (S.fundBoard && S.fundBoard.length) {
      h += `<div class="ty-sub">\u4eca\u65e5\u51fa\u8d44\u699c</div>`;
      for (var i = 0; i < S.fundBoard.length; i++) {
        var b = S.fundBoard[i];
        h += `<div class="ty-target"><span class="ty-name">${b.rank}. ${esc(b.nick)}`
           + `<span class="r-sub">\u4eca\u5929\u6295\u7d2f ${fmtInt(b.amount)} \u5143</span></span>`
           + `<span class="ty-eq">${fmtInt(b.amount)}</span></div>`;
      }
    }
    if (S.fundBets && S.fundBets.length) {
      h += `<div class="ty-sub">\u62bc\u6ce8\u699c</div>`;
      for (var j = 0; j < S.fundBets.length; j++) {
        var bb = S.fundBets[j];
        h += `<div class="ty-target"><span class="ty-name">${bb.rank}. ${esc(bb.nick)}`
           + `<span class="r-sub">\u62bc\u4e86\u300c${esc(bb.side)}\u300d</span></span>`
           + `<span class="ty-eq">${fmtInt(bb.amount)}</span></div>`;
      }
    }
    box.innerHTML = h;
    var bs = box.querySelectorAll("[data-fund]");
    for (var k = 0; k < bs.length; k++) {
      bs[k].addEventListener("click", function () {
        betFund(this.getAttribute("data-fund") === "up" ? "\u6da8" : "\u8dcc");
      });
    }
  }

  function betFund(side) {
    post("/api/fund", { side: side }).then(function (d) {
      S.msg.fund = d.msg || "";
      toast(d.msg || "\u5df2\u62bc\u6ce8");
      return loadFund();
    }).catch(function (e) { toast(e.message); });
  }

  function doRob(target) {
    post("/api/tycoon/rob", { target: target }).then(function (d) {
      S.msg.rob = d.msg || "\u51fa\u624b\u4e86";
      toast("\u51fa\u624b\u4e86");
      loadRob();
    }).catch(function (e) { toast(e.message); });
  }

  function doLux(item) {
    post("/api/tycoon/luxury", { item: item }).then(function (d) {
      S.msg.lux = d.msg || "\u4e70\u5230\u4e86";
      toast("\u4e70\u5230\u4e86");
      loadLux();
    }).catch(function (e) { toast(e.message); });
  }

  function doClaimDividend() {
    post("/api/tycoon/dividend", {}).then(function (d) {
      S.msg.dividend = d.msg || "\u9886\u5230\u4e86";
      toast("\u5206\u7ea2\u5230\u8d26");
      loadDividend();
    }).catch(function (e) { toast(e.message); });
  }

  function renderRob() {
    var box = $("robBox");
    if (!box) return;
    var st = S.rob;
    if (!st) { box.innerHTML = `<div class="empty">\u52a0\u8f7d\u4e2d\u2026</div>`; return; }
    var h = "";
    if (S.msg.rob) h += `<div class="ty-msg">${esc(S.msg.rob).replace(/\n/g, "<br>")}</div>`;
    h += `<div class="ty-line">\u4eca\u5929\u8fd8\u80fd\u51fa\u624b <b>${st.left}</b> / ${st.limit} \u6b21</div>`;
    h += `<div class="ty-line">\u51fa\u624b\u8d39 = \u76ee\u6807\u51c0\u503c \u00d7 1%\uff08\u5c01\u9876 2000\uff09\uff1b\u6210\u529f\u62ff\u4ed6\u73b0\u91d1\u7684 1%~3%\uff08\u5c01\u9876 5 \u4e07\uff09</div>`;
    h += `<div class="ty-line">\u7ffb\u8f66\u4e86\u51fa\u624b\u8d39\u5168\u6ca1\uff0c\u5bf9\u65b9\u8fd8\u80fd\u53cd\u624b\u62ff\u8d70 20% \u5f53\u8d54\u6b3e</div>`;
    h += `<div class="ty-sub">\u73b0\u5728\u53ef\u4ee5\u4e0b\u624b\u7684\u76ee\u6807</div>`;
    if (!st.targets || !st.targets.length) {
      h += `<div class="empty">\u73b0\u5728\u6ca1\u6709\u591f\u683c\u7684\u76ee\u6807\uff08\u5bf9\u65b9\u51c0\u503c\u8981 \u2265 3 \u4e07\uff09</div>`;
    } else {
      for (var i = 0; i < st.targets.length; i++) {
        var t = st.targets[i];
        h += `<div class="ty-target">
              <span class="ty-name">${esc(t.nick)}<span class="r-sub">${esc(t.tier)} \u00b7 \u6210\u529f\u7387 ${t.rate}%${t.guarded ? " \u00b7 \u4fdd\u62a4\u671f" : ""}</span></span>
              <span class="ty-eq">${fmtInt(t.equity)}</span>
              <button class="mini-btn danger" type="button" data-rob="${esc(t.nick)}">\u62a2</button>
            </div>`;
      }
    }
    box.innerHTML = h;
    var btns = box.querySelectorAll("[data-rob]");
    for (var k = 0; k < btns.length; k++) {
      btns[k].addEventListener("click", function () { doRob(this.getAttribute("data-rob")); });
    }
  }

  function renderLux() {
    var box = $("luxBox");
    if (!box) return;
    var st = S.luxury;
    if (!st) { box.innerHTML = `<div class="empty">\u52a0\u8f7d\u4e2d\u2026</div>`; return; }
    var h = "";
    if (S.msg.lux) h += `<div class="ty-msg">${esc(S.msg.lux).replace(/\n/g, "<br>")}</div>`;
    h += `<div class="ty-line">\u51c0\u503c <b>${fmtInt(st.equity)}</b> \u5143 \u00b7 \u58f0\u671b <b>${st.rep}</b></div>`;
    h += `<div class="ty-line">\u57fa\u51c6\u4ef7 <b>${fmtInt(st.base != null ? st.base : st.price)}</b> \u5143\uff08\u51c0\u503c\u7684 5%\uff09\uff0c\u5404\u6863 ${fmtYuan(st.min_price)} ~ ${fmtYuan(st.max_price)} \u5143\u3002</div>`;
    h += `<div class="ty-line">\u58f0\u671b\u8d8a\u9ad8\u8d8a\u4e0d\u5bb9\u6613\u88ab\u4eba\u62a2\uff1b\u6863\u4f4d\u8d8a\u9ad8\u4ef7\u683c\u8d8a\u8d35\uff0c\u6bcf\u70b9\u58f0\u671b\u7684\u6027\u4ef7\u6bd4\u8d8a\u5dee\u3002</div>`;
    if (st.limit != null) {
      if (st.full) {
        h += `<div class="ty-line job-tip">\u4eca\u5929\u7684\u989d\u5ea6\u7528\u5b8c\u4e86\uff08${st.used}/${st.limit}\uff09\uff0c\u660e\u5929\u518d\u6765\u3002</div>`;
      } else {
        h += `<div class="ty-line">\u4eca\u5929\u8fd8\u80fd\u6325\u970d <b>${st.left}</b> / ${st.limit} \u6b21</div>`;
      }
    }
    h += `<div class="ty-sub">\u6392\u9762\u6e05\u5355</div>`;
    for (var i = 0; i < st.items.length; i++) {
      var it = st.items[i];
      h += `<div class="ty-target">
              <span class="ty-name">${it.emoji} ${esc(it.name)}<span class="r-sub">${esc(it.tier || "")} \u00b7 \u58f0\u671b +${it.rep} \u00b7 ${esc(it.desc || "")}</span></span>
              <span class="ty-eq">${fmtYuan(it.price)}</span>
              <button class="mini-btn" type="button" data-lux="${esc(it.name)}"${(st.full || (st.cash != null && it.price > st.cash)) ? " disabled" : ""}>${st.full ? "\u4eca\u5929\u6ca1\u989d\u5ea6\u4e86" : ((st.cash != null && it.price > st.cash) ? "\u94b1\u4e0d\u591f" : "\u4e70")}</button>
            </div>`;
    }
    box.innerHTML = h;
    var btns = box.querySelectorAll("[data-lux]");
    for (var k = 0; k < btns.length; k++) {
      btns[k].addEventListener("click", function () { doLux(this.getAttribute("data-lux")); });
    }
  }

  function renderDividend() {
    var box = $("dividendBox");
    if (!box) return;
    var st = S.dividend;
    if (!st) { box.innerHTML = `<div class="empty">\u52a0\u8f7d\u4e2d\u2026</div>`; return; }
    var h = "";
    if (S.msg.dividend) h += `<div class="ty-msg">${esc(S.msg.dividend).replace(/\n/g, "<br>")}</div>`;
    h += `<div class="ty-line">\u5206\u7ea2\u6c60\u4f59\u989d <b>${fmtInt(st.pool)}</b> \u5143\uff08\u5bcc\u8c6a\u4f1a\u5458\u8d39 + \u6325\u970d + \u7f5a\u91d1\u6512\u7684\uff09</div>`;
    h += `<div class="ty-line">\u4f60\u4eca\u5929\u53ef\u4ee5\u9886 <b>${fmt(st.amount)}</b> \u5143\uff08\u8fde\u7740 ${st.streak} \u5929\u6d3b\u8dc3\u62ff\u5f97\u66f4\u591a\uff09</div>`;
    h += `<div class="ty-line">${st.claimed ? "\u2705 \u4eca\u5929\u5df2\u7ecf\u9886\u8fc7\u4e86" :
      (st.active ? "\u4f60\u4eca\u5929\u6d3b\u8dc3\u8fc7\uff0c\u53ef\u4ee5\u9886" :
       "\u4f60\u4eca\u5929\u8fd8\u6ca1\u5192\u8fc7\u6ce1\uff08\u7b7e\u5230 / \u6253\u5de5 / \u4ea4\u6613\u90fd\u7b97\uff09")}</div>`;
    box.innerHTML = h;
    var btn = $("btnClaimDividend");
    if (btn) {
      btn.disabled = !!(st.claimed || !st.active);
      btn.textContent = st.claimed ? "\u4eca\u65e5\u5df2\u9886\u53d6" : "\ud83e\udd99 \u9886\u53d6\u5206\u7ea2";
    }
  }

  function renderWealth() {
    var box = $("wealthBox");
    if (!box) return;
    var st = S.wealth;
    if (!st) { box.innerHTML = `<div class="empty">\u52a0\u8f7d\u4e2d\u2026</div>`; return; }
    var h = `<div class="ty-line">${st.n} \u4eba\uff0c\u51c0\u8d44\u4ea7\u5408\u8ba1 <b>${fmtInt(st.total)}</b> \u5143</div>`;
    h += `<div class="ty-line">\u4e2d\u4f4d\u6570 <b>${fmtInt(st.median)}</b> \u5143</div>`;
    h += `<div class="ty-line">\u524d 10% \u7684\u4eba\u5360 <b>${st.top10_share}%</b> \u7684\u94b1\uff1b\u540e 50% \u7684\u4eba\u5360 <b>${st.bottom50_share}%</b></div>`;
    h += `<div class="ty-line">\u73b0\u91d1 <b>${fmtInt(st.cash)}</b> \u5143\uff0c\u5176\u4e2d <b>${fmtInt(st.frozen)}</b> \u51bb\u5728\u7834\u4ea7\u6237\u91cc</div>`;
    h += `<div class="ty-line">\u516c\u5171\u5206\u7ea2\u6c60 <b>${fmtInt(st.pool)}</b> \u5143</div>`;
    box.innerHTML = h;
  }

  /* ---------------------------------------------------------- 档位 / 新手引导 */
  function renderTier() {
    var badge = $("tierBadge");
    if (!badge) return;
    var st = S.tier;
    var hint = $("tierHint");
    var perk = $("tierPerk");
    var nextBox = $("tierNext");
    if (!st) {
      badge.textContent = "\u672a\u5f00\u6237";
      badge.className = "tier-badge";
      if (hint) hint.textContent = "\u5728\u7fa4\u91cc\u53d1\u4e00\u6b21\u300c\u80a1\u7968\u300d\u5c31\u80fd\u5f00\u6237";
      if (perk) perk.innerHTML = "";
      if (nextBox) nextBox.hidden = true;
      return;
    }
    badge.textContent = st.tier;
    badge.className = "tier-badge t-" + st.tier_key;
    if (hint) {
      if (st.fee_rate > 0) {
        hint.innerHTML = `\u6bcf\u65e5\u4f1a\u5458\u8d39 <b>${fmt(st.fee)}</b> \u5143\uff08${(st.fee_rate * 100).toFixed(2)}%\uff09`;
      } else {
        hint.textContent = "\u4e0d\u7528\u4ea4\u4f1a\u5458\u8d39";
      }
    }
    /* \u7279\u6743\u6807\u7b7e\uff1a\u53ea\u653e\u9876\u680f\u6ca1\u6709\u7684\u4fe1\u606f */
    if (perk) {
      var chips = [`<span class="pk">\u6760\u6746 ${st.lev_cap}x</span>`];
      if (st.fee_disc > 0) chips.push(`<span class="pk">\u624b\u7eed\u8d39 -${Math.round(st.fee_disc * 100)}%</span>`);
      if (st.wage_bonus > 0) chips.push(`<span class="pk">\u6253\u5de5 +${Math.round(st.wage_bonus * 100)}%</span>`);
      chips.push(`<span class="pk">\u88ab\u6253\u6210\u529f\u7387 ${Math.round(st.rob_rate * 100)}%</span>`);
      chips.push(`<span class="pk">\u58f0\u671b ${st.rep}</span>`);
      perk.innerHTML = chips.join("");
    }
    /* \u79bb\u4e0b\u4e00\u6863\u8fd8\u5dee\u591a\u5c11 \u2014\u2014 \u7ed9\u4e2a\u8fdb\u5ea6\u6761\uff0c\u6bd4\u4e00\u53e5\u300c\u8fd8\u5dee 18 \u4e07\u300d\u6709\u671b\u5934 */
    if (nextBox) {
      if (st.next_tier) {
        var base = Number(st.tier_min || 0);
        var goal = Number(st.next_min || 0);
        var eq = Number(st.equity || 0);
        var pv = goal > base ? Math.max(0, Math.min(100, (eq - base) / (goal - base) * 100)) : 0;
        nextBox.hidden = false;
        $("tierNextTxt").textContent = "\u8ddd" + st.next_tier + "\u8fd8\u5dee";
        $("tierNextGap").textContent = fmtInt(Math.max(0, goal - eq)) + " \u5143";
        $("tierBar").style.width = pv.toFixed(1) + "%";
      } else {
        nextBox.hidden = false;
        $("tierNextTxt").textContent = "\u5df2\u662f\u9876\u914d\u8d22\u9622\uff0c\u5168\u7fa4\u6ca1\u4eba\u6bd4\u4f60\u66f4\u96be\u62a2";
        $("tierNextGap").textContent = "";
        $("tierBar").style.width = "100%";
      }
    }
  }

  function renderOnboard() {
    var card = $("onboardCard");
    var list = $("obList");
    if (!card || !list) return;
    var ob = S.onboarding;
    if (!ob || !ob.steps || !ob.steps.length) { card.hidden = true; return; }
    card.hidden = false;
    var sub = $("onboardSub");
    if (sub) sub.textContent = ob.done + " / " + ob.total + " \u5df2\u5b8c\u6210";
    /* \u5df2\u5b8c\u6210\u7684\u6b65\u9aa4\u6298\u53e0\u6210\u4e00\u884c\u5c0f\u5b57\uff0c\u53ea\u8be6\u7ec6\u5c55\u793a\u8fd8\u6ca1\u5b8c\u6210\u7684\uff1a
       \u5426\u5219\u65b0\u53f7\u8fdb\u6765\u5c31\u662f\u4e00\u5927\u724c\u5b57\u5806\u3001\u8981\u6eda\u4e09\u5c4f\u624d\u770b\u5230\u7b2c\u4e00\u4e2a\u6309\u94ae\u3002 */
    var doneNames = [], undone = [];
    for (var i = 0; i < ob.steps.length; i++) {
      var s = ob.steps[i];
      if (s.done) doneNames.push(s.title);
      else undone.push({ s: s });
    }
    var pv = ob.total ? Math.round(ob.done / ob.total * 100) : 0;
    var h = `<div class="ob-bar"><div class="ob-bar-in" style="width:${pv}%"></div></div>`;
    if (doneNames.length) {
      h += `<div class="ob-done"><b>\u2713 \u5df2\u5b8c\u6210</b> ${esc(doneNames.join("\u3001"))}</div>`;
    }
    for (var k = 0; k < undone.length; k++) {
      var u = undone[k].s;
      h += `<button class="ob-step${k === 0 ? " next" : ""}" type="button" data-otab="${esc(u.tab)}" data-opanel="${esc(u.panel || "")}">
              <span class="ob-dot">${k + 1}</span>
              <span class="ob-main"><span class="ob-title">${esc(u.title)}</span>
              <span class="ob-desc">${esc(u.desc)}</span></span>
              <span class="ob-go">\u53bb</span></button>`;
    }
    if (ob.finished) {
      h += `<div class="ob-finish">\ud83c\udf89 \u65b0\u624b\u5df2\u901a\u5173\u3002\u53bb\u300c\u6392\u884c\u300d\u770b\u770b\u4f60\u6392\u7b2c\u51e0\uff0c\u6216\u8005\u53bb\u300c\u6211\u7684\u300d\u9886\u7b2c\u4e00\u7b14\u5206\u7ea2\u3002</div>`;
    }
    list.innerHTML = h;
    var btns = list.querySelectorAll(".ob-step");
    for (var b = 0; b < btns.length; b++) {
      btns[b].addEventListener("click", function () {
        showTab(this.getAttribute("data-otab"));
        var p = this.getAttribute("data-opanel");
        if (p) setTimeout(function () { togglePanel(p); }, 80);
      });
    }
  }

  function renderQuest() {
    var box = $("questList");
    if (!box) return;
    var list = S.quests || [];
    var rise = S.rise || [];
    if (!list.length && !rise.length) { box.innerHTML = `<div class="empty">\u4eca\u5929\u6ca1\u6709\u4efb\u52a1</div>`; return; }
    var h = "";
    for (var i = 0; i < list.length; i++) {
      var q = list[i];
      h += `<div class="quest${q.done ? " done" : ""}">
              <span class="q-dot">${q.done ? "\u2713" : "\u25cb"}</span>
              <span class="q-main"><span class="q-name">${esc(q.name)}</span>
              <span class="q-desc">${esc(q.desc)}</span></span>
              <span class="q-reward">${fmtInt(q.reward)} \u5143</span></div>`;
    }
    if (rise.length) {
      h += `<div class="quest-sec">🚀 翻身任务 · 中下层专属</div>`;
      for (var j = 0; j < rise.length; j++) {
        var r = rise[j];
        h += `<div class="quest rise${r.done ? " done" : ""}">
                <span class="q-dot">${r.done ? "\u2713" : "\u25cb"}</span>
                <span class="q-main"><span class="q-name">${esc(r.name)}</span>
                <span class="q-desc">${esc(r.desc)}</span></span>
                <span class="q-reward">${fmtInt(r.reward)} \u5143</span></div>`;
      }
    }
    box.innerHTML = h;
  }

  /* 打工面板：日上限按档位走，直接把真实数字摆出来，别再写死「封顶 2000」 */
  function renderJob() {
    var box = $("jobStat");
    if (!box) return;
    var st = S.job;
    if (!st) { box.innerHTML = ""; return; }
    var pct = st.cap > 0 ? Math.min(100, st.earned / st.cap * 100) : 0;
    var h = "";
    h += `<div class="ty-line">💼 当前职级 <b>${esc(st.level)}</b> · 档位 <b>${esc(st.tier)}</b> · 每日上限 <b>${fmtInt(st.cap)}</b> 元</div>`;
    h += `<div class="ty-line">今日已挣 <b>${fmtInt(st.earned)}</b> / ${fmtInt(st.cap)} 元 · 还剩 <b>${fmtInt(st.left)}</b> 元</div>`;
    h += `<div class="job-bar"><i style="width:${pct.toFixed(1)}%"></i></div>`;
    if (st.bankrupt) {
      h += `<div class="ty-line job-tip">🆘 破产纾困中：打工上限 ${fmtInt(st.cap)} 元，签到翻倍、贷款免息。</div>`;
    } else if (st.full) {
      h += `<div class="ty-line job-tip">今天已经挣满了，明天再来 —— 想赚大钱只能去炒股。</div>`;
    } else if (st.wait > 0) {
      h += `<div class="ty-line job-tip">⏳ 老板让你歇会儿，<b>${st.wait}</b> 秒后再来。</div>`;
    } else {
      h += `<div class="ty-line job-tip">工资按雇主身家的比例结算，职级越高拿得越多，但冷却也更久。</div>`;
    }
    if (S.rise && S.rise.length) {
      h += `<div class="ty-line job-tip rise-tip">💡 你有 ${S.rise.length} 个翻身任务：在「每日任务」里真的下单买、平仓、签到都能拿钱。</div>`;
    }
    box.innerHTML = h;
    var btn = $("btnJob");
    if (btn) {
      btn.classList.toggle("done", !!st.full);
      btn.textContent = st.full ? "今天已经打工挣满了" : (st.wait > 0 ? ("冷却中 · 还剩 " + st.wait + " 秒") : "现在去打一份工");
    }
  }

  /* 事件绑定 */
  function showTab(name) {
    if (!name) return;
    document.querySelectorAll(".tab").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-tab") === name);
    });
    S.tab = name;
    document.querySelectorAll(".pane").forEach(function (p) { p.classList.remove("active"); });
    var pane = $("pane-" + name);
    if (pane) pane.classList.add("active");
    window.scrollTo(0, 0);
  }
  document.querySelectorAll(".tab").forEach(function (btn) {
    btn.addEventListener("click", function () { showTab(btn.getAttribute("data-tab")); });
  });

  document.querySelectorAll(".seg-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".seg-btn").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      S.side = btn.getAttribute("data-side");
      renderCalc();
    });
  });

  $("levRange").addEventListener("input", function () {
    S.lev = Number(this.value);
    $("levVal").textContent = S.lev + "x";
    renderCalc();
  });

  $("qtyInput").addEventListener("input", renderCalc);

  document.querySelectorAll("[data-qty]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var mode = btn.getAttribute("data-qty");
      var c = null;
      for (var i = 0; i < S.companies.length; i++) {
        if (S.companies[i].id === S.cid) { c = S.companies[i]; break; }
      }
      if (!c) return;
      if (mode === "all") {
        var max = Math.floor((S.me ? S.me.cash : 0) * S.lev / c.price / 100) * 100;
        $("qtyInput").value = Math.max(0, max);
      } else {
        $("qtyInput").value = mode;
      }
      renderCalc();
    });
  });

  $("btnLong").addEventListener("click", function () { doOrder("long"); });
  $("btnShort").addEventListener("click", function () { doOrder("short"); });
  $("btnBorrow").addEventListener("click", function () { moneyAction("borrow"); });
  $("btnRepay").addEventListener("click", function () { moneyAction("repay"); });
  $("btnJob").addEventListener("click", doJob);
  $("btnPeek").addEventListener("click", doPeek);
  $("btnCheckin").addEventListener("click", doCheckin);
  $("btnRemAdd").addEventListener("click", addReminder);
  $("btnDaily").addEventListener("click", showDaily);
  document.querySelectorAll(".subtab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".subtab").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      S.boardKind = btn.getAttribute("data-board");
      refreshBoard();
    });
  });
  $("remList").addEventListener("click", function (e) {
    var el = e.target.closest ? e.target.closest("[data-remdel]") : null;
    if (!el) return;
    post("/api/reminders", { id: Number(el.getAttribute("data-remdel")) }, "DELETE")
      .then(function () { toast("已删除"); return refreshReminders(); })
      .catch(function (err) { toast(err.message); });
  });
  $("btnLatest").addEventListener("click", function () {
    S.offset = 0; S.cross = null;
    $("crossInfo").hidden = true;
    draw();
  });

  $("stockList").addEventListener("click", function (e) {
    var el = e.target.closest ? e.target.closest("[data-cid]") : null;
    if (el) selectCompany(Number(el.getAttribute("data-cid")));
  });

  $("stockSearch").addEventListener("input", function (e) {
    S.q = e.target.value || "";
    renderStocks();
  });
  $("btnClearSearch").addEventListener("click", function () {
    S.q = "";
    $("stockSearch").value = "";
    renderStocks();
  });
  $("btnSabotage").addEventListener("click", doSabotage);
  $("btnCondAdd").addEventListener("click", addCond);
  $("btnVote").addEventListener("click", castVote);
  $("btnLottery").addEventListener("click", drawLottery);
  $("btnGuess").addEventListener("click", makeGuess);
  $("btnClaimDividend").addEventListener("click", doClaimDividend);
  $("btnHelp").addEventListener("click", function () { showHelp(true); });
  $("btnHelpClose").addEventListener("click", function () { showHelp(false); });
  $("helpModal").addEventListener("click", function (e) {
    if (e.target === this) showHelp(false);
  });
  $("condList").addEventListener("click", function (e) {
    var el = e.target.closest ? e.target.closest("[data-conddel]") : null;
    if (el) cancelCond(Number(el.getAttribute("data-conddel")));
  });

  $("posList").addEventListener("click", function (e) {
    var el = e.target.closest ? e.target.closest("[data-close]") : null;
    if (el) doClose(Number(el.getAttribute("data-close")));
  });

  document.querySelectorAll("[data-act=job]").forEach(function (btn) {
    btn.addEventListener("click", doJob);
  });

  var lo = $("btnLogout");
  if (lo) {
    lo.addEventListener("click", function () {
      clearAuth();
      location.href = "/";
    });
  }

  function doSetPwd() {
    var v = ($("pwdInput").value || "").trim();
    if (v.length < 4 || v.length > 32) { toast("口令要 4~32 位"); return; }
    post("/api/passcode", { pwd: v })
      .then(function () {
        $("pwdInput").value = "";
        $("pwdInfo").textContent = "当前：已设置固定口令（以后直接用这个口令登录，不用再找布莱泽领）";
        toast("口令已设置");
      })
      .catch(function (err) { toast(err.message); });
  }
  $("btnSetPwd").addEventListener("click", doSetPwd);

  /* ---------------------------------------------------------- 换群 */
  /* 一个 QQ 常常同时在好几个群里炒股。以前网页只认链接里那一个群，
     想看另一个群的账户只能回群里再发一次「炒股」拿新链接，太绕。
     现在服务端给一个「换群」下拉：点一下，签一个目标群的新令牌就行。
     令牌是按「群 + 用户」签的，所以换群必须换令牌 —— 不能只改个群号糊弄过去。 */
  function loadGroups() {
    return get("/api/g/mygroups").then(function (d) {
      S.groups = (d && d.groups) || [];
      renderGroupBar();
      return S.groups;
    }).catch(function () {
      S.groups = [];
      renderGroupBar();
      return [];
    });
  }

  function currentGroup() {
    for (var i = 0; i < S.groups.length; i++) {
      if (S.groups[i] && S.groups[i].cur) return S.groups[i];
    }
    return null;
  }

  function renderGroupBar() {
    var bar = $("groupBar");
    if (!bar) return;
    var cur = currentGroup();
    if (!cur) { bar.hidden = true; return; }
    var hasName = !!(cur.name && cur.name.length);
    $("gName").textContent = hasName ? cur.name : ("群 " + cur.gid);
    $("gGid").textContent = hasName ? cur.gid : "";
    // 只有一个群就不显示了 —— 没必要占地方
    bar.hidden = S.groups.length <= 1;
  }

  function renderGroupList() {
    var box = $("grpBody");
    if (!box) return;
    if (!S.groups.length) {
      box.innerHTML = '<div class="empty">没找到你在别的群的账户</div>';
      return;
    }
    var h = '<div class="grp-hint">每个群的账户是独立的（钱、持仓、榜单各算各的）。' +
            '点一下就切过去，这边会自动刷新。</div>';
    for (var i = 0; i < S.groups.length; i++) {
      var g = S.groups[i] || {};
      h += '<button class="group-item' + (g.cur ? " cur" : "") + '" type="button" data-gid="' +
           esc(g.gid) + '"><span class="gi-main"><span class="gi-name">' +
           esc(g.name || ("群 " + g.gid)) + '</span><span class="gi-sub">' +
           (g.name ? ("群号 " + esc(g.gid)) : "没取到群名，先用群号") + '</span></span>' +
           (g.cur ? '<span class="gi-badge">当前</span>' : '<span class="gi-go">切换 ›</span>') +
           '</button>';
    }
    box.innerHTML = h;
  }

  function doSwitch(gid, name) {
    if (S.switching) return;
    S.switching = true;
    toast("正在切到「" + (name || gid) + "」…");
    post("/api/g/switch", { to: gid }).then(function (d) {
      if (!d || !d.t) throw new Error("切换失败");
      try { sessionStorage.setItem("sm_switch_notice", name || gid); } catch (e) {}
      G = d.g; U = d.u; T = d.t;
      saveAuth({ g: G, u: U, t: T });
      // 地址栏也换掉：群友把链接收藏/转发时，指向的还是这个群
      try {
        history.replaceState(null, "", location.pathname + "?g=" + encodeURIComponent(G) +
          "&u=" + encodeURIComponent(U) + "#t=" + encodeURIComponent(T));
      } catch (e) {}
      location.reload();
    }).catch(function (err) {
      S.switching = false;
      toast(friendlyErr(err));
    });
  }

  $("btnGroup").addEventListener("click", function () {
    var m = $("grpModal");
    if (!m) return;
    m.hidden = false;
    renderGroupList();
    loadGroups().then(renderGroupList);
  });
  $("btnGrpClose").addEventListener("click", function () { $("grpModal").hidden = true; });
  $("grpModal").addEventListener("click", function (e) {
    if (e.target === $("grpModal")) { $("grpModal").hidden = true; return; }
    var btn = e.target.closest ? e.target.closest(".group-item") : null;
    if (!btn) return;
    var gid = btn.getAttribute("data-gid");
    if (!gid || gid === G) { $("grpModal").hidden = true; return; }
    var name = "";
    for (var i = 0; i < S.groups.length; i++) {
      if (S.groups[i] && S.groups[i].gid === gid) { name = S.groups[i].name; break; }
    }
    btn.classList.add("loading");
    doSwitch(gid, name);
  });
  /* ---------------------------------------------------------- 启动 */
  buildTools();
  resizeCanvas();
  $("btnPeek").textContent = "🔮 买内幕消息（" + (S.cfg.peek_cost || 800) + " 元）";
  loadGroups();
  refreshState().then(function () {
    return refreshCandles();
  }).then(function () {
    refreshBoard();
    refreshAch();
    refreshReminders();
    refreshFun();
  }).then(function () {
    pollEvery(3000, refreshState, 1500);
    pollEvery(5000, refreshCandles, 2000);
    pollEvery(8000, refreshBoard, 2500);
    pollEvery(10000, refreshFun, 3000);
    /* 【v4.12.3】从后台切回来立刻补一次全量刷新，
       别让用户盯着旧数据等下一轮。 */
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) return;
      refreshState().then(refreshCandles).then(refreshBoard).then(refreshFun);
    });
  });
})();

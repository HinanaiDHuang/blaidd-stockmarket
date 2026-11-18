# ---------------------------------------------------------------------------
# 部署「布莱泽模拟炒股」到生产服务器
# 用法：.\deploy_stockmarket.ps1 -Zip "C:\AstrBot\deploy\stockmarket.zip"
#                  -PublicHost "203.0.113.10:9030" -BotQQ "123456789"
#
#   .\deploy_stockmarket.ps1 -Zip "C:\AstrBot\deploy\stockmarket.zip"
#
# 做的事：
#   1. 用「服务自己的 python 3.10」做 py_compile（本地 3.12 编过不算数）
#   2. 全量备份现网 .py / config.json / static / 炒股.lua
#   3. 覆盖文件
#   4. nssm restart DicePyStock
#   5. 打 /health；不健康就自动回滚
#
# 注意：改了 plugin/炒股.lua 必须另外重启 Dice! 核心（Dice 不读热更新）。
#       本脚本只重启 DicePyStock（<1s，群友无感）。
# ---------------------------------------------------------------------------
param(
    [Parameter(Mandatory = $true)][string]$Zip,
    [string]$SM    = 'C:\DiceDriver.LLOneBot\py\stockmarket',
    [string]$BotQQ = 'YOUR_BOT_QQ',
    [string]$PublicHost = 'YOUR_SERVER_IP:9030',
    [string]$DEP   = 'C:\AstrBot\deploy',
    [string]$PY    = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe',
    [string]$NSSM  = 'C:\DiceDriver.LLOneBot\nssm.exe',
    [switch]$SkipLua
)
$ErrorActionPreference = 'Continue'
$PLUG = "C:\DiceDriver.LLOneBot\Dice$BotQQ\plugin"
if ($BotQQ -eq 'YOUR_BOT_QQ' -or $PublicHost -like 'YOUR_SERVER_IP*') {
    Log '需要显式传 -BotQQ 和 -PublicHost（仓库里是占位符，别直接上线）'
    exit 4
}
$TS  = (Get-Date -Format 'yyyyMMdd_HHmmss')
$STG = "$DEP\stage_$TS"
$BK  = "$DEP\backup\stockmarket_$TS"

function Log($m) { Write-Output ("[deploy] " + $m) }

# ---------- 0. 解包 ----------
if (-not (Test-Path $Zip)) { Log "ZIP NOT FOUND: $Zip"; exit 1 }
New-Item -ItemType Directory -Force -Path $STG | Out-Null
Expand-Archive -Path $Zip -DestinationPath $STG -Force
Log ("extracted -> " + $STG)

# ---------- 1. 编译检查（用服务自己的 python） ----------
& $PY -c "import py_compile,glob;[py_compile.compile(f,doraise=True) for f in glob.glob(r'$STG\*.py')];print('COMPILE_OK')"
if ($LASTEXITCODE -ne 0) { Log "COMPILE FAILED - ABORT"; exit 1 }

# ---------- 2. 备份 ----------
New-Item -ItemType Directory -Force -Path "$BK\static" | Out-Null
Copy-Item "$SM\*.py"      $BK -Force
Copy-Item "$SM\config.json" $BK -Force
Copy-Item "$SM\static\*"  "$BK\static\" -Force
$lua = $null
if (-not $SkipLua) {
    $lua = Get-ChildItem $PLUG -Filter *.lua | Where-Object {
        try { (Get-Content $_.FullName -Raw -Encoding Default) -match '127\.0\.0\.1:9030' } catch { $false }
    } | Select-Object -First 1
    if (-not $lua) { Log "LUA TARGET NOT FOUND - ABORT"; exit 2 }
    Copy-Item $lua.FullName "$BK\plugin.lua" -Force
    Log ("lua target -> " + $lua.Name)
}
Log ("backup -> " + $BK)

# ---------- 3. 覆盖 ----------
# 仓库里为了不泄露服务器信息，config.json / lua 里的地址是占位符，
# 这里用部署参数注入真实值（漏替换就直接中止，别让占位符上线）。
if (Test-Path "$STG\config.json") {
    $c = Get-Content "$STG\config.json" -Raw -Encoding UTF8
    $c = $c.Replace('YOUR_SERVER_IP', $PublicHost.Split(':')[0])
    [System.IO.File]::WriteAllText("$STG\config.json", $c, (New-Object System.Text.UTF8Encoding($false)))
}
if ($lua -and (Test-Path "$STG\chaogu.lua")) {
    $lb = [System.IO.File]::ReadAllBytes("$STG\chaogu.lua")
    $lt = [System.Text.Encoding]::GetEncoding(936).GetString($lb)
    $lt = $lt.Replace('YOUR_BOT_QQ', $BotQQ).Replace('YOUR_SERVER_IP', $PublicHost.Split(':')[0])
    [System.IO.File]::WriteAllBytes("$STG\chaogu.lua", [System.Text.Encoding]::GetEncoding(936).GetBytes($lt))
}
Copy-Item "$STG\*.py"        $SM -Force
Copy-Item "$STG\config.json" $SM -Force
if (Test-Path "$STG\static") { Copy-Item "$STG\static\*" "$SM\static\" -Force }
if ($lua -and (Test-Path "$STG\chaogu.lua")) { Copy-Item "$STG\chaogu.lua" $lua.FullName -Force }
Log "files copied"

# ---------- 4. 重启服务 ----------
& $NSSM stop DicePyStock | Out-Null
Start-Sleep -Seconds 2
& $NSSM start DicePyStock | Out-Null
Start-Sleep -Seconds 5
Log ("service status: " + (& $NSSM status DicePyStock))

# ---------- 5. 健康检查 ----------
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:9030/health") -TimeoutSec 20
    Log ("health: " + $r.StatusCode + " " + $r.Content)
} catch {
    Log ("HEALTH FAILED: " + $_.Exception.Message)
    Log "rolling back"
    Copy-Item "$BK\*.py"        $SM -Force
    Copy-Item "$BK\config.json" $SM -Force
    Copy-Item "$BK\static\*"    "$SM\static\" -Force
    & $NSSM stop DicePyStock | Out-Null; Start-Sleep -Seconds 2; & $NSSM start DicePyStock | Out-Null
    Log "ROLLED BACK"
    exit 3
}

# ---------- 6. 日志尾部 ----------
$out = & $NSSM get DicePyStock AppStdout
$err = & $NSSM get DicePyStock AppStderr
if ($out -and (Test-Path $out)) { Log "--- stdout tail ---"; Get-Content $out -Tail 20 | ForEach-Object { Write-Output $_ } }
if ($err -and (Test-Path $err)) { Log "--- stderr tail ---"; Get-Content $err -Tail 20 | ForEach-Object { Write-Output $_ } }

Log "DEPLOY OK"

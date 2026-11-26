# 证书机器人 · 依赖自动拉起（幂等：已在跑就跳过）
# 由计划任务 DSH-Cert-QQ 在登录时调用，也可手动运行

$ErrorActionPreference = "Continue"

$BASE   = "C:\Users\Administrator\Documents\deepseek-harness\default-workspace"
$PY     = "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$EDGE   = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$PROF   = "C:\Users\Administrator\AppData\Local\dsh-cert\edgeprof"
$NAPCAT = "E:\迅雷下载\NapCat.Shell"
$QQEXE  = "D:\QQ\QQ.exe"
$LOGDIR = "$BASE\logs"
$LOG    = "$LOGDIR\autostart.log"

New-Item -ItemType Directory -Force -Path $LOGDIR, $PROF | Out-Null

function Log([string]$m) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -Append -Encoding utf8 $LOG
}

function Probe([string]$url, [string]$method = "GET") {
    try {
        if ($method -eq "POST") {
            Invoke-WebRequest $url -Method POST -Body "{}" -ContentType "application/json" -UseBasicParsing -TimeoutSec 3 | Out-Null
        } else {
            Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null
        }
        return $true
    } catch { return $false }
}

Log "===== autostart begin ====="

# ---------- 1. Edge headless（查门户用，CDP 9333）----------
if (Probe "http://127.0.0.1:9333/json/version") {
    Log "edge: already up"
} else {
    if (-not (Test-Path $EDGE)) {
        Log "edge: NOT FOUND at $EDGE"
    } else {
        Start-Process $EDGE -ArgumentList @(
            "--headless=new",
            "--remote-debugging-port=9333",
            "--user-data-dir=$PROF",
            "--no-first-run",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "about:blank"
        ) -WindowStyle Hidden
        Start-Sleep 8
        Log ("edge: started -> " + (Probe "http://127.0.0.1:9333/json/version"))
    }
}

# ---------- 2. NapCat 已停用（改用 AstrBot）----------

# ---------- 3. qq_server 已停用（改用 Aurabot 插件 trademark-cert）----------

Log "===== autostart end ====="

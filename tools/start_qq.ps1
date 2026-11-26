# 一键启动：无头浏览器（查数据用）+ QQ 接入服务
# 用法：在项目根目录执行  powershell -ExecutionPolicy Bypass -File tools\start_qq.ps1
$ErrorActionPreference = "Continue"
$BASE = Split-Path -Parent $PSScriptRoot
$PY   = "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$EDGE = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$PROF = Join-Path $BASE ".tmp\edgeprof"

Write-Host "=== 1/2 检查浏览器调试端口 9333 ==="
$ok = $false
try {
    $r = Invoke-WebRequest "http://127.0.0.1:9333/json/version" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ok = $true }
} catch { }

if (-not $ok) {
    if (Test-Path $EDGE) {
        New-Item -ItemType Directory -Force -Path $PROF | Out-Null
        Start-Process -FilePath $EDGE -ArgumentList @(
            "--headless=new", "--remote-debugging-port=9333",
            "--user-data-dir=$PROF", "--no-first-run",
            "--disable-gpu", "--disable-blink-features=AutomationControlled",
            "about:blank"
        ) -WindowStyle Hidden
        Start-Sleep 6
        try {
            Invoke-WebRequest "http://127.0.0.1:9333/json/version" -UseBasicParsing -TimeoutSec 3 | Out-Null
            Write-Host "[ok] 浏览器已启动"
        } catch {
            Write-Host "[!!] 浏览器启动失败，检查 Edge 路径：$EDGE"
        }
    } else {
        Write-Host "[!!] 找不到 Edge：$EDGE"
    }
} else {
    Write-Host "[ok] 浏览器已在运行"
}

Write-Host ""
Write-Host "=== 2/2 启动 QQ 接入服务（127.0.0.1:8080）==="
Write-Host "NapCat 里把「HTTP 客户端(上报)」的地址填：http://127.0.0.1:8080/qq"
Write-Host ""
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
& $PY -u (Join-Path $BASE "tools\qq_server.py")

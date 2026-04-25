$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Users\aaa85\AppData\Local\Python\bin\python3.14.exe"
$appFile = Join-Path $repo "app.py"
$url = "http://127.0.0.1:8501"
$port = 8501

function Test-AppReady {
    param([string]$TargetUrl)
    try {
        Invoke-WebRequest -Uri $TargetUrl -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Get-PortProcess {
    param([int]$TargetPort)
    try {
        $conn = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($conn) {
            return Get-CimInstance Win32_Process -Filter ("ProcessId = " + $conn.OwningProcess) -ErrorAction SilentlyContinue
        }
    }
    catch {
    }
    return $null
}

if (-not (Test-Path $python)) {
    throw "Python executable not found: $python"
}

$existingProcess = Get-PortProcess -TargetPort $port
if ($existingProcess -and (($existingProcess.CommandLine -or "") -notlike ("*" + $appFile + "*"))) {
    Stop-Process -Id $existingProcess.ProcessId -Force -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process | Where-Object {
        ($_.CommandLine -or "") -like "*streamlit run app.py*" -and
        ($_.CommandLine -or "") -like "*\.codex\worktrees\*"
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

if (-not (Test-AppReady -TargetUrl $url)) {
    $streamlitArgs = "-m streamlit run `"$appFile`" --server.headless true --server.port $port --browser.gatherUsageStats false"
    Start-Process -FilePath $python -ArgumentList $streamlitArgs -WorkingDirectory $repo | Out-Null

    for ($i = 0; $i -lt 30; $i++) {
        if (Test-AppReady -TargetUrl $url) {
            break
        }
        Start-Sleep -Seconds 1
    }
}

if (Test-AppReady -TargetUrl $url) {
    Start-Process explorer.exe $url | Out-Null
}

$ErrorActionPreference = "Stop"

param(
    [int]$PreferredPort = 8511
)

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Users\aaa85\AppData\Local\Python\bin\python3.14.exe"
$appFile = Join-Path $repo "app.py"
$portSearchWindow = 50

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

function Test-IsCurrentAppProcess {
    param($ProcessRecord)
    if (-not $ProcessRecord) {
        return $false
    }
    $commandLine = ($ProcessRecord.CommandLine -or "")
    return $commandLine -like ("*" + $appFile + "*")
}

function Resolve-TargetPort {
    param([int]$StartPort)

    for ($offset = 0; $offset -lt $portSearchWindow; $offset++) {
        $candidatePort = $StartPort + $offset
        $existingProcess = Get-PortProcess -TargetPort $candidatePort

        if (-not $existingProcess) {
            return @{
                Port = $candidatePort
                ReuseExisting = $false
            }
        }

        if (Test-IsCurrentAppProcess -ProcessRecord $existingProcess) {
            return @{
                Port = $candidatePort
                ReuseExisting = $true
            }
        }
    }

    throw "No available port found in range $StartPort-$($StartPort + $portSearchWindow - 1)."
}

if (-not (Test-Path $python)) {
    throw "Python executable not found: $python"
}

$target = Resolve-TargetPort -StartPort $PreferredPort
$port = [int]$target.Port
$url = "http://127.0.0.1:$port"

if (-not $target.ReuseExisting) {
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
else {
    throw "Streamlit app did not become ready on $url."
}

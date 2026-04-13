@echo off
setlocal

set "REPO_DIR=%~dp0"
set "PYTHON_EXE=C:\Users\aaa85\AppData\Local\Python\bin\python3.14.exe"
set "APP_URL=http://127.0.0.1:8501"

if not exist "%PYTHON_EXE%" (
  echo Python executable not found:
  echo %PYTHON_EXE%
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$repo = [System.IO.Path]::GetFullPath('%REPO_DIR%');" ^
  "$python = '%PYTHON_EXE%';" ^
  "$url = '%APP_URL%';" ^
  "function Test-AppReady { param([string]$TargetUrl) try { Invoke-WebRequest -Uri $TargetUrl -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true } catch { return $false } };" ^
  "if (-not (Test-AppReady -TargetUrl $url)) {" ^
  "  Start-Process -FilePath $python -ArgumentList @('-m','streamlit','run','app.py','--server.headless','true','--server.port','8501','--browser.gatherUsageStats','false') -WorkingDirectory $repo | Out-Null;" ^
  "  for ($i = 0; $i -lt 30; $i++) { if (Test-AppReady -TargetUrl $url) { break }; Start-Sleep -Seconds 1 }" ^
  "};" ^
  "Start-Process $url | Out-Null"

endlocal

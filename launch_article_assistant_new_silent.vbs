Dim shell
Dim projectDir
Dim pythonPath
Dim launchCmd

Set shell = CreateObject("WScript.Shell")
projectDir = "C:\Users\aaa85\.codex\worktrees\f06f\Article-Transcription-Assistant-feature-sandbox"
pythonPath = "C:\Users\aaa85\AppData\Local\Python\bin\python3.14.exe"
launchCmd = "cmd /c cd /d """ & projectDir & """ && start """" /b """ & pythonPath & """ -m streamlit run app.py --server.headless true"

shell.Run launchCmd, 0, False
WScript.Sleep 3500
shell.Run "http://localhost:8501", 1, False
Dim shell
Dim projectDir
Dim launchCmd

Set shell = CreateObject("WScript.Shell")
projectDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
launchCmd = "cmd /c cd /d """ & projectDir & """ && launch_article_tool.cmd"

shell.Run launchCmd, 0, False

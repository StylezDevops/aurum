' aurum-startup.vbs — silent launcher for the Windows Startup folder
'
' Place this file (or a shortcut to it) in:
'   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
'
' The 0 in WshShell.Run hides the PowerShell window.
' False = don't wait for the script to finish (fire-and-forget).
'
' Path is derived from this script's own location so it works regardless of
' where the repo is cloned — no hardcoded paths.

Dim ScriptDir, psScript
ScriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
psScript = ScriptDir & "\start-aurum.ps1"

Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & psScript & """", 0, False

' aurum-startup.vbs — silent launcher for the Windows Startup folder
'
' Place this file (or a shortcut to it) in:
'   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
'
' The 0 in WshShell.Run hides the PowerShell window.
' False = don't wait for the script to finish (fire-and-forget).

Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & _
    "C:\Users\24kar\Projects\aurum\scripts\start-aurum.ps1""", 0, False

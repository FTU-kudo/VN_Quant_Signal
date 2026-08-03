Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
ProjectRoot = FSO.GetParentFolderName(ScriptDir)

PythonExe = ProjectRoot & "\.venv\Scripts\pythonw.exe"
ScriptPath = ProjectRoot & "\run_daily.py"

' Run hidden (window style 0 = vbHide)
WshShell.Run chr(34) & PythonExe & chr(34) & " " & chr(34) & ScriptPath & chr(34), 0, False
Set WshShell = Nothing
Set FSO = Nothing

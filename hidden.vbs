' Run a .bat hidden (no console window). Usage: wscript hidden.vbs run_swing.bat
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
bat = here & "\" & WScript.Arguments(0)
rc = sh.Run("""" & bat & """", 0, True)
WScript.Quit rc

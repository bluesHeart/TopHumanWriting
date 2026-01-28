Option Explicit

Dim WshShell, fso, base, pyw, cmd
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = base

pyw = base & "\python\pythonw.exe"
If Not fso.FileExists(pyw) Then
  pyw = base & "\venv\Scripts\pythonw.exe"
End If
If Not fso.FileExists(pyw) Then
  pyw = "pythonw"
End If

cmd = """" & pyw & """ -m webapp.launch"
WshShell.Run cmd, 0, False


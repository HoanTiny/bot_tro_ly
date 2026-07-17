' Chay run_bot.bat trong che do AN (khong hien cua so den cmd).
' Tham so 0 = hidden window, False = khong cho doi ket thuc.
Set shell = CreateObject("Wscript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(Wscript.ScriptFullName)
shell.Run """" & shell.CurrentDirectory & "\run_bot.bat""", 0, False

' ============================================================
'  Lancer_Estimateur.vbs
'  Lance l'application web Estimation Elec en mode INVISIBLE
'  (aucune fenetre console). Ouvre le navigateur automatiquement.
'
'  Prerequis : dossier python\ avec pythonw.exe + dependances pip
'  Debug     : Lancer_Estimateur_DEBUG.bat (console visible)
' ============================================================

Option Explicit

Dim objShell, objFSO, strScriptDir, strPythonw, strApp
Dim ready, elapsed, maxWaitMs, stepMs, strLogFile

Set objShell = CreateObject("WScript.Shell")
Set objFSO   = CreateObject("Scripting.FileSystemObject")

strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptDir

' ─── Dossiers requis (aligne sur Lancer_Estimateur.bat) ─────
EnsureFolder strScriptDir & "\logs"
EnsureFolder strScriptDir & "\exports"
EnsureFolder strScriptDir & "\uploads"

strLogFile = strScriptDir & "\logs\serveur.log"

' ─── Verification Python embeddable ─────────────────────────
strPythonw = strScriptDir & "\python\pythonw.exe"
If Not objFSO.FileExists(strPythonw) Then
  MsgBox "Python embeddable introuvable :" & vbCrLf & strPythonw & vbCrLf & vbCrLf & _
         "Installez Python dans le dossier python\ du projet" & vbCrLf & _
         "(python.exe -m pip install -r requirements.txt)" & vbCrLf & vbCrLf & _
         "Ou utilisez Lancer_Estimateur_DEBUG.bat pour diagnostiquer.", _
         16, "Estimation Elec - Erreur"
  WScript.Quit 1
End If

strApp = strScriptDir & "\app.py"
If Not objFSO.FileExists(strApp) Then
  MsgBox "app.py introuvable dans :" & vbCrLf & strScriptDir, _
         16, "Estimation Elec - Erreur"
  WScript.Quit 1
End If

' ─── Port 5000 deja en ecoute ? ─────────────────────────────
If IsPort5000Listening(objShell) Then
  If FlaskHttpReady() Then
    OpenBrowser objShell
    WScript.Quit 0
  Else
    MsgBox "Le port 5000 est deja utilise, mais ce n'est pas l'application Estimation Elec." & vbCrLf & vbCrLf & _
           "Arretez l'autre programme ou changez de port, puis relancez.", _
           48, "Estimation Elec"
    WScript.Quit 1
  End If
End If

' ─── Lancement invisible (cmd + cd /d pour chemins avec espaces)
Dim strCmd
strCmd = "cmd /c cd /d """ & strScriptDir & """ && """ & strPythonw & """ """ & strApp & """"
objShell.Run strCmd, 0, False

' ─── Attente : HTTP (prioritaire) + repli netstat ───────────
ready     = False
elapsed   = 0
maxWaitMs = 45000
stepMs    = 400

Do While Not ready And elapsed < maxWaitMs
  WScript.Sleep stepMs
  elapsed = elapsed + stepMs
  If FlaskHttpReady() Then
    ready = True
  ElseIf IsPort5000Listening(objShell) Then
    ' Port ouvert mais HTTP pas encore pret (demarrage lent)
    WScript.Sleep 500
    If FlaskHttpReady() Then ready = True
  End If
Loop

If ready Then
  OpenBrowser objShell
  WScript.Quit 0
End If

MsgBox "Le serveur n'a pas repondu a temps (45 s)." & vbCrLf & vbCrLf & _
       "Consultez le journal :" & vbCrLf & strLogFile & vbCrLf & vbCrLf & _
       "Pour un diagnostic avec fenetre console :" & vbCrLf & _
       "Lancer_Estimateur_DEBUG.bat", _
       48, "Estimation Elec"
WScript.Quit 1


' ─── Helpers ────────────────────────────────────────────────

Sub EnsureFolder(path)
  If Not objFSO.FolderExists(path) Then objFSO.CreateFolder(path)
End Sub

Sub OpenBrowser(shell)
  shell.Run "http://127.0.0.1:5000/", 1, False
End Sub

Function IsPort5000Listening(shell)
  Dim exec, out
  Set exec = shell.Exec("cmd /c netstat -ano | findstr /C:"":5000 "" | findstr /C:""LISTENING""")
  Do While exec.Status = 0
    WScript.Sleep 50
  Loop
  out = exec.StdOut.ReadAll()
  IsPort5000Listening = (Len(Trim(out)) > 0)
End Function

Function FlaskHttpReady()
  Dim http, st
  On Error Resume Next
  Err.Clear

  Set http = Nothing
  Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  If http Is Nothing Or Err.Number <> 0 Then
    Err.Clear
    Set http = CreateObject("MSXML2.XMLHTTP")
  End If
  If http Is Nothing Then
    FlaskHttpReady = False
    Exit Function
  End If

  On Error Resume Next
  http.Open "GET", "http://127.0.0.1:5000/", False
  If Err.Number <> 0 Then
    Err.Clear
    FlaskHttpReady = False
    Exit Function
  End If

  On Error Resume Next
  http.setTimeouts 1500, 1500, 3000, 5000
  http.Send
  If Err.Number <> 0 Then
    Err.Clear
    FlaskHttpReady = False
    Exit Function
  End If

  st = http.Status
  FlaskHttpReady = (st >= 200 And st < 400)
  Err.Clear
  On Error Goto 0
End Function

' ============================================================
'  Lancer_Estimateur.vbs
'  Lance l'application web Estimation Elec en mode INVISIBLE
'  (aucune fenetre console). Ouvre le navigateur automatiquement.
'  Port : variable PORT (defaut 8080)
'
'  Prerequis : dossier python\ avec pythonw.exe + dependances pip
'  Debug     : Lancer_Estimateur_DEBUG.bat (console visible)
' ============================================================

Option Explicit

Dim objShell, objFSO, strScriptDir, strPythonw, strPython, strApp
Dim ready, elapsed, maxWaitMs, stepMs, strLogFile, appPort

Set objShell = CreateObject("WScript.Shell")
Set objFSO   = CreateObject("Scripting.FileSystemObject")

strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptDir

appPort = objShell.Environment("PROCESS").Item("PORT")
If appPort = "" Then appPort = "8080"

' ─── Dossiers requis (aligne sur Lancer_Estimateur.bat) ─────
EnsureFolder strScriptDir & "\logs"
EnsureFolder strScriptDir & "\exports"
EnsureFolder strScriptDir & "\uploads"
EnsureFolder strScriptDir & "\backups\cloud\Hopitaux"
EnsureFolder strScriptDir & "\backups\cloud\Industriel"
EnsureFolder strScriptDir & "\backups\cloud\Autres"

strLogFile = strScriptDir & "\logs\serveur.log"

' ─── Verification Python embeddable ─────────────────────────
strPythonw = strScriptDir & "\python\pythonw.exe"
strPython = strScriptDir & "\python\python.exe"
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

' ─── Sauvegarde BDD (quotidienne + hebdo vendredi 16h si besoin) ─
If objFSO.FileExists(strPython) And objFSO.FileExists(strScriptDir & "\scripts\backup_db.py") Then
  Dim strBackupCmd
  strBackupCmd = "cmd /c cd /d """ & strScriptDir & """ && """ & strPython & """ scripts\backup_db.py --launch"
  objShell.Run strBackupCmd, 0, True
End If

' ─── Port deja en ecoute ? ────────────────────────────────────
If IsAppPortListening(objShell, appPort) Then
  If FlaskHttpReady(appPort) Then
    OpenBrowser objShell, appPort
    WScript.Quit 0
  Else
    MsgBox "Le port " & appPort & " est deja utilise, mais ce n'est pas l'application Estimation Elec." & vbCrLf & vbCrLf & _
           "Arretez l'autre programme ou definissez PORT=xxxx, puis relancez.", _
           48, "Estimation Elec"
    WScript.Quit 1
  End If
End If

' ─── Lancement invisible (cmd + cd /d pour chemins avec espaces)
Dim strCmd
strCmd = "cmd /c cd /d """ & strScriptDir & """ && set PORT=" & appPort & " && """ & strPythonw & """ """ & strApp & """"
objShell.Run strCmd, 0, False

' ─── Attente : HTTP (prioritaire) + repli netstat ───────────
ready     = False
elapsed   = 0
maxWaitMs = 45000
stepMs    = 400

Do While Not ready And elapsed < maxWaitMs
  WScript.Sleep stepMs
  elapsed = elapsed + stepMs
  If FlaskHttpReady(appPort) Then
    ready = True
  ElseIf IsAppPortListening(objShell, appPort) Then
    ' Port ouvert mais HTTP pas encore pret (demarrage lent)
    WScript.Sleep 500
    If FlaskHttpReady(appPort) Then ready = True
  End If
Loop

If ready Then
  OpenBrowser objShell, appPort
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

Sub OpenBrowser(shell, port)
  shell.Run "http://127.0.0.1:" & port & "/", 1, False
End Sub

Function IsAppPortListening(shell, port)
  Dim exec, out, findPat
  findPat = "cmd /c netstat -ano | findstr /C:"":" & port & " "" | findstr /C:""LISTENING"""
  Set exec = shell.Exec(findPat)
  Do While exec.Status = 0
    WScript.Sleep 50
  Loop
  out = exec.StdOut.ReadAll()
  IsAppPortListening = (Len(Trim(out)) > 0)
End Function

Function FlaskHttpReady(port)
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
  http.Open "GET", "http://127.0.0.1:" & port & "/", False
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

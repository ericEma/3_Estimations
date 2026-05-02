' ============================================================
'  Lancer_Estimateur.vbs
'  Lance l'application web Estimation Elec en mode INVISIBLE
'  (aucune fenetre console). Ouvre le navigateur automatiquement.
'
'  Pour un lancement AVEC fenetre console (debug),
'  utiliser Lancer_Estimateur_DEBUG.bat a la place.
' ============================================================

Option Explicit

Dim objShell, objFSO, strScriptDir, strPythonw, strApp, strLogsDir

Set objShell = CreateObject("WScript.Shell")
Set objFSO   = CreateObject("Scripting.FileSystemObject")

' ─── Dossier du script ──────────────────────────────────────
strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptDir

' ─── Verification Python embeddable ────────────────────────
strPythonw = strScriptDir & "\python\pythonw.exe"
If Not objFSO.FileExists(strPythonw) Then
  MsgBox "Python embeddable introuvable :" & vbCrLf & strPythonw & vbCrLf & vbCrLf & _
         "Relancez le setup initial ou utilisez Lancer_Estimateur_DEBUG.bat.", _
         16, "Estimation Elec - Erreur"
  WScript.Quit 1
End If

' ─── Verification app.py ───────────────────────────────────
strApp = strScriptDir & "\app.py"
If Not objFSO.FileExists(strApp) Then
  MsgBox "app.py introuvable dans :" & vbCrLf & strScriptDir, _
         16, "Estimation Elec - Erreur"
  WScript.Quit 1
End If

' ─── Creation dossier logs si absent ───────────────────────
strLogsDir = strScriptDir & "\logs"
If Not objFSO.FolderExists(strLogsDir) Then
  objFSO.CreateFolder(strLogsDir)
End If

' ─── Si le port 5000 est deja pris : on ouvre juste le navigateur ─
Dim objExec, strNetstat
Set objExec = objShell.Exec("cmd /c netstat -ano | findstr "":5000 "" | findstr ""LISTENING""")
strNetstat = objExec.StdOut.ReadAll()
If Len(Trim(strNetstat)) > 0 Then
  ' Serveur deja actif - on ouvre juste le navigateur
  objShell.Run "http://localhost:5000", 1, False
  WScript.Quit 0
End If

' ─── Lancement invisible du serveur Flask ──────────────────
' On passe par cmd /c pour pouvoir detacher proprement le processus.
' La redirection des logs est deja gereee par app.py (via sys.stdout).
Dim strCmd
strCmd = """" & strPythonw & """ """ & strApp & """"
objShell.Run strCmd, 0, False

' ─── Polling : on attend que Flask reponde vraiment sur / ──
' Tentatives toutes les 400 ms jusqu'a 45 s. Des que Flask repond
' avec un code HTTP valide (2xx/3xx), on ouvre le navigateur.
Dim http, ready, elapsed, maxWaitMs, stepMs
ready     = False
elapsed   = 0
maxWaitMs = 45000
stepMs    = 400

Do While Not ready And elapsed < maxWaitMs
  WScript.Sleep stepMs
  elapsed = elapsed + stepMs
  On Error Resume Next
  Set http = CreateObject("MSXML2.XMLHTTP")
  http.Open "GET", "http://localhost:5000/", False
  http.Send
  If Err.Number = 0 Then
    If http.Status >= 200 And http.Status < 400 Then
      ready = True
    End If
  End If
  Err.Clear
  On Error Goto 0
Loop

If Not ready Then
  MsgBox "Le serveur met anormalement longtemps a demarrer." & vbCrLf & _
         "Consultez logs\serveur.log pour diagnostiquer.", _
         48, "Estimation Elec"
End If

' ─── Ouverture du navigateur ───────────────────────────────
objShell.Run "http://localhost:5000", 1, False

Set objShell = Nothing
Set objFSO   = Nothing
Set objExec  = Nothing
Set http     = Nothing

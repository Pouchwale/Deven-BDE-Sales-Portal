Attribute VB_Name = "PortalUpload"
'==============================================================================
' PortalUpload - sends this SAP workbook to the BDE & Sales Portal.
'
' Every time the workbook is saved (after the daily SAP refresh), a copy of it
' is uploaded to the live portal through the Super Admin "Upload SAP file"
' import. The portal's import is idempotent: an unchanged row creates nothing,
' so an extra upload is harmless.
'
' SETTINGS are NOT stored in this workbook (the file gets shared; the Super
' Admin password must not travel with it). They are read from:
'
'     %APPDATA%\bde-portal\portal-upload.ini
'
' with these lines:
'
'     PORTAL_URL=https://deven-bde-sales-portal-frontend.onrender.com
'     PORTAL_USERNAME=admin
'     PORTAL_PASSWORD=your-super-admin-password
'
' Run the macro SetUpPortalUpload once to create that file.
' Run UploadToPortalNow to upload by hand and see the result.
' A log is kept in %APPDATA%\bde-portal\portal-upload.log
'==============================================================================
Option Explicit

Private Const MIN_SECONDS_BETWEEN_UPLOADS As Long = 120   ' the portal allows 6 imports / 5 min
Private Const TIMEOUT_MS As Long = 180000                   ' a sleeping free server can take ~1 min to wake

Private mLastUpload As Date

' ----------------------------------------------------------------- public ---
' Called from ThisWorkbook.Workbook_AfterSave. Quiet: problems go to the log
' and the status bar, never a pop-up in the middle of someone's work.
Public Sub UploadAfterSave()
    If mLastUpload <> 0 Then
        If DateDiff("s", mLastUpload, Now) < MIN_SECONDS_BETWEEN_UPLOADS Then Exit Sub
    End If
    Dim message As String
    If RunUpload(message) Then mLastUpload = Now
    Application.StatusBar = "Portal: " & message
    Application.OnTime Now + TimeSerial(0, 0, 15), "PortalUpload.ClearStatusBar"
End Sub

' Upload now and show the result.
Public Sub UploadToPortalNow()
    Dim message As String
    If RunUpload(message) Then
        mLastUpload = Now
        MsgBox message, vbInformation, "BDE & Sales Portal"
    Else
        MsgBox message, vbExclamation, "BDE & Sales Portal"
    End If
End Sub

' One-time setup on each computer: writes the settings file.
Public Sub SetUpPortalUpload()
    Dim url As String, user As String, pass As String
    url = InputBox("Portal address:", "Portal upload setup", _
                   "https://deven-bde-sales-portal-frontend.onrender.com")
    If Len(url) = 0 Then Exit Sub
    user = InputBox("Super Admin username:", "Portal upload setup", "admin")
    If Len(user) = 0 Then Exit Sub
    pass = InputBox("Super Admin password (stored only on this computer):", "Portal upload setup")
    If Len(pass) = 0 Then Exit Sub

    EnsureFolder SettingsFolder()
    Dim f As Integer: f = FreeFile
    Open SettingsFile() For Output As #f
    Print #f, "PORTAL_URL=" & Trim$(url)
    Print #f, "PORTAL_USERNAME=" & Trim$(user)
    Print #f, "PORTAL_PASSWORD=" & pass
    Close #f
    MsgBox "Saved. Each save of this workbook now uploads it to the portal." & vbCrLf & _
           "Run UploadToPortalNow to test it.", vbInformation, "BDE & Sales Portal"
End Sub

Public Sub ClearStatusBar()
    Application.StatusBar = False
End Sub

' ------------------------------------------------------------------- core ---
Private Function RunUpload(ByRef message As String) As Boolean
    On Error GoTo Failed
    Dim baseUrl As String, user As String, pass As String
    If Not ReadSettings(baseUrl, user, pass) Then
        message = "Not set up on this computer - run the macro SetUpPortalUpload."
        WriteLog message
        Exit Function
    End If

    ' Upload a saved copy, never the open file: Excel holds it locked.
    Dim tempPath As String
    tempPath = Environ$("TEMP") & "\bde_sap_upload_" & Format$(Now, "yyyymmddhhnnss") & _
               "." & LCase$(Mid$(ThisWorkbook.Name, InStrRev(ThisWorkbook.Name, ".") + 1))
    ThisWorkbook.SaveCopyAs tempPath

    Dim session As String
    session = SignIn(baseUrl, user, pass)
    Dim result As String
    result = UploadFile(baseUrl, session, tempPath, ThisWorkbook.Name)
    SignOut baseUrl, session
    Kill tempPath

    message = "uploaded " & ThisWorkbook.Name & " - " & Summarise(result)
    WriteLog message
    RunUpload = True
    Exit Function

Failed:
    message = "upload failed: " & Err.Description
    WriteLog message
    On Error Resume Next
    If Len(tempPath) > 0 Then If Len(Dir$(tempPath)) > 0 Then Kill tempPath
End Function

Private Function SignIn(ByVal baseUrl As String, ByVal user As String, ByVal pass As String) As String
    Dim http As Object: Set http = NewRequest()
    http.Open "POST", baseUrl & "/api/auth/login", False
    http.SetRequestHeader "Content-Type", "application/json"
    http.Send "{""identifier"":""" & JsonEscape(user) & """,""password"":""" & JsonEscape(pass) & """}"
    If http.Status <> 200 Then
        Err.Raise vbObjectError + 1, , "sign-in refused (" & http.Status & ") - check the username and password"
    End If

    ' The session secret arrives only in the Set-Cookie header.
    Dim headers As String, p As Long, q As Long
    headers = http.GetAllResponseHeaders()
    p = InStr(1, headers, "bde_session=", vbTextCompare)
    If p = 0 Then Err.Raise vbObjectError + 2, , "sign-in returned no session"
    p = p + Len("bde_session=")
    q = InStr(p, headers, ";")
    If q = 0 Then q = InStr(p, headers, vbCr)
    SignIn = Mid$(headers, p, q - p)
End Function

Private Sub SignOut(ByVal baseUrl As String, ByVal session As String)
    On Error Resume Next
    Dim http As Object: Set http = NewRequest()
    http.Open "POST", baseUrl & "/api/auth/logout", False
    http.SetRequestHeader "Authorization", "Bearer " & session
    http.Send ""
End Sub

Private Function UploadFile(ByVal baseUrl As String, ByVal session As String, _
                            ByVal path As String, ByVal displayName As String) As String
    Dim boundary As String
    boundary = "----BDEPortal" & Format$(Now, "yyyymmddhhnnss") & CStr(Int(Rnd * 1000000))

    Dim head As String, tail As String
    head = "--" & boundary & vbCrLf & _
           "Content-Disposition: form-data; name=""file""; filename=""" & displayName & """" & vbCrLf & _
           "Content-Type: application/octet-stream" & vbCrLf & vbCrLf
    tail = vbCrLf & "--" & boundary & "--" & vbCrLf

    ' multipart body = head bytes + file bytes + tail bytes
    Dim body As Object: Set body = CreateObject("ADODB.Stream")
    body.Type = 1: body.Open
    body.Write AsciiBytes(head)
    Dim fileStream As Object: Set fileStream = CreateObject("ADODB.Stream")
    fileStream.Type = 1: fileStream.Open
    fileStream.LoadFromFile path
    body.Write fileStream.Read
    fileStream.Close
    body.Write AsciiBytes(tail)
    body.Position = 0

    Dim http As Object: Set http = NewRequest()
    http.Open "POST", baseUrl & "/api/admin/sap-import/upload", False
    http.SetRequestHeader "Authorization", "Bearer " & session
    http.SetRequestHeader "Content-Type", "multipart/form-data; boundary=" & boundary
    http.Send body.Read
    body.Close

    If http.Status <> 200 Then
        Err.Raise vbObjectError + 3, , "upload refused (" & http.Status & "): " & Left$(http.ResponseText, 300)
    End If
    UploadFile = http.ResponseText
End Function

' --------------------------------------------------------------- helpers ---
Private Function NewRequest() As Object
    Dim http As Object
    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
    http.SetTimeouts 30000, 30000, TIMEOUT_MS, TIMEOUT_MS
    Set NewRequest = http
End Function

Private Function AsciiBytes(ByVal text As String) As Variant
    Dim s As Object: Set s = CreateObject("ADODB.Stream")
    s.Type = 2: s.Charset = "utf-8": s.Open
    s.WriteText text
    s.Position = 0: s.Type = 1
    s.Position = 3                       ' skip the UTF-8 byte-order mark
    AsciiBytes = s.Read
    s.Close
End Function

Private Function JsonEscape(ByVal text As String) As String
    text = Replace(text, "\", "\\")
    text = Replace(text, """", "\""")
    JsonEscape = text
End Function

' Pulls a few counts out of the JSON reply without a JSON library.
Private Function Summarise(ByVal json As String) As String
    Summarise = "rows " & JsonNumber(json, "total_rows") & _
                ", new customers " & JsonNumber(json, "customers_created") & _
                ", updated " & JsonNumber(json, "customers_updated") & _
                ", new invoice lines " & JsonNumber(json, "lines_created") & _
                ", errors " & JsonNumber(json, "error_count")
End Function

Private Function JsonNumber(ByVal json As String, ByVal key As String) As String
    Dim p As Long, q As Long
    p = InStr(1, json, """" & key & """:")
    If p = 0 Then JsonNumber = "?": Exit Function
    p = p + Len(key) + 3
    q = p
    Do While q <= Len(json) And Mid$(json, q, 1) Like "[0-9]"
        q = q + 1
    Loop
    JsonNumber = Mid$(json, p, q - p)
End Function

Private Function SettingsFolder() As String
    SettingsFolder = Environ$("APPDATA") & "\bde-portal"
End Function

Private Function SettingsFile() As String
    SettingsFile = SettingsFolder() & "\portal-upload.ini"
End Function

Private Sub EnsureFolder(ByVal folder As String)
    If Len(Dir$(folder, vbDirectory)) = 0 Then MkDir folder
End Sub

Private Function ReadSettings(ByRef url As String, ByRef user As String, ByRef pass As String) As Boolean
    If Len(Dir$(SettingsFile())) = 0 Then Exit Function
    Dim f As Integer, textLine As String, key As String, value As String, eq As Long
    f = FreeFile
    Open SettingsFile() For Input As #f
    Do While Not EOF(f)
        Line Input #f, textLine
        eq = InStr(textLine, "=")
        If eq > 0 Then
            key = UCase$(Trim$(Left$(textLine, eq - 1)))
            value = Mid$(textLine, eq + 1)
            Select Case key
                Case "PORTAL_URL": url = Trim$(value)
                Case "PORTAL_USERNAME": user = Trim$(value)
                Case "PORTAL_PASSWORD": pass = value
            End Select
        End If
    Loop
    Close #f
    If Right$(url, 1) = "/" Then url = Left$(url, Len(url) - 1)
    ReadSettings = Len(url) > 0 And Len(user) > 0 And Len(pass) > 0
End Function

Private Sub WriteLog(ByVal message As String)
    On Error Resume Next
    EnsureFolder SettingsFolder()
    Dim f As Integer: f = FreeFile
    Open SettingsFolder() & "\portal-upload.log" For Append As #f
    Print #f, Format$(Now, "yyyy-mm-dd hh:nn:ss") & "  " & message
    Close #f
End Sub

; KimiLM installer additions (electron-builder "nsis.include").
;
; 1. The installer says what it is doing: the details list is shown and each step prints a line, including how big
;    each part is once it is in place (electron-builder hides all of this by default).
; 2. It looks for Datavera Agent (this app's earlier name) and offers to remove it. Its data is kept: chats,
;    models, memory, settings and saved keys move into KimiLM when KimiLM first starts (app/electron/migrate.cjs).
;
; Plain ASCII only: NSIS reads this file in the system code page.

!include "FileFunc.nsh"
; Defining customCheckAppRunning makes electron-builder skip these two lines, which its own check needs.
!include "getProcessInfo.nsh"
Var pid

!define OLD_APP_NAME "Datavera Agent"
!define OLD_UNINSTALL_ROOT "Software\Microsoft\Windows\CurrentVersion\Uninstall"

!macro customHeader
  ShowInstDetails show
!macroend

; Runs right before the old version is removed and the app is unpacked (and in the uninstaller, where only the
; check runs). Keeps electron-builder's own check that KimiLM is not running, then turns the messages on
; (electron-builder switches them off just before this).
!macro customCheckAppRunning
  !insertmacro IS_POWERSHELL_AVAILABLE
  !insertmacro _CHECK_APP_RUNNING
  !ifndef BUILD_UNINSTALLER
  SetDetailsPrint both
  DetailPrint "Installing ${PRODUCT_NAME} ${VERSION} into $INSTDIR"
  DetailPrint "Step 1 of 4 - Removing the previous version of ${PRODUCT_NAME}, if there is one..."
  DetailPrint "Step 2 of 4 - Unpacking the app, its local AI engine (Python 3.12) and llama.cpp."
  DetailPrint "    This is the longest step: usually 1 to 3 minutes, longer on a slow disk."
  DetailPrint "    The progress bar may stand still while it works. Please wait."
  !endif
!macroend

!macro customInstall
  SetDetailsPrint both
  DetailPrint "Step 3 of 4 - Checking what was installed:"
  ${GetSize} "$INSTDIR\resources\engine" "/S=0M" $0 $1 $2
  DetailPrint "    KimiLM engine - chats, memory, harnesses, skills and plugins ($0 MB)"
  ${GetSize} "$INSTDIR\resources\python" "/S=0M" $0 $1 $2
  DetailPrint "    Local AI engine runtime - Python 3.12 with its packages ($0 MB)"
  ${GetSize} "$INSTDIR\resources\llama" "/S=0M" $0 $1 $2
  DetailPrint "    llama.cpp - runs models on this computer; CPU build ($0 MB). GPU builds can be added in Models."
  DetailPrint "    Shortcuts in the Start menu and on the desktop"
  DetailPrint "    Nothing was sent anywhere: models and chats stay on this computer."

  DetailPrint "Step 4 of 4 - Looking for ${OLD_APP_NAME} (the earlier name of this app)..."
  ; This user's installed programs: $R8 = its uninstaller (no quotes) or "", $R9 = its folder.
  StrCpy $R8 ""
  StrCpy $R9 ""
  StrCpy $R7 0
  kimiFindLoop:
    EnumRegKey $R6 HKCU "${OLD_UNINSTALL_ROOT}" $R7
    StrCmp $R6 "" kimiFindDone
    IntOp $R7 $R7 + 1
    ReadRegStr $R5 HKCU "${OLD_UNINSTALL_ROOT}\$R6" "DisplayName"
    StrCpy $R4 $R5 14
    StrCmp $R4 "${OLD_APP_NAME}" 0 kimiFindLoop
    ReadRegStr $R5 HKCU "${OLD_UNINSTALL_ROOT}\$R6" "UninstallString"
    !insertmacro GetInQuotes $R8 "$R5"
    ReadRegStr $R9 HKCU "${OLD_UNINSTALL_ROOT}\$R6" "InstallLocation"
    ${if} $R9 == ""
    ${andIf} $R8 != ""
      Push $R8
      Call GetFileParent
      Pop $R9
    ${endIf}
    ${ifNot} ${FileExists} "$R8"
      StrCpy $R8 ""
      Goto kimiFindLoop
    ${endIf}
  kimiFindDone:
  ${if} $R8 == ""
    DetailPrint "    Not installed: nothing to do."
  ${else}
    DetailPrint "    Found ${OLD_APP_NAME} in $R9"
    MessageBox MB_YESNO|MB_ICONQUESTION "${OLD_APP_NAME} is installed on this computer. ${PRODUCT_NAME} is its new name and replaces it.$\r$\n$\r$\nRemove ${OLD_APP_NAME} now? (Recommended)$\r$\n$\r$\nYour chats, models, memory, settings and saved keys are kept: they move into ${PRODUCT_NAME} when it first starts. If ${OLD_APP_NAME} is running, it is closed, and any task it is running stops." /SD IDNO IDNO kimiKeepOld
      DetailPrint "    Removing ${OLD_APP_NAME} (its data is kept)..."
      CopyFiles /SILENT "$R8" "$PLUGINSDIR\old-app-uninstaller.exe"
      ClearErrors
      ExecWait '"$PLUGINSDIR\old-app-uninstaller.exe" /S /KEEP_APP_DATA /currentuser _?=$R9' $0
      ${if} ${Errors}
      ${orIf} $0 != 0
        DetailPrint "    Could not remove it automatically (code $0). Remove ${OLD_APP_NAME} later in Windows Settings > Apps."
      ${else}
        RMDir "$R9"
        DetailPrint "    Removed ${OLD_APP_NAME}."
      ${endIf}
      Goto kimiOldDone
    kimiKeepOld:
      DetailPrint "    Kept ${OLD_APP_NAME}. You can remove it later in Windows Settings > Apps."
    kimiOldDone:
  ${endIf}
  DetailPrint "Your chats, models, memory, settings and saved keys from ${OLD_APP_NAME}, if any, move into ${PRODUCT_NAME} when it first starts."
  DetailPrint "Installation complete. ${PRODUCT_NAME} starts when you click Finish."
  SetDetailsPrint none
!macroend

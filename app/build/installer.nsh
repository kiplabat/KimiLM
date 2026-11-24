; KimiLM installer additions (electron-builder "nsis.include").
;
; The installer says what it is doing: the details list is shown and each step prints a line, including how big
; each part is once it is in place (electron-builder hides all of this by default).
;
; Plain ASCII only: NSIS reads this file in the system code page.

!include "FileFunc.nsh"
; Defining customCheckAppRunning makes electron-builder skip these two lines, which its own check needs.
!include "getProcessInfo.nsh"
Var pid

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
  DetailPrint "Step 1 of 3 - Removing the previous version of ${PRODUCT_NAME}, if there is one..."
  DetailPrint "Step 2 of 3 - Unpacking the app, its local AI engine (Python 3.12) and llama.cpp."
  DetailPrint "    This is the longest step: usually 1 to 3 minutes, longer on a slow disk."
  DetailPrint "    The progress bar may stand still while it works. Please wait."
  !endif
!macroend

!macro customInstall
  SetDetailsPrint both
  DetailPrint "Step 3 of 3 - Checking what was installed:"
  ${GetSize} "$INSTDIR\resources\engine" "/S=0M" $0 $1 $2
  DetailPrint "    KimiLM engine - chats, memory, harnesses, skills and plugins ($0 MB)"
  ${GetSize} "$INSTDIR\resources\python" "/S=0M" $0 $1 $2
  DetailPrint "    Local AI engine runtime - Python 3.12 with its packages ($0 MB)"
  ${GetSize} "$INSTDIR\resources\llama" "/S=0M" $0 $1 $2
  DetailPrint "    llama.cpp - runs models on this computer; CPU build ($0 MB). GPU builds can be added in Models."
  DetailPrint "    Shortcuts in the Start menu and on the desktop"
  DetailPrint "    Nothing was sent anywhere: models and chats stay on this computer."
  DetailPrint "Installation complete. ${PRODUCT_NAME} starts when you click Finish."
  SetDetailsPrint none
!macroend

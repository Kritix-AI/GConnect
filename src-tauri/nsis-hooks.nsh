!define GCONNECT_NSIS_HOOK_DIR "${__FILEDIR__}"

!macro GCONNECT_CLOSE_BEFORE_INSTALL_OR_UNINSTALL
  ; Ask the installed app to close cleanly so it can stop its GPU backend too.
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=gconnect-close-before-uninstall.ps1 "${GCONNECT_NSIS_HOOK_DIR}\gconnect-close-before-uninstall.ps1"
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\gconnect-close-before-uninstall.ps1" -InstallDir "$INSTDIR" -ProcessName "${MAINBINARYNAME}"'
  Pop $0
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro GCONNECT_CLOSE_BEFORE_INSTALL_OR_UNINSTALL
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro GCONNECT_CLOSE_BEFORE_INSTALL_OR_UNINSTALL
!macroend

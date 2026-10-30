@echo off
chcp 65001 >nul
title Meninges SVG Generator and Folder Sync
rem Interactive menu supports multi-select: batch lock and WPS export.
rem Extra command-line arguments are passed through to generate-svg.ps1.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\generate-svg.ps1" %*
if errorlevel 1 pause

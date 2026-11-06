@echo off
rem HullOpt motoru kurulumu: cift tiklayin. Ayrintilar kurulum.ps1 icinde.
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kurulum.ps1"
echo.
echo Bitti. Pencereyi kapatabilirsiniz.
pause >nul

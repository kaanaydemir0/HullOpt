@echo off
rem HullOpt C# araclari: cift tiklayin. Ayrintilar csharp_kurulum.ps1 icinde.
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0csharp_kurulum.ps1"
echo.
echo Bitti. Pencereyi kapatabilirsiniz.
pause >nul

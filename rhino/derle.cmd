@echo off
rem HullOpt Rhino eklentisini derler: cift tiklayin. Cikti: rhino\HullOpt\bin\Debug\net48\HullOpt.rhp
rem Rhino acikken de derlenir: Rhino'nun kilitledigi eski .rhp yeniden adlandirilir, yenisi yerine konur.
rem Yeni surum Rhino yeniden acilinca yuklenir.
chcp 65001 >nul
cd /d "%~dp0"
set DOTNET_CLI_TELEMETRY_OPTOUT=1
set DOTNET_NOLOGO=1
set CIKTI=HullOpt\bin\Debug\net48
if not exist "..\calisma" mkdir "..\calisma"
del /q "%CIKTI%\HullOpt.rhp.eski_*" 2>nul
dotnet build "HullOpt\HullOpt.csproj" -c Debug -nologo > "..\calisma\derleme_gunlugu.txt" 2>&1
set SONUC=%ERRORLEVEL%
if not %SONUC%==0 (
  findstr /c:"error CS" "..\calisma\derleme_gunlugu.txt" >nul
  if errorlevel 1 (
    findstr /c:"MSB3027" "..\calisma\derleme_gunlugu.txt" >nul
    if not errorlevel 1 call :kilitli
  )
)
type "..\calisma\derleme_gunlugu.txt"
echo.
if %SONUC%==0 (echo DERLEME TAMAM) else (echo DERLEME HATASI: %SONUC%)
echo DERLEME BITTI: %SONUC% >> "..\calisma\derleme_gunlugu.txt"
if %SONUC%==0 (
  echo Pencere 5 saniye sonra kapanacak.
  timeout /t 5 >nul
  exit /b 0
)
echo Pencereyi kapatabilirsiniz.
pause >nul
exit /b %SONUC%

:kilitli
rem Derleme basarili, yalniz acik Rhino eski .rhp dosyasini kilitledigi icin kopyalanamadi.
ren "%CIKTI%\HullOpt.rhp" "HullOpt.rhp.eski_%RANDOM%" 2>nul
copy /y "HullOpt\obj\Debug\net48\HullOpt.rhp" "%CIKTI%\HullOpt.rhp" >nul
if errorlevel 1 exit /b 0
copy /y "HullOpt\obj\Debug\net48\HullOpt.pdb" "%CIKTI%\HullOpt.pdb" >nul
set SONUC=0
echo KILITLI DOSYA: eski .rhp yeniden adlandirildi, yeni surum yerine kondu; Rhino yeniden acilinca yuklenir. >> "..\calisma\derleme_gunlugu.txt"
exit /b 0

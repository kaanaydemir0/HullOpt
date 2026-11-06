@echo off
rem Motorun testlerini calistirir: cift tiklayin. Gunluk: calisma\test_gunlugu.txt
chcp 65001 >nul
cd /d "%~dp0engine"
if not exist "..\calisma" mkdir "..\calisma"
"..\.venv\Scripts\python.exe" -m pytest tests -q -p no:cacheprovider > "..\calisma\test_gunlugu.txt" 2>&1
set SONUC=%ERRORLEVEL%
type "..\calisma\test_gunlugu.txt"
echo.
echo TESTLER BITTI: %SONUC% >> "..\calisma\test_gunlugu.txt"
if %SONUC%==0 (
  echo TESTLER GECTI. Pencere 5 saniye sonra kapanacak.
  timeout /t 5 >nul
  exit /b 0
)
echo TEST HATASI: %SONUC%. Pencereyi kapatabilirsiniz.
pause >nul
exit /b %SONUC%

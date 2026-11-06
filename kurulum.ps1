# HullOpt motoru: Windows kurulumu (Faz 0)
# Kullanım: kurulum.cmd dosyasına çift tıkla
#   ya da:  powershell -ExecutionPolicy Bypass -File .\kurulum.ps1
# Gerekli: Python 3.13 (python.org yükleyicisi)
# Betik yalnız bu klasörün içinde çalışır: .venv sanal ortamını kurar, paketleri yükler, testleri çalıştırır.
# Günlük: calisma\kurulum_gunlugu.txt

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
New-Item -ItemType Directory -Force -Path "calisma" | Out-Null
$gunluk = Join-Path $PSScriptRoot "calisma\kurulum_gunlugu.txt"
"HullOpt kurulum günlüğü - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $gunluk -Encoding utf8

function Yaz([string]$metin, [string]$renk = "Gray") {
    Write-Host $metin -ForegroundColor $renk
    Add-Content -Path $gunluk -Value $metin -Encoding UTF8
}

function Akit {
    # Bir komutun çıktısını hem ekrana hem günlüğe yazar.
    process { $s = "$_"; Write-Host $s; Add-Content -Path $gunluk -Value $s -Encoding UTF8 }
}

# 1) Python 3.13'ü bul
function PythonBul {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $yol = & py -3.13 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $yol) { return "$yol".Trim() }
    }
    $aday = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
    if (Test-Path $aday) { return $aday }
    return $null
}
$pyexe = PythonBul

# 1b) Yoksa: İndirilenler'deki resmi python.org yükleyicisini doğrula ve yalnız bu kullanıcı için kur.
#     PATH değiştirilmez (Anaconda vb. etkilenmez), .py dosya ilişkilendirmesi değiştirilmez.
if (-not $pyexe) {
    $beklenen = "5da012101ba2e17113a9a1cd901e9ce1441086f2b7ecb5de06ec94163899a190"  # python.org, python-3.13.17-amd64.exe
    $indirilen = Join-Path $env:USERPROFILE "Downloads"
    $yukleyici = $null
    foreach ($f in (Get-ChildItem -Path $indirilen -Filter "python-3.13.17-amd64*.exe" -ErrorAction SilentlyContinue)) {
        if ((Get-FileHash -Algorithm SHA256 -Path $f.FullName).Hash.ToLower() -eq $beklenen) { $yukleyici = $f.FullName; break }
    }
    if (-not $yukleyici) {
        Yaz "Python 3.13 kurulu değil ve İndirilenler'de doğrulanmış python-3.13.17-amd64.exe yok." "Red"
        Yaz "İndirme adresi: https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe" "Red"
        exit 1
    }
    Yaz "Yükleyici doğrulandı (SHA-256 python.org ile aynı): $yukleyici" "Green"
    Yaz "== Python 3.13.17 kuruluyor (yalnız bu kullanıcı için; birkaç dakika sürebilir)"
    Unblock-File -Path $yukleyici
    $islem = Start-Process -FilePath $yukleyici -Wait -PassThru -ArgumentList @(
        "/passive", "InstallAllUsers=0", "PrependPath=0", "AssociateFiles=0",
        "Include_launcher=1", "InstallLauncherAllUsers=0", "Include_test=0", "Include_doc=0")
    Yaz "Yükleyici çıkış kodu: $($islem.ExitCode)"
    $pyexe = PythonBul
}
if (-not $pyexe) {
    Yaz "Python 3.13 bulunamadı. Önce python.org'dan Python 3.13'ü kurun." "Red"
    exit 1
}
$surum = & $pyexe -c "import sys; print(sys.version.split()[0])"
Yaz "Python $surum bulundu: $pyexe" "Green"

# 2) Sanal ortam
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Yaz "== Sanal ortam kuruluyor (.venv)"
    & $pyexe -m venv .venv 2>&1 | Akit
    if ($LASTEXITCODE -ne 0) { Yaz "Sanal ortam kurulamadı." "Red"; exit 1 }
}
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# 3) Paketler (birkaç dakika sürebilir)
Yaz "== pip güncelleniyor"
& $py -m pip install --upgrade pip 2>&1 | Akit
Yaz "== Test edilmiş sürümler yükleniyor (engine\requirements-lock.txt)"
& $py -m pip install -r "engine\requirements-lock.txt" 2>&1 | Akit
if ($LASTEXITCODE -ne 0) {
    Yaz "Test edilmiş sürümler kurulamadı; uyumlu en yeni sürümler deneniyor." "Yellow"
    & $py -m pip install "numpy>=2.0" "scipy>=1.10" "matplotlib>=3.7" "pydantic>=2.0" "pyyaml>=6.0" pytest 2>&1 | Akit
    if ($LASTEXITCODE -ne 0) { Yaz "Paketler kurulamadı." "Red"; exit 1 }
}
Yaz "== hullopt kuruluyor (engine)"
& $py -m pip install -e "engine" 2>&1 | Akit
if ($LASTEXITCODE -ne 0) { Yaz "hullopt kurulamadı." "Red"; exit 1 }
& $py -m pip freeze | Out-File -Encoding utf8 "calisma\kurulu_surumler.txt"

# 4) Testler
Yaz "== Testler çalışıyor"
Push-Location engine
& $py -m pytest tests -q -p no:cacheprovider 2>&1 | Akit
$sonuc = $LASTEXITCODE
Pop-Location

if ($sonuc -eq 0) {
    Yaz "KURULUM TAMAM: testler geçti. Kurulu sürümler: calisma\kurulu_surumler.txt" "Green"
} else {
    Yaz "Testlerde hata var (çıkış kodu $sonuc). Günlük: calisma\kurulum_gunlugu.txt" "Yellow"
}
exit $sonuc

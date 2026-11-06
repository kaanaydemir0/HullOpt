# HullOpt: C# araçlarının kurulumu (Rhino 7 eklentisi için)
# Kullanım: csharp_kurulum.cmd dosyasına çift tıkla
#   ya da:  powershell -ExecutionPolicy Bypass -File .\csharp_kurulum.ps1
# Yapılanlar:
#   1) .NET SDK 10 yoksa İndirilenler'deki dotnet-sdk-10.0.403-win-x64.exe dosyasını
#      Microsoft'un yayımladığı SHA-512 değeriyle doğrular ve kurar (Windows yönetici onayı ister).
#   2) VS Code'a C# eklentisini (ms-dotnettools.csharp) kurar.
#   3) Eklenti projesi varsa derler (rhino\HullOpt).
# Günlük: calisma\csharp_kurulum_gunlugu.txt

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_NOLOGO = "1"
New-Item -ItemType Directory -Force -Path "calisma" | Out-Null
$gunluk = Join-Path $PSScriptRoot "calisma\csharp_kurulum_gunlugu.txt"
"HullOpt C# kurulum günlüğü - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $gunluk -Encoding utf8

function Yaz([string]$metin, [string]$renk = "Gray") {
    Write-Host $metin -ForegroundColor $renk
    Add-Content -Path $gunluk -Value $metin -Encoding UTF8
}

function Akit {
    # Bir komutun çıktısını hem ekrana hem günlüğe yazar.
    process { $s = "$_"; Write-Host $s; Add-Content -Path $gunluk -Value $s -Encoding UTF8 }
}

function DotnetBul {
    $adaylar = @()
    $komut = Get-Command dotnet -ErrorAction SilentlyContinue
    if ($komut) { $adaylar += $komut.Source }
    $adaylar += (Join-Path $env:ProgramFiles "dotnet\dotnet.exe")
    foreach ($d in $adaylar) { if ($d -and (Test-Path $d)) { return $d } }
    return $null
}

function Sdk10Var([string]$dotnet) {
    if (-not $dotnet) { return $false }
    $liste = & $dotnet --list-sdks 2>$null
    return [bool]($liste | Where-Object { "$_" -match '^10\.' })
}

# 1) .NET SDK 10
$dotnet = DotnetBul
if (-not (Sdk10Var $dotnet)) {
    # Microsoft yayın kaydı (release-metadata/10.0/releases.json), dotnet-sdk-10.0.403-win-x64.exe
    $beklenen = "cca8d82788252a8d13e778150a5e8393e204af274a12d19de7da10a0d98370a4edd5361ce5da73d0db13997fe5191e5db3a69c9665c6ddd6fba0d77845046f3c"
    $indirilen = Join-Path $env:USERPROFILE "Downloads"
    $yukleyici = $null
    foreach ($f in (Get-ChildItem -Path $indirilen -Filter "dotnet-sdk-10.0.403-win-x64*.exe" -ErrorAction SilentlyContinue)) {
        if ((Get-FileHash -Algorithm SHA512 -Path $f.FullName).Hash.ToLower() -eq $beklenen) { $yukleyici = $f.FullName; break }
        Yaz "Doğrulanamadı (SHA-512 farklı): $($f.FullName)" "Yellow"
    }
    if (-not $yukleyici) {
        Yaz ".NET SDK 10 kurulu değil ve İndirilenler'de doğrulanmış dotnet-sdk-10.0.403-win-x64.exe yok." "Red"
        Yaz "İndirme sayfası: https://dotnet.microsoft.com/en-us/download/dotnet/thank-you/sdk-10.0.403-windows-x64-installer" "Red"
        exit 1
    }
    Yaz "Yükleyici doğrulandı (SHA-512 Microsoft'unkiyle aynı): $yukleyici" "Green"
    Unblock-File -Path $yukleyici
    Yaz "== .NET SDK 10.0.403 kuruluyor. Windows yönetici onayı isteyecek: 'Evet'e basın."
    $islem = Start-Process -FilePath $yukleyici -Wait -PassThru -ArgumentList @("/install", "/passive", "/norestart")
    Yaz "Yükleyici çıkış kodu: $($islem.ExitCode) (0 ya da 3010 başarılı demek; 1602 iptal)"
    $dotnet = DotnetBul
    if (-not (Sdk10Var $dotnet)) {
        Yaz ".NET SDK 10 kurulamadı." "Red"
        exit 1
    }
}
Yaz ".NET SDK hazır: $dotnet" "Green"
& $dotnet --list-sdks 2>&1 | Akit

# 2) VS Code C# eklentisi
$code = Join-Path $env:LOCALAPPDATA "Programs\Microsoft VS Code\bin\code.cmd"
if (-not (Test-Path $code)) {
    $komut = Get-Command code -ErrorAction SilentlyContinue
    if ($komut) { $code = $komut.Source }
}
if (Test-Path $code) {
    $ekler = & $code --list-extensions 2>$null
    if ($ekler -contains "ms-dotnettools.csharp") {
        Yaz "VS Code C# eklentisi zaten kurulu." "Green"
    } else {
        Yaz "== VS Code'a C# eklentisi kuruluyor (ms-dotnettools.csharp)"
        & $code --install-extension ms-dotnettools.csharp 2>&1 | Akit
    }
} else {
    Yaz "VS Code bulunamadı; C# eklentisi kurulmadı." "Yellow"
}

# 3) Eklenti projesi
$proje = Join-Path $PSScriptRoot "rhino\HullOpt\HullOpt.csproj"
if (Test-Path $proje) {
    Yaz "== Eklenti derleniyor (rhino\HullOpt)"
    & $dotnet build $proje -c Debug -nologo 2>&1 | Akit
    $sonuc = $LASTEXITCODE
    if ($sonuc -eq 0) {
        Yaz "KURULUM TAMAM: .NET SDK ve VS Code C# eklentisi hazır, eklenti derlendi." "Green"
    } else {
        Yaz "Derleme hatası (çıkış kodu $sonuc). Günlük: calisma\csharp_kurulum_gunlugu.txt" "Yellow"
    }
    exit $sonuc
}
Yaz "KURULUM TAMAM: .NET SDK ve VS Code C# eklentisi hazır." "Green"
exit 0

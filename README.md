# HullOpt

Rhino 7 için gövde formu direnç optimizasyonu eklentisi. Gövdeyi Rhino'da seçersiniz; eklenti hidrostatiği, direnci ve fren gücünü hesaplar, gövdeyi sekiz düzgün biçim moduyla büker ve kısıtlı bir aramayla direnci ya da fren gücünü en aza indiren adayı arar.

**Sürüm:** eklenti 0.5.2, motor (hullopt) 0.6.0 · 04.11.2026 · Takım-1

Kişisel öğrenci projesi; CFD yok. Araç genel amaçlıdır: gövdeye özgü değerler motorun varsayılanlarında değil, gövdenin kendisinden ve paneldeki ayarlardan gelir. Eklentinin arayüzü İngilizce, belgeler Türkçe.

## Ne yapar

| Sekme | İş |
|---|---|
| Hull | Gövde ve takıntı yüzeylerini seçer; seçim .3dm dosyasının içinde saklanır |
| Condition | Su (ρ, ν), yüzme konumu (su hattı z'si ya da deplasman), hızlar (aralık ve tek tek hızlar), tasarım hızı |
| Hydrostatics | Hacim, deplasman, LWL, BWL, T, LCB, KB, LCF, ıslak alan, batık ayna alanı, katsayılar, giriş açısı, kesit alanı eğrisi; su hattını modele çizer |
| Resistance | Holtrop 1984, Michell ince gemi + ITTC-57, ITTC-57; tablo, grafik, CSV |
| Propulsion | Wageningen B-serisi pervane, Keller ölçütü, sevk katsayıları, paylar; P_E, P_B, makine yükü, %100 MCR'de en yüksek hız |
| Morph | Sekiz düzgün (C2) biçim modu; aday başlangıç deplasmanında yüzdürülür, sınırlar ve düzgünlük kapılarıyla denetlenir, istenirse Rhino'da kurulur |
| Optimise | Diferansiyel evrim ve Deb kuralı; amaç J, seçilen büyüklüğün (Holtrop R_T, Michell kısmi toplamı ya da P_B) başlangıç gövdesine oranı; hızlar eşit ağırlıkla, işletme profiline göre ya da yalnız servis hızında tartılır |

Hesaplar ayrı süreçte çalışan Python motorunda yapılır; eklenti onunla satır başına bir JSON-RPC 2.0 mesajıyla konuşur (`hullopt serve`). Seçilen yüzeyler NURBS olarak motora gider; motor ağı örer, hesaplar ve sonucu panele geri verir.

## Belgeler

- [Eklenti notları](rhino/README.md): sekmeler, komutlar, dosyalar, derleme ve yükleme, hata ayıklama.
- [Değişiklik günlüğü](CHANGELOG.md)

## Gereksinimler

- Windows 10 ya da 11, 64 bit
- Rhino 7 (7.1 SR1'de geliştirildi ve sınandı)
- Python 3.13 (motor için; `kurulum.cmd` kurar)
- .NET SDK 10 (yalnız eklentiyi derlemek için; `csharp_kurulum.cmd` kurar)

## Kurulum

1. Depoyu klonlayın: `git clone https://github.com/kaanaydemir0/HullOpt.git`
2. Python 3.13.17 yükleyicisini (`python-3.13.17-amd64.exe`, python.org) İndirilenler klasörüne indirin ve `kurulum.cmd` dosyasına çift tıklayın. Betik yükleyiciyi SHA-256 ile doğrular, Python'u yalnız bu kullanıcı için kurar (PATH değişmez), `.venv` sanal ortamını kurar, `engine/requirements-lock.txt` sürümlerini yükler ve testleri çalıştırır. Günlük: `calisma\kurulum_gunlugu.txt`.
3. .NET SDK 10.0.403 yükleyicisini (`dotnet-sdk-10.0.403-win-x64.exe`) İndirilenler'e indirin ve `csharp_kurulum.cmd` dosyasına çift tıklayın. Betik yükleyiciyi Microsoft'un SHA-512 değeriyle doğrulayıp kurar, VS Code'a C# eklentisini ekler ve eklentiyi derler. Sonraki derlemeler: `rhino\derle.cmd`.
4. Rhino 7'de `PlugInManager` → Install… → `rhino\HullOpt\bin\Debug\net48\HullOpt.rhp`. Rhino sonraki açılışlarda eklentiyi kendisi yükler.
5. `HoPanel` komutu paneli açar. Eklenti motoru, `.rhp` dosyasının klasöründen yukarı doğru `.venv` ve `engine\hullopt` içeren klasörü arayarak bulur; başka yerdeyse `HULLOPT_PROJECT` ortam değişkenini verin.

## Hızlı başlangıç

1. **Hull** → *Select hull surfaces*: gövdenin dış kabuğunu güverte kenarına kadar seçin. Kabuk su hattının altında kapalı olmalı.
2. **Condition**: su, su hattı z'si ya da deplasman, hızlar.
3. **Hydrostatics** → *Run hydrostatics*; **Resistance** → *Run resistance*.
4. **Morph**: bir iki modu küçük genliklerle deneyin, *Evaluate*.
5. **Optimise**: amacı ve hızların tartısını seçin, *Run optimisation*. En iyi adayı *Evaluate on the Morph tab* ile denetleyin, *Build in Rhino* ile kurun.

## Depo düzeni

| Klasör | İçinde |
|---|---|
| `engine/` | Hesap motoru `hullopt` (Python paketi), testler, sürüm kilidi |
| `rhino/` | Rhino 7 eklentisi (C#, .NET Framework 4.8, Eto) ve `derle.cmd` |
| `docs/` | Belgeler ve görseller |
| kök | Windows kurulum betikleri (`kurulum.cmd`, `csharp_kurulum.cmd`) ve `testler.cmd` |

Depoya girmeyenler (`.gitignore`): `girdiler/` (büyük geometri girdileri), `calisma/` (koşu çıktıları ve günlükler), `.venv/` ve derleme çıktıları.

## Motor komutları

`hullopt measure | equilibrium | baseline | transform | resistance <çalışma.yaml>` komut satırından bir çalışma dosyasıyla hesap yapar; çalışma dosyasının alanları `engine/hullopt/config.py` içinde tanımlıdır. `hullopt serve` eklentinin kullandığı JSON-RPC 2.0 servisidir (standart giriş ve çıkış). Ayrıntı: `hullopt <komut> --help`.

## Testler

`testler.cmd` motorun testlerini çalıştırır; günlük `calisma\test_gunlugu.txt`. Testler yapay gövdelerle (kutu, parabolik gövde, Wigley) çalışır. Bir yat projesinin verisine dayanan testler (hidrostatik ve direnç altın kaydı, referans hesap tablosuyla eşitleme) bu depoda yok.

## Durum ve sınırlar

- Çalışan sekmeler: Hull, Condition, Hydrostatics, Resistance, Propulsion, Morph, Optimise. Sırada: AP/FP ve çizgi işaretleme, Maxsurf CSV içe aktarma, sonuç, rapor ve dışa aktarma sekmeleri, yakıt ve menzil.
- CFD yok. J, adayları başlangıç gövdesiyle aynı Python modelinde karşılaştırır; doğrulanmış bir direnç kazancı değildir.
- Hidrostatik, DELFTship Free 17.40'ın iki örnek modelinde DELFTship'le karşılaştırıldı: hacim farkı %0,002 ve %0,07 (Python).
- Holtrop ve sevk hesabı, aynı girdilerle bir referans hesap tablosunun sonucunu 10⁻¹⁴ düzeyinde tekrar ediyor (Python); o tablo ve test verisi bu depoda yok.
- Michell'in mutlak değeri ayna kapanışına çok duyarlıdır; yalnız iki gövdeyi aynı ayarlarla karşılaştırmak için kullanılır.
- Hesabın başında gövde arayüz iş parçacığında dışa aktarılır; büyük gövdelerde Rhino bir an duraklar.

## Çalışma kuralları

- Her sayı kaynağıyla etiketlenir: Python, Maxsurf, Excel, CFD.
- Hesaplanmamış direnç kazancı yazılmaz.
- Var olan sonuç dosyasının üzerine yazılmaz; her koşu yeni klasöre gider.

## Lisans

Kişisel öğrenci projesi; lisans dosyası eklenmedi.

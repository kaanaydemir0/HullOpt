# Rhino eklentisi

**Hedef: Rhino 7, C#; arayüz İngilizce.** Komut adları, panel ve mesajlar İngilizce; bu belge Türkçe.

- Eklenti .NET Framework 4.8 için derlenir: `HullOpt.rhp`.
- RhinoCommon, Eto ve Rhino.UI kurulu Rhino 7'den alınır (`C:\Program Files\Rhino 7\System`). Rhino başka bir klasördeyse derlerken `-p:RhinoSystemDir=...` verilir.
- Rhino 7.1 (SR1) ile derlendi ve denendi. Eklenti 7.1'in kütüphaneleriyle derlenir; 7.1'den sonra eklenen RhinoCommon özellikleri kullanılmaz.
- Hesap motoru ayrı süreçte çalışır (Python 3.13, proje klasöründeki `.venv`). Eklenti motoru, `.rhp` dosyasının klasöründen yukarı doğru içinde `.venv` ve `engine\hullopt` olan klasörü arayarak bulur. `HULLOPT_PROJECT` ortam değişkeniyle de verilebilir.

## Sürüm 0.2 (01.11.2026): hidrostatik ve direnç

Eklenti motorla JSON-RPC 2.0 üzerinden konuşur: `python -m hullopt serve` sürecini bir kez başlatır, her satıra bir JSON mesajı yazar (`engine/hullopt/service.py`). Motorun varsayılanlarında gövdeye özgü değer yok; her değer panelden gelir.

| Sekme | Ne yapar |
|---|---|
| Hull | Gövde ve takıntı yüzeylerini seçer (`HoHull`, `HoAppendages`); seçim .3dm dosyasının içinde saklanır. Ağ inceliği: standart (yüzey yönü başına en çok 50 nokta) ya da ince (100). Çalışma klasörünü açar. |
| Condition | Su: deniz ya da tatlı su 15 °C (ITTC) veya elle ρ, ν. Yüzme konumu: su hattı yüksekliği z (model birimi) ya da deplasman (t; su hattını motor bulur). Hız aralığı, tasarım hızı; LWL'ye göre Fn 0,10–0,50 hız önerisi. |
| Hydrostatics | Hacim, deplasman, LWL, BWL, T, LCB, KB, LCF, ıslak alan, batık ayna alanı, Cb, Cp, Cx, Cwp, iE; kesit alanı eğrisi. Su hattını `HullOpt::Waterline` katmanına çizer. |
| Resistance | Holtrop 1984 (toplam), Michell + ITTC-57 (kısmi), ITTC-57 sürtünme. Holtrop seçenekleri: iE (ölçülen, regresyon, verilen), LCB referansı (LWL ortası ya da AP–FP ortası), kıç biçimi, pürüzlülük, ayna yüzü. Tablo, grafik (direnç ya da PE), CSV, uyarılar. |

Komutlar: `HoSetup`, `HoHull`, `HoAppendages`, `HoCondition`, `HoHydrostatics`, `HoResistance`, `HoPanel`, `HoEngineCheck`.

Akış: seçilen yüzeyler motorun okuduğu `hgeo.json` biçimine yazılır (`calisma\rhino\<model adı>\hull.hgeo.json`), motor ağı örer ve hesaplar, sonuç panele ve modele gelir. Gövde değişmediyse dosya yeniden yazılmaz, motor ağı yeniden örmez.

| Dosya | Ne |
|---|---|
| `HullOpt/HullOptPlugIn.cs` | Eklenti: paneli kaydeder, Rhino kapanırken motoru durdurur |
| `HullOpt/HullOptPanel.cs`, `HullOpt/UI/` | Panel ve sekmeleri, grafik çizimi |
| `HullOpt/Session.cs` | Belge, motor ve panel arasındaki iş akışı; su hattı çizimi |
| `HullOpt/Engine/EngineClient.cs`, `Json.cs` | JSON-RPC istemcisi (ASCII JSON, ilerleme bildirimi, iptal) |
| `HullOpt/Export/HgeoWriter.cs` | Rhino yüzeyleri → `hullgeo-1` (homojen NURBS kontrol noktaları, düğümler, UV'de trim döngüleri) |
| `HullOpt/Model/HullDefinition.cs` | Kullanıcının seçimleri; .3dm içinde belge metni olarak saklanır |
| `HullOpt/Commands/` | Komutlar |
| `derle.cmd` | Çift tıklayınca derler; günlük `calisma\derleme_gunlugu.txt` |

## Sürüm 0.3 (01.11.2026): biçim modları (Morph)

Yeni **Morph** sekmesi ve `HoMorph` komutu. Gövde sekiz düzgün (C2) biçim moduyla bükülür: kıç ve baş yarılarında kesitleri boyuna kaydırma, ince baş, ince baş ucu, dar kıç, kıç tabanını kaldırma, dolgun sintine, düz taban. Modlar NURBS kontrol noktalarını kaydırır; düğümler, ağırlıklar ve trimler değişmez. Baş, kıç ve orta gövde modları üst eğride söner ve güverte kenarını yerinde bırakır; Lackenby türü iki mod kesitleri güverte kenarıyla birlikte boyuna kaydırır (üst kenar sınırı bunu sınırlar).

- **Evaluate:** motor (`morph.frame`, `morph.evaluate`) adayı başlangıç deplasmanında yüzdürür ve başlangıçla karşılaştırır: su hattı, LWL, BWL, T, LCB, ıslak alan, Cp, iE. Kapılar: su çekimi değişimi, LCB kayması, LWL ve BWL değişimi, yüzeyin normal yönde en büyük hareketi, üst kenar hareketi, mod eğimleri, düzgünlük kapıları G1, G2, G3 ve G5. İstenirse Resistance sekmesinin yöntemleriyle direnç oranı R/R₀ (Condition sekmesinin hızlarında).
- **Limits:** gövdeye göre oransal (% T, % LWL), bu yüzden her boyda gövdeye uyar.
- **Build candidate in Rhino** (`HoMorph _Build`): motor (`morph.export`) her yüzün yeni kontrol noktalarını verir. Eklenti seçili gövde nesnelerinin kopyasını alır, her yüze yeni yüzeyini verir (`BrepFace.ChangeSurface`; trimler parametre uzayında aynen kalır), kenarları yeniden kurar. Adaylar `HullOpt::Candidates` katmanına gruplu konur; modeldeki yüzeyler değişmez. Yalnız gövde bükülür; takıntılar bükülmez ve karşılaştırmaya girmez (deplasmanla yüzmede su hattı 0.5.1'den beri takıntılarla birlikte bulunur).
- Sayı kutuları artık her tuşta değil, Enter'a basınca ya da kutudan çıkınca kaydedilir.

| Dosya | Ne |
|---|---|
| `HullOpt/UI/MorphPage.cs` | Morph sekmesi |
| `HullOpt/Export/CandidateBuilder.cs` | Motorun kontrol noktalarından aday gövdeyi Rhino'da kurar |

## Sürüm 0.4 (01.11.2026): sevk ve fren gücü

Yeni **Propulsion** sekmesi ve `HoPropulsion` komutu. Fren gücü Holtrop 1984'ün toplam direncinden hesaplanır (Resistance sekmesinin seçenekleriyle); hızlar ve tasarım hızı Condition sekmesinden gelir. Tasarım hızı listede yoksa hesaba eklenir.

- **Pervane:** pervane sayısı, kanat sayısı Z, çap D (m), şaft merkezinin model z'si. P/D boşsa tasarım hızında en yüksek açık su verimini veren P/D (0,5–1,4, 0,025 adım); A_E/A_0 boşsa Keller'in en küçüğü, 0,05'e yukarı yuvarlanmış. Tasarım itkisi servis (itki × (1 + deniz payı)) ya da sakin su.
- **Sevk katsayıları:** iz w, itme azalması t, bağıl dönme verimi η_R. Holtrop 1984 ikiz pervane formülleri ya da verilen değer; iz için Taylor da (tek ya da ikiz pervane). Tek pervanede ya da 3–4 pervanede Holtrop formülleri kullanılamaz, değer verilir.
- **Paylar ve makine:** ek takıntı ve hava payı (% R), deniz payı (% güç), şaft ve dişli verimi η_S, makine sayısı (1–8; 0.5.1'e kadar panelde 1–4), makine başına MCR, sürekli servis oranı, PTO, isteğe bağlı makine devri (dişli oranı için).
- **Sonuç:** her hızda P_E, w, t, η_H, J, η0, η_D, pervane devri, P_B (sakin su ve deniz paylı), % MCR ve notlar (Holtrop'un aralık dışı notları dahil); tasarım noktası özeti, gerekli kurulu güç, %100 MCR'de en yüksek hız; grafik ve CSV.
- **Doğrulama:** bir referans direnç–sevk hesap tablosuyla aynı girdilerle 41 hızda P_B farkı 8·10⁻¹⁵ (Python). O tablo ve test verisi bu depoda yok.
- Sevk hesabı yalnız direnç koşusuyla gelir: Propulsion sekmesindeki kutu işaretliyse her direnç koşusunda, değilse yalnız *Run resistance and power* ya da `HoPropulsion` ile.

## Sürüm 0.3.1 (01.11.2026): istenen her hız

- **Condition → Also at:** aralığın (from–to–step) yanına tek tek hızlar ve başka aralıklar yazılır: `13,7; 17; 18-24:2`. Öğeler `;` ya da boşlukla ayrılır; ondalık nokta da virgül de olur. `a-b` yukarıdaki adımı, `a-b:s` kendi adımını kullanır. Hepsi birleşir, sıralanır, aynı hız bir kez sayılır; en çok 1000 hız. Kutunun altındaki not hız sayısını ve aralığı yazar.
- Direnç, Morph karşılaştırması ve sonra gelecek optimizasyon bu hızların hepsinde çalışır. Bir hız yöntemin yayımlanmış aralığı dışındaysa (Holtrop'ta Fn > 0,8) ya da Michell'in dış integrali o hızda yakınsamadıysa değer atılmaz; tablonun **Notes** sütununda o hız için yazılır. CSV'ye de bu sütun girer.
- Michell bir hızda kesme değeri yetmezse taban kesmeyi kendisi ikiye katlar (taban en çok 1024, motor ayarı `lambda_max_limit`; döndürülen integral iki katı kesmeyle hesaplandığı için λ 2048'e kadar uzanır). Bir test gövdesinde 50–100 kn bu yolla yakınsadı; önceden bu hızlarda bütün koşu hata veriyordu. Yakınsayan hızların sonucu değişmedi (10⁻¹² içinde).
- Michell her hız için ilerleme bildirir. Çok düşük hızlar yavaştır (bir test gövdesinde, bulut ortamında: 2 kn yaklaşık 8 sn, 1 kn 25 sn, 0,5 kn 90 sn). Bu hızlarda dalga direnci zaten sıfıra yakındır.
- Michell'in mutlak değerleri ayna kapanış uzunluğuna çok duyarlıdır. Michell'i iki gövdeyi aynı ayarlarla karşılaştırmak için kullanın.

## Sürüm 0.5 (02.11.2026): optimizasyon (Optimise)

Yeni **Optimise** sekmesi ve `HoOptimise` komutu. Arama, Morph sekmesinin biçim modlarını değiştirerek en küçük J'yi arar. J, seçilen büyüklüğün başlangıç gövdesine oranıdır; başlangıç gövdesinde J = 1. Motor tarafı `optimize.run` (motor 0.6.0, `engine/hullopt/optimize/search.py`).

- **Minimise:** Holtrop 1984 toplam direnci R_T; Michell dalga direnci + ITTC-57 sürtünmesi (kısmi toplam, yalnız göreli karşılaştırma için); fren gücü P_B (Holtrop 1984 + Propulsion sekmesi). P_B'de pervane her adayda başlangıç gövdesinin P/D'sini ve kanat alanını korur.
- **Speeds** (kullanıcı seçer):
  - *Equal weight* (varsayılan): Condition sekmesindeki bütün hızlarda Q/Q₀ oranlarının ortalaması. Her hızdaki değişim aynı sayılır.
  - *Operating profile*: `hız:pay` öğeleri, ör. `12:60; 14:20; 16:20`. J = Σ w·Q / Σ w·Q₀; payların toplamının 100 olması gerekmez. P_B'de bu oran sevk enerjisinin oranıdır.
  - *Service speed only*: yalnız servis hızında Q/Q₀. Kutu boşsa Condition sekmesinin tasarım hızı kullanılır.
- **Kısıtlar:** her aday başlangıç deplasmanında yüzdürülür. Morph sekmesinin sınırlarından (su çekimi, LCB, LWL, BWL, yüzey hareketi, üst kenar, mod eğimleri) ya da düzgünlük kapılarından (G1, G2, G3, G5) birine takılan aday uygun sayılmaz ve kazanamaz; direnci de hesaplanmaz.
- **Shape modes to vary:** her mod için alt ve üst sınır, Morph sekmesinin birimleriyle. Boş sınır, gövdeye oranlı varsayılanı alır (yanında yazılı).
- **Search:** diferansiyel evrim (current-to-pbest/1 ve rand/1/bin) ve Deb kuralı: uygun adaylar J'ye, ötekiler kapıları ne kadar aştıklarına göre sıralanır. Nüfus en az 30 (varsayılan 30), değerlendirme sayısı varsayılan 600, rastgele sayı tohumu varsayılan 170026; aynı tohum ve ayarlar aynı aramayı verir. *First generation*: sınırlar içine yayılır ya da başlangıç gövdesinin çevresine toplanır. Aranan bütün modların sınırları sıfırı içerdiğinde (boş sınırların varsayılanları içerir) başlangıç gövdesi ilk nesildedir; bir modun sınırları sıfırı içermiyorsa başlangıç gövdesi aday olarak denenmez.
- **İzleme:** ilerleme çubuğu; nesil, değerlendirme sayısı, uygun aday sayısı, en iyi J, geçen ve kalan süre; canlı grafikte değerlendirmelere göre en iyi uygun J. Panel en çok saniyede bir yenilenir. **Stop** (ya da paneldeki **Cancel**) aramayı o anki aday bitince durdurur; o ana kadar bulunanlar gösterilir.
- **Sonuç:** en iyi uygun adaylar (en çok 10), seçilen adayın hız hız oranları, notlar (kullanılan sınırlar, adayları eleyen kapıların sayısı, kayıt dosyası) ve değerlendirmelere göre en iyi J ile uygun nüfusun ortanca J'si grafiği. *Evaluate on the Morph tab* adayın genliklerini Morph sekmesine taşır ve orada değerlendirir; *Build in Rhino* adayı `HullOpt::Candidates` katmanında kurar; *Save candidates as CSV…* adayları CSV'ye yazar.
- **Kayıt:** her değerlendirme `calisma\rhino\<model adı>\optimise_<tarih>_<saat>.jsonl` dosyasına bir satır olarak yazılır: genlikler, uygunluk, kapı aşımı, J, hız hız oranlar, takılan kapılar, hata.
- J, adayları başlangıç gövdesiyle aynı Python modelinde karşılaştırır; doğrulanmış bir direnç kazancı değildir.

| Dosya | Ne |
|---|---|
| `HullOpt/UI/OptimisePage.cs` | Optimise sekmesi |
| `engine/hullopt/optimize/search.py` | Aramanın motor tarafı: amaç, kapılar, diferansiyel evrim, kayıt |

## Sürüm 0.5.1 (02.11.2026): düzeltmeler

Motorun hesapları değişmedi (motor 0.6.0); motorda yalnız açıklamalar düzeltildi.

- **Takıntılar ve deplasman:** motorun biçim modeli yalnız HULL katmanını büker ve yüzdürür. Takıntılar dahil ve yüzme deplasmanla tanımlıyken eklenti artık önce `hydro.equilibrium` ile su hattını takıntılarla birlikte bulur (Hydrostatics sekmesi gibi) ve Morph, Optimise ve aday kurma isteklerine bu z'yi verir. Önceden su hattı yalnız gövdeyle bulunuyordu ve başlangıç Hydrostatics'ten farklıydı. Morph ve Optimise'ın hacim, ıslak alan ve direnç değerleri çıplak gövdenindir.
- **Çalışma klasörü:** Rhino açıkken başka bir model açılınca panel klasörü `Untitled` gösteriyordu; panel artık modelin adı belli olunca bir kez daha yenileniyor. Panel klasörün yolunu yalnız gösteriyor, klasörü ilk hesapta açıyor; boş `calisma\rhino\Untitled` klasörü artık oluşmuyor.
- **Etiketler:** "Draft T (to lowest point)" (Hydrostatics ve Morph), "Max. section coefficient Cx", Narrower stern notu "+ narrows the afterbody toward the transom".
- **Optimise notu:** "The baseline is in the first generation when the bounds of every varied mode include zero."
- **Propulsion:** makine sayısı 1–8 (motor zaten 1–8 kabul ediyordu).
- Kod açıklamaları kodun yaptığıyla uyumlu hale getirildi (ilk nesil, Michell kesmesi, Lackenby modları ve üst kenar, düzgünlük çizgileri, giriş açısı, Holtrop LCB birimi, eğim sınırları, trim örnekleme, su hattı parçaları).

## Sürüm 0.5.2 (04.11.2026): Optimise ve Morph sekmelerinde takılma

Motor ve hesaplar değişmedi (motor 0.6.0).

- **Belirti:** Optimise sekmesi açıkken Rhino takılıyordu: arama sürerken her ilerleme iletisinde 1–2 s donma, arama bitince de boşta yaklaşık 1,9 s'de bir donma. Morph sekmesinde her düzen yaklaşık 0,9 s sürüyordu.
- **Neden:** Optimise'ın mod tablosunda ve Morph'un mod ve sınır tablolarında (açık `TableLayout`) etiketler satır kaydırıyordu; Eto'da `Label`'ın varsayılanı `WrapMode.Word`. Sayfa panelden genişken (yatay kaydırma çubuğu varken) WPF bu tablolarda düzeni durmadan yeniliyordu: sekme boştayken 5 s'de 153 `LayoutUpdated`, her düzen geçişi 1,3–1,9 s. Panelin altındaki durum yazısı her değiştiğinde de sekme yeniden düzenleniyordu. 0.5'te uzun aramada görülen yavaşlamanın nedeni de buydu.
- **Düzeltme:** bir tablo satırını başka hücrelerle paylaşan etiketler tek satır: `Ui.Field` ve `Ui.Unit` artık kaydırmaz; tablolardaki gri notlar için `Ui.Hint` eklendi (tek satır, dikeyde ortalı). Kaydıran uzun açıklamalar (`Ui.Note`) yalnız kendi satırlarında. Kural `UI/Controls.cs`'in başında yazılı.

## Derleme ve yükleme

1. İlk kurulum: kök klasördeki `csharp_kurulum.cmd`. .NET SDK 10'u ve VS Code C# eklentisini kurar, sonra derler.
2. Sonraki derlemeler: `derle.cmd` ya da `dotnet build rhino\HullOpt\HullOpt.csproj -c Debug`.
   - Rhino açıkken de derlenir: açık Rhino'nun kilitlediği eski `.rhp` yeniden adlandırılır (`HullOpt.rhp.eski_*`), yenisi yerine konur. Yeni sürüm Rhino yeniden açılınca yüklenir. Eski kopyalar bir sonraki derlemede silinir.
3. Çıktı: `rhino\HullOpt\bin\Debug\net48\HullOpt.rhp`.
4. Rhino'ya yükleme bir kez yapılır: `PlugInManager` → Install… → `HullOpt.rhp`. Sonra Rhino her açılışta kendisi yükler.
5. Panel: `HoPanel` komutu. İlk açılışta yüzen pencere olarak gelir. Sekmesini Rhino'nun panel alanına sürükleyince orada kalır; alt kenarından boyu ayarlanır.

## VS Code ile hata ayıklama (isteğe bağlı)

VS Code'un kendi ayar klasörüne (`.vscode`) bu iki dosyayı elle kaydedin.

`.vscode/tasks.json`:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "eklentiyi derle",
      "type": "process",
      "command": "dotnet",
      "args": ["build", "${workspaceFolder}/rhino/HullOpt/HullOpt.csproj", "-c", "Debug", "-nologo"],
      "group": { "kind": "build", "isDefault": true },
      "problemMatcher": "$msCompile"
    }
  ]
}
```

`.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Rhino 7 ile HullOpt",
      "type": "clr",
      "request": "launch",
      "preLaunchTask": "eklentiyi derle",
      "program": "C:\\Program Files\\Rhino 7\\System\\Rhino.exe",
      "args": ["/nosplash"],
      "cwd": "${workspaceFolder}",
      "stopAtEntry": false,
      "console": "internalConsole"
    }
  ]
}
```

F5 eklentiyi derler ve Rhino 7'yi hata ayıklayıcıyla açar. Bu, VS Code C# eklentisinin .NET Framework hata ayıklayıcısıdır (`clr`); yalnız 64 bit programlarda ve taşınabilir PDB ile çalışır, proje de buna göre ayarlı.

## Sonraki adımlar

- Gövde tanımı: AP/FP, taban çizgisi, işaretli çizgiler, dondurulmuş bölgeler; geometriden otomatik öneri.
- Maxsurf CSV içe aktarma ve karşılaştırma.
- Sonuç gezgini (`HoResults`): optimizasyon adaylarını SQLite'a kaydetmek ve karşılaştırmak. Aramada Latin hiperküp ön taraması, paralel değerlendirme ve kaldığı yerden devam.
- Rapor (`HoReport`), dışa aktarma (`HoExport`).
- Panel düzeni: geniş tablolar (ör. Optimise'ın aday tablosu) paneli pencereden genişletiyor, grafiklerin sağ ucu ancak yana kaydırınca görünüyor.
- Morph: çerçeveyi ve düzgünlük planını panelde gösterip düzenlemek; ilk değerlendirmeyi hızlandırmak (ince ağda bu bilgisayarda yaklaşık 1,5 dakika, sonrakiler 1 saniyeden kısa).

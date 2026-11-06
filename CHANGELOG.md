# Değişiklik günlüğü

Her sürüm eklentinin (Rhino, C#) ve motorun (hullopt, Python) sürüm numarasıyla yazılır, en yenisi üstte. Ayrıntı `rhino/README.md` içindedir.

## Eklenti 0.5.2 · motor 0.6.0 — 04.11.2026

Düzeltme: Optimise ve Morph sekmelerinde Rhino'nun takılması. Motor ve hesaplar değişmedi.

- **Neden:** Optimise'ın mod tablosunda, Morph'un mod ve sınır tablolarında etiketler satır kaydırıyordu (Eto'nun varsayılanı). Sayfa panelden genişken WPF bu tablolarda düzeni durmadan yeniden hesaplıyordu; her düzen 1,3–1,9 s sürdüğü için Rhino arama boyunca ve sonrasında donup çözülüyordu. 0.5'te uzun aramada görülen yavaşlamanın nedeni de buydu.
- **Düzeltme:** başka hücrelerle aynı tablo satırındaki etiketler tek satır: `Ui.Field` ve `Ui.Unit` kaydırmaz, tablolardaki gri notlar yeni `Ui.Hint` ile tek satırdır. Uzun açıklamalar (`Ui.Note`) kendi satırlarında kaydırmaya devam eder.
- **Denendi** (Rhino 7.1; süreler Rhino'da ölçüldü): Optimise sekmesinin ilk açılışı 1348 ms → 69 ms; durum satırı değişince düzen Optimise'da 1324–1516 ms → 0,3–0,8 ms, Morph'ta 893 ms → 0,3 ms. 90 değerlendirmelik aramada takılma 58 → 1 (0,75 s, başta gövdenin dışa aktarılması); arama bitince boşta takılma yok. Arama sonucu değişmedi: kayıt dosyası bayt bayt aynı. Derleyici 0 uyarı, 0 hata.

## Eklenti 0.5.1 · motor 0.6.0 — 02.11.2026

Düzeltmeler. Motorun hesapları değişmedi; motorda yalnız açıklamalar düzeltildi.

- **Takıntılar ve deplasman:** takıntılar dahil ve yüzme deplasmanla tanımlıyken Morph, Optimise ve aday kurma, su hattını Hydrostatics gibi takıntılarla birlikte bulur, sonra gövdeyi bu su hattından başlatır. Önceden su hattı yalnız gövdeyle bulunduğu için başlangıç Hydrostatics'ten farklıydı.
- **Çalışma klasörü:** Rhino açıkken başka bir model açılınca panel klasörü "Untitled" gösteriyordu; artık modelin adı belli olunca yenileniyor. Panel klasörü yalnız gösterirken oluşturmuyor.
- **Etiketler:** Hydrostatics ve Morph tablolarında "Draft T (to lowest point)"; Hydrostatics'te "Max. section coefficient Cx"; Morph'ta Narrower stern notu "+ narrows the afterbody toward the transom".
- **Optimise notu:** başlangıç gövdesi yalnız aranan bütün modların sınırları sıfırı içerdiğinde ilk nesildedir.
- **Propulsion:** makine sayısı 1–8 arası seçilebiliyor.
- **Kod açıklamaları** kodun yaptığıyla uyumlu hale getirildi.

## Eklenti 0.5 · motor 0.6.0 — 02.11.2026

- Optimise sekmesi ve `HoOptimise` komutu; motorda `optimize.run`.
- Diferansiyel evrim (current-to-pbest/1 ve rand/1/bin) ve Deb kuralı. Morph sekmesinin sınırları ve düzgünlük kapıları kısıttır; her aday başlangıç deplasmanında yüzdürülür.
- Amaç J: Holtrop R_T, Michell kısmi toplamı ya da P_B'nin başlangıç gövdesine oranı. Hızlar eşit ağırlıkla, işletme profiline göre ya da yalnız servis hızında tartılır; kullanıcı seçer.
- Canlı ilerleme ve grafik, Stop (durdurma dosyası), en iyi adaylar, adayı Morph sekmesinde değerlendirme ve Rhino'da kurma, CSV. Her değerlendirme `calisma\rhino\<model>\optimise_<tarih>.jsonl` kaydına yazılır.

## Eklenti 0.4 · motor 0.5.0 — 01.11.2026

- Propulsion sekmesi ve `HoPropulsion` komutu: Wageningen B-serisi, Keller ölçütü, en iyi P/D, Holtrop ikiz pervane w, t, η_R (ya da Taylor ya da verilen değer), paylar, makine yükü, %100 MCR'de en yüksek hız.

## Eklenti 0.3.1 · motor 0.4.1 — 01.11.2026

- İstenen her hızda hesap: aralığa ek olarak tek tek hızlar ve başka aralıklar (`13,7; 17; 18-24:2`), en çok 1000 hız.
- Yöntemin aralığı dışındaki ya da yakınsamayan hız atılmaz, Notes sütununda yazılır. Michell yakınsamayan hızda kesmeyi kendisi büyütür.

## Eklenti 0.3 · motor 0.4.0 — 01.11.2026

- Morph sekmesi ve `HoMorph` komutu: sekiz düzgün biçim modu, sınırlar, düzgünlük kapıları, direnç oranı; adayı Rhino'da kurma (`HullOpt::Candidates`). Motorda `morph.frame`, `morph.evaluate`, `morph.export`.
- Mod çerçevesi ve düzgünlük planı her gövdenin kendi ölçülerinden türetilir.

## Eklenti 0.2 · motor 0.3.0 — 01.11.2026

- Sekmeli panel: Hull, Condition, Hydrostatics, Resistance; su hattı çizimi; tablo, grafik ve CSV.
- `hullopt serve`: eklentinin kullandığı JSON-RPC 2.0 servisi.

## Eklenti 0.1 · motor 0.3.0 — 01.11.2026

- Motor `hullopt` 0.3.0 bir yat tasarım projesindeki çalışmadan alındı; Windows kurulumu.
- Holtrop'ta giriş açısı her gövdede su hattından ölçülür. Motor varsayılanları genel; projeye özgü modüller motordan çıkarıldı.
- C# eklenti iskeleti: `HoEngineCheck` komutu ve HullOpt paneli.

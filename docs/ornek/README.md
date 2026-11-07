# Örnek gövde

[Kullanım kılavuzundaki](../HullOpt_Kullanim_Kilavuzu_v0.5.2.pdf) bütün ekran görüntüleri ve sayılar bu gövdeyle alındı. Aynı gövdeyi kurup kılavuzun adımlarını tekrarlayabilirsiniz.

| Dosya | İçinde |
|---|---|
| `parabolik_govde.json` | İki bikübik NURBS yüzey (iskele, sancak), metre; kontrol noktaları ve düğüm vektörleri |
| `parabolik_govde_kur.py` | Yüzeyleri Rhino'da kuran ve HullOpt tanımını yazan RhinoPython betiği |

Gövde, motorun testlerindeki parabolik gövdedir (`engine/tests/hydro_fixtures.py`, `sheer_hull_hgeo(40, 8, 4, sheer=0.25, spans=(16, 6))`):

x = L(u − ½), |y| = (B/2)·4u(1 − u)·v(2 − v), z = D·v·(1 + 0,25u²); L = 40 m, B = 8 m, D = 4 m.

Kıçı ve başı sivri, güvertesi açık bir gövdedir; gerçek bir tekne değil, yalnız aracın adımlarını göstermek için seçildi.

## Kurulum

1. Rhino 7'de boş bir model açın (*File → New*) ve `HoPanel` ile HullOpt panelini bir kez açın.
2. Komut satırına yazın:

   ```
   _-RunPythonScript "<proje klasörü>\docs\ornek\parabolik_govde_kur.py"
   ```

3. Betik iki yüzeyi `Hull` katmanına kurar ve şu ayarları yazar: deniz suyu 15 °C, su hattı z = 2,2 m, 10–18 kn (1 kn aralık), tasarım hızı 16 kn, Holtrop + Michell + ITTC-57, iki pervane (Z = 4, D = 1,4 m, şaft z = 0,8 m), iki makine × 600 kW. Modeli kaydetmez.
4. Modeli kaydedin (çalışma klasörü modelin adını alır), sonra sekmeleri sırayla çalıştırın.

Açık model boş değilse ya da kaydedilmemiş değişikliği varsa betik hiçbir şey yapmaz.

## Beklenen sonuçlar (Python)

- Hydrostatics, z = 2,2 m: ∇ = 199,302 m³, Δ = 204,463 t, LWL = 40 m, BWL = 6,143 m, Cb = 0,3687, Cp = 0,6602.
- Resistance, Holtrop R_T: 10 kn 13,69 kN … 18 kn 92,03 kN.
- Optimise, varsayılan ayarlar (tohum 170026, 600 değerlendirme): en iyi J = 0,98091 (−%1,91), 175 aday kapılardan geçer.

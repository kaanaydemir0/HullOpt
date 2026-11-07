# -*- coding: utf-8 -*-
"""HullOpt kılavuzunun örnek gövdesini Rhino 7'de kurar (IronPython 2.7, RhinoPython).

Kullanım: Rhino'da boş bir model açın (File > New), HullOpt panelini bir kez açın (HoPanel), sonra
    _-RunPythonScript "<depo>\\docs\\ornek\\parabolik_govde_kur.py"
Betik, yanındaki parabolik_govde.json dosyasındaki iki NURBS yüzeyini (iskele ve sancak) metre biriminde
"Hull" katmanına kurar, normalleri dışa çevirir ve HullOpt tanımını kılavuzdaki ayarlarla yazar:
gövde = bu iki yüzey, deniz suyu 15 °C, su hattı z = 2,2 m, 10–18 kn (1 kn aralık), tasarım hızı 16 kn,
Holtrop + Michell + ITTC-57, iki pervane (Z = 4, D = 1,4 m, şaft z = 0,8 m), iki makine × 600 kW.
Modeli kaydetmez. Boş olmayan ya da kaydedilmemiş değişikliği olan bir modelde hiçbir şey yapmaz.

Gövde, motorun testlerindeki parabolik gövdedir (engine/tests/hydro_fixtures.py, sheer_hull_hgeo(40, 8, 4,
sheer=0.25, spans=(16, 6))): x = L(u − 1/2), z = D·v·(1 + 0,25·u²), |y| = (B/2)·4u(1 − u)·v(2 − v);
L = 40 m, B = 8 m, D = 4 m. Kıç ve baş sivri, güverte açık.
"""
import clr
import json
import os
import System
clr.AddReference("System.Drawing")
import System.Drawing
import Rhino
from Rhino.Geometry import NurbsSurface, ControlPoint, Point3d, BoundingBox

NET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parabolik_govde.json")


def say(text):
    Rhino.RhinoApp.WriteLine(u"Örnek gövde: " + text)


def hullopt():
    asm = [a for a in System.AppDomain.CurrentDomain.GetAssemblies() if a.GetName().Name == "HullOpt"]
    if not asm:
        return None
    clr.AddReference(asm[0])
    import HullOpt
    return HullOpt


def surfaces(doc, layer):
    data = json.loads(System.IO.File.ReadAllText(NET))
    ids = []
    for f in data["faces"]:
        nu, nv = f["cv_count"]
        ou, ov = f["order"]
        srf = NurbsSurface.Create(3, False, ou, ov, nu, nv)
        for k, v in enumerate(f["knots_u"]):
            srf.KnotsU[k] = v
        for k, v in enumerate(f["knots_v"]):
            srf.KnotsV[k] = v
        for i in range(nu):
            for j in range(nv):
                x, y, z = f["cv"][i * nv + j]
                srf.Points.SetControlPoint(i, j, ControlPoint(x, y, z))
        if not srf.IsValid:
            say(u"%s geçersiz yüzey; durdu." % f["name"])
            return None
        brep = srf.ToBrep()
        normal = srf.NormalAt(srf.Domain(0).Mid, srf.Domain(1).Mid)
        if normal.Y * f["side"] < 0:
            brep.Flip()
        attr = Rhino.DocObjects.ObjectAttributes()
        attr.LayerIndex = layer
        attr.Name = f["name"]
        ids.append(doc.Objects.AddBrep(brep, attr))
    return ids


def main():
    if not os.path.exists(NET):
        say(u"%s bulunamadı." % NET)
        return
    doc = Rhino.RhinoDoc.ActiveDoc
    if doc is None or doc.Modified or doc.Objects.Count > 0:
        say(u"boş bir model açın (File > New) ve yeniden çalıştırın; açık model değiştirilmedi.")
        return
    doc.AdjustModelUnitSystem(Rhino.UnitSystem.Meters, False)
    doc.ModelAbsoluteTolerance = 0.001
    layer = doc.Layers.FindName("Hull")
    index = layer.Index if layer is not None else doc.Layers.Add("Hull", System.Drawing.Color.FromArgb(118, 140, 166))
    doc.Layers.SetCurrentLayerIndex(index, True)
    ids = surfaces(doc, index)
    if not ids or System.Guid.Empty in ids:
        say(u"yüzeyler eklenemedi.")
        return
    box = BoundingBox.Empty
    for g in ids:
        box.Union(doc.Objects.FindId(g).Geometry.GetBoundingBox(True))
    for view in doc.Views:
        view.ActiveViewport.ZoomBoundingBox(box)
    doc.Views.Redraw()
    H = hullopt()
    if H is None:
        say(u"iki yüzey kuruldu (Hull katmanı). HullOpt yüklü değil: HoPanel'i açıp Hull sekmesinden yüzeyleri seçin.")
        return
    d = H.Session.Definition(doc)
    d.HullObjects.Clear()
    for g in ids:
        d.HullObjects.Add(str(g))
    d.WaterPreset = "sea15"
    d.Rho = 1025.9
    d.Nu = 1.18831e-6
    d.FloatMode = "waterline"
    d.WaterlineZ = 2.2
    d.SpeedFrom = 10.0
    d.SpeedTo = 18.0
    d.SpeedStep = 1.0
    d.SpeedList = ""
    d.DesignSpeed = 16.0
    d.UseHoltrop = True
    d.UseMichell = True
    d.UseFriction = True
    d.PropulsionEnabled = True
    d.Propellers = 2
    d.Blades = 4
    d.PropellerDiameter = 1.4
    d.ShaftZ = 0.8
    d.Engines = 2
    d.EngineMcrKw = 600.0
    H.Session.Save(doc, d)
    say(u"iki yüzey kuruldu ve HullOpt tanımı yazıldı (su hattı z = 2,2 m, 10–18 kn). Modeli kaydedip "
        u"Hydrostatics sekmesinden başlayın.")


main()

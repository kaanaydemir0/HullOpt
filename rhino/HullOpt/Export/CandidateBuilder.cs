using System;
using System.Collections;
using System.Collections.Generic;
using Rhino;
using Rhino.Geometry;

namespace HullOpt
{
    /// <summary>
    /// Rebuilds a morphed hull in Rhino from the engine's control points. Each chosen hull object is
    /// copied; every face gets its morphed surface (same knots and weights) and keeps its own trims in
    /// surface parameters, which is exactly what the engine evaluated; the 3D edges are then rebuilt on
    /// the new surfaces. The model's own objects are never changed.
    /// </summary>
    public static class CandidateBuilder
    {
        public static List<Brep> Build(RhinoDoc doc, IList<string> hullIds, Dictionary<string, object> export,
                                       List<string> warnings)
        {
            var moved = new Dictionary<long, Dictionary<string, object>>();
            var faces = J.List(export, "faces");
            if (faces == null) throw new InvalidOperationException("The engine returned no faces.");
            foreach (var item in faces)
            {
                var face = item as Dictionary<string, object>;
                if (face == null) continue;
                moved[Key((int)J.Num(face, "object_index"), (int)J.Num(face, "face_index"))] = face;
            }
            var result = new List<Brep>();
            var objectIndex = 0;
            var tolerance = doc.ModelAbsoluteTolerance;
            foreach (var item in HgeoWriter.ExportedObjects(doc, hullIds, HgeoWriter.HullRole, warnings, ref objectIndex))
            {
                var brep = item.Brep.DuplicateBrep();
                var name = HgeoWriter.Name(item.Object);
                var complete = true;
                for (var i = 0; i < brep.Faces.Count; i++)
                {
                    var face = brep.Faces[i];
                    Dictionary<string, object> data;
                    var source = HgeoWriter.FaceNurbs(face);
                    var surface = source == null || !moved.TryGetValue(Key(item.Index, i), out data) ? null : Morphed(source, data);
                    if (surface == null)
                    {
                        warnings.Add("A face of '" + name + "' has no matching morphed control points; evaluate again, then build.");
                        complete = false;
                        break;
                    }
                    var index = brep.AddSurface(surface);
                    if (index < 0 || !face.ChangeSurface(index))
                    {
                        warnings.Add("A face of '" + name + "' could not take its morphed surface.");
                        complete = false;
                        break;
                    }
                }
                if (!complete) continue;
                for (var i = 0; i < brep.Faces.Count; i++) brep.Faces[i].RebuildEdges(tolerance, true, true);
                brep.Compact();
                if (!brep.IsValid) warnings.Add("The morphed copy of '" + name + "' is not a valid surface; check it with _Check.");
                result.Add(brep);
            }
            return result;
        }

        private static long Key(int objectIndex, int faceIndex)
        {
            return ((long)objectIndex << 32) | (uint)faceIndex;
        }

        /// <summary>A copy of the surface with the engine's homogeneous control points (model units).</summary>
        private static NurbsSurface Morphed(NurbsSurface source, Dictionary<string, object> data)
        {
            var counts = J.Nums(J.List(data, "cv_count"));
            var cv = J.List(data, "cv");
            int countU = source.Points.CountU, countV = source.Points.CountV;
            if (counts.Length != 2 || cv == null || (int)counts[0] != countU || (int)counts[1] != countV || cv.Count != countU * countV)
                return null;
            var surface = (NurbsSurface)source.Duplicate();
            for (var u = 0; u < countU; u++)
            {
                for (var v = 0; v < countV; v++)
                {
                    var p = J.Nums(cv[u * countV + v] as IList);
                    if (p.Length != 4 || !(p[3] > 0)) return null;
                    surface.Points.SetPoint(u, v, new Point3d(p[0] / p[3], p[1] / p[3], p[2] / p[3]), p[3]);
                }
            }
            return surface;
        }
    }
}

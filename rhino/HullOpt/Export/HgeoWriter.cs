using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace HullOpt
{
    /// <summary>What an export wrote and anything the user should know about it.</summary>
    public sealed class ExportResult
    {
        public string Path;
        public int Objects;
        public int Faces;
        public double MetersPerUnit;
        public string UnitSystem;
        public bool Rewritten;
        public readonly List<string> Roles = new List<string>();
        public readonly List<string> Warnings = new List<string>();
    }

    /// <summary>
    /// Writes Rhino surfaces and polysurfaces as neutral "hullgeo-1" JSON, the format the engine reads:
    /// homogeneous NURBS control points, OpenNURBS knot vectors and trim loops sampled in the surface's
    /// UV space. Each face carries its role (HULL or APPENDAGE) as its layer name.
    /// </summary>
    public static class HgeoWriter
    {
        public const string HullRole = "HULL";
        public const string AppendageRole = "APPENDAGE";

        public static ExportResult Write(RhinoDoc doc, IList<string> hullIds, IList<string> appendageIds, string path)
        {
            var result = new ExportResult
            {
                Path = path,
                UnitSystem = doc.ModelUnitSystem.ToString(),
                MetersPerUnit = RhinoMath.UnitScale(doc.ModelUnitSystem, Rhino.UnitSystem.Meters),
            };
            if (!(result.MetersPerUnit > 0))
                throw new InvalidOperationException("The model has no length unit; set the model units first.");
            var faces = new StringBuilder(1 << 20);
            var objectIndex = 0;
            AppendRole(doc, hullIds, HullRole, faces, result, ref objectIndex);
            if (result.Faces == 0)
                throw new InvalidOperationException("No hull faces to export. Select the hull surfaces first (HoHull).");
            if (appendageIds != null && appendageIds.Count > 0)
            {
                var before = result.Faces;
                AppendRole(doc, appendageIds, AppendageRole, faces, result, ref objectIndex);
                if (result.Faces == before) result.Warnings.Add("No appendage faces were found; appendages are left out.");
            }

            var layers = new StringBuilder();
            for (var i = 0; i < result.Roles.Count; i++)
            {
                if (i > 0) layers.Append(',');
                layers.Append(Json.Quote(i.ToString(System.Globalization.CultureInfo.InvariantCulture))).Append(':')
                      .Append(Json.Quote(result.Roles[i]));
            }
            var json = new StringBuilder(faces.Length + 1024);
            json.Append("{\"format\":\"hullgeo-1\"")
                .Append(",\"writer\":").Append(Json.Quote("HullOpt Rhino plug-in " + typeof(HgeoWriter).Assembly.GetName().Version))
                .Append(",\"source\":").Append(Json.Quote(string.IsNullOrEmpty(doc.Path) ? (doc.Name ?? "Untitled") : doc.Path))
                .Append(",\"unit_system\":").Append(Json.Quote(result.UnitSystem))
                .Append(",\"meters_per_unit\":").Append(Json.Number(result.MetersPerUnit))
                .Append(",\"abs_tol\":").Append(Json.Number(doc.ModelAbsoluteTolerance))
                .Append(",\"warnings\":").Append(result.Warnings.Count)
                .Append(",\"layers\":{").Append(layers).Append('}')
                .Append(",\"document_strings\":{}")
                .Append(",\"face_count\":").Append(result.Faces)
                .Append(",\"faces\":[").Append(faces).Append("]}");
            result.Rewritten = WriteIfChanged(path, Encoding.ASCII.GetBytes(json.ToString()));
            return result;
        }

        /// <summary>One chosen object as the engine sees it: its Brep in NURBS form and its export number.</summary>
        internal sealed class ExportedObject
        {
            public RhinoObject Object;
            public Brep Brep;
            public int Index;
            public bool[] Exact;
        }

        /// <summary>
        /// The chosen objects in export order, each as a Brep made NURBS where possible. The engine numbers
        /// objects in this order (object_index) and faces by Brep face index, so the candidate builder
        /// walks the same list to put morphed control points back on the right faces.
        /// </summary>
        internal static List<ExportedObject> ExportedObjects(RhinoDoc doc, IList<string> ids, string role,
                                                            List<string> warnings, ref int objectIndex)
        {
            var list = new List<ExportedObject>();
            if (ids == null) return list;
            foreach (var text in ids)
            {
                Guid id;
                RhinoObject obj = Guid.TryParse(text, out id) ? doc.Objects.FindId(id) : null;
                if (obj == null || obj.IsDeleted)
                {
                    warnings.Add(role + ": an object chosen earlier no longer exists (" + text + ").");
                    continue;
                }
                var brep = ToBrep(obj.Geometry);
                if (brep == null)
                {
                    warnings.Add(role + ": object '" + Name(obj) + "' is not a surface or polysurface; skipped.");
                    continue;
                }
                var exact = new bool[brep.Faces.Count];
                var allNurbs = true;
                for (var i = 0; i < brep.Faces.Count; i++)
                {
                    var surface = brep.Faces[i].UnderlyingSurface();
                    exact[i] = surface is NurbsSurface || surface is PlaneSurface;
                    allNurbs &= surface is NurbsSurface;
                }
                if (!allNurbs && !brep.MakeValidForV2())
                    warnings.Add(role + ": object '" + Name(obj) + "' could not be converted to NURBS form.");
                list.Add(new ExportedObject { Object = obj, Brep = brep, Index = objectIndex, Exact = exact });
                objectIndex++;
            }
            return list;
        }

        /// <summary>The NURBS form of a face as exported (null when Rhino cannot make one).</summary>
        internal static NurbsSurface FaceNurbs(BrepFace face)
        {
            return face.UnderlyingSurface() as NurbsSurface ?? face.UnderlyingSurface().ToNurbsSurface();
        }

        private static void AppendRole(RhinoDoc doc, IList<string> ids, string role, StringBuilder faces,
                                       ExportResult result, ref int objectIndex)
        {
            var any = false;
            foreach (var item in ExportedObjects(doc, ids, role, result.Warnings, ref objectIndex))
            {
                var obj = item.Object;
                var brep = item.Brep;
                var layer = doc.Layers.FindIndex(obj.Attributes.LayerIndex);
                var layerPath = layer != null ? layer.FullPath : "";
                for (var i = 0; i < brep.Faces.Count; i++)
                {
                    var face = brep.Faces[i];
                    var faceExact = i < item.Exact.Length && item.Exact[i] && face.UnderlyingSurface() is NurbsSurface;
                    if (!(face.UnderlyingSurface() is NurbsSurface))
                        result.Warnings.Add(role + ": a face of '" + Name(obj) + "' has no exact NURBS form; run _ToNURBS on it.");
                    var nurbs = FaceNurbs(face);
                    if (nurbs == null) continue;
                    AppendFace(faces, face, nurbs, faceExact, role, item.Index, i, Name(obj), layerPath);
                    result.Faces++;
                    any = true;
                }
                result.Objects++;
            }
            if (any && !result.Roles.Contains(role)) result.Roles.Add(role);
        }

        internal static string Name(RhinoObject obj)
        {
            return string.IsNullOrEmpty(obj.Attributes.Name) ? obj.Id.ToString() : obj.Attributes.Name;
        }

        private static Brep ToBrep(GeometryBase geometry)
        {
            var brep = geometry as Brep;
            if (brep != null) return brep.DuplicateBrep();
            var extrusion = geometry as Extrusion;
            if (extrusion != null) return extrusion.ToBrep(true);
            var surface = geometry as Surface;
            if (surface != null) return surface.ToBrep();
            return null;
        }

        private static void AppendFace(StringBuilder sb, BrepFace face, NurbsSurface surface, bool exact, string role,
                                       int objectIndex, int faceIndex, string objectName, string layerPath)
        {
            if (sb.Length > 0) sb.Append(',');
            int countU = surface.Points.CountU, countV = surface.Points.CountV;
            sb.Append("{\"layer\":").Append(Json.Quote(role))
              .Append(",\"source_layer\":").Append(Json.Quote(layerPath))
              .Append(",\"object_name\":").Append(Json.Quote(objectName))
              .Append(",\"object_index\":").Append(objectIndex)
              .Append(",\"face_index\":").Append(faceIndex)
              .Append(",\"rev\":").Append(face.OrientationIsReversed ? 1 : 0)
              .Append(",\"nurbs_form_exact\":").Append(exact ? 1 : 0)
              .Append(",\"nurbs\":{\"rational\":").Append(surface.IsRational ? 1 : 0)
              .Append(",\"order\":[").Append(surface.OrderU).Append(',').Append(surface.OrderV).Append(']')
              .Append(",\"cv_count\":[").Append(countU).Append(',').Append(countV).Append(']')
              .Append(",\"knots_u\":[");
            for (var i = 0; i < surface.KnotsU.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append(Json.Number(surface.KnotsU[i]));
            }
            sb.Append("],\"knots_v\":[");
            for (var i = 0; i < surface.KnotsV.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append(Json.Number(surface.KnotsV[i]));
            }
            sb.Append("],\"cv\":[");
            var rational = surface.IsRational;
            for (var u = 0; u < countU; u++)
            {
                for (var v = 0; v < countV; v++)
                {
                    var point = surface.Points.GetControlPoint(u, v);
                    var w = rational ? point.Weight : 1.0;
                    var p = point.Location;
                    if (u > 0 || v > 0) sb.Append(',');
                    sb.Append('[').Append(Json.Number(p.X * w)).Append(',').Append(Json.Number(p.Y * w)).Append(',')
                      .Append(Json.Number(p.Z * w)).Append(',').Append(Json.Number(w)).Append(']');
                }
            }
            sb.Append("]},\"loops\":[");
            var firstLoop = true;
            foreach (var loop in face.Loops)
            {
                var uv = new List<Point3d>();
                foreach (var trim in loop.Trims) SampleTrim(trim, uv);
                if (uv.Count < 3) continue;
                if (!firstLoop) sb.Append(',');
                firstLoop = false;
                var type = loop.LoopType == BrepLoopType.Outer ? "outer" : loop.LoopType == BrepLoopType.Inner ? "inner" : "other";
                sb.Append("{\"type\":\"").Append(type).Append("\",\"uv\":[");
                for (var i = 0; i < uv.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append('[').Append(Json.Number(uv[i].X)).Append(',').Append(Json.Number(uv[i].Y)).Append(']');
                }
                sb.Append("]}");
            }
            sb.Append("]}");
        }

        /// <summary>
        /// Points along a trim in UV, from its start up to (not including) its end, which is where
        /// the next trim of the loop starts. Polyline trims keep their vertices; curved trims get
        /// eight points per span and degree, at least 16 and at most 512.
        /// </summary>
        private static void SampleTrim(BrepTrim trim, List<Point3d> uv)
        {
            var domain = trim.Domain;
            if (trim.Degree <= 1)
            {
                for (var i = 0; i < trim.SpanCount; i++) uv.Add(trim.PointAt(trim.SpanDomain(i).T0));
                return;
            }
            var count = Math.Max(16, Math.Min(512, 8 * trim.SpanCount * trim.Degree));
            for (var i = 0; i < count; i++) uv.Add(trim.PointAt(domain.ParameterAt((double)i / count)));
        }

        /// <summary>Keeps an identical file untouched, so the engine's cache of the tessellated hull stays valid.</summary>
        private static bool WriteIfChanged(string path, byte[] bytes)
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path));
            if (File.Exists(path) && new FileInfo(path).Length == bytes.Length)
            {
                using (var sha = SHA256.Create())
                {
                    var old = sha.ComputeHash(File.ReadAllBytes(path));
                    var current = sha.ComputeHash(bytes);
                    if (old.SequenceEqual(current)) return false;
                }
            }
            var temporary = path + ".tmp";
            File.WriteAllBytes(temporary, bytes);
            if (File.Exists(path)) File.Delete(path);
            File.Move(temporary, path);
            return true;
        }
    }
}

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace HullOpt
{
    /// <summary>A finished hydrostatics or resistance request with what is needed to show it.</summary>
    public sealed class Outcome
    {
        public string Kind;
        public Dictionary<string, object> Result;
        public double MetersPerUnit;
        public string UnitName;
        public uint DocSerial;
        public DateTime Time = DateTime.Now;
        public readonly List<string> Warnings = new List<string>();

        public Dictionary<string, object> Hydrostatics
        {
            get { return J.Obj(Result, "hydrostatics"); }
        }
    }

    /// <summary>
    /// Ties the document, the engine and the panel together. Methods are called on Rhino's UI thread;
    /// engine replies come back on background threads and are handed to the UI thread before any
    /// document or panel change. Events are raised on the UI thread.
    /// </summary>
    public static class Session
    {
        private static readonly Dictionary<uint, HullDefinition> Definitions = new Dictionary<uint, HullDefinition>();
        private static CancellationTokenSource _cancel;
        private static bool _initialized;
        private static long _optimiseRequest;
        private static bool _stopSent;
        private static string _stopFile;

        /// <summary>The hull definition of a document changed (input fields need refreshing).</summary>
        public static event Action DefinitionChanged;
        /// <summary>A document was opened or created (inputs and results need refreshing).</summary>
        public static event Action DocumentChanged;
        public static event Action<Outcome> HydrostaticsReady;
        public static event Action<Outcome> ResistanceReady;
        public static event Action<Outcome> MorphReady;
        /// <summary>An optimisation started (the Optimise tab clears its live chart).</summary>
        public static event Action OptimiseStarted;
        /// <summary>Live state of the running search: evaluations, generation, feasible count, best J.</summary>
        public static event Action<Dictionary<string, object>> OptimiseProgress;
        public static event Action<Outcome> OptimiseReady;
        public static event Action<string, bool> Message;
        public static event Action<bool> BusyChanged;
        public static event Action<string, double> ProgressChanged;
        public static event Action<string> EngineStatusChanged;
        public static event Action<string> TabRequested;

        public static bool Busy { get; private set; }
        public static Outcome LastHydrostatics { get; private set; }
        public static Outcome LastResistance { get; private set; }
        public static Outcome LastMorph { get; private set; }
        public static Outcome LastOptimise { get; private set; }
        /// <summary>An optimisation is running (Cancel first asks it to stop and keep what it found).</summary>
        public static bool Optimising { get; private set; }

        /// <summary>The engine's shape modes, in its order.</summary>
        public static readonly string[] ModeNames =
            { "lack_aft", "lack_fwd", "bow_fine", "bow_tip", "stern_narrow", "stern_lift", "bilge_full", "deadrise" };
        public static readonly List<string> EngineLog = new List<string>();

        public static void Initialize()
        {
            if (_initialized) return;
            _initialized = true;
            var engine = EngineClient.Shared;
            engine.Progress += (id, stage, fraction) => Ui(() =>
            {
                var handler = ProgressChanged;
                if (handler != null) handler(stage, fraction);
            });
            engine.ProgressData += (id, data) =>
            {
                if (id != Interlocked.Read(ref _optimiseRequest)) return;
                Ui(() =>
                {
                    var handler = OptimiseProgress;
                    if (handler != null) handler(data);
                });
            };
            engine.StatusChanged += status => Ui(() =>
            {
                var handler = EngineStatusChanged;
                if (handler != null) handler(status);
            });
            engine.Log += line =>
            {
                lock (EngineLog)
                {
                    EngineLog.Add(line);
                    if (EngineLog.Count > 400) EngineLog.RemoveRange(0, 100);
                }
            };
            RhinoDoc.EndOpenDocument += (sender, e) => Ui(DocumentSwitched);
            // An opened model's name is set only after EndOpenDocument, so the panel refreshes once more
            // when Rhino is idle; otherwise it would show the work folder of "Untitled".
            RhinoDoc.EndOpenDocument += (sender, e) => Ui(RefreshWhenIdle);
            RhinoDoc.NewDocument += (sender, e) => Ui(DocumentSwitched);
        }

        private static void RefreshWhenIdle()
        {
            EventHandler idle = null;
            idle = (sender, e) =>
            {
                RhinoApp.Idle -= idle;
                Raise(DocumentChanged);
            };
            RhinoApp.Idle += idle;
        }

        private static void DocumentSwitched()
        {
            LastHydrostatics = null;
            LastResistance = null;
            LastMorph = null;
            LastOptimise = null;
            Raise(DocumentChanged);
        }

        public static void Ui(Action action)
        {
            RhinoApp.InvokeOnUiThread(action);
        }

        private static void Raise(Action handler)
        {
            if (handler != null) handler();
        }

        public static void Say(string text, bool isError)
        {
            RhinoApp.WriteLine("HullOpt: " + text);
            var handler = Message;
            if (handler != null) handler(text, isError);
        }

        public static void RequestTab(string name)
        {
            Rhino.UI.Panels.OpenPanel(HullOptPanel.PanelId);
            var handler = TabRequested;
            if (handler != null) handler(name);
        }

        public static HullDefinition Definition(RhinoDoc doc)
        {
            if (doc == null) return new HullDefinition();
            HullDefinition definition;
            if (!Definitions.TryGetValue(doc.RuntimeSerialNumber, out definition))
            {
                definition = HullDefinition.Load(doc);
                Definitions[doc.RuntimeSerialNumber] = definition;
            }
            return definition;
        }

        public static void Save(RhinoDoc doc, HullDefinition definition)
        {
            if (doc == null) return;
            Definitions[doc.RuntimeSerialNumber] = definition;
            definition.Save(doc);
            Raise(DefinitionChanged);
        }

        public static string UnitName(RhinoDoc doc)
        {
            switch (doc.ModelUnitSystem)
            {
                case UnitSystem.Millimeters: return "mm";
                case UnitSystem.Centimeters: return "cm";
                case UnitSystem.Meters: return "m";
                case UnitSystem.Inches: return "in";
                case UnitSystem.Feet: return "ft";
                default: return doc.ModelUnitSystem.ToString().ToLowerInvariant();
            }
        }

        public static double MetersPerUnit(RhinoDoc doc)
        {
            return RhinoMath.UnitScale(doc.ModelUnitSystem, UnitSystem.Meters);
        }

        /// <summary>
        /// Folder for exported geometry and results: calisma\rhino\&lt;model name&gt; in the engine project,
        /// or %LOCALAPPDATA%\HullOpt\work\&lt;model name&gt; when no project folder is found.
        /// </summary>
        public static string WorkFolder(RhinoDoc doc)
        {
            var folder = WorkFolderPath(doc);
            Directory.CreateDirectory(folder);
            return folder;
        }

        /// <summary>Where WorkFolder keeps the model's files, without creating the folder (for display).</summary>
        public static string WorkFolderPath(RhinoDoc doc)
        {
            var project = EngineClient.FindProjectFolder();
            var root = project != null
                ? Path.Combine(project, "calisma", "rhino")
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "HullOpt", "work");
            var name = string.IsNullOrEmpty(doc.Name) ? "Untitled" : Path.GetFileNameWithoutExtension(doc.Name);
            foreach (var c in Path.GetInvalidFileNameChars()) name = name.Replace(c, '_');
            return Path.Combine(root, name);
        }

        /// <summary>Counts objects and faces still present in the document.</summary>
        public static string Describe(RhinoDoc doc, IList<string> ids)
        {
            if (ids == null || ids.Count == 0) return "none selected";
            int objects = 0, faces = 0, missing = 0;
            foreach (var text in ids)
            {
                Guid id;
                var obj = Guid.TryParse(text, out id) ? doc.Objects.FindId(id) : null;
                if (obj == null || obj.IsDeleted)
                {
                    missing++;
                    continue;
                }
                objects++;
                var brep = obj.Geometry as Brep;
                if (brep != null) faces += brep.Faces.Count;
                else if (obj.Geometry is Extrusion || obj.Geometry is Surface) faces += 1;
            }
            var text1 = objects + (objects == 1 ? " object, " : " objects, ") + faces + (faces == 1 ? " face" : " faces");
            if (missing > 0) text1 += "; " + missing + " no longer in the model";
            return text1;
        }

        private static string HullProblem(RhinoDoc doc, HullDefinition d)
        {
            if (doc == null) return "No active model.";
            if (d.HullObjects.Count == 0) return "Hull: select the hull surfaces first (Hull tab or HoHull).";
            return null;
        }

        private static List<object> Layers(HullDefinition d, ExportResult export)
        {
            return export.Roles.Where(role => role == HgeoWriter.HullRole || d.IncludeAppendages).Cast<object>().ToList();
        }

        private static bool Begin(string stage)
        {
            if (Busy)
            {
                Say("HullOpt is busy; wait for the running calculation or cancel it.", true);
                return false;
            }
            Busy = true;
            _cancel = new CancellationTokenSource();
            var busy = BusyChanged;
            if (busy != null) busy(true);
            var progress = ProgressChanged;
            if (progress != null) progress(stage, 0.0);
            return true;
        }

        private static void End()
        {
            Busy = false;
            var busy = BusyChanged;
            if (busy != null) busy(false);
        }

        /// <summary>
        /// Cancels the running calculation. A running search is first asked to stop after the current
        /// candidate and return what it found (it watches for its stop file); a second Cancel stops the
        /// engine at once.
        /// </summary>
        public static void Cancel()
        {
            var cancel = _cancel;
            if (!Busy || cancel == null) return;
            var request = Interlocked.Read(ref _optimiseRequest);
            if (Optimising && request > 0 && !_stopSent && _stopFile != null)
            {
                _stopSent = true;
                try
                {
                    File.WriteAllText(_stopFile, "stop");
                    Say("stopping the search after the current candidate; the best candidates so far will be shown. " +
                        "Cancel again to stop the engine at once.", false);
                    return;
                }
                catch (Exception ex)
                {
                    Say("Could not ask the search to stop (" + ex.Message + "); stopping the engine.", true);
                }
            }
            cancel.Cancel();
        }

        private static void RemoveStopFile(string path)
        {
            try
            {
                if (path != null && File.Exists(path)) File.Delete(path);
            }
            catch (Exception)
            {
                // A leftover stop file only names a finished search; the next search uses a new name.
            }
        }

        private static string ErrorText(Exception exception)
        {
            var aggregate = exception as AggregateException;
            if (aggregate != null) exception = aggregate.Flatten().InnerExceptions.FirstOrDefault() ?? exception;
            return exception.Message;
        }

        private static Dictionary<string, object> BaseParameters(HullDefinition d, ExportResult export)
        {
            return new Dictionary<string, object>
            {
                { "hgeo", export.Path },
                { "layers", Layers(d, export) },
                { "rho_kg_m3", d.Rho },
                { "tessellation", new Dictionary<string, object> { { "max_grid", d.MaxGrid } } },
            };
        }

        private static ExportResult Export(RhinoDoc doc, HullDefinition d)
        {
            var path = Path.Combine(WorkFolder(doc), "hull.hgeo.json");
            return HgeoWriter.Write(doc, d.HullObjects, d.IncludeAppendages ? d.AppendageObjects : null, path);
        }

        /// <summary>Hydrostatics at the condition's waterline, or at the waterline that floats the displacement.</summary>
        public static void RunHydrostatics(RhinoDoc doc)
        {
            var d = Definition(doc);
            var problem = HullProblem(doc, d) ?? d.FloatingProblem();
            if (problem != null)
            {
                Say(problem, true);
                return;
            }
            if (!Begin("Exporting the hull")) return;
            ExportResult export;
            try
            {
                export = Export(doc, d);
            }
            catch (Exception ex)
            {
                End();
                Say("Hull export failed: " + ex.Message, true);
                return;
            }
            var parameters = BaseParameters(d, export);
            string method;
            if (d.FloatMode == HullDefinition.FloatByDisplacement)
            {
                method = "hydro.equilibrium";
                parameters["displacement_t"] = d.DisplacementTonnes.Value;
            }
            else
            {
                method = "hydro.measure";
                parameters["z_wl_m"] = d.WaterlineZ.Value * export.MetersPerUnit;
            }
            var serial = doc.RuntimeSerialNumber;
            var unit = UnitName(doc);
            var drawWaterline = d.DrawWaterline;
            EngineClient.Shared.CallAsync(method, parameters, _cancel.Token).ContinueWith(task => Ui(() =>
            {
                End();
                if (task.IsCanceled)
                {
                    Say("Hydrostatics cancelled.", false);
                    return;
                }
                if (task.IsFaulted)
                {
                    Say("Hydrostatics failed: " + ErrorText(task.Exception), true);
                    return;
                }
                var outcome = new Outcome { Kind = method, Result = task.Result, MetersPerUnit = export.MetersPerUnit, UnitName = unit, DocSerial = serial };
                outcome.Warnings.AddRange(export.Warnings);
                LastHydrostatics = outcome;
                var current = RhinoDoc.FromRuntimeSerialNumber(serial);
                if (current != null && drawWaterline) DrawWaterline(current, task.Result, export.MetersPerUnit);
                var handler = HydrostaticsReady;
                if (handler != null) handler(outcome);
                var hydro = outcome.Hydrostatics;
                Say(string.Format(System.Globalization.CultureInfo.InvariantCulture,
                    "hydrostatics done: z = {0:0.####} m, volume {1:0.###} m³, displacement {2:0.###} t.",
                    J.Num(hydro, "z_wl_m"), J.Num(hydro, "volume_m3"), J.Num(hydro, "displacement_kg") / 1000.0), false);
            }));
        }

        /// <summary>
        /// Resistance at the condition's waterline (solved first from the displacement when given); with the
        /// Propulsion tab's brake power when it is turned on or asked for.
        /// </summary>
        public static void RunResistance(RhinoDoc doc, bool withPropulsion = false)
        {
            var d = Definition(doc);
            string speedProblem;
            var speeds = d.Speeds(out speedProblem);
            var propulsion = withPropulsion || d.PropulsionEnabled;
            var problem = HullProblem(doc, d) ?? d.FloatingProblem() ?? speedProblem ?? d.ResistanceProblem() ??
                          (propulsion ? d.PropulsionProblem() : null);
            if (problem != null)
            {
                Say(problem, true);
                return;
            }
            if (!Begin("Exporting the hull")) return;
            ExportResult export;
            try
            {
                export = Export(doc, d);
            }
            catch (Exception ex)
            {
                End();
                Say("Hull export failed: " + ex.Message, true);
                return;
            }
            var baseParameters = BaseParameters(d, export);
            var mpu = export.MetersPerUnit;
            var resistance = new Dictionary<string, object>(baseParameters);
            AddResistanceParameters(resistance, d, mpu, speeds);
            if (propulsion) AddPropulsionParameters(resistance, d, mpu);
            var byDisplacement = d.FloatMode == HullDefinition.FloatByDisplacement;
            var displacement = d.DisplacementTonnes;
            var waterline = d.WaterlineZ;
            var serial = doc.RuntimeSerialNumber;
            var unit = UnitName(doc);
            var drawWaterline = d.DrawWaterline;
            ResistanceAsync(baseParameters, resistance, byDisplacement, displacement, waterline, mpu, _cancel.Token)
                .ContinueWith(task => Ui(() =>
                {
                    End();
                    if (task.IsCanceled)
                    {
                        Say("Resistance cancelled.", false);
                        return;
                    }
                    if (task.IsFaulted)
                    {
                        Say("Resistance failed: " + ErrorText(task.Exception), true);
                        return;
                    }
                    var outcome = new Outcome { Kind = "resistance.evaluate", Result = task.Result, MetersPerUnit = mpu, UnitName = unit, DocSerial = serial };
                    outcome.Warnings.AddRange(export.Warnings);
                    LastResistance = outcome;
                    var current = RhinoDoc.FromRuntimeSerialNumber(serial);
                    if (current != null && drawWaterline && task.Result.ContainsKey("waterline_m"))
                        DrawWaterline(current, task.Result, mpu);
                    var handler = ResistanceReady;
                    if (handler != null) handler(outcome);
                    var count = J.Nums(J.Obj(task.Result, "conditions"), "speeds_kn").Length;
                    Say("resistance " + (propulsion ? "and brake power " : "") + "done for " + count + " speeds.", false);
                }));
        }

        /// <summary>The Propulsion tab's propeller, factors, margins and machinery for resistance.evaluate.</summary>
        private static void AddPropulsionParameters(Dictionary<string, object> parameters, HullDefinition d, double mpu)
        {
            var p = new Dictionary<string, object>
            {
                { "diameter_m", d.PropellerDiameter.Value },
                { "shaft_z_m", d.ShaftZ.Value * mpu },
                { "design_speed_kn", d.DesignSpeed.Value },
                { "propellers", d.Propellers },
                { "blades", d.Blades },
                { "wake", d.WakeMethod },
                { "thrust_deduction", d.ThrustDeductionMethod },
                { "relative_rotative", d.RelativeRotativeMethod },
                { "design_thrust", d.DesignThrust },
                { "appendage_margin", d.AppendageMarginPercent / 100.0 },
                { "air_margin", d.AirMarginPercent / 100.0 },
                { "sea_margin", d.SeaMarginPercent / 100.0 },
                { "shaft_efficiency", d.ShaftEfficiency },
                { "engines", d.Engines },
                { "mcr_fraction", d.McrPercent / 100.0 },
                { "pto_kw", d.PtoKw },
            };
            if (d.PitchRatio.HasValue) p["pitch_ratio"] = d.PitchRatio.Value;
            if (d.AreaRatio.HasValue) p["area_ratio"] = d.AreaRatio.Value;
            if (d.KellerConstant.HasValue) p["keller_constant"] = d.KellerConstant.Value;
            if (d.WakeMethod == "given") p["wake_fraction"] = d.WakeFraction.Value;
            if (d.ThrustDeductionMethod == "given") p["thrust_deduction_fraction"] = d.ThrustDeduction.Value;
            if (d.RelativeRotativeMethod == "given") p["relative_rotative_efficiency"] = d.RelativeRotative.Value;
            if (d.EngineMcrKw.HasValue) p["engine_mcr_kw"] = d.EngineMcrKw.Value;
            if (d.EngineRpm.HasValue) p["engine_rpm"] = d.EngineRpm.Value;
            parameters["propulsion"] = p;
        }

        /// <summary>Methods and options of the Resistance tab, for resistance.evaluate and morph.evaluate.</summary>
        private static void AddResistanceParameters(Dictionary<string, object> parameters, HullDefinition d, double mpu,
                                                    List<double> speeds)
        {
            var methods = new List<object>();
            if (d.UseHoltrop) methods.Add("holtrop");
            if (d.UseMichell) methods.Add("michell");
            if (d.UseFriction) methods.Add("friction");
            var holtrop = new Dictionary<string, object>
            {
                { "entrance_angle_policy", d.EntrancePolicy },
                { "lcb_reference", d.LcbReference },
                { "stern_parameter", d.SternParameter },
                { "roughness_m", d.RoughnessMicrons * 1e-6 },
            };
            if (d.EntrancePolicy == "given")
            {
                holtrop["entrance_half_angle_deg"] = d.EntranceAngle.Value;
                holtrop["entrance_angle_source"] = "user input";
            }
            if (d.LcbReference == "amidships")
            {
                holtrop["x_ap_m"] = d.ApX.Value * mpu;
                holtrop["x_fp_m"] = d.FpX.Value * mpu;
            }
            parameters["speeds_kn"] = speeds.Cast<object>().ToList();
            parameters["nu_m2_s"] = d.Nu;
            parameters["methods"] = methods;
            parameters["holtrop"] = holtrop;
            parameters["michell"] = new Dictionary<string, object>
            {
                { "multi_interval_policy", d.MichellMultiInterval },
                { "bow_padding_fraction_lwl", d.MichellBowPaddingPercent / 100.0 },
            };
            parameters["friction_include_transom"] = d.FrictionIncludesTransom;
        }

        private static Dictionary<string, object> MorphParameters(HullDefinition d, ExportResult export)
        {
            var parameters = BaseParameters(d, export);
            if (d.FloatMode == HullDefinition.FloatByDisplacement) parameters["displacement_t"] = d.DisplacementTonnes.Value;
            else parameters["z_wl_m"] = d.WaterlineZ.Value * export.MetersPerUnit;
            var amplitudes = new Dictionary<string, object>();
            foreach (var name in ModeNames)
            {
                double value;
                if (d.MorphAmplitudes != null && d.MorphAmplitudes.TryGetValue(name, out value) && value != 0) amplitudes[name] = value;
            }
            parameters["amplitudes"] = amplitudes;
            return parameters;
        }

        /// <summary>
        /// The engine bends and floats the hull faces only. With appendages included and a displacement
        /// to float, this is the hydro.equilibrium request that floats the hull with its appendages, as the
        /// Hydrostatics and Resistance tabs do, so the morph starts from the same waterline; otherwise null.
        /// </summary>
        private static Dictionary<string, object> AppendageFloating(HullDefinition d, ExportResult export)
        {
            if (d.FloatMode != HullDefinition.FloatByDisplacement || !d.IncludeAppendages ||
                !export.Roles.Contains(HgeoWriter.AppendageRole)) return null;
            var parameters = BaseParameters(d, export);
            parameters["displacement_t"] = d.DisplacementTonnes.Value;
            return parameters;
        }

        /// <summary>Solves an AppendageFloating request and gives its waterline to the morph requests.</summary>
        private static async Task UseWaterline(Dictionary<string, object> floating, CancellationToken cancel,
                                               params Dictionary<string, object>[] requests)
        {
            if (floating == null) return;
            var result = await EngineClient.Shared.CallAsync("hydro.equilibrium", floating, cancel).ConfigureAwait(false);
            var z = J.Num(result, "z_wl_m");
            if (double.IsNaN(z)) throw new InvalidOperationException("the engine returned no waterline for the displacement.");
            foreach (var request in requests)
            {
                request.Remove("displacement_t");
                request["z_wl_m"] = z;
            }
        }

        /// <summary>
        /// Evaluates the Morph tab's candidate: the engine bends the hull faces with the amplitudes, floats
        /// the result at the baseline displacement and checks it; with speeds it also compares resistance.
        /// </summary>
        public static void RunMorph(RhinoDoc doc)
        {
            var d = Definition(doc);
            string speedProblem = null;
            var speeds = d.MorphResistance ? d.Speeds(out speedProblem) : null;
            var problem = HullProblem(doc, d) ?? d.FloatingProblem() ?? d.MorphProblem() ??
                          (d.MorphResistance ? speedProblem ?? d.ResistanceProblem() : null);
            if (problem != null)
            {
                Say(problem, true);
                return;
            }
            if (!Begin("Exporting the hull")) return;
            ExportResult export;
            try
            {
                export = Export(doc, d);
            }
            catch (Exception ex)
            {
                End();
                Say("Hull export failed: " + ex.Message, true);
                return;
            }
            var mpu = export.MetersPerUnit;
            var frameParameters = MorphParameters(d, export);
            frameParameters.Remove("amplitudes");
            var evaluate = MorphParameters(d, export);
            if (d.MorphResistance) AddResistanceParameters(evaluate, d, mpu, speeds);
            var limits = d.Copy();
            var serial = doc.RuntimeSerialNumber;
            var unit = UnitName(doc);
            MorphAsync(AppendageFloating(d, export), frameParameters, evaluate, limits, _cancel.Token).ContinueWith(task => Ui(() =>
            {
                End();
                if (task.IsCanceled)
                {
                    Say("Morph evaluation cancelled.", false);
                    return;
                }
                if (task.IsFaulted)
                {
                    Say("Morph evaluation failed: " + ErrorText(task.Exception), true);
                    return;
                }
                var outcome = new Outcome { Kind = "morph.evaluate", Result = task.Result, MetersPerUnit = mpu, UnitName = unit, DocSerial = serial };
                outcome.Warnings.AddRange(export.Warnings);
                LastMorph = outcome;
                var handler = MorphReady;
                if (handler != null) handler(outcome);
                var changes = J.Obj(task.Result, "changes");
                var text = string.Format(System.Globalization.CultureInfo.InvariantCulture,
                    "candidate evaluated: waterline {0:+0.0;-0.0;0.0} mm, LCB {1:+0.000;-0.000;0.000}% LWL, {2}.",
                    1000.0 * J.Num(changes, "z_wl_m"), J.Num(changes, "lcb_percent_lwl"),
                    true.Equals(task.Result.ContainsKey("feasible") ? task.Result["feasible"] : null) ? "all gates pass" : "some gates fail");
                Say(text, false);
            }));
        }

        private static async Task<Dictionary<string, object>> MorphAsync(Dictionary<string, object> floating,
            Dictionary<string, object> frameParameters, Dictionary<string, object> evaluate, HullDefinition d,
            CancellationToken cancel)
        {
            await UseWaterline(floating, cancel, frameParameters, evaluate).ConfigureAwait(false);
            var frame = await EngineClient.Shared.CallAsync("morph.frame", frameParameters, cancel).ConfigureAwait(false);
            evaluate["limits"] = GateLimits(d, frame);
            var result = await EngineClient.Shared.CallAsync("morph.evaluate", evaluate, cancel).ConfigureAwait(false);
            result["modes"] = frame.ContainsKey("modes") ? frame["modes"] : null;
            return result;
        }

        /// <summary>The Morph tab's limits in engine units, scaled with the baseline LWL and draft of a frame.</summary>
        private static Dictionary<string, object> GateLimits(HullDefinition d, Dictionary<string, object> frame)
        {
            var hydro = J.Obj(frame, "hydrostatics");
            double lwl = J.Num(hydro, "lwl_m"), draft = J.Num(hydro, "draft_m");
            return new Dictionary<string, object>
            {
                { "draft_change_m", d.LimitDraftPercentT / 100.0 * draft },
                { "lcb_shift_percent_lwl", d.LimitLcbPercentLwl },
                { "lwl_change_percent", d.LimitLengthPercent },
                { "bwl_change_percent", d.LimitBeamPercent },
                { "normal_deviation_m", d.LimitSurfacePercentLwl / 100.0 * lwl },
                { "upper_edge_m", d.LimitUpperEdgePercentLwl / 100.0 * lwl },
                { "slope_dy_dx", d.LimitSlopeDyDx },
                { "slope_dy_dz", d.LimitSlopeDyDz },
            };
        }

        /// <summary>
        /// Searches the shape modes for the smallest objective ratio J with the Optimise tab's settings.
        /// The gates are the Morph tab's limits and the fairness gates; every evaluation is logged to a
        /// JSON-lines file in the work folder. Cancel asks the search to stop and keep what it found.
        /// </summary>
        public static void RunOptimise(RhinoDoc doc)
        {
            var d = Definition(doc);
            var problem = HullProblem(doc, d) ?? d.FloatingProblem() ?? d.OptimiseProblem();
            if (problem != null)
            {
                Say(problem, true);
                return;
            }
            string speedProblem;
            var speeds = d.Speeds(out speedProblem);
            if (!Begin("Exporting the hull")) return;
            ExportResult export;
            try
            {
                export = Export(doc, d);
            }
            catch (Exception ex)
            {
                End();
                Say("Hull export failed: " + ex.Message, true);
                return;
            }
            var mpu = export.MetersPerUnit;
            var frameParameters = MorphParameters(d, export);
            frameParameters.Remove("amplitudes");
            var run = MorphParameters(d, export);
            run.Remove("amplitudes");
            AddResistanceParameters(run, d, mpu, speeds);
            run.Remove("methods");
            if (d.OptimiseQuantity == "brake_power") AddPropulsionParameters(run, d, mpu);
            var objective = new Dictionary<string, object> { { "quantity", d.OptimiseQuantity }, { "weighting", d.OptimiseWeighting } };
            if (d.OptimiseWeighting == "profile")
            {
                string profileProblem;
                objective["profile"] = d.Profile(out profileProblem)
                    .Select(pair => (object)new List<object> { pair.Key, pair.Value }).ToList();
            }
            else if (d.OptimiseWeighting == "service")
            {
                objective["service_speed_kn"] = d.ObjectiveServiceSpeed.Value;
            }
            run["objective"] = objective;
            var variables = new Dictionary<string, object>();
            foreach (var name in ModeNames)
            {
                if (d.OptimiseModes == null || !d.OptimiseModes.Contains(name)) continue;
                var bounds = new Dictionary<string, object>();
                double value;
                if (d.OptimiseMin != null && d.OptimiseMin.TryGetValue(name, out value)) bounds["min"] = value;
                if (d.OptimiseMax != null && d.OptimiseMax.TryGetValue(name, out value)) bounds["max"] = value;
                variables[name] = bounds;
            }
            run["variables"] = variables;
            run["search"] = new Dictionary<string, object>
            {
                { "population", d.OptimisePopulation },
                { "max_evaluations", d.OptimiseEvaluations },
                { "seed", d.OptimiseSeed },
                { "start", d.OptimiseStart },
            };
            var log = Path.Combine(WorkFolder(doc), "optimise_" +
                                   DateTime.Now.ToString("yyyyMMdd_HHmmss", System.Globalization.CultureInfo.InvariantCulture) + ".jsonl");
            run["log_path"] = log;
            var stopFile = log + ".stop";
            RemoveStopFile(stopFile);
            run["stop_path"] = stopFile;
            var limits = d.Copy();
            var serial = doc.RuntimeSerialNumber;
            var unit = UnitName(doc);
            Interlocked.Exchange(ref _optimiseRequest, 0);
            _stopSent = false;
            _stopFile = stopFile;
            Optimising = true;
            Raise(OptimiseStarted);
            OptimiseAsync(AppendageFloating(d, export), frameParameters, run, limits, _cancel.Token).ContinueWith(task => Ui(() =>
            {
                Optimising = false;
                Interlocked.Exchange(ref _optimiseRequest, 0);
                _stopFile = null;
                RemoveStopFile(stopFile);
                End();
                if (task.IsCanceled)
                {
                    Say("optimisation cancelled; the candidates evaluated so far are listed in " + log + ".", false);
                    return;
                }
                if (task.IsFaulted)
                {
                    Say("Optimisation failed: " + ErrorText(task.Exception), true);
                    return;
                }
                var outcome = new Outcome { Kind = "optimize.run", Result = task.Result, MetersPerUnit = mpu, UnitName = unit, DocSerial = serial };
                outcome.Warnings.AddRange(export.Warnings);
                LastOptimise = outcome;
                var handler = OptimiseReady;
                if (handler != null) handler(outcome);
                var best = J.Obj(task.Result, "best");
                var search = J.Obj(task.Result, "search");
                var stopped = search != null && true.Equals(search.ContainsKey("stopped") ? search["stopped"] : null);
                Say(string.Format(System.Globalization.CultureInfo.InvariantCulture,
                    "optimisation {0} after {1:0} evaluations: {2}",
                    stopped ? "stopped" : "done", J.Num(search, "evaluations"),
                    best == null ? "no feasible candidate." :
                    string.Format(System.Globalization.CultureInfo.InvariantCulture, "best J {0:0.00000} ({1:+0.00;-0.00;0.00}%).",
                                  J.Num(best, "J"), 100.0 * (J.Num(best, "J") - 1.0))), false);
            }));
        }

        private static async Task<Dictionary<string, object>> OptimiseAsync(Dictionary<string, object> floating,
            Dictionary<string, object> frameParameters, Dictionary<string, object> run, HullDefinition d,
            CancellationToken cancel)
        {
            await UseWaterline(floating, cancel, frameParameters, run).ConfigureAwait(false);
            var frame = await EngineClient.Shared.CallAsync("morph.frame", frameParameters, cancel).ConfigureAwait(false);
            run["limits"] = GateLimits(d, frame);
            var result = await EngineClient.Shared.CallAsync("optimize.run", run, cancel,
                                                             id => Interlocked.Exchange(ref _optimiseRequest, id)).ConfigureAwait(false);
            result["modes"] = frame.ContainsKey("modes") ? frame["modes"] : null;
            return result;
        }

        /// <summary>Copies a candidate's amplitudes to the Morph tab; with evaluate, evaluates it there.</summary>
        public static void UseCandidate(RhinoDoc doc, Dictionary<string, double> amplitudes, bool evaluate)
        {
            if (doc == null || amplitudes == null) return;
            var d = Definition(doc);
            d.MorphAmplitudes = amplitudes.Where(a => a.Value != 0).ToDictionary(a => a.Key, a => a.Value);
            Save(doc, d);
            RequestTab("Morph");
            if (evaluate) RunMorph(doc);
        }

        /// <summary>Builds the current Morph candidate as new surfaces on layer HullOpt::Candidates.</summary>
        public static void BuildCandidate(RhinoDoc doc)
        {
            var d = Definition(doc);
            var problem = HullProblem(doc, d) ?? d.FloatingProblem() ?? d.MorphProblem();
            if (problem != null)
            {
                Say(problem, true);
                return;
            }
            if (!Begin("Exporting the hull")) return;
            ExportResult export;
            try
            {
                export = Export(doc, d);
            }
            catch (Exception ex)
            {
                End();
                Say("Hull export failed: " + ex.Message, true);
                return;
            }
            var parameters = MorphParameters(d, export);
            var hullIds = d.HullObjects.ToList();
            var amplitudes = (Dictionary<string, object>)parameters["amplitudes"];
            var serial = doc.RuntimeSerialNumber;
            BuildAsync(AppendageFloating(d, export), parameters, _cancel.Token).ContinueWith(task => Ui(() =>
            {
                End();
                if (task.IsCanceled)
                {
                    Say("Building the candidate cancelled.", false);
                    return;
                }
                if (task.IsFaulted)
                {
                    Say("Building the candidate failed: " + ErrorText(task.Exception), true);
                    return;
                }
                var current = RhinoDoc.FromRuntimeSerialNumber(serial);
                if (current == null) return;
                var warnings = new List<string>();
                List<Brep> breps;
                try
                {
                    breps = CandidateBuilder.Build(current, hullIds, task.Result, warnings);
                }
                catch (Exception ex)
                {
                    Say("Building the candidate failed: " + ex.Message, true);
                    return;
                }
                if (breps.Count == 0)
                {
                    Say("No candidate surfaces could be built. " + string.Join(" ", warnings), true);
                    return;
                }
                var name = "HullOpt candidate " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture) +
                           (amplitudes.Count == 0 ? " (baseline)" : " (" + string.Join(", ", amplitudes.Select(a =>
                               a.Key + " " + Convert.ToDouble(a.Value).ToString("0.####", System.Globalization.CultureInfo.InvariantCulture))) + ")");
                AddToLayer(current, "Candidates", System.Drawing.Color.FromArgb(216, 90, 48), breps, name);
                Say("candidate built on layer HullOpt::Candidates (" + breps.Count + (breps.Count == 1 ? " object)." : " objects).") +
                    (warnings.Count > 0 ? " " + string.Join(" ", warnings) : ""), warnings.Count > 0);
            }));
        }

        private static async Task<Dictionary<string, object>> BuildAsync(Dictionary<string, object> floating,
            Dictionary<string, object> parameters, CancellationToken cancel)
        {
            await UseWaterline(floating, cancel, parameters).ConfigureAwait(false);
            return await EngineClient.Shared.CallAsync("morph.export", parameters, cancel).ConfigureAwait(false);
        }

        private static void AddToLayer(RhinoDoc doc, string child, System.Drawing.Color color, List<Brep> breps, string name)
        {
            var index = ResultLayer(doc, child, color);
            if (index < 0) return;
            var undo = doc.BeginUndoRecord("HullOpt " + child);
            try
            {
                var attributes = new ObjectAttributes { LayerIndex = index, Name = name };
                if (breps.Count > 1)
                {
                    var group = doc.Groups.Add();
                    if (group >= 0) attributes.AddToGroup(group);
                }
                foreach (var brep in breps) doc.Objects.AddBrep(brep, attributes);
            }
            finally
            {
                doc.EndUndoRecord(undo);
            }
            doc.Views.Redraw();
        }

        private static async Task<Dictionary<string, object>> ResistanceAsync(
            Dictionary<string, object> baseParameters, Dictionary<string, object> resistance, bool byDisplacement,
            double? displacement, double? waterline, double mpu, CancellationToken cancel)
        {
            Dictionary<string, object> floating = null;
            double z;
            if (byDisplacement)
            {
                var parameters = new Dictionary<string, object>(baseParameters) { { "displacement_t", displacement.Value } };
                floating = await EngineClient.Shared.CallAsync("hydro.equilibrium", parameters, cancel).ConfigureAwait(false);
                z = J.Num(floating, "z_wl_m");
            }
            else
            {
                z = waterline.Value * mpu;
                var parameters = new Dictionary<string, object>(baseParameters) { { "z_wl_m", z } };
                floating = await EngineClient.Shared.CallAsync("hydro.measure", parameters, cancel).ConfigureAwait(false);
            }
            resistance["z_wl_m"] = z;
            var result = await EngineClient.Shared.CallAsync("resistance.evaluate", resistance, cancel).ConfigureAwait(false);
            if (floating != null && floating.ContainsKey("waterline_m")) result["waterline_m"] = floating["waterline_m"];
            return result;
        }

        /// <summary>Replaces the curves on layer HullOpt::Waterline with the engine's waterline cut.</summary>
        public static void DrawWaterline(RhinoDoc doc, Dictionary<string, object> result, double metersPerUnit)
        {
            var lines = J.List(result, "waterline_m");
            if (lines == null) return;
            var polylines = new List<Polyline>();
            foreach (var line in lines)
            {
                var points = line as IList;
                if (points == null) continue;
                var polyline = new Polyline();
                foreach (var item in points)
                {
                    var xyz = J.Nums(item as IList);
                    if (xyz.Length == 3) polyline.Add(xyz[0] / metersPerUnit, xyz[1] / metersPerUnit, xyz[2] / metersPerUnit);
                }
                if (polyline.Count >= 2) polylines.Add(polyline);
            }
            // The engine cuts the whole mesh once; its polylines end at face seams, where the separately
            // tessellated faces share no vertices. Joining within 1 mm gives whole waterlines.
            var pieces = polylines.Select(p => (Curve)new PolylineCurve(p)).ToList();
            var joined = Curve.JoinCurves(pieces, 0.001 / metersPerUnit);
            ReplaceLayerCurves(doc, "Waterline", System.Drawing.Color.FromArgb(24, 95, 165),
                               joined != null && joined.Length > 0 ? joined.ToList() : pieces);
        }

        private static int ResultLayer(RhinoDoc doc, string child, System.Drawing.Color color)
        {
            var root = doc.Layers.FindByFullPath("HullOpt", -1);
            if (root < 0) root = doc.Layers.Add(new Layer { Name = "HullOpt", Color = System.Drawing.Color.Black });
            var index = doc.Layers.FindByFullPath("HullOpt::" + child, -1);
            if (index < 0 && root >= 0)
                index = doc.Layers.Add(new Layer { Name = child, Color = color, ParentLayerId = doc.Layers[root].Id });
            return index;
        }

        private static void ReplaceLayerCurves(RhinoDoc doc, string child, System.Drawing.Color color, List<Curve> curves)
        {
            var index = ResultLayer(doc, child, color);
            if (index < 0) return;
            var undo = doc.BeginUndoRecord("HullOpt " + child);
            try
            {
                var existing = doc.Objects.FindByLayer(doc.Layers[index]);
                if (existing != null)
                    foreach (var obj in existing) doc.Objects.Delete(obj.Id, true);
                var attributes = new ObjectAttributes { LayerIndex = index };
                foreach (var curve in curves) doc.Objects.AddCurve(curve, attributes);
            }
            finally
            {
                doc.EndUndoRecord(undo);
            }
            doc.Views.Redraw();
        }
    }
}

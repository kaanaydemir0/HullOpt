using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Rhino;

namespace HullOpt
{
    /// <summary>
    /// What the user chose for one document: hull objects, floating condition, speeds and model
    /// options. Saved inside the .3dm as a document string, so it travels with the model.
    /// Lengths are in model units; the engine receives metres. No value belongs to a particular hull.
    /// </summary>
    public sealed class HullDefinition
    {
        public const string Section = "HullOpt";
        public const string Entry = "definition.v1";
        public const string FloatByWaterline = "waterline";
        public const string FloatByDisplacement = "displacement";

        public List<string> HullObjects { get; set; } = new List<string>();
        public List<string> AppendageObjects { get; set; } = new List<string>();
        public bool IncludeAppendages { get; set; }

        public string WaterPreset { get; set; } = "sea15";
        public double Rho { get; set; } = 1025.9;
        public double Nu { get; set; } = 1.18831e-6;

        public string FloatMode { get; set; } = FloatByWaterline;
        public double? WaterlineZ { get; set; }
        public double? DisplacementTonnes { get; set; }

        public double SpeedFrom { get; set; } = 6;
        public double SpeedTo { get; set; } = 14;
        public double SpeedStep { get; set; } = 1;
        /// <summary>More speeds besides the range: single speeds and ranges, e.g. "13.7; 17; 18-24:2".</summary>
        public string SpeedList { get; set; } = "";
        public double? DesignSpeed { get; set; }

        public bool UseHoltrop { get; set; } = true;
        public bool UseMichell { get; set; }
        public string MichellMultiInterval { get; set; } = "equivalent_width";
        public double MichellBowPaddingPercent { get; set; } = 1.0;
        public bool UseFriction { get; set; } = true;
        public string EntrancePolicy { get; set; } = "measured";
        public double? EntranceAngle { get; set; }
        public string LcbReference { get; set; } = "lwl_midpoint";
        public double? ApX { get; set; }
        public double? FpX { get; set; }
        public double SternParameter { get; set; }
        public double RoughnessMicrons { get; set; } = 150;
        public bool FrictionIncludesTransom { get; set; } = true;

        // Propulsion tab. Diameter in metres; the shaft centre in the model's own z (model units).
        public bool PropulsionEnabled { get; set; }
        public int Propellers { get; set; } = 2;
        public int Blades { get; set; } = 4;
        public double? PropellerDiameter { get; set; }
        public double? ShaftZ { get; set; }
        public double? PitchRatio { get; set; }
        public double? AreaRatio { get; set; }
        public double? KellerConstant { get; set; }
        public string WakeMethod { get; set; } = "holtrop";
        public double? WakeFraction { get; set; }
        public string ThrustDeductionMethod { get; set; } = "holtrop";
        public double? ThrustDeduction { get; set; }
        public string RelativeRotativeMethod { get; set; } = "holtrop";
        public double? RelativeRotative { get; set; }
        public string DesignThrust { get; set; } = "service";
        public double AppendageMarginPercent { get; set; }
        public double AirMarginPercent { get; set; }
        public double SeaMarginPercent { get; set; } = 15;
        public double ShaftEfficiency { get; set; } = 0.97;
        public int Engines { get; set; } = 2;
        public double? EngineMcrKw { get; set; }
        public double McrPercent { get; set; } = 85;
        public double PtoKw { get; set; }
        public double? EngineRpm { get; set; }

        public bool DrawWaterline { get; set; } = true;
        public int MaxGrid { get; set; } = 50;

        /// <summary>Shape-mode amplitudes by engine mode name (m or fraction, see the Morph tab).</summary>
        public Dictionary<string, double> MorphAmplitudes { get; set; } = new Dictionary<string, double>();
        public bool MorphResistance { get; set; } = true;
        // Gate limits relative to the hull, so one set of defaults suits any size. The defaults are the
        // first test hull's study limits expressed this way.
        public double LimitDraftPercentT { get; set; } = 2.4;
        public double LimitLcbPercentLwl { get; set; } = 0.25;
        public double LimitLengthPercent { get; set; } = 0.2;
        public double LimitBeamPercent { get; set; } = 0.2;
        public double LimitSurfacePercentLwl { get; set; } = 0.25;
        public double LimitUpperEdgePercentLwl { get; set; } = 0.012;
        public double LimitSlopeDyDx { get; set; } = 0.05;
        public double LimitSlopeDyDz { get; set; } = 0.10;

        // Optimise tab. The gates are the Morph tab's limits plus the fairness gates.
        /// <summary>Shape modes the search may change; the Lackenby modes move the deck edge and start off.</summary>
        public List<string> OptimiseModes { get; set; } =
            new List<string> { "bow_fine", "bow_tip", "stern_narrow", "stern_lift", "bilge_full", "deadrise" };
        /// <summary>Bounds by mode name in Morph-tab units; a missing entry lets the engine choose (relative to the hull).</summary>
        public Dictionary<string, double> OptimiseMin { get; set; } = new Dictionary<string, double>();
        public Dictionary<string, double> OptimiseMax { get; set; } = new Dictionary<string, double>();
        /// <summary>holtrop, michell or brake_power.</summary>
        public string OptimiseQuantity { get; set; } = "holtrop";
        /// <summary>equal (the Condition speeds), profile (time shares) or service (one speed).</summary>
        public string OptimiseWeighting { get; set; } = "equal";
        /// <summary>Operating profile "speed:share; ..." e.g. "12:60; 14:20; 16:20"; shares are normalised.</summary>
        public string OptimiseProfile { get; set; } = "";
        /// <summary>Speed of the service-speed objective; blank uses the design speed.</summary>
        public double? ServiceSpeed { get; set; }
        public int OptimisePopulation { get; set; } = 30;
        public int OptimiseEvaluations { get; set; } = 600;
        public int OptimiseSeed { get; set; } = 170026;
        /// <summary>uniform: the first generation spread over the bounds; baseline: clustered at the baseline.</summary>
        public string OptimiseStart { get; set; } = "uniform";

        public static HullDefinition Load(RhinoDoc doc)
        {
            if (doc == null) return new HullDefinition();
            var text = doc.Strings.GetValue(Section, Entry);
            if (string.IsNullOrEmpty(text)) return new HullDefinition();
            try
            {
                return Json.Deserialize<HullDefinition>(text) ?? new HullDefinition();
            }
            catch (Exception)
            {
                return new HullDefinition();
            }
        }

        public void Save(RhinoDoc doc)
        {
            if (doc != null) doc.Strings.SetString(Section, Entry, Json.Serialize(this));
        }

        public HullDefinition Copy()
        {
            return Json.Deserialize<HullDefinition>(Json.Serialize(this));
        }

        /// <summary>Most speeds one calculation may hold.</summary>
        public const int MaxSpeeds = 1000;

        /// <summary>
        /// Every speed to compute: the range plus the extra list, sorted, each once (to 1e-6 kn).
        /// An empty list and a problem message when an entry cannot be read.
        /// </summary>
        public List<double> Speeds(out string problem)
        {
            problem = null;
            if (!(SpeedFrom > 0) || !(SpeedTo >= SpeedFrom) || !(SpeedStep > 0))
            {
                problem = "Speeds: 'from' must be positive, 'to' at least 'from', and the step positive.";
                return new List<double>();
            }
            var set = new SortedSet<double>();
            problem = AddRange(set, SpeedFrom, SpeedTo, SpeedStep) ?? AddList(set, SpeedList, SpeedStep);
            if (problem == null && set.Count > MaxSpeeds)
                problem = "Speeds: at most " + MaxSpeeds + " speeds; use a larger step.";
            return problem == null ? set.ToList() : new List<double>();
        }

        /// <summary>"N speeds, a–b kn" for the speeds of a set.</summary>
        public static string Describe(IList<double> speeds)
        {
            if (speeds == null || speeds.Count == 0) return "No speeds.";
            if (speeds.Count == 1) return "1 speed: " + Format(speeds[0], "0.###") + " kn.";
            return speeds.Count + " speeds, " + Format(speeds[0], "0.###") + "–" + Format(speeds[speeds.Count - 1], "0.###") + " kn.";
        }

        private static string AddRange(SortedSet<double> set, double from, double to, double step)
        {
            var count = Math.Floor((to - from) / step + 1e-9) + 1;
            if (count > MaxSpeeds) return "Speeds: at most " + MaxSpeeds + " speeds; use a larger step.";
            for (var i = 0; i < (int)count; i++) set.Add(Math.Round(from + i * step, 6));
            return null;
        }

        /// <summary>
        /// Adds the entries of a list: items separated by ';' or spaces, each a speed ("13.7" or
        /// "13,7") or a range "a-b" in the main step or "a-b:s" in its own step.
        /// </summary>
        public static string AddList(SortedSet<double> set, string text, double step)
        {
            if (string.IsNullOrWhiteSpace(text)) return null;
            var items = text.Split(new[] { ';', ' ', '\t', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            foreach (var raw in items)
            {
                var item = raw.Trim().TrimEnd(',');
                if (item.Length == 0) continue;
                var bad = "Speeds: cannot read '" + raw + "'. Separate speeds with ';' or spaces; write a range as 18-24 or 18-24:2.";
                var dash = item.IndexOfAny(new[] { '-', '–', '—' }, 1);
                if (dash < 0)
                {
                    double speed;
                    if (!TryParse(item, out speed)) return bad;
                    if (!(speed > 0)) return "Speeds: every speed must be positive ('" + raw + "').";
                    set.Add(Math.Round(speed, 6));
                    continue;
                }
                var rest = item.Substring(dash + 1);
                var colon = rest.IndexOf(':');
                double from, to, own = step;
                if (!TryParse(item.Substring(0, dash), out from) ||
                    !TryParse(colon < 0 ? rest : rest.Substring(0, colon), out to) ||
                    (colon >= 0 && !TryParse(rest.Substring(colon + 1), out own)))
                    return bad;
                if (!(from > 0) || !(to >= from) || !(own > 0))
                    return "Speeds: in '" + raw + "' the start must be positive, the end at least the start and the step positive.";
                var problem = AddRange(set, from, to, own);
                if (problem != null) return problem;
            }
            return null;
        }

        public string FloatingProblem()
        {
            if (FloatMode == FloatByDisplacement)
                return DisplacementTonnes.HasValue && DisplacementTonnes.Value > 0
                    ? null : "Condition: enter the displacement in tonnes.";
            return WaterlineZ.HasValue ? null : "Condition: enter the waterline height (model z).";
        }

        public string ResistanceProblem()
        {
            if (!UseHoltrop && !UseMichell && !UseFriction) return "Resistance: choose at least one method.";
            if (UseHoltrop && EntrancePolicy == "given" && !(EntranceAngle > 0 && EntranceAngle < 90))
                return "Holtrop: enter the half angle of entrance (degrees) or let HullOpt measure it.";
            if (UseHoltrop && LcbReference == "amidships")
            {
                if (!ApX.HasValue || !FpX.HasValue) return "Holtrop: enter AP and FP positions for the amidships LCB reference.";
                if (FpX.Value <= ApX.Value) return "Holtrop: FP must lie forward of AP (larger x).";
            }
            if (RoughnessMicrons < 150) return "Holtrop: roughness below 150 µm is outside the method.";
            if (UseMichell && !(MichellBowPaddingPercent >= 0 && MichellBowPaddingPercent <= 10))
                return "Michell: the grid extension ahead of the bow must be between 0 and 10% of LWL.";
            return null;
        }

        public string PropulsionProblem()
        {
            if (!UseHoltrop) return "Propulsion: brake power uses Holtrop 1984's total resistance; tick Holtrop on the Resistance tab.";
            if (!(DesignSpeed > 0)) return "Propulsion: enter the design speed on the Condition tab.";
            if (!(PropellerDiameter > 0)) return "Propulsion: enter the propeller diameter in metres.";
            if (!ShaftZ.HasValue) return "Propulsion: enter the shaft centre height (model z).";
            if (Propellers < 1 || Propellers > 4) return "Propulsion: choose 1 to 4 propellers.";
            if (Blades < 2 || Blades > 7) return "Propulsion: the B-series covers 2 to 7 blades.";
            var holtrop = new List<string>();
            if (WakeMethod == "holtrop") holtrop.Add("w");
            if (ThrustDeductionMethod == "holtrop") holtrop.Add("t");
            if (RelativeRotativeMethod == "holtrop") holtrop.Add("ηR");
            if (Propellers != 2 && holtrop.Count > 0)
                return "Propulsion: Holtrop's propulsion factors here are the twin-screw formulas; with " + Propellers +
                       " propeller(s) give " + string.Join(", ", holtrop) + " as values" + (holtrop.Contains("w") ? " (or use the Taylor wake)." : ".");
            if (WakeMethod == "taylor" && Propellers > 2) return "Propulsion: Taylor's wake covers single and twin screws; give w.";
            if (WakeMethod == "given" && !(WakeFraction >= 0 && WakeFraction < 1)) return "Propulsion: enter the wake fraction w (0 to below 1).";
            if (ThrustDeductionMethod == "given" && !(ThrustDeduction >= 0 && ThrustDeduction < 1))
                return "Propulsion: enter the thrust deduction t (0 to below 1).";
            if (RelativeRotativeMethod == "given" && !(RelativeRotative > 0 && RelativeRotative <= 1.2))
                return "Propulsion: enter the relative rotative efficiency ηR (above 0, at most 1.2).";
            if (PitchRatio.HasValue && !(PitchRatio > 0)) return "Propulsion: the pitch ratio must be positive (or blank).";
            if (AreaRatio.HasValue && !(AreaRatio > 0)) return "Propulsion: the blade area ratio must be positive (or blank).";
            if (KellerConstant.HasValue && !(KellerConstant >= 0)) return "Propulsion: the Keller constant must not be negative (or blank).";
            var margins = new[] { AppendageMarginPercent, AirMarginPercent, SeaMarginPercent };
            if (margins.Any(m => !(m >= 0 && m <= 100))) return "Propulsion: margins are 0 to 100%.";
            if (!(ShaftEfficiency > 0 && ShaftEfficiency <= 1)) return "Propulsion: the shaft and gear efficiency is above 0 and at most 1.";
            if (Engines < 1 || Engines > 8) return "Propulsion: choose 1 to 8 engines.";
            if (EngineMcrKw.HasValue && !(EngineMcrKw > 0)) return "Propulsion: the engine MCR must be positive (or blank).";
            if (!(McrPercent > 0 && McrPercent <= 100)) return "Propulsion: the continuous service rating is above 0 and at most 100% MCR.";
            if (!(PtoKw >= 0)) return "Propulsion: the PTO power must not be negative.";
            if (EngineRpm.HasValue && !(EngineRpm > 0)) return "Propulsion: the engine speed must be positive (or blank).";
            return null;
        }

        public string MorphProblem()
        {
            var limits = new[] { LimitDraftPercentT, LimitLcbPercentLwl, LimitLengthPercent, LimitBeamPercent,
                                 LimitSurfacePercentLwl, LimitUpperEdgePercentLwl, LimitSlopeDyDx, LimitSlopeDyDz };
            if (limits.Any(v => !(v > 0) || double.IsInfinity(v))) return "Morph: every limit must be a positive number.";
            if (MorphAmplitudes != null && MorphAmplitudes.Values.Any(v => double.IsNaN(v) || double.IsInfinity(v)))
                return "Morph: amplitudes must be numbers.";
            return null;
        }

        /// <summary>The speed of the service-speed objective: the service speed, else the design speed.</summary>
        public double? ObjectiveServiceSpeed
        {
            get { return ServiceSpeed.HasValue ? ServiceSpeed : DesignSpeed; }
        }

        /// <summary>
        /// The operating profile as (speed, share) pairs sorted by speed, shares as typed (the engine
        /// normalises them). Items are separated by ';' or new lines, each "speed:share" ("12,5:30" works).
        /// </summary>
        public List<KeyValuePair<double, double>> Profile(out string problem)
        {
            problem = null;
            var pairs = new SortedDictionary<double, double>();
            var items = (OptimiseProfile ?? "").Split(new[] { ';', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            foreach (var raw in items)
            {
                var item = raw.Trim();
                if (item.Length == 0) continue;
                var parts = item.Split(new[] { ':', '=' });
                double speed, share;
                if (parts.Length != 2 || !TryParse(parts[0], out speed) || !TryParse(parts[1].Replace("%", ""), out share))
                {
                    problem = "Optimise: cannot read '" + item + "' in the profile. Write speed:share items separated by ';', e.g. 12:60; 14:20; 16:20.";
                    return new List<KeyValuePair<double, double>>();
                }
                speed = Math.Round(speed, 6);
                if (!(speed > 0) || share < 0)
                {
                    problem = "Optimise: profile speeds must be positive and shares zero or positive ('" + item + "').";
                    return new List<KeyValuePair<double, double>>();
                }
                if (pairs.ContainsKey(speed))
                {
                    problem = "Optimise: the profile names " + Format(speed, "0.###") + " kn twice.";
                    return new List<KeyValuePair<double, double>>();
                }
                pairs[speed] = share;
            }
            if (pairs.Count == 0) problem = "Optimise: enter the operating profile, e.g. 12:60; 14:20; 16:20 (speed:share).";
            else if (!(pairs.Values.Sum() > 0)) problem = "Optimise: the profile shares must not all be zero.";
            else if (pairs.Count > MaxSpeeds) problem = "Optimise: at most " + MaxSpeeds + " profile speeds.";
            return problem == null ? pairs.ToList() : new List<KeyValuePair<double, double>>();
        }

        public string OptimiseProblem()
        {
            var problem = MorphProblem();
            if (problem != null) return problem;
            if (OptimiseModes == null || OptimiseModes.Count == 0) return "Optimise: tick at least one shape mode to vary.";
            foreach (var name in OptimiseModes)
            {
                double low, high;
                if (OptimiseMin != null && OptimiseMax != null && OptimiseMin.TryGetValue(name, out low) &&
                    OptimiseMax.TryGetValue(name, out high) && !(high > low))
                    return "Optimise: the upper bound of " + name + " must be above its lower bound.";
            }
            if (OptimiseQuantity == "brake_power")
            {
                problem = PropulsionProblem();
                if (problem != null) return problem.Replace("Propulsion:", "Optimise (brake power) needs the Propulsion tab:");
            }
            else if (OptimiseQuantity == "holtrop" || OptimiseQuantity == "michell")
            {
                problem = ResistanceProblem();
                if (problem != null && (OptimiseQuantity == "holtrop" ? problem.StartsWith("Holtrop") : problem.StartsWith("Michell")))
                    return problem;
            }
            else return "Optimise: choose what to minimise.";
            if (OptimiseWeighting == "equal")
            {
                Speeds(out problem);
                if (problem != null) return problem;
            }
            else if (OptimiseWeighting == "profile")
            {
                Profile(out problem);
                if (problem != null) return problem;
            }
            else if (OptimiseWeighting == "service")
            {
                if (!(ObjectiveServiceSpeed > 0)) return "Optimise: enter the service speed (or the design speed on the Condition tab).";
            }
            else return "Optimise: choose how the speeds are weighted.";
            if (OptimisePopulation < 30) return "Optimise: the population must be at least 30.";
            if (OptimiseEvaluations < OptimisePopulation) return "Optimise: allow at least one full population of evaluations.";
            if (OptimiseSeed < 0) return "Optimise: the random seed must be zero or positive.";
            return null;
        }

        public static string Format(double? value, string format = "0.######")
        {
            return value.HasValue ? value.Value.ToString(format, CultureInfo.InvariantCulture) : "";
        }

        /// <summary>Reads a number typed with either a decimal point or a decimal comma.</summary>
        public static bool TryParse(string text, out double value)
        {
            value = double.NaN;
            if (string.IsNullOrWhiteSpace(text)) return false;
            var cleaned = text.Trim().Replace(" ", "").Replace(',', '.');
            return double.TryParse(cleaned, NumberStyles.Float, CultureInfo.InvariantCulture, out value)
                   && !double.IsNaN(value) && !double.IsInfinity(value);
        }
    }
}

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Eto.Drawing;
using Eto.Forms;
using Rhino;

namespace HullOpt
{
    /// <summary>
    /// Propulsion tab: propeller, propulsion factors, margins and machinery; brake power at every speed
    /// of the Condition tab, from Holtrop 1984's total resistance.
    /// </summary>
    public sealed class PropulsionPage : Panel
    {
        private readonly Func<RhinoDoc> _doc;
        private readonly CheckBox _enabled = new CheckBox { Text = "Compute brake power with every resistance run (uses Holtrop 1984's total resistance)" };
        private readonly DropDown _propellers = Ui.Choices("1", "1", "2", "2", "3", "3", "4", "4");
        private readonly DropDown _blades = Ui.Choices("2", "2", "3", "3", "4", "4", "5", "5", "6", "6", "7", "7");
        private readonly NumberBox _diameter = new NumberBox(70);
        private readonly NumberBox _shaftZ = new NumberBox(90);
        private readonly Label _shaftUnit = Ui.Unit("");
        private readonly NumberBox _pitch = new NumberBox(60);
        private readonly NumberBox _area = new NumberBox(60);
        private readonly NumberBox _keller = new NumberBox(60);
        private readonly DropDown _wake = Ui.Choices(
            "holtrop", "Holtrop 1984, twin screw (effective wake)",
            "taylor", "Taylor (single or twin screw)",
            "given", "Given value");
        private readonly NumberBox _wakeValue = new NumberBox(60);
        private readonly DropDown _thrust = Ui.Choices("holtrop", "Holtrop 1984, twin screw", "given", "Given value");
        private readonly NumberBox _thrustValue = new NumberBox(60);
        private readonly DropDown _etaR = Ui.Choices("holtrop", "Holtrop 1984, twin screw", "given", "Given value");
        private readonly NumberBox _etaRValue = new NumberBox(60);
        private readonly DropDown _designThrust = Ui.Choices(
            "service", "Service: thrust × (1 + sea margin)",
            "calm", "Calm water, clean hull");
        private readonly NumberBox _appendages = new NumberBox(60);
        private readonly NumberBox _air = new NumberBox(60);
        private readonly NumberBox _sea = new NumberBox(60);
        private readonly NumberBox _etaS = new NumberBox(60);
        private readonly DropDown _engines = Ui.Choices("1", "1", "2", "2", "3", "3", "4", "4", "5", "5", "6", "6", "7", "7", "8", "8");
        private readonly NumberBox _mcr = new NumberBox(80);
        private readonly NumberBox _mcrPercent = new NumberBox(60);
        private readonly NumberBox _pto = new NumberBox(60);
        private readonly NumberBox _rpm = new NumberBox(70);
        private readonly Label _caption = new Label { Wrap = WrapMode.Word };
        private readonly GridView _table = new GridView { Height = 260, ShowHeader = true };
        private readonly LineChart _chart = new LineChart();
        private readonly TextArea _notes = new TextArea { ReadOnly = true, Wrap = true, Height = 110 };
        private Outcome _shown;
        private bool _loading;

        private static readonly string[] Headers =
        {
            "Speed (kn)", "PE (kW)", "w", "t", "ηH", "J", "η0", "ηD", "n (rpm)", "PB (kW)", "PB service (kW)", "% MCR", "Notes",
        };

        public PropulsionPage(Func<RhinoDoc> doc)
        {
            _doc = doc;
            _propellers.Width = _blades.Width = _engines.Width = 60;
            _wake.Width = _thrust.Width = _etaR.Width = _designThrust.Width = 280;
            _enabled.CheckedChanged += (sender, e) => Change(d => d.PropulsionEnabled = _enabled.Checked == true);
            _propellers.SelectedIndexChanged += (sender, e) => Change(d => d.Propellers = Int(_propellers.SelectedKey, 2));
            _blades.SelectedIndexChanged += (sender, e) => Change(d => d.Blades = Int(_blades.SelectedKey, 4));
            _diameter.Committed += (sender, e) => Change(d => d.PropellerDiameter = _diameter.Value);
            _shaftZ.Committed += (sender, e) => Change(d => d.ShaftZ = _shaftZ.Value);
            _pitch.Committed += (sender, e) => Change(d => d.PitchRatio = _pitch.Value);
            _area.Committed += (sender, e) => Change(d => d.AreaRatio = _area.Value);
            _keller.Committed += (sender, e) => Change(d => d.KellerConstant = _keller.Value);
            _wake.SelectedIndexChanged += (sender, e) => Change(d => d.WakeMethod = _wake.SelectedKey ?? "holtrop");
            _wakeValue.Committed += (sender, e) => Change(d => d.WakeFraction = _wakeValue.Value);
            _thrust.SelectedIndexChanged += (sender, e) => Change(d => d.ThrustDeductionMethod = _thrust.SelectedKey ?? "holtrop");
            _thrustValue.Committed += (sender, e) => Change(d => d.ThrustDeduction = _thrustValue.Value);
            _etaR.SelectedIndexChanged += (sender, e) => Change(d => d.RelativeRotativeMethod = _etaR.SelectedKey ?? "holtrop");
            _etaRValue.Committed += (sender, e) => Change(d => d.RelativeRotative = _etaRValue.Value);
            _designThrust.SelectedIndexChanged += (sender, e) => Change(d => d.DesignThrust = _designThrust.SelectedKey ?? "service");
            _appendages.Committed += (sender, e) => Change(d => { if (_appendages.Value.HasValue) d.AppendageMarginPercent = _appendages.Value.Value; });
            _air.Committed += (sender, e) => Change(d => { if (_air.Value.HasValue) d.AirMarginPercent = _air.Value.Value; });
            _sea.Committed += (sender, e) => Change(d => { if (_sea.Value.HasValue) d.SeaMarginPercent = _sea.Value.Value; });
            _etaS.Committed += (sender, e) => Change(d => { if (_etaS.Value.HasValue) d.ShaftEfficiency = _etaS.Value.Value; });
            _engines.SelectedIndexChanged += (sender, e) => Change(d => d.Engines = Int(_engines.SelectedKey, 2));
            _mcr.Committed += (sender, e) => Change(d => d.EngineMcrKw = _mcr.Value);
            _mcrPercent.Committed += (sender, e) => Change(d => { if (_mcrPercent.Value.HasValue) d.McrPercent = _mcrPercent.Value.Value; });
            _pto.Committed += (sender, e) => Change(d => { if (_pto.Value.HasValue) d.PtoKw = _pto.Value.Value; });
            _rpm.Committed += (sender, e) => Change(d => d.EngineRpm = _rpm.Value);

            var run = new Button { Text = "Run resistance and power" };
            run.Click += (sender, e) =>
            {
                var current = _doc();
                if (current != null) Session.RunResistance(current, true);
            };
            var save = new Button { Text = "Save table as CSV…" };
            save.Click += (sender, e) => SaveCsv();

            var layout = new DynamicLayout { Padding = new Padding(8), Spacing = new Size(6, 6) };
            layout.AddRow(Ui.Heading("Brake power"));
            layout.AddRow(Ui.Note("Effective power from Holtrop 1984's total resistance (Resistance tab options), the propeller's operating " +
                                  "point on Wageningen B-series curves, propulsion factors and margins. Speeds and the design speed come " +
                                  "from the Condition tab; the design speed is added to the speeds when it is not one of them."));
            layout.AddRow(_enabled);
            layout.AddRow(Ui.Heading("Propeller"));
            layout.AddRow(Row(Name("Propellers"), _propellers, Ui.Field("Blades Z"), _blades));
            layout.AddRow(Row(Name("Diameter D"), _diameter, Ui.Unit("m")));
            layout.AddRow(Row(Name("Shaft centre height z"), _shaftZ, _shaftUnit));
            layout.AddRow(Row(Name("Pitch ratio P/D"), _pitch, Ui.Note("blank: best open-water efficiency at the design speed")));
            layout.AddRow(Row(Name("Blade area ratio AE/A0"), _area, Ui.Note("blank: Keller's minimum, rounded up to 0.05")));
            layout.AddRow(Row(Name("Keller constant K"), _keller, Ui.Note("blank: 0.1 twin screw, 0.2 single screw")));
            layout.AddRow(Row(Name("Design thrust"), _designThrust));
            layout.AddRow(Ui.Heading("Propulsion factors"));
            layout.AddRow(Row(Name("Wake fraction w"), _wake, _wakeValue));
            layout.AddRow(Row(Name("Thrust deduction t"), _thrust, _thrustValue));
            layout.AddRow(Row(Name("Relative rotative ηR"), _etaR, _etaRValue));
            layout.AddRow(Ui.Heading("Margins and machinery"));
            layout.AddRow(Row(Name("Extra appendages"), _appendages, Ui.Unit("% of R"), Ui.Field("   Air"), _air, Ui.Unit("% of R")));
            layout.AddRow(Row(Name("Sea margin"), _sea, Ui.Unit("% of power"), Ui.Field("   Shaft and gear ηS"), _etaS));
            layout.AddRow(Row(Name("Engines"), _engines, Ui.Field("   MCR per engine"), _mcr, Ui.Unit("kW")));
            layout.AddRow(Row(Name("Continuous service rating"), _mcrPercent, Ui.Unit("% MCR"), Ui.Field("   PTO"), _pto, Ui.Unit("kW")));
            layout.AddRow(Row(Name("Engine speed"), _rpm, Ui.Unit("rpm (optional, for the gear ratio)")));
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(run, save, null);
            layout.EndVertical();
            layout.AddRow(_caption);
            layout.AddRow(_table);
            layout.AddRow(_chart);
            layout.AddRow(Ui.Heading("Notes and model limits"));
            layout.AddRow(_notes);
            layout.Add(null, yscale: true);
            Content = Ui.Scroll(layout);
            Show(null);
        }

        /// <summary>Controls side by side at their own widths.</summary>
        private static Control Row(params Control[] items)
        {
            var row = new StackLayout { Orientation = Orientation.Horizontal, Spacing = 6, VerticalContentAlignment = VerticalAlignment.Center };
            foreach (var item in items) row.Items.Add(new StackLayoutItem(item));
            return row;
        }

        /// <summary>A field name of a fixed width, so the inputs line up.</summary>
        private static Label Name(string text)
        {
            var label = Ui.Field(text);
            label.Width = 170;
            return label;
        }

        private static int Int(string text, int fallback)
        {
            int value;
            return int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out value) ? value : fallback;
        }

        private static string F(double value, string format)
        {
            return double.IsNaN(value) ? "—" : value.ToString(format, CultureInfo.InvariantCulture);
        }

        private void Change(Action<HullDefinition> edit)
        {
            if (_loading) return;
            var doc = _doc();
            if (doc == null) return;
            var definition = Session.Definition(doc);
            edit(definition);
            Session.Save(doc, definition);
        }

        public void Refresh()
        {
            var doc = _doc();
            if (doc == null) return;
            var d = Session.Definition(doc);
            _loading = true;
            try
            {
                _enabled.Checked = d.PropulsionEnabled;
                _propellers.SelectedKey = d.Propellers.ToString(CultureInfo.InvariantCulture);
                _blades.SelectedKey = d.Blades.ToString(CultureInfo.InvariantCulture);
                _diameter.Show(d.PropellerDiameter);
                _shaftZ.Show(d.ShaftZ);
                _shaftUnit.Text = Session.UnitName(doc) + " (model z of the shaft centre at the propeller)";
                _pitch.Show(d.PitchRatio);
                _area.Show(d.AreaRatio);
                _keller.Show(d.KellerConstant);
                _wake.SelectedKey = d.WakeMethod;
                _wakeValue.Show(d.WakeFraction);
                _wakeValue.Enabled = d.WakeMethod == "given";
                _thrust.SelectedKey = d.ThrustDeductionMethod;
                _thrustValue.Show(d.ThrustDeduction);
                _thrustValue.Enabled = d.ThrustDeductionMethod == "given";
                _etaR.SelectedKey = d.RelativeRotativeMethod;
                _etaRValue.Show(d.RelativeRotative);
                _etaRValue.Enabled = d.RelativeRotativeMethod == "given";
                _designThrust.SelectedKey = d.DesignThrust;
                _appendages.Show(d.AppendageMarginPercent, "0.##");
                _air.Show(d.AirMarginPercent, "0.##");
                _sea.Show(d.SeaMarginPercent, "0.##");
                _etaS.Show(d.ShaftEfficiency, "0.###");
                _engines.SelectedKey = d.Engines.ToString(CultureInfo.InvariantCulture);
                _mcr.Show(d.EngineMcrKw, "0.#");
                _mcrPercent.Show(d.McrPercent, "0.##");
                _pto.Show(d.PtoKw, "0.#");
                _rpm.Show(d.EngineRpm, "0.#");
            }
            finally
            {
                _loading = false;
            }
        }

        public void Show(Outcome outcome)
        {
            _shown = outcome;
            var prop = outcome == null ? null : J.Obj(outcome.Result, "propulsion");
            _table.Columns.Clear();
            for (var c = 0; c < Headers.Length; c++)
                _table.Columns.Add(new GridColumn { HeaderText = Headers[c], DataCell = new TextBoxCell(c),
                                                    Width = c == Headers.Length - 1 ? 300 : c == 0 ? 70 : 75 });
            if (prop == null)
            {
                _table.DataStore = new List<string[]>();
                _caption.Text = "No brake power yet. Enter the propeller and machinery, then run resistance and power.";
                _chart.Clear("Run resistance and power to see the curves.");
                _notes.Text = "";
                return;
            }
            var design = J.Obj(prop, "design");
            var per = J.Obj(prop, "per_speed");
            var speeds = J.Nums(prop, "speeds_kn");
            Func<string, double[]> column = key => J.Nums(per, key);
            double[] pe = column("pe_W"), w = column("wake"), t = column("thrust_deduction"), etaH = column("eta_h"),
                     j = column("j"), eta0 = column("eta0"), etaD = column("eta_d"), rpm = column("rpm"),
                     pb = column("pb_W"), pbs = column("pb_service_W"), mcr = column("mcr_fraction");
            var notes = J.List(prop, "speed_notes");
            var holtropNotes = HoltropNotes(outcome);
            var rows = new List<string[]>();
            for (var i = 0; i < speeds.Length; i++)
            {
                var texts = new List<string>();
                if (holtropNotes != null && i < holtropNotes.Count && holtropNotes[i] is IList)
                    texts.AddRange(((IList)holtropNotes[i]).Cast<object>().Select(x => "Holtrop: " + Convert.ToString(x, CultureInfo.InvariantCulture)));
                if (notes != null && i < notes.Count && notes[i] is IList)
                    texts.AddRange(((IList)notes[i]).Cast<object>().Select(x => Convert.ToString(x, CultureInfo.InvariantCulture)));
                var note = string.Join("; ", texts);
                rows.Add(new[]
                {
                    F(speeds[i], "0.###"), F(At(pe, i) / 1000, "0.0"), F(At(w, i), "0.0000"), F(At(t, i), "0.0000"),
                    F(At(etaH, i), "0.0000"), F(At(j, i), "0.0000"), F(At(eta0, i), "0.0000"), F(At(etaD, i), "0.0000"),
                    F(At(rpm, i), "0.0"), F(At(pb, i) / 1000, "0.0"), F(At(pbs, i) / 1000, "0.0"), F(100 * At(mcr, i), "0.0"), note,
                });
            }
            _table.DataStore = rows;

            var series = new List<LineChart.Series>
            {
                Line("PE", speeds, pe, Color.FromArgb(136, 135, 128)),
                Line("PB calm water", speeds, pb, Color.FromArgb(24, 95, 165)),
                Line("PB service", speeds, pbs, Color.FromArgb(216, 90, 48)),
            };
            var installed = J.Num(design, "installed_W");
            if (installed > 0 && speeds.Length > 1)
                series.Add(new LineChart.Series { Name = "Installed (100% MCR)", X = new[] { speeds[0], speeds[speeds.Length - 1] },
                                                  Y = new[] { installed / 1000, installed / 1000 }, Color = Color.FromArgb(127, 119, 221) });
            _chart.Show("Speed (kn)", "Power (kW)", series.Where(s => s != null));

            var caption = new StringBuilder();
            caption.AppendFormat(CultureInfo.InvariantCulture,
                "Python engine results, {0:HH:mm:ss}. Design speed {1:0.###} kn: P/D {2:0.###} ({3}), AE/A0 {4:0.###} ({5}; Keller minimum {6:0.###}), " +
                "η0 {7:0.###}, ηR {8:0.###}, w {9:0.####}, t {10:0.####}. PB {11:0.#} kW calm water, {12:0.#} kW with the sea margin.",
                outcome.Time, J.Num(design, "speed_kn"), J.Num(design, "pitch_ratio"),
                J.Str(design, "pitch_ratio_source") == "given" ? "given" : "best η0",
                J.Num(design, "area_ratio"), J.Str(design, "area_ratio_source") == "given" ? "given" : "Keller",
                J.Num(design, "keller_area_ratio_min"), J.Num(design, "eta0"), J.Num(design, "eta_r"),
                J.Num(design, "wake"), J.Num(design, "thrust_deduction"), J.Num(design, "pb_W") / 1000, J.Num(design, "pb_service_W") / 1000);
            var required = J.Num(design, "required_installed_W");
            if (installed > 0)
            {
                caption.AppendFormat(CultureInfo.InvariantCulture, " Required installed power {0:0.#} kW of {1:0.#} kW ({2}).",
                    required / 1000, installed / 1000, required <= installed ? "sufficient" : "NOT sufficient");
                var vCalm = J.Num(design, "max_speed_calm_kn");
                var vService = J.Num(design, "max_speed_service_kn");
                caption.AppendFormat(CultureInfo.InvariantCulture, " Speed at 100% MCR: {0} kn calm water, {1} kn with the sea margin.",
                    F(vCalm, "0.##"), F(vService, "0.##"));
            }
            else if (!double.IsNaN(required))
            {
                caption.AppendFormat(CultureInfo.InvariantCulture, " Required installed power {0:0.#} kW (enter the engine MCR to check it).", required / 1000);
            }
            var gear = J.Num(design, "gear_ratio");
            if (!double.IsNaN(gear)) caption.AppendFormat(CultureInfo.InvariantCulture, " Gear ratio {0:0.###}.", gear);
            _caption.Text = caption.ToString();

            var text = new StringBuilder();
            foreach (var warning in outcome.Warnings) text.AppendLine("• " + warning);
            var added = J.Nums(J.Obj(outcome.Result, "conditions"), "added_speeds_kn");
            if (added.Length > 0)
                text.AppendLine("• The design speed " + string.Join(", ", added.Select(v => v.ToString("0.###", CultureInfo.InvariantCulture))) +
                                " kn was added to the speeds.");
            foreach (var warning in J.Strs(prop, "warnings")) text.AppendLine("• " + warning);
            var methods = J.Obj(prop, "methods");
            if (methods != null)
                text.AppendLine("• Methods: wake " + J.Str(methods, "wake") + "; thrust deduction " + J.Str(methods, "thrust_deduction") +
                                "; ηR " + J.Str(methods, "relative_rotative") + "; propeller " + J.Str(methods, "propeller") +
                                "; blade area " + J.Str(methods, "blade_area") + ".");
            _notes.Text = text.ToString().TrimEnd();
        }

        /// <summary>Per-speed notes of the Holtrop result the brake power was computed from.</summary>
        private static IList HoltropNotes(Outcome outcome)
        {
            var results = J.List(outcome.Result, "results");
            if (results == null) return null;
            foreach (var item in results)
            {
                var result = item as Dictionary<string, object>;
                if (J.Str(result, "backend") == "PY-HOLTROP-1984") return J.List(result, "speed_notes");
            }
            return null;
        }

        private static double At(double[] values, int i)
        {
            return values != null && i < values.Length ? values[i] : double.NaN;
        }

        private static LineChart.Series Line(string name, double[] speeds, double[] watts, Color color)
        {
            var x = new List<double>();
            var y = new List<double>();
            for (var i = 0; i < speeds.Length && i < watts.Length; i++)
            {
                if (double.IsNaN(watts[i])) continue;
                x.Add(speeds[i]);
                y.Add(watts[i] / 1000);
            }
            return x.Count == 0 ? null : new LineChart.Series { Name = name, X = x.ToArray(), Y = y.ToArray(), Color = color };
        }

        private void SaveCsv()
        {
            var rows = _table.DataStore as List<string[]>;
            if (_shown == null || rows == null || rows.Count == 0)
            {
                Session.Say("Run resistance and power before saving the table.", true);
                return;
            }
            var doc = _doc();
            var dialog = new SaveFileDialog { Title = "Save brake power table" };
            dialog.Filters.Add(new FileFilter("CSV file", ".csv"));
            if (doc != null)
            {
                try
                {
                    dialog.Directory = new Uri(Session.WorkFolder(doc) + Path.DirectorySeparatorChar);
                }
                catch (Exception)
                {
                    // Start in the dialog's default folder.
                }
            }
            dialog.FileName = "power_" + DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture) + ".csv";
            if (dialog.ShowDialog(this) != DialogResult.Ok) return;
            var csv = new StringBuilder();
            csv.AppendLine(string.Join(",", Headers.Select(h => Csv(h, true))));
            foreach (var row in rows) csv.AppendLine(string.Join(",", row.Select(cell => Csv(cell, false))));
            var path = dialog.FileName;
            if (!path.EndsWith(".csv", StringComparison.OrdinalIgnoreCase)) path += ".csv";
            File.WriteAllText(path, csv.ToString(), new UTF8Encoding(true));
            Session.Say("table saved: " + path, false);
        }

        private static string Csv(string text, bool quote)
        {
            text = text ?? "";
            if (quote || text.IndexOfAny(new[] { ',', '"', ';', '\n', '\r' }) >= 0)
                return "\"" + text.Replace("\"", "\"\"") + "\"";
            return text;
        }
    }
}

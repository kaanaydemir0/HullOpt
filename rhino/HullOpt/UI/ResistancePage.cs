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
    /// <summary>Resistance tab: methods, Holtrop options, results table and chart.</summary>
    public sealed class ResistancePage : Panel
    {
        private const double KnotToMs = 1852.0 / 3600.0;

        private sealed class Column
        {
            public string Backend;
            public string Header;
            public string ShortName;
            public bool Power;
            public Color Color;
        }

        private static readonly Dictionary<string, string> Names = new Dictionary<string, string>
        {
            { "PY-HOLTROP-1984", "Holtrop 1984" },
            { "PY-MICHELL-PLUS-ITTC57-PARTIAL", "Michell + ITTC-57 (partial)" },
            { "PY-MICHELL", "Michell wave only" },
            { "PY-ITTC57-FRICTION-ONLY", "ITTC-57 friction only" },
        };

        private static readonly Dictionary<string, Color> Colours = new Dictionary<string, Color>
        {
            { "PY-HOLTROP-1984", Color.FromArgb(24, 95, 165) },
            { "PY-MICHELL-PLUS-ITTC57-PARTIAL", Color.FromArgb(216, 90, 48) },
            { "PY-MICHELL", Color.FromArgb(127, 119, 221) },
            { "PY-ITTC57-FRICTION-ONLY", Color.FromArgb(136, 135, 128) },
        };

        private readonly Func<RhinoDoc> _doc;
        private readonly CheckBox _holtrop = new CheckBox { Text = "Holtrop 1984 (total resistance, empirical)" };
        private readonly CheckBox _michell = new CheckBox { Text = "Michell thin-ship wave + ITTC-57 friction (partial, slower)" };
        private readonly CheckBox _friction = new CheckBox { Text = "ITTC-57 friction only" };
        private readonly DropDown _pieces = Ui.Choices(
            "equivalent_width", "Add their widths (equivalent width, screening only)",
            "reject", "Stop with an error");
        private readonly NumberBox _padding = new NumberBox(60);
        private readonly DropDown _entrance = Ui.Choices(
            "measured", "Measured from the hull (forward 10% of LWL)",
            "regression", "Holtrop's regression formula",
            "given", "Given value");
        private readonly NumberBox _angle = new NumberBox(60);
        private readonly DropDown _lcb = Ui.Choices(
            "lwl_midpoint", "Middle of LWL (Holtrop)",
            "amidships", "Amidships, between AP and FP");
        private readonly NumberBox _ap = new NumberBox(90);
        private readonly NumberBox _fp = new NumberBox(90);
        private readonly Label _apUnit = Ui.Unit("");
        private readonly DropDown _stern = Ui.Choices(
            "0", "Normal sections (Cstern 0)",
            "-10", "V-shaped sections (Cstern −10)",
            "10", "U-shaped sections, Hogner stern (Cstern +10)",
            "-25", "Pram with gondola (Cstern −25)");
        private readonly NumberBox _roughness = new NumberBox(60);
        private readonly CheckBox _transom = new CheckBox { Text = "Count the immersed transom face in the friction area" };
        private readonly DropDown _quantity = Ui.Choices("force", "Resistance (kN)", "power", "Effective power PE (kW)");
        private readonly GridView _table = new GridView { Height = 260, ShowHeader = true };
        private readonly LineChart _chart = new LineChart();
        private readonly TextArea _notes = new TextArea { ReadOnly = true, Wrap = true, Height = 110 };
        private readonly Label _caption = new Label { Wrap = WrapMode.Word };
        private Outcome _shown;
        private bool _loading;

        public ResistancePage(Func<RhinoDoc> doc)
        {
            _doc = doc;
            _holtrop.CheckedChanged += (sender, e) => Change(d => d.UseHoltrop = _holtrop.Checked == true);
            _michell.CheckedChanged += (sender, e) => Change(d => d.UseMichell = _michell.Checked == true);
            _friction.CheckedChanged += (sender, e) => Change(d => d.UseFriction = _friction.Checked == true);
            _pieces.SelectedIndexChanged += (sender, e) => Change(d => d.MichellMultiInterval = _pieces.SelectedKey ?? "equivalent_width");
            _padding.Committed += (sender, e) => Change(d => { if (_padding.Value.HasValue) d.MichellBowPaddingPercent = _padding.Value.Value; });
            _entrance.SelectedIndexChanged += (sender, e) => Change(d => d.EntrancePolicy = _entrance.SelectedKey ?? "measured");
            _angle.Committed += (sender, e) => Change(d => d.EntranceAngle = _angle.Value);
            _lcb.SelectedIndexChanged += (sender, e) => Change(d => d.LcbReference = _lcb.SelectedKey ?? "lwl_midpoint");
            _ap.Committed += (sender, e) => Change(d => d.ApX = _ap.Value);
            _fp.Committed += (sender, e) => Change(d => d.FpX = _fp.Value);
            _stern.SelectedIndexChanged += (sender, e) => Change(d =>
            {
                double value;
                if (HullDefinition.TryParse(_stern.SelectedKey, out value)) d.SternParameter = value;
            });
            _roughness.Committed += (sender, e) => Change(d => { if (_roughness.Value.HasValue) d.RoughnessMicrons = _roughness.Value.Value; });
            _transom.CheckedChanged += (sender, e) => Change(d => d.FrictionIncludesTransom = _transom.Checked == true);
            _quantity.SelectedIndex = 0;
            _quantity.SelectedIndexChanged += (sender, e) => Show(_shown);

            var run = new Button { Text = "Run resistance" };
            run.Click += (sender, e) =>
            {
                var current = _doc();
                if (current != null) Session.RunResistance(current);
            };
            var save = new Button { Text = "Save table as CSV…" };
            save.Click += (sender, e) => SaveCsv();
            _table.Columns.Add(new GridColumn { HeaderText = "Speed (kn)", DataCell = new TextBoxCell(0), Width = 70 });

            var layout = new DynamicLayout { Padding = new Padding(8), Spacing = new Size(6, 6) };
            layout.AddRow(Ui.Heading("Methods"));
            layout.AddRow(_holtrop);
            layout.AddRow(_michell);
            layout.AddRow(_friction);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("Michell: a station cuts the hull in several underwater pieces"), _pieces, null);
            layout.AddRow(Ui.Field("Michell: grid extension ahead of the bow"), _padding, Ui.Unit("% LWL"), null);
            layout.EndVertical();
            layout.AddRow(Ui.Heading("Holtrop options"));
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("Half angle of entrance"), _entrance, _angle, Ui.Unit("°"), null);
            layout.AddRow(Ui.Field("LCB measured from"), _lcb, null);
            layout.EndVertical();
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("AP x"), _ap, Ui.Field("FP x"), _fp, _apUnit, null);
            layout.EndVertical();
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("Afterbody form"), _stern, null);
            layout.AddRow(Ui.Field("Hull roughness"), _roughness, Ui.Unit("µm"), null);
            layout.EndVertical();
            layout.AddRow(_transom);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(run, save, null);
            layout.EndVertical();
            layout.AddRow(_caption);
            layout.AddRow(_table);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("Chart"), _quantity, null);
            layout.EndVertical();
            layout.AddRow(_chart);
            layout.AddRow(Ui.Heading("Notes and model limits"));
            layout.AddRow(_notes);
            layout.Add(null, yscale: true);
            Content = Ui.Scroll(layout);
            _chart.Clear("Run resistance to see the curves.");
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
                _holtrop.Checked = d.UseHoltrop;
                _michell.Checked = d.UseMichell;
                _friction.Checked = d.UseFriction;
                _pieces.SelectedKey = d.MichellMultiInterval;
                _pieces.Enabled = d.UseMichell;
                _padding.Show(d.MichellBowPaddingPercent, "0.##");
                _padding.Enabled = d.UseMichell;
                _entrance.SelectedKey = d.EntrancePolicy;
                _angle.Show(d.EntranceAngle);
                _angle.Enabled = d.EntrancePolicy == "given";
                _lcb.SelectedKey = d.LcbReference;
                _ap.Show(d.ApX);
                _fp.Show(d.FpX);
                _ap.Enabled = _fp.Enabled = d.LcbReference == "amidships";
                _apUnit.Text = Session.UnitName(doc) + " (model x)";
                _stern.SelectedKey = d.SternParameter.ToString("0", CultureInfo.InvariantCulture);
                _roughness.Show(d.RoughnessMicrons, "0.#");
                _transom.Checked = d.FrictionIncludesTransom;
            }
            finally
            {
                _loading = false;
            }
        }

        /// <summary>Table columns (forces and powers) or chart series; the chart leaves out the wave-only component.</summary>
        private List<Column> Columns(Outcome outcome, bool power, bool forChart)
        {
            var columns = new List<Column>();
            var results = J.List(outcome.Result, "results");
            if (results == null) return columns;
            foreach (var item in results)
            {
                var result = item as Dictionary<string, object>;
                var backend = J.Str(result, "backend");
                if (backend == null || (forChart && backend == "PY-MICHELL")) continue;
                string name;
                if (!Names.TryGetValue(backend, out name)) name = backend;
                Color colour;
                if (!Colours.TryGetValue(backend, out colour)) colour = Colors.Gray;
                columns.Add(new Column { Backend = backend, ShortName = name, Power = false, Color = colour,
                                         Header = name + (backend == "PY-ITTC57-FRICTION-ONLY" ? " RF (kN)" : backend == "PY-MICHELL" ? " RW (kN)" : " R (kN)") });
                if (power && result.ContainsKey("power_W"))
                    columns.Add(new Column { Backend = backend, ShortName = name, Power = true, Color = colour, Header = name + " P (kW)" });
            }
            return columns;
        }

        /// <summary>One text per speed: each method's notes at that speed ("Holtrop: Fn 0.85 above 0.8 ...").</summary>
        private static string[] SpeedNotes(Outcome outcome, int count)
        {
            var notes = new List<string>[count];
            for (var i = 0; i < count; i++) notes[i] = new List<string>();
            var results = J.List(outcome.Result, "results");
            if (results == null) return notes.Select(n => "").ToArray();
            foreach (var item in results)
            {
                var result = item as Dictionary<string, object>;
                var perSpeed = J.List(result, "speed_notes");
                if (perSpeed == null) continue;
                string name;
                if (!Names.TryGetValue(J.Str(result, "backend") ?? "", out name)) name = J.Str(result, "backend");
                for (var i = 0; i < count && i < perSpeed.Count; i++)
                {
                    var texts = perSpeed[i] as IList;
                    if (texts == null) continue;
                    foreach (var text in texts)
                    {
                        var line = name + ": " + text;
                        if (!notes[i].Contains(line)) notes[i].Add(line);
                    }
                }
            }
            return notes.Select(n => string.Join("; ", n)).ToArray();
        }

        private static double[] Values(Outcome outcome, string backend, bool power)
        {
            var results = J.List(outcome.Result, "results");
            if (results == null) return new double[0];
            foreach (var item in results)
            {
                var result = item as Dictionary<string, object>;
                if (J.Str(result, "backend") != backend) continue;
                var values = J.Nums(result, power ? "power_W" : "values");
                return values.Select(v => v / 1000.0).ToArray();
            }
            return new double[0];
        }

        public void Show(Outcome outcome)
        {
            _shown = outcome;
            if (outcome == null || J.List(outcome.Result, "results") == null)
            {
                _table.DataStore = new List<string[]>();
                _caption.Text = "No results yet. Set the hull and the condition, then run.";
                _chart.Clear("Run resistance to see the curves.");
                _notes.Text = "";
                return;
            }
            var speeds = J.Nums(J.Obj(outcome.Result, "conditions"), "speeds_kn");
            var h = outcome.Hydrostatics;
            var lwl = J.Num(h, "lwl_m");
            var gravity = J.Num(J.Obj(outcome.Result, "conditions"), "gravity_m_s2");
            if (double.IsNaN(gravity)) gravity = 9.80665;
            var columns = Columns(outcome, true, false);

            _table.Columns.Clear();
            _table.Columns.Add(new GridColumn { HeaderText = "Speed (kn)", DataCell = new TextBoxCell(0), Width = 70 });
            _table.Columns.Add(new GridColumn { HeaderText = "Fn", DataCell = new TextBoxCell(1), Width = 50 });
            for (var c = 0; c < columns.Count; c++)
                _table.Columns.Add(new GridColumn { HeaderText = columns[c].Header, DataCell = new TextBoxCell(c + 2),
                                                    Width = Math.Max(110, 7 * columns[c].Header.Length) });
            _table.Columns.Add(new GridColumn { HeaderText = "Notes", DataCell = new TextBoxCell(columns.Count + 2), Width = 320 });
            var data = columns.Select(c => Values(outcome, c.Backend, c.Power)).ToList();
            var speedNotes = SpeedNotes(outcome, speeds.Length);
            var rows = new List<string[]>();
            for (var i = 0; i < speeds.Length; i++)
            {
                var row = new string[columns.Count + 3];
                row[0] = speeds[i].ToString("0.###", CultureInfo.InvariantCulture);
                row[1] = lwl > 0 ? (speeds[i] * KnotToMs / Math.Sqrt(gravity * lwl)).ToString("0.000", CultureInfo.InvariantCulture) : "—";
                for (var c = 0; c < columns.Count; c++)
                    row[c + 2] = i < data[c].Length ? data[c][i].ToString(columns[c].Power ? "0.0" : "0.00", CultureInfo.InvariantCulture) : "—";
                row[columns.Count + 2] = speedNotes[i];
                rows.Add(row);
            }
            _table.DataStore = rows;

            var power = _quantity.SelectedKey == "power";
            var series = Columns(outcome, power, true).Where(c => c.Power == power)
                .Select(c => new LineChart.Series { Name = c.ShortName, X = speeds, Y = Values(outcome, c.Backend, power), Color = c.Color });
            _chart.Show("Speed (kn)", power ? "PE (kW)" : "Resistance (kN)", series.ToList());

            var condition = J.Obj(outcome.Result, "conditions");
            _caption.Text = string.Format(CultureInfo.InvariantCulture,
                "Python engine results, {0:HH:mm:ss}. z = {1:0.####} m, ∇ = {2:0.##} m³, LWL = {3:0.###} m, ρ = {4:0.#} kg/m³, ν = {5:0.#####E+0} m²/s. Labels name each model; only Holtrop is a total resistance.",
                outcome.Time, J.Num(h, "z_wl_m"), J.Num(h, "volume_m3"), lwl, J.Num(condition, "rho_kg_m3"), J.Num(condition, "nu_m2_s"));

            var notes = new StringBuilder();
            foreach (var warning in outcome.Warnings) notes.AppendLine("• " + warning);
            var marked = speedNotes.Count(n => n.Length > 0);
            if (marked > 0)
                notes.AppendLine("• " + marked + " of " + speeds.Length + " speeds carry a note in the table's Notes column " +
                                 "(outside a method's published range, or numerics not converged); their values are kept.");
            var results = J.List(outcome.Result, "results");
            foreach (var item in results)
            {
                var result = item as Dictionary<string, object>;
                string name;
                if (!Names.TryGetValue(J.Str(result, "backend") ?? "", out name)) name = J.Str(result, "backend");
                foreach (var warning in J.Strs(result, "warnings")) notes.AppendLine("• " + name + ": " + warning);
            }
            _notes.Text = notes.ToString().TrimEnd();
        }

        private void SaveCsv()
        {
            var outcome = _shown;
            var rows = _table.DataStore as List<string[]>;
            if (outcome == null || rows == null || rows.Count == 0)
            {
                Session.Say("Run resistance before saving the table.", true);
                return;
            }
            var doc = _doc();
            var dialog = new SaveFileDialog { Title = "Save resistance table" };
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
            dialog.FileName = "resistance_" + DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture) + ".csv";
            if (dialog.ShowDialog(this) != DialogResult.Ok) return;
            var csv = new StringBuilder();
            csv.AppendLine(string.Join(",", _table.Columns.Select(c => Csv(c.HeaderText, true))));
            foreach (var row in rows) csv.AppendLine(string.Join(",", row.Select(cell => Csv(cell, false))));
            var path = dialog.FileName;
            if (!path.EndsWith(".csv", StringComparison.OrdinalIgnoreCase)) path += ".csv";
            File.WriteAllText(path, csv.ToString(), new UTF8Encoding(true));
            Session.Say("table saved: " + path, false);
        }

        /// <summary>A CSV field, quoted when it holds a separator, a quote or text that must stay text.</summary>
        private static string Csv(string text, bool quote)
        {
            text = text ?? "";
            if (quote || text.IndexOfAny(new[] { ',', '"', ';', '\n', '\r' }) >= 0)
                return "\"" + text.Replace("\"", "\"\"") + "\"";
            return text;
        }
    }
}

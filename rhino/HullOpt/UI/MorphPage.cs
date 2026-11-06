using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Eto.Drawing;
using Eto.Forms;
using Rhino;

namespace HullOpt
{
    /// <summary>Morph tab: bend the hull with the shape modes, check the candidate, build it in Rhino.</summary>
    public sealed class MorphPage : Panel
    {
        private sealed class Mode
        {
            public string Name;
            public string Label;
            public string Unit;
            public string Hint;
            public NumberBox Box;
        }

        private sealed class Limit
        {
            public string Label;
            public string Unit;
            public Func<HullDefinition, double> Get;
            public Action<HullDefinition, double> Set;
            public NumberBox Box;
        }

        private static readonly Dictionary<string, string> Methods = new Dictionary<string, string>
        {
            { "PY-HOLTROP-1984", "Holtrop 1984" },
            { "PY-MICHELL-PLUS-ITTC57-PARTIAL", "Michell + ITTC-57" },
            { "PY-MICHELL", "Michell wave only" },
            { "PY-ITTC57-FRICTION-ONLY", "ITTC-57 friction" },
        };

        private static readonly Dictionary<string, string> GateNames = new Dictionary<string, string>
        {
            { "draft_change_m", "Draft change" },
            { "lcb_shift_percent_lwl", "LCB shift" },
            { "lwl_change_percent", "LWL change" },
            { "bwl_change_percent", "BWL change" },
            { "normal_deviation_m", "Surface moved (normal)" },
            { "upper_edge_m", "Upper edge moved" },
            { "slope_dy_dx", "Mode slope dy/dx" },
            { "slope_dy_dz", "Mode slope dy/dz" },
        };

        private readonly Func<RhinoDoc> _doc;
        private readonly List<Mode> _modes = new List<Mode>
        {
            new Mode { Name = "lack_aft", Label = "Aft body fore/aft", Unit = "m", Hint = "+ moves aft-half sections forward" },
            new Mode { Name = "lack_fwd", Label = "Fore body fore/aft", Unit = "m", Hint = "+ moves forward-half sections forward" },
            new Mode { Name = "bow_fine", Label = "Finer bow", Unit = "fraction", Hint = "+ narrows the forebody below the sheer" },
            new Mode { Name = "bow_tip", Label = "Finer bow tip", Unit = "fraction", Hint = "+ narrows only the bow tip" },
            new Mode { Name = "stern_narrow", Label = "Narrower stern", Unit = "fraction", Hint = "+ narrows the afterbody toward the transom" },
            new Mode { Name = "stern_lift", Label = "Lift aft bottom", Unit = "m", Hint = "+ raises the aft bottom toward the transom" },
            new Mode { Name = "bilge_full", Label = "Fuller bilge", Unit = "fraction", Hint = "+ fills midbody sections outward; beam kept" },
            new Mode { Name = "deadrise", Label = "Flatter floor", Unit = "m", Hint = "+ lowers the outer bottom (less deadrise)" },
        };
        private readonly List<Limit> _limits = new List<Limit>
        {
            new Limit { Label = "Draft change", Unit = "% of T", Get = d => d.LimitDraftPercentT, Set = (d, v) => d.LimitDraftPercentT = v },
            new Limit { Label = "LCB shift", Unit = "% LWL", Get = d => d.LimitLcbPercentLwl, Set = (d, v) => d.LimitLcbPercentLwl = v },
            new Limit { Label = "LWL change", Unit = "%", Get = d => d.LimitLengthPercent, Set = (d, v) => d.LimitLengthPercent = v },
            new Limit { Label = "BWL change", Unit = "%", Get = d => d.LimitBeamPercent, Set = (d, v) => d.LimitBeamPercent = v },
            new Limit { Label = "Surface moved (normal)", Unit = "% LWL", Get = d => d.LimitSurfacePercentLwl, Set = (d, v) => d.LimitSurfacePercentLwl = v },
            new Limit { Label = "Upper edge moved", Unit = "% LWL", Get = d => d.LimitUpperEdgePercentLwl, Set = (d, v) => d.LimitUpperEdgePercentLwl = v },
            new Limit { Label = "Mode slope dy/dx", Unit = "", Get = d => d.LimitSlopeDyDx, Set = (d, v) => d.LimitSlopeDyDx = v },
            new Limit { Label = "Mode slope dy/dz", Unit = "", Get = d => d.LimitSlopeDyDz, Set = (d, v) => d.LimitSlopeDyDz = v },
        };
        private readonly CheckBox _resistance = new CheckBox { Text = "Compare resistance (methods of the Resistance tab, speeds of the Condition tab)" };
        private readonly Label _caption = new Label { Wrap = WrapMode.Word };
        private readonly GridView _changes = new GridView { Height = 250, ShowHeader = true };
        private readonly GridView _gates = new GridView { Height = 250, ShowHeader = true };
        private readonly GridView _drag = new GridView { Height = 180, ShowHeader = true };
        private readonly TextArea _notes = new TextArea { ReadOnly = true, Wrap = true, Height = 110 };
        private bool _loading;

        public MorphPage(Func<RhinoDoc> doc)
        {
            _doc = doc;
            foreach (var mode in _modes)
            {
                var m = mode;
                m.Box = new NumberBox(70);
                m.Box.Committed += (sender, e) => Change(d =>
                {
                    if (d.MorphAmplitudes == null) d.MorphAmplitudes = new Dictionary<string, double>();
                    var value = m.Box.Value;
                    if (value.HasValue && value.Value != 0) d.MorphAmplitudes[m.Name] = value.Value;
                    else d.MorphAmplitudes.Remove(m.Name);
                });
            }
            foreach (var limit in _limits)
            {
                var l = limit;
                l.Box = new NumberBox(70);
                l.Box.Committed += (sender, e) => Change(d => { if (l.Box.Value.HasValue) l.Set(d, l.Box.Value.Value); });
            }
            _resistance.CheckedChanged += (sender, e) => Change(d => d.MorphResistance = _resistance.Checked == true);

            var evaluate = new Button { Text = "Evaluate" };
            evaluate.Click += (sender, e) =>
            {
                var current = _doc();
                if (current != null) Session.RunMorph(current);
            };
            var reset = new Button { Text = "Reset to baseline" };
            reset.Click += (sender, e) =>
            {
                Change(d => d.MorphAmplitudes = new Dictionary<string, double>());
                Refresh();
            };
            var build = new Button { Text = "Build candidate in Rhino" };
            build.Click += (sender, e) =>
            {
                var current = _doc();
                if (current != null) Session.BuildCandidate(current);
            };

            _changes.Columns.Add(Column("Quantity", 0, 170));
            _changes.Columns.Add(Column("Baseline", 1, 90));
            _changes.Columns.Add(Column("Candidate", 2, 90));
            _changes.Columns.Add(Column("Change", 3, 90));
            _changes.Columns.Add(Column("Unit", 4, 60));
            _gates.Columns.Add(Column("Check", 0, 170));
            _gates.Columns.Add(Column("Value", 1, 90));
            _gates.Columns.Add(Column("Limit", 2, 90));
            _gates.Columns.Add(Column("Unit", 3, 60));
            _gates.Columns.Add(Column("Result", 4, 60));
            _drag.Columns.Add(Column("Speed (kn)", 0, 70));
            _drag.Columns.Add(Column("Method", 1, 130));
            _drag.Columns.Add(Column("Baseline (kN)", 2, 95));
            _drag.Columns.Add(Column("Candidate (kN)", 3, 95));
            _drag.Columns.Add(Column("Ratio", 4, 70));
            _drag.Columns.Add(Column("Notes", 5, 300));

            var layout = new DynamicLayout { Padding = new Padding(8), Spacing = new Size(6, 6) };
            layout.AddRow(Ui.Heading("Shape modes"));
            layout.AddRow(Ui.Note("Bends the hull with eight smooth modes; each candidate floats at the hull's own displacement. " +
                                  "The model's surfaces never change: a built candidate goes on layer HullOpt::Candidates. " +
                                  "Only the hull is bent; appendages are left out."));
            var table = new TableLayout { Spacing = new Size(6, 4) };
            foreach (var m in _modes)
                table.Rows.Add(new TableRow(new TableCell(Ui.Field(m.Label)), new TableCell(m.Box), new TableCell(Ui.Unit(m.Unit)),
                                            new TableCell(Ui.Hint(m.Hint), true)));
            layout.AddRow(table);
            layout.AddRow(_resistance);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(evaluate, reset, build, null);
            layout.EndVertical();
            layout.AddRow(_caption);
            layout.AddRow(_changes);
            layout.AddRow(Ui.Heading("Checks"));
            layout.AddRow(_gates);
            layout.AddRow(Ui.Heading("Resistance"));
            layout.AddRow(_drag);
            layout.AddRow(Ui.Heading("Limits"));
            layout.AddRow(Ui.Note("Relative to the hull, so they suit any size. Defaults: the first test hull's study limits."));
            var limits = new TableLayout { Spacing = new Size(6, 4) };
            foreach (var l in _limits)
                limits.Rows.Add(new TableRow(new TableCell(Ui.Field(l.Label)), new TableCell(l.Box), new TableCell(Ui.Unit(l.Unit), true)));
            layout.AddRow(limits);
            layout.AddRow(Ui.Heading("Notes"));
            layout.AddRow(_notes);
            layout.Add(null, yscale: true);
            Content = Ui.Scroll(layout);
            Show(null);
        }

        private static GridColumn Column(string header, int index, int width)
        {
            return new GridColumn { HeaderText = header, DataCell = new TextBoxCell(index), Width = width };
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
                foreach (var m in _modes)
                {
                    double value;
                    m.Box.Show(d.MorphAmplitudes != null && d.MorphAmplitudes.TryGetValue(m.Name, out value) ? value : 0.0);
                }
                foreach (var l in _limits) l.Box.Show(l.Get(d), "0.####");
                _resistance.Checked = d.MorphResistance;
            }
            finally
            {
                _loading = false;
            }
        }

        private static string F(double value, string format)
        {
            return double.IsNaN(value) ? "—" : value.ToString(format, CultureInfo.InvariantCulture);
        }

        public void Show(Outcome outcome)
        {
            var result = outcome == null ? null : outcome.Result;
            if (result == null)
            {
                _caption.Text = "No candidate yet. Set amplitudes, then Evaluate.";
                _changes.DataStore = new List<string[]>();
                _gates.DataStore = new List<string[]>();
                _drag.DataStore = new List<string[]>();
                _notes.Text = "";
                return;
            }
            var b = J.Obj(J.Obj(result, "baseline"), "hydrostatics");
            var c = J.Obj(result, "hydrostatics");
            var rows = new List<string[]>();
            Action<string, string, double, string, string> row = (name, key, scale, format, unit) =>
            {
                double v0 = J.Num(b, key) * scale, v1 = J.Num(c, key) * scale;
                rows.Add(new[] { name, F(v0, format), F(v1, format), F(v1 - v0, format), unit });
            };
            var z0 = J.Num(J.Obj(result, "baseline"), "z_wl_m");
            var z1 = J.Num(result, "z_wl_m");
            rows.Add(new[] { "Waterline height z (change in mm)", F(z0, "0.####"), F(z1, "0.####"), F(1000.0 * (z1 - z0), "0.0"), "m" });
            row("Displacement Δ", "displacement_kg", 0.001, "0.###", "t");
            row("Length on waterline LWL", "lwl_m", 1.0, "0.####", "m");
            row("Beam on waterline BWL", "bwl_m", 1.0, "0.####", "m");
            row("Draft T (to lowest point)", "draft_m", 1.0, "0.####", "m");
            row("LCB from LWL midpoint", "lcb_percent_lwl", 1.0, "0.###", "% LWL");
            row("Wetted surface S", "wetted_area_m2", 1.0, "0.##", "m²");
            row("Immersed transom area", "transom_area_m2", 1.0, "0.###", "m²");
            row("Prismatic coefficient Cp", "cp", 1.0, "0.0000", "");
            row("Waterplane coefficient Cwp", "cwp", 1.0, "0.0000", "");
            row("Half angle of entrance iE", "entrance_half_angle_deg", 1.0, "0.##", "°");
            _changes.DataStore = rows;

            var gates = new List<string[]>();
            var gateList = J.List(result, "gates");
            if (gateList != null)
            {
                foreach (var item in gateList)
                {
                    var g = item as Dictionary<string, object>;
                    if (g == null) continue;
                    var key = J.Str(g, "name") ?? "";
                    string label;
                    if (!GateNames.TryGetValue(key, out label)) label = key;
                    var ok = true.Equals(g.ContainsKey("pass") ? g["pass"] : null);
                    gates.Add(new[] { label, F(J.Num(g, "value"), "0.#####"), F(J.Num(g, "limit"), "0.#####"), J.Str(g, "unit") ?? "",
                                      ok ? "pass" : "FAIL" });
                }
            }
            _gates.DataStore = gates;

            var drag = new List<string[]>();
            var summary = new List<string>();
            var rowsList = J.List(result, "resistance");
            if (rowsList != null)
            {
                foreach (var item in rowsList)
                {
                    var r = item as Dictionary<string, object>;
                    if (r == null || J.Str(r, "units") != "N") continue;
                    string method;
                    if (!Methods.TryGetValue(J.Str(r, "backend") ?? "", out method)) method = J.Str(r, "backend");
                    double[] speeds = J.Nums(r, "speeds_kn"), r0 = J.Nums(r, "baseline"), r1 = J.Nums(r, "candidate");
                    var notesBySpeed = J.List(r, "speed_notes");
                    for (var i = 0; i < speeds.Length && i < r0.Length && i < r1.Length; i++)
                    {
                        var texts = notesBySpeed != null && i < notesBySpeed.Count ? notesBySpeed[i] as IList : null;
                        var note = texts == null ? "" : string.Join("; ", texts.Cast<object>().Select(t => Convert.ToString(t, CultureInfo.InvariantCulture)));
                        drag.Add(new[] { F(speeds[i], "0.###"), method, F(r0[i] / 1000.0, "0.00"), F(r1[i] / 1000.0, "0.00"),
                                         F(r0[i] != 0 ? r1[i] / r0[i] : double.NaN, "0.0000"), note });
                    }
                    summary.Add(method + " mean R/R₀ " + F(J.Num(r, "mean_ratio"), "0.0000") + " over " + speeds.Length +
                                (speeds.Length == 1 ? " speed" : " speeds"));
                }
            }
            _drag.DataStore = drag;

            var feasible = true.Equals(result.ContainsKey("feasible") ? result["feasible"] : null);
            _caption.Text = string.Format(CultureInfo.InvariantCulture, "Python engine result, {0:HH:mm:ss}. {1}{2}",
                outcome.Time, feasible ? "All checks pass." : "Some checks fail (see Checks).",
                summary.Count > 0 ? " " + string.Join("; ", summary) + "." : "");

            var notes = new List<string>(outcome.Warnings);
            notes.AddRange(J.Strs(result, "notes"));
            var fairness = J.Obj(result, "fairness");
            var where = fairness == null ? null : J.List(fairness, "dk_where");
            if (where != null && where.Count > 0)
                notes.Add("Largest curvature change on line " + where[0] + ".");
            notes.Add("Fairness: G1 no new inflections, G2 curvature change and ratio, G3 fairness energy, G5 bilge radius; limits scale with the hull.");
            notes.Add("Python results; no candidate is validated by Maxsurf, tank tests or CFD.");
            _notes.Text = string.Join(Environment.NewLine, notes);
        }
    }
}

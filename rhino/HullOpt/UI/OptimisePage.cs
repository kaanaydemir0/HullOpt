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
    /// Optimise tab: searches the shape modes for the smallest resistance or brake power ratio J within
    /// the Morph tab's limits and the fairness gates. J is equal-weighted over the Condition speeds,
    /// weighted by an operating profile, or taken at the service speed only, as the user chooses.
    /// </summary>
    public sealed class OptimisePage : Panel
    {
        private sealed class Mode
        {
            public string Name;
            public string Label;
            public string Unit;
            public string Auto;
            public CheckBox Vary;
            public NumberBox Min;
            public NumberBox Max;
        }

        private readonly Func<RhinoDoc> _doc;
        // Labels as on the Morph tab; "Auto" repeats the engine's hull-relative default bounds.
        private readonly List<Mode> _modes = new List<Mode>
        {
            new Mode { Name = "lack_aft", Label = "Aft body fore/aft", Unit = "m", Auto = "±0.05% LWL" },
            new Mode { Name = "lack_fwd", Label = "Fore body fore/aft", Unit = "m", Auto = "±0.05% LWL" },
            new Mode { Name = "bow_fine", Label = "Finer bow", Unit = "fraction", Auto = "−0.06 … 0.12" },
            new Mode { Name = "bow_tip", Label = "Finer bow tip", Unit = "fraction", Auto = "−0.05 … 0.10" },
            new Mode { Name = "stern_narrow", Label = "Narrower stern", Unit = "fraction", Auto = "−0.03 … 0.06" },
            new Mode { Name = "stern_lift", Label = "Lift aft bottom", Unit = "m", Auto = "−3.6% … 7.2% of T" },
            new Mode { Name = "bilge_full", Label = "Fuller bilge", Unit = "fraction", Auto = "−0.125 … 0.25" },
            new Mode { Name = "deadrise", Label = "Flatter floor", Unit = "m", Auto = "−6% … 12% of T" },
        };
        private readonly DropDown _quantity = Ui.Choices(
            "holtrop", "Holtrop 1984 total resistance R_T",
            "michell", "Michell wave + ITTC-57 friction (partial; relative only)",
            "brake_power", "Brake power P_B (Holtrop 1984 + Propulsion tab)");
        private readonly DropDown _weighting = Ui.Choices(
            "equal", "Equal weight: mean over the Condition speeds",
            "profile", "Operating profile: weighted by time share",
            "service", "Service speed only");
        private readonly CommitBox _profile = new CommitBox(230);
        private readonly NumberBox _service = new NumberBox(60);
        private readonly Label _objectiveNote = Ui.Note("");
        private readonly NumberBox _population = new NumberBox(60);
        private readonly NumberBox _evaluations = new NumberBox(70);
        private readonly NumberBox _seed = new NumberBox(80);
        private readonly DropDown _start = Ui.Choices(
            "uniform", "Spread over the bounds",
            "baseline", "Around the baseline");
        private readonly Label _live = new Label { Wrap = WrapMode.Word };
        private readonly Label _caption = new Label { Wrap = WrapMode.Word };
        private readonly LineChart _chart = new LineChart();
        private readonly GridView _candidates = new GridView { Height = 230, ShowHeader = true };
        private readonly GridView _speeds = new GridView { Height = 170, ShowHeader = true };
        private readonly TextArea _notes = new TextArea { ReadOnly = true, Wrap = true, Height = 130 };
        private readonly List<double> _liveX = new List<double>();
        private readonly List<double> _liveY = new List<double>();
        private DateTime _lastDraw = DateTime.MinValue;
        private Outcome _shown;
        private List<Dictionary<string, object>> _rows = new List<Dictionary<string, object>>();
        private List<string> _varied = new List<string>();
        private bool _loading;

        public OptimisePage(Func<RhinoDoc> doc)
        {
            _doc = doc;
            _quantity.Width = 330;
            _weighting.Width = 330;
            _start.Width = 180;
            // Ratios near 1 need their own scale; the tables scroll inside instead of widening the tab.
            _chart.FromZero = false;
            _candidates.Width = 200;
            _speeds.Width = 200;
            _service.PlaceholderText = "design";
            _profile.PlaceholderText = "12:60; 14:20; 16:20";
            foreach (var mode in _modes)
            {
                var m = mode;
                m.Vary = new CheckBox { Text = m.Label, Width = 160 };
                m.Min = new NumberBox(70) { PlaceholderText = "auto" };
                m.Max = new NumberBox(70) { PlaceholderText = "auto" };
                m.Vary.CheckedChanged += (sender, e) => Change(d =>
                {
                    if (d.OptimiseModes == null) d.OptimiseModes = new List<string>();
                    d.OptimiseModes.Remove(m.Name);
                    if (m.Vary.Checked == true) d.OptimiseModes.Add(m.Name);
                });
                m.Min.Committed += (sender, e) => Change(d => SetBound(d.OptimiseMin = d.OptimiseMin ?? new Dictionary<string, double>(), m.Name, m.Min.Value));
                m.Max.Committed += (sender, e) => Change(d => SetBound(d.OptimiseMax = d.OptimiseMax ?? new Dictionary<string, double>(), m.Name, m.Max.Value));
            }
            _quantity.SelectedIndexChanged += (sender, e) => Change(d => d.OptimiseQuantity = _quantity.SelectedKey ?? "holtrop");
            _weighting.SelectedIndexChanged += (sender, e) => Change(d => d.OptimiseWeighting = _weighting.SelectedKey ?? "equal");
            _profile.Committed += (sender, e) => Change(d => d.OptimiseProfile = (_profile.Text ?? "").Trim());
            _service.Committed += (sender, e) => Change(d => d.ServiceSpeed = _service.Value);
            _population.Committed += (sender, e) => Change(d => { if (_population.Value.HasValue) d.OptimisePopulation = (int)Math.Round(_population.Value.Value); });
            _evaluations.Committed += (sender, e) => Change(d => { if (_evaluations.Value.HasValue) d.OptimiseEvaluations = (int)Math.Round(_evaluations.Value.Value); });
            _seed.Committed += (sender, e) => Change(d => { if (_seed.Value.HasValue) d.OptimiseSeed = (int)Math.Round(_seed.Value.Value); });
            _start.SelectedIndexChanged += (sender, e) => Change(d => d.OptimiseStart = _start.SelectedKey ?? "uniform");
            _candidates.SelectionChanged += (sender, e) => ShowSpeeds();

            var run = new Button { Text = "Run optimisation" };
            run.Click += (sender, e) =>
            {
                var current = _doc();
                if (current != null) Session.RunOptimise(current);
            };
            var stop = new Button { Text = "Stop" };
            stop.Click += (sender, e) => Session.Cancel();
            var apply = new Button { Text = "Evaluate on the Morph tab" };
            apply.Click += (sender, e) => UseSelected(true);
            var build = new Button { Text = "Build in Rhino" };
            build.Click += (sender, e) => UseSelected(false);
            var save = new Button { Text = "Save candidates as CSV…" };
            save.Click += (sender, e) => SaveCsv();

            _speeds.Columns.Add(Column("Speed (kn)", 0, 70));
            _speeds.Columns.Add(Column("Weight", 1, 60));
            _speeds.Columns.Add(Column("Baseline", 2, 90));
            _speeds.Columns.Add(Column("Candidate", 3, 90));
            _speeds.Columns.Add(Column("Ratio", 4, 70));
            _speeds.Columns.Add(Column("Notes", 5, 300));

            var layout = new DynamicLayout { Padding = new Padding(8), Spacing = new Size(6, 6) };
            layout.AddRow(Ui.Heading("Objective"));
            layout.AddRow(Ui.Note("The search looks for the smallest J, the chosen quantity as a ratio to the baseline hull (J = 1). " +
                                  "Each candidate floats at the hull's own displacement; one that fails a Morph-tab limit or a fairness " +
                                  "gate never wins, whatever its resistance."));
            layout.AddRow(Row(Name("Minimise"), _quantity));
            layout.AddRow(Row(Name("Speeds"), _weighting));
            layout.AddRow(Row(Name("Operating profile"), _profile, Ui.Unit("speed:share; …")));
            layout.AddRow(Row(Name("Service speed"), _service, Ui.Unit("kn (blank: the design speed)")));
            layout.AddRow(_objectiveNote);
            layout.AddRow(Ui.Heading("Shape modes to vary"));
            layout.AddRow(Ui.Note("Bounds in the Morph tab's units; a blank bound takes the hull-relative default shown. The Lackenby " +
                                  "modes move whole sections and the deck edge, so the upper-edge limit leaves them little room."));
            var table = new TableLayout { Spacing = new Size(6, 4) };
            table.Rows.Add(new TableRow(new TableCell(Ui.Field("Mode")), new TableCell(Ui.Field("Min")), new TableCell(Ui.Field("Max")),
                                        new TableCell(Ui.Field("Unit")), new TableCell(Ui.Field("Blank bounds"), true)));
            foreach (var m in _modes)
                table.Rows.Add(new TableRow(new TableCell(m.Vary), new TableCell(m.Min), new TableCell(m.Max), new TableCell(Ui.Unit(m.Unit)),
                                            new TableCell(Ui.Hint(m.Auto), true)));
            layout.AddRow(table);
            layout.AddRow(Ui.Heading("Search"));
            layout.AddRow(Row(Name("Population"), _population, Ui.Field("   Evaluations"), _evaluations, Ui.Field("   Random seed"), _seed));
            layout.AddRow(Row(Name("First generation"), _start));
            layout.AddRow(Ui.Note("Differential evolution (current-to-pbest/1 and rand/1/bin) with Deb's rule: feasible candidates by J, " +
                                  "the others by how far they break the gates. The baseline is in the first generation when the bounds of every varied mode include zero. " +
                                  "The same seed and settings repeat the same search."));
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(run, stop, null);
            layout.EndVertical();
            layout.AddRow(_live);
            layout.AddRow(_caption);
            layout.AddRow(_chart);
            layout.AddRow(Ui.Heading("Best candidates"));
            layout.AddRow(Ui.Note("Select a row to see its speeds; evaluate it on the Morph tab for every speed and check, or build it."));
            layout.AddRow(_candidates);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(apply, build, save, null);
            layout.EndVertical();
            layout.AddRow(Ui.Heading("Objective speeds of the selected candidate"));
            layout.AddRow(_speeds);
            layout.AddRow(Ui.Heading("Notes"));
            layout.AddRow(_notes);
            layout.Add(null, yscale: true);
            Content = Ui.Scroll(layout);
            Show(null);
        }

        private static void SetBound(Dictionary<string, double> bounds, string name, double? value)
        {
            if (value.HasValue) bounds[name] = value.Value;
            else bounds.Remove(name);
        }

        /// <summary>Controls side by side at their own widths.</summary>
        private static Control Row(params Control[] items)
        {
            var row = new StackLayout { Orientation = Orientation.Horizontal, Spacing = 6, VerticalContentAlignment = VerticalAlignment.Center };
            foreach (var item in items) row.Items.Add(new StackLayoutItem(item));
            return row;
        }

        private static Label Name(string text)
        {
            var label = Ui.Field(text);
            label.Width = 130;
            return label;
        }

        private static GridColumn Column(string header, int index, int width)
        {
            return new GridColumn { HeaderText = header, DataCell = new TextBoxCell(index), Width = width };
        }

        private static string F(double value, string format)
        {
            return double.IsNaN(value) || double.IsInfinity(value) ? "—" : value.ToString(format, CultureInfo.InvariantCulture);
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
                _quantity.SelectedKey = d.OptimiseQuantity;
                _weighting.SelectedKey = d.OptimiseWeighting;
                _profile.ShowText(d.OptimiseProfile);
                _service.Show(d.ServiceSpeed);
                _profile.Enabled = d.OptimiseWeighting == "profile";
                _service.Enabled = d.OptimiseWeighting == "service";
                foreach (var m in _modes)
                {
                    m.Vary.Checked = d.OptimiseModes != null && d.OptimiseModes.Contains(m.Name);
                    double value;
                    m.Min.Show(d.OptimiseMin != null && d.OptimiseMin.TryGetValue(m.Name, out value) ? value : (double?)null, "0.#####");
                    m.Max.Show(d.OptimiseMax != null && d.OptimiseMax.TryGetValue(m.Name, out value) ? value : (double?)null, "0.#####");
                }
                _population.Show(d.OptimisePopulation, "0");
                _evaluations.Show(d.OptimiseEvaluations, "0");
                _seed.Show(d.OptimiseSeed, "0");
                _start.SelectedKey = d.OptimiseStart;
                _objectiveNote.Text = ObjectiveText(d);
            }
            finally
            {
                _loading = false;
            }
        }

        /// <summary>What J will be, in words, for the current settings.</summary>
        private static string ObjectiveText(HullDefinition d)
        {
            var symbol = d.OptimiseQuantity == "brake_power" ? "P_B" : d.OptimiseQuantity == "michell" ? "R_W+R_F" : "R_T";
            string text, problem;
            if (d.OptimiseWeighting == "profile")
            {
                var profile = d.Profile(out problem);
                if (problem != null) return problem;
                var total = profile.Sum(p => p.Value);
                text = "J = Σ w·" + symbol + " / Σ w·" + symbol + "₀ over " + profile.Count + (profile.Count == 1 ? " speed" : " speeds") + " (" +
                       string.Join(", ", profile.Select(p => F(p.Key, "0.###") + " kn " + F(100.0 * p.Value / total, "0.#") + "%")) + ")." +
                       (d.OptimiseQuantity == "brake_power" ? " With brake power and time shares this is the ratio of propulsion energy over the profile."
                                                           : " Shares are time shares; speeds with more time count more.");
            }
            else if (d.OptimiseWeighting == "service")
            {
                var speed = d.ObjectiveServiceSpeed;
                if (!(speed > 0)) return "Enter the service speed, or the design speed on the Condition tab.";
                text = "J = " + symbol + "/" + symbol + "₀ at " + F(speed.Value, "0.###") + " kn (" +
                       (d.ServiceSpeed.HasValue ? "service speed" : "the design speed") + "); other speeds are not looked at.";
            }
            else
            {
                var speeds = d.Speeds(out problem);
                if (problem != null) return problem;
                text = "J = mean(" + symbol + "/" + symbol + "₀) over the Condition tab's " + HullDefinition.Describe(speeds).TrimEnd('.') +
                       "; each speed's change counts the same.";
            }
            if (d.OptimiseQuantity == "michell")
                text += " Michell's sum leaves out the form factor, transom and correlation terms: use it to compare hulls only.";
            if (d.OptimiseQuantity == "brake_power")
                text += " The propeller keeps the baseline's P/D and blade area for every candidate.";
            return text;
        }

        /// <summary>Live progress of the running search.</summary>
        public void ShowProgress(Dictionary<string, object> data)
        {
            var evaluations = J.Num(data, "evaluations");
            var total = J.Num(data, "max_evaluations");
            var best = J.Num(data, "best_J");
            var elapsed = J.Num(data, "elapsed_s");
            if (!double.IsNaN(evaluations) && !double.IsNaN(best))
            {
                if (_liveX.Count > 0 && evaluations < _liveX[_liveX.Count - 1]) Clear();
                _liveX.Add(evaluations);
                _liveY.Add(best);
            }
            // Redrawing relays out the whole tab; at most once a second keeps Rhino responsive.
            var now = DateTime.Now;
            if ((now - _lastDraw).TotalSeconds < 1.0 && !(evaluations >= total)) return;
            _lastDraw = now;
            if (_liveX.Count > 0)
                _chart.Show("Evaluations", "J (baseline = 1)", new[]
                {
                    new LineChart.Series { Name = "Best feasible J so far", X = _liveX.ToArray(), Y = _liveY.ToArray(), Color = Color.FromArgb(24, 95, 165), Markers = false },
                });
            var left = evaluations > 0 && total > evaluations && elapsed > 0 ? elapsed / evaluations * (total - evaluations) : double.NaN;
            _live.Text = string.Format(CultureInfo.InvariantCulture,
                "Running: generation {0:0}, {1:0} of {2:0} evaluations, {3:0} feasible. Best J {4} ({5}). {6} elapsed{7}.",
                J.Num(data, "generation"), evaluations, total, J.Num(data, "feasible"),
                F(best, "0.00000"), double.IsNaN(best) ? "none feasible yet" : F(100.0 * (best - 1.0), "+0.00;-0.00;0.00") + "%",
                Duration(elapsed), double.IsNaN(left) ? "" : ", about " + Duration(left) + " left");
        }

        /// <summary>A new search started.</summary>
        public void Clear()
        {
            _liveX.Clear();
            _liveY.Clear();
            _lastDraw = DateTime.MinValue;
            _chart.Clear("Searching…");
            _live.Text = "Starting: the baseline, its fairness lines and the first generation.";
        }

        private static string Duration(double seconds)
        {
            if (double.IsNaN(seconds) || seconds < 0) return "—";
            var span = TimeSpan.FromSeconds(Math.Round(seconds));
            return span.TotalHours >= 1 ? string.Format(CultureInfo.InvariantCulture, "{0:0} h {1:00} min", Math.Floor(span.TotalHours), span.Minutes)
                 : span.TotalMinutes >= 1 ? string.Format(CultureInfo.InvariantCulture, "{0:0} min {1:00} s", Math.Floor(span.TotalMinutes), span.Seconds)
                 : string.Format(CultureInfo.InvariantCulture, "{0:0} s", span.TotalSeconds);
        }

        private Dictionary<string, object> Selected()
        {
            var index = _candidates.SelectedRow;
            if (index < 0 && _rows.Count > 0) index = 0;
            return index >= 0 && index < _rows.Count ? _rows[index] : null;
        }

        private void UseSelected(bool evaluate)
        {
            var doc = _doc();
            var row = Selected();
            if (doc == null || row == null)
            {
                Session.Say("Run the optimisation first, then choose a candidate.", true);
                return;
            }
            var amplitudes = J.Obj(row, "amplitudes");
            var values = new Dictionary<string, double>();
            if (amplitudes != null)
                foreach (var pair in amplitudes) values[pair.Key] = J.Num(pair.Value);
            Session.UseCandidate(doc, values, evaluate);
            if (!evaluate) Session.BuildCandidate(doc);
        }

        public void Show(Outcome outcome)
        {
            _shown = outcome;
            var result = outcome == null ? null : outcome.Result;
            _rows = new List<Dictionary<string, object>>();
            if (result == null)
            {
                _caption.Text = "No search yet. Choose the objective and the modes, then Run optimisation.";
                if (_liveX.Count == 0) _chart.Clear("No search yet.");
                _candidates.DataStore = new List<string[]>();
                _speeds.DataStore = new List<string[]>();
                _notes.Text = "";
                return;
            }
            _live.Text = "";
            var objective = J.Obj(result, "objective");
            var search = J.Obj(result, "search");
            var counts = J.Obj(result, "counts");
            var best = J.Obj(result, "best");
            var stopped = search != null && true.Equals(search.ContainsKey("stopped") ? search["stopped"] : null);

            var trace = J.List(result, "best_by_evaluation");
            var x = new List<double>();
            var y = new List<double>();
            if (trace != null)
                for (var i = 0; i < trace.Count; i++)
                {
                    if (trace[i] == null) continue;
                    x.Add(i + 1);
                    y.Add(J.Num(trace[i]));
                }
            var medianX = new List<double>();
            var medianY = new List<double>();
            var history = J.List(result, "history");
            if (history != null)
                foreach (var item in history)
                {
                    var h = item as Dictionary<string, object>;
                    var median = J.Num(h, "population_median_J");
                    if (double.IsNaN(median)) continue;
                    medianX.Add(J.Num(h, "evaluations"));
                    medianY.Add(median);
                }
            _liveX.Clear();
            _liveY.Clear();
            if (x.Count > 0)
                _chart.Show("Evaluations", "J (baseline = 1)", new[]
                {
                    new LineChart.Series { Name = "Best feasible J so far", X = x.ToArray(), Y = y.ToArray(), Color = Color.FromArgb(24, 95, 165), Markers = false },
                    new LineChart.Series { Name = "Median J of the feasible population", X = medianX.ToArray(), Y = medianY.ToArray(), Color = Color.FromArgb(216, 90, 48) },
                });
            else _chart.Clear("No feasible candidate.");

            _varied = (J.List(result, "variables") ?? new ArrayList()).Cast<object>()
                .Select(v => J.Str(v as Dictionary<string, object>, "name")).Where(n => n != null).ToList();
            _candidates.Columns.Clear();
            _candidates.Columns.Add(Column("#", 0, 34));
            _candidates.Columns.Add(Column("J", 1, 76));
            _candidates.Columns.Add(Column("Change", 2, 66));
            for (var i = 0; i < _varied.Count; i++)
            {
                var mode = _modes.FirstOrDefault(m => m.Name == _varied[i]);
                _candidates.Columns.Add(Column((mode != null ? mode.Label : _varied[i]) + (mode != null ? " (" + (mode.Unit == "m" ? "m" : "–") + ")" : ""), 3 + i, 104));
            }
            var tail = 3 + _varied.Count;
            _candidates.Columns.Add(Column("Surface moved (mm)", tail, 118));
            _candidates.Columns.Add(Column("LCB shift (% LWL)", tail + 1, 110));
            _candidates.Columns.Add(Column("Waterline (mm)", tail + 2, 96));
            var table = new List<string[]>();
            var list = J.List(result, "candidates");
            if (list != null)
                foreach (var item in list)
                {
                    var c = item as Dictionary<string, object>;
                    if (c == null) continue;
                    _rows.Add(c);
                    var jValue = J.Num(c, "J");
                    var amplitudes = J.Obj(c, "amplitudes");
                    var cells = new List<string> { (table.Count + 1).ToString(CultureInfo.InvariantCulture), F(jValue, "0.00000"),
                                                    F(100.0 * (jValue - 1.0), "+0.00;-0.00;0.00") + "%" };
                    cells.AddRange(_varied.Select(n => F(J.Num(amplitudes, n), "0.#####")));
                    cells.Add(F(1000.0 * J.Num(J.Obj(c, "deviation"), "normal_deviation_m"), "0.0"));
                    cells.Add(F(J.Num(J.Obj(c, "changes"), "lcb_percent_lwl"), "+0.000;-0.000;0.000"));
                    cells.Add(F(1000.0 * J.Num(J.Obj(c, "changes"), "z_wl_m"), "+0.0;-0.0;0.0"));
                    table.Add(cells.ToArray());
                }
            _candidates.DataStore = table;
            if (table.Count > 0) _candidates.SelectRow(0);
            ShowSpeeds();

            var bestJ = J.Num(best, "J");
            _caption.Text = string.Format(CultureInfo.InvariantCulture,
                "Python engine result, {0:HH:mm:ss}. {1}. {2} {3:0} evaluations in {4} generations ({5:0} feasible), {6}{7}.",
                outcome.Time, J.Str(objective, "formula") ?? "",
                best == null ? "No feasible candidate." :
                    "Best J " + F(bestJ, "0.00000") + " (" + F(100.0 * (bestJ - 1.0), "+0.00;-0.00;0.00") + "%).",
                J.Num(search, "evaluations"), F(J.Num(search, "generations"), "0"),
                J.Num(counts, "feasible"), Duration(J.Num(search, "elapsed_s")),
                stopped ? "; stopped by the user" : "");

            var notes = new List<string>(outcome.Warnings);
            notes.AddRange(J.Strs(result, "warnings"));
            var variables = J.List(result, "variables");
            if (variables != null)
                notes.Add("Bounds used: " + string.Join("; ", variables.Cast<object>().Select(v =>
                {
                    var b = v as Dictionary<string, object>;
                    var mode = _modes.FirstOrDefault(m => m.Name == J.Str(b, "name"));
                    var auto = J.Strs(b, "automatic");
                    return (mode != null ? mode.Label : J.Str(b, "name")) + " " + F(J.Num(b, "min"), "0.#####") + " … " +
                           F(J.Num(b, "max"), "0.#####") + (auto.Count > 0 ? " (blank " + string.Join(" and ", auto) + ": default)" : "");
                })) + ".");
            var failures = J.Obj(counts, "gate_failures");
            if (failures != null && failures.Count > 0)
                notes.Add("Gates that rejected candidates (count): " + string.Join(", ", failures
                    .OrderByDescending(f => J.Num(f.Value)).Select(f => GateLabel(f.Key) + " " + F(J.Num(f.Value), "0"))) + ".");
            var errors = J.Num(counts, "errors");
            if (errors > 0) notes.Add(F(errors, "0") + " candidates could not be floated or measured; they count as failures.");
            notes.AddRange(J.Strs(result, "notes"));
            var log = J.Str(result, "log_path");
            if (!string.IsNullOrEmpty(log)) notes.Add("Every evaluation is listed in " + log + " (one JSON object per line).");
            notes.Add("J compares candidates with the baseline under the same Python model; it is not a validated resistance gain.");
            _notes.Text = string.Join(Environment.NewLine, notes);
        }

        private static string GateLabel(string key)
        {
            switch (key)
            {
                case "draft_change_m": return "draft change";
                case "lcb_shift_percent_lwl": return "LCB shift";
                case "lwl_change_percent": return "LWL change";
                case "bwl_change_percent": return "BWL change";
                case "normal_deviation_m": return "surface moved";
                case "upper_edge_m": return "upper edge moved";
                case "slope_dy_dx": return "mode slope dy/dx";
                case "slope_dy_dz": return "mode slope dy/dz";
                default: return key;
            }
        }

        private void ShowSpeeds()
        {
            var result = _shown == null ? null : _shown.Result;
            var row = Selected();
            var rows = new List<string[]>();
            if (result != null && row != null)
            {
                var objective = J.Obj(result, "objective");
                var baseline = J.Obj(result, "baseline");
                var speeds = J.Nums(objective, "speeds_kn");
                var weights = J.Nums(objective, "weights");
                var q0 = J.Nums(baseline, "values");
                var q = J.Nums(row, "values");
                var unit = J.Str(objective, "units") == "W" ? "kW" : "kN";
                const double scale = 0.001;
                _speeds.Columns[2].HeaderText = "Baseline (" + unit + ")";
                _speeds.Columns[3].HeaderText = "Candidate (" + unit + ")";
                var notes = J.List(row, "notes");
                for (var i = 0; i < speeds.Length && i < q0.Length && i < q.Length; i++)
                {
                    var texts = notes != null && i < notes.Count ? notes[i] as IList : null;
                    var note = texts == null ? "" : string.Join("; ", texts.Cast<object>().Select(t => Convert.ToString(t, CultureInfo.InvariantCulture)));
                    rows.Add(new[] { F(speeds[i], "0.###"), i < weights.Length ? F(100.0 * weights[i], "0.#") + "%" : "",
                                     F(q0[i] * scale, "0.00"), F(q[i] * scale, "0.00"), F(q0[i] != 0 ? q[i] / q0[i] : double.NaN, "0.0000"), note });
                }
            }
            _speeds.DataStore = rows;
        }

        private void SaveCsv()
        {
            var table = _candidates.DataStore as List<string[]>;
            var result = _shown == null ? null : _shown.Result;
            if (result == null || table == null || table.Count == 0)
            {
                Session.Say("Run the optimisation before saving the candidates.", true);
                return;
            }
            var doc = _doc();
            var dialog = new SaveFileDialog { Title = "Save optimisation candidates" };
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
            dialog.FileName = "optimise_" + DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture) + ".csv";
            if (dialog.ShowDialog(this) != DialogResult.Ok) return;
            var speeds = J.Nums(J.Obj(result, "objective"), "speeds_kn");
            var csv = new StringBuilder();
            csv.AppendLine("# " + (J.Str(J.Obj(result, "objective"), "formula") ?? "") + "; Python engine result");
            var headers = _candidates.Columns.Select(c => c.HeaderText).Concat(speeds.Select(v => "Ratio at " + F(v, "0.###") + " kn"));
            csv.AppendLine(string.Join(",", headers.Select(h => Csv(h, true))));
            for (var i = 0; i < table.Count && i < _rows.Count; i++)
            {
                var ratios = J.Nums(_rows[i], "ratios");
                var cells = table[i].Concat(ratios.Select(r => F(r, "0.000000")));
                csv.AppendLine(string.Join(",", cells.Select(cell => Csv(cell, false))));
            }
            var path = dialog.FileName;
            if (!path.EndsWith(".csv", StringComparison.OrdinalIgnoreCase)) path += ".csv";
            File.WriteAllText(path, csv.ToString(), new UTF8Encoding(true));
            Session.Say("candidates saved: " + path, false);
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

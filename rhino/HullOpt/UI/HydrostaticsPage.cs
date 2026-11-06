using System;
using System.Collections.Generic;
using System.Globalization;
using Eto.Drawing;
using Eto.Forms;
using Rhino;

namespace HullOpt
{
    /// <summary>Hydrostatics tab: results table and sectional area curve.</summary>
    public sealed class HydrostaticsPage : Panel
    {
        private readonly Func<RhinoDoc> _doc;
        private readonly GridView _table = new GridView { Height = 330, ShowHeader = true };
        private readonly LineChart _sections = new LineChart();
        private readonly CheckBox _draw = new CheckBox { Text = "Draw the waterline in the model (layer HullOpt::Waterline)" };
        private readonly Label _caption = new Label { Wrap = WrapMode.Word };
        private readonly Label _warnings = Ui.Note("");
        private bool _loading;

        public HydrostaticsPage(Func<RhinoDoc> doc)
        {
            _doc = doc;
            _table.Columns.Add(new GridColumn { HeaderText = "Quantity", DataCell = new TextBoxCell(0), Width = 190 });
            _table.Columns.Add(new GridColumn { HeaderText = "Value", DataCell = new TextBoxCell(1), Width = 110 });
            _table.Columns.Add(new GridColumn { HeaderText = "Unit", DataCell = new TextBoxCell(2), Width = 60 });
            var run = new Button { Text = "Run hydrostatics" };
            run.Click += (sender, e) =>
            {
                var current = _doc();
                if (current != null) Session.RunHydrostatics(current);
            };
            _draw.CheckedChanged += (sender, e) =>
            {
                if (_loading) return;
                var current = _doc();
                if (current == null) return;
                var definition = Session.Definition(current);
                definition.DrawWaterline = _draw.Checked == true;
                Session.Save(current, definition);
            };
            var layout = new DynamicLayout { Padding = new Padding(8), Spacing = new Size(6, 6) };
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(run, null);
            layout.EndVertical();
            layout.AddRow(_draw);
            layout.AddRow(_caption);
            layout.AddRow(_table);
            layout.AddRow(Ui.Heading("Sectional area curve"));
            layout.AddRow(_sections);
            layout.AddRow(_warnings);
            layout.Add(null, yscale: true);
            Content = Ui.Scroll(layout);
            _sections.Clear("Run hydrostatics to see the sectional area curve.");
        }

        public void Refresh()
        {
            var doc = _doc();
            if (doc == null) return;
            _loading = true;
            try
            {
                _draw.Checked = Session.Definition(doc).DrawWaterline;
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
            var h = outcome == null ? null : outcome.Hydrostatics;
            if (h == null)
            {
                _table.DataStore = new List<string[]>();
                _caption.Text = "No results yet. Set the hull and the condition, then run.";
                _sections.Clear("Run hydrostatics to see the sectional area curve.");
                _warnings.Text = "";
                return;
            }
            var mpu = outcome.MetersPerUnit;
            var z = J.Num(h, "z_wl_m");
            var rows = new List<string[]>
            {
                new[] { "Waterline height z", F(z, "0.####"), "m" },
                new[] { "Waterline height z (model)", F(z / mpu, "0.###"), outcome.UnitName },
                new[] { "Volume ∇", F(J.Num(h, "volume_m3"), "0.###"), "m³" },
                new[] { "Displacement Δ", F(J.Num(h, "displacement_kg") / 1000.0, "0.###"), "t" },
                new[] { "Length on waterline LWL", F(J.Num(h, "lwl_m"), "0.###"), "m" },
                new[] { "Beam on waterline BWL", F(J.Num(h, "bwl_m"), "0.###"), "m" },
                new[] { "Draft T (to lowest point)", F(J.Num(h, "draft_m"), "0.###"), "m" },
                new[] { "LCB x (model frame)", F(J.Num(h, "lcb_m"), "0.###"), "m" },
                new[] { "LCB from LWL midpoint, +fwd", F(J.Num(h, "lcb_percent_lwl"), "0.###"), "% LWL" },
                new[] { "LCF x (model frame)", F(J.Num(h, "lcf_m"), "0.###"), "m" },
                new[] { "KB above keel", F(J.Num(h, "kb_above_keel_m"), "0.###"), "m" },
                new[] { "Wetted surface S (with transom)", F(J.Num(h, "wetted_area_m2"), "0.##"), "m²" },
                new[] { "Immersed transom area (projected)", F(J.Num(h, "transom_area_m2"), "0.###"), "m²" },
                new[] { "Waterplane area Awp", F(J.Num(h, "waterplane_area_m2"), "0.##"), "m²" },
                new[] { "Max. section area Ax", F(J.Num(h, "max_section_area_m2"), "0.###"), "m²" },
                new[] { "Block coefficient Cb", F(J.Num(h, "cb"), "0.0000"), "" },
                new[] { "Prismatic coefficient Cp", F(J.Num(h, "cp"), "0.0000"), "" },
                new[] { "Max. section coefficient Cx", F(J.Num(h, "cx"), "0.0000"), "" },
                new[] { "Waterplane coefficient Cwp", F(J.Num(h, "cwp"), "0.0000"), "" },
                new[] { "Half angle of entrance iE", F(J.Num(h, "entrance_half_angle_deg"), "0.##"), "°" },
            };
            if (outcome.Kind == "hydro.equilibrium")
                rows.Insert(0, new[] { "Equilibrium iterations", F(J.Num(outcome.Result, "iterations"), "0"), "" });
            _table.DataStore = rows;
            _caption.Text = string.Format(CultureInfo.InvariantCulture, "Python engine result, {0:HH:mm:ss}. Zero trim, ρ = {1:0.#} kg/m³.",
                                          outcome.Time, J.Num(h, "displacement_kg") / Math.Max(J.Num(h, "volume_m3"), 1e-12));
            var xs = J.Nums(h, "section_x_m");
            var areas = J.Nums(h, "section_area_m2");
            _sections.Show("x (m, model frame)", "Section area (m²)", new[]
            {
                new LineChart.Series { Name = "Sectional area", X = xs, Y = areas, Color = Color.FromArgb(24, 95, 165) },
            });
            var notes = new List<string>(outcome.Warnings);
            notes.AddRange(J.Strs(h, "warnings"));
            _warnings.Text = notes.Count == 0 ? "" : "Notes: " + string.Join(" · ", notes);
        }
    }
}

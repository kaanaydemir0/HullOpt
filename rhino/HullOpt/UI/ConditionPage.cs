using System;
using System.Globalization;
using Eto.Drawing;
using Eto.Forms;
using Rhino;

namespace HullOpt
{
    /// <summary>Condition tab: water, floating position and speeds.</summary>
    public sealed class ConditionPage : Panel
    {
        private const double KnotToMs = 1852.0 / 3600.0;
        private readonly Func<RhinoDoc> _doc;
        private readonly DropDown _water = Ui.Choices(
            "sea15", "Sea water, 15 °C (ITTC)",
            "fresh15", "Fresh water, 15 °C (ITTC)",
            "custom", "Custom");
        private readonly NumberBox _rho = new NumberBox();
        private readonly NumberBox _nu = new NumberBox(110);
        private readonly RadioButtonList _mode = new RadioButtonList { Orientation = Orientation.Vertical, Spacing = new Size(4, 4) };
        private readonly NumberBox _z = new NumberBox(110);
        private readonly NumberBox _displacement = new NumberBox(110);
        private readonly Label _zUnit = Ui.Unit("");
        private readonly NumberBox _from = new NumberBox(60);
        private readonly NumberBox _to = new NumberBox(60);
        private readonly NumberBox _step = new NumberBox(60);
        private readonly NumberBox _design = new NumberBox(60);
        private readonly CommitBox _list = new CommitBox(230);
        private readonly Label _speedNote = Ui.Note("");
        private readonly Label _suggestNote = Ui.Note("");
        private bool _loading;

        public ConditionPage(Func<RhinoDoc> doc)
        {
            _doc = doc;
            _mode.Items.Add(new ListItem { Key = HullDefinition.FloatByWaterline, Text = "Waterline height (zero trim)" });
            _mode.Items.Add(new ListItem { Key = HullDefinition.FloatByDisplacement, Text = "Displacement: HullOpt finds the waterline" });

            _water.SelectedIndexChanged += (sender, e) => Change(d =>
            {
                d.WaterPreset = _water.SelectedKey;
                if (d.WaterPreset == "sea15") { d.Rho = 1025.9; d.Nu = 1.18831e-6; }
                else if (d.WaterPreset == "fresh15") { d.Rho = 999.1; d.Nu = 1.13902e-6; }
            }, true);
            _rho.Committed += (sender, e) => Change(d =>
            {
                if (_rho.Value > 0 && _rho.Value != d.Rho) { d.Rho = _rho.Value.Value; d.WaterPreset = "custom"; }
            });
            _nu.Committed += (sender, e) => Change(d =>
            {
                if (_nu.Value > 0 && _nu.Value != d.Nu) { d.Nu = _nu.Value.Value; d.WaterPreset = "custom"; }
            });
            _mode.SelectedIndexChanged += (sender, e) => Change(d => d.FloatMode = _mode.SelectedKey ?? HullDefinition.FloatByWaterline, true);
            _z.Committed += (sender, e) => Change(d => d.WaterlineZ = _z.Value);
            _displacement.Committed += (sender, e) => Change(d => d.DisplacementTonnes = _displacement.Value);
            _from.Committed += (sender, e) => Change(d => { if (_from.Value.HasValue) d.SpeedFrom = _from.Value.Value; });
            _to.Committed += (sender, e) => Change(d => { if (_to.Value.HasValue) d.SpeedTo = _to.Value.Value; });
            _step.Committed += (sender, e) => Change(d => { if (_step.Value.HasValue) d.SpeedStep = _step.Value.Value; });
            _list.Committed += (sender, e) => Change(d => d.SpeedList = (_list.Text ?? "").Trim());
            _design.Committed += (sender, e) => Change(d => d.DesignSpeed = _design.Value);
            var suggest = new Button { Text = "Suggest from LWL" };
            suggest.Click += (sender, e) => SuggestSpeeds();

            var layout = new DynamicLayout { Padding = new Padding(8), Spacing = new Size(6, 6) };
            layout.AddRow(Ui.Heading("Water"));
            layout.AddRow(_water);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("Density ρ"), _rho, Ui.Unit("kg/m³"), null);
            layout.AddRow(Ui.Field("Kinematic viscosity ν"), _nu, Ui.Unit("m²/s"), null);
            layout.EndVertical();
            layout.AddRow(Ui.Heading("Floating position"));
            layout.AddRow(_mode);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("Waterline height z"), _z, _zUnit, null);
            layout.AddRow(Ui.Field("Displacement Δ"), _displacement, Ui.Unit("t"), null);
            layout.EndVertical();
            layout.AddRow(Ui.Note("z is the model's own z coordinate of the waterline. Trim is zero; the hull must be modelled at its floating attitude."));
            layout.AddRow(Ui.Heading("Speeds"));
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("From"), _from, Ui.Field("to"), _to, Ui.Field("step"), _step, Ui.Unit("kn"), null);
            layout.EndVertical();
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("Also at"), _list, Ui.Unit("kn"), null);
            layout.EndVertical();
            layout.AddRow(Ui.Note("Single speeds or more ranges, separated by ';' or spaces: 13.7; 17; 18-24:2 (from-to:step; " +
                                  "without ':step' the range uses the step above). Every speed is computed; a speed outside a method's " +
                                  "range is marked in the results, not left out."));
            layout.AddRow(_speedNote);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(Ui.Field("Design speed"), _design, Ui.Unit("kn"), null);
            layout.AddRow(suggest, null);
            layout.EndVertical();
            layout.AddRow(_suggestNote);
            layout.Add(null, yscale: true);
            Content = Ui.Scroll(layout);
        }

        private void Change(Action<HullDefinition> edit, bool refresh = false)
        {
            if (_loading) return;
            var doc = _doc();
            if (doc == null) return;
            var definition = Session.Definition(doc);
            edit(definition);
            Session.Save(doc, definition);
        }

        private void SuggestSpeeds()
        {
            var last = Session.LastHydrostatics ?? Session.LastResistance;
            var lwl = J.Num(last == null ? null : last.Hydrostatics, "lwl_m");
            if (!(lwl > 0))
            {
                _suggestNote.Text = "Run hydrostatics first; the suggestion uses the waterline length.";
                return;
            }
            var root = Math.Sqrt(9.80665 * lwl) / KnotToMs;
            var from = Math.Max(0.5, Math.Round(0.10 * root * 2) / 2);
            var to = Math.Round(0.50 * root * 2) / 2;
            var step = Math.Max(0.5, Math.Round((to - from) / 16 * 2) / 2);
            Change(d =>
            {
                d.SpeedFrom = from;
                d.SpeedTo = to;
                d.SpeedStep = step;
            });
            _suggestNote.Text = string.Format(CultureInfo.InvariantCulture,
                "Range set to Froude number 0.10–0.50 on LWL = {0:0.##} m: {1:0.#}–{2:0.#} kn; the 'Also at' list is kept. Adjust as needed.",
                lwl, from, to);
            Refresh();
        }

        public void Refresh()
        {
            var doc = _doc();
            if (doc == null) return;
            var d = Session.Definition(doc);
            _loading = true;
            try
            {
                _water.SelectedKey = d.WaterPreset;
                _rho.Show(d.Rho);
                _nu.Show(d.Nu, "0.#####E+0");
                _mode.SelectedKey = d.FloatMode;
                _z.Show(d.WaterlineZ);
                _displacement.Show(d.DisplacementTonnes);
                _zUnit.Text = Session.UnitName(doc) + " (model z)";
                _from.Show(d.SpeedFrom);
                _to.Show(d.SpeedTo);
                _step.Show(d.SpeedStep);
                _list.ShowText(d.SpeedList);
                _design.Show(d.DesignSpeed);
                var byDisplacement = d.FloatMode == HullDefinition.FloatByDisplacement;
                _z.Enabled = !byDisplacement;
                _displacement.Enabled = byDisplacement;
                string problem;
                var speeds = d.Speeds(out problem);
                _speedNote.Text = problem ?? HullDefinition.Describe(speeds);
            }
            finally
            {
                _loading = false;
            }
        }
    }
}

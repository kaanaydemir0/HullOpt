using System;
using System.Diagnostics;
using System.Globalization;
using Eto.Drawing;
using Eto.Forms;
using Rhino;

namespace HullOpt
{
    /// <summary>Hull tab: which Rhino objects form the hull and the appendages.</summary>
    public sealed class HullPage : Panel
    {
        private readonly Func<RhinoDoc> _doc;
        private readonly Label _hull = new Label { Wrap = WrapMode.Word };
        private readonly Label _appendages = new Label { Wrap = WrapMode.Word };
        private readonly CheckBox _include = new CheckBox { Text = "Include appendages in hydrostatics and resistance" };
        private readonly Label _units = new Label { Wrap = WrapMode.Word };
        private readonly Label _folder = new Label { Wrap = WrapMode.Word };
        private readonly Label _summary = new Label { Wrap = WrapMode.Word };
        private readonly CheckBox _fine = new CheckBox { Text = "Fine surface mesh (slower; for final numbers)" };
        private bool _loading;

        public HullPage(Func<RhinoDoc> doc)
        {
            _doc = doc;
            var selectHull = new Button { Text = "Select hull surfaces" };
            selectHull.Click += (sender, e) => RhinoApp.RunScript("_HoHull", false);
            var clearHull = new Button { Text = "Clear" };
            clearHull.Click += (sender, e) => Change(d => d.HullObjects.Clear());
            var selectAppendages = new Button { Text = "Select appendages" };
            selectAppendages.Click += (sender, e) => RhinoApp.RunScript("_HoAppendages", false);
            var clearAppendages = new Button { Text = "Clear" };
            clearAppendages.Click += (sender, e) => Change(d =>
            {
                d.AppendageObjects.Clear();
                d.IncludeAppendages = false;
            });
            _include.CheckedChanged += (sender, e) =>
            {
                if (!_loading) Change(d => d.IncludeAppendages = _include.Checked == true);
            };
            _fine.CheckedChanged += (sender, e) =>
            {
                if (!_loading) Change(d => d.MaxGrid = _fine.Checked == true ? 100 : 50);
            };
            var showHull = new Button { Text = "Show in model" };
            showHull.Click += (sender, e) => RhinoApp.RunScript("_HoHull _Show", false);
            var openFolder = new Button { Text = "Open work folder" };
            openFolder.Click += (sender, e) =>
            {
                var current = _doc();
                if (current == null) return;
                try
                {
                    Process.Start("explorer.exe", "\"" + Session.WorkFolder(current) + "\"");
                }
                catch (Exception ex)
                {
                    Session.Say("Could not open the work folder: " + ex.Message, true);
                }
            };

            var layout = new DynamicLayout { Padding = new Padding(8), Spacing = new Size(6, 6) };
            layout.AddRow(Ui.Heading("Hull"));
            layout.AddRow(Ui.Note("Select every surface or polysurface of the hull's outer skin, up to the deck edge. " +
                                  "The skin must be closed below the waterline; an open deck above it is fine."));
            layout.AddRow(_hull);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(selectHull, clearHull, showHull, null);
            layout.EndVertical();
            layout.AddRow(Ui.Heading("Appendages"));
            layout.AddRow(Ui.Note("Optional: skegs, keels, rudders as closed bodies. Leave empty for bare-hull results."));
            layout.AddRow(_appendages);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(selectAppendages, clearAppendages, null);
            layout.EndVertical();
            layout.AddRow(_include);
            layout.AddRow(Ui.Heading("Model"));
            layout.AddRow(_units);
            layout.AddRow(_fine);
            layout.AddRow(Ui.Note("Standard mesh: up to 50 points per surface direction; fine: up to 100. On a 42 m test hull the two differed by 0.01% in volume and 0.07% in wetted area."));
            layout.AddRow(_folder);
            layout.BeginVertical(spacing: new Size(6, 6));
            layout.AddRow(openFolder, null);
            layout.EndVertical();
            layout.AddRow(_summary);
            layout.Add(null, yscale: true);
            Content = Ui.Scroll(layout);
        }

        private void Change(Action<HullDefinition> edit)
        {
            var doc = _doc();
            if (doc == null) return;
            var definition = Session.Definition(doc);
            edit(definition);
            Session.Save(doc, definition);
        }

        public void Refresh()
        {
            var doc = _doc();
            _loading = true;
            try
            {
                if (doc == null)
                {
                    _hull.Text = "No active model.";
                    return;
                }
                var d = Session.Definition(doc);
                _hull.Text = "Hull: " + Session.Describe(doc, d.HullObjects);
                _appendages.Text = "Appendages: " + Session.Describe(doc, d.AppendageObjects);
                _include.Checked = d.IncludeAppendages;
                _include.Enabled = d.AppendageObjects.Count > 0;
                _fine.Checked = d.MaxGrid > 50;
                var mpu = Session.MetersPerUnit(doc);
                _units.Text = string.Format(CultureInfo.InvariantCulture, "Model units: {0} (1 {1} = {2} m). Results are in SI units.",
                                            doc.ModelUnitSystem, Session.UnitName(doc), mpu);
                _folder.Text = "Work folder: " + Session.WorkFolderPath(doc);
            }
            finally
            {
                _loading = false;
            }
        }

        public void ShowGeometry(Outcome outcome)
        {
            var geometry = J.Obj(outcome == null ? null : outcome.Result, "geometry");
            if (geometry == null)
            {
                _summary.Text = "";
                return;
            }
            var box = J.List(geometry, "bbox_m");
            var text = string.Format(CultureInfo.InvariantCulture, "Last export: {0:0} faces, {1:0} triangles, skin area {2:0.##} m².",
                                     J.Num(geometry, "faces"), J.Num(geometry, "triangles"), J.Num(geometry, "area_m2"));
            if (box != null && box.Count == 2)
            {
                var low = J.Nums(box[0] as System.Collections.IList);
                var high = J.Nums(box[1] as System.Collections.IList);
                if (low.Length == 3 && high.Length == 3)
                    text += string.Format(CultureInfo.InvariantCulture, " Extent: {0:0.###} × {1:0.###} × {2:0.###} m (x, y, z).",
                                          high[0] - low[0], high[1] - low[1], high[2] - low[2]);
            }
            _summary.Text = text;
        }
    }
}

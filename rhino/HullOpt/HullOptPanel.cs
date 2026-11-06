using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using Eto.Drawing;
using Eto.Forms;
using Rhino;

namespace HullOpt
{
    /// <summary>
    /// The HullOpt panel, docked in Rhino's panel area like Layers or Properties.
    /// Tabs follow the work: hull, condition, hydrostatics, resistance, propulsion, morph, optimise.
    /// </summary>
    [Guid("dcf09534-c8e1-4f58-b1d4-3dd090baba6e")]
    public class HullOptPanel : Panel
    {
        private readonly uint _documentSerial;
        private readonly TabControl _tabs = new TabControl();
        private readonly HullPage _hull;
        private readonly ConditionPage _condition;
        private readonly HydrostaticsPage _hydrostatics;
        private readonly ResistancePage _resistance;
        private readonly PropulsionPage _propulsion;
        private readonly MorphPage _morph;
        private readonly OptimisePage _optimise;
        private readonly Label _engine = new Label { TextAlignment = TextAlignment.Right };
        private readonly Label _status = new Label { Wrap = WrapMode.Word };
        private readonly ProgressBar _progress = new ProgressBar { MinValue = 0, MaxValue = 100 };
        private readonly Button _cancel = new Button { Text = "Cancel", Enabled = false };
        private bool _subscribed;

        public static Guid PanelId
        {
            get { return typeof(HullOptPanel).GUID; }
        }

        public HullOptPanel() : this(0)
        {
        }

        public HullOptPanel(uint documentSerialNumber)
        {
            _documentSerial = documentSerialNumber;
            Session.Initialize();
            Func<RhinoDoc> doc = Document;
            _hull = new HullPage(doc);
            _condition = new ConditionPage(doc);
            _hydrostatics = new HydrostaticsPage(doc);
            _resistance = new ResistancePage(doc);
            _propulsion = new PropulsionPage(doc);
            _morph = new MorphPage(doc);
            _optimise = new OptimisePage(doc);
            _tabs.Pages.Add(new TabPage { Text = "Hull", Content = _hull });
            _tabs.Pages.Add(new TabPage { Text = "Condition", Content = _condition });
            _tabs.Pages.Add(new TabPage { Text = "Hydrostatics", Content = _hydrostatics });
            _tabs.Pages.Add(new TabPage { Text = "Resistance", Content = _resistance });
            _tabs.Pages.Add(new TabPage { Text = "Propulsion", Content = _propulsion });
            _tabs.Pages.Add(new TabPage { Text = "Morph", Content = _morph });
            _tabs.Pages.Add(new TabPage { Text = "Optimise", Content = _optimise });
            _cancel.Click += (sender, e) => Session.Cancel();

            var header = new TableLayout
            {
                Padding = new Padding(8, 6, 8, 2),
                Spacing = new Size(6, 0),
                Rows = { new TableRow(new TableCell(new Label { Text = "HullOpt", Font = SystemFonts.Bold(11) }, false),
                                      new TableCell(_engine, true)) },
            };
            var footer = new TableLayout
            {
                Padding = new Padding(8, 4, 8, 8),
                Spacing = new Size(6, 4),
                Rows =
                {
                    new TableRow(new TableCell(_progress, true), new TableCell(_cancel, false)),
                    new TableRow(new TableCell(_status, true)),
                },
            };
            Content = new TableLayout
            {
                Rows =
                {
                    new TableRow(header),
                    new TableRow(_tabs) { ScaleHeight = true },
                    new TableRow(footer),
                },
            };
        }

        private RhinoDoc Document()
        {
            return (_documentSerial != 0 ? RhinoDoc.FromRuntimeSerialNumber(_documentSerial) : null) ?? RhinoDoc.ActiveDoc;
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            Subscribe(true);
            RefreshAll();
            ShowEngine(EngineClient.Shared.Status);
            EngineClient.Shared.StartAsync().ContinueWith(task =>
            {
                if (task.IsFaulted) Session.Ui(() => SetStatus(task.Exception.InnerException.Message, true));
            });
        }

        protected override void OnUnLoad(EventArgs e)
        {
            Subscribe(false);
            base.OnUnLoad(e);
        }

        private void Subscribe(bool on)
        {
            if (on == _subscribed) return;
            _subscribed = on;
            if (on)
            {
                Session.DefinitionChanged += RefreshInputs;
                Session.DocumentChanged += RefreshAll;
                Session.HydrostaticsReady += OnHydrostatics;
                Session.ResistanceReady += OnResistance;
                Session.MorphReady += OnMorph;
                Session.OptimiseStarted += OnOptimiseStarted;
                Session.OptimiseProgress += _optimise.ShowProgress;
                Session.OptimiseReady += OnOptimise;
                Session.Message += SetStatus;
                Session.BusyChanged += OnBusy;
                Session.ProgressChanged += OnProgress;
                Session.EngineStatusChanged += ShowEngine;
                Session.TabRequested += SelectTab;
            }
            else
            {
                Session.DefinitionChanged -= RefreshInputs;
                Session.DocumentChanged -= RefreshAll;
                Session.HydrostaticsReady -= OnHydrostatics;
                Session.ResistanceReady -= OnResistance;
                Session.MorphReady -= OnMorph;
                Session.OptimiseStarted -= OnOptimiseStarted;
                Session.OptimiseProgress -= _optimise.ShowProgress;
                Session.OptimiseReady -= OnOptimise;
                Session.Message -= SetStatus;
                Session.BusyChanged -= OnBusy;
                Session.ProgressChanged -= OnProgress;
                Session.EngineStatusChanged -= ShowEngine;
                Session.TabRequested -= SelectTab;
            }
        }

        /// <summary>Input fields only; results stay as they are until a new result arrives.</summary>
        private void RefreshInputs()
        {
            _hull.Refresh();
            _condition.Refresh();
            _hydrostatics.Refresh();
            _resistance.Refresh();
            _propulsion.Refresh();
            _morph.Refresh();
            _optimise.Refresh();
        }

        private void RefreshAll()
        {
            RefreshInputs();
            var doc = Document();
            var serial = doc == null ? 0 : doc.RuntimeSerialNumber;
            var hydro = Session.LastHydrostatics != null && Session.LastHydrostatics.DocSerial == serial ? Session.LastHydrostatics : null;
            var resistance = Session.LastResistance != null && Session.LastResistance.DocSerial == serial ? Session.LastResistance : null;
            _hydrostatics.Show(hydro);
            _hull.ShowGeometry(hydro ?? resistance);
            _resistance.Show(resistance);
            _propulsion.Show(resistance);
            _morph.Show(Session.LastMorph != null && Session.LastMorph.DocSerial == serial ? Session.LastMorph : null);
            _optimise.Show(Session.LastOptimise != null && Session.LastOptimise.DocSerial == serial ? Session.LastOptimise : null);
        }

        private void OnHydrostatics(Outcome outcome)
        {
            _hydrostatics.Show(outcome);
            _hull.ShowGeometry(outcome);
            SelectTab("Hydrostatics");
        }

        private void OnResistance(Outcome outcome)
        {
            _resistance.Show(outcome);
            _propulsion.Show(outcome);
            _hull.ShowGeometry(outcome);
            var onPropulsion = _tabs.SelectedPage != null && _tabs.SelectedPage.Text == "Propulsion";
            SelectTab(onPropulsion && J.Obj(outcome.Result, "propulsion") != null ? "Propulsion" : "Resistance");
        }

        private void OnMorph(Outcome outcome)
        {
            _morph.Show(outcome);
            SelectTab("Morph");
        }

        private void OnOptimiseStarted()
        {
            _optimise.Clear();
            SelectTab("Optimise");
        }

        private void OnOptimise(Outcome outcome)
        {
            _optimise.Show(outcome);
            SelectTab("Optimise");
        }

        private void OnBusy(bool busy)
        {
            _cancel.Enabled = busy;
            if (!busy) _progress.Value = 0;
        }

        private void OnProgress(string stage, double fraction)
        {
            if (!double.IsNaN(fraction)) _progress.Value = Math.Max(0, Math.Min(100, (int)Math.Round(fraction * 100)));
            if (!string.IsNullOrEmpty(stage)) SetStatus(stage + "…", false);
        }

        private void SetStatus(string text, bool isError)
        {
            _status.Text = text ?? "";
            _status.TextColor = isError ? Colors.Firebrick : SystemColors.ControlText;
        }

        private void ShowEngine(string status)
        {
            var info = EngineClient.Shared.Info;
            var version = info == null ? "" : " · hullopt " + J.Str(info, "version") + ", Python " + J.Str(info, "python");
            _engine.Text = "Engine: " + (status ?? "") + version;
        }

        private void SelectTab(string name)
        {
            var page = _tabs.Pages.FirstOrDefault(p => p.Text == name);
            if (page != null) _tabs.SelectedPage = page;
        }
    }
}

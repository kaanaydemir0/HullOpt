using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Eto.Drawing;
using Eto.Forms;

namespace HullOpt
{
    /// <summary>A small line chart: one or more series against a shared x axis, with a legend.</summary>
    public sealed class LineChart : Drawable
    {
        public sealed class Series
        {
            public string Name;
            public double[] X;
            public double[] Y;
            public Color Color;
            /// <summary>Dots at the data points; off for dense traces.</summary>
            public bool Markers = true;
        }

        /// <summary>Start the y axis at zero (quantities); off to fit the data (ratios near 1).</summary>
        public bool FromZero { get; set; } = true;

        private readonly List<Series> _series = new List<Series>();
        private string _xTitle = "";
        private string _yTitle = "";
        private string _empty = "No results yet.";

        public LineChart()
        {
            Height = 230;
            Paint += OnPaint;
        }

        public void Show(string xTitle, string yTitle, IEnumerable<Series> series)
        {
            _xTitle = xTitle ?? "";
            _yTitle = yTitle ?? "";
            _series.Clear();
            if (series != null)
                _series.AddRange(series.Where(s => s != null && s.X != null && s.Y != null && s.X.Length == s.Y.Length && s.X.Length > 0));
            Invalidate();
        }

        public void Clear(string message)
        {
            _series.Clear();
            _empty = message ?? "";
            Invalidate();
        }

        private static double NiceStep(double span, int targetTicks)
        {
            if (!(span > 0)) return 1;
            var raw = span / Math.Max(1, targetTicks);
            var power = Math.Pow(10, Math.Floor(Math.Log10(raw)));
            foreach (var factor in new[] { 1.0, 2.0, 2.5, 5.0, 10.0 })
                if (factor * power >= raw) return factor * power;
            return 10 * power;
        }

        private static string Label(double value, double step)
        {
            var decimals = 0;
            while (decimals < 6 && Math.Abs(step * Math.Pow(10, decimals) - Math.Round(step * Math.Pow(10, decimals))) > 1e-6)
                decimals++;
            return value.ToString("F" + decimals, CultureInfo.InvariantCulture);
        }

        private void OnPaint(object sender, PaintEventArgs e)
        {
            var g = e.Graphics;
            g.AntiAlias = true;
            var text = SystemColors.ControlText;
            var faint = Color.FromArgb(128, 128, 128, 60);
            var font = SystemFonts.Default(8);
            var width = (float)Width;
            var height = (float)Height;
            g.FillRectangle(SystemColors.ControlBackground, 0, 0, width, height);
            var points = _series.SelectMany(s => s.X.Zip(s.Y, (x, y) => new { x, y }))
                                .Where(p => !double.IsNaN(p.x) && !double.IsNaN(p.y)).ToList();
            if (points.Count == 0)
            {
                g.DrawText(font, SystemColors.DisabledText, 8, height / 2 - 8, _empty);
                return;
            }
            double xMin = points.Min(p => p.x), xMax = points.Max(p => p.x);
            double yMin = FromZero ? Math.Min(0, points.Min(p => p.y)) : points.Min(p => p.y), yMax = points.Max(p => p.y);
            if (xMax - xMin < 1e-12) { xMin -= 0.5; xMax += 0.5; }
            if (yMax - yMin < 1e-12) { yMax = yMin + 1; }
            var xStep = NiceStep(xMax - xMin, 6);
            var yStep = NiceStep(yMax - yMin, 5);
            xMin = Math.Floor(xMin / xStep) * xStep;
            xMax = Math.Ceiling(xMax / xStep) * xStep;
            yMin = Math.Floor(yMin / yStep) * yStep;
            yMax = Math.Ceiling(yMax / yStep) * yStep;

            var yLabelWidth = 0f;
            for (var v = yMin; v <= yMax + yStep * 0.5; v += yStep)
                yLabelWidth = Math.Max(yLabelWidth, g.MeasureString(font, Label(v, yStep)).Width);
            float left = yLabelWidth + 26, right = width - 10, top = 22, bottom = height - 34;
            if (right - left < 40 || bottom - top < 40) return;
            Func<double, float> px = x => (float)(left + (x - xMin) / (xMax - xMin) * (right - left));
            Func<double, float> py = y => (float)(bottom - (y - yMin) / (yMax - yMin) * (bottom - top));

            for (var v = yMin; v <= yMax + yStep * 0.5; v += yStep)
            {
                var y = py(v);
                g.DrawLine(faint, left, y, right, y);
                var label = Label(v, yStep);
                var size = g.MeasureString(font, label);
                g.DrawText(font, text, left - size.Width - 4, y - size.Height / 2, label);
            }
            for (var v = xMin; v <= xMax + xStep * 0.5; v += xStep)
            {
                var x = px(v);
                g.DrawLine(faint, x, top, x, bottom);
                var label = Label(v, xStep);
                var size = g.MeasureString(font, label);
                g.DrawText(font, text, x - size.Width / 2, bottom + 3, label);
            }
            g.DrawLine(text, left, bottom, right, bottom);
            g.DrawLine(text, left, top, left, bottom);
            var xTitleSize = g.MeasureString(font, _xTitle);
            g.DrawText(font, text, (left + right) / 2 - xTitleSize.Width / 2, height - xTitleSize.Height - 2, _xTitle);
            g.DrawText(font, text, 4, 3, _yTitle);

            foreach (var s in _series)
            {
                using (var pen = new Pen(s.Color, 2f))
                {
                    for (var i = 1; i < s.X.Length; i++)
                    {
                        if (double.IsNaN(s.Y[i - 1]) || double.IsNaN(s.Y[i])) continue;
                        g.DrawLine(pen, px(s.X[i - 1]), py(s.Y[i - 1]), px(s.X[i]), py(s.Y[i]));
                    }
                }
                if (s.Markers)
                    for (var i = 0; i < s.X.Length; i++)
                        if (!double.IsNaN(s.Y[i])) g.FillEllipse(s.Color, px(s.X[i]) - 2.5f, py(s.Y[i]) - 2.5f, 5, 5);
            }

            var legendX = left + 8;
            var legendY = top + 4;
            foreach (var s in _series)
            {
                if (string.IsNullOrEmpty(s.Name)) continue;
                g.FillRectangle(s.Color, legendX, legendY + 4, 12, 3);
                g.DrawText(font, text, legendX + 16, legendY - 1, s.Name);
                legendY += g.MeasureString(font, s.Name).Height + 2;
            }
        }
    }
}

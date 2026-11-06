using System;
using Eto.Drawing;
using Eto.Forms;

namespace HullOpt
{
    /// <summary>
    /// Small building blocks shared by the panel pages. A label that shares a TableLayout row with other cells stays
    /// on one line (Field, Unit, Hint): on WPF a wrapping label there can keep the layout looping and stall Rhino.
    /// A Note wraps and goes in a row of its own.
    /// </summary>
    public static class Ui
    {
        public static Label Heading(string text)
        {
            return new Label { Text = text, Font = SystemFonts.Bold() };
        }

        public static Label Note(string text)
        {
            return new Label { Text = text, Wrap = WrapMode.Word, TextColor = SystemColors.DisabledText };
        }

        /// <summary>A short grey note on one line, for a table cell.</summary>
        public static Label Hint(string text)
        {
            return new Label
            {
                Text = text, Wrap = WrapMode.None, TextColor = SystemColors.DisabledText,
                VerticalAlignment = VerticalAlignment.Center,
            };
        }

        public static Label Unit(string text)
        {
            return new Label { Text = text, Wrap = WrapMode.None, VerticalAlignment = VerticalAlignment.Center };
        }

        public static Label Field(string text)
        {
            return new Label { Text = text, Wrap = WrapMode.None, VerticalAlignment = VerticalAlignment.Center };
        }

        public static DropDown Choices(params string[] keyTextPairs)
        {
            var dropDown = new DropDown();
            for (var i = 0; i + 1 < keyTextPairs.Length; i += 2)
                dropDown.Items.Add(new ListItem { Key = keyTextPairs[i], Text = keyTextPairs[i + 1] });
            return dropDown;
        }

        public static Scrollable Scroll(Control content)
        {
            return new Scrollable { Content = content, Border = BorderType.None, ExpandContentWidth = true };
        }
    }

    /// <summary>
    /// A text box whose Committed event fires when the user presses Enter or leaves the box after
    /// changing the text, not on every keystroke.
    /// </summary>
    public class CommitBox : TextBox
    {
        private string _committedText = "";

        public event EventHandler<EventArgs> Committed;

        public CommitBox(int width = 80)
        {
            Width = width;
            LostFocus += (sender, e) => Commit();
            KeyDown += (sender, e) =>
            {
                if (e.Key == Keys.Enter) Commit();
            };
        }

        private void Commit()
        {
            if (Text == _committedText) return;
            _committedText = Text;
            var handler = Committed;
            if (handler != null) handler(this, EventArgs.Empty);
        }

        /// <summary>Shows a text unless the user is typing in this box.</summary>
        public void ShowText(string text)
        {
            if (HasFocus) return;
            text = text ?? "";
            if (Text != text) Text = text;
            _committedText = Text;
        }
    }

    /// <summary>A commit box for a number; accepts a decimal point or a decimal comma.</summary>
    public sealed class NumberBox : CommitBox
    {
        public NumberBox(int width = 80) : base(width)
        {
        }

        public double? Value
        {
            get
            {
                double value;
                return HullDefinition.TryParse(Text, out value) ? value : (double?)null;
            }
        }

        /// <summary>Shows a value unless the user is typing in this box.</summary>
        public void Show(double? value, string format = "0.######")
        {
            ShowText(HullDefinition.Format(value, format));
        }
    }
}

using System.Runtime.InteropServices;
using Rhino;
using Rhino.Commands;

namespace HullOpt.Commands
{
    /// <summary>HoPanel: opens the HullOpt panel.</summary>
    [Guid("d51d7713-cee8-4fd1-a2cf-0ddde7e65c6c")]
    public class HoPanelCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoPanel"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            Rhino.UI.Panels.OpenPanel(HullOptPanel.PanelId);
            return Result.Success;
        }
    }
}

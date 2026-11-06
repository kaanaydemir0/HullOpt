using Rhino.PlugIns;

namespace HullOpt
{
    /// <summary>
    /// Plug-in entry point. Loads when Rhino starts, registers the HullOpt panel in Rhino's panel
    /// area and stops the Python engine when Rhino closes.
    /// </summary>
    public class HullOptPlugIn : PlugIn
    {
        public HullOptPlugIn()
        {
            Instance = this;
        }

        public static HullOptPlugIn Instance { get; private set; }

        public override PlugInLoadTime LoadTime
        {
            get { return PlugInLoadTime.AtStartup; }
        }

        protected override LoadReturnCode OnLoad(ref string errorMessage)
        {
            Rhino.UI.Panels.RegisterPanel(this, typeof(HullOptPanel), "HullOpt", System.Drawing.SystemIcons.Application);
            Session.Initialize();
            return LoadReturnCode.Success;
        }

        protected override void OnShutdown()
        {
            EngineClient.ShutdownShared();
            base.OnShutdown();
        }
    }
}

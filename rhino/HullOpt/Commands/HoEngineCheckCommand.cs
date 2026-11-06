using System;
using System.Runtime.InteropServices;
using Rhino;
using Rhino.Commands;

namespace HullOpt.Commands
{
    /// <summary>HoEngineCheck: starts the Python engine service and prints its version.</summary>
    [Guid("7388f5e2-3ffb-48ab-b028-edfc9ea3b60a")]
    public class HoEngineCheckCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoEngineCheck"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            return Check();
        }

        public static Result Check()
        {
            RhinoApp.WriteLine("HullOpt: checking the engine...");
            var client = EngineClient.Shared;
            var start = client.StartAsync();
            try
            {
                if (!start.Wait(TimeSpan.FromSeconds(90)))
                {
                    RhinoApp.WriteLine("HullOpt: the engine did not answer within 90 s.");
                    return Result.Failure;
                }
            }
            catch (AggregateException ex)
            {
                RhinoApp.WriteLine("HullOpt: engine check failed: " + ex.InnerException.Message);
                return Result.Failure;
            }
            var info = start.Result;
            RhinoApp.WriteLine("Engine is running.");
            RhinoApp.WriteLine("Project folder: " + client.ProjectFolder);
            RhinoApp.WriteLine("Python: " + J.Str(info, "executable"));
            RhinoApp.WriteLine("Engine reply: hullopt " + J.Str(info, "version") + " / Python " + J.Str(info, "python") +
                               ", protocol " + J.Str(info, "protocol"));
            return Result.Success;
        }
    }
}

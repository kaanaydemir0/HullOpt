using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using Rhino;
using Rhino.Commands;
using Rhino.DocObjects;
using Rhino.Input;
using Rhino.Input.Custom;

namespace HullOpt.Commands
{
    /// <summary>Shared object picking for HoHull and HoAppendages.</summary>
    internal static class Picking
    {
        public static Result Run(RhinoDoc doc, bool appendages)
        {
            var definition = Session.Definition(doc);
            var stored = appendages ? definition.AppendageObjects : definition.HullObjects;
            var what = appendages ? "appendage" : "hull";
            var get = new GetObject();
            get.SetCommandPrompt("Select the " + what + " surfaces and polysurfaces");
            get.GeometryFilter = ObjectType.Surface | ObjectType.PolysrfFilter | ObjectType.Extrusion;
            get.SubObjectSelect = false;
            get.GroupSelect = true;
            get.EnablePreSelect(true, true);
            var show = get.AddOption("Show");
            var result = get.GetMultiple(1, 0);
            if (result == GetResult.Option && get.OptionIndex() == show)
            {
                doc.Objects.UnselectAll();
                var count = 0;
                foreach (var text in stored)
                {
                    Guid id;
                    if (Guid.TryParse(text, out id) && doc.Objects.Select(id)) count++;
                }
                doc.Views.Redraw();
                RhinoApp.WriteLine("HullOpt: {0} {1} object(s) selected.", count, what);
                return Result.Success;
            }
            if (result != GetResult.Object) return get.CommandResult();
            var chosen = new List<string>();
            for (var i = 0; i < get.ObjectCount; i++)
            {
                var id = get.Object(i).ObjectId.ToString();
                if (!chosen.Contains(id)) chosen.Add(id);
            }
            stored.Clear();
            stored.AddRange(chosen);
            if (appendages) definition.IncludeAppendages = chosen.Count > 0;
            Session.Save(doc, definition);
            doc.Objects.UnselectAll();
            doc.Views.Redraw();
            RhinoApp.WriteLine("HullOpt: {0}: {1}.", appendages ? "appendages" : "hull", Session.Describe(doc, stored));
            Session.RequestTab("Hull");
            return Result.Success;
        }
    }

    /// <summary>HoHull: chooses the surfaces that form the hull. Option Show selects the current choice.</summary>
    [Guid("3cd2b84c-7b9f-42b2-a87f-d1db865a137c")]
    public class HoHullCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoHull"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            return Picking.Run(doc, false);
        }
    }

    /// <summary>HoAppendages: chooses appendage bodies (skeg, keel, rudder).</summary>
    [Guid("9c8069b0-a682-48dd-9690-6f438ea761d3")]
    public class HoAppendagesCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoAppendages"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            return Picking.Run(doc, true);
        }
    }

    /// <summary>HoCondition: shows the floating condition and speeds in the panel.</summary>
    [Guid("7417d770-055e-4b98-8147-0297233d55fd")]
    public class HoConditionCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoCondition"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            var d = Session.Definition(doc);
            var floating = d.FloatMode == HullDefinition.FloatByDisplacement
                ? "displacement " + HullDefinition.Format(d.DisplacementTonnes) + " t"
                : "waterline z = " + HullDefinition.Format(d.WaterlineZ) + " " + Session.UnitName(doc);
            string problem;
            var speeds = d.Speeds(out problem);
            RhinoApp.WriteLine(string.Format(CultureInfo.InvariantCulture,
                "HullOpt: {0}; rho = {1:0.###} kg/m3; speeds {2:0.###}-{3:0.###} kn by {4:0.###}{5}. {6}",
                floating, d.Rho, d.SpeedFrom, d.SpeedTo, d.SpeedStep,
                string.IsNullOrWhiteSpace(d.SpeedList) ? "" : ", also at " + d.SpeedList,
                problem ?? HullDefinition.Describe(speeds)));
            Session.RequestTab("Condition");
            return Result.Success;
        }
    }

    /// <summary>HoHydrostatics: hydrostatics of the chosen hull at the chosen condition.</summary>
    [Guid("e74ba006-15fb-4833-b982-334f9105e506")]
    public class HoHydrostaticsCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoHydrostatics"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            Session.RequestTab("Hydrostatics");
            Session.RunHydrostatics(doc);
            return Result.Success;
        }
    }

    /// <summary>HoResistance: resistance over the speed range with the chosen methods.</summary>
    [Guid("d48758c1-f630-4308-b725-5ebf722180a9")]
    public class HoResistanceCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoResistance"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            Session.RequestTab("Resistance");
            Session.RunResistance(doc);
            return Result.Success;
        }
    }

    /// <summary>HoSetup: starts the engine, reports where everything is and opens the panel.</summary>
    [Guid("c21d22f0-a101-4d6a-bb8b-b053b5bcc4b1")]
    public class HoSetupCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoSetup"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            var result = HoEngineCheckCommand.Check();
            RhinoApp.WriteLine("HullOpt: work folder {0}", Session.WorkFolder(doc));
            Session.RequestTab("Hull");
            return result;
        }
    }

    /// <summary>HoPropulsion: resistance and brake power with the Propulsion tab's propeller and machinery.</summary>
    [Guid("141b7b72-d978-44df-aa50-d835bd623d02")]
    public class HoPropulsionCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoPropulsion"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            Session.RequestTab("Propulsion");
            Session.RunResistance(doc, true);
            return Result.Success;
        }
    }

    /// <summary>
    /// HoOptimise: searches the shape modes with the Optimise tab's objective, bounds and search settings;
    /// Cancel (or the tab's Stop) ends the search early and keeps the best candidates found.
    /// </summary>
    [Guid("62e8c75a-a670-48ef-a694-a68ea2e05e8d")]
    public class HoOptimiseCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoOptimise"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            Session.RequestTab("Optimise");
            Session.RunOptimise(doc);
            return Result.Success;
        }
    }

    /// <summary>HoMorph: evaluates the Morph tab's candidate; option Build adds it to the model.</summary>
    [Guid("09318f81-d3a9-425b-8d83-0e613358515b")]
    public class HoMorphCommand : Command
    {
        public override string EnglishName
        {
            get { return "HoMorph"; }
        }

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            var get = new Rhino.Input.Custom.GetOption();
            get.SetCommandPrompt("HullOpt shape modes. Press Enter to evaluate");
            var evaluate = get.AddOption("Evaluate");
            var build = get.AddOption("Build");
            get.AcceptNothing(true);
            var answer = get.Get();
            if (answer != Rhino.Input.GetResult.Option && answer != Rhino.Input.GetResult.Nothing) return Result.Cancel;
            Session.RequestTab("Morph");
            if (answer == Rhino.Input.GetResult.Option && get.OptionIndex() == build) Session.BuildCandidate(doc);
            else Session.RunMorph(doc);
            return Result.Success;
        }
    }
}

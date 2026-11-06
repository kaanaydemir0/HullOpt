"""HULLOPT - hull form resistance optimisation engine (Rhino plug-in back end).

General purpose: hull-specific values live in study files, not in the defaults.

Layers:
    config      study definition (YAML -> validated model)
    geometry    neutral NURBS geometry, evaluation, trimmed tessellation
    hydro       hydrostatics by the divergence theorem, sectional areas, equilibrium waterline
    resistance  backends behind one interface (each result carries its backend label)
    propulsion  B-series propeller, propulsion factors, margins and brake power
    optimize    shape modes, mode frame from the hull, fairness gates, constrained search (optimize.run)
    morph       shape-mode candidates: build, float at the baseline volume, measure, check
    evaluation  candidate records and states
    store       SQLite results store
"""

__version__ = "0.6.0"

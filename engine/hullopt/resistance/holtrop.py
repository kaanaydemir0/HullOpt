"""Holtrop (1984) calm-water regression with explicit physical inputs.

1984 replaces the 1982 form factor and wave hump/hollow term, adds a
high-Froude branch, and retains the 1982 bulb/transom/appendage/CA formulae.
The original scans and transcription decisions are indexed in
validation/sources/HOLTROP_SOURCES.md. This is not a Maxsurf implementation.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .base import Conditions, RawResult
from .friction import KNOT_TO_M_S

STANDARD_ROUGHNESS_M = 150e-6
GRAVITY_M_S2 = 9.80665
SOURCE_URL = (
    "https://moodle2.units.it/pluginfile.php/493107/mod_resource/content/0/"
    "Holtrop%20calcolo%20statistico%20resistenza.pdf"
)


def _get(measurements: Any, name: str, default: Any = None) -> Any:
    return measurements.get(name, default) if isinstance(measurements, dict) else getattr(measurements, name, default)


def _number(measurements: Any, name: str, default: Any = None) -> float:
    value = _get(measurements, name, default)
    if value is None:
        raise ValueError(f"Holtrop requires measured {name}; include sectional measurements")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Holtrop requires numeric {name}") from exc


def _finite(name: str, value: float, *, positive: bool = False, nonnegative: bool = False) -> None:
    if not math.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        qualifier = "finite and positive" if positive else "finite and nonnegative" if nonnegative else "finite"
        raise ValueError(f"{name} must be {qualifier}")


@dataclass(frozen=True)
class HoltropAppendage:
    """User supplied wetted area and complete (1+k2), not k2 alone.

    reference_length_m=None uses the hull CF, as in Holtrop-Mennen 1982. A
    length gives the appendage its own ITTC-57 CF at Rn = V*length/nu, the
    convention of Maxsurf and of the reference resistance workbook (C_F,app from L_app).
    """

    name: str
    wetted_area_m2: float
    form_factor_1_plus_k2: float
    reference_length_m: float | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Appendage name is required")
        _finite("appendage wetted_area_m2", self.wetted_area_m2, nonnegative=True)
        _finite("appendage form_factor_1_plus_k2", self.form_factor_1_plus_k2, positive=True)
        if self.form_factor_1_plus_k2 < 1:
            raise ValueError("Appendage form_factor_1_plus_k2 must be at least 1")
        if self.reference_length_m is not None:
            _finite("appendage reference_length_m", self.reference_length_m, positive=True)


@dataclass(frozen=True)
class HoltropParameters:
    """SI inputs; LCB is percent LWL forward of the reference point.

    The Holtrop reference is the LWL midpoint (0.5L); from_measurements can
    use amidships between AP and FP instead (Maxsurf/workbook convention).

    draft_m is average moulded draft T; forward_draft_m is TF. Measured
    Hydrostatics.draft_m is a lowest-vertex draft, so its use is labelled as
    a proxy. cm is the true midship coefficient when supplied directly.
    """

    lwl_m: float
    bwl_m: float
    draft_m: float
    volume_m3: float
    wetted_area_m2: float
    cp: float
    cm: float
    cwp: float
    lcb_percent_lwl: float
    entrance_half_angle_deg: float | None = None
    transom_area_m2: float = 0.0
    transom_wetted_area_m2: float = 0.0
    bulb_area_m2: float = 0.0
    bulb_height_m: float = 0.0
    stern_parameter: float = 0.0
    roughness_m: float = STANDARD_ROUGHNESS_M
    forward_draft_m: float | None = None
    aft_draft_m: float | None = None
    appendages: tuple[HoltropAppendage, ...] = ()
    input_notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("lwl_m", "bwl_m", "draft_m", "volume_m3", "wetted_area_m2"):
            _finite(name, getattr(self, name), positive=True)
        for name in ("cp", "cm", "cwp"):
            value = getattr(self, name)
            if not math.isfinite(value) or not (0 < value < 1 or (name == "cm" and value == 1)):
                raise ValueError(f"{name} must lie between 0 and 1 (only Cm may equal 1)")
        for name in ("transom_area_m2", "transom_wetted_area_m2", "bulb_area_m2", "bulb_height_m"):
            _finite(name, getattr(self, name), nonnegative=True)
        if self.transom_wetted_area_m2 > self.wetted_area_m2:
            raise ValueError("Transom wetted area cannot exceed the measured hull wetted area")
        for name in ("lcb_percent_lwl", "stern_parameter"):
            _finite(name, getattr(self, name))
        if 1 + 0.011 * self.stern_parameter <= 0:
            raise ValueError("stern_parameter gives a nonpositive c14")
        if self.roughness_m < STANDARD_ROUGHNESS_M or not math.isfinite(self.roughness_m):
            raise ValueError("roughness_m must be >= 150e-6 m; the paper only defines an increase above this standard")
        for name in ("forward_draft_m", "aft_draft_m"):
            value = getattr(self, name)
            if value is not None:
                _finite(name, value, positive=True)
        if self.forward_draft_m is not None and self.aft_draft_m is not None:
            average = 0.5 * (self.forward_draft_m + self.aft_draft_m)
            if not math.isclose(average, self.draft_m, rel_tol=1e-8, abs_tol=1e-9):
                raise ValueError("draft_m must equal average forward/aft moulded drafts")
        if self.entrance_half_angle_deg is not None:
            if not math.isfinite(self.entrance_half_angle_deg) or not 0 <= self.entrance_half_angle_deg < 90:
                raise ValueError("entrance_half_angle_deg must be >= 0 and < 90 degrees")
        if self.lwl_m / self.bwl_m <= 2:
            raise ValueError("Holtrop high-speed formula requires L/B > 2")
        if self.cp <= 0.25 or self.run_length_m <= 0:
            raise ValueError("Cp/LCB gives a nonpositive or undefined run length LR")
        if self.wave_transom_factor < 0:
            raise ValueError("Transom area gives negative c5; empirical model is outside its mathematical domain")
        if self.bulb_area_m2:
            if self.bulb_height_m > 0.6 * self.tf_m:
                raise ValueError("bulb_height_m must not exceed 0.6 forward draft (1984 recommendation)")
            if self.tf_m - self.bulb_height_m - 0.25 * math.sqrt(self.bulb_area_m2) <= 0:
                raise ValueError("Bulb emergence gives nonpositive static immersion in Fni")
        elif self.bulb_height_m:
            raise ValueError("Nonzero bulb_height_m requires nonzero bulb_area_m2")
        for appendage in self.appendages:
            if not isinstance(appendage, HoltropAppendage):
                raise ValueError("appendages must contain HoltropAppendage instances")

    @property
    def tf_m(self) -> float:
        return self.draft_m if self.forward_draft_m is None else self.forward_draft_m

    @property
    def run_length_m(self) -> float:
        if self.cp == 0.25:
            return math.nan
        return self.lwl_m * (1 - self.cp + 0.06 * self.cp * self.lcb_percent_lwl / (4 * self.cp - 1))

    @property
    def wave_transom_factor(self) -> float:
        return 1 - 0.8 * self.transom_area_m2 / (self.bwl_m * self.draft_m * self.cm)

    @classmethod
    def from_measurements(
        cls, measurements: Any, *, bulb_area_m2: float = 0.0,
        bulb_height_m: float = 0.0, stern_parameter: float = 0.0,
        roughness_m: float = STANDARD_ROUGHNESS_M,
        forward_draft_m: float | None = None, aft_draft_m: float | None = None,
        appendages: tuple[HoltropAppendage, ...] = (),
        entrance_half_angle_deg: float | None = None, midship_coefficient: float | None = None,
        entrance_angle_source: str = "", lcb_reference_x_m: float | None = None,
    ) -> "HoltropParameters":
        """Map measured hydrostatics to Holtrop inputs, with every proxy noted.

        lcb_reference_x_m=None keeps Holtrop's reference, the LWL midpoint
        (0.5L). A value measures LCB from that x instead, e.g. amidships
        between AP and FP as Maxsurf does.
        entrance_half_angle_deg overrides the measured waterline angle.
        """
        notes = [
            "Measured BWL is used as moulded breadth B; verify the definition against the intended resistance model.",
            "Measured lowest-immersed-vertex draft is used as average moulded draft T; verify this proxy against Maxsurf.",
            "Measured transom area is an aft-normal projected-area heuristic, pending external validation.",
        ]
        if midship_coefficient is None:
            notes.append("Cm uses the measured maximum-section coefficient Cx, not an independently located midship section.")
        if forward_draft_m is None:
            notes.append("Forward draft TF is assumed equal to T (zero static trim and common moulded baseline).")
        if entrance_half_angle_deg is not None:
            notes.append(f"Entrance half angle is a supplied value ({entrance_half_angle_deg:g} deg; source: "
                         f"{entrance_angle_source or 'not stated'}); it does not follow candidate shape changes.")
        if lcb_reference_x_m is not None:
            _finite("lcb_reference_x_m", float(lcb_reference_x_m))
            notes.append(f"LCB is measured from x = {lcb_reference_x_m:g} m (amidships between AP and FP), "
                         "not from the LWL midpoint of the Holtrop papers; Maxsurf/workbook convention.")

        def lcb_percent() -> float:
            if lcb_reference_x_m is None:
                return _number(measurements, "lcb_percent_lwl")
            return 100 * (_number(measurements, "lcb_m") - lcb_reference_x_m) / _number(measurements, "lwl_m")

        return cls(
            lwl_m=_number(measurements, "lwl_m"), bwl_m=_number(measurements, "bwl_m"),
            draft_m=_number(measurements, "draft_m"), volume_m3=_number(measurements, "volume_m3"),
            wetted_area_m2=_number(measurements, "wetted_area_m2"), cp=_number(measurements, "cp"),
            cm=_number(measurements, "cx") if midship_coefficient is None else midship_coefficient,
            cwp=_number(measurements, "cwp"), lcb_percent_lwl=lcb_percent(),
            entrance_half_angle_deg=(_get(measurements, "entrance_half_angle_deg")
                                     if entrance_half_angle_deg is None else entrance_half_angle_deg),
            transom_area_m2=_number(measurements, "transom_area_m2"),
            transom_wetted_area_m2=_number(measurements, "transom_wetted_area_m2"),
            bulb_area_m2=bulb_area_m2, bulb_height_m=bulb_height_m, stern_parameter=stern_parameter,
            roughness_m=roughness_m, forward_draft_m=forward_draft_m, aft_draft_m=aft_draft_m,
            appendages=tuple(appendages), input_notes=tuple(notes),
        )


def form_factor_1984(parameters: HoltropParameters) -> float:
    """Return the complete 1+k1 from the 1984 paper, section 2."""
    p = parameters
    c14 = 1 + 0.011 * p.stern_parameter
    return (0.93 + 0.487118 * c14 * (p.bwl_m / p.lwl_m) ** 1.06806
            * (p.draft_m / p.lwl_m) ** 0.46106 * (p.lwl_m / p.run_length_m) ** 0.121563
            * (p.lwl_m**3 / p.volume_m3) ** 0.36486 * (1 - p.cp) ** -0.604247)


def _coefficients(p: HoltropParameters, angle_policy: str) -> dict[str, float]:
    length, beam, draft, volume = p.lwl_m, p.bwl_m, p.draft_m, p.volume_m3
    if angle_policy in ("measured", "given"):
        if p.entrance_half_angle_deg is None:
            raise ValueError(f"{angle_policy.capitalize()} entrance angle policy requires entrance_half_angle_deg")
        ie = p.entrance_half_angle_deg
    else:
        fullness = 1 - p.cp - 0.0225 * p.lcb_percent_lwl
        if fullness <= 0:
            raise ValueError("Entrance-angle regression requires 1-Cp-0.0225*lcb > 0")
        ie = 1 + 89 * math.exp(-(length / beam) ** 0.80856 * (1 - p.cwp) ** 0.30484
                               * fullness ** 0.6367 * (p.run_length_m / beam) ** 0.34574
                               * (100 * volume / length**3) ** 0.16302)
    if ie >= 90:
        raise ValueError("Entrance-angle regression is numerically singular at 90 degrees")
    ratio = beam / length
    c7 = 0.229577 * ratio ** (1 / 3) if ratio < 0.11 else ratio if ratio <= 0.25 else 0.5 - 0.0625 / ratio
    c1 = 2223105 * c7 ** 3.78613 * (draft / beam) ** 1.07961 * (90 - ie) ** -1.37565
    c3 = (0.56 * p.bulb_area_m2 ** 1.5
          / (beam * draft * (0.31 * math.sqrt(p.bulb_area_m2) + p.tf_m - p.bulb_height_m))) if p.bulb_area_m2 else 0.0
    c2 = math.exp(-1.89 * math.sqrt(c3))
    slenderness3 = length**3 / volume
    if slenderness3 < 512:
        c15 = -1.69385
    elif slenderness3 > 1726.91:
        c15 = 0.0
    else:
        c15 = -1.69385 + (length / volume ** (1 / 3) - 8) / 2.36
    c16 = (8.07981 * p.cp - 13.8673 * p.cp**2 + 6.984388 * p.cp**3
           if p.cp < 0.8 else 1.73014 - 0.7067 * p.cp)
    m1 = 0.0140407 * length / draft - 1.75254 * volume ** (1 / 3) / length - 4.79323 * beam / length - c16
    c17 = 6919.3 * p.cm ** -1.3346 * (volume / length**3) ** 2.00977 * (length / beam - 2) ** 1.40692
    m3 = -7.2035 * (beam / length) ** 0.326869 * (draft / beam) ** 0.605375
    lam = 1.446 * p.cp - (0.03 * length / beam if length / beam < 12 else 0.36)
    c4 = min(p.tf_m / length, 0.04)
    cb = volume / (length * beam * draft)
    ca = 0.006 * (length + 100) ** -0.16 - 0.00205 + 0.003 * math.sqrt(length / 7.5) * cb**4 * c2 * (0.04 - c4)
    roughness_increment = 0.0
    if p.roughness_m > STANDARD_ROUGHNESS_M:
        roughness_increment = (0.105 * p.roughness_m ** (1 / 3) - 0.005579) / length ** (1 / 3)
        ca += roughness_increment
    return {"entrance_half_angle_deg": ie, "run_length_m": p.run_length_m,
            "form_factor_1_plus_k1": form_factor_1984(p), "cb": cb,
            "c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": p.wave_transom_factor,
            "c7": c7, "c15": c15, "c16": c16, "c17": c17,
            "m1": m1, "m3": m3, "lambda": lam, "CA": ca,
            "roughness_CA_increment": roughness_increment}


def _wave(fn: float, p: HoltropParameters, c: dict[str, float], rho: float, gravity: float) -> tuple[float, str]:
    def branch(at: float, high: bool) -> float:
        m4 = 0.4 * c["c15"] * math.exp(-0.034 * at ** -3.29)
        exponent = (c["m3"] if high else c["m1"]) * at ** -0.9 + m4 * math.cos(c["lambda"] * at ** -2)
        return (c["c17"] if high else c["c1"]) * c["c2"] * c["c5"] * p.volume_m3 * rho * gravity * math.exp(exponent)
    if fn <= 0.4:
        return branch(fn, False), "low_Fn_1984"
    if fn >= 0.55:
        return branch(fn, True), "high_Fn_1984"
    low, high = branch(0.4, False), branch(0.55, True)
    return low + (10 * fn - 4) * (high - low) / 1.5, "linear_0.4_to_0.55"


class Holtrop1984:
    name = "PY-HOLTROP-1984"

    def __init__(self, parameters: HoltropParameters | None = None, *, include_transom: bool = True,
                 gravity_m_s2: float = GRAVITY_M_S2, entrance_angle_policy: str = "regression",
                 bulb_area_m2: float = 0.0, bulb_height_m: float = 0.0,
                 stern_parameter: float = 0.0, roughness_m: float = STANDARD_ROUGHNESS_M,
                 forward_draft_m: float | None = None, aft_draft_m: float | None = None,
                 appendages: tuple[HoltropAppendage, ...] = (),
                 entrance_half_angle_deg: float | None = None, entrance_angle_source: str = "",
                 lcb_reference_x_m: float | None = None):
        """entrance_angle_policy: 'regression' (Holtrop's iE formula), 'measured'
        (waterline fit from the measurements) or 'given' (entrance_half_angle_deg,
        e.g. a Maxsurf value; fixed, so it does not follow candidate shapes).
        lcb_reference_x_m: see HoltropParameters.from_measurements."""
        _finite("gravity_m_s2", gravity_m_s2, positive=True)
        if entrance_angle_policy not in ("regression", "measured", "given"):
            raise ValueError("entrance_angle_policy must be 'regression', 'measured' or 'given'")
        if entrance_angle_policy == "given":
            if parameters is None and entrance_half_angle_deg is None:
                raise ValueError("Given entrance angle policy requires entrance_half_angle_deg")
        elif entrance_half_angle_deg is not None:
            raise ValueError("entrance_half_angle_deg is only used with entrance_angle_policy 'given'")
        if parameters is not None and (entrance_half_angle_deg is not None or lcb_reference_x_m is not None):
            raise ValueError("With explicit parameters, set the entrance angle and LCB in HoltropParameters")
        self.parameters = parameters
        self.include_transom = bool(include_transom)
        self.gravity_m_s2 = gravity_m_s2
        self.entrance_angle_policy = entrance_angle_policy
        self.entrance_angle_source = entrance_angle_source
        self.parameter_options = dict(bulb_area_m2=bulb_area_m2, bulb_height_m=bulb_height_m,
                                      stern_parameter=stern_parameter, roughness_m=roughness_m,
                                      forward_draft_m=forward_draft_m, aft_draft_m=aft_draft_m,
                                      appendages=tuple(appendages),
                                      entrance_half_angle_deg=entrance_half_angle_deg,
                                      entrance_angle_source=entrance_angle_source,
                                      lcb_reference_x_m=lcb_reference_x_m)

    def evaluate(self, candidate_measurements: Any, conditions: Conditions) -> RawResult:
        p = self.parameters or HoltropParameters.from_measurements(candidate_measurements, **self.parameter_options)
        c = _coefficients(p, self.entrance_angle_policy)
        area = p.wetted_area_m2 - (0 if self.include_transom else p.transom_wetted_area_m2)
        if area <= 0:
            raise ValueError("Positive hull reference area is required after transom subtraction")
        warnings = list(p.input_notes)
        warnings += [
            "Empirical Holtrop 1984 prediction; external Maxsurf/towing-tank trend validation remains pending.",
            "Deep, unrestricted calm water; zero dynamic trim/sinkage corrections. CA already includes empirical roughness and still-air correlation effects.",
            "Bulb area/height, stern parameter and roughness are supplied modelling inputs, not inferred from the surface model.",
        ]
        if not p.bulb_area_m2:
            warnings.append("No bulb specified: ABT=0, c2=1 and RB=0 are explicit modelling assumptions.")
        if not p.appendages:
            warnings.append("No separate appendages specified: RAPP=0. Selected hull measurements must exclude appendages to represent bare-hull resistance.")
        if self.entrance_angle_policy == "measured" and p.input_notes:
            warnings.append("Entrance angle is the measured forward-10%-LWL waterline fit; it matched Maxsurf within 0.2 deg on one test hull and is not yet checked on others.")
        if any(a.reference_length_m is not None for a in p.appendages):
            warnings.append("Some appendages use their own length for CF (workbook convention); Holtrop-Mennen 1982 uses the hull CF.")
        if p.stern_parameter == 0:
            warnings.append("Cstern=0 selects the paper's normal afterbody category; this classification requires an explicit engineering check.")
        if not 0.55 < p.cp < 0.85:
            warnings.append("Cp is outside the Maxsurf manual Appendix B Holtrop screening interval (0.55, 0.85).")
        if not 3.9 < p.lwl_m / p.bwl_m < 15:
            warnings.append("L/B is outside the Maxsurf manual Appendix B Holtrop screening interval (3.9, 15).")
        if not 2.1 < p.bwl_m / p.draft_m < 4:
            warnings.append("B/T is outside the Maxsurf manual Appendix B Holtrop screening interval (2.1, 4.0).")
        if not -25 <= p.stern_parameter <= 10:
            warnings.append("Cstern is outside the 1984 paper's tentative afterbody categories [-25, 10].")
        if c["CA"] < 0:
            warnings.append("Correlation allowance CA is negative at these inputs; retained as published, without silent clamping.")
        components = {key: [] for key in ("RF", "Rform", "RW", "RTR", "RB", "RA", "RAPP")}
        reynolds, froude, transom_froude, friction_coefficient, branches = [], [], [], [], []
        totals = []
        for speed_kn in conditions.speeds_kn:
            velocity = speed_kn * KNOT_TO_M_S
            rn = velocity * p.lwl_m / conditions.nu_m2_s
            if rn <= 1e5:
                raise ValueError("Holtrop ITTC57 turbulent friction requires Reynolds number above 1e5")
            fn = velocity / math.sqrt(self.gravity_m_s2 * p.lwl_m)
            cf = 0.075 / (math.log10(rn) - 2) ** 2
            q = 0.5 * conditions.rho_kg_m3 * velocity**2
            rf = q * area * cf
            rw, branch_name = _wave(fn, p, c, conditions.rho_kg_m3, self.gravity_m_s2)
            fnt, rtr = None, 0.0
            if p.transom_area_m2:
                fnt = velocity / math.sqrt(2 * self.gravity_m_s2 * p.transom_area_m2 / (p.bwl_m * (1 + p.cwp)))
                rtr = q * p.transom_area_m2 * (0.2 * (1 - 0.2 * fnt) if fnt < 5 else 0.0)
            rb = 0.0
            if p.bulb_area_m2:
                pb = 0.56 * math.sqrt(p.bulb_area_m2) / (p.tf_m - 1.5 * p.bulb_height_m)
                fni = velocity / math.sqrt(self.gravity_m_s2 * (p.tf_m - p.bulb_height_m - 0.25 * math.sqrt(p.bulb_area_m2)) + 0.15 * velocity**2)
                rb = (0.11 * math.exp(-3 / pb**2) * fni**3 * p.bulb_area_m2**1.5
                      * conditions.rho_kg_m3 * self.gravity_m_s2 / (1 + fni**2))
            rapp = q * cf * sum(a.wetted_area_m2 * a.form_factor_1_plus_k2
                                for a in p.appendages if a.reference_length_m is None)
            for a in p.appendages:
                if a.reference_length_m is not None:
                    rn_app = velocity * a.reference_length_m / conditions.nu_m2_s
                    if rn_app <= 1e5:
                        raise ValueError(f"Appendage {a.name!r}: ITTC57 friction requires Reynolds number above 1e5")
                    cf_app = 0.075 / (math.log10(rn_app) - 2) ** 2
                    rapp += q * cf_app * a.wetted_area_m2 * a.form_factor_1_plus_k2
            row = dict(RF=rf, Rform=rf * (c["form_factor_1_plus_k1"] - 1), RW=rw,
                       RTR=rtr, RB=rb, RA=q * area * c["CA"], RAPP=rapp)
            if any(not math.isfinite(v) for v in row.values()):
                raise ValueError("Holtrop empirical equations produced a nonfinite component")
            for key, value in row.items():
                components[key].append(value)
            totals.append(sum(row.values()))
            reynolds.append(rn)
            froude.append(fn)
            transom_froude.append(fnt)
            friction_coefficient.append(cf)
            branches.append(branch_name)
        if max(froude) > 0.8:
            warnings.append("Fn exceeds 0.8, the Maxsurf manual's published Holtrop speed screening limit; extrapolation.")
        return RawResult(
            backend=self.name, label="Holtrop 1984 empirical total resistance (explicit hull/appendage assumptions)",
            quantity="total_resistance", is_physical_prediction=True, speeds_kn=conditions.speeds_kn,
            values=tuple(totals), units="N", warnings=tuple(warnings),
            metadata={
                "method": "Holtrop 1984 with explicitly retained Holtrop-Mennen 1982 components",
                "method_provenance": {"primary_scan": SOURCE_URL, "form_factor": "1984 section 2",
                                      "wave": "1984 section 2; low Fn<=0.4, interpolation to 0.55, high Fn>=0.55",
                                      "bulb_transom_appendage_CA": "1982 section 2, retained by 1984"},
                "components_N": components, "parameters": asdict(p), "coefficients": c,
                "component_definitions": {"RF": "ITTC57 skin friction", "Rform": "k1*RF (increment only)",
                                          "RW": "Holtrop wave making/breaking", "RTR": "transom pressure",
                                          "RB": "bulb near-surface pressure", "RA": "model-ship correlation allowance",
                                          "RAPP": "sum of specified appendage contributions"},
                "component_flags": {"bulb_specified": p.bulb_area_m2 > 0, "appendages_specified": bool(p.appendages),
                                    "transom_specified": p.transom_area_m2 > 0, "correlation_allowance_included": True},
                "include_transom": self.include_transom, "friction_area_m2": area,
                "gravity_m_s2": self.gravity_m_s2, "knot_to_m_s": KNOT_TO_M_S,
                "entrance_angle_policy": self.entrance_angle_policy,
                "entrance_angle_source": self.entrance_angle_source if self.entrance_angle_policy == "given" else "",
                "reynolds_number": reynolds, "froude_number": froude, "transom_froude_number": transom_froude,
                "friction_coefficient": friction_coefficient, "wave_branch": branches,
                "water": {"rho_kg_m3": conditions.rho_kg_m3, "nu_m2_s": conditions.nu_m2_s},
            },
        )

"""Validated study inputs. Every relative file path is relative to the YAML file."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BaselineConfig(StrictModel):
    geometry_dump: Path
    source_3dm: Path
    source_sha256: str
    hull_layers: list[str] = Field(default_factory=lambda: ["HULL"], min_length=1)
    appendage_layers: list[str] = Field(default_factory=list)

    @field_validator("source_sha256")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("source_sha256 must be a 64-character SHA-256 digest")
        return value


class ImmersionConfig(StrictModel):
    reference_z_wl_m: float
    policy: Literal["fixed_displacement"] = "fixed_displacement"
    displacement_tolerance_rel: float = Field(default=0.001, gt=0, lt=1)
    draft_change_limit_m: float = Field(default=0.05, gt=0)
    trim_deg: Literal[0.0] = 0.0
    status: str = "user_supplied"


class WaterConfig(StrictModel):
    rho_kg_m3: float = Field(default=1025.0, gt=0)
    nu_m2_s: float = Field(default=1.18831e-6, gt=0)
    status: str = "proposed_default"


class ChecksConfig(StrictModel):
    """Optional clearance heights in model z; None skips that check.

    The waterline must stay min_clearance_m below each given height, e.g. the
    bottom of an open-deck closure or of a side recess. Hull specific, so
    there are no default heights.
    """

    closure_bottom_z_m: float | None = None
    side_recess_bottom_z_m: float | None = None
    min_clearance_m: float = Field(default=0.10, ge=0)

    @property
    def waterline_ceiling_m(self) -> float | None:
        heights = [h for h in (self.closure_bottom_z_m, self.side_recess_bottom_z_m) if h is not None]
        return min(heights) - self.min_clearance_m if heights else None


class VariableConfig(StrictModel):
    name: str = Field(min_length=1)
    min: float
    max: float

    @model_validator(mode="after")
    def ordered_bounds(self) -> "VariableConfig":
        if self.min >= self.max:
            raise ValueError("variable min must be less than max")
        return self


class TessellationConfig(StrictModel):
    min_grid: int = Field(default=12, ge=2)
    max_grid: int = Field(default=100, ge=2)
    grid_factor: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def ordered_grid(self) -> "TessellationConfig":
        if self.min_grid > self.max_grid:
            raise ValueError("tessellation min_grid must not exceed max_grid")
        return self


class TransformationConfig(StrictModel):
    minimum_map_derivative: float = Field(default=0.15, gt=0, lt=1)
    cp_tolerance_abs: float = Field(default=1e-5, gt=0)
    lcb_tolerance_fraction_lwl: float = Field(default=1e-5, gt=0)
    volume_tolerance_rel: float = Field(default=1e-6, gt=0, lt=1)
    length_tolerance_rel: float = Field(default=0.002, gt=0)
    breadth_tolerance_rel: float = Field(default=0.002, gt=0)
    max_function_evaluations: int = Field(default=45, ge=1)


class HoltropConfig(StrictModel):
    """Holtrop 1984 inputs that are modelling choices, not measurements.

    entrance_angle_policy: 'measured' (default; waterline fit of each hull, so it
    follows shape changes), 'regression' (Holtrop's iE formula, for hulls without
    a usable waterline) or 'given' (entrance_half_angle_deg, labelled by
    entrance_angle_source; fixed for every candidate, for Maxsurf comparisons).
    lcb_reference: 'lwl_midpoint' (Holtrop's 0.5L) or 'amidships', the midpoint
    of x_ap_m and x_fp_m in model coordinates (Maxsurf/workbook convention).
    """

    entrance_angle_policy: Literal["regression", "measured", "given"] = "measured"
    entrance_half_angle_deg: float | None = Field(default=None, ge=0, lt=90)
    entrance_angle_source: str = ""
    lcb_reference: Literal["lwl_midpoint", "amidships"] = "lwl_midpoint"
    x_ap_m: float | None = None
    x_fp_m: float | None = None
    stern_parameter: float = Field(default=0.0, ge=-25, le=10)
    roughness_m: float = Field(default=150e-6, ge=150e-6)
    bulb_area_m2: float = Field(default=0.0, ge=0)
    bulb_height_m: float = Field(default=0.0, ge=0)
    assumptions_status: str = "proposed_normal_stern_150um_roughness_no_bulb_or_appendages"

    @model_validator(mode="after")
    def consistent_choices(self) -> "HoltropConfig":
        if (self.entrance_angle_policy == "given") != (self.entrance_half_angle_deg is not None):
            raise ValueError("entrance_half_angle_deg is required for, and only used with, entrance_angle_policy 'given'")
        if self.lcb_reference == "amidships":
            if self.x_ap_m is None or self.x_fp_m is None:
                raise ValueError("lcb_reference 'amidships' requires x_ap_m and x_fp_m")
            if self.x_fp_m <= self.x_ap_m:
                raise ValueError("x_fp_m must lie forward of x_ap_m")
        elif self.x_ap_m is not None or self.x_fp_m is not None:
            raise ValueError("x_ap_m and x_fp_m are only used with lcb_reference 'amidships'")
        return self

    @property
    def lcb_reference_x_m(self) -> float | None:
        if self.lcb_reference == "lwl_midpoint":
            return None
        return 0.5 * (self.x_ap_m + self.x_fp_m)


class MichellConfig(StrictModel):
    nx: int = Field(default=81, ge=11)
    nz: int = Field(default=41, ge=11)
    symmetry_tolerance: float = Field(default=0.02, gt=0)
    multi_interval_policy: Literal["reject", "equivalent_width"] = "reject"
    bow_padding_fraction_lwl: float = Field(default=0.0, ge=0, le=0.1)
    lambda_max: float = Field(default=32.0, gt=1)
    quadrature_order: int = Field(default=12, ge=4, le=64)
    check_convergence: bool = True
    convergence_tolerance: float = Field(default=0.01, gt=0, lt=1)
    # Adaptive refinement (null: none): at a speed that misses the tolerance the base cut-off doubles
    # while it stays within this value, then the Gauss order doubles (up to 64). The returned integral
    # uses twice the final cut-off and order, so lambda reaches at most twice this value.
    lambda_max_limit: float | None = Field(default=1024.0, gt=1)
    transom_closure_fraction_lwl: float = Field(default=0.05, ge=0)
    closure_status: str = "explicit_heuristic_not_Maxsurf_equivalent; sensitivity_required"

    @model_validator(mode="after")
    def adaptive_limit(self) -> "MichellConfig":
        if self.lambda_max_limit is not None and self.lambda_max_limit < self.lambda_max:
            raise ValueError("lambda_max_limit must be at least lambda_max")
        return self


class PropulsionConfig(StrictModel):
    """Propeller, propulsion factors, margins and machinery for brake power (twin or single screw)."""

    diameter_m: float = Field(gt=0)
    design_speed_kn: float = Field(gt=0)
    shaft_immersion_m: float | None = Field(default=None, gt=0)   # shaft centre below the still waterline
    shaft_z_m: float | None = None                  # or the shaft centre's z; immersion = waterline z - this
    propellers: int = Field(default=2, ge=1, le=4)
    blades: int = Field(default=4, ge=2, le=7)
    pitch_ratio: float | None = Field(default=None, gt=0)   # None: best open-water efficiency at the design point
    area_ratio: float | None = Field(default=None, gt=0)    # None: Keller's minimum, rounded up to 0.05
    keller_constant: float | None = Field(default=None, ge=0)  # None: 0.1 twin screw, 0.2 single screw
    wake: Literal["holtrop", "taylor", "given"] = "holtrop"
    wake_fraction: float | None = Field(default=None, ge=0, lt=1)
    thrust_deduction: Literal["holtrop", "given"] = "holtrop"
    thrust_deduction_fraction: float | None = Field(default=None, ge=0, lt=1)
    relative_rotative: Literal["holtrop", "given"] = "holtrop"
    relative_rotative_efficiency: float | None = Field(default=None, gt=0, le=1.2)
    design_thrust: Literal["calm", "service"] = "service"   # service: thrust x (1 + sea margin) for Keller and P/D
    appendage_margin: float = Field(default=0.0, ge=0, le=1)  # extra appendages not in the resistance model
    air_margin: float = Field(default=0.0, ge=0, le=1)
    sea_margin: float = Field(default=0.15, ge=0, le=1)
    shaft_efficiency: float = Field(default=0.97, gt=0, le=1)  # shafting and gearbox
    engines: int = Field(default=2, ge=1, le=8)
    engine_mcr_kw: float | None = Field(default=None, gt=0)   # per engine
    mcr_fraction: float = Field(default=0.85, gt=0, le=1)
    pto_kw: float = Field(default=0.0, ge=0)
    engine_rpm: float | None = Field(default=None, gt=0)
    atmospheric_pressure_pa: float = Field(default=101325.0, gt=0)
    vapour_pressure_pa: float = Field(default=1705.0, ge=0)

    @model_validator(mode="after")
    def consistent_choices(self) -> "PropulsionConfig":
        if (self.shaft_immersion_m is None) == (self.shaft_z_m is None):
            raise ValueError("give exactly one of shaft_immersion_m and shaft_z_m")
        pairs = (("wake", "wake_fraction"), ("thrust_deduction", "thrust_deduction_fraction"),
                 ("relative_rotative", "relative_rotative_efficiency"))
        for choice, value in pairs:
            if (getattr(self, choice) == "given") != (getattr(self, value) is not None):
                raise ValueError(f"{value} is required for, and only used with, {choice} 'given'")
        holtrop = [c for c, _ in pairs if getattr(self, c) == "holtrop"]
        if holtrop and self.propellers != 2:
            raise ValueError("Holtrop's propulsion factors here are the twin-screw formulas; with "
                             f"{self.propellers} propeller(s) give {', '.join(holtrop)} as values "
                             "(or the Taylor wake)")
        return self


class ObjectiveConfig(StrictModel):
    """What an optimisation minimises, as a ratio to the baseline hull (J = 1 for the baseline).

    quantity: Holtrop 1984 total resistance, Michell wave + ITTC-57 friction (a partial sum, for
    relative comparison only) or brake power P_B (Holtrop + propulsion settings).
    weighting:
      equal    J = mean over the speeds of Q/Q0 (each speed's change counts the same)
      profile  J = sum(w Q) / sum(w Q0) with time shares w (with P_B: the ratio of propulsion energy)
      service  J = Q/Q0 at the service speed
    propeller (brake power only): "baseline" keeps the baseline's P/D and blade area for every
    candidate; "redesign" chooses them again for each candidate.
    """

    quantity: Literal["holtrop", "michell", "brake_power"] = "holtrop"
    weighting: Literal["equal", "profile", "service"] = "equal"
    profile: list[tuple[float, float]] | None = None     # (speed kn, time share), weighting "profile"
    service_speed_kn: float | None = Field(default=None, gt=0)
    propeller: Literal["baseline", "redesign"] = "baseline"

    @model_validator(mode="after")
    def consistent_weighting(self) -> "ObjectiveConfig":
        if (self.weighting == "profile") != (self.profile is not None):
            raise ValueError("profile is required for, and only used with, weighting 'profile'")
        if (self.weighting == "service") != (self.service_speed_kn is not None):
            raise ValueError("service_speed_kn is required for, and only used with, weighting 'service'")
        if self.profile is not None:
            speeds = [s for s, _ in self.profile]
            if not speeds or any(not s > 0 for s in speeds) or len(set(speeds)) != len(speeds):
                raise ValueError("profile speeds must be positive and different")
            if any(w < 0 for _, w in self.profile) or not sum(w for _, w in self.profile) > 0:
                raise ValueError("profile shares must be zero or positive, with a positive sum")
        return self


class SearchConfig(StrictModel):
    """Differential evolution settings; the population is kept at 30 or more (Deb ranking needs a spread)."""

    population: int = Field(default=30, ge=30, le=1000)
    max_evaluations: int = Field(default=600, ge=30, le=1_000_000)
    seed: int = Field(default=170026, ge=0)
    start: Literal["uniform", "baseline"] = "uniform"   # uniform over the bounds, or clustered at the baseline
    keep: int = Field(default=10, ge=1, le=200)         # best feasible candidates returned

    @model_validator(mode="after")
    def whole_first_generation(self) -> "SearchConfig":
        if self.max_evaluations < self.population:
            raise ValueError("max_evaluations must cover at least one full population")
        return self


class ResistanceConfig(StrictModel):
    gravity_m_s2: float = Field(default=9.80665, gt=0)
    holtrop: HoltropConfig = Field(default_factory=HoltropConfig)
    michell: MichellConfig = Field(default_factory=MichellConfig)


class StudyConfig(StrictModel):
    study_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str = ""
    baseline: BaselineConfig
    scope: Literal["bare_hull", "hull_with_appendages"] = "bare_hull"
    appendage_volume_m3: float = Field(default=0.0, ge=0)
    immersion: ImmersionConfig
    water: WaterConfig = Field(default_factory=WaterConfig)
    speeds_kn: list[float] = Field(min_length=1)
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    variables: list[VariableConfig] = Field(default_factory=list)
    run_root: Path = Path("../runs")
    database: Path = Path("../runs/hullopt.sqlite3")
    section_count: int = Field(default=161, ge=3)
    tessellation: TessellationConfig = Field(default_factory=TessellationConfig)
    transformation: TransformationConfig = Field(default_factory=TransformationConfig)
    resistance: ResistanceConfig = Field(default_factory=ResistanceConfig)
    friction_include_transom: bool = True
    _config_path: Path | None = PrivateAttr(default=None)

    @field_validator("speeds_kn")
    @classmethod
    def speed_range(cls, values: list[float]) -> list[float]:
        if any(value <= 0 for value in values):
            raise ValueError("speeds_kn must contain positive finite speeds")
        if values != sorted(set(values)):
            raise ValueError("speeds_kn must be strictly increasing and unique")
        return values

    @model_validator(mode="after")
    def unique_variables(self) -> "StudyConfig":
        names = [variable.name for variable in self.variables]
        if len(names) != len(set(names)):
            raise ValueError("variable names must be unique")
        if self.scope == "hull_with_appendages" and not self.baseline.appendage_layers:
            raise ValueError("hull_with_appendages requires appendage_layers")
        return self

    @property
    def selected_layers(self) -> list[str]:
        layers = list(self.baseline.hull_layers)
        if self.scope == "hull_with_appendages":
            layers.extend(self.baseline.appendage_layers)
        return list(dict.fromkeys(layers))

    @property
    def config_path(self) -> Path:
        if self._config_path is None:
            raise ValueError("StudyConfig must be loaded with load_study to have a source path")
        return self._config_path

    def input_hashes(self) -> dict[str, str]:
        """Read and validate inputs before any numerical evaluation."""
        paths = {
            "study_yaml": self.config_path,
            "geometry_dump": self.baseline.geometry_dump,
            "source_3dm": self.baseline.source_3dm,
        }
        hashes = {name: file_sha256(path) for name, path in paths.items()}
        if hashes["source_3dm"] != self.baseline.source_sha256:
            raise ValueError(
                "Source .3dm SHA-256 mismatch: expected "
                f"{self.baseline.source_sha256}, got {hashes['source_3dm']}"
            )
        return hashes


def load_study(path: str | Path) -> StudyConfig:
    source = Path(path).expanduser().resolve(strict=True)
    with source.open("r", encoding="utf-8-sig") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("Study YAML must contain a mapping")
    config = StudyConfig.model_validate(document)
    config._config_path = source
    for name in ("geometry_dump", "source_3dm"):
        value = getattr(config.baseline, name).expanduser()
        setattr(config.baseline, name, (source.parent / value).resolve())
    for name in ("run_root", "database"):
        value = getattr(config, name).expanduser()
        setattr(config, name, (source.parent / value).resolve())
    return config

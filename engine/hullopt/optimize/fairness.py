"""Fairness metrics and gates for hull candidates.

Lines (stations, waterlines, buttocks) are cut from a fair reference mesh, resampled by arc length and
smoothed lightly; plane curvature kappa(s) is compared between the reference and a candidate.

Two ways to obtain candidate lines here:
  * ``LineSet.deformed_points(amps)`` maps the reference line points through precomputed mode
    displacements (exact for a pointwise morph, approximate for a CV morph);
  * ``LineSet.from_mesh(mesh_triangles, ...)`` cuts an actual candidate mesh with the same planes
    (used to verify the final CAD).
The optimiser and the morph service use ``material_lines.MaterialLineSet`` instead: it keeps the exact
NURBS address (face, u, v) of every reference sample, evaluates each candidate there and compares
signed 3-D curvature, with the plan and the gates defined in this module.

Where the lines are cut and how strict the gates are is a ``FairnessPlan``. The plan has no built-in
hull: ``FairnessPlan.from_frame`` derives it from the hull's own length, draft and beam (curvature
limits scale with 1/LWL, so geometrically similar hulls get the same verdict), or a study gives every
value explicitly.

Gates, all relative to the reference:
  G1  no new inflection: per curve, curvature sign changes (|kappa| > thr, with persistence) <= reference
  G2  |kappa1 - kappa0| <= dk_max, and kappa1/kappa0 within ratio where |kappa0| > ratio_min_curvature
  G3  fairness energy  sum int (dkappa/ds)^2 ds  <= energy_max x reference, per family
  G5  bilge radius: kappa0/kappa1 >= bilge_radius_min on stations inside the bilge window
Points where the reference already has a knuckle (|kappa0| > knuckle_curvature), aft of end_mask_x and
at curve ends are masked.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, asdict

import numpy as np
from scipy.ndimage import gaussian_filter1d
from hullopt.hydro.sections import plane_segments

H = 0.02          # resampling step of the first test hull's study, m (FairnessPlan.spacing_m)
SIGMA = 4         # smoothing, samples (Gaussian sigma = SIGMA * spacing)
KNUCKLE = 15.0    # |kappa0| above this = intentional knuckle on the first test hull, 1/m


@dataclass(frozen=True)
class FairnessRules:
    """Fairness line positions and gate limits relative to the hull.

    Lengths are fractions of LWL, heights fractions of the draft T, buttocks fractions of the hull's
    largest half-beam; curvatures are kappa*LWL. The defaults are the hand-tuned values of the first
    test hull (42.6 m LWL, T 2.09 m), rounded; they are starting values, not validated limits.
    """
    stations: int = 41
    station_inset_aft: float = 0.0094
    station_inset_fwd: float = 0.0070
    waterline_low: float = 0.84
    waterline_high: float = 0.48
    waterline_step: float = 0.12
    buttocks: int = 8
    buttock_outer: float = 0.92
    samples_per_lwl: float = 2100.0
    knuckle_curvature_lwl: float = 640.0
    end_mask: float = 0.0070
    min_length: float = 0.0235
    inflection_curvature_lwl: float = 2.1
    curvature_change_lwl: float = 21.0
    curvature_ratio: tuple = (0.5, 2.0)
    ratio_curvature_lwl: float = 13.0
    energy_ratio_max: float = 1.15
    bilge_radius_ratio_min: float = 0.75
    bilge_start: float = 0.32
    bilge_end: float = 0.50
    bilge_depth: float = 0.10


@dataclass(frozen=True)
class FairnessPlan:
    """Where fairness lines are cut and the gate limits, in metres and 1/m (model axes)."""
    stations: tuple
    waterlines: tuple
    buttocks: tuple
    spacing_m: float
    knuckle_curvature: float
    end_mask_x: float
    min_length_m: float
    inflection_threshold: float
    dk_max: float
    ratio: tuple
    ratio_min_curvature: float
    energy_max: float
    bilge_radius_min: float
    bilge_x: tuple
    bilge_z_max: float

    def __post_init__(self):
        numbers = [v for f in fields(self) for v in (getattr(self, f.name) if isinstance(getattr(self, f.name), tuple)
                                                      else (getattr(self, f.name),))]
        if not all(math.isfinite(float(v)) for v in numbers):
            raise ValueError("fairness plan values must be finite")
        if not (self.stations or self.waterlines or self.buttocks):
            raise ValueError("fairness plan has no lines")
        if self.spacing_m <= 0 or self.knuckle_curvature <= 0 or self.min_length_m < 0 or self.inflection_threshold < 0:
            raise ValueError("invalid fairness sampling settings")
        if self.dk_max <= 0 or self.energy_max < 1 or not 0 < self.bilge_radius_min <= 1:
            raise ValueError("Invalid fairness gate limits")
        if len(self.ratio) != 2 or not 0 < self.ratio[0] <= 1 <= self.ratio[1]:
            raise ValueError("curvature ratio limits must bracket 1")
        if len(self.bilge_x) != 2 or self.bilge_x[0] > self.bilge_x[1]:
            raise ValueError("bilge window must be (x_from, x_to)")

    @property
    def smoothing_m(self):
        return SIGMA * self.spacing_m

    def planes(self):
        return ([("st", 0, float(x)) for x in self.stations] + [("wl", 2, float(z)) for z in self.waterlines] +
                [("bt", 1, float(y)) for y in self.buttocks])

    @classmethod
    def from_frame(cls, frame, rules: FairnessRules = FairnessRules()) -> "FairnessPlan":
        """Plan from the hull's dimensions as held in a mode frame (LWL, draft, half-beam)."""
        lwl = frame.x_fwd - frame.x_aft
        draft = frame.z_wl - frame.z_keel
        half = frame.y_ref
        if not (lwl > 0 and draft > 0 and half > 0):
            raise ValueError("frame needs positive LWL, draft and half-beam")
        stations = np.linspace(frame.x_aft + rules.station_inset_aft * lwl,
                               frame.x_fwd - rules.station_inset_fwd * lwl, rules.stations)
        count = int(math.floor((rules.waterline_low + rules.waterline_high) / rules.waterline_step + 1e-9)) + 1
        waterlines = frame.z_wl + draft * (-rules.waterline_low + rules.waterline_step * np.arange(count))
        buttocks = half * rules.buttock_outer * np.arange(1, rules.buttocks + 1) / rules.buttocks
        return cls(stations=tuple(float(x) for x in stations), waterlines=tuple(float(z) for z in waterlines),
                   buttocks=tuple(float(y) for y in buttocks), spacing_m=lwl / rules.samples_per_lwl,
                   knuckle_curvature=rules.knuckle_curvature_lwl / lwl,
                   end_mask_x=frame.x_aft + rules.end_mask * lwl, min_length_m=rules.min_length * lwl,
                   inflection_threshold=rules.inflection_curvature_lwl / lwl,
                   dk_max=rules.curvature_change_lwl / lwl, ratio=tuple(float(r) for r in rules.curvature_ratio),
                   ratio_min_curvature=rules.ratio_curvature_lwl / lwl, energy_max=rules.energy_ratio_max,
                   bilge_radius_min=rules.bilge_radius_ratio_min,
                   bilge_x=(frame.x_aft + rules.bilge_start * lwl, frame.x_aft + rules.bilge_end * lwl),
                   bilge_z_max=frame.z_wl - rules.bilge_depth * draft)

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else float(v)) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict) -> "FairnessPlan":
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError("unknown fairness plan keys: " + ", ".join(unknown))
        return cls(**{k: (tuple(float(x) for x in v) if isinstance(v, (list, tuple)) else float(v))
                      for k, v in data.items()})


def cut(tri, axis, val):
    return plane_segments(np.asarray(tri, float), float(val), int(axis))[0]


def chain(segs, tol=1e-6):
    if len(segs) == 0:
        return []
    key = lambda p: tuple(np.round(p / tol).astype(np.int64))
    adj = {}
    for i, (p, q) in enumerate(segs):
        adj.setdefault(key(p), []).append((i, 0))
        adj.setdefault(key(q), []).append((i, 1))
    used = np.zeros(len(segs), bool)
    lines = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        line = [segs[i][0], segs[i][1]]
        for end in (1, 0):
            while True:
                p = line[-1] if end == 1 else line[0]
                nxt = [(j, e) for j, e in adj.get(key(p), []) if not used[j]]
                if not nxt:
                    break
                j, e = nxt[0]
                used[j] = True
                q = segs[j][1 - e]
                if end == 1:
                    line.append(q)
                else:
                    line.insert(0, q)
        lines.append(np.array(line))
    return lines


def starboard(lines, ymin=-1e-6):
    out = []
    for l in lines:
        runs=[]
        cur = []
        for p in l:
            if p[1] >= ymin:
                cur.append(p)
            else:
                if len(cur) > 1:
                    runs.append(np.array(cur))
                cur = []
        if len(cur) > 1:
            runs.append(np.array(cur))
        # A closed contour may start on starboard. Its first and last kept
        # runs are the SAME curve across the arbitrary cyclic start point.
        if len(runs)>1 and np.linalg.norm(l[0]-l[-1])<=1e-6 and l[0,1]>=ymin and l[-1,1]>=ymin:
            runs[0]=np.vstack((runs[-1],runs[0][1:]));runs.pop()
        out.extend(runs)
    return out

def merge_curve_endpoints(lines, tolerance_m):
    """Join mutually nearest polyline ends only; never a CAD gap certificate.

    Used after independent native seam verification for final mesh plane cuts.
    A sub-tolerance stitching chord is retained explicitly in the polyline.
    """
    from scipy.spatial import cKDTree
    lines=list(lines)
    while len(lines)>1:
        ends=np.array([p for line in lines for p in (line[0],line[-1])])
        pairs=cKDTree(ends).query_pairs(tolerance_m)
        pairs=[(np.linalg.norm(ends[a]-ends[b]),a,b) for a,b in pairs if a//2!=b//2]
        if not pairs:break
        _,a,b=min(pairs);i,j=a//2,b//2
        left=lines[i] if a%2 else lines[i][::-1]
        right=lines[j] if not b%2 else lines[j][::-1]
        joined=np.vstack((left,right))
        lines=[line for k,line in enumerate(lines) if k not in (i,j)]+[joined]
    return lines


def resample(p, h=H):
    p = np.asarray(p, float)
    if p.ndim != 2 or p.shape[1] != 3 or len(p) < 2 or not np.isfinite(p).all() or not np.isfinite(h) or h <= 0:
        raise ValueError('Expected finite (n,3) line points and positive spacing')
    p = p[np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-12]]
    if len(p) < 2:
        raise ValueError('Curve has no positive length')
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    s = np.arange(0, d[-1], h)
    return np.stack([np.interp(s, d, p[:, i]) for i in range(3)], axis=1)


def kappa(q2, h=H):
    q2 = np.asarray(q2, float)
    if q2.ndim != 2 or q2.shape[1] != 2 or len(q2) < 5 or not np.isfinite(q2).all():
        raise ValueError('Curvature requires at least five finite plane points')
    x = gaussian_filter1d(q2[:, 0], SIGMA, mode="nearest")
    y = gaussian_filter1d(q2[:, 1], SIGMA, mode="nearest")
    dx, dy = np.gradient(x, h), np.gradient(y, h)
    ddx, ddy = np.gradient(dx, h), np.gradient(dy, h)
    ds = np.sqrt(dx * dx + dy * dy)
    return (dx * ddy - dy * ddx) / np.maximum(ds ** 3, 1e-9), ds * h


def inflections(k, mask, thr, min_run=10):
    """Sign changes of kappa: a lobe counts only if |kappa| > thr for at least ``min_run`` consecutive
    samples (10 samples = 0.2 m at 20 mm spacing), so near-zero curvature noise on flat waterlines is
    not counted."""
    state, n, run, run_sign = 0, 0, 0, 0
    for v, m in zip(k, mask):
        if not m:
            state, run, run_sign = 0, 0, 0
            continue
        sg = 1 if v > thr else (-1 if v < -thr else 0)
        if sg == 0:
            run, run_sign = 0, 0
            continue
        run = run + 1 if sg == run_sign else 1
        run_sign = sg
        if run >= min_run and sg != state:
            if state != 0:
                n += 1
            state = sg
    return n


@dataclass
class Curve:
    family: str          # 'st' | 'wl' | 'bt'
    label: str
    plane: tuple         # in-plane coordinate indices
    p: np.ndarray        # reference points (n,3)
    k0: np.ndarray
    mask: np.ndarray
    thr: float
    ds0: np.ndarray = None


@dataclass
class LineSet:
    curves: list = field(default_factory=list)
    plan: FairnessPlan = None

    # ------------------------------------------------------------------ build
    @classmethod
    def from_mesh(cls, tri, frame, planes=None, ref: "LineSet" = None, min_len=None, join_gap_m=0., plan=None):
        """Cut a mesh.  With ``ref`` the reference curvature/mask of matching curves are attached
        (matching = same plane, nearest centroid). Without a plan one is derived from the frame."""
        plan = plan or (ref.plan if ref is not None and ref.plan is not None else FairnessPlan.from_frame(frame))
        planes = planes or plan.planes()
        min_len = plan.min_length_m if min_len is None else min_len
        curves = []
        for fam, axis, val in planes:
            lines = chain(cut(tri, axis, val))
            if join_gap_m>0:
                lines=merge_curve_endpoints(lines,join_gap_m)
            if fam != "bt":
                lines = starboard(lines)
            for i, l in enumerate(lines):
                if len(l) < 3:
                    continue
                direction = 2 if fam == 'st' else 0
                if np.linalg.norm(l[0]-l[-1])<=1e-6:
                    l=l[:-1]
                    first=np.lexsort((l[:,2],l[:,1],np.round(l[:,0],8)))[0]
                    l=np.roll(l,-first,axis=0)
                    pl={"st":(1,2),"wl":(0,1),"bt":(0,2)}[fam]
                    q=l[:,pl];area=np.sum(q[:,0]*np.roll(q[:,1],-1)-q[:,1]*np.roll(q[:,0],-1))
                    if area<0:l=np.vstack((l[:1],l[:0:-1]))
                    l=np.vstack((l,l[:1]))
                if l[-1, direction] < l[0, direction]:
                    l = l[::-1]
                p = resample(l, plan.spacing_m)
                if len(p) * plan.spacing_m < min_len:
                    continue
                plane = {"st": (1, 2), "wl": (0, 1), "bt": (0, 2)}[fam]
                k, ds0 = kappa(p[:, plane], plan.spacing_m)
                mask = (np.abs(k) < plan.knuckle_curvature) & (p[:, 0] > plan.end_mask_x)
                mask[:3 * SIGMA] = mask[-3 * SIGMA:] = False
                thr = plan.inflection_threshold
                curves.append(Curve(fam, f"{fam} {val:.3f}#{i}", plane, p, k, mask, thr, ds0))
        if not curves:
            raise ValueError('No usable fairness curves were extracted')
        result = cls(curves, plan)
        if ref is not None:
            # Match by cutting plane and closest centroid. Match coverage is
            # explicit: a split/missing curve must not silently pass via zip().
            from scipy.optimize import linear_sum_assignment
            matched = [None] * len(ref.curves)
            def key(c):
                return c.family, c.label.split('#')[0]
            for group in {key(c) for c in ref.curves}:
                ri = [i for i,c in enumerate(ref.curves) if key(c) == group]
                ci = [i for i,c in enumerate(curves) if key(c) == group]
                if len(ri) != len(ci):
                    raise ValueError(f'Candidate curve topology/coverage differs on {group}: {len(ri)} vs {len(ci)}')
                cost = np.array([[np.linalg.norm(ref.curves[i].p.mean(0)-curves[j].p.mean(0)) for j in ci] for i in ri])
                a,b = linear_sum_assignment(cost)
                for i,j in zip(a,b):
                    c0,c1 = ref.curves[ri[i]],curves[ci[j]]
                    u0 = np.linspace(0,1,len(c0.p)); u1 = np.linspace(0,1,len(c1.p))
                    matched[ri[i]] = np.stack([np.interp(u0,u1,c1.p[:,k]) for k in range(3)],axis=1)
            result.reference_points = matched
        return result

    # -------------------------------------------------------------- candidate
    def precompute(self, mode_fn, names):
        """Cache per-mode displacement of every reference point: mode_fn(name, p) -> (n,3)."""
        self._names = list(names)
        self._D = [np.stack([mode_fn(n, c.p) for n in names]) for c in self.curves]

    def deformed_points(self, amps):
        a = np.array([float(amps.get(n, 0.0)) for n in self._names])
        return [c.p + np.tensordot(a, D, axes=1) for c, D in zip(self.curves, self._D)]

    def evaluate(self, pts_list, detail=False, plan=None):
        """Fairness metrics of candidate points (one array per reference curve, same ordering)."""
        if len(pts_list) != len(self.curves) or not self.curves:
            raise ValueError('Every nonempty reference curve requires exactly one candidate curve')
        plan = plan or self.plan
        if plan is None:
            raise ValueError('Fairness evaluation needs a FairnessPlan')
        dk_max, ratio, energy_max, bilge_radius_min = plan.dk_max, plan.ratio, plan.energy_max, plan.bilge_radius_min
        worst_dk, worst_where, new_infl, ratio_bad = 0.0, None, 0, 0.0
        radius_ratio = 1.0
        e0 = {"st": 0.0, "wl": 0.0, "bt": 0.0}
        e1 = dict(e0)
        rows = []
        for c, q in zip(self.curves, pts_list):
            if np.asarray(q).shape != c.p.shape:
                raise ValueError('Candidate/reference material point counts must match')
            k1, ds = kappa(q[:, c.plane], plan.spacing_m)
            # Mask only EXISTING reference knuckles. Candidate spikes must
            # remain visible; masking them would reward new sharp defects.
            m = c.mask
            dk = np.where(m, np.abs(k1 - c.k0), 0.0)
            i = int(np.argmax(dk))
            if dk[i] > worst_dk:
                worst_dk, worst_where = float(dk[i]), (c.label, c.p[i].round(2).tolist())
            big = m & (np.abs(c.k0) > plan.ratio_min_curvature)
            if big.any():
                r = k1[big] / c.k0[big]
                ratio_bad = max(ratio_bad, float(np.max(np.maximum(ratio[0] - r, r - ratio[1]))))
            n0 = inflections(c.k0, m, c.thr)
            n1 = inflections(k1, m, c.thr)
            new_infl += max(0, n1 - n0)
            dk0 = np.gradient(c.k0) / np.maximum(c.ds0, 1e-12)
            dk1 = np.gradient(k1) / np.maximum(ds, 1e-12)
            if c.family == 'st':
                bilge = (m & (c.p[:,0] >= plan.bilge_x[0]) & (c.p[:,0] <= plan.bilge_x[1]) & (c.p[:,2] < plan.bilge_z_max)
                         & (np.abs(c.k0) > plan.ratio_min_curvature))
                if bilge.any():
                    radius_ratio = min(radius_ratio, float(np.min(np.abs(c.k0[bilge])/np.maximum(np.abs(k1[bilge]),1e-12))))
            e0[c.family] += float(np.sum(np.where(m, dk0 ** 2, 0) * c.ds0))
            e1[c.family] += float(np.sum(np.where(m, dk1 ** 2, 0) * ds))
            if detail:
                rows.append(dict(label=c.label, dk_max=float(dk[i]), at=c.p[i].round(3).tolist(),
                                 infl0=n0, infl1=n1))
        er = {f: (e1[f] / e0[f] if e0[f] > 1e-12 else (1.0 if e1[f] <= 1e-12 else 1.0+e1[f]/1e-12)) for f in e0}
        out = dict(dk_max=worst_dk, dk_where=worst_where, new_infl=new_infl, ratio_excess=ratio_bad,
                   energy_ratio=er,
                   pass_G1=new_infl == 0, pass_G2=(worst_dk <= dk_max) and ratio_bad <= 0.0,
                   pass_G3=all(v <= energy_max+1e-12 for v in er.values()),
                   bilge_radius_ratio_min=radius_ratio, pass_G5=radius_ratio >= bilge_radius_min-1e-12)
        out["pass"] = out["pass_G1"] and out["pass_G2"] and out["pass_G3"] and out['pass_G5']
        if detail:
            out["curves"] = rows
        return out

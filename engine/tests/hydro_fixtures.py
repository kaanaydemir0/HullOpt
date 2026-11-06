"""Analytic fixtures independent of the production measurement algorithms."""

from __future__ import annotations

import numpy as np


def oriented_triangles(vertices, faces):
    """Orient a convex polyhedron outwards using an interior point."""
    points = np.asarray(vertices, dtype=float)
    interior = points.mean(axis=0)
    triangles = []
    for indices in faces:
        polygon = points[np.asarray(indices)]
        for index in range(1, len(polygon) - 1):
            triangle = polygon[[0, index, index + 1]].copy()
            normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
            if np.dot(normal, triangle.mean(axis=0) - interior) < 0:
                triangle[[1, 2]] = triangle[[2, 1]]
            triangles.append(triangle)
    return np.asarray(triangles)


def box_triangles(length=10.0, breadth=2.0, depth=2.0):
    vertices = [(x, y, z) for x in (0.0, length)
                for y in (-breadth / 2, breadth / 2) for z in (0.0, depth)]
    return oriented_triangles(vertices, [(0, 1, 3, 2), (4, 6, 7, 5),
                                        (0, 4, 5, 1), (2, 3, 7, 6),
                                        (0, 2, 6, 4), (1, 5, 7, 3)])


def box_mesh(length=10.0, breadth=2.0, depth=2.0):
    from hullopt.geometry import TriangleMesh
    return TriangleMesh(box_triangles(length, breadth, depth))


def tetrahedron_mesh():
    from hullopt.geometry import TriangleMesh
    triangles = oriented_triangles([(0, 0, 0), (2, 0, 0), (0, 3, 0), (0, 0, 4)],
                                   [(0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2)])
    return TriangleMesh(triangles)


def planar_face_hgeo(corners, *, rev=False, object_index=0):
    """Make a bilinear face; corners are P00,P01,P10,P11 in u-major order."""
    return {
        "object_index": object_index, "object_name": "analytic", "layer": "HULL",
        "face_index": 0, "rev": int(rev), "nurbs_form_exact": 1,
        "nurbs": {"rational": False, "order": [2, 2], "cv_count": [2, 2],
                  "knots_u": [0, 1], "knots_v": [0, 1],
                  "cv": [list(point) + [1.0] for point in corners]},
        "loops": [{"type": "outer", "uv": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]}],
    }


def box_hgeo(*, meters_per_unit=1.0):
    """Closed box, including one deliberately reversed surface parameterization."""
    # P00, P01, P10, P11: cross(dP/du,dP/dv) gives outward normals,
    # except the aft end where rev=True repairs the parameterization.
    faces = [
        planar_face_hgeo([(0, -1, 0), (0, -1, 2), (0, 1, 0), (0, 1, 2)], rev=True),
        planar_face_hgeo([(10, -1, 0), (10, -1, 2), (10, 1, 0), (10, 1, 2)]),
        planar_face_hgeo([(0, -1, 0), (0, -1, 2), (10, -1, 0), (10, -1, 2)]),
        planar_face_hgeo([(0, 1, 0), (10, 1, 0), (0, 1, 2), (10, 1, 2)]),
        planar_face_hgeo([(0, -1, 0), (0, 1, 0), (10, -1, 0), (10, 1, 0)], rev=True),
        planar_face_hgeo([(0, -1, 2), (0, 1, 2), (10, -1, 2), (10, 1, 2)]),
    ]
    for index, face in enumerate(faces):
        face["object_index"] = index
        for point in face["nurbs"]["cv"]:
            point[:3] = [coordinate / meters_per_unit for coordinate in point[:3]]
    return {"format": "hullgeo-1", "source": "analytic box", "unit_system": "fixture",
            "meters_per_unit": meters_per_unit, "abs_tol": 1e-9 / meters_per_unit,
            "document_strings": {}, "layers": {"0": "HULL"}, "faces": faces,
            "face_count": len(faces), "warnings": []}


def semicircle_prism_hgeo(length=4.0, radius=1.0):
    """Exact rational lower half cylinder, closed at both x ends; open at z=0."""
    weight = np.sqrt(0.5)
    yz = [(radius, 0), (radius, -radius), (0, -radius),
          (-radius, -radius), (-radius, 0)]
    weights = [1.0, weight, 1.0, weight, 1.0]
    curve = [{"yz": p, "w": w} for p, w in zip(yz, weights)]
    control = [[x * item["w"], item["yz"][0] * item["w"],
                item["yz"][1] * item["w"], item["w"]]
               for x in (0.0, length) for item in curve]
    side = {"object_index": 0, "object_name": "rational semicircle", "layer": "HULL",
            "face_index": 0, "rev": 0, "nurbs_form_exact": 1,
            "nurbs": {"rational": True, "order": [2, 3], "cv_count": [2, 5],
                      "knots_u": [0, 1], "knots_v": [0, 0, 1, 1, 2, 2], "cv": control},
            "loops": [{"type": "outer", "uv": [[0, 0], [1, 0], [1, 2], [0, 2], [0, 0]]}]}
    theta = np.linspace(0, np.pi, 1025)
    # End-cap parameterization maps [0,1]^2 onto y=[-R,R], z=[-R,0].
    loop = np.column_stack(((np.cos(theta) + 1) / 2, 1 - np.sin(theta)))
    loop = np.vstack((loop, loop[0])).tolist()
    caps = []
    for index, x in enumerate((0.0, length), 1):
        cap = planar_face_hgeo([(x, -radius, -radius), (x, -radius, 0),
                               (x, radius, -radius), (x, radius, 0)],
                              rev=(index == 1), object_index=index)
        cap["loops"][0]["uv"] = loop
        caps.append(cap)
    return {"format": "hullgeo-1", "source": "analytic rational semicircle prism",
            "unit_system": "Meters", "meters_per_unit": 1.0, "abs_tol": 1e-10,
            "document_strings": {}, "layers": {"0": "HULL"}, "faces": [side, *caps],
            "face_count": 3, "warnings": []}


def geometry_from_hgeo(payload):
    """Use the actual serialization loader to exercise unit conversion as well."""
    import json
    import tempfile
    from pathlib import Path
    from hullopt.geometry import load_hgeo
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.hgeo.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_hgeo(path)


def semicircle_prism_geometry():
    return geometry_from_hgeo(semicircle_prism_hgeo())


def parity_scan_volume(triangles, z_wl_m, *, nx=401, nz=321):
    """Independent midpoint integration of even/odd polygon widths in x,z.

    No orientation or surface flux from the production code is used. Odd
    intersections are fatal, rather than silently discarded as in the old script.
    """
    triangles = np.asarray(triangles)
    lo = triangles.min(axis=(0, 1))
    hi = triangles.max(axis=(0, 1))
    dx = (hi[0] - lo[0]) / nx
    dz = (z_wl_m - lo[2]) / nz
    xs = lo[0] + (np.arange(nx) + 0.5) * dx
    zs = lo[2] + (np.arange(nz) + 0.5) * dz
    x_min = triangles[:, :, 0].min(axis=1)
    x_max = triangles[:, :, 0].max(axis=1)
    volume = 0.0
    scanlines = 0
    for x in xs:
        selected = triangles[(x_min < x) & (x_max > x)]
        if not len(selected):
            continue
        endpoints = []
        valid = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            pa, pb = selected[:, a], selected[:, b]
            crosses = (pa[:, 0] - x) * (pb[:, 0] - x) < 0
            fraction = np.divide(x - pa[:, 0], pb[:, 0] - pa[:, 0],
                                 out=np.zeros(len(selected)), where=crosses)
            endpoints.append(pa + fraction[:, None] * (pb - pa))
            valid.append(crosses)
        endpoints = np.stack(endpoints, axis=1)
        valid = np.stack(valid, axis=1)
        two = valid.sum(axis=1) == 2
        segments = endpoints[two][valid[two]].reshape(-1, 2, 3)[:, :, 1:]
        if not len(segments):
            continue
        ya, za = segments[:, 0, 0], segments[:, 0, 1]
        yb, zb = segments[:, 1, 0], segments[:, 1, 1]
        for z in zs:
            crossing = (za - z) * (zb - z) < 0
            if not crossing.any():
                continue
            ys = np.sort(ya[crossing] + (yb[crossing] - ya[crossing]) *
                         (z - za[crossing]) / (zb[crossing] - za[crossing]))
            if len(ys) % 2:
                raise AssertionError(f"Odd parity at x={x:.12g}, z={z:.12g}: {len(ys)}")
            volume += (ys[1::2] - ys[0::2]).sum() * dx * dz
            scanlines += 1
    return float(volume), scanlines


def sheer_hull_hgeo(length=40.0, beam=8.0, depth=4.0, sheer=0.25, spans=None):
    """Two exact sides of a parabolic hull, open deck, closed below the deck.

    x = L(u - 1/2), z = D v (1 + sheer u^2), |y| = (B/2) 4u(1-u) v(2-v): sides meet on the
    centreplane at the keel (v=0), the stern (u=0) and the stem (u=1); the deck edge rises forward.
    Every coordinate scales with the arguments, so a scaled copy is geometrically similar.
    Without ``spans`` each side is one biquadratic Bezier patch (3 x 3 control points); with
    ``spans=(nu, nv)`` it is the same surface as a uniform cubic B-spline with a denser control net
    (exact: the quadratic surface lies in the cubic spline space), as real hulls have.
    """
    cu = (0.0, 0.5, 1.0)
    rise = (1.0, 1.0, 1.0 + sheer)
    side = (0.0, 2.0, 0.0)
    vz = (0.0, 0.5, 1.0)
    vy = (0.0, 1.0, 1.0)

    def surface(u, v, sign):
        u, v = np.meshgrid(u, v, indexing="ij")
        return np.stack([length * (u - 0.5), sign * 0.5 * beam * 4 * u * (1 - u) * v * (2 - v),
                         depth * v * (1 + sheer * u * u)], axis=-1)

    faces = []
    for index, sign in enumerate((1.0, -1.0)):
        if spans is None:
            order, counts, knots = [3, 3], [3, 3], ([0, 0, 1, 1], [0, 0, 1, 1])
            cv = [[length * (cu[i] - 0.5), sign * 0.5 * beam * side[i] * vy[j], depth * rise[i] * vz[j], 1.0]
                  for i in range(3) for j in range(3)]
        else:
            from hullopt.geometry.nurbs import basis_matrix
            order, counts = [4, 4], [spans[0] + 3, spans[1] + 3]
            knots = tuple([0.0, 0.0] + list(np.linspace(0, 1, n + 1)) + [1.0, 1.0] for n in spans)
            su, sv = np.linspace(0, 1, 4 * counts[0]), np.linspace(0, 1, 4 * counts[1])
            bu, bv = basis_matrix(su, knots[0], 4, counts[0]), basis_matrix(sv, knots[1], 4, counts[1])
            values = surface(su, sv, sign)
            net = np.einsum("ia,abk,jb->ijk", np.linalg.pinv(bu), values, np.linalg.pinv(bv))
            cv = [list(net[i, j]) + [1.0] for i in range(counts[0]) for j in range(counts[1])]
        faces.append({"object_index": index, "object_name": "sheer hull side", "layer": "HULL",
                      "face_index": 0, "rev": int(sign > 0), "nurbs_form_exact": 1,
                      "nurbs": {"rational": False, "order": order, "cv_count": counts,
                                "knots_u": list(knots[0]), "knots_v": list(knots[1]), "cv": cv},
                      "loops": [{"type": "outer", "uv": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]}]})
    return {"format": "hullgeo-1", "source": "analytic sheer hull", "unit_system": "Meters",
            "meters_per_unit": 1.0, "abs_tol": 1e-9, "document_strings": {}, "layers": {"0": "HULL"},
            "faces": faces, "face_count": 2, "warnings": []}

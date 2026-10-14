#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import os
import resource
import struct
import subprocess
import sys
import time
from pathlib import Path

import meshio
import numpy as np


# ----------------------------------------------------------------------
# Profiling
# ----------------------------------------------------------------------

def peak_rss_gb() -> float:
    """
    Peak resident set size.

    Linux reports KiB; macOS reports bytes.
    """
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    if sys.platform == "darwin":
        return rss / 1024**3

    return rss * 1024 / 1024**3


@contextlib.contextmanager
def profile(name: str):
    t0 = time.perf_counter()
    rss0 = peak_rss_gb()

    print(f"[{name}] starting, peak RSS={rss0:.2f} GB")

    yield

    dt = time.perf_counter() - t0
    rss1 = peak_rss_gb()

    print(
        f"[{name}] {dt:.2f} s, "
        f"peak RSS={rss1:.2f} GB "
        f"(+{rss1-rss0:.2f} GB)"
    )

def check_binary_mesh(base):
    # points
    with open(base.with_suffix(".bpts"), "rb") as fi:
        npts = int(fi.read(1024).split()[0])
        pts = np.fromfile(fi, dtype=np.float32)

    if pts.size != 3 * npts:
        raise RuntimeError(
            f"Invalid bpts: expected {3*npts} floats, got {pts.size}"
        )

    # elements
    with open(base.with_suffix(".belem"), "rb") as fi:
        nelm = int(fi.read(1024).split()[0])
        data = np.fromfile(fi, dtype=np.int32)

    if data.size != 6 * nelm:
        raise RuntimeError(
            f"Invalid belem: expected {6*nelm} ints, got {data.size}"
        )

    data = data.reshape(nelm, 6)

    etypes = np.unique(data[:, 0])
    tags = np.unique(data[:, -1])

    print("Binary mesh check:")
    print(f"  points: {npts:,}")
    print(f"  elements: {nelm:,}")
    print(f"  element types: {etypes}")
    print(f"  region tags: {tags}")

def remove_stale_mesh_files(base, binary):
    if binary:
        suffixes = [".pts", ".elem", ".lon"]
    else:
        suffixes = [".bpts", ".belem", ".blon"]

    for suffix in suffixes:
        fn = base.with_suffix(suffix)
        if fn.exists():
            print(f"Removing stale {fn}")
            fn.unlink()

# ----------------------------------------------------------------------
# Gmsh
# ----------------------------------------------------------------------

def generate_gmsh(
    geo_file: Path,
    msh_file: Path,
    lc: float | None = None,
    *,
    binary: bool = True,
    algorithm: str | None = "hxt",
    threads: int | None = None,
):
    gmsh = os.environ.get("GMSH", "") or "gmsh"

    cmd = [
        gmsh,
        "-3",
        str(geo_file),
        "-format",
        "msh2",
        "-o",
        str(msh_file),
    ]

    if binary:
        cmd.append("-bin")

    if algorithm is not None:
        cmd += ["-algo", algorithm]

    if threads is not None:
        cmd += ["-nt", str(threads)]

    if lc is not None:
        cmd += [
            "-setnumber", "minlc", str(lc),
            "-setnumber", "maxlc", str(lc),
        ]

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def read_gmsh_tetra(mesh_file: Path):
    """
    Read tetrahedra from Gmsh.

    This is currently the likely large-memory stage. Keep it isolated so
    that it is easy to replace by a direct Gmsh reader/API later.
    """
    with profile("meshio.read"):
        msh = meshio.read(mesh_file)

    pts = msh.points

    tetra_blocks = []
    tag_blocks = []

    physical = msh.cell_data.get("gmsh:physical", None)

    for ib, cell_block in enumerate(msh.cells):
        if cell_block.type != "tetra":
            continue

        tetra_blocks.append(cell_block.data)

        if physical is not None:
            tag_blocks.append(physical[ib])

    if not tetra_blocks:
        raise RuntimeError("No tetrahedral elements found")

    # For this geometry Gmsh should normally produce one tetra block.
    # Avoid vstack/concatenate copies in that common case.
    if len(tetra_blocks) == 1:
        elm = tetra_blocks[0]
    else:
        with profile("concatenate tetra blocks"):
            elm = np.concatenate(tetra_blocks, axis=0)

    if len(tag_blocks) == 1:
        tags = tag_blocks[0]
    elif tag_blocks:
        tags = np.concatenate(tag_blocks)
    else:
        tags = np.zeros(elm.shape[0], dtype=np.int32)

    # At this point pts/elm/tags keep references to arrays owned by msh.
    # The other cell blocks etc. can be released.
    del msh
    del tetra_blocks
    del tag_blocks

    return pts, elm, tags


# ----------------------------------------------------------------------
# Fibres -- deliberately chunked
# ----------------------------------------------------------------------

def fiber_chunk(
    pts: np.ndarray,
    elm: np.ndarray,
    start: int,
    stop: int,
    *,
    zmin: float,
    zmax: float,
    theta0: float,
    theta1: float,
) -> np.ndarray:
    """
    Compute fibres without constructing pts[elm].

    Only tetrahedral centroid z is needed.
    """
    tet = elm[start:stop]
    z = pts[:, 2]

    zc = (
        z[tet[:, 0]]
        + z[tet[:, 1]]
        + z[tet[:, 2]]
        + z[tet[:, 3]]
    ) * 0.25

    s = (zc - zmin) / (zmax - zmin)
    theta = theta0 + s * (theta1 - theta0)

    fib = np.empty((stop - start, 3), dtype=np.float32)
    fib[:, 0] = np.cos(theta)
    fib[:, 1] = np.sin(theta)
    fib[:, 2] = 0.0

    return fib


def iter_fibers(
    pts: np.ndarray,
    elm: np.ndarray,
    angle0_deg: float,
    angle1_deg: float,
    chunk_size: int,
):
    zmin = float(pts[:, 2].min())
    zmax = float(pts[:, 2].max())

    if zmax <= zmin:
        raise ValueError("Mesh has zero extent in z")

    theta0 = np.deg2rad(angle0_deg)
    theta1 = np.deg2rad(angle1_deg)

    for start in range(0, elm.shape[0], chunk_size):
        stop = min(start + chunk_size, elm.shape[0])

        yield fiber_chunk(
            pts,
            elm,
            start,
            stop,
            zmin=zmin,
            zmax=zmax,
            theta0=theta0,
            theta1=theta1,
        )


# ----------------------------------------------------------------------
# Stimulus
# ----------------------------------------------------------------------

def make_stimulus(
    pts,
    kind="disk",
    center=None,
    radius=1.0,
    axis=2,
    side="min",
    tol=None,
):
    pmin = pts.min(axis=0)
    pmax = pts.max(axis=0)

    if center is None:
        center = 0.5 * (pmin + pmax)
        center[axis] = pmin[axis] if side == "min" else pmax[axis]

    center = np.asarray(center, dtype=float)

    if kind == "sphere":
        # Written component-wise to avoid allocating pts-center.
        d2 = (
            (pts[:, 0] - center[0]) ** 2
            + (pts[:, 1] - center[1]) ** 2
            + (pts[:, 2] - center[2]) ** 2
        )
        return np.flatnonzero(d2 <= radius**2)

    if tol is None:
        tol = 1e-8 * np.linalg.norm(pmax - pmin)

    face = pmin[axis] if side == "min" else pmax[axis]
    on_face = np.abs(pts[:, axis] - face) <= tol

    if kind == "face":
        return np.flatnonzero(on_face)

    if kind == "disk":
        transverse = [k for k in range(3) if k != axis]

        d2 = (
            (pts[:, transverse[0]] - center[transverse[0]]) ** 2
            + (pts[:, transverse[1]] - center[transverse[1]]) ** 2
        )

        return np.flatnonzero(on_face & (d2 <= radius**2))

    raise ValueError(f"Unknown stimulus type: {kind}")


# ----------------------------------------------------------------------
# Binary openCARP helpers
# ----------------------------------------------------------------------

HEADER_SIZE = 1024

def write_binary_header(fo, value):
    """
    openCARP binary mesh files use an ASCII header padded to 1024 bytes.
    """
    header = f"{value}\n".encode("ascii")

    if len(header) > HEADER_SIZE:
        raise ValueError("Header too large")

    fo.write(header)
    fo.write(b" " * (HEADER_SIZE - len(header)))

# ----------------------------------------------------------------------
# openCARP output
# ----------------------------------------------------------------------

def write_bpts(filename: Path, pts: np.ndarray, chunk_size: int):
    """
    openCARP coordinates are micrometres.

    Stream the mm -> um conversion to avoid 1000*pts for the whole mesh.
    """
    with open(filename, "wb") as fo:
        write_binary_header(fo, pts.shape[0])

        for start in range(0, pts.shape[0], chunk_size):
            stop = min(start + chunk_size, pts.shape[0])

            block = np.asarray(
                pts[start:stop] * 1000.0,   # mm -> um
                dtype=np.float32,
            )
            block.tofile(fo)

def write_pts(filename: Path, pts: np.ndarray, chunk_size: int):
    with open(filename, "w") as fo:
        print(pts.shape[0], file=fo)

        for start in range(0, pts.shape[0], chunk_size):
            stop = min(start + chunk_size, pts.shape[0])

            # Conversion only for the current block.
            block = pts[start:stop] * 1000.0

            np.savetxt(
                fo,
                block,
                fmt="%.10g",
            )


def write_belem(
    filename: Path,
    elm: np.ndarray,
    tags: np.ndarray,
    chunk_size: int,
):
    """
    Binary tetrahedral element record:

        int32 type
        int32 v0
        int32 v1
        int32 v2
        int32 v3
        int32 region_tag

    Tetrahedron type = 0.
    """
    with open(filename, "wb") as fo:
        write_binary_header(fo, elm.shape[0])

        for start in range(0, elm.shape[0], chunk_size):
            stop = min(start + chunk_size, elm.shape[0])
            n = stop - start

            block = np.empty((n, 6), dtype=np.int32)

            block[:, 0] = 0
            block[:, 1:5] = elm[start:stop]
            block[:, 5] = tags[start:stop]

            block.tofile(fo)


def write_elem(
    filename: Path,
    elm: np.ndarray,
    tags: np.ndarray,
    chunk_size: int,
):
    """
    Chunked np.savetxt is much faster than one Python print per tetrahedron.
    """
    with open(filename, "w") as fo:
        print(elm.shape[0], file=fo)

        for start in range(0, elm.shape[0], chunk_size):
            stop = min(start + chunk_size, elm.shape[0])
            n = stop - start

            block = np.empty((n, 5), dtype=np.int64)
            block[:, :4] = elm[start:stop]
            block[:, 4] = tags[start:stop]

            np.savetxt(
                fo,
                block,
                fmt="Tt %d %d %d %d %d",
            )


def write_blon(
    filename: Path,
    pts: np.ndarray,
    elm: np.ndarray,
    angle0_deg: float,
    angle1_deg: float,
    chunk_size: int,
):
    with open(filename, "wb") as fo:
            # One vector field
            write_binary_header(fo, 1)

            for fib in iter_fibers(
                pts,
                elm,
                angle0_deg,
                angle1_deg,
                chunk_size,
            ):
                np.asarray(
                    fib,
                    dtype=np.float32,
                ).tofile(fo)

def write_lon(
    filename: Path,
    pts: np.ndarray,
    elm: np.ndarray,
    angle0_deg: float,
    angle1_deg: float,
    chunk_size: int,
):
    with open(filename, "w") as fo:
        print(1, file=fo)

        for fib in iter_fibers(
            pts,
            elm,
            angle0_deg,
            angle1_deg,
            chunk_size,
        ):
            np.savetxt(fo, fib, fmt="%.10g")


def write_vtx(filename: Path, indices: np.ndarray):
    indices = np.asarray(indices)

    with open(filename, "w") as fo:
        print(indices.size, file=fo)
        print("extra", file=fo)
        np.savetxt(fo, indices, fmt="%d")


# ----------------------------------------------------------------------
# MMG anisotropic remeshing
# ----------------------------------------------------------------------

def fibers_at_z(z, zmin, zmax, angle0_deg=-60.0, angle1_deg=60.0):
    if zmax <= zmin:
        raise ValueError("Mesh has zero extent in z")

    s = (z - zmin) / (zmax - zmin)

    theta0 = np.deg2rad(angle0_deg)
    theta1 = np.deg2rad(angle1_deg)
    theta = theta0 + s * (theta1 - theta0)

    fib = np.empty((z.size, 3), dtype=float)
    fib[:, 0] = np.cos(theta)
    fib[:, 1] = np.sin(theta)
    fib[:, 2] = 0.0

    return fib

def make_fibers(pts, elm, angle0_deg=-60.0, angle1_deg=60.0):
    zmin = pts[:, 2].min()
    zmax = pts[:, 2].max()

    z = pts[:, 2]
    zc = (
        z[elm[:, 0]]
        + z[elm[:, 1]]
        + z[elm[:, 2]]
        + z[elm[:, 3]]
    ) * 0.25

    return fibers_at_z(
        zc,
        zmin,
        zmax,
        angle0_deg,
        angle1_deg,
    )

def make_mmg_metric(
    pts,
    angle0_deg=-60.0,
    angle1_deg=60.0,
    ht=0.2,
    vl=0.6,
    vt=0.38,
):
    """
    Construct an MMG nodal metric aligned with the fiber field.

    ht : target element size transverse to fibers [mm]
    vl, vt : longitudinal/transverse CVs; only their ratio matters.

    The longitudinal target size is

        hl / ht = vl / vt.

    MMG metric eigenvalues are 1 / h_i^2.
    """
    if ht <= 0:
        raise ValueError("ht must be positive")

    if vl <= 0 or vt <= 0:
        raise ValueError("vl and vt must be positive")

    zmin = pts[:, 2].min()
    zmax = pts[:, 2].max()

    fib = fibers_at_z(
        pts[:, 2],
        zmin,
        zmax,
        angle0_deg,
        angle1_deg,
    )

    ratio = vl / vt
    hl = ratio * ht

    mt = 1.0 / ht**2
    ml = 1.0 / hl**2

    # M = mt I + (ml-mt) f f^T
    #
    # Build only the six independent components needed by MMG,
    # avoiding an (N,3,3) array.
    fx = fib[:, 0]
    fy = fib[:, 1]
    fz = fib[:, 2]

    metric = np.empty((pts.shape[0], 6), dtype=float)

    metric[:, 0] = mt + (ml - mt) * fx * fx      # M11
    metric[:, 1] =      (ml - mt) * fx * fy      # M12
    metric[:, 2] = mt + (ml - mt) * fy * fy      # M22
    metric[:, 3] =      (ml - mt) * fx * fz      # M13
    metric[:, 4] =      (ml - mt) * fy * fz      # M23
    metric[:, 5] = mt + (ml - mt) * fz * fz      # M33

    return metric

def write_mmg_metric(filename, metric):
    """
    Write an MMG3D anisotropic metric.

    Columns:
        M11 M12 M22 M13 M23 M33
    """
    metric = np.asarray(metric)

    if metric.ndim != 2 or metric.shape[1] != 6:
        raise ValueError("Metric must have shape (npoints, 6)")

    with open(filename, "w") as fo:
        print("MeshVersionFormatted 2", file=fo)
        print("Dimension 3", file=fo)
        print("SolAtVertices", file=fo)
        print(metric.shape[0], file=fo)
        print("1 3", file=fo)

        np.savetxt(
            fo,
            metric,
            fmt="%.10g",
        )

        print("End", file=fo)

def write_mmg_mesh(filename, pts, elm, tags):
    mesh = meshio.Mesh(
        points=pts,
        cells=[
            ("tetra", np.asarray(elm, dtype=np.int32)),
        ],
        cell_data={
            "medit:ref": [
                np.asarray(tags, dtype=np.int32),
            ]
        },
    )

    meshio.write(
        filename,
        mesh,
        file_format="medit",
    )

def read_mmg_mesh(filename, region_tag=1):
    msh = meshio.read(filename)

    pts = np.asarray(msh.points)

    blocks = [
        c.data
        for c in msh.cells
        if c.type == "tetra"
    ]

    if not blocks:
        raise RuntimeError("MMG output contains no tetrahedra")

    if len(blocks) == 1:
        elm = blocks[0]
    else:
        elm = np.concatenate(blocks, axis=0)

    # Single-region cube: no ambiguity.
    tags = np.full(
        elm.shape[0],
        region_tag,
        dtype=np.int32,
    )

    return pts, elm, tags

def run_mmg(
    mesh_in,
    metric_file,
    mesh_out,
):
    mmg = os.environ.get("MMG3D", "") or "mmg3d_O3"

    cmd = [
        mmg,
        "-in", str(mesh_in),
        "-met", str(metric_file),
        "-out", str(mesh_out),
    ]

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)

def anisotropic_remesh_mmg(
    pts,
    elm,
    tags,
    *,
    base,
    angle0_deg=-60.0,
    angle1_deg=60.0,
    ht=0.2,
    vl=0.6,
    vt=0.38,
    keep_intermediate=False,
):
    """
    Anisotropically remesh the cube using MMG.

    Input/output coordinates are in mm.
    """
    mesh_in = base.with_name(base.name + "_orig").with_suffix(".mesh")
    metric_file = base.with_name(base.name + "-met").with_suffix(".sol")
    mesh_out = base.with_name(base.name + "_aniso").with_suffix(".mesh")

    print(
        f"MMG metric: ht={ht:g} mm, "
        f"hl={ht * vl / vt:g} mm, "
        f"hl/ht={vl/vt:.3f}"
    )

    with profile("write MMG mesh"):
        write_mmg_mesh(
            mesh_in,
            pts,
            elm,
            tags,
        )

    with profile("build MMG metric"):
        metric = make_mmg_metric(
            pts,
            angle0_deg=angle0_deg,
            angle1_deg=angle1_deg,
            ht=ht,
            vl=vl,
            vt=vt,
        )

    with profile("write MMG metric"):
        write_mmg_metric(
            metric_file,
            metric,
        )

    # Important before MMG on a large mesh.
    del metric

    with profile("mmg3d"):
        run_mmg(
            mesh_in,
            metric_file,
            mesh_out,
        )

    with profile("read MMG mesh"):
        pts_new, elm_new, tags_new = read_mmg_mesh(
            mesh_out,
            region_tag=1,
        )

    print(
        f"MMG: {pts.shape[0]:,} -> {pts_new.shape[0]:,} vertices, "
        f"{elm.shape[0]:,} -> {elm_new.shape[0]:,} tetrahedra"
    )

    if not keep_intermediate:
        mesh_in.unlink(missing_ok=True)
        metric_file.unlink(missing_ok=True)
        mesh_out.unlink(missing_ok=True)

    return pts_new, elm_new, tags_new


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate an openCARP slab mesh."
    )

    parser.add_argument(
        "--geo",
        type=Path,
        default=Path("cube.geo"),
    )

    parser.add_argument(
        "--base",
        type=Path,
        default=Path("cube"),
    )

    parser.add_argument(
        "--lc",
        type=float,
        default=None,
        help="Gmsh mesh size [mm]",
    )

    parser.add_argument(
        "--binary",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write .bpts/.belem/.blon instead of ASCII files",
    )

    parser.add_argument(
        "--gmsh-binary",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use binary .msh as intermediate format",
    )

    parser.add_argument(
        "--gmsh-algo",
        default="hxt",
        help="Gmsh 3D algorithm, e.g. hxt",
    )

    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="Gmsh thread count",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1_000_000,
        help="Number of points/elements processed per output chunk",
    )

    parser.add_argument(
        "--fiber-angle0",
        type=float,
        default=-60.0,
    )

    parser.add_argument(
        "--fiber-angle1",
        type=float,
        default=60.0,
    )

    parser.add_argument(
        "--stim",
        choices=("disk", "sphere", "face"),
        default="disk",
    )

    parser.add_argument(
        "--stim-center",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        default=None,
        help="Stimulus center [mm]",
    )

    parser.add_argument(
        "--stim-radius",
        type=float,
        default=1.0,
        help="Stimulus radius [mm]",
    )

    parser.add_argument(
        "--keep-msh",
        action="store_true",
        help="Keep intermediate Gmsh .msh file",
    )

    parser.add_argument(
        "--mmg",
        action="store_true",
        help="Apply MMG anisotropic remeshing",
    )

    parser.add_argument(
        "--mmg-ht",
        type=float,
        default=0.4,
        help="MMG target transverse mesh size [mm]",
    )

    parser.add_argument(
        "--mmg-vl",
        type=float,
        default=0.6,
        help="Longitudinal CV used to define metric ratio",
    )

    parser.add_argument(
        "--mmg-vt",
        type=float,
        default=0.38,
        help="Transverse CV used to define metric ratio",
    )

    parser.add_argument(
        "--keep-mmg",
        action="store_true",
        help="Keep intermediate MMG .mesh/.sol files",
    )

    args = parser.parse_args()

    args.base.parent.mkdir(parents=True, exist_ok=True)

    msh_file = args.base.with_suffix(".msh")

    with profile("gmsh"):
        generate_gmsh(
            args.geo,
            msh_file,
            lc=args.lc,
            binary=args.gmsh_binary,
            algorithm=args.gmsh_algo,
            threads=args.threads,
        )

    pts, elm, tags = read_gmsh_tetra(msh_file)

    print(
        f"Mesh: {pts.shape[0]:,} vertices, "
        f"{elm.shape[0]:,} tetrahedra"
    )
    print("dtypes:", pts.dtype, elm.dtype, tags.dtype)
    print("Bounding box [mm]:")
    print("  min:", pts.min(axis=0))
    print("  max:", pts.max(axis=0))
    print(f"Current peak RSS: {peak_rss_gb():.2f} GB")

    if args.mmg:
        pts, elm, tags = anisotropic_remesh_mmg(
            pts,
            elm,
            tags,
            base=args.base,
            angle0_deg=args.fiber_angle0,
            angle1_deg=args.fiber_angle1,
            ht=args.mmg_ht,
            vl=args.mmg_vl,
            vt=args.mmg_vt,
            keep_intermediate=args.keep_mmg,
        )

    if args.stim_center is None:
        pmin = pts.min(axis=0)
        pmax = pts.max(axis=0)

        stim_center = 0.5 * (pmin + pmax)
        stim_center[2] = pmin[2]
    else:
        stim_center = np.asarray(
            args.stim_center,
            dtype=float,
        )

    with profile("stimulus"):
        stim = make_stimulus(
            pts,
            kind=args.stim,
            center=stim_center,
            radius=args.stim_radius,
        )

    if stim.size == 0:
        raise RuntimeError(
            f"No stimulus vertices found: "
            f"kind={args.stim}, center={stim_center}, "
            f"radius={args.stim_radius}"
        )

    if args.binary:
        pts_file = args.base.with_suffix(".bpts")
        elem_file = args.base.with_suffix(".belem")
        lon_file = args.base.with_suffix(".blon")

        with profile("write bpts"):
            write_bpts(
                pts_file,
                pts,
                args.chunk_size,
            )

        with profile("write belem"):
            write_belem(
                elem_file,
                elm,
                tags,
                args.chunk_size,
            )

        with profile("write blon"):
            write_blon(
                lon_file,
                pts,
                elm,
                args.fiber_angle0,
                args.fiber_angle1,
                args.chunk_size,
            )

    else:
        pts_file = args.base.with_suffix(".pts")
        elem_file = args.base.with_suffix(".elem")
        lon_file = args.base.with_suffix(".lon")

        with profile("write pts"):
            write_pts(
                pts_file,
                pts,
                args.chunk_size,
            )

        with profile("write elem"):
            write_elem(
                elem_file,
                elm,
                tags,
                args.chunk_size,
            )

        with profile("write lon"):
            write_lon(
                lon_file,
                pts,
                elm,
                args.fiber_angle0,
                args.fiber_angle1,
                args.chunk_size,
            )

    vtx_file = args.base.with_suffix(".vtx")
    write_vtx(vtx_file, stim)

    print(
        f"Fibres: {args.fiber_angle0:g} -> "
        f"{args.fiber_angle1:g} deg"
    )
    print(
        f"Stimulus: {args.stim}, {stim.size:,} vertices, "
        f"center={stim_center}, r={args.stim_radius:g} mm"
    )

    if not args.keep_msh:
        msh_file.unlink(missing_ok=True)

    remove_stale_mesh_files(args.base, args.binary)

    print("Written:")
    for fn in (
        pts_file,
        elem_file,
        lon_file,
        vtx_file,
    ):
        print(" ", fn)

    check_binary_mesh(args.base)


if __name__ == "__main__":
    main()

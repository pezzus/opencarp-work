#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

import meshio
import numpy as np
import pyvista as pv
from numpy import loadtxt
from rich.console import Console
from rich.progress import track


console = Console()

def get_durrer_colormap(n: int = 256):
    """Return Durrer colormap as a matplotlib colormap."""
    from matplotlib.colors import ListedColormap
    import numpy as np

    pts = np.array([
        0.05, 0.8353, 0.4157, 0.5255,
        0.10, 0.7176, 0.3725, 0.4196,
        0.15, 0.6627, 0.3059, 0.3020,
        0.20, 0.7961, 0.3412, 0.2824,
        0.25, 0.8275, 0.4902, 0.3529,
        0.30, 0.8902, 0.7294, 0.4078,
        0.35, 0.8980, 0.8745, 0.4431,
        0.40, 0.7922, 0.7412, 0.4353,
        0.45, 0.5882, 0.7137, 0.4588,
        0.50, 0.4039, 0.5882, 0.3059,
        0.55, 0.4235, 0.6431, 0.7020,
        0.60, 0.6471, 0.7529, 0.7804,
        0.65, 0.5059, 0.6196, 0.7529,
        0.70, 0.3294, 0.4510, 0.6353,
        0.75, 0.2667, 0.2471, 0.3725,
        0.80, 0.5961, 0.3647, 0.3098,
        0.85, 0.4353, 0.3490, 0.2980,
        0.90, 0.4863, 0.4510, 0.4235,
        0.95, 0.3490, 0.3373, 0.3020,
        1.00, 0.2314, 0.1922, 0.1882,
    ]).reshape(-1, 4)

    x = pts[:, 0]
    rgb = pts[:, 1:]

    xi = np.linspace(0.0, 1.0, n)
    colors = np.column_stack([
        np.interp(xi, x, rgb[:, 0]),
        np.interp(xi, x, rgb[:, 1]),
        np.interp(xi, x, rgb[:, 2]),
    ])

    return ListedColormap(colors, name="Durrer")


def from_carp(meshbase: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Read an openCARP mesh from .pts/.elem or .bpts/.belem."""
    pts, elm, _ = from_carp_with_tags(meshbase)
    return pts, elm


def from_carp_with_tags(meshbase: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read an openCARP mesh and keep the element tag column."""
    meshbase = Path(meshbase)

    bpts = meshbase.with_suffix(".bpts")
    ptsf = meshbase.with_suffix(".pts")
    belem = meshbase.with_suffix(".belem")
    elemf = meshbase.with_suffix(".elem")

    if bpts.is_file():
        with open(bpts, "rb") as fi:
            num_pts = int(fi.read(1024).split()[0])
            pts = np.fromfile(fi, dtype=np.float32).reshape((num_pts, -1))
    elif ptsf.is_file():
        with open(ptsf, "r") as fi:
            num_pts = int(fi.readline().strip())
            pts = loadtxt(fi, dtype=float)
        if pts.shape[0] != num_pts:
            raise ValueError("Invalid point file: number of points mismatch")
    else:
        raise FileNotFoundError(f"Cannot find {meshbase}.(bpts|pts)")

    if belem.is_file():
        with open(belem, "rb") as fi:
            nelm = int(fi.read(1024).split()[0])
            elm = np.fromfile(fi, dtype=np.int32).reshape((nelm, -1))
            tags = elm[:, -1]
            elm = elm[:, 1:-1]
    elif elemf.is_file():
        with open(elemf, "r") as fi:
            num_elm = int(fi.readline().strip())
            ncols = len(fi.readline().split())
        data = loadtxt(elemf, dtype=int, skiprows=1, usecols=range(1, ncols))
        data = np.atleast_2d(data)
        if data.shape[0] != num_elm:
            raise ValueError("Invalid .elem file: number of elements mismatch")
        tags = data[:, -1]
        elm = data[:, 0:-1]
    else:
        raise FileNotFoundError(f"Cannot find {meshbase}.(belem|elem)")

    return pts, elm, tags


def to_pyvista(pts: np.ndarray, elm: np.ndarray) -> pv.UnstructuredGrid:
    """Convert points/elements to a PyVista UnstructuredGrid."""
    cells = np.c_[elm.shape[1] * np.ones(elm.shape[0], dtype=int), elm].ravel()

    if elm.shape[1] == 4:
        cell_type = pv.CellType.TETRA * np.ones(elm.shape[0], dtype=np.uint8)
    elif elm.shape[1] == 3:
        cell_type = pv.CellType.TRIANGLE * np.ones(elm.shape[0], dtype=np.uint8)
    else:
        raise RuntimeError(f"Element type with {elm.shape[1]} nodes not supported")

    return pv.UnstructuredGrid(cells, cell_type, pts)


def meshio_cell_type(elm: np.ndarray) -> str:
    if elm.shape[1] == 4:
        return "tetra"
    if elm.shape[1] == 3:
        return "triangle"
    raise RuntimeError(f"Element type with {elm.shape[1]} nodes not supported")


def read_igb_header(filename: str | Path) -> dict:
    """Read and parse the 1024-byte header of an IGB file."""
    filename = Path(filename)
    with open(filename, "rb") as f:
        buf = f.read(1024)

    hdr_lines = buf.decode(errors="replace").split("\r\n")
    comments = [line.strip()[2:] for line in hdr_lines if line.startswith("#")]

    items = []
    for line in hdr_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.extend(line.split())

    hdr = dict(tuple(item.split(":", 1)) for item in items if ":" in item)
    hdr["comments"] = comments

    for key in "xyzt":
        if key in hdr:
            hdr[key] = int(hdr[key])

    for key in ("zero", "facteur"):
        if key in hdr:
            hdr[key] = float(hdr[key])

    return hdr


def read_igb(
    filename: str | Path,
    convert_to_float: bool = False,
    incomplete_ok: bool = True,
):
    """Read IGB file into NumPy format."""
    dtypes = {
        "byte": np.uint8,
        "char": np.int8,
        "short": np.int16,
        "long": np.int32,
        "float": np.float32,
        "double": np.float64,
    }

    filename = Path(filename)
    hdr = read_igb_header(filename)

    nx, ny, nz = hdr["x"], hdr["y"], hdr["z"]
    nt = hdr.get("t", 1)
    dtype = dtypes[hdr["type"]]

    data = np.fromfile(filename, dtype=dtype, count=nx * ny * nz * nt, offset=1024)

    if incomplete_ok:
        nt = int(np.floor(data.shape[0] / (nx * ny * nz)))

    shape = (nt, nz, ny, nx) if nt > 1 else (nz, ny, nx)
    data = data[: nt * nz * ny * nx].reshape(shape)

    if convert_to_float:
        data = hdr.get("facteur", 1.0) * data + hdr.get("zero", 0.0)

    return data


def extract_dt_from_par(par_file: Path) -> float | None:
    """Try to extract timedt from an openCARP .par file."""
    if not par_file.exists():
        return None

    patterns = [
        r"^\s*-timedt\s+([0-9.eE+-]+)",
        r"^\s*timedt\s*=\s*([0-9.eE+-]+)",
        r"^\s*timedt\s+([0-9.eE+-]+)",
    ]

    text = par_file.read_text(errors="ignore")
    for line in text.splitlines():
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                return float(m.group(1))

    return None


def get_dt(output_dir: Path, dt_arg: float | None) -> float:
    """Use input dt if given, otherwise try to read it from parameters.par."""
    if dt_arg is not None:
        return dt_arg

    dt = extract_dt_from_par(output_dir / "parameters.par")
    if dt is not None:
        console.print(f"[green]Detected dt={dt} from parameters.par[/green]")
        return dt

    raise RuntimeError("Could not determine dt. Pass --dt or provide parameters.par.")


def extract_value_from_par(par_file: Path, key: str) -> str | None:
    """Extract the last value assigned to `key` in an openCARP .par file."""
    if not par_file.exists():
        return None

    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*$")
    value = None

    for line in par_file.read_text(errors="ignore").splitlines():
        # Remove comments after value
        line = line.split("#", 1)[0].strip()
        if not line:
            continue

        match = pattern.match(line)
        if match:
            value = match.group(1).strip().strip('"').strip("'")

    return value


def get_meshbase(output_dir: Path, suffix: str = "_m") -> Path:
    """Get mesh base name (from meshname in parameters.par) and map to output_dir."""
    meshname = extract_value_from_par(output_dir / "parameters.par", "meshname")
    if meshname is None:
        raise RuntimeError("Could not find meshname in parameters.par")

    # Take only the basename (ignore original path)
    meshbase_name = Path(meshname).name

    # openCARP writes emigrid_* in output_dir
    return output_dir / f"{meshbase_name}{suffix}"


def compute_activation(vm: np.ndarray, threshold: float, dt: float) -> np.ndarray:
    """Compute first upward threshold crossing per location."""
    act = np.full(vm.shape[1], np.nan, dtype=np.float32)

    for k in track(range(1, vm.shape[0]), description="Computing activation"):
        crossed = (vm[k, :] > threshold) & (vm[k - 1, :] < threshold) & np.isnan(act)
        if not np.any(crossed):
            continue

        old = vm[k - 1, crossed]
        new = vm[k, crossed]
        denom = new - old
        good = denom != 0

        idx = np.flatnonzero(crossed)
        vals = np.full(idx.shape[0], np.nan, dtype=np.float32)
        vals[good] = dt * k - (new[good] - threshold) / denom[good] * dt
        act[idx] = vals

    return act


def write_timeseries_xdmf(
    filename: Path,
    pts: np.ndarray,
    elm: np.ndarray,
    data: np.ndarray,
    name: str,
    dt: float,
    association: str,
) -> None:
    """Write a time series XDMF file readable by ParaView."""
    cell_type = meshio_cell_type(elm)

    with meshio.xdmf.TimeSeriesWriter(filename) as writer:
        writer.write_points_cells(pts, cells={cell_type: elm})

        for k in track(range(data.shape[0]), description=f"Writing {name}"):
            t = dt * k
            if association == "cell":
                writer.write_data(t, cell_data={name: [data[k, :]]})
            elif association == "point":
                writer.write_data(t, point_data={name: data[k, :]})
            else:
                raise ValueError("association must be 'cell' or 'point'")
        
    console.print(f"Wrote {filename}")


def save_mesh_vtkhdf(
    filename: Path,
    pts: np.ndarray,
    elm: np.ndarray,
    cell_data: dict[str, np.ndarray] | None = None,
) -> None:
    mesh = to_pyvista(pts, elm)
    if cell_data is not None:
        for name, data in cell_data.items():
            mesh.cell_data[name] = data
    mesh.save(filename)
    console.print(f"Wrote {filename}")


def save_activation_vtkhdf(filename: Path, pts: np.ndarray, elm: np.ndarray, act: np.ndarray) -> None:
    mesh = to_pyvista(pts, elm)
    mesh.cell_data["act"] = act
    mesh.save(filename)
    console.print(f"Wrote {filename}")


def read_part_file(part_file: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read an openCARP .part file as tag -> partition pairs."""
    data = np.loadtxt(part_file, dtype=np.int64)
    data = np.atleast_1d(data)
    if data.size < 1:
        raise ValueError(f"Invalid part file: {part_file}")

    expected = int(data[0])
    rest = data[1:]
    if rest.size != expected:
        raise ValueError(
            f"Invalid part file: expected {expected} integers after the header, got {rest.size}"
        )
    if expected % 2 != 0:
        raise ValueError(
            f"Invalid part file: expected an even number of integers after the header, got {expected}"
        )

    pairs = rest.reshape(expected // 2, 2)
    return pairs[:, 0], pairs[:, 1]


def export_vm(
    output_dir: Path,
    out_dir: Path,
    dt: float,
    threshold: float,
    write_vm: bool,
    write_act: bool,
    render_act: bool,
) -> None:
    meshbase = get_meshbase(output_dir, "_m")
    vm_file = output_dir / "vm.igb"

    console.print(f"[bold]Reading myocardium mesh:[/bold] {meshbase}")
    pts, elm = from_carp(meshbase)

    console.print(f"[bold]Reading Vm:[/bold] {vm_file}")
    vm = read_igb(vm_file, convert_to_float=True).squeeze()
    vm = np.ascontiguousarray(vm)
    console.print(f"Vm shape: {vm.shape}, dtype: {vm.dtype}")

    if write_vm:
        write_timeseries_xdmf(
            out_dir / "vm.xdmf",
            pts,
            elm,
            vm,
            name="vm",
            dt=dt,
            association="cell",
        )

    if write_act or render_act:
        act = compute_activation(vm, threshold=threshold, dt=dt)
        if write_act:
            save_activation_vtkhdf(out_dir / "act.vtkhdf", pts, elm, act)
        if render_act:
            render_activation_png(out_dir / "act.png", pts, elm, act)

def export_phie(output_dir: Path, out_dir: Path, dt: float) -> None:
    meshbase = get_meshbase(output_dir, "_e")
    phie_file = output_dir / "phie.igb"

    console.print(f"[bold]Reading extracellular mesh:[/bold] {meshbase}")
    pts, elm = from_carp(meshbase)

    console.print(f"[bold]Reading phie:[/bold] {phie_file}")
    phie = read_igb(phie_file, convert_to_float=True).squeeze()
    phie = np.ascontiguousarray(phie)
    console.print(f"phie shape: {phie.shape}, dtype: {phie.dtype}")

    write_timeseries_xdmf(
        out_dir / "phie.xdmf",
        pts,
        elm,
        phie,
        name="phie",
        dt=dt,
        association="point",
    )


def render_activation_png(
    filename: Path,
    pts: np.ndarray,
    elm: np.ndarray,
    act: np.ndarray,
    window_size: list[int] = [1600, 1200],
) -> None:
    """Render activation map to a PNG using PyVista off-screen rendering."""
    mesh = to_pyvista(pts, elm)
    mesh.cell_data["act"] = act

    plotter = pv.Plotter(off_screen=True, window_size=window_size)
    plotter.add_mesh(
        mesh,
        scalars="act",
        cmap=get_durrer_colormap(),
        show_edges=False,
        nan_color="lightgray",
        scalar_bar_args={"title": "Activation [ms]"},
    )
    plotter.camera.zoom(1.2)
    plotter.add_axes()
    plotter.screenshot(filename)
    plotter.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert openCARP output to ParaView-readable files."
    )
    parser.add_argument(
        "path",
        type=Path,
        help="openCARP output directory for postprocess mode, or mesh basename for input-mesh mode.",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path("paraview_output"),
        help="Directory where converted files are written.",
    )
    parser.add_argument(
        "--dt",
        type=float,
        default=None,
        help="Time step. If omitted, try to extract timedt from parameters.par.",
    )
    parser.add_argument(
        "--act-threshold",
        type=float,
        default=-60.0,
        help="Activation threshold for Vm.",
    )
    parser.add_argument(
        "--vm",
        action="store_true",
        help="Write Vm time series vm.xdmf.",
    )
    parser.add_argument(
        "--act",
        action="store_true",
        help="Compute and write activation map act.vtkhdf.",
    )
    parser.add_argument(
        "--phie",
        action="store_true",
        help="Write extracellular potential time series phie.xdmf.",
    )
    parser.add_argument(
        "--mesh",
        action="store_true",
        help="Write myocardium mesh only as mesh_m.vtkhdf.",
    )
    parser.add_argument(
        "--input-mesh",
        action="store_true",
        help="Inspect an input mesh basename and write a VTK file with tag and partition data.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Write Vm, activation, phie, and mesh in postprocess mode.",
    )
    parser.add_argument(
        "--render-act",
        action="store_true",
        help="Render activation map to act.png.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    target = args.path.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.input_mesh:
        if any([args.vm, args.act, args.phie, args.mesh, args.render_act, args.all]):
            raise SystemExit("--input-mesh cannot be combined with postprocess options")

        meshbase = target
        part_file = meshbase.with_suffix(".part")

        console.print(f"[bold]Input mesh:[/bold] {meshbase}")
        console.print(f"[bold]Output:[/bold] {out_dir}")

        pts, elm, tags = from_carp_with_tags(meshbase)

        cell_data: dict[str, np.ndarray] = {"tag": tags.astype(np.int32, copy=False)}
        if part_file.is_file():
            console.print(f"[bold]Reading partition map:[/bold] {part_file}")
            part_tags, part_ids = read_part_file(part_file)
            tag_to_part = {int(tag): int(part) for tag, part in zip(part_tags, part_ids)}

            missing = sorted(set(np.unique(tags).tolist()) - set(tag_to_part.keys()))
            if missing:
                raise ValueError(f"Partition file does not cover tags: {missing[:10]}")

            partitions = np.array([tag_to_part[int(tag)] for tag in tags], dtype=np.int32)
            cell_data["partition"] = partitions
        else:
            console.print(f"[yellow]No partition file found:[/yellow] {part_file}")

        save_mesh_vtkhdf(out_dir / f"{meshbase.name}.vtkhdf", pts, elm, cell_data)
        console.print("[green bold]Done.[/green bold]")
        return

    if args.all:
        args.vm = True
        args.act = True
        args.phie = True
        args.mesh = True
        args.render_act = True

    if not any([args.vm, args.act, args.phie, args.mesh, args.render_act]):
        console.print("[yellow]Nothing selected. Use --vm, --act, --phie, --mesh, or --all.[/yellow]")
        return

    output_dir = target
    dt = get_dt(output_dir, args.dt)

    console.print(f"[bold]Input:[/bold]  {output_dir}")
    console.print(f"[bold]Output:[/bold] {out_dir}")
    console.print(f"[bold]dt:[/bold]     {dt}")

    if args.mesh:
        pts, elm = from_carp(output_dir / "emigrid_m")
        save_mesh_vtkhdf(out_dir / "mesh_m.vtkhdf", pts, elm)

    if args.vm or args.act or args.render_act:
        export_vm(
            output_dir=output_dir,
            out_dir=out_dir,
            dt=dt,
            threshold=args.act_threshold,
            write_vm=args.vm,
            write_act=args.act,
            render_act=args.render_act,
        )

    if args.phie:
        export_phie(output_dir=output_dir, out_dir=out_dir, dt=dt)

    console.print("[green bold]Done.[/green bold]")


if __name__ == "__main__":
    main()

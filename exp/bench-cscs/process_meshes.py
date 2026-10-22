import pyvista as pv
import meshio
from pathlib import Path
import numpy as np

def from_carp(meshbase: str | Path):
    meshbase = Path(meshbase)

    # --------------------------------------------------------------
    # Points
    # --------------------------------------------------------------

    bptsf = meshbase.with_suffix(".bpts")
    ptsf = meshbase.with_suffix(".pts")

    if bptsf.is_file():
        with bptsf.open("rb") as fi:
            npts = int(fi.read(1024).split()[0])
            pts = np.fromfile(
                fi,
                dtype=np.float32,
            ).reshape((npts, 3))

    elif ptsf.is_file():
        with ptsf.open() as fi:
            npts = int(fi.readline())
            pts = np.loadtxt(fi)

        if pts.shape[0] != npts:
            raise ValueError("Invalid .pts file")

    else:
        raise FileNotFoundError(
            f"Could not find {bptsf} or {ptsf}"
        )

    # --------------------------------------------------------------
    # Elements
    # --------------------------------------------------------------

    belemf = meshbase.with_suffix(".belem")
    elemf = meshbase.with_suffix(".elem")

    if belemf.is_file():
        with belemf.open("rb") as fi:
            nelem = int(fi.read(1024).split()[0])

            data = np.fromfile(
                fi,
                dtype=np.int32,
            )

        # Tetra binary record:
        #
        #   type i j k l tag
        #
        if data.size != 6 * nelem:
            raise ValueError(
                f"Invalid .belem file: expected "
                f"{6 * nelem} int32 values, got {data.size}"
            )

        data = data.reshape((nelem, 6))

        etype = data[:, 0]

        if not np.all(etype == 0):
            raise ValueError(
                "Only tetrahedral .belem meshes are supported; "
                f"found element types {np.unique(etype)}"
            )

        elm = data[:, 1:5]
        tags = data[:, 5]

    elif elemf.is_file():
        with elemf.open() as fi:
            nelem = int(fi.readline())
            first = fi.readline().split()

        data = np.loadtxt(
            elemf,
            skiprows=1,
            usecols=range(1, len(first)),
            dtype=int,
        )
        data = np.atleast_2d(data)

        if data.shape[0] != nelem:
            raise ValueError("Invalid .elem file")

        elm = data[:, :-1]
        tags = data[:, -1]

    else:
        raise FileNotFoundError(
            f"Could not find {belemf} or {elemf}"
        )

    return pts, elm, tags

def to_pyvista(pts, elm):
    nelem = elm.shape[0]
    ndim = elm.shape[1]

    cells = np.empty(
        (nelem, ndim + 1),
        dtype=np.int64,
    )
    cells[:, 0] = ndim
    cells[:, 1:] = elm

    cell_types = np.full(
        nelem,
        {3: pv.CellType.TRIANGLE, 4: pv.CellType.TETRA}[ndim],
        dtype=np.uint8,
    )

    return pv.UnstructuredGrid(
        cells.ravel(),
        cell_types,
        pts,
    )

if __name__ == "__main__":

    if False:
        pts, elm, lon = from_carp('P1_450um_spl')
        pts *= 1e-3
        grid = to_pyvista(pts, elm)
        grid = grid.clean()
        grid.save('P1_450um_spl.vtu')
        pts = grid.points
        elm = grid.cell_connectivity.reshape((-1,4))
        meshio.Mesh(pts, cells={'tetra':elm}).write('P1_450um_spl.mesh')

        surf = grid.extract_surface(algorithm=None).cast_to_unstructured_grid()
        surf.save('P1_450um_spl_surf.vtu')

        pts = surf.points
        elm = surf.cell_connectivity.reshape((-1,3))

        meshio.Mesh(pts, cells={'triangle':elm}).write('P1_450um_spl_surf.mesh')

    else:
        msh = meshio.read('P1_450um_spl_surf.o.mesh')
        pts = msh.points
        elm = msh.cells_dict['triangle']
        grid = to_pyvista(pts, elm)
        grid.save('P1_450um_spl_surf.o.mesh.vtu')

        msh = meshio.read('P1_200um_spl.mesh')
        pts = msh.points
        elm = msh.cells_dict['tetra']
        grid = to_pyvista(pts, elm)
        grid.save('P1_200um_spl.mesh.vtu')


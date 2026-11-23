# Third-party software

LaminarFloor application source is distributed under GNU GPL v3.0; see [LICENSE](LICENSE). Third-party components retain their own copyrights and licenses.

| Component | Version | License and source |
|---|---|---|
| CPython | 3.10 family | [PSF](https://github.com/python/cpython/tree/3.10) |
| NumPy | 1.26.4 | [BSD-3-Clause](https://github.com/numpy/numpy/tree/v1.26.4) |
| SciPy | 1.15.3 | [BSD-3-Clause](https://github.com/scipy/scipy/tree/v1.15.3) |
| Shapely / GEOS | Shapely 2.1.2 | [BSD / LGPL](https://github.com/shapely/shapely/tree/2.1.2) |
| trimesh | 5.2.0 | [MIT](https://github.com/mikedh/trimesh/tree/5.2.0) |
| manifold3d | 3.5.4 | [Apache-2.0](https://github.com/elalish/manifold/tree/v3.5.4) |
| ezdxf | 1.4.5 | [MIT](https://github.com/mozman/ezdxf/tree/v1.4.5) |
| PyMeshLab / MeshLab / VCGLib | PyMeshLab 2025.7.post1 | [GPL-3.0 and source](https://github.com/cnr-isti-vclab/PyMeshLab) |
| PySide2 / Shiboken2 | 5.15.2.1 | [Qt for Python source; LGPL/GPL](https://code.qt.io/cgit/pyside/pyside-setup.git/?h=5.15.2.1) |
| Qt | 5.15 family | [Source archives; LGPL/GPL by module](https://download.qt.io/archive/qt/5.15/) |
| PyInstaller | 6.22.0 | [GPL with bootloader exception](https://github.com/pyinstaller/pyinstaller/tree/v6.22.0) |
| Microsoft runtime | msvc-runtime 14.42.34433; UCRT 10.0.17763.132 | Microsoft redistribution terms in the bundled notice |

PyMeshLab: Alessandro Muntoni, Paolo Cignoni and contributors, ISTI-CNR. See [project attribution](https://pymeshlab.readthedocs.io/en/latest/about.html). MeshLab, VCGLib and their dependencies retain their upstream notices.

## Source access and build modifications

Application sources and build patches are in this repository. Exact Python versions and download hashes are in `native/requirements-pyinstaller.txt`. Dependency source releases are supplied by the upstream repositories linked above and their matching PyPI release pages. For PyMeshLab use the release corresponding to 2025.7.post1 and initialize pinned submodules recursively: the top-level GitHub archive alone omits submodule contents. Qt/PySide provide source archives and build instructions at the links above.

The application build does not modify Qt or PyMeshLab DLLs. Its PyInstaller analysis and bootloader modifications are contained in `native/prepare_qt5.py` and `native/build_pyinstaller.py`, including the pinned original archive hash.

## Notices included with this release

`licenses/bundled/` contains notices extracted directly from the executable. `licenses/` also includes Python, NumPy, SciPy, Qt LGPL/GPL and PyInstaller license texts. Upstream source distributions may contain further component-specific notices. These licenses apply to their respective components.

The release attachment `THIRD-PARTY-NOTICES.zip` packages these notices, the application license, dependency lock and build instructions alongside the executable. Preserve applicable notices when redistributing.

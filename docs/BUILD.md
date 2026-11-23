# Build the Windows v6 preview

This source snapshot corresponds to commit `13020b05887560fbb9afe5d6a9fed087626f2886` and the executable built on 19 November 2026. Original source hashes are in [source-manifest.json](source-manifest.json). The EXE was not rebuilt for publication.

## Prerequisites

Windows x64 build machine; Python 3.10 x64; Microsoft Visual C++ Build Tools in a developer command prompt; Windows SDK 10.0.17763 with app-local UCRT 10.0.17763.132. The UCRT hash is pinned in `native/LaminarFloor.spec`. Network access is needed to obtain build dependencies.

## Build

From the repository root, with Python 3.10 selected:

```bat
native\build_pyinstaller.bat
```

Output: `dist\native\LaminarFloor-Win81.exe`.

The script creates `.native-venv`, installs dependencies pinned with hashes in `native/requirements-pyinstaller.txt`, adjusts PyInstaller's analysis import order, builds a patched bootloader and packages the application. `native/build_pyinstaller.py` verifies the upstream PyInstaller source archive hash before applying the recorded patches. `native/prepare_qt5.py` changes the analysis order, not Qt DLLs. PyMeshLab must initialize before PySide2.

The patched loader executes the extracted payload in one process. Payloads are stored under `%TEMP%\LaminarFloorWin81`; runtime cleanup targets inactive application payloads.

## Run from source

After installing the pinned dependencies:

```bat
.native-venv\Scripts\python native_main.py
```

## Focused v6 tests

```bat
.native-venv\Scripts\python -m pip install pytest
.native-venv\Scripts\python -m pytest tests/test_v6_core.py tests/test_v6_ui.py
```

These are instructions, not a claim of a new test run during publication. `native/acceptance.py` is retained because it is bundled in the original executable, but its historical project-save/load scenario is not valid acceptance coverage for v6.

## Limitations

The build sources and dependency hashes are provided; byte-for-byte reproducibility is not promised. Compiler and Python patch versions can affect output. Clean-Windows testing was not repeated for v6. Do not interpret a successful build as full runtime validation.

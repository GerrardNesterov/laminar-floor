@echo off
setlocal
cd /d "%~dp0\.."
python -m venv .native-venv || exit /b 1
.native-venv\Scripts\python -m pip install --require-hashes -r native\requirements-pyinstaller.txt || exit /b 1
.native-venv\Scripts\python native\prepare_qt5.py || exit /b 1
.native-venv\Scripts\python native\build_pyinstaller.py || exit /b 1
.native-venv\Scripts\python -m PyInstaller --clean --noconfirm --distpath dist\native --workpath build\native-pyinstaller native\LaminarFloor.spec || exit /b 1

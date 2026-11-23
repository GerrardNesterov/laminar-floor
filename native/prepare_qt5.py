"""Keep the verified MeshLab-before-Shiboken initialization order in analysis.

PySide2 initialization before MeshLab crashes even with identical Qt DLLs.
Do not change wheel binaries; mirror the application startup order instead.
"""
from pathlib import Path
import PyInstaller
from importlib import metadata
import base64
import hashlib


def prepare():
    # Reject a venv left over from the discarded DLL-normalization experiment.
    dist = metadata.distribution('PySide2')
    for item in dist.files or []:
        if item.name.startswith('Qt5') and item.suffix.lower() == '.dll':
            actual = base64.urlsafe_b64encode(hashlib.sha256(Path(dist.locate_file(item)).read_bytes()).digest()).decode().rstrip('=')
            assert item.hash and item.hash.mode == 'sha256' and item.hash.value == actual, 'Modified PySide2 DLL; recreate .native-venv from the hash lock'
    assert PyInstaller.__version__ == '6.22.0'
    path = Path(PyInstaller.__file__).parent / 'building/build_main.py'
    source = path.read_text(encoding='utf-8')
    original = '        import_packages = qt_packages + other_packages'
    replacement = "        import_packages = ([p for p in other_packages if p == 'pymeshlab'] + qt_packages + [p for p in other_packages if p != 'pymeshlab'])"
    if replacement not in source:
        assert source.count(original) == 1, 'Pinned PyInstaller import-order anchor changed'
        path.write_text(source.replace(original, replacement), encoding='utf-8')


if __name__ == '__main__':
    prepare()

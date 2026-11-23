"""Build a pinned, single-process Windows PyInstaller bootloader locally."""
import hashlib,shutil,subprocess,sys,tarfile,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
folder=root/'build/pyinstaller-source';folder.mkdir(parents=True,exist_ok=True)
archive=folder/'source.tar.gz'
url='https://files.pythonhosted.org/packages/05/03/669d06735cf57d7e2e5dfc3c2e1643554b34c559eff21449c58172ca0335/pyinstaller-6.22.0.tar.gz'
if not archive.exists():urllib.request.urlretrieve(url,archive)
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='8b0166fff4583b374bbe7fa044bf03ddc33cab0f792a665807d96048e63f060e'
with tarfile.open(archive) as package:package.extractall(folder)  # Exact trusted archive SHA256 verified above.
source=folder/'pyinstaller-6.22.0';main=source/'bootloader/src/pyi_main.c'
s=main.read_text()
anchor='    pyi_win32_free_security_descriptor(&pyi_ctx->security_attr);\n#endif'
assert s.count(anchor)==1
s=s.replace(anchor,anchor+'''
#if defined(_WIN32)
    /* LaminarFloor: execute the extracted payload in this process. Loaded
       DLLs are reclaimed by the next launch, not by a second launcher. */
    pyi_ctx->process_level = PYI_PROCESS_LEVEL_MAIN;
    if (pyi_setenv("_PYI_PARENT_PROCESS_LEVEL", "1") < 0) return -1;
    return _pyi_main_onedir_or_onefile_child(pyi_ctx);
#endif
''')
anchor='if (pyi_ctx->is_onefile && pyi_ctx->process_level == PYI_PROCESS_LEVEL_PARENT) {\n        const wchar_t *dll_names[]'
assert s.count(anchor)==1
s=s.replace(anchor,'if (0) { /* Single-process payload uses its bundled VC runtime. */\n        const wchar_t *dll_names[]')
main.write_text(s)
p=source/'bootloader/src/pyi_utils_win32.c';s=p.read_text()
anchor='swprintf(prefix, 16, L"_MEI%d", _getpid());';assert s.count(anchor)==1
p.write_text(s.replace(anchor,'swprintf(prefix, 16, L"run_%d_", _getpid());'))
original_bootloader=hashlib.sha256((source/'PyInstaller/bootloader/Windows-64bit-intel/runw.exe').read_bytes()).digest()
subprocess.run([sys.executable,'waf','all'],cwd=source/'bootloader',check=True)
import PyInstaller
assert PyInstaller.__version__=='6.22.0'
relative=Path('bootloader/Windows-64bit-intel/runw.exe')
rebuilt=source/'PyInstaller'/relative;target=Path(PyInstaller.__file__).parent/relative
assert rebuilt.is_file() and target.is_file()
assert hashlib.sha256(rebuilt.read_bytes()).digest()!=original_bootloader
shutil.copy2(rebuilt,target)
print('Single-process bootloader SHA256:',hashlib.sha256(target.read_bytes()).hexdigest())

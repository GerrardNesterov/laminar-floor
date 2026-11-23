from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata
from pathlib import Path
import os,json,hashlib
from importlib import metadata
root=Path(SPECPATH).parent
datas=[(str(path),'examples') for path in (root/'examples').glob('*.dxf')]+[(str(root/'assets'),'assets')]
binaries=[]
ucrt_root=Path(os.environ['ProgramFiles(x86)'])/'Windows Kits/10/Redist'
ucrt_candidates=list(ucrt_root.glob('*/ucrt/DLLs/x64'))+list(ucrt_root.glob('ucrt/DLLs/x64'))
assert ucrt_candidates, 'Windows SDK app-local UCRT redist required for Windows8.1'
# Pin the actual downlevel runtime already verified on Windows8.1, not an
# arbitrary newest SDK from the build machine. SDK17763 supplies this UCRT.
ucrt_hash='eb89af5911a60d892a685181c397d32b72c61dc2ad77dd45b8cac0fbb7602b84'
ucrt_matches=[path for path in ucrt_candidates if (path/'ucrtbase.dll').is_file() and hashlib.sha256((path/'ucrtbase.dll').read_bytes()).hexdigest()==ucrt_hash]
assert ucrt_matches, 'Install Windows SDK10.0.17763 with pinned UCRT10.0.17763.132'
ucrt=sorted(ucrt_matches)[0]
ucrt_dlls=sorted(ucrt.glob('*.dll'))
for dll in ucrt_dlls:binaries.append((str(dll),'.'))
ucrt_evidence=root/'build/native-evidence/ucrt.json';ucrt_evidence.parent.mkdir(parents=True,exist_ok=True)
ucrt_evidence.write_text(json.dumps({'path':str(ucrt),'version':'10.0.17763.132','sha256':{dll.name:hashlib.sha256(dll.read_bytes()).hexdigest() for dll in ucrt_dlls}},indent=2))
print('Pinned app-local UCRT:',ucrt)
for item in metadata.distribution('msvc-runtime').files or []:
    path=Path(metadata.distribution('msvc-runtime').locate_file(item))
    if path.suffix.lower()=='.dll' and path.is_file():binaries.append((str(path),'.'))
for name in ('pymeshlab','ezdxf'):datas+=collect_data_files(name)
for name in ('pymeshlab','manifold3d','shapely','numpy','scipy'):binaries+=collect_dynamic_libs(name)
for name in ('pymeshlab','ezdxf','trimesh','manifold3d','shapely','msvc-runtime'):datas+=copy_metadata(name)
hidden=collect_submodules('laminar')+['pymeshlab.pmeshlab']+['scipy.spatial.transform._rotation','native.acceptance','native.runtime']
a=Analysis([str(root/'native_main.py')],pathex=[str(root)],binaries=binaries,datas=datas,hiddenimports=hidden,
           runtime_hooks=[str(root/'native/rth_mesh_first.py')],
           excludes=['sitecustomize','streamlit','plotly','pytest','matplotlib','IPython','notebook','PyQt5','PyQt6','PySide6','pymeshlab.tests','tkinter'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='LaminarFloor-Win81',debug=False,strip=False,upx=False,console=False,
        runtime_tmpdir=r'%TEMP%\LaminarFloorWin81',icon=str(root/'assets/laminar.ico'))

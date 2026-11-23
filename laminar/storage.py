"""Writable per-user state, independent of the installation and current directory."""
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import tempfile


def resources():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))


def user_directory():
    if override:=os.environ.get('LAMINAR_USER_DIR'):
        root=Path(override)
    elif sys.platform=='win32':
        root=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))/'LaminarFloor'
    elif sys.platform=='darwin':
        root=Path.home()/'Library'/'Application Support'/'LaminarFloor'
    else:
        root=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'LaminarFloor'
    root.mkdir(parents=True,exist_ok=True)
    return root


def atomic_write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    name=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
            name=f.name;f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if name and Path(name).exists():Path(name).unlink()


def save_project(name,cfg):
    cfg.validate()
    name=name.strip()
    if name.upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        raise ValueError('Название зарезервировано Windows; выберите другое')
    if not re.fullmatch(r'[\w \-]{1,80}',name,flags=re.UNICODE):
        raise ValueError('Название: 1–80 букв, цифр, пробелов или дефисов')
    path=user_directory()/'Projects'/(name.strip()+'.json')
    if path.exists():atomic_write(path.with_suffix('.json.bak'),path.read_bytes())
    atomic_write(path,cfg.dumps().encode('utf-8'))
    return path


def import_profile(name,raw):
    path=user_directory()/'DXF'/(hashlib.sha256(raw).hexdigest()[:16]+'.dxf')
    atomic_write(path,raw)
    return str(path)


def configure_logging():
    root=user_directory()/'Logs';root.mkdir(exist_ok=True)
    logger=logging.getLogger('laminar')
    if not logger.handlers:
        handler=RotatingFileHandler(root/'application.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler);logger.setLevel(logging.INFO)
    return logger

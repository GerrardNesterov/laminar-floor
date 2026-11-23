"""Initialize MeshLab before PySide2, preserving early startup diagnostics."""
import ctypes
import json
import os
from pathlib import Path
import sys
import traceback
from laminar.storage import configure_logging, user_directory

try:
    configure_logging().info('Starting bundled MeshLab before PySide2')
    from native.runtime import maintain_payload, configure_platform_queries
    configure_platform_queries()
    maintain_payload()
    import pymeshlab
except Exception:
    detail = traceback.format_exc()
    try:
        configure_logging().error('Early bundled startup failed\n%s', detail)
    except OSError:
        pass
    index = sys.argv.index('--acceptance') + 1 if '--acceptance' in sys.argv else None
    if os.environ.get('LAMINAR_ACCEPTANCE') == '1' and index is not None and index < len(sys.argv):
        folder = Path(sys.argv[index])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'acceptance.json').write_text(json.dumps({'passed': False, 'error': detail}), encoding='utf-8')
    else:
        ctypes.windll.user32.MessageBoxW(None, 'Не удалось загрузить LaminarFloor.\nЖурнал: ' + str(user_directory() / 'Logs/application.log'), 'LaminarFloor', 0x10)
    raise SystemExit(1)

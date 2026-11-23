"""Windows8.1 native entry, including diagnostics before Qt can be imported."""
import json,os,sys,traceback
from pathlib import Path
from laminar.storage import configure_logging,user_directory

def main():
    try:
        logger=configure_logging()
        logger.info('Startup: %s; Python %s',sys.getwindowsversion() if sys.platform=='win32' else sys.platform,sys.version)
        from native.runtime import maintain_payload,configure_platform_queries
        configure_platform_queries()
        maintain_payload()
        import pymeshlab  # Initialize before Shiboken/PySide2; see native/prepare_qt5.py.
        from PySide2.QtWidgets import QApplication
        from PySide2.QtGui import QFont
        app=QApplication(sys.argv);app.setApplicationName('LaminarFloor');app.setFont(QFont('Segoe UI',10))
        from laminar.native_ui import MainWindow
        window=MainWindow();window.show()
        if '--acceptance' in sys.argv and os.environ.get('LAMINAR_ACCEPTANCE')=='1':
            from native.acceptance import start
            start(app,window,sys.argv[sys.argv.index('--acceptance')+1])
        return app.exec_()
    except Exception:
        detail=traceback.format_exc()
        try:configure_logging().error('Native startup failed\n%s',detail)
        except OSError:pass
        if '--acceptance' in sys.argv and os.environ.get('LAMINAR_ACCEPTANCE')=='1' and sys.argv.index('--acceptance')+1<len(sys.argv):
            folder=Path(sys.argv[sys.argv.index('--acceptance')+1]);folder.mkdir(parents=True,exist_ok=True)
            (folder/'acceptance.json').write_text(json.dumps({'passed':False,'error':detail}),encoding='utf-8')
        elif sys.platform=='win32':
            import ctypes
            message='Не удалось запустить LaminarFloor для Windows 8.1.\nЖурнал: '+str(user_directory()/'Logs/application.log')
            ctypes.windll.user32.MessageBoxW(None,message,'LaminarFloor',0x10)
        return 1

if __name__=='__main__':raise SystemExit(main())

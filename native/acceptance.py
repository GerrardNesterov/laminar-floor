"""Legacy pre-v5 acceptance (JSON project UI); NOT a v5 verification scenario.

Kept as historical test tooling. Do not run against v5: removed project actions
and filtered edge IDs need a future harness update before tests are re-enabled.
"""
import json,os,sys,time,traceback,io
from pathlib import Path
from PySide2.QtCore import QTimer,Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QFileDialog,QToolBar
from laminar.config import Config
from laminar.native_ui import ROOT,ProfileDialog
from laminar.storage import atomic_write


def start(app,window,destination):
    folder=Path(destination);folder.mkdir(parents=True,exist_ok=True)
    state={'pid':os.getpid(),'executable':sys.executable,'compiled':(hasattr(sys.modules['__main__'],'__compiled__') or getattr(sys,'frozen',False)),
           'checks':{},'passed':False,'dialog_test':'fixed-path dialog responses; real Qt actions and engine'}
    atomic_write(folder/'running.json',json.dumps(state).encode())
    timer=QTimer(window);timer.setInterval(100);ticks=[0];timer.timeout.connect(lambda:ticks.__setitem__(0,ticks[0]+1));timer.start()
    original_open=QFileDialog.getOpenFileName;original_save=QFileDialog.getSaveFileName
    original_exec=ProfileDialog.exec_
    def finish(error=None):
        if error:state['error']=error
        atomic_write(folder/'acceptance.json',json.dumps(state,ensure_ascii=False,indent=2).encode('utf-8'))
        QFileDialog.getOpenFileName=original_open;QFileDialog.getSaveFileName=original_save;ProfileDialog.exec_=original_exec
        window.dirty=False;window.close();app.exit(0 if state['passed'] else 1)
    window.error=lambda text:finish(text)
    def click(label):
        toolbar=window.findChild(QToolBar);button=toolbar.widgetForAction(window.actions[label])
        assert button.isEnabled();QTest.mouseClick(button,Qt.LeftButton)
    def begin():
        try:
            assert window.isVisible();state['checks']['native_window']=True
            window.example();window.dirty=False
            QFileDialog.getOpenFileName=lambda *a,**k:(str(ROOT/'examples/support.dxf'),'')
            def profile_choice(dialog):dialog.edge.setCurrentIndex(2);return ProfileDialog.Accepted
            ProfileDialog.exec_=profile_choice
            click('Импорт DXF');assert window.cfg.profile_points and window.cfg.top_edge==2
            state['checks']['dxf']=True
            QFileDialog.getSaveFileName=lambda *a,**k:(str(folder/'Проект проверки.json'),'')
            click('Сохранить');cfg=Config.loads((folder/'Проект проверки.json').read_bytes())
            assert cfg.profile_points and Path(cfg.profile_path).is_file()
            click('Новый проект');assert not window.cfg.profile_points
            QFileDialog.getOpenFileName=lambda *a,**k:(str(folder/'Проект проверки.json'),'')
            click('Открыть');assert window.cfg==cfg;state['checks']['project_new_save_load']=True
            window._acceptance_started=time.monotonic();window._acceptance_ticks=ticks[0]
            click('Генерировать');QTimer.singleShot(200,poll)
        except Exception:finish(traceback.format_exc())
    def poll():
        try:
            # Thread completion can precede delivery of its queued UI signals.
            if time.monotonic()-window._acceptance_started>300:raise TimeoutError('Generation/UI completion exceeded five minutes')
            if window.worker.isRunning() or window.result is None or not window.actions['Генерировать'].isEnabled():
                QTimer.singleShot(200,poll);return
            assert window.result is not None and window.stl_data is not None,window.info.text()
            assert ticks[0]>window._acceptance_ticks+2
            state['checks']['responsive_worker']=True
            assert not window.result.report['errors'];state['checks']['calculations']=True
            QFileDialog.getSaveFileName=lambda *a,**k:(str(folder/'Панель.stl'),'')
            click('Экспорт STL')
            import trimesh
            mesh=trimesh.load(io.BytesIO((folder/'Панель.stl').read_bytes()),file_type='stl')
            assert mesh.is_volume and mesh.is_watertight and len(mesh.split())==1
            state['checks']['stl_valid']=True
            started=time.perf_counter();window.view.grab();state['preview_frame_ms']=(time.perf_counter()-started)*1000
            assert state['preview_frame_ms']<1000,'Preview frame exceeded one second'
            window.grab().save(str(folder/'native-window.png'))
            view=window.view;yaw=view.yaw
            QTest.mousePress(view,Qt.LeftButton,pos=view.rect().center())
            from PySide2.QtCore import QPoint,QPointF,QEvent
            from PySide2.QtGui import QMouseEvent
            # Qt5 QTest.mouseMove omits held buttons; send an actual drag event.
            app.sendEvent(view,QMouseEvent(QEvent.MouseMove,QPointF(view.rect().center()+QPoint(40,20)),Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
            QTest.mouseRelease(view,Qt.LeftButton)
            assert view.yaw!=yaw;state['checks']['rotate_3d']=True
            for i in range(3):
                assert (window.view.layers==i).any()
                window.toggle(i,False);assert not window.view.visible_layers[i]
                window.toggle(i,True);assert window.view.visible_layers[i]
            window.view.grab();drawn=set(window.view.drawn_layers)
            window.view.flip();window.view.grab();drawn.update(window.view.drawn_layers);window.view.flip()
            assert drawn=={0,1,2},drawn
            assert len(window.view.faces)>100
            state['checks']['vector_mesh_layers']=True
            window.fields['load'].setValue(window.cfg.load+1);assert window.stl_data is None and not window.actions['Экспорт STL'].isEnabled()
            state['checks']['stale_export_blocked']=True
            state['passed']=True;finish()
        except Exception:finish(traceback.format_exc())
    QTimer.singleShot(250,begin)

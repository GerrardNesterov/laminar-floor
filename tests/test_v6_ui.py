import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import pytest
from PySide2.QtWidgets import QApplication,QGraphicsLineItem,QFileDialog
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from laminar.native_ui import ProfileDialog,MainWindow,Generation
from laminar.profile import prepare,valid_orientations
from laminar.config import Config
from test_v6_core import PROFILE

@pytest.fixture(scope='module')
def app():return QApplication.instance() or QApplication([])

@pytest.fixture
def ui_errors(monkeypatch):
    errors=[]
    monkeypatch.setattr(sys,'excepthook',lambda *args:errors.append(args))
    yield errors
    assert not errors


def test_every_visible_edge_is_selectable_and_axis_updates(app,ui_errors):
    dialog=ProfileDialog(PROFILE,2);dialog.show();app.processEvents()
    axes=[]
    for side in ('left','right'):
        dialog.axis.setCurrentIndex(dialog.axis.findData(side));app.processEvents()
        expected=valid_orientations(PROFILE,45,axis_side=side)
        assert {dialog.edge.itemData(i) for i in range(dialog.edge.count())}==set(expected)
        for index in range(dialog.edge.count()):
            dialog.edge.setCurrentIndex(index);app.processEvents()
            edge=dialog.edge.currentData()
            lines=[x for x in dialog.view.scene().items() if isinstance(x,QGraphicsLineItem) and x.pen().style()==Qt.DashLine]
            assert len(lines)==1
            a,b=expected[edge].axis_original
            axis=lines[0].line()
            assert axis.x1()==pytest.approx(a[0]-.15*(b[0]-a[0]))
            assert axis.y1()==pytest.approx(-a[1]+.15*(b[1]-a[1]))
        dialog.edge.setCurrentIndex(dialog.edge.findData(2));app.processEvents()
        axes.append(dialog.profiles[2].axis_original)
    assert axes[0]!=axes[1]
    dialog.close()


def test_filtered_noncontiguous_edge_ids_survive_keyboard_selection(app,ui_errors):
    points=[[0,0],[20,0],[20,20],[12,20],[12,10],[8,10],[8,20],[0,20]]
    dialog=ProfileDialog(points,0);dialog.show();app.processEvents()
    ids=[dialog.edge.itemData(i) for i in range(dialog.edge.count())]
    assert 4 not in ids
    dialog.edge.setCurrentIndex(0)
    for i in range(1,len(ids)):
        QTest.keyClick(dialog.edge,Qt.Key_Down);app.processEvents()
        assert dialog.edge.currentData()==ids[i]
        assert dialog.profiles[ids[i]].solid().volume()>0
    dialog.close()


def test_import_and_edit_pass_axis_and_edge_ids(app,monkeypatch,tmp_path,ui_errors):
    import ezdxf
    path=tmp_path/'profile.dxf';doc=ezdxf.new();doc.header['$INSUNITS']=4;doc.modelspace().add_lwpolyline(PROFILE,close=True);doc.saveas(path)
    monkeypatch.setenv('LAMINAR_USER_DIR',str(tmp_path/'state'))
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(path),''))
    def choose(dialog):
        dialog.axis.setCurrentIndex(dialog.axis.findData('right'))
        dialog.edge.setCurrentIndex(dialog.edge.findData(2))
        return ProfileDialog.Accepted
    monkeypatch.setattr(ProfileDialog,'exec_',choose)
    window=MainWindow();window.error=lambda m:pytest.fail(m)
    window.import_dxf()
    assert window.cfg.top_edge==2 and window.cfg.axis_side=='right'
    window.edit_profile()
    cfg=window.current_config()
    assert cfg.axis_side=='right' and cfg.top_edge==2
    assert prepare(cfg.profile_points,cfg.top_edge,axis_side=cfg.axis_side).axis_original==prepare(PROFILE,2,axis_side='right').axis_original
    assert not {'Сохранить','Открыть','Новый проект'} & window.actions.keys()
    assert window.load_type.findData('wheel')==-1
    window.close()


@pytest.mark.parametrize('pressure',[1,None])
def test_quality_warning_keeps_export_and_write_errors_are_reported(app,monkeypatch,tmp_path,pressure):
    from types import SimpleNamespace
    monkeypatch.setenv('LAMINAR_USER_DIR',str(tmp_path/'state'))
    w=MainWindow();monkeypatch.setattr(w.view,'set_result',lambda result:None)
    report={'geometry':{'volume_cm3':1,'open_fraction':.4},'mechanics':{'deflection_mm':2},
            'printing':{'mass_g':1},'aerodynamics':{'pressure_pa':pressure},'errors':['Прочность недостаточна'],
            'supports':{'merged_flange_pairs':[]}}
    w.generated(SimpleNamespace(report=report),b'geometry');w.set_busy(False)
    assert 'Экспорт не рекомендован' in w.info.text()
    assert 'Прочность недостаточна' in w.info.text()
    assert w.actions['Экспорт STL'].isEnabled()
    target=tmp_path/'panel.stl'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(target),''))
    w.export_stl();assert target.read_bytes()==b'geometry'
    import laminar.native_ui as ui
    def no_write(*args):raise PermissionError('Нет доступа')
    monkeypatch.setattr(ui,'atomic_write',no_write)
    errors=[];w.error=errors.append
    w.export_stl();assert errors==['Нет доступа']
    w.close()


def test_worker_serializes_despite_quality_errors(app,monkeypatch):
    import laminar.engine as engine
    from types import SimpleNamespace
    monkeypatch.setattr(engine,'generate',lambda cfg:SimpleNamespace(report={'errors':['warning']},stl=lambda:b'stl'))
    worker=Generation(Config());received=[]
    worker.completed.connect(lambda result,data:received.append(data))
    worker.run();assert received==[b'stl']


def test_right_edge_is_preselected_for_mirrored_half_profile(app):
    points=[[12-x,y] for x,y in PROFILE]
    dialog=ProfileDialog(points,2)
    assert dialog.axis.currentData()=='right'
    assert all(abs(p[0]-12)<1e-6 for p in dialog.profiles[2].axis_original)
    dialog.close()


def test_full_symmetric_profile_discloses_edge_axis_diameter(app):
    dialog=ProfileDialog([[-12,0],[12,0],[8,15],[-8,15]],2)
    assert dialog.axis.count()==2
    assert 'центральная ось' in dialog.axis_info.text()
    assert dialog.profiles[2].radius==24
    dialog.close()

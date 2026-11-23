"""Native Qt Widgets front end. No browser, networking, or child application processes."""
import logging
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from math import dist
from PySide2.QtCore import Qt,QThread,Signal,QSettings
from PySide2.QtGui import QIcon,QPainter,QPen,QColor
from PySide2.QtWidgets import QAction
from PySide2.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,
    QComboBox,QScrollArea,QSplitter,QLabel,QCheckBox,QPushButton,QProgressBar,
    QFileDialog,QMessageBox,QDialog,QDialogButtonBox,QGraphicsView,QGraphicsScene,QPlainTextEdit,QLineEdit)
from .config import Config
from .storage import user_directory,atomic_write,import_profile
from .native_view import PanelView

ROOT=Path(__file__).resolve().parents[1]


class NumericField(QLineEdit):
    """Keep numeric precision; validate through the existing core before use."""
    def value(self):
        try:return float(self.text().strip().replace(',','.'))
        except ValueError:raise ValueError(self.accessibleName()+': введите число') from None
    def setValue(self,value):self.setText(str(value))


class Generation(QThread):
    completed=Signal(object,object);failed=Signal(str);stage=Signal(str)
    def __init__(self,cfg,parent=None):super().__init__(parent);self.cfg=cfg
    def run(self):
        try:
            from .engine import generate
            self.stage.emit('Генерация геометрии, механический и аэродинамический расчёт…')
            result=generate(self.cfg)
            self.stage.emit('Проверка печатаемости и экспортируемого STL…')
            data=result.stl()
            self.completed.emit(result,data)
        except Exception as exc:
            logging.getLogger('laminar').exception('Native generation failed');self.failed.emit(str(exc))


class ProfileDialog(QDialog):
    def __init__(self,points,edge,parent=None,nominal=45,axis_side=None):
        from .profile import valid_orientations,prepare
        # No menu is populated or shown until all candidates have been checked.
        options={}
        for side in ('left','right'):
            try:options[side]=valid_orientations(points,nominal,axis_side=side)
            except ValueError:options[side]={}
        if not any(options.values()):
            raise ValueError('У профиля нет геометрически допустимых ориентаций')
        legacy={}
        for index in set().union(*(set(v) for v in options.values())):
            try:legacy[index]=prepare(points,index)
            except ValueError:pass
        preferred=edge if any(edge in v for v in options.values()) else next(i for v in options.values() for i in v)
        if axis_side is None:
            axis_side='left'
            previous=legacy.get(preferred)
            if previous:
                for side,profiles in options.items():
                    if preferred in profiles and all(dist(a,b)<1e-6 for a,b in zip(previous.axis_original,profiles[preferred].axis_original)):
                        axis_side=side;break
        super().__init__(parent);self.setWindowTitle('Профиль башмака — верхняя сторона');self.resize(560,460)
        self.points=points;self.options=options;self.profiles={};self.legacy_profiles=legacy
        layout=QVBoxLayout(self);scene=QGraphicsScene(self);self.view=QGraphicsView(scene)
        self.view.setRenderHint(QPainter.Antialiasing);layout.addWidget(self.view)
        self.axis=QComboBox();self.axis.setObjectName('axis_side')
        for side,label in [('left','Ось у левого края'),('right','Ось у правого края')]:
            self.axis.addItem(label,side)
            self.axis.model().item(self.axis.count()-1).setEnabled(bool(options[side]))
        self.axis.setCurrentIndex(self.axis.findData(axis_side if options.get(axis_side) else next(k for k,v in options.items() if v)))
        layout.addWidget(self.axis)
        self.axis_info=QLabel();self.axis_info.setWordWrap(True);self.axis_info.setTextFormat(Qt.PlainText);layout.addWidget(self.axis_info)
        self.edge=QComboBox();self.edge.setObjectName('top_edge')
        layout.addWidget(QLabel('Зелёный — допустимые стороны · синий — выбранная · пунктир — ось'))
        layout.addWidget(self.edge)
        self.edge.currentIndexChanged.connect(self.draw_profile)
        self.axis.currentIndexChanged.connect(self.change_axis)
        self.change_axis(preferred=edge)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def change_axis(self,*args,preferred=None):
        edge=self.edge.currentData() if preferred is None else preferred
        self.profiles=self.options[self.axis.currentData()]
        self.edge.blockSignals(True);self.edge.clear()
        for index in self.profiles:self.edge.addItem('Сторона '+str(index),index)
        self.edge.setCurrentIndex(max(0,self.edge.findData(edge)));self.edge.blockSignals(False)
        self.draw_profile()
    def draw_profile(self,*args):
        scene=self.view.scene();scene.clear();selected=self.edge.currentData()
        if selected not in self.profiles:return
        xs=[p[0] for p in self.points];ys=[p[1] for p in self.points]
        span=max(max(xs)-min(xs),max(ys)-min(ys),1.)
        for i,p in enumerate(self.points):
            q=self.points[(i+1)%len(self.points)]
            pen=QPen(QColor('#185caa' if i==selected else '#226446' if i in self.profiles else '#65717a'))
            pen.setWidth(3 if i==selected else 2);pen.setCosmetic(True)
            scene.addLine(p[0],-p[1],q[0],-q[1],pen)
            if i in self.profiles:
                label=scene.addSimpleText(str(i));label.setBrush(QColor('#263642'))
                label.setScale(span/220);label.setPos((p[0]+q[0])/2,-(p[1]+q[1])/2)
        profile=self.profiles[selected]
        previous=self.legacy_profiles.get(selected)
        note=f'Диаметр тела вращения: {2*profile.radius:.2f} мм.'
        if previous and all(not all(dist(a,b)<1e-6 for a,b in zip(previous.axis_original,variants[selected].axis_original))
                            for variants in self.options.values() if selected in variants):
            note+=' Полный симметричный профиль: ось у края даёт больший диаметр, чем прежняя центральная ось.'
        self.axis_info.setText(note)
        a,b=profile.axis_original
        delta=[(b[i]-a[i])*.15 for i in range(2)]
        pen=QPen(QColor('#9b3d17'));pen.setWidth(2);pen.setCosmetic(True);pen.setStyle(Qt.DashLine)
        scene.addLine(a[0]-delta[0],-a[1]+delta[1],b[0]+delta[0],-b[1]-delta[1],pen)
        label=scene.addSimpleText('Ось вращения');label.setBrush(QColor('#9b3d17'))
        label.setScale(span/220);label.setPos(b[0]+delta[0]+span*.03,-b[1]-delta[1])
        self.view.fitInView(scene.itemsBoundingRect().adjusted(-span*.08,-span*.08,span*.08,span*.08),Qt.KeepAspectRatio)
    def showEvent(self,event):
        super().showEvent(event);self.draw_profile()
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'edge'):self.draw_profile()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('LaminarFloor');self.resize(1280,840)
        self.setWindowIcon(QIcon(str(ROOT/'assets/laminar.ico')))
        self.cfg=Config();self.result=None;self.stl_data=None;self.worker=None
        self.settings=QSettings(str(user_directory()/'native-settings.ini'),QSettings.IniFormat);self.fields={};self.actions={}
        toolbar=self.addToolBar('Панель');toolbar.setMovable(False)
        for label,slot,key in [('Сбросить параметры',self.new,'Ctrl+N'),('Импорт DXF',self.import_dxf,'Ctrl+I'),
            ('Генерировать',self.generate,'F5'),('Экспорт STL',self.export_stl,'Ctrl+E')]:
            action=QAction(label,self);action.setShortcut(key);action.triggered.connect(slot);toolbar.addAction(action);self.actions[label]=action
        self.actions['Экспорт STL'].setEnabled(False)
        split=QSplitter();self.setCentralWidget(split)
        self.form_widget=QWidget();form=QFormLayout(self.form_widget);form.setSpacing(8)
        definitions=[('width','Ширина, мм',.01,2000),('depth','Глубина, мм',.01,2000),
            ('max_thickness','Номинальная толщина, мм',.01,2000),('outer_flange','Внешний бурт, мм (0–10)',0,10),('mmf','MMF',.4,1.2),('fos','FoS',1,100),
            ('load','Нагрузка, Н',.01,1e7),('air_speed','Воздух, м/с',0,100),('nozzle','Сопло, мм',.01,20),
            ('extrusion','Ширина экструзии, мм',.01,30),('layer','Высота слоя, мм',.01,10),
            ('max_opening','Макс. отверстие, мм',.01,2000),('min_contact','Мин. доля контакта',.1,.9),
            ('max_deflection','Макс. прогиб, мм',.01,100)]
        for name,label,low,high in definitions:
            field=NumericField();field.setMinimumWidth(96)
            field.setObjectName(name);field.setAccessibleName(label);field.textChanged.connect(self.changed)
            self.fields[name]=field;form.addRow(label,field)
        self.material=QComboBox();self.material.addItems(['PETG','PP','PA6']);form.insertRow(4,'Материал',self.material)
        self.load_type=QComboBox()
        for key,label in [('person','Человек'),('point','Точечная'),('distributed','Распределённая'),('equipment','Оборудование')]:self.load_type.addItem(label,key)
        form.insertRow(8,'Тип нагрузки',self.load_type)
        self.material.currentIndexChanged.connect(self.changed);self.load_type.currentIndexChanged.connect(self.changed)
        self.profile=QPushButton('Башмак: не выбран');self.profile.clicked.connect(self.edit_profile);form.addRow(self.profile)
        example=QPushButton('Учебный пример');example.clicked.connect(self.example);form.addRow(example)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumWidth(350);scroll.setMaximumWidth(420)
        scroll.setWidget(self.form_widget);split.addWidget(scroll)
        center=QWidget();layout=QVBoxLayout(center);toggles=QHBoxLayout()
        self.view=PanelView()
        for index,label in enumerate(['Рабочая поверхность','Несущая структура','Посадки']):
            cb=QCheckBox(label);cb.setChecked(True);cb.toggled.connect(lambda checked,i=index:self.toggle(i,checked));toggles.addWidget(cb)
        problems=QCheckBox('Проблемы');problems.setChecked(True)
        problems.toggled.connect(lambda checked:self.show_problems(checked));toggles.addWidget(problems)
        flip=QPushButton('Перевернуть');flip.clicked.connect(self.view.flip);toggles.addWidget(flip)
        layout.addLayout(toggles);layout.addWidget(self.view,1)
        self.info=QLabel('Задайте параметры, импортируйте профиль DXF и нажмите «Генерировать».')
        self.info.setWordWrap(True);self.info.setTextFormat(Qt.PlainText);layout.addWidget(self.info)
        report=QPushButton('Полный расчётный отчёт');report.clicked.connect(self.show_report);layout.addWidget(report)
        self.progress=QProgressBar();self.progress.hide();layout.addWidget(self.progress)
        split.addWidget(center);split.setSizes([300,980])
        self.statusBar().showMessage('Готово · Все вычисления выполняются на этом компьютере')
        self.apply_config(self.cfg)
        geometry=self.settings.value('geometry')
        if geometry:self.restoreGeometry(geometry)

    def toggle(self,index,checked):self.view.visible_layers[index]=checked;self.view.update()
    def show_problems(self,checked):self.view.show_problems=checked;self.view.update()
    def changed(self,*args):
        self.result=None;self.stl_data=None;self.view.set_result(None)
        self.actions['Экспорт STL'].setEnabled(False);self.info.setText('Параметры изменены. Нажмите «Генерировать».')
    def current_config(self):
        cfg=replace(self.cfg,**{k:v.value() for k,v in self.fields.items()},material=self.material.currentText(),load_type=self.load_type.currentData())
        cfg.validate();return cfg
    def apply_config(self,cfg):
        self.cfg=cfg
        for name,field in self.fields.items():field.blockSignals(True);field.setValue(getattr(cfg,name));field.blockSignals(False)
        for field,value in [(self.material,self.material.findText(cfg.material)),(self.load_type,self.load_type.findData(cfg.load_type))]:
            field.blockSignals(True);field.setCurrentIndex(value);field.blockSignals(False)
        self.profile.setText('Башмак: '+(cfg.profile_name or 'не выбран'))
        self.changed()
    def new(self):
        self.apply_config(Config())
    def example(self):
        self.apply_config(Config(width=80,depth=80,load=100,max_opening=18,
            profile_points=[[0.,0.],[12.,0.],[8.,15.],[0.,15.]],profile_name='support.dxf',top_edge=2))
    def import_dxf(self):
        path,_=QFileDialog.getOpenFileName(self,'Импорт DXF','','DXF (*.dxf)')
        if not path:return
        try:
            from .profile import read_dxf
            raw=Path(path).read_bytes();points=read_dxf(raw)
            dialog=ProfileDialog(points,0,self,nominal=self.fields['max_thickness'].value())
            if dialog.exec_()!=QDialog.Accepted:return
            edge=dialog.edge.currentData()
            self.cfg=replace(self.cfg,profile_points=points,top_edge=edge,axis_side=dialog.axis.currentData(),profile_name=Path(path).name,profile_path=import_profile(path,raw))
            self.profile.setText('Башмак: '+self.cfg.profile_name);self.changed()
        except Exception as exc:self.error(str(exc))
    def edit_profile(self):
        if not self.cfg.profile_points:self.import_dxf();return
        try:
            dialog=ProfileDialog(self.cfg.profile_points,self.cfg.top_edge,self,nominal=self.fields['max_thickness'].value(),axis_side=self.cfg.axis_side)
            if dialog.exec_()==QDialog.Accepted:
                self.cfg=replace(self.cfg,top_edge=dialog.edge.currentData(),axis_side=dialog.axis.currentData());self.changed()
        except ValueError as exc:self.error(str(exc))
    def generate(self):
        if self.worker and self.worker.isRunning():return
        try:cfg=self.current_config()
        except Exception as exc:self.error(str(exc));return
        self.cfg=cfg;self.result=None;self.stl_data=None;self.view.set_result(None);self.set_busy(True)
        self.worker=Generation(deepcopy(cfg),self)
        self.worker.stage.connect(self.statusBar().showMessage);self.worker.completed.connect(self.generated)
        self.worker.failed.connect(self.error);self.worker.finished.connect(lambda:self.set_busy(False));self.worker.start()
    def set_busy(self,busy):
        self.form_widget.setEnabled(not busy)
        for action in self.actions.values():action.setEnabled(not busy)
        self.actions['Экспорт STL'].setEnabled(not busy and self.stl_data is not None)
        self.progress.setRange(0,0 if busy else 1);self.progress.setVisible(busy)
    def generated(self,result,data):
        self.result=result;self.stl_data=data;self.view.set_result(result)
        r=result.report;g=r['geometry'];m=r['mechanics'];air=r['aerodynamics']
        pressure='не определён' if air['pressure_pa'] is None else f"{air['pressure_pa']:.2f} Па"
        self.info.setText(f"Объём {g['volume_cm3']:.2f} см³ · Масса {r['printing']['mass_g']:.1f} г · Прогиб {m['deflection_mm']:.3f} мм\n"
            f"Открытость {g['open_fraction']:.1%} · Перепад давления {pressure}\n"
            + ('Экспорт не рекомендован: '+'; '.join(r['errors']) if r['errors'] else 'Проверки модели пройдены; STL доступен.')
            +(f"\nОбъединены соседние бурты: {len(r['supports']['merged_flange_pairs'])} пар." if r['supports']['merged_flange_pairs'] else '')
            +'\nПредварительная модель. Перед применением несущего пола необходимы испытания.')
        self.statusBar().showMessage('Расчёт завершён')
    def export_stl(self):
        if self.stl_data is None:return
        name,_=QFileDialog.getSaveFileName(self,'Экспорт STL',str(user_directory()/'panel.stl'),'STL (*.stl)')
        if name:
            try:atomic_write(name,self.stl_data);self.statusBar().showMessage('STL сохранён: '+name)
            except OSError as exc:self.error(str(exc))
    def show_report(self):
        if self.result is None:return
        dialog=QDialog(self);dialog.setWindowTitle('Расчётный отчёт');dialog.resize(720,560)
        layout=QVBoxLayout(dialog);text=QPlainTextEdit(self.result.report_json());text.setReadOnly(True);layout.addWidget(text);dialog.exec_()
    def error(self,message):
        logging.getLogger('laminar').error(message);self.statusBar().showMessage('Ошибка: '+message)
        QMessageBox.warning(self,'LaminarFloor',message)
    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.statusBar().showMessage('Дождитесь завершения расчёта перед закрытием.');event.ignore();return
        self.settings.setValue('geometry',self.saveGeometry());event.accept()

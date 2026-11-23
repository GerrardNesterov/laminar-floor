"""Vector rendering of the real triangulated panel using Qt's raster-independent painter."""
import math
import numpy as np
from PySide2.QtCore import Qt, QPointF
from PySide2.QtGui import QColor, QPainter, QPolygonF, QPen
from PySide2.QtWidgets import QWidget


class PanelView(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMinimumSize(400,360);self.setFocusPolicy(Qt.StrongFocus)
        self.result=None;self.yaw=-35.;self.pitch=125.;self.zoom=1.;self.pan=np.zeros(2)
        self.visible_layers=[True,True,True];self.show_problems=True;self.last=None
        self.setAccessibleName('Трёхмерная панель')
        self.palette=[]
        for base in ['#4c8192','#55766a','#ad6c21','#b3261e']:
            for tone in range(32):
                color=QColor(base);shade=.55+.45*tone/31
                color.setRgbF(color.redF()*shade,color.greenF()*shade,color.blueF()*shade)
                self.palette.append(color)

    def set_result(self,result):
        self.result=result
        if result is not None:
            self.reset_view()
            mesh=result.mesh
            self.vertices=np.asarray(mesh.vertices)-mesh.bounds.mean(axis=0)
            self.faces=np.asarray(mesh.faces);self.normals=np.asarray(mesh.face_normals)
            # Categories refer to actual mesh faces, not synthetic replacement geometry.
            z=mesh.triangles[:,:,2]
            self.layers=np.ones(len(self.faces),dtype=int)
            self.layers[np.max(np.abs(z),axis=1)<.02]=0
            centers=mesh.triangles_center
            supports=np.asarray(result.report['supports']['positions_mm'])
            radius=result.report['supports'].get('preview_radius_mm',0)
            near=np.min(np.linalg.norm(centers[:,None,:2]-supports[None,:,:],axis=2),axis=1)<=radius
            self.layers[near & (z.mean(axis=1)>result.report['geometry']['available_height_mm']-.02)]=2
        else:
            self.vertices=np.empty((0,3));self.faces=np.empty((0,3),dtype=int)
            self.normals=np.empty((0,3));self.layers=np.empty(0,dtype=int);self.drawn_layers=set()
        self.update()

    def reset_view(self):
        self.yaw=-35.;self.pitch=125.;self.zoom=1.;self.pan[:]=0;self.update()

    def flip(self):
        self.pitch=(-self.pitch)%360-180;self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(),QColor('#f8f9fa'))
        if self.result is None:
            painter.setPen(QColor('#43515c'))
            painter.drawText(self.rect(),Qt.AlignCenter,'Импортируйте DXF и нажмите «Генерировать»')
            return
        a,b=math.radians(self.yaw),math.radians(self.pitch)
        rz=np.array([[math.cos(a),-math.sin(a),0],[math.sin(a),math.cos(a),0],[0,0,1]])
        rx=np.array([[1,0,0],[0,math.cos(b),-math.sin(b)],[0,math.sin(b),math.cos(b)]])
        rotation=rx@rz;v=self.vertices@rotation.T;n=self.normals@rotation.T
        span=np.maximum(np.ptp(v[:,:2],axis=0),1)
        scale=.78*min(self.width()/span[0],(self.height()-64)/span[1])*self.zoom
        screen=v[:,:2]*np.array([scale,-scale])+np.array([self.width()/2,self.height()/2])+self.pan
        triangles=screen[self.faces];depth=v[self.faces,2].mean(axis=1)
        show=np.asarray(self.visible_layers)[self.layers] & (n[:,2]>0)
        tones=np.clip(n@np.array([.2,-.4,.89]),0,1)
        category=self.layers.copy()
        if self.show_problems:category[self.result.invalid_faces]=3
        color_indices=category*32+np.rint(tones*31).astype(int)
        order=np.flatnonzero(show);order=order[np.argsort(depth[order])]
        self.drawn_layers=set(self.layers[order].tolist())
        painter.setRenderHint(QPainter.Antialiasing,False)
        painter.setPen(Qt.NoPen)
        for triangle,color_index in zip(triangles[order],color_indices[order]):
            painter.setBrush(self.palette[color_index])
            painter.drawPolygon(QPolygonF([QPointF(*triangle[0]),QPointF(*triangle[1]),QPointF(*triangle[2])]))
        painter.setPen(QPen(QColor('#33434e'),1))
        size=self.result.mesh.extents
        painter.drawText(16,24,'Габариты: '+ ' × '.join(f'{v:.2f}' for v in size)+' мм')
        painter.drawText(16,self.height()-16,'ЛКМ — вращение · ПКМ / Shift — сдвиг · колесо — масштаб · Home — сброс')

    def mousePressEvent(self,event):self.last=event.localPos()
    def mouseMoveEvent(self,event):
        if self.last is None:return
        delta=event.localPos()-self.last;self.last=event.localPos()
        if event.buttons()&Qt.RightButton or event.modifiers()&Qt.ShiftModifier:
            self.pan+=np.array([delta.x(),delta.y()])
        elif event.buttons()&Qt.LeftButton:
            self.yaw+=delta.x()*.5;self.pitch=(self.pitch+delta.y()*.5+180)%360-180
        self.update()
    def mouseReleaseEvent(self,event):self.last=None
    def wheelEvent(self,event):
        self.zoom=max(.1,min(20,self.zoom*math.exp(event.angleDelta().y()/1200)));self.update()
    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Home:self.reset_view()
        else:super().keyPressEvent(event)

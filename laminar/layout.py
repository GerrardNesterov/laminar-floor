import math
from dataclasses import dataclass
from shapely.geometry import Polygon, box, Point
from shapely.affinity import translate
from shapely.ops import unary_union
from .geometry import polygons


@dataclass
class Tile:
    name: str
    kind: str
    domain: Polygon
    origin: tuple
    supports: list


def layout(cfg):
    room = Polygon(cfg.room or [(0,0),(cfg.width,0),(cfg.width,cfg.depth),(0,cfg.depth)])
    if not room.is_valid or room.area <= 0:
        raise ValueError('Контур помещения некорректен')
    obstacles = [Polygon(p) for p in cfg.obstacles]
    if any(not p.is_valid or p.area <= 0 for p in obstacles):
        raise ValueError('Некорректный контур препятствия')
    usable = room.difference(unary_union(obstacles))
    x0,y0,x1,y1 = room.bounds
    ox,oy = cfg.layout_offset
    if not all(math.isfinite(v) for v in (ox,oy)):
        raise ValueError('Некорректное смещение раскладки')
    start_x = x0 + ox % cfg.width - cfg.width
    start_y = y0 + oy % cfg.depth - cfg.depth
    count = math.ceil((x1-start_x)/cfg.width)*math.ceil((y1-start_y)/cfg.depth)
    if count > 1000:
        raise ValueError('Раскладка ограничена 1000 панелей')
    result=[]
    for i in range(math.ceil((x1-start_x)/cfg.width)):
        for j in range(math.ceil((y1-start_y)/cfg.depth)):
            x,y=start_x+i*cfg.width,start_y+j*cfg.depth
            rect=box(x,y,x+cfg.width,y+cfg.depth)
            cut=usable.intersection(rect)
            parts=polygons(cut)
            if len(parts)>1:
                raise ValueError('Препятствие разделяет панель: измените смещение раскладки или размеры')
            if not parts or parts[0].area<1e-6: continue
            p=parts[0]
            if p.area < 25:
                raise ValueError('Возникла слишком узкая краевая панель; скорректируйте раскладку')
            wall=p.boundary.intersection(usable.boundary)
            kind='внутренняя' if wall.length<1e-5 else 'пристенная'
            if wall.geom_type=='MultiLineString' and len(wall.geoms)>1: kind='угловая'
            local=translate(p,xoff=-x,yoff=-y)
            if cfg.supports:
                supports=[[sx-x,sy-y] for sx,sy in cfg.supports if p.buffer(1e-5).covers(Point(sx,sy))]
            else:
                supports=list(local.exterior.coords)[:-1]
            result.append(Tile(f'panel_{len(result)+1:03}',kind,local,(x,y),supports))
    return result

from dataclasses import asdict, dataclass, field
import json
import math


@dataclass
class Config:
    width: float = 160
    depth: float = 160
    max_thickness: float = 45  # Nominal distance from working plane to external foot top.
    outer_flange: float = 0
    material: str = "PETG"
    mmf: float = 1.0
    fos: float = 2.0
    nozzle: float = 0.6
    layer: float = 0.2
    extrusion: float = 0.66
    perimeters: int = 3
    air_speed: float = 0.35
    load: float = 1000
    load_type: str = "person"
    max_opening: float = 14
    min_contact: float = 0.3
    max_deflection: float = 1.0
    wheel_diameter: float = 60
    wheel_width: float = 20
    friction_coefficient: float | None = None
    seed: int = 42
    profile_points: list = field(default_factory=list)
    profile_name: str = ""
    profile_path: str = ""
    top_edge: int = 0
    axis_side: str = "left"
    room: list = field(default_factory=list)
    obstacles: list = field(default_factory=list)
    supports: list = field(default_factory=list)
    layout_offset: list = field(default_factory=lambda: [0., 0.])

    def validate(self):
        if not isinstance(self.profile_path,str) or len(self.profile_path)>32768:
            raise ValueError("Некорректный путь DXF")
        def numeric(v):
            return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
        for k in ('width', 'depth', 'max_thickness', 'nozzle', 'layer', 'extrusion',
                  'load', 'max_opening', 'max_deflection', 'wheel_diameter', 'wheel_width'):
            v = getattr(self, k)
            if not numeric(v) or v < 0.01:
                raise ValueError(f'{k}: требуется положительное конечное число')
        if isinstance(self.seed,bool) or not isinstance(self.seed, int) or not 0 <= self.seed <= 2**32-1:
            raise ValueError('Некорректное начальное число генератора')
        if self.axis_side not in ('left','right'):
            raise ValueError('Ось: только левый или правый край профиля')
        if isinstance(self.top_edge,bool) or not isinstance(self.top_edge, int) or self.top_edge < 0:
            raise ValueError('Некорректная верхняя сторона')
        def points(value, label, minimum=0):
            if not isinstance(value, list) or (value and len(value) < minimum):
                raise ValueError(f'{label}: некорректный список точек')
            for p in value:
                if not isinstance(p, (list, tuple)) or len(p) != 2 or not all(
                    isinstance(v, (int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in p):
                    raise ValueError(f'{label}: нужны конечные координаты [[x,y], …]')
        points(self.room, 'Помещение', 3)
        points(self.profile_points, 'Профиль', 3)
        points(self.supports, 'Опоры', 3)
        if not isinstance(self.obstacles, list):
            raise ValueError('Препятствия: нужен список контуров')
        for obstacle in self.obstacles:
            points(obstacle, 'Препятствие', 3)
        if not isinstance(self.layout_offset, list) or len(self.layout_offset)!=2 or not all(
            numeric(v) for v in self.layout_offset):
            raise ValueError('Смещение раскладки: нужны два конечных числа')
        if self.friction_coefficient is not None and (not numeric(self.friction_coefficient) or self.friction_coefficient < 0):
            raise ValueError('Измеренный коэффициент трения должен быть неотрицательным числом')
        if self.material not in ('PETG', 'PP', 'PA6'):
            raise ValueError('Материал: PETG, PP или PA6')
        if not numeric(self.mmf) or not 0.4 <= self.mmf <= 1.2:
            raise ValueError('MMF должен быть в диапазоне 0.4–1.2')
        if not numeric(self.fos) or self.fos < 1:
            raise ValueError('FoS должен быть не меньше 1')
        if not numeric(self.air_speed) or self.air_speed < 0:
            raise ValueError('Скорость воздуха не может быть отрицательной')
        if not numeric(self.min_contact) or not 0.1 <= self.min_contact <= 0.9:
            raise ValueError('Доля контакта должна быть между 0 и 1')
        if self.layer > self.nozzle * 0.8 or self.extrusion < self.nozzle:
            raise ValueError('Слой ≤ 0.8 диаметра сопла; ширина экструзии ≥ диаметра сопла')
        if isinstance(self.perimeters,bool) or not isinstance(self.perimeters, int) or not 2 <= self.perimeters <= 12:
            raise ValueError('Количество периметров: целое число 2–12')
        if not numeric(self.outer_flange) or not 0 <= self.outer_flange <= 10:
            raise ValueError('Ширина внешнего бурта должна быть от 0 до 10 мм')
        if self.load_type not in ('distributed', 'point', 'person', 'equipment'):
            raise ValueError('Неизвестный вид нагрузки')
        if self.width > 2000 or self.depth > 2000:
            raise ValueError('Размер больше 2000 мм: превышен предел текущего расчётного ядра')

    def dumps(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, allow_nan=False)

    @classmethod
    def loads(cls, raw):
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('Конфигурация должна быть JSON-объектом')
        result = cls(**data)
        result.validate()
        return result

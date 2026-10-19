"""
Íconos vectoriales en PDF a partir de un ``path`` SVG (formato ``@mdi/js``).

reportlab no interpreta SVG; este módulo convierte el atributo ``d`` de un path
(comandos M L H V C S Q T A Z, absolutos y relativos) a un path de reportlab y
lo dibuja relleno. Así los PDF usan los mismos íconos que la web, nítidos a
cualquier escala y sin añadir dependencias.
"""

import math
import re

from reportlab.platypus import Flowable

_NUMERO = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_SEPARADORES = ' \t\r\n,'


class _Lector:
    """Recorre ``d`` carácter a carácter: comandos, números y banderas de arco."""

    def __init__(self, d: str):
        self.d = d or ''
        self.pos = 0

    def _saltar(self):
        while self.pos < len(self.d) and self.d[self.pos] in _SEPARADORES:
            self.pos += 1

    def fin(self) -> bool:
        self._saltar()
        return self.pos >= len(self.d)

    def hay_comando(self) -> bool:
        self._saltar()
        return self.pos < len(self.d) and self.d[self.pos].isalpha()

    def comando(self) -> str:
        self._saltar()
        letra = self.d[self.pos]
        self.pos += 1
        return letra

    def numero(self) -> float:
        self._saltar()
        coincidencia = _NUMERO.match(self.d, self.pos)
        if not coincidencia:
            raise ValueError(f'Path SVG inválido cerca de la posición {self.pos}')
        self.pos = coincidencia.end()
        return float(coincidencia.group())

    def bandera(self) -> bool:
        """Las banderas de un arco son un único dígito y pueden ir pegadas ("01")."""
        self._saltar()
        letra = self.d[self.pos]
        if letra not in '01':
            raise ValueError(f'Bandera de arco inválida cerca de la posición {self.pos}')
        self.pos += 1
        return letra == '1'


def _arco_a_beziers(x1, y1, rx, ry, angulo, grande, barrido, x2, y2):
    """Arco elíptico SVG → lista de curvas cúbicas ``(c1x, c1y, c2x, c2y, x, y)``
    (algoritmo de la implementación de referencia del W3C, F.6.5)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [(x1, y1, x2, y2, x2, y2)]

    fi = math.radians(angulo % 360)
    cos_fi, sin_fi = math.cos(fi), math.sin(fi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_fi * dx + sin_fi * dy
    y1p = -sin_fi * dx + cos_fi * dy

    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)

    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if grande == barrido:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_fi * cxp - sin_fi * cyp + (x1 + x2) / 2
    cy = sin_fi * cxp + cos_fi * cyp + (y1 + y2) / 2

    def _angulo(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = _angulo(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = _angulo((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not barrido and dt > 0:
        dt -= 2 * math.pi
    elif barrido and dt < 0:
        dt += 2 * math.pi

    segmentos = max(1, math.ceil(abs(dt) / (math.pi / 2)))
    delta = dt / segmentos
    k = 4 / 3 * math.tan(delta / 4)
    curvas = []
    t = t1
    for _ in range(segmentos):
        cos1, sin1 = math.cos(t), math.sin(t)
        cos2, sin2 = math.cos(t + delta), math.sin(t + delta)
        puntos = [
            (cos1 - k * sin1, sin1 + k * cos1),
            (cos2 + k * sin2, sin2 - k * cos2),
            (cos2, sin2),
        ]
        curva = []
        for ex, ey in puntos:
            px, py = rx * ex, ry * ey
            curva += [cos_fi * px - sin_fi * py + cx, sin_fi * px + cos_fi * py + cy]
        curvas.append(tuple(curva))
        t += delta
    return curvas


def segmentos_svg(d: str) -> list:
    """
    Interpreta ``d`` y devuelve segmentos absolutos: ``('M', x, y)``,
    ``('L', x, y)``, ``('C', c1x, c1y, c2x, c2y, x, y)`` y ``('Z',)``.
    Las cuadráticas y los arcos se convierten a cúbicas.
    """
    lector = _Lector(d)
    segmentos = []
    comando = None
    x = y = 0.0
    inicio_x = inicio_y = 0.0
    control_c = control_q = None  # último punto de control (para S y T)
    numero = lector.numero

    while not lector.fin():
        if lector.hay_comando():
            comando = lector.comando()
            if comando in 'Zz':
                segmentos.append(('Z',))
                x, y = inicio_x, inicio_y
                control_c = control_q = None
                continue
        elif comando is None:
            break

        relativo = comando.islower()
        base = comando.upper()
        ox, oy = (x, y) if relativo else (0.0, 0.0)
        nuevo_c = nuevo_q = None

        if base == 'M':
            x, y = ox + numero(), oy + numero()
            segmentos.append(('M', x, y))
            inicio_x, inicio_y = x, y
            comando = 'l' if relativo else 'L'  # pares siguientes = líneas
        elif base == 'L':
            x, y = ox + numero(), oy + numero()
            segmentos.append(('L', x, y))
        elif base == 'H':
            x = ox + numero()
            segmentos.append(('L', x, y))
        elif base == 'V':
            y = oy + numero()
            segmentos.append(('L', x, y))
        elif base == 'C':
            c1x, c1y = ox + numero(), oy + numero()
            c2x, c2y = ox + numero(), oy + numero()
            x, y = ox + numero(), oy + numero()
            segmentos.append(('C', c1x, c1y, c2x, c2y, x, y))
            nuevo_c = (c2x, c2y)
        elif base == 'S':
            c1x, c1y = (2 * x - control_c[0], 2 * y - control_c[1]) if control_c else (x, y)
            c2x, c2y = ox + numero(), oy + numero()
            x, y = ox + numero(), oy + numero()
            segmentos.append(('C', c1x, c1y, c2x, c2y, x, y))
            nuevo_c = (c2x, c2y)
        elif base in 'QT':
            if base == 'Q':
                qx, qy = ox + numero(), oy + numero()
            else:
                qx, qy = (2 * x - control_q[0], 2 * y - control_q[1]) if control_q else (x, y)
            fx, fy = ox + numero(), oy + numero()
            segmentos.append((
                'C',
                x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y),
                fx + 2 / 3 * (qx - fx), fy + 2 / 3 * (qy - fy),
                fx, fy,
            ))
            x, y = fx, fy
            nuevo_q = (qx, qy)
        elif base == 'A':
            rx, ry, angulo = numero(), numero(), numero()
            grande, barrido = lector.bandera(), lector.bandera()
            fx, fy = ox + numero(), oy + numero()
            for curva in _arco_a_beziers(x, y, rx, ry, angulo, grande, barrido, fx, fy):
                segmentos.append(('C', *curva))
            x, y = fx, fy
        control_c, control_q = nuevo_c, nuevo_q

    return segmentos


class IconoSvg(Flowable):
    """Ícono relleno de un path SVG de ``caja`` × ``caja`` unidades (24 en MDI)."""

    def __init__(self, d: str, tamano: float, color, caja: float = 24):
        super().__init__()
        self.segmentos = segmentos_svg(d)
        self.tamano = tamano
        self.color = color
        self.caja = caja
        self.width = self.height = tamano

    def wrap(self, *_):
        return self.tamano, self.tamano

    def draw(self):
        dibujar_icono(self.canv, self.segmentos, 0, 0, self.tamano, self.color, self.caja)


def dibujar_icono(canvas, segmentos, x, y, tamano, color, caja: float = 24) -> None:
    """Dibuja ``segmentos`` (de :func:`segmentos_svg`) con la esquina inferior
    izquierda en ``(x, y)``. El eje Y del SVG crece hacia abajo: se invierte."""
    escala = tamano / caja
    path = canvas.beginPath()
    for segmento in segmentos:
        tipo, valores = segmento[0], segmento[1:]
        puntos = [
            (x + valores[j] * escala, y + tamano - valores[j + 1] * escala)
            for j in range(0, len(valores), 2)
        ]
        if tipo == 'M':
            path.moveTo(*puntos[0])
        elif tipo == 'L':
            path.lineTo(*puntos[0])
        elif tipo == 'C':
            path.curveTo(*puntos[0], *puntos[1], *puntos[2])
        else:
            path.close()
    canvas.saveState()
    canvas.setFillColor(color)
    canvas.drawPath(path, stroke=0, fill=1, fillMode=1)  # nonzero, como el SVG
    canvas.restoreState()

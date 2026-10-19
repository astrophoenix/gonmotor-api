"""
PDF de UNA inspección (informe de diagnóstico para el cliente).

Misma línea gráfica que el PDF de la cotización (``apps/cotizaciones/pdf.py``):
cabecera con logo e identidad, filetes negros, divisorias grises y casi sin
rellenos. El cuerpo sigue las cuatro fases de la pantalla de la inspección.

Página 1:
  ┌──────────────────────────────────────────────────────────────────┐
  │ [logo] EMPRESA                              INFORME DE INSPECCIÓN │
  │        RUC · Taller · Dirección · Telf.            Nº INS-000123  │
  │                                       Inicio … · Cierre …         │
  ├──────────────────────────────────────────────────────────────────┤
  │ CLIENTE            │ VEHÍCULO            │ INSPECCIÓN             │
  ├──────────────────────────────────────────────────────────────────┤
  │ ① DIAGNÓSTICO     motivo · DTC · diagnóstico · recomendaciones    │
  │ ② TESTIGOS        cuadrícula 10×2, encendido en color / apagado   │
  │ ③ EVIDENCIA       fotos de los hallazgos (3 por fila)             │
  │ ④ PLAN DE TRABAJO servicios y repuestos, prioridad, opcional      │
  │ ___________________       ___________________                     │
  └──────────────────────────────────────────────────────────────────┘
Páginas 2+: "EMPRESA · INFORME DE INSPECCIÓN Nº … (continuación)". Pie en
todas: generado por / "Página X de Y". Sin precios: van en la cotización.
"""

import logging
import re
from decimal import Decimal
from io import BytesIO

from django.utils import timezone
from PIL import Image as PilImage
from PIL import ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from apps.core.utils.pdf_export import (
    LST_GRIS_FILA,
    LST_GRIS_TEXTO,
    LST_MARGEN_INFERIOR,
    LST_MARGEN_LATERAL,
    LST_MARGEN_SUPERIOR,
    NEGRO,
    PAGINA,
    NumberedCanvas,
    _coincidir_logo,
    _datos_identidad,
    _escape_xml,
    _leer_logo,
)
from apps.core.utils.svg_path import dibujar_icono, segmentos_svg
from apps.cotizaciones.pdf import (
    ALTO_CONTINUACION,
    ALTO_PIE,
    CELDA,
    CELDA_DER,
    CELDA_GRIS,
    COLOR_MARCA,
    COLUMNA,
    COLUMNA_DER,
    FIRMA_NOMBRE,
    FIRMA_ROL,
    GRIS_FONDO,
    GRIS_SUAVE,
    GRUPO,
    GRUPO_DER,
    LOGO_MAX,
    NOTA_TABLA,
    SIN_PADDING,
    VACIO,
    _cantidad,
    _Documento,
    _estilo,
    _local,
    _nombre_persona,
    _texto,
)

from .testigos import TESTIGOS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MEDIDAS Y COLORES
# ---------------------------------------------------------------------------

GRIS_BORDE = colors.HexColor('#D1D5DB')      # contorno de piezas apagadas / vacías
GRIS_APAGADO = colors.HexColor('#BFC5CD')    # ícono de un testigo apagado
GRIS_ETIQUETA_OFF = colors.HexColor('#9CA3AF')
AMBAR_SUAVE = colors.HexColor('#FFFBEB')

# Testigo encendido: (ícono, contorno, fondo). Mismos tonos que la web.
COLORES_TESTIGO = {
    'red': (colors.HexColor('#DC2626'), colors.HexColor('#F87171'), colors.HexColor('#FEF2F2')),
    'yellow': (colors.HexColor('#D97706'), colors.HexColor('#FBBF24'), colors.HexColor('#FFFBEB')),
    'green': (colors.HexColor('#16A34A'), colors.HexColor('#4ADE80'), colors.HexColor('#F0FDF4')),
}

PRIORIDADES = {
    'ALTA': ('Alta', '#DC2626'),
    'MEDIA': ('Media', '#D97706'),
    'BAJA': ('Baja', '#6B7280'),
}

ESPACIO_FASE = 14            # aire antes de cada fase
FOTOS_POR_FILA = 3
PROPORCION_FOTO = 3 / 4      # alto / ancho
PPP_FOTO = 170               # resolución de las fotos incrustadas

# ---------------------------------------------------------------------------
# ESTILOS
# ---------------------------------------------------------------------------

FASE_TITULO = _estilo('InsFaseTitulo', 10.5, 'Helvetica-Bold', COLOR_MARCA, charSpace=0.8)
FASE_RESUMEN = _estilo('InsFaseResumen', 8, color=GRIS_SUAVE, alineacion=TA_RIGHT)
SUBTITULO = _estilo('InsSubtitulo', 7, 'Helvetica-Bold', GRIS_SUAVE, charSpace=0.8)
TEXTO_BLOQUE = _estilo('InsTextoBloque', 8.5, interlinea=11.5)
TESTIGO_ON = _estilo('InsTestigoOn', 6.5, 'Helvetica-Bold', alineacion=TA_CENTER, interlinea=7.5)
TESTIGO_OFF = _estilo('InsTestigoOff', 6.5, color=GRIS_ETIQUETA_OFF, alineacion=TA_CENTER, interlinea=7.5)
LEYENDA = _estilo('InsLeyenda', 7.5, color=GRIS_SUAVE, interlinea=9.5)
PIE_FOTO = _estilo('InsPieFoto', 7.5, color=LST_GRIS_TEXTO, interlinea=9.5)
CIERRE = _estilo('InsCierre', 7.5, color=GRIS_SUAVE, interlinea=10)


def _km(valor) -> str:
    return f'{valor:,} km'.replace(',', '.') if valor else ''


def _fecha_hora(valor) -> str:
    valor = _local(valor)
    return valor.strftime('%d/%m/%Y %H:%M') if valor else ''


def _duracion(minutos) -> str:
    if minutos is None:
        return ''
    horas, resto = divmod(int(minutos), 60)
    if horas and resto:
        return f'{horas} h {resto} min'
    return f'{horas} h' if horas else f'{resto} min'


def _plural(n, singular, plural) -> str:
    return f'{n} {singular if n == 1 else plural}'


def _orden_plan(item):
    rango = {'ALTA': 0, 'MEDIA': 1, 'BAJA': 2}.get(item.prioridad, 3)
    return rango, item.created_at or timezone.now(), item.pk or 0


def codigos_dtc(texto) -> list:
    """'p0301, P0420;p0171' → ['P0301', 'P0420', 'P0171'] (sin repetidos)."""
    vistos = []
    for codigo in re.split(r'[\s,;]+', texto or ''):
        codigo = codigo.strip().upper()
        if codigo and codigo not in vistos:
            vistos.append(codigo)
    return vistos


# ---------------------------------------------------------------------------
# FLOWABLES PROPIOS
# ---------------------------------------------------------------------------

def _circulo_numero(canvas, cx, cy, radio, numero, relleno=True):
    """Círculo con el número de la fase: navy relleno, o solo contorno (vacía)."""
    canvas.saveState()
    if relleno:
        canvas.setFillColor(COLOR_MARCA)
        canvas.circle(cx, cy, radio, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
    else:
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(GRIS_BORDE)
        canvas.setLineWidth(1)
        canvas.circle(cx, cy, radio, stroke=1, fill=1)
        canvas.setFillColor(GRIS_SUAVE)
    tamano = radio * 1.05
    canvas.setFont('Helvetica-Bold', tamano)
    canvas.drawCentredString(cx, cy - tamano * 0.36, str(numero))
    canvas.restoreState()


class _EncabezadoFase(Flowable):
    """① TÍTULO ……………………………… resumen, sobre un filete negro."""

    ALTO = 24

    def __init__(self, numero, titulo, resumen, ancho):
        super().__init__()
        self.numero, self.titulo, self.resumen, self.ancho = numero, titulo, resumen, ancho

    def wrap(self, *_):
        return self.ancho, self.ALTO

    def draw(self):
        c = self.canv
        radio = 8
        base = 6
        cy = base + radio + 1
        _circulo_numero(c, radio, cy, radio, self.numero)
        titulo = Paragraph(self.titulo.upper(), FASE_TITULO)
        resumen = Paragraph(_escape_xml(self.resumen), FASE_RESUMEN)
        ancho_resumen = self.ancho * 0.45
        _, alto_t = titulo.wrap(self.ancho - ancho_resumen - radio * 2 - 8, 20)
        titulo.drawOn(c, radio * 2 + 8, cy - alto_t / 2 + 1)
        _, alto_r = resumen.wrap(ancho_resumen, 20)
        resumen.drawOn(c, self.ancho - ancho_resumen, cy - alto_r / 2 + 1)
        c.setStrokeColor(NEGRO)
        c.setLineWidth(1)
        c.line(0, 0, self.ancho, 0)


class _Puntos(Flowable):
    """Texto precedido de un punto de color (prioridad, leyenda). Los puntos se
    dibujan: los glifos de viñeta de las fuentes base descuadran la línea."""

    RADIO = 2.4

    def __init__(self, elementos, estilo, alineacion='L', separacion=10):
        super().__init__()
        self.elementos = elementos  # [(texto, color)]
        self.estilo, self.alineacion, self.separacion = estilo, alineacion, separacion

    def _ancho_total(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth

        anchos = [self.RADIO * 2 + 4 + stringWidth(t, self.estilo.fontName, self.estilo.fontSize)
                  for t, _ in self.elementos]
        return anchos, sum(anchos) + self.separacion * (len(anchos) - 1)

    def wrap(self, ancho_disponible, _):
        self.ancho = ancho_disponible
        return ancho_disponible, self.estilo.leading

    def draw(self):
        c = self.canv
        anchos, total = self._ancho_total()
        x = self.ancho - total if self.alineacion == 'R' else 0
        base = self.estilo.leading - self.estilo.fontSize
        for (texto, color), ancho in zip(self.elementos, anchos):
            c.setFillColor(colors.HexColor(color) if isinstance(color, str) else color)
            c.circle(x + self.RADIO, base + self.estilo.fontSize * 0.36, self.RADIO, stroke=0, fill=1)
            c.setFillColor(self.estilo.textColor)
            c.setFont(self.estilo.fontName, self.estilo.fontSize)
            c.drawString(x + self.RADIO * 2 + 4, base, texto)
            x += ancho + self.separacion


class _Chips(Flowable):
    """Códigos DTC como etiquetas redondeadas que saltan de línea si no caben."""

    ALTO = 15
    SEPARACION = 4

    def __init__(self, codigos, ancho):
        super().__init__()
        self.codigos, self.ancho = codigos, ancho
        self._posiciones = []
        x = y = 0
        for codigo in codigos:
            w = self._ancho_chip(codigo)
            if x and x + w > ancho:
                x, y = 0, y + self.ALTO + self.SEPARACION
            self._posiciones.append((x, y, w, codigo))
            x += w + self.SEPARACION
        self.alto = (y + self.ALTO) if codigos else 0

    @staticmethod
    def _ancho_chip(codigo):
        from reportlab.pdfbase.pdfmetrics import stringWidth

        return stringWidth(codigo, 'Courier-Bold', 8.5) + 12

    def wrap(self, *_):
        return self.ancho, self.alto

    def draw(self):
        c = self.canv
        for x, y, w, codigo in self._posiciones:
            base = self.alto - y - self.ALTO
            c.setFillColor(AMBAR_SUAVE)
            c.setStrokeColor(colors.HexColor('#FCD34D'))
            c.setLineWidth(0.7)
            c.roundRect(x, base, w, self.ALTO, 3, stroke=1, fill=1)
            c.setFillColor(colors.HexColor('#92400E'))
            c.setFont('Courier-Bold', 8.5)
            c.drawCentredString(x + w / 2, base + 4.3, codigo)


class _NuevaHoja(Flowable):
    """Salto de página que no deja una hoja en blanco: si el contenido anterior
    ya llenó la hoja (el marco está al inicio), no ocupa nada."""

    def wrap(self, ancho, alto):
        if getattr(self, '_frame', None) is not None and self._frame._atTop:
            return 0, 0
        return 0, alto + 1  # no cabe → la plantilla pasa a la hoja siguiente

    def split(self, *_):
        return []

    def draw(self):
        pass


class _Testigo(Flowable):
    """Pieza de la cuadrícula de testigos: encendido en color, apagado en gris."""

    def __init__(self, path, etiqueta, color, encendido, ancho, alto):
        super().__init__()
        self.segmentos = segmentos_svg(path)
        self.etiqueta = Paragraph(_escape_xml(etiqueta), TESTIGO_ON if encendido else TESTIGO_OFF)
        self.color, self.encendido = color, encendido
        self.ancho, self.alto = ancho, alto

    def wrap(self, *_):
        return self.ancho, self.alto

    def draw(self):
        c = self.canv
        icono, contorno, fondo = COLORES_TESTIGO.get(self.color, COLORES_TESTIGO['yellow'])
        c.saveState()
        if self.encendido:
            c.setFillColor(fondo)
            c.setStrokeColor(contorno)
            c.setLineWidth(1.1)
            c.roundRect(0.5, 0.5, self.ancho - 1, self.alto - 1, 5, stroke=1, fill=1)
        else:
            c.setStrokeColor(GRIS_BORDE)
            c.setLineWidth(0.5)
            c.roundRect(0.5, 0.5, self.ancho - 1, self.alto - 1, 5, stroke=1, fill=0)
        tamano = 18
        dibujar_icono(
            c, self.segmentos, (self.ancho - tamano) / 2, self.alto - tamano - 6, tamano,
            icono if self.encendido else GRIS_APAGADO,
        )
        if self.encendido:
            # Punto "encendido" en la esquina: se distingue aun impreso en grises.
            c.setFillColor(icono)
            c.circle(self.ancho - 6, self.alto - 6, 2.2, stroke=0, fill=1)
        ancho_texto = self.ancho - 4
        _, alto_texto = self.etiqueta.wrap(ancho_texto, self.alto)
        zona = self.alto - tamano - 8  # espacio bajo el ícono
        self.etiqueta.drawOn(c, 2, max(3, (zona - alto_texto) / 2 + 1))
        c.restoreState()


class _Foto(Flowable):
    """Foto recortada a 4:3 con esquinas redondeadas."""

    def __init__(self, contenido: bytes, ancho, alto):
        super().__init__()
        self.lector = ImageReader(BytesIO(contenido))
        self.ancho, self.alto = ancho, alto

    def wrap(self, *_):
        return self.ancho, self.alto

    def draw(self):
        c = self.canv
        c.saveState()
        recorte = c.beginPath()
        recorte.roundRect(0, 0, self.ancho, self.alto, 4)
        c.clipPath(recorte, stroke=0, fill=0)
        c.drawImage(self.lector, 0, 0, self.ancho, self.alto)
        c.restoreState()
        c.setStrokeColor(GRIS_BORDE)
        c.setLineWidth(0.5)
        c.roundRect(0, 0, self.ancho, self.alto, 4, stroke=1, fill=0)


def _preparar_foto(archivo, ancho_pt, alto_pt):
    """Lee la foto del almacenamiento, la recorta al centro a la proporción de
    la celda y la reduce a ``PPP_FOTO``: el PDF pesa poco y se ve nítido."""
    if not archivo or not getattr(archivo, 'name', ''):
        return None
    try:
        from django.core.files.storage import default_storage

        with default_storage.open(archivo.name, 'rb') as fh:
            imagen = PilImage.open(BytesIO(fh.read()))
            imagen.load()
        imagen = ImageOps.exif_transpose(imagen).convert('RGB')
        ancho_px = round(ancho_pt / 72 * PPP_FOTO)
        alto_px = round(alto_pt / 72 * PPP_FOTO)
        imagen = ImageOps.fit(imagen, (ancho_px, alto_px), method=PilImage.LANCZOS)
        salida = BytesIO()
        imagen.save(salida, 'JPEG', quality=82, optimize=True)
        return salida.getvalue()
    except Exception:
        logger.warning('No se pudo incrustar la foto %s en el PDF', archivo.name)
        return None


def _bloque_texto(texto, vacio='Sin registro.'):
    """Texto de una fase alineado con su subtítulo; vacío, en cursiva gris."""
    contenido = _texto(texto)
    return Paragraph(contenido, TEXTO_BLOQUE) if contenido else Paragraph(vacio, VACIO)


def _caja_vacia(texto, ancho):
    """Estado vacío de una fase: recuadro punteado con el mensaje centrado."""
    caja = Table([[Paragraph(texto, VACIO)]], colWidths=[ancho])
    caja.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
        ('BOX', (0, 0), (-1, -1), 0.6, GRIS_BORDE, None, (3, 3)),
    ]))
    return caja


# ---------------------------------------------------------------------------
# DOCUMENTO: CABECERA, DATOS Y PÁGINA (heredados de la cotización)
# ---------------------------------------------------------------------------

class _DocumentoInspeccion(_Documento):
    TIPO = 'INFORME DE INSPECCIÓN'

    def __init__(self, inspeccion, empresa, taller, usuario, logo):  # noqa: super no aplica
        self.inspeccion = inspeccion
        self.empresa = empresa
        self.taller = taller
        self.identidad = _datos_identidad(empresa, taller)
        self.usuario = (usuario or '').strip()
        self.generado = timezone.localtime()
        self.numero = inspeccion.numero_inspeccion or ''

        if logo is None:
            logo = _coincidir_logo(empresa, taller)
        self.logo_bin, self.logo_ancho, self.logo_alto = _leer_logo(logo, LOGO_MAX * 2.2, LOGO_MAX)

        recepcion = inspeccion.recepcion
        self.recepcion = recepcion
        self.cliente = inspeccion.cliente or (recepcion.cliente if recepcion else None)
        self.vehiculo = inspeccion.vehiculo or (recepcion.vehiculo if recepcion else None)
        self.inicio = inspeccion.fecha_inspeccion or inspeccion.created_at
        self.cierre = inspeccion.fecha_finalizacion
        self.cotizacion = next(iter(inspeccion.cotizaciones_generadas.all()), None)

        self.dtc = codigos_dtc(inspeccion.codigos_dtc)
        self.testigos_on = [t for t in TESTIGOS if getattr(inspeccion, t[0], False)]
        self.fotos = [f for f in inspeccion.fotos.all() if f.imagen or f.miniatura]
        # Lo urgente primero; a igual prioridad, el orden en que se registró.
        self.servicios = sorted(inspeccion.servicios_detectados.all(), key=_orden_plan)
        self.repuestos = sorted(inspeccion.repuestos_sugeridos.all(), key=_orden_plan)
        self.horas = sum((Decimal(str(s.horas_estimadas or 0)) for s in self.servicios), Decimal('0'))

    def meta(self) -> list:
        meta = [f'Inicio: {_fecha_hora(self.inicio)}'] if self.inicio else []
        if self.cierre:
            meta.append(f'Cierre: {_fecha_hora(self.cierre)}')
        else:
            meta.append(f'Estado: {self.inspeccion.get_estado_display()}')
        return meta

    def datos(self, ancho: float) -> Table:
        i = self.inspeccion
        cliente = self.cliente
        tipo_id = ''
        if cliente is not None and hasattr(cliente, 'get_tipo_identificacion_display'):
            tipo_id = cliente.get_tipo_identificacion_display()
        filas_cliente = [
            (tipo_id or 'Identificación', getattr(cliente, 'identificacion', '')),
            ('Teléfono', getattr(cliente, 'telefono', '')),
            ('Correo', getattr(cliente, 'email', '')),
            ('Dirección', getattr(cliente, 'direccion', '')),
        ]

        vehiculo = self.vehiculo
        if vehiculo is not None:
            descripcion = ' '.join(
                str(p) for p in (vehiculo.marca, vehiculo.modelo, getattr(vehiculo, 'anio', '')) if p
            )
            km = i.kilometraje_diagnostico or getattr(vehiculo, 'kilometraje_actual', None)
            principal_vehiculo = vehiculo.placa or ''
            filas_vehiculo = [
                ('Vehículo', descripcion),
                ('Color', getattr(vehiculo, 'color', '')),
                ('VIN', getattr(vehiculo, 'vin', '')),
                ('Kilometraje', _km(km)),
            ]
        else:
            principal_vehiculo, filas_vehiculo = 'Sin vehículo asignado', []

        duracion = _duracion(i.duracion_inspeccion) if self.cierre else ''
        filas_doc = [
            ('Tipo', i.get_tipo_inspeccion_display()),
            ('Técnico', _nombre_persona(i.responsable)),
            ('Estado', i.get_estado_display()),
            ('Duración', duracion),
            ('Recepción', getattr(self.recepcion, 'numero_recepcion', '') or ''),
            ('Cotización', getattr(self.cotizacion, 'numero_cotizacion', '') or ''),
        ]

        separacion = 12
        anchos = [ancho * 0.36, ancho * 0.32, ancho * 0.32]
        internos = [a - separacion * 2 for a in anchos]
        celdas = [
            self._bloque('CLIENTE', getattr(cliente, 'nombre', ''), filas_cliente, internos[0]),
            self._bloque('VEHÍCULO', principal_vehiculo, filas_vehiculo, internos[1]),
            self._bloque('INSPECCIÓN', getattr(self.taller, 'nombre', '') or 'Datos del documento',
                         filas_doc, internos[2]),
        ]
        tabla = Table([celdas], colWidths=anchos)
        tabla.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), separacion),
            ('RIGHTPADDING', (0, 0), (-1, -1), separacion),
            ('LEFTPADDING', (0, 0), (0, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LINEAFTER', (0, 0), (-2, -1), 0.4, LST_GRIS_FILA),
            ('LINEBELOW', (0, 0), (-1, -1), 0.4, LST_GRIS_FILA),
        ]))
        return tabla

    # -- resúmenes de cada fase -------------------------------------------

    def resumenes(self) -> dict:
        items = len(self.servicios) + len(self.repuestos)
        plan = _plural(items, 'ítem', 'ítems') if items else 'Sin ítems'
        if self.horas:
            plan += f' · {_cantidad(self.horas)} h'
        return {
            'diagnostico': _plural(len(self.dtc), 'código DTC', 'códigos DTC') if self.dtc else 'Sin códigos DTC',
            'testigos': f'{len(self.testigos_on)} de {len(TESTIGOS)} encendidos'
            if self.testigos_on else 'Ninguno encendido',
            'evidencia': _plural(len(self.fotos), 'foto', 'fotos') if self.fotos else 'Sin fotos',
            'plan': plan,
        }


# ---------------------------------------------------------------------------
# FASES
# ---------------------------------------------------------------------------

def _fase_diagnostico(doc: _DocumentoInspeccion, ancho: float) -> list:
    i = doc.inspeccion
    separacion = 20
    mitad = (ancho - separacion) / 2

    if doc.dtc:
        dtc = [_Chips(doc.dtc, mitad)]
    else:
        dtc = [Paragraph('El escáner no reportó códigos de falla.', VACIO)]
    fila = Table(
        [[
            [Paragraph('MOTIVO DE INGRESO', SUBTITULO), Spacer(1, 4),
             _bloque_texto(i.motivo_ingreso, vacio='Sin motivo registrado.')],
            '',
            [Paragraph('CÓDIGOS DE FALLA (DTC)', SUBTITULO), Spacer(1, 4), *dtc],
        ]],
        colWidths=[mitad, separacion, mitad],
    )
    fila.setStyle(TableStyle(SIN_PADDING + [('VALIGN', (0, 0), (-1, -1), 'TOP')]))

    return [
        _EncabezadoFase(1, 'Diagnóstico', doc.resumenes()['diagnostico'], ancho),
        Spacer(1, 9),
        fila,
        Spacer(1, 10),
        Paragraph('DIAGNÓSTICO TÉCNICO', SUBTITULO), Spacer(1, 4),
        _bloque_texto(i.diagnostico_tecnico, vacio='Sin diagnóstico registrado.'),
        Spacer(1, 10),
        Paragraph('RECOMENDACIONES', SUBTITULO), Spacer(1, 4),
        _bloque_texto(i.recomendaciones, vacio='Sin recomendaciones registradas.'),
    ]


def _fase_testigos(doc: _DocumentoInspeccion, ancho: float) -> list:
    i = doc.inspeccion
    columnas = 10
    separacion = 4
    ancho_pieza = (ancho - separacion * (columnas - 1)) / columnas
    alto_pieza = 47

    piezas = [
        _Testigo(path, etiqueta, color, bool(getattr(i, campo, False)), ancho_pieza, alto_pieza)
        for campo, etiqueta, color, path in TESTIGOS
    ]
    filas = [piezas[n:n + columnas] for n in range(0, len(piezas), columnas)]
    filas[-1] += [''] * (columnas - len(filas[-1]))
    anchos = []
    for n in range(columnas):
        anchos += [ancho_pieza] + ([separacion] if n < columnas - 1 else [])
    contenido = []
    for fila in filas:
        celdas = []
        for n, pieza in enumerate(fila):
            celdas += [pieza] + ([''] if n < columnas - 1 else [])
        contenido.append(celdas)
    rejilla = Table(contenido, colWidths=anchos, rowHeights=[alto_pieza + separacion] * len(contenido))
    rejilla.setStyle(TableStyle(SIN_PADDING + [('VALIGN', (0, 0), (-1, -1), 'TOP')]))

    if doc.testigos_on:
        encendidos = ', '.join(t[1] for t in doc.testigos_on)
        leyenda_izq = f'<b>Encendidos:</b> {_escape_xml(encendidos)}.'
    else:
        leyenda_izq = 'Ningún testigo encendido en el tablero durante el diagnóstico.'
    leyenda = Table(
        [[Paragraph(leyenda_izq, LEYENDA),
          _Puntos([('Encendido', '#DC2626'), ('Apagado', GRIS_APAGADO)], LEYENDA, 'R')]],
        colWidths=[ancho * 0.72, ancho * 0.28],
    )
    leyenda.setStyle(TableStyle(SIN_PADDING + [('VALIGN', (0, 0), (-1, -1), 'TOP')]))

    flujo = [
        _EncabezadoFase(2, 'Testigos del tablero', doc.resumenes()['testigos'], ancho),
        Spacer(1, 9),
        rejilla,
        Spacer(1, 2),
        leyenda,
    ]
    if i.otros_testigos_observaciones:
        flujo += [Spacer(1, 8), Paragraph('OBSERVACIONES DEL TABLERO', SUBTITULO), Spacer(1, 4),
                  _bloque_texto(i.otros_testigos_observaciones)]
    return [KeepTogether(flujo[:5]), *flujo[5:]]


def filas_de_fotos(fotos, ancho: float) -> list:
    """Fotos recortadas a 4:3, ``FOTOS_POR_FILA`` por fila y numeradas con su
    descripción al pie. La comparten la inspección y la orden de trabajo."""
    separacion = 10
    ancho_foto = (ancho - separacion * (FOTOS_POR_FILA - 1)) / FOTOS_POR_FILA
    alto_foto = ancho_foto * PROPORCION_FOTO

    celdas = []
    for numero, foto in enumerate(fotos, start=1):
        contenido = _preparar_foto(foto.imagen, ancho_foto, alto_foto) or _preparar_foto(
            foto.miniatura, ancho_foto, alto_foto,
        )
        if contenido:
            imagen = _Foto(contenido, ancho_foto, alto_foto)
        else:
            imagen = Table([[Paragraph('Foto no disponible', VACIO)]], colWidths=[ancho_foto],
                           rowHeights=[alto_foto])
            imagen.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('BOX', (0, 0), (-1, -1), 0.6, GRIS_BORDE, None, (3, 3)),
            ]))
        descripcion = _escape_xml((foto.descripcion or '').strip())
        pie = f'<b>{numero}</b>' + (f' · {descripcion}' if descripcion else '')
        celdas.append([imagen, Spacer(1, 4), Paragraph(pie, PIE_FOTO)])

    tablas = []
    for n in range(0, len(celdas), FOTOS_POR_FILA):
        fila = celdas[n:n + FOTOS_POR_FILA]
        fila = fila + [''] * (FOTOS_POR_FILA - len(fila))
        contenido = []
        anchos = []
        for i, celda in enumerate(fila):
            contenido += [celda] + ([''] if i < FOTOS_POR_FILA - 1 else [])
            anchos += [ancho_foto] + ([separacion] if i < FOTOS_POR_FILA - 1 else [])
        tabla = Table([contenido], colWidths=anchos)
        tabla.setStyle(TableStyle(SIN_PADDING + [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        tablas.append(tabla)
    return tablas


def _fase_evidencia(doc: _DocumentoInspeccion, ancho: float) -> list:
    encabezado = _EncabezadoFase(3, 'Evidencia fotográfica', doc.resumenes()['evidencia'], ancho)
    if not doc.fotos:
        # Mismo aire inferior que deja la última fila de fotos.
        return [KeepTogether([
            encabezado, Spacer(1, 9), _caja_vacia('No se registraron fotos de los hallazgos.', ancho), Spacer(1, 10),
        ])]

    flujo = [encabezado, Spacer(1, 9), *filas_de_fotos(doc.fotos, ancho)]
    # El encabezado no se queda solo al pie de una página: viaja con la 1.ª fila.
    return [KeepTogether(flujo[:3]), *flujo[3:]]


def _fase_plan(doc: _DocumentoInspeccion, ancho: float) -> list:
    encabezado = _EncabezadoFase(4, 'Plan de trabajo', doc.resumenes()['plan'], ancho)
    if not (doc.servicios or doc.repuestos):
        return [KeepTogether([
            encabezado, Spacer(1, 9), _caja_vacia('El técnico no registró servicios ni repuestos.', ancho),
        ])]

    columnas = [
        ('#', 0.28, 'L'),
        ('Descripción', 3.6, 'L'),
        ('Cant.', 0.7, 'R'),
        ('Prioridad', 0.9, 'L'),
        ('Requerimiento', 1.2, 'L'),
    ]
    factor = ancho / sum(a for _, a, _ in columnas)
    anchos = [a * factor for _, a, _ in columnas]
    filas = [[Paragraph(t.upper(), COLUMNA_DER if al == 'R' else COLUMNA) for t, _, al in columnas]]
    comandos = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (0, -1), 2),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, NEGRO),
    ]

    grupos = [
        ('Mano de obra', doc.servicios, lambda s: f'{_cantidad(s.horas_estimadas)} h',
         f'{_cantidad(doc.horas)} h' if doc.horas else ''),
        ('Repuestos', doc.repuestos, lambda r: _cantidad(r.cantidad), _plural(len(doc.repuestos), 'ítem', 'ítems')),
    ]
    numero = 0
    for titulo, items, cantidad, total in grupos:
        if not items:
            continue
        fila_grupo = len(filas)
        filas.append([Paragraph(titulo.upper(), GRUPO), '', Paragraph(total, GRUPO_DER), '', ''])
        comandos += [
            ('SPAN', (0, fila_grupo), (1, fila_grupo)),
            ('BACKGROUND', (0, fila_grupo), (-1, fila_grupo), GRIS_FONDO),
            ('TOPPADDING', (0, fila_grupo), (-1, fila_grupo), 4),
            ('BOTTOMPADDING', (0, fila_grupo), (-1, fila_grupo), 4),
        ]
        for item in items:
            numero += 1
            etiqueta, color = PRIORIDADES.get(item.prioridad, (item.prioridad or '—', '#6B7280'))
            prioridad = _Puntos([(etiqueta, color)], CELDA) if item.prioridad else Paragraph('—', CELDA)
            tipo = '<font color="#6B7280">Opcional</font>' if item.es_sugerido else 'Necesario'
            filas.append([
                Paragraph(str(numero), CELDA_GRIS),
                Paragraph(_texto(item.descripcion) or '—', CELDA),
                Paragraph(cantidad(item), CELDA_DER),
                prioridad,
                Paragraph(tipo, CELDA),
            ])
            if item is not items[-1]:
                comandos.append(('LINEBELOW', (0, len(filas) - 1), (-1, len(filas) - 1), 0.3, LST_GRIS_FILA))
    comandos.append(('LINEBELOW', (0, -1), (-1, -1), 0.8, NEGRO))

    tabla = Table(filas, colWidths=anchos, repeatRows=1)
    tabla.setStyle(TableStyle(comandos))

    nota = 'Los ítems «Opcional» son recomendaciones que el cliente puede postergar. '
    if doc.cotizacion is not None and doc.cotizacion.numero_cotizacion:
        nota += f'Los precios se detallan en la cotización {doc.cotizacion.numero_cotizacion}.'
    else:
        nota += 'Los precios se detallan en la cotización.'
    return [
        KeepTogether([
            encabezado,
            Spacer(1, 9),
            Paragraph('Lo que el técnico propone hacer en el vehículo.', LEYENDA),
            Spacer(1, 6),
        ]),
        tabla,
        Spacer(1, 3),
        Paragraph(_escape_xml(nota), NOTA_TABLA),
    ]


def _cierre(doc: _DocumentoInspeccion, ancho: float) -> list:
    cliente = getattr(doc.cliente, 'nombre', '') or ''
    tecnico = _nombre_persona(doc.inspeccion.responsable)
    ancho_firma = min(ancho * 0.36, 190)
    hueco = ancho - 2 * ancho_firma

    def celda(nombre, rol):
        return [Paragraph(_escape_xml(nombre) or '&nbsp;', FIRMA_NOMBRE), Paragraph(rol, FIRMA_ROL)]

    firmas = Table(
        [[celda(tecnico, 'Técnico responsable'), '', celda(cliente, 'Recibí conforme el informe')]],
        colWidths=[ancho_firma, hueco, ancho_firma],
    )
    firmas.setStyle(TableStyle(SIN_PADDING + [
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('LINEABOVE', (0, 0), (0, 0), 0.6, NEGRO),
        ('LINEABOVE', (2, 0), (2, 0), 0.6, NEGRO),
    ]))
    aviso = Paragraph(
        'Este informe describe el estado del vehículo al momento del diagnóstico. '
        'Ningún trabajo se realiza sin la aprobación del cliente.', CIERRE,
    )
    return [KeepTogether([aviso, Spacer(1, 34), firmas])]


# ---------------------------------------------------------------------------
# API PÚBLICA
# ---------------------------------------------------------------------------

def exportar_inspeccion_pdf(
    buffer: BytesIO,
    inspeccion,
    *,
    empresa=None,
    taller=None,
    usuario: str = '',
    logo=None,
) -> None:
    """Genera el PDF de UNA inspección en ``buffer``."""
    doc_datos = _DocumentoInspeccion(inspeccion, empresa, taller, usuario, logo)
    ancho = PAGINA[0] - 2 * LST_MARGEN_LATERAL
    alto_util = PAGINA[1] - LST_MARGEN_SUPERIOR - LST_MARGEN_INFERIOR - ALTO_PIE

    doc = BaseDocTemplate(
        buffer,
        pagesize=PAGINA,
        leftMargin=LST_MARGEN_LATERAL,
        rightMargin=LST_MARGEN_LATERAL,
        topMargin=LST_MARGEN_SUPERIOR,
        bottomMargin=LST_MARGEN_INFERIOR,
        title=f'Inspección {doc_datos.numero}'.strip(),
        author=doc_datos.identidad['nombre'],
    )
    base = LST_MARGEN_INFERIOR + ALTO_PIE
    sin_padding = dict(leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate(
            id='p1',
            frames=[Frame(LST_MARGEN_LATERAL, base, ancho, alto_util, id='p1', **sin_padding)],
            onPage=doc_datos.dibujar_primera,
        ),
        PageTemplate(
            id='cn',
            frames=[Frame(LST_MARGEN_LATERAL, base, ancho, alto_util - ALTO_CONTINUACION, id='cn', **sin_padding)],
            onPage=doc_datos.dibujar_continuacion,
        ),
    ])

    story = [
        NextPageTemplate('cn'),
        doc_datos.cabecera(ancho),
        doc_datos.datos(ancho),
        Spacer(1, ESPACIO_FASE),
        *_fase_diagnostico(doc_datos, ancho),
        Spacer(1, ESPACIO_FASE),
        *_fase_testigos(doc_datos, ancho),
        Spacer(1, ESPACIO_FASE),
        *_fase_evidencia(doc_datos, ancho),
        _NuevaHoja(),  # el plan de trabajo siempre abre hoja
        *_fase_plan(doc_datos, ancho),
        Spacer(1, ESPACIO_FASE * 1.2),
        *_cierre(doc_datos, ancho),
    ]
    doc.build(story, canvasmaker=NumberedCanvas)

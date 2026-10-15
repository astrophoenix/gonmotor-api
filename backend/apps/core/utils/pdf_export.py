"""
Exportación de PDFs con ReportLab/Platypus, filosofía *ink-friendly*.

LISTADOS (plantilla única, secciones 7-9)
-----------------------------------------
Todos los listados (clientes, vehículos, proveedores, inventario, empleados,
talleres, recepciones…) usan ``generar_listado_pdf``, directamente o a través
de ``PdfExportConfig`` + ``PdfExportService``:

  * Cabecera página 1: logo en escala de grises + datos de la empresa a la
    izquierda y el título a la derecha; filete negro; franja de metadatos
    (fecha y hora, usuario, registros y página X de Y).
  * Páginas 2+: "Empresa · TÍTULO (continuación)" y "Página X de Y", con la
    cabecera de la tabla repetida.
  * Tabla compacta sin rellenos: cabecera en negrita entre filetes negros,
    filas separadas por líneas grises finas, alineación por columna y anchos
    escalados al ancho útil. Orientación vertical u horizontal automática
    según el ancho total de las columnas.

DOCUMENTOS (secciones 1-6)
--------------------------
``_crear_documento`` + ``NumberedCanvas`` montan la cabecera de identidad de
documentos individuales (la cotización, ``apps/cotizaciones/pdf.py``).
"""

from decimal import Decimal
from io import BytesIO
from typing import Iterable, List, Optional, Tuple

from django.http import FileResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdf_gen_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# Helvetica conoce su variante en negrita (fuentes base, sin incrustar).
pdfmetrics.registerFontFamily(
    'Helvetica',
    normal='Helvetica',
    bold='Helvetica-Bold',
    italic='Helvetica-Oblique',
    boldItalic='Helvetica-BoldOblique',
)

# ---------------------------------------------------------------------------
# PÁGINA Y MÁRGENES (A4, perimetrales, aire visual arriba siempre)
# ---------------------------------------------------------------------------

# Hoja A4 (595.27 x 841.89 pt). Ancho útil = 595.27 - 72 = 523.27 pt (7.26 in).
PAGINA = A4

MARGEN_SUPERIOR = 40    # banda libre de aire antes de la cabecera (todas las hojas)
MARGEN_INFERIOR = 40
MARGEN_LATERAL = 36     # left = right

# Línea fina divisoria debajo de la cabecera, con espaciado EQUILIBRADO:
# 10pt de aire junto a la cabecera y 10pt de aire hasta el título del reporte
# (la línea queda centrada entre ambos, ni pegada ni alejada).
ESPACIO_LINEA_DIVISORIA = 10
ESPACIO_POST_CABECERA = 10

# Altura (fija) de la banda ocupada por la cabecera slim de las páginas 2+.
ALTURA_CABECERA_SLIM = 18

# Número de página: se dibuja dentro de la banda del margen superior, a esta
# distancia de la cima de la hoja (respetando el margen superior establecido).
NUMERO_PAGINA_BAJADA = 18  # baseline a MARGEN_SUPERIOR - NUMERO_PAGINA_BAJADA

# Logo: ancho máximo fijo, alto proporcional. Separación logo <-> texto.
ANCHO_LOGO_MAX = 75
ALTO_LOGO_MAX = 50
ESPACIO_LOGO_TEXTO = 10

# Paleta ink-friendly (fondo blanco, tonos tenues y gris asulado).
GRIS_IDENTIDAD = colors.HexColor('#555555')         # datos secundarios
GRIS_INFORMACION = colors.HexColor('#888888')       # metadatos / paginado
GRIS_OSCURO_TEXTO = colors.HexColor('#111827')      # texto de los datos
# Gris ASULADO único: define las líneas de la tabla Y el fondo de la cabecera
# de campos (mismo color). Claro y discreto: la cabecera NO se ve resaltada.
GRIS_ASULADO = colors.HexColor('#D7E0E9')
GRIS_LINEA_FINA = GRIS_ASULADO                      # divisorias de la tabla
GRIS_CABECERA_TABLA = GRIS_ASULADO                  # fondo suave de cabeceras
NEGRO = colors.black                                 # títulos / campos

# Tabla de datos: misma alineación cabecera/datos, texto regular y saldo
# pendiente en NEGRILLA solo si > $0.00.
ESTILO_TABLA_INK_FRIENDLY = [
    ('BACKGROUND', (0, 0), (-1, 0), GRIS_CABECERA_TABLA),   # cabecera gris suave
    ('TEXTCOLOR', (0, 0), (-1, -1), GRIS_OSCURO_TEXTO),
    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
    ('FONTSIZE', (0, 0), (-1, -1), 8.5),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('TOPPADDING', (0, 0), (-1, -1), 5),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    # Línea fina gris suave bajo los nombres de columnas
    ('LINEBELOW', (0, 0), (-1, 0), 0.5, GRIS_LINEA_FINA),
    # Divisorias horizontales finas entre filas de datos
    ('LINEBELOW', (0, 1), (-1, -2), 0.3, GRIS_LINEA_FINA),
    # Cierre inferior de la última fila
    ('LINEABOVE', (0, -1), (-1, -1), 0.5, GRIS_LINEA_FINA),
]


# ---------------------------------------------------------------------------
# 1) NUMBEREDCANVAS — "Página X de Y" (esquina superior derecha, márgenes)
# ---------------------------------------------------------------------------

class NumberedCanvas(pdf_gen_canvas.Canvas):
    """
    Canvas de dos pasadas: captura el estado de cada página y en una segunda
    pasada dibuja "Página X de Y" en la esquina superior derecha, DENTRO de la
    banda del margen superior (respeta ``MARGEN_SUPERIOR`` y ``MARGEN_LATERAL``),
    en todas las hojas y con la misma posición.
    """

    PLANTILLA_NUMERO = 'Página %s de %s'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estados_paginas = []

    def showPage(self) -> None:
        self._estados_paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        numero_total = len(self._estados_paginas)
        for estado in self._estados_paginas:
            self.__dict__.update(estado)
            self._dibujar_paginado(numero_total)
            pdf_gen_canvas.Canvas.showPage(self)
        pdf_gen_canvas.Canvas.save(self)

    def _dibujar_paginado(self, numero_total: int) -> None:
        # Las plantillas pueden fijar por página dónde va el paginado
        # (``_gm_paginado``: x, y, tamaño, plantilla de texto, alineación).
        destino = getattr(self, '_gm_paginado', None)
        if destino:
            x, y, tamano, plantilla, alineacion, *color = destino
            self.saveState()
            self.setFont('Helvetica', tamano)
            self.setFillColor(color[0] if color else colors.black)
            etiqueta = plantilla % (self._pageNumber, numero_total)
            if alineacion == 'R':
                self.drawRightString(x, y, etiqueta)
            else:
                self.drawString(x, y, etiqueta)
            self.restoreState()
            return

        self.saveState()
        self.setFont('Helvetica', 8)
        self.setFillColor(GRIS_INFORMACION)
        etiqueta = self.PLANTILLA_NUMERO % (self._pageNumber, numero_total)
        # Dentro de la banda del margen superior, alineado al margen derecho.
        self.drawRightString(
            self._pagesize[0] - MARGEN_LATERAL,
            self._pagesize[1] - (MARGEN_SUPERIOR - NUMERO_PAGINA_BAJADA),
            etiqueta,
        )
        self.restoreState()


# ---------------------------------------------------------------------------
# 2) LOGO E IDENTIDAD
# ---------------------------------------------------------------------------

def _coincidir_logo(empresa, taller):
    """Devuelve un logo candidato (taller > empresa) o ``None``."""
    return getattr(taller, 'logo', None) or getattr(empresa, 'logo', None) or None


def _leer_logo(
    logo,
    ancho_max: float = ANCHO_LOGO_MAX,
    alto_max: float = ALTO_LOGO_MAX,
) -> Tuple[Optional[bytes], float, float]:
    """
    Lee un logo (ImageField/FieldFile, ruta o bytes) en memoria y calcula sus
    dimensiones finales de impresión (ancho máx. ``ANCHO_LOGO_MAX``, alto máx.
    ``ALTO_LOGO_MAX``, proporción respetada). Devuelve ``(bin, ancho, alto)``
    o ``(None, 0, 0)`` si no hay logo o falla la lectura (el call no rompe).
    """
    try:
        if not logo:
            return None, 0, 0

        if hasattr(logo, 'read'):
            try:
                logo.seek(0)
            except Exception:
                pass
            bin_ = logo.read()
        elif hasattr(logo, 'name') or isinstance(logo, str):
            nombre = str(getattr(logo, 'name', logo))
            if not nombre:
                return None, 0, 0
            from django.core.files.storage import default_storage
            try:
                with default_storage.open(nombre, 'rb') as fh:
                    bin_ = fh.read()
            except Exception:
                with open(nombre, 'rb') as fh:
                    bin_ = fh.read()
        else:
            return None, 0, 0

        if not bin_:
            return None, 0, 0

        ancho_nat, alto_nat = ImageReader(BytesIO(bin_)).getSize()
        escala = min(
            ancho_max / (ancho_nat or 1),
            alto_max / (alto_nat or 1),
        )
        return bin_, ancho_nat * escala, alto_nat * escala
    except Exception:
        return None, 0, 0


def _escape_xml(texto) -> str:
    """Escapa caracteres de control usados por el markup de ``Paragraph``."""
    return (texto or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _datos_identidad(empresa=None, taller=None) -> dict:
    """
    Normaliza los datos visibles de la identidad del reporte. El NOMBRE y el
    RUC pertenecen a la EMPRESA (no al taller); la dirección proviene del
    taller físico (la entidad Empresa no almacena dirección).
    """
    nombre = ''
    ruc = ''
    direccion = ''
    telefono = ''

    if empresa:
        nombre = getattr(empresa, 'nombre_comercial', '') or getattr(empresa, 'razon_social', '') or ''
        ruc = getattr(empresa, 'ruc', '') or ''
        telefono = getattr(empresa, 'telefono', '') or ''

    if taller:
        direccion = getattr(taller, 'direccion', '') or ''
        telefono = telefono or getattr(taller, 'telefono', '') or ''

    if not nombre and taller:
        nombre = getattr(taller, 'nombre', '') or ''

    return {
        'nombre': nombre,
        'ruc': ruc,
        'direccion': direccion,
        'telefono': telefono,
    }


# ---------------------------------------------------------------------------
# 3) STYLES DEL ENCABEZADO Y EL TÍTULO
# ---------------------------------------------------------------------------

def _estilo_nombre_empresa() -> ParagraphStyle:
    """Nombre de la EMPRESA en NEGRILLA y 11-12pt (1-2pt > secundarios)."""
    return ParagraphStyle(
        'NombreEmpresa',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11.5,
        leading=14,
        textColor=GRIS_OSCURO_TEXTO,
    )


def _estilo_dato_secundario() -> ParagraphStyle:
    """Líneas RUC: / Dir: / Telf: en gris #555555 a 9pt."""
    return ParagraphStyle(
        'DatoSecundario',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11,
        textColor=GRIS_IDENTIDAD,
    )


def _estilo_meta_cabecera() -> ParagraphStyle:
    """Metadatos (Generado / Usuario) a 9pt, gris y alineados a la derecha."""
    return ParagraphStyle(
        'MetaCabecera',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11,
        alignment=TA_RIGHT,
        textColor=GRIS_IDENTIDAD,
    )


def _estilo_titulo_reporte() -> ParagraphStyle:
    """Título del reporte: 14pt, NEGRILLA, negro puro."""
    return ParagraphStyle(
        'TituloReporte',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=NEGRO,
    )


def _estilo_metadato_reporte() -> ParagraphStyle:
    """Metadato debajo del título (ej. 'Número total de Clientes: X')."""
    return ParagraphStyle(
        'MetadatoReporte',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=13,
        textColor=GRIS_OSCURO_TEXTO,
    )


def _estilo_informacion() -> ParagraphStyle:
    """Subtítulos del story (p. ej. 'Empresa: …' en reportes genéricos)."""
    return ParagraphStyle(
        'InfoReporte',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11,
        textColor=GRIS_INFORMACION,
    )


# ---------------------------------------------------------------------------
# 4) CABECERA COMPLETA (PÁGINA 1)
# ---------------------------------------------------------------------------

def _construir_identidad(
    datos: dict,
    logo_bin: Optional[bytes],
    logo_ancho: float,
    logo_alto: float,
    ancho_celda: float,
) -> Table:
    """
    Sub-tabla que agrupa el logo y los datos de la empresa (4 líneas: Nombre,
    RUC:, Dir:, Telf:). Si el logo no está disponible, la sub-columna del logo
    se COLAPSA automáticamente de la estructura.
    """
    flujos: List = []
    if datos.get('nombre'):
        flujos.append(Paragraph(_escape_xml(datos['nombre']), _estilo_nombre_empresa()))
    if datos.get('ruc'):
        flujos.append(Paragraph(_escape_xml(f"RUC: {datos['ruc']}"), _estilo_dato_secundario()))
    if datos.get('direccion'):
        flujos.append(Paragraph(_escape_xml(f"Dir: {datos['direccion']}"), _estilo_dato_secundario()))
    if datos.get('telefono'):
        flujos.append(Paragraph(_escape_xml(f"Telf: {datos['telefono']}"), _estilo_dato_secundario()))
    if not flujos:
        flujos.append(Paragraph('', _estilo_dato_secundario()))

    sin_padding = TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ])

    if logo_bin and logo_ancho and logo_alto:
        # Dos sub-columnas: [logo | identidad]
        ancho_logo = logo_ancho + ESPACIO_LOGO_TEXTO
        celda = Table(
            [[Image(BytesIO(logo_bin), width=logo_ancho, height=logo_alto), flujos]],
            colWidths=[ancho_logo, ancho_celda - ancho_logo],
            hAlign='LEFT',
        )
        celda.setStyle(TableStyle(sin_padding.getCommands() + [
            ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
            ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),
        ]))
    else:
        # Sin logo: sub-columna del logo colapsada -> identidad a ancho completo.
        celda = Table([[flujos]], colWidths=[ancho_celda], hAlign='LEFT')
        celda.setStyle(sin_padding)

    return celda


def _construir_metadatos(usuario: str) -> List:
    """
    Columna derecha del encabezado: "Generado: …" y "Usuario: …".
    El ``Spacer`` inicial desplaza los metadatos para dejarlos por debajo del
    área del número de página (esquina superior derecha del margen).
    """
    generado = 'Generado: {}'.format(
        timezone.localtime().strftime('%d/%m/%Y %H:%M')
    )
    usuario_vis = (usuario or '').strip() or '—'
    return [
        Spacer(1, 18),
        Paragraph(_escape_xml(generado), _estilo_meta_cabecera()),
        Paragraph(_escape_xml(f'Usuario: {usuario_vis}'), _estilo_meta_cabecera()),
    ]


def _construir_encabezado_completo(
    ancho_util: float,
    empresa=None,
    taller=None,
    usuario: str = '',
    logo_bin: Optional[bytes] = None,
    logo_ancho: float = 0,
    logo_alto: float = 0,
) -> Table:
    """
    Tabla PRINCIPAL invisible de dos columnas (grilla exacta, sin solapes):
      - Izquierda: sub-tabla de identidad (logo + Nombre/RUC/Dir/Telf).
      - Derecha: metadatos del reporte alineados a la derecha.
    La línea divisoria la dibuja el callback, no esta tabla.
    """
    datos = _datos_identidad(empresa, taller)
    ancho_der = max(ancho_util * 0.40, 150)
    ancho_izq = ancho_util - ancho_der

    izquierda = _construir_identidad(datos, logo_bin, logo_ancho, logo_alto, ancho_izq)
    derecha = _construir_metadatos(usuario)

    tabla = Table(
        [[izquierda, derecha]],
        colWidths=[ancho_izq, ancho_der],
    )
    tabla.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return tabla


def _alto_encabezado_completo(
    ancho_util: float,
    empresa=None,
    taller=None,
    usuario: str = '',
    logo_bin: Optional[bytes] = None,
    logo_ancho: float = 0,
    logo_alto: float = 0,
) -> float:
    """Altura REAL de la Tabla de cabecera (necesaria para el margen exacto)."""
    tabla = _construir_encabezado_completo(
        ancho_util, empresa, taller, usuario, logo_bin, logo_ancho, logo_alto
    )
    _, alto = tabla.wrap(ancho_util, 1000)
    return alto


def _dibujar_cabecera_completa(canvas, doc) -> None:
    """
    Callback ``onPage`` de la ``PageTemplate`` "p1": dibuja la cabecera
    completa y la línea divisoria justo debajo. La cabecera comienza a
    ``MARGEN_SUPERIOR`` puntos de la cima (aire visual arriba) y se ancla POR
    SU BASE en el borde superior del marco (grilla exacta: el contenido nunca
    la invade).
    """
    canvas.saveState()
    frame = doc.pageTemplate.frames[0]
    content_top = frame.y1 + frame.height
    ancho_util = doc.pagesize[0] - doc.leftMargin - doc.rightMargin

    encabezado = _construir_encabezado_completo(
        ancho_util,
        getattr(doc, 'empresa', None),
        getattr(doc, 'taller', None),
        getattr(doc, 'usuario', '') or '',
        getattr(doc, 'logo_bin', None),
        getattr(doc, 'logo_ancho', 0) or 0,
        getattr(doc, 'logo_alto', 0) or 0,
    )
    _, alto = encabezado.wrapOn(canvas, ancho_util, 1000)

    # base = borde inferior de la cabecera. La cabecera va desde
    # (pageheight - MARGEN_SUPERIOR) hacia abajo -> nunca toca el borde físico.
    base = content_top + ESPACIO_LINEA_DIVISORIA + ESPACIO_POST_CABECERA
    encabezado.drawOn(canvas, doc.leftMargin, base)

    # Línea divisoria horizontal muy fina justo debajo de la cabecera.
    y_linea = base - ESPACIO_LINEA_DIVISORIA
    canvas.setStrokeColor(GRIS_LINEA_FINA)
    canvas.setLineWidth(0.6)
    canvas.line(
        doc.leftMargin,
        y_linea,
        doc.pagesize[0] - doc.rightMargin,
        y_linea,
    )
    canvas.restoreState()


# ---------------------------------------------------------------------------
# 5) CABECERA SLIM (PÁGINAS 2+)
# ---------------------------------------------------------------------------

def _dibujar_cabecera_slim(canvas, doc) -> None:
    """
    Callback ``onPage`` de la ``PageTemplate`` "cn": sin logo ni identidad.
    Solo una línea gris fina y, encima, un texto pequeño (8pt):
    "[Nombre empresa] - <Título> (Continuación)". Todo dentro de la banda
    ``ALTURA_CABECERA_SLIM`` bajo el margen superior.
    """
    canvas.saveState()
    nombre = _datos_identidad(getattr(doc, 'empresa', None), getattr(doc, 'taller', None))['nombre']
    titulo = (getattr(doc, 'titulo_reporte', '') or '').strip()

    texto = f' {titulo}' if titulo else ''
    texto = f'{nombre}{texto} (Continuación)' if nombre else f'{titulo} (Continuación)'

    zona = doc.pagesize[1] - MARGEN_SUPERIOR  # tope de la banda bajo el margen

    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(GRIS_IDENTIDAD)
    canvas.drawString(doc.leftMargin, zona - 10, texto)

    canvas.setStrokeColor(GRIS_LINEA_FINA)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, zona - 18, doc.pagesize[0] - doc.rightMargin, zona - 18)
    canvas.restoreState()


# ---------------------------------------------------------------------------
# 6) DOCUMENTO BASE (márgenes perimetrales + dos PageTemplate)
# ---------------------------------------------------------------------------

def _ancho_util() -> float:
    """Ancho útil de la hoja A4 con los márgenes laterales establecidos."""
    return PAGINA[0] - 2 * MARGEN_LATERAL


def _crear_documento(
    buffer: BytesIO,
    titulo: str,
    empresa=None,
    taller=None,
    usuario: str = '',
    logo=None,
) -> Tuple[BaseDocTemplate, float]:
    """
    Crea la ``BaseDocTemplate`` con márgenes perimetrales explícitos
    (top/bottom=40, left/right=36) y dos templates de página:

      * ``p1`` (cabecera completa): margen superior efectivo = MARGEN_SUPERIOR
        + altura REAL de la cabecera + línea + respiración (24pt).
      * ``cn`` (cabecera slim): margen superior efectivo = MARGEN_SUPERIOR +
        ALTURA_CABECERA_SLIM + respiración.

    El marco de las páginas 2+ es más alto, por lo que el contenido aprovecha
    mejor la hoja en las continuaciones. En ``doc`` quedan expuestos los datos
    que los callbacks necesitan (empresa, taller, usuario, logo, título).
    """
    ancho_util = _ancho_util()

    if logo is None:
        logo = _coincidir_logo(empresa, taller)
    logo_bin, logo_ancho, logo_alto = _leer_logo(logo)

    alto_completa = _alto_encabezado_completo(
        ancho_util, empresa, taller, usuario, logo_bin, logo_ancho, logo_alto
    )
    top_efectivo_p1 = (
        MARGEN_SUPERIOR + alto_completa + ESPACIO_LINEA_DIVISORIA + ESPACIO_POST_CABECERA
    )
    top_efectivo_cn = MARGEN_SUPERIOR + ALTURA_CABECERA_SLIM + ESPACIO_POST_CABECERA

    doc = BaseDocTemplate(
        buffer,
        pagesize=PAGINA,
        leftMargin=MARGEN_LATERAL,
        rightMargin=MARGEN_LATERAL,
        topMargin=MARGEN_SUPERIOR,       # margen perimetral declarado
        bottomMargin=MARGEN_INFERIOR,
        title=titulo,
    )

    alto_p1 = PAGINA[1] - MARGEN_INFERIOR - top_efectivo_p1
    alto_cn = PAGINA[1] - MARGEN_INFERIOR - top_efectivo_cn

    frame_p1 = Frame(
        MARGEN_LATERAL, MARGEN_INFERIOR, ancho_util, alto_p1,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id='frame_p1',
    )
    frame_cn = Frame(
        MARGEN_LATERAL, MARGEN_INFERIOR, ancho_util, alto_cn,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id='frame_cn',
    )

    # El primer template añadido es el DEFAULT (página 1); el story inicia con
    # NextPageTemplate('cn') para que el resto de hojas usen la cabecera slim.
    doc.addPageTemplates([
        PageTemplate(id='p1', frames=[frame_p1], onPage=_dibujar_cabecera_completa),
        PageTemplate(id='cn', frames=[frame_cn], onPage=_dibujar_cabecera_slim),
    ])

    doc.titulo_reporte = titulo
    doc.empresa = empresa
    doc.taller = taller
    doc.usuario = usuario or ''
    doc.logo_bin = logo_bin
    doc.logo_ancho = logo_ancho
    doc.logo_alto = logo_alto
    doc.alto_encabezado_completo = alto_completa
    return doc, ancho_util



# ---------------------------------------------------------------------------
# 7) PLANTILLA ESTÁNDAR DE LISTADOS
# ---------------------------------------------------------------------------
#
# Página 1:
#   ┌───────────────────────────────────────────────────────────────┐
#   │ [logo] EMPRESA                           LISTADO DE CLIENTES   │
#   │        RUC 0925033667001                Fecha: 15/10/2026 11:09 │
#   │        Dirección                               Usuario: gromero │
#   │        Telf. 0984024265           Registros: 63 - Página 1 de 2 │
#   └───────────────────────────────────────────────────────────────┘
#   Tabla: cabecera en negrita entre filetes negros, filas separadas por
#   líneas grises finas, sin rellenos (mínimo consumo de tinta).
#
# Páginas 2+: "EMPRESA · TÍTULO (continuación)" a la izquierda, "Página X de Y"
# a la derecha y la cabecera de la tabla repetida (sin filete propio).
#
# Todo en blanco y negro/grises; el logo se convierte a escala de grises.

LST_MARGEN_SUPERIOR = 28
LST_MARGEN_INFERIOR = 28
LST_MARGEN_LATERAL = 28
LST_LOGO_MAX = 48             # ~64 px a 96 ppp
LST_ESPACIO_LOGO = 8
LST_PROPORCION_TITULO = 0.45   # ancho de la columna del título
LST_ESPACIO_FILETE = 6
LST_META_TAMANO = 8          # metadatos bajo el título
LST_META_INTERLINEA = 10
LST_META_ESPACIO = 2         # aire entre el título y los metadatos
LST_ESPACIO_ANTES_TABLA = 10
LST_ALTO_CABECERA_CONT = 13

LST_GRIS_TEXTO = colors.HexColor('#333333')     # datos secundarios (alto contraste)
LST_GRIS_FILA = colors.HexColor('#C4C4C4')      # divisoria entre filas

ORIENTACION_VERTICAL = 'vertical'
ORIENTACION_HORIZONTAL = 'horizontal'
# Con orientación automática, la hoja pasa a horizontal cuando las columnas
# pedidas superan en este factor el ancho útil vertical.
FACTOR_ORIENTACION_AUTO = 1.2

_ALINEACIONES = {'L': TA_LEFT, 'C': TA_CENTER, 'R': TA_RIGHT}

def contexto_reporte(request, empresa_id) -> Tuple[object, object, str]:
    """
    Identidad de la cabecera: ``(empresa, taller, usuario)``. La empresa es la
    de la sesión (aunque el listado salga vacío) y el taller, el activo de la
    sesión (de él sale la dirección); si no hay, el primero de la empresa.
    """
    from apps.authentication.utils import get_taller_desde_request
    from apps.empresas.models import Empresa, Taller

    empresa = Empresa.objects.filter(pk=empresa_id).first() if empresa_id else None
    taller = None
    if empresa:
        taller_id = get_taller_desde_request(request, empresa_id)
        if taller_id:
            taller = Taller.objects.filter(pk=taller_id, empresa=empresa).first()
        if taller is None:
            taller = empresa.talleres.first()

    usuario = ''
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        usuario = getattr(user, 'username', '') or getattr(user, 'email', '') or ''
    return empresa, taller, usuario


def _logo_en_grises(bin_: Optional[bytes]) -> Optional[bytes]:
    """Convierte el logo a escala de grises (conserva la transparencia)."""
    if not bin_:
        return bin_
    try:
        from PIL import Image as PilImage

        with PilImage.open(BytesIO(bin_)) as imagen:
            tiene_alfa = imagen.mode in ('RGBA', 'LA') or (
                imagen.mode == 'P' and 'transparency' in imagen.info
            )
            gris = imagen.convert('LA' if tiene_alfa else 'L')
            salida = BytesIO()
            gris.save(salida, format='PNG')
            return salida.getvalue()
    except Exception:
        return bin_


def _recortar_texto(texto: str, fuente: str, tamano: float, ancho: float) -> str:
    """Recorta ``texto`` con '…' para que quepa en ``ancho`` puntos."""
    if pdfmetrics.stringWidth(texto, fuente, tamano) <= ancho:
        return texto
    while texto and pdfmetrics.stringWidth(texto + '…', fuente, tamano) > ancho:
        texto = texto[:-1]
    return texto + '…'


def estilo_celda_listado() -> ParagraphStyle:
    """Estilo base de las celdas de datos (8pt, negro)."""
    return ParagraphStyle(
        'CeldaListado',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=9.6,
        textColor=NEGRO,
    )


def _estilo_cabecera_columna(alineacion: int) -> ParagraphStyle:
    return ParagraphStyle(
        f'CabeceraListado{alineacion}',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9,
        alignment=alineacion,
        textColor=NEGRO,
    )


def _normalizar_columnas(columnas) -> List[Tuple[str, float, str]]:
    """Columnas como ``(título, ancho_pulgadas[, 'L'|'C'|'R'])``."""
    normalizadas = []
    for columna in columnas:
        titulo, ancho = columna[0], columna[1]
        alineacion = columna[2] if len(columna) > 2 else 'L'
        normalizadas.append((titulo, float(ancho), alineacion))
    return normalizadas


def _resolver_pagina(anchos_pulgadas: List[float], orientacion: Optional[str]):
    """Tamaño de hoja según la orientación pedida o, si es ``None``, automática."""
    if orientacion is None:
        util_vertical = PAGINA[0] - 2 * LST_MARGEN_LATERAL
        pedido = sum(anchos_pulgadas) * inch
        orientacion = (
            ORIENTACION_HORIZONTAL
            if pedido > util_vertical * FACTOR_ORIENTACION_AUTO
            else ORIENTACION_VERTICAL
        )
    if orientacion == ORIENTACION_HORIZONTAL:
        return (PAGINA[1], PAGINA[0])
    return PAGINA


class _CabeceraListado:
    """Dibuja la cabecera estándar (página 1 y continuaciones)."""

    def __init__(self, titulo, empresa, taller, usuario, total_registros, logo):
        self.titulo = (titulo or '').strip()
        self.identidad = _datos_identidad(empresa, taller)
        self.usuario = (usuario or '').strip() or '—'
        self.total_registros = total_registros
        self.generado = timezone.localtime()

        if logo is None:
            logo = _coincidir_logo(empresa, taller)
        bin_, self.logo_ancho, self.logo_alto = _leer_logo(logo, LST_LOGO_MAX, LST_LOGO_MAX)
        self.logo_bin = _logo_en_grises(bin_)

    # -- bloques -------------------------------------------------------------

    def _identidad_y_titulo(self, ancho: float) -> Table:
        nombre = ParagraphStyle(
            'LstEmpresa', fontName='Helvetica-Bold', fontSize=11, leading=13, textColor=NEGRO,
        )
        secundario = ParagraphStyle(
            'LstEmpresaDato', fontName='Helvetica', fontSize=8, leading=10, textColor=LST_GRIS_TEXTO,
        )
        datos = self.identidad
        flujos: List = []
        if datos['nombre']:
            flujos.append(Paragraph(_escape_xml(datos['nombre']), nombre))
        if datos['ruc']:
            flujos.append(Paragraph(_escape_xml(f"RUC {datos['ruc']}"), secundario))
        if datos['direccion']:
            flujos.append(Paragraph(_escape_xml(datos['direccion']), secundario))
        if datos['telefono']:
            flujos.append(Paragraph(_escape_xml(f"Telf. {datos['telefono']}"), secundario))
        if not flujos:
            flujos.append(Paragraph('', secundario))

        ancho_der = ancho * LST_PROPORCION_TITULO
        ancho_izq = ancho - ancho_der
        sin_padding = [
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]

        if self.logo_bin and self.logo_ancho and self.logo_alto:
            ancho_logo = self.logo_ancho + LST_ESPACIO_LOGO
            izquierda = Table(
                [[Image(BytesIO(self.logo_bin), width=self.logo_ancho, height=self.logo_alto), flujos]],
                colWidths=[ancho_logo, ancho_izq - ancho_logo],
            )
        else:
            izquierda = Table([[flujos]], colWidths=[ancho_izq])
        izquierda.setStyle(TableStyle(sin_padding))

        # Columna derecha: título + hueco reservado para los metadatos, que se
        # dibujan después en el canvas (el número de página se conoce al final).
        derecha = [self._parrafo_titulo(), Spacer(1, self._alto_metadatos())]
        tabla = Table([[izquierda, derecha]], colWidths=[ancho_izq, ancho_der])
        tabla.setStyle(TableStyle(sin_padding + [('VALIGN', (1, 0), (1, 0), 'TOP')]))
        return tabla

    def _parrafo_titulo(self) -> Paragraph:
        estilo = ParagraphStyle(
            'LstTitulo', fontName='Helvetica-Bold', fontSize=13, leading=16,
            alignment=TA_RIGHT, textColor=NEGRO,
        )
        return Paragraph(_escape_xml(self.titulo).upper(), estilo)

    def _lineas_metadatos(self) -> List[str]:
        """Metadatos bajo el título. La última línea lleva el paginado y es una
        plantilla para ``NumberedCanvas`` (rellena "Página %s de %s")."""
        total = '—' if self.total_registros is None else f'{self.total_registros:,}'.replace(',', '.')
        return [
            f"Fecha: {self.generado.strftime('%d/%m/%Y %H:%M')}",
            f'Usuario: {self.usuario}',
            f'Registros: {total}  -  Página %s de %s',
        ]

    def _alto_metadatos(self) -> float:
        return LST_META_ESPACIO + len(self._lineas_metadatos()) * LST_META_INTERLINEA

    # -- alturas -------------------------------------------------------------

    def alto_primera(self, ancho: float) -> float:
        _, alto_ident = self._identidad_y_titulo(ancho).wrap(ancho, 1000)
        return alto_ident + LST_ESPACIO_FILETE + LST_ESPACIO_ANTES_TABLA

    # -- dibujo --------------------------------------------------------------

    def dibujar_primera(self, canvas, doc) -> None:
        canvas.saveState()
        x0 = doc.leftMargin
        ancho = doc.pagesize[0] - doc.leftMargin - doc.rightMargin
        x1 = x0 + ancho
        y = doc.pagesize[1] - LST_MARGEN_SUPERIOR

        # Identidad + título
        bloque = self._identidad_y_titulo(ancho)
        _, alto = bloque.wrapOn(canvas, ancho, 1000)
        y_tope = y
        y -= alto
        bloque.drawOn(canvas, x0, y)

        # Metadatos discretos, alineados a la derecha bajo el título
        _, alto_titulo = self._parrafo_titulo().wrap(ancho * LST_PROPORCION_TITULO, 1000)
        base = y_tope - alto_titulo - LST_META_ESPACIO - LST_META_TAMANO
        canvas.setFont('Helvetica', LST_META_TAMANO)
        canvas.setFillColor(LST_GRIS_TEXTO)
        for linea in self._lineas_metadatos()[:-1]:
            canvas.drawRightString(x1, base, linea)
            base -= LST_META_INTERLINEA
        canvas._gm_paginado = (
            x1, base, LST_META_TAMANO, self._lineas_metadatos()[-1], 'R', LST_GRIS_TEXTO,
        )

        canvas.restoreState()

    def dibujar_continuacion(self, canvas, doc) -> None:
        canvas.saveState()
        x0 = doc.leftMargin
        x1 = doc.pagesize[0] - doc.rightMargin
        y = doc.pagesize[1] - LST_MARGEN_SUPERIOR - 8

        texto = ' · '.join(p for p in (self.identidad['nombre'], self.titulo.upper()) if p)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(LST_GRIS_TEXTO)
        canvas.drawString(
            x0, y,
            _recortar_texto(f'{texto} (continuación)', 'Helvetica', 8, (x1 - x0) * 0.75),
        )
        canvas._gm_paginado = (x1, y, 8, 'Página %s de %s', 'R', LST_GRIS_TEXTO)
        canvas.restoreState()


def _construir_tabla_listado(columnas, filas, ancho_util: float) -> Table:
    """
    Tabla compacta y sin rellenos: cabecera en negrita entre filetes negros,
    filas separadas por líneas grises finas y alineación por columna (las
    cifras a la derecha). Los anchos se escalan para ocupar todo el ancho útil.
    """
    anchos = [ancho for _, ancho, _ in columnas]
    factor = ancho_util / ((sum(anchos) * inch) or 1)
    col_widths = [a * inch * factor for a in anchos]

    base = estilo_celda_listado()
    estilos_col = [
        ParagraphStyle(f'CeldaListado{i}', parent=base, alignment=_ALINEACIONES.get(al, TA_LEFT))
        for i, (_, _, al) in enumerate(columnas)
    ]

    cabecera = [
        Paragraph(_escape_xml(titulo).upper(), _estilo_cabecera_columna(_ALINEACIONES.get(al, TA_LEFT)))
        for titulo, _, al in columnas
    ]
    contenido = [cabecera]

    for fila in filas:
        celdas = []
        for indice, celda in enumerate(fila):
            estilo = estilos_col[indice] if indice < len(estilos_col) else base
            if isinstance(celda, Paragraph):
                # Mismo cuerpo de letra que ``base``: solo cambia la alineación.
                celda.style = estilo
            else:
                celda = Paragraph(_escape_xml('' if celda is None else str(celda)), estilo)
            celdas.append(celda)
        contenido.append(celdas)

    comandos = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (0, -1), 2),
        ('RIGHTPADDING', (-1, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ('LINEABOVE', (0, 0), (-1, 0), 1.0, NEGRO),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, NEGRO),
    ]

    if not filas:
        vacio = ParagraphStyle(
            'ListadoVacio', parent=base, fontName='Helvetica-Oblique', textColor=LST_GRIS_TEXTO,
        )
        contenido.append(
            [Paragraph('No se encontraron registros.', vacio)]
            + [''] * (len(columnas) - 1)
        )
        comandos.append(('SPAN', (0, 1), (-1, 1)))
        comandos.append(('TOPPADDING', (0, 1), (-1, 1), 8))
        comandos.append(('BOTTOMPADDING', (0, 1), (-1, 1), 8))
    else:
        comandos.append(('LINEBELOW', (0, 1), (-1, -2), 0.3, LST_GRIS_FILA))
    comandos.append(('LINEBELOW', (0, -1), (-1, -1), 0.8, NEGRO))

    tabla = Table(contenido, colWidths=col_widths, repeatRows=1)
    tabla.setStyle(TableStyle(comandos))
    return tabla


def generar_listado_pdf(
    buffer: BytesIO,
    *,
    titulo: str,
    columnas,
    filas,
    empresa=None,
    taller=None,
    usuario: str = '',
    total_registros: Optional[int] = None,
    logo=None,
    orientacion: Optional[str] = None,
) -> None:
    """
    Plantilla única de los listados. Escribe en ``buffer`` un PDF con la
    cabecera estándar (logo, título, fecha y hora, usuario, registros y
    página) y la tabla de datos.

    * ``columnas``: ``[(título, ancho_pulgadas[, 'L'|'C'|'R']), ...]``; los
      anchos son relativos (se escalan al ancho útil de la hoja).
    * ``filas``: lista de filas; cada celda es un ``Paragraph`` (construido con
      ``estilo_celda_listado()``) o un valor que se convierte a texto.
    * ``orientacion``: ``'vertical'``, ``'horizontal'`` o ``None`` (automática
      según el ancho total de las columnas).
    """
    columnas = _normalizar_columnas(columnas)
    filas = list(filas or [])
    if total_registros is None:
        total_registros = len(filas)

    pagina = _resolver_pagina([ancho for _, ancho, _ in columnas], orientacion)
    ancho_util = pagina[0] - 2 * LST_MARGEN_LATERAL

    cabecera = _CabeceraListado(titulo, empresa, taller, usuario, total_registros, logo)
    top_p1 = LST_MARGEN_SUPERIOR + cabecera.alto_primera(ancho_util)
    top_cn = LST_MARGEN_SUPERIOR + LST_ALTO_CABECERA_CONT + LST_ESPACIO_ANTES_TABLA

    doc = BaseDocTemplate(
        buffer,
        pagesize=pagina,
        leftMargin=LST_MARGEN_LATERAL,
        rightMargin=LST_MARGEN_LATERAL,
        topMargin=LST_MARGEN_SUPERIOR,
        bottomMargin=LST_MARGEN_INFERIOR,
        title=titulo,
        author=cabecera.identidad['nombre'] or 'GonMotor',
    )
    marco = dict(leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate(
            id='p1',
            frames=[Frame(
                LST_MARGEN_LATERAL, LST_MARGEN_INFERIOR, ancho_util,
                pagina[1] - LST_MARGEN_INFERIOR - top_p1, id='frame_p1', **marco,
            )],
            onPage=cabecera.dibujar_primera,
        ),
        PageTemplate(
            id='cn',
            frames=[Frame(
                LST_MARGEN_LATERAL, LST_MARGEN_INFERIOR, ancho_util,
                pagina[1] - LST_MARGEN_INFERIOR - top_cn, id='frame_cn', **marco,
            )],
            onPage=cabecera.dibujar_continuacion,
        ),
    ])

    story = [NextPageTemplate('cn'), _construir_tabla_listado(columnas, filas, ancho_util)]
    doc.build(story, canvasmaker=NumberedCanvas)


# ---------------------------------------------------------------------------
# 8) LISTADO DE CLIENTES
# ---------------------------------------------------------------------------

COLUMNAS_CLIENTES = [
    ('Identificación', 1.05),
    ('Cliente', 2.0),
    ('Contacto', 1.9),
    ('Vehículos', 1.75),
    ('Saldo', 0.7, 'R'),
]


def _cliente_a_datos(cliente) -> dict:
    """Normaliza un cliente (ORM o dict) al esquema plano de la tabla."""
    if hasattr(cliente, 'nombre'):
        rels = getattr(cliente, 'propietarios_actuales', None)
        if rels is None:
            rels = cliente.vehiculos_asociados.filter(
                es_actual=True
            ).select_related('vehiculo')
        vehiculos = []
        for relacion in rels:
            v = relacion.vehiculo
            vehiculos.append({'placa': v.placa or '', 'detalle': f'{v.marca} {v.modelo}'.strip()})

        return {
            'identificacion': (cliente.identificacion or '').strip(),
            'nombre': cliente.nombre or '',
            'telefono': cliente.telefono or '',
            'email': cliente.email or '',
            'vehiculos': vehiculos,
            'saldo': getattr(cliente, 'saldo', 0),
        }

    vehiculos = cliente.get('vehiculos') or []
    norm_vehiculos = []
    for v in vehiculos:
        if hasattr(v, 'get'):
            norm_vehiculos.append({'placa': v.get('placa', ''), 'detalle': v.get('detalle', '')})
        else:
            norm_vehiculos.append({'placa': getattr(v, 'placa', ''), 'detalle': getattr(v, 'detalle', '')})

    return {
        'identificacion': (cliente.get('identificacion', '') or '').strip(),
        'nombre': cliente.get('nombre', ''),
        'telefono': cliente.get('telefono', ''),
        'email': cliente.get('email', ''),
        'vehiculos': norm_vehiculos,
        'saldo': cliente.get('saldo', 0),
    }


def _fila_cliente(datos: dict, estilo: ParagraphStyle) -> List[Paragraph]:
    """Celdas de un cliente; el saldo pendiente (> $0.00) va en negrita."""
    contacto = [_escape_xml(datos[c]) for c in ('telefono', 'email') if datos.get(c)]
    texto_contacto = '<br/>'.join(contacto) if contacto else '—'

    lineas_vehiculos = []
    for v in datos.get('vehiculos') or []:
        partes = [_escape_xml(v.get(c, '') or '') for c in ('placa', 'detalle')]
        partes = ' - '.join(p for p in partes if p)
        if partes:
            lineas_vehiculos.append(f'• {partes}')
    texto_vehiculos = '<br/>'.join(lineas_vehiculos) if lineas_vehiculos else '—'

    saldo, es_positivo = _formatear_saldo(datos.get('saldo'))
    texto_saldo = f'<b>{saldo}</b>' if es_positivo else saldo

    return [
        Paragraph(_escape_xml(datos.get('identificacion', '') or ''), estilo),
        Paragraph(_escape_xml(datos.get('nombre', '') or ''), estilo),
        Paragraph(texto_contacto, estilo),
        Paragraph(texto_vehiculos, estilo),
        Paragraph(texto_saldo, estilo),
    ]


def _formatear_saldo(valor) -> Tuple[str, bool]:
    """
    Devuelve (texto_formateado, es_positivo). El texto se pinta en negrita
    únicamente cuando el saldo pendiente es mayor a $0.00.
    """
    try:
        saldo = Decimal(str(valor if valor is not None else 0))
    except (TypeError, ValueError, ArithmeticError):
        saldo = Decimal('0.00')
    return f'${saldo:,.2f}', saldo > 0


def exportar_clientes_pdf(
    buffer: BytesIO,
    data_clientes: Iterable,
    total_registros: Optional[int] = None,
    *,
    titulo: str = 'Listado de Clientes',
    empresa=None,
    taller=None,
    usuario: str = '',
    logo=None,
) -> None:
    """
    Exporta clientes con la plantilla estándar de listados. ``data_clientes``
    acepta un queryset de ``Cliente`` o una lista de dicts con las claves de
    ``_cliente_a_datos``.
    """
    estilo = estilo_celda_listado()
    filas = [_fila_cliente(_cliente_a_datos(c), estilo) for c in (data_clientes or [])]
    generar_listado_pdf(
        buffer,
        titulo=titulo,
        columnas=COLUMNAS_CLIENTES,
        filas=filas,
        empresa=empresa,
        taller=taller,
        usuario=usuario,
        total_registros=total_registros,
        logo=logo,
    )


# ---------------------------------------------------------------------------
# 9) API GENÉRICA PARA LAS PANTALLAS DE LISTADO
# ---------------------------------------------------------------------------

class PdfExportConfig:
    """
    Configuración de un listado PDF.

    * ``headers``: ``[(título, ancho_pulgadas[, 'L'|'C'|'R']), ...]``.
    * ``row_builder(instancia, estilo)``: devuelve las celdas de una fila;
      ``estilo`` es el de ``estilo_celda_listado()``.
    * ``total_registros``: si no se indica, se cuentan las filas.
    * ``orientacion``: ``'vertical'``, ``'horizontal'`` o ``None`` (auto).
    """

    def __init__(
        self,
        title: str,
        filename: str,
        headers: List[Tuple],
        row_builder=None,
        empresa=None,
        taller=None,
        usuario: Optional[str] = None,
        total_registros: Optional[int] = None,
        orientacion: Optional[str] = None,
        logo=None,
    ):
        self.title = title
        self.filename = filename
        self.headers = headers
        self.row_builder = row_builder
        self.empresa = empresa
        self.taller = taller
        self.usuario = usuario
        self.total_registros = total_registros
        self.orientacion = orientacion
        self.logo = logo


class PdfExportService:
    """Genera el listado de un ``PdfExportConfig`` y lo devuelve como ``FileResponse``."""

    def __init__(self, config: PdfExportConfig, queryset):
        self.config = config
        self.queryset = queryset

    def generate_response(self) -> FileResponse:
        estilo = estilo_celda_listado()
        if self.config.row_builder:
            filas = [self.config.row_builder(instancia, estilo) for instancia in self.queryset]
        else:
            filas = [[str(instancia)] for instancia in self.queryset]

        buffer = BytesIO()
        generar_listado_pdf(
            buffer,
            titulo=self.config.title,
            columnas=self.config.headers,
            filas=filas,
            empresa=self.config.empresa,
            taller=self.config.taller,
            usuario=self.config.usuario or '',
            total_registros=self.config.total_registros,
            logo=self.config.logo,
            orientacion=self.config.orientacion,
        )
        buffer.seek(0)

        return FileResponse(
            buffer,
            content_type='application/pdf',
            filename=self.config.filename,
            as_attachment=False,
        )


def _ajustar_anchos(
    anchos: List[float],
    ancho_disponible: float,
) -> List[float]:
    """
    Escala proporcionalmente los anchos (en pulgadas) si su total supera el
    ancho útil (en puntos). Lo usa la cotización.
    """
    total_pt = sum(anchos) * inch
    if total_pt <= ancho_disponible:
        return anchos
    factor = ancho_disponible / total_pt
    return [a * factor for a in anchos]

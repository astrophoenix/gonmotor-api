"""
Exportación de PDFs con ReportLab/Platypus, filosofía *ink-friendly* (ahorro
de tinta) y un diseño de grilla exacta que elimina cualquier solapamiento.

PÁGINA Y MÁRGENES PERIMETRALES
------------------------------
Hoja A4 con márgenes limpios y explícitos: topMargin=40, bottomMargin=40,
leftMargin=36, rightMargin=36 (puntos). La cabecera ya NO toca el borde físico
superior: siempre queda un aire visual de ``MARGEN_SUPERIOR`` puntos antes de
dibujar el primer elemento, en TODAS las hojas.

DISEÑO DE CABECERA ASIMÉTRICA (multi-página)
---------------------------------------------
  * ``Número de página``: la etiqueta "Página X de Y" (``NumberedCanvas``) se
    dibuja en la esquina superior derecha de absolutamente todas las hojas,
    dentro de la banda del margen superior establecido y alineada al margen
    derecho (nunca invade el contenido).

  * Página 1 (``p1`` / cabecera completa): identidad completa mediante una
    **Tabla principal invisible de dos columnas**:

      - Columna izquierda: sub-tabla de dos columnas:
          + Sub-columna izquierda: ``Image`` del logo (ancho máx. 75pt, alto
            proporcional). Si el logo es ``None`` o falla su lectura, esta
            sub-columna se colapsa automáticamente de la estructura.
          + Sub-columna derecha (datos de la EMPRESA, no del taller):
              · L1: Nombre de la empresa en NEGRILLA (11-12pt).
              · L2: "RUC: <ruc>" (9pt, gris #555555).
              · L3: "Dir: <dirección>" (9pt, gris #555555).
              · L4: "Telf: <teléfono>" (9pt, gris #555555).
      - Columna derecha (metadatos alineados a la derecha): "Generado: …" y
        "Usuario: …" a 9pt en gris #555555.

    Debajo de la tabla se traza una línea divisoria horizontal gris muy fina
    (gris asulado) con espaciado equilibrado (10pt sobre y 10pt bajo la línea),
    de modo que queda centrada entre la cabecera y el título (grilla exacta).

  * Páginas 2+ (``cn`` / cabecera slim): NO se repite logo ni identidad. Solo
    una línea gris fina y, encima, un texto pequeño (8pt):
    "[Nombre empresa] - <Título del reporte> (Continuación)".

GRÁFICA DEL DOCUMENTO (PÁGINA 1)
--------------------------------
- MARGEN_SUPERIOR (40)            <- banda libre (número de página aquí)
  - Cabecera completa (altura real)
  - Línea divisoria fina (2pt), espaciada 10/10 alrededor (equilibrada)
  - Story: título 14pt NEGRILLA negro puro + "Número total de Clientes: X"
  - Tabla de datos (repeatRows=1) con: Identificación / Cliente / Contacto /
    Vehículos / Saldo (las mismas columnas del Excel más el Saldo).

TABLA DE DATOS INK-FRIENDLY
---------------------------
  * Cabecera de campos con fondo y líneas en el MISMO gris asulado (#D7E0E9),
    claro y discreto (la cabecera NO se ve resaltada), con texto negro en
    NEGRILLA para distinguir los campos de los datos.
  * Alineación idéntica cabecera/datos por columna (Identificación a la
    izquierda, Cliente a la izquierda, Saldo a la derecha), aplicada a nivel de
    ``ParagraphStyle`` de cada celda (el ``TableStyle ALIGN`` no reescribe
    Paragraphs).
  * Registros en texto regular (sin negrillas); ÚNICA excepción: el saldo
    pendiente > $0.00 en NEGRILLA (alerta visual).
  * Vehículos múltiples separados con viñeta y salto de línea.
  * Líneas de tabla finas en el mismo gris asulado (#D7E0E9).

API PÚBLICA (compatible con las pantallas existentes)
-------------------------------------------------------
  * ``exportar_clientes_pdf(...)``: reporte de referencia (Clientes).
  * ``PdfExportConfig`` + ``PdfExportService``: servicio genérico usado por
    Vehículos, Recepciones/Órdenes, Inventario y Configuración. El servicio
    AUTOESCALA las columnas si la configuración supera el ancho útil de la
    hoja, garantizando que ninguna tabla se salga de la página. Ambos
    devuelven un ``FileResponse`` listo para Django REST Framework.
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


def _leer_logo(logo) -> Tuple[Optional[bytes], float, float]:
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
            ANCHO_LOGO_MAX / (ancho_nat or 1),
            ALTO_LOGO_MAX / (alto_nat or 1),
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
# 7) EXPORTACIÓN DE CLIENTES (flujo Platypus de referencia)
# ---------------------------------------------------------------------------

# (encabezado, ancho en pulgadas) — total 7.25in = 522pt < 523.27pt (A4 útil).
# Coincide con el Excel de clientes: Identificación / Cliente / Contacto /
# Vehículos / Saldo (mismas columnas y anchos relativos).
COLUMNAS_CLIENTES: List[Tuple[str, float]] = [
    ('Identificación', 1.20),
    ('Cliente', 2.05),
    ('Contacto', 1.75),
    ('Vehículos', 1.40),
    ('Saldo', 0.85),
]

# Alineación por columna, IDÉNTICA para cabeceras y datos.
ALINEACION_COLUMNAS: List[int] = [TA_LEFT, TA_LEFT, TA_LEFT, TA_LEFT, TA_RIGHT]


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

    # Entrada tipo dict
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


def _celda_cliente(datos: dict, celdas_style: List[ParagraphStyle]) -> List[Paragraph]:
    """
    Celdas de la fila del cliente. Ink-friendly: texto regular (sin negrillas
    para clientes, email ni vehículos); el saldo > $0.00 se resalta en negrita
    como alerta visual discreta. Cabecera y datos comparten alineación.
    """
    contacto = []
    if datos.get('telefono'):
        contacto.append(_escape_xml(datos['telefono']))
    if datos.get('email'):
        contacto.append(_escape_xml(datos['email']))
    texto_contacto = '<br/>'.join(contacto) if contacto else 'Sin contacto'

    vehiculos = datos.get('vehiculos') or []
    if vehiculos:
        lineas_vehiculos = []
        for v in vehiculos:
            placa = _escape_xml(v.get('placa', '') or '')
            detalle = _escape_xml(v.get('detalle', '') or '')
            if placa and detalle:
                lineas_vehiculos.append(f'• {placa} - {detalle}')
            elif placa:
                lineas_vehiculos.append(f'• {placa}')
            elif detalle:
                lineas_vehiculos.append(f'• {detalle}')
        texto_vehiculos = '<br/>'.join(lineas_vehiculos)
    else:
        texto_vehiculos = '—'

    saldo, es_positivo = _formatear_saldo(datos.get('saldo'))
    texto_saldo = f'<b>{saldo}</b>' if es_positivo else saldo

    return [
        Paragraph(_escape_xml(datos.get('identificacion', '') or ''), celdas_style[0]),
        Paragraph(_escape_xml(datos.get('nombre', '') or ''), celdas_style[1]),
        Paragraph(texto_contacto, celdas_style[2]),
        Paragraph(texto_vehiculos, celdas_style[3]),
        Paragraph(texto_saldo, celdas_style[4]),
    ]


def _construir_tabla_clientes(data_clientes: Iterable) -> Table:
    """Construye la ``Table`` de clientes con celdas ``Paragraph``."""
    styles = getSampleStyleSheet()
    cabecera_base = dict(
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=10,
        textColor=NEGRO,
    )
    # Un estilo POR COLUMNA: así cabeceras y datos quedan alineados exactamente
    # igual (el TableStyle ALIGN no reescribe la alineación de Paragraphs).
    celdas_style = [
        ParagraphStyle(
            f'CeldaCliente{i}',
            parent=styles['Normal'],
            alignment=alineacion,
            fontName='Helvetica',
            fontSize=8.5,
            leading=10,
            textColor=GRIS_OSCURO_TEXTO,
        )
        for i, alineacion in enumerate(ALINEACION_COLUMNAS)
    ]

    cabeceras = [
        Paragraph(h, ParagraphStyle(
            f'Cabecera{i}',
            alignment=ALINEACION_COLUMNAS[i],
            **cabecera_base,
        ))
        for i, (h, _) in enumerate(COLUMNAS_CLIENTES)
    ]
    filas = [cabeceras]

    filas_datos = [_cliente_a_datos(c) for c in (data_clientes or [])]
    if not filas_datos:
        fila_vacia = [Paragraph('', s) for s in celdas_style]
        fila_vacia[0] = Paragraph('No se encontraron registros.', celdas_style[0])
        filas.append(fila_vacia)
    else:
        for datos in filas_datos:
            filas.append(_celda_cliente(datos, celdas_style))

    tabla = Table(
        filas,
        colWidths=[ancho * inch for _, ancho in COLUMNAS_CLIENTES],
        repeatRows=1,
    )
    tabla.setStyle(TableStyle(ESTILO_TABLA_INK_FRIENDLY + [
        # Alineación de celdas por columna (a nivel de tabla, refuerzo).
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (4, 0), (4, -1), 'RIGHT'),
    ]))
    return tabla


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
    Exporta clientes a un PDF dentro de ``buffer`` (flujo Platypus de
    referencia). ``data_clientes`` acepta un queryset de ``Cliente`` o una
    lista de dicts con las claves usadas en ``_cliente_a_datos``.

    ``logo`` recibe la ubicación del logo (ImageField/ruta); si no se indica o
    su archivo no está disponible, la sub-columna del logo se colapsa y el
    nombre de la empresa toma el lugar sin romper el PDF.

    El story de la página 1 es: título (14pt negrita), metadato
    "Número total de Clientes: X" y la tabla de datos con cabecera repetible.
    """
    doc, _ = _crear_documento(buffer, titulo, empresa, taller, usuario, logo)

    if total_registros is None:
        total_registros = len(list(data_clientes))

    story: List = [
        NextPageTemplate('cn'),
        Paragraph(_escape_xml(titulo).upper(), _estilo_titulo_reporte()),
        Paragraph(
            f'Número total de Clientes: {total_registros}',
            _estilo_metadato_reporte(),
        ),
        Spacer(1, 10),
        _construir_tabla_clientes(data_clientes),
    ]

    doc.build(story, canvasmaker=NumberedCanvas)


# ---------------------------------------------------------------------------
# 8) CAPA DE COMPATIBILIDAD: API GENÉRICA PARA LAS PANTALLAS EXISTENTES
# ---------------------------------------------------------------------------

def _ajustar_anchos(
    anchos: List[float],
    ancho_disponible: float,
) -> List[float]:
    """
    AUTOESCALA proporcionalmente los anchos (en pulgadas) de columnas si su
    total supera el ancho útil de la hoja (en puntos). Garantiza que ninguna
    tabla se salga de la página sin importar la configuración histórica.
    """
    total_pt = sum(anchos) * inch
    if total_pt <= ancho_disponible:
        return anchos
    factor = ancho_disponible / total_pt
    return [a * factor for a in anchos]


class PdfExportConfig:
    """Configuración de un reporte genérico (mantiene su API histórica)."""

    def __init__(
        self,
        title: str,
        filename: str,
        headers: List[Tuple[str, float]],
        subtitle_builder=None,
        row_builder=None,
        empresa=None,
        taller=None,
        usuario: Optional[str] = None,
        metadata: Optional[str] = None,
    ):
        self.title = title
        self.filename = filename
        self.headers = headers
        self.subtitle_builder = subtitle_builder
        self.row_builder = row_builder
        self.empresa = empresa
        self.taller = taller
        self.usuario = usuario
        self.metadata = metadata


class PdfExportService:
    """
    Servicio genérico que construye una tabla Platypus a partir de un
    ``PdfExportConfig`` y se apoya en la cabecera asimétrica (completa/slim) +
    ``NumberedCanvas`` para estandarizar todas las pantallas. Devuelve un
    ``FileResponse`` listo para Django REST Framework.
    """

    def __init__(self, config: PdfExportConfig, queryset):
        self.config = config
        self.queryset = queryset

    def _build_subtitle(self) -> Optional[Paragraph]:
        if not self.config.subtitle_builder:
            return None
        texto = self.config.subtitle_builder(self.queryset)
        if not texto:
            return None
        return Paragraph(texto, _estilo_informacion())

    def _build_metadata(self) -> Optional[Paragraph]:
        """Metadato del reporte (ej. 'Número total de Vehículos: X')."""
        if not self.config.metadata:
            return None
        return Paragraph(_escape_xml(self.config.metadata), _estilo_metadato_reporte())

    def _build_table(self, ancho_util: float) -> Table:
        styles = getSampleStyleSheet()
        celda_style = ParagraphStyle(
            'CeldaGenerica',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9,
            leading=11,
            textColor=GRIS_OSCURO_TEXTO,
        )
        encabezado_style = ParagraphStyle(
            'CabeceraGenerica',
            parent=celda_style,
            fontName='Helvetica-Bold',
            textColor=NEGRO,
        )

        filas = [[Paragraph(h[0], encabezado_style) for h in self.config.headers]]

        if self.config.row_builder:
            for instancia in self.queryset:
                filas.append(self.config.row_builder(instancia, celda_style))
        else:
            filas.append([Paragraph(str(instancia), celda_style)])

        anchos_orig = [ancho for _, ancho in self.config.headers]
        anchos = _ajustar_anchos(anchos_orig, ancho_util)  # nunca se sale de la hoja
        col_widths = [a * inch for a in anchos]

        tabla = Table(filas, colWidths=col_widths, repeatRows=1)
        tabla.setStyle(TableStyle(ESTILO_TABLA_INK_FRIENDLY))
        return tabla

    def generate_response(self) -> FileResponse:
        buffer = BytesIO()
        doc, ancho_util = _crear_documento(
            buffer,
            self.config.title,
            self.config.empresa,
            self.config.taller,
            self.config.usuario or '',
        )

        story: List = [
            NextPageTemplate('cn'),
            Paragraph(
                _escape_xml(self.config.title).upper(),
                _estilo_titulo_reporte(),
            ),
        ]
        subtitulo = self._build_metadata() or self._build_subtitle()
        if subtitulo:
            story.append(subtitulo)
        story.append(Spacer(1, 10))
        story.append(self._build_table(ancho_util))

        doc.build(story, canvasmaker=NumberedCanvas)
        buffer.seek(0)

        return FileResponse(
            buffer,
            content_type='application/pdf',
            filename=self.config.filename,
            as_attachment=False,
        )
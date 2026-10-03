"""
PDF de UNA cotización: documento sobrio, en una sola hoja A4 (caso habitual).

Usa identidad, márgenes y paginado compartidos de
``apps.core.utils.pdf_export`` (cabecera p1/cn + ``NumberedCanvas``) y su
paleta ink-friendly: fondo blanco, cabeceras de tabla en gris muy claro,
texto oscuro y divisorias horizontales de 0.5pt. El color de la marca se usa
únicamente como acento tipográfico (la palabra "COTIZACIÓN" y el monto del
TOTAL); no hay bloques de color sólido.

Estructura del story (vertical, compacta):

    título + estado            Table invisible de 2 columnas
    metadatos                  línea de emisión / validez / taller / asesor
    cliente y vehículo         Table invisible de 3 pares etiqueta/valor
    conceptos                  tablas con cabecera gris y filetes de 0.5pt
    totales                    Table compacta alineada a la derecha
    observaciones / aceptación secciones de texto a 8.5pt

Antes de armar las tablas, ``_consolidar`` agrupa ítems idénticos y suma sus
cantidades: menos filas, menos alto y el documento entra en una sola página.
"""

from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    KeepTogether,
    NextPageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from apps.core.utils.pdf_export import (
    ESTILO_TABLA_INK_FRIENDLY,
    GRIS_IDENTIDAD,
    GRIS_INFORMACION,
    GRIS_LINEA_FINA,
    GRIS_OSCURO_TEXTO,
    NEGRO,
    NumberedCanvas,
    _ajustar_anchos,
    _crear_documento,
    _escape_xml,
)

# Acento de marca: solo texto, nunca fondos.
AZUL_MARINO = colors.HexColor('#002B49')

# Color del número de cotización dentro del título (gris oscuro, no marca).
COLOR_NUMERO = GRIS_OSCURO_TEXTO.hexval().replace('0x', '#')  # '#111827'

# Respiraciones: 10pt entre secciones, 4pt dentro de un bloque.
ESPACIO = 10
ESPACIO_COMPACTO = 4

# ---------------------------------------------------------------------------
# ESTILOS (heredados de Normal; sin marcado inline salvo el acento de color)
# ---------------------------------------------------------------------------

_NORMAL = getSampleStyleSheet()['Normal']

COTIZACION_TITULO = ParagraphStyle(
    'CotizacionTitulo',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=14,
    leading=17,
    alignment=TA_LEFT,
    textColor=AZUL_MARINO,
)

COTIZACION_ESTADO = ParagraphStyle(
    'CotizacionEstado',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=10,
    leading=17,          # mismo leading que el título: baselines alineadas
    alignment=TA_RIGHT,
    textColor=GRIS_IDENTIDAD,
)

COTIZACION_METADATO = ParagraphStyle(
    'CotizacionMetadato',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=9,
    leading=12,
    textColor=GRIS_OSCURO_TEXTO,
)

COTIZACION_SECCION = ParagraphStyle(
    'CotizacionSeccion',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=9.5,
    leading=12,
    spaceBefore=2,
    spaceAfter=3,
    textColor=NEGRO,
)

COTIZACION_ETIQUETA = ParagraphStyle(
    'CotizacionEtiqueta',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=7.5,
    leading=9.5,
    textColor=GRIS_INFORMACION,
)

COTIZACION_VALOR = ParagraphStyle(
    'CotizacionValor',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=9,
    leading=11,
    textColor=GRIS_OSCURO_TEXTO,
)

COTIZACION_NOTA = ParagraphStyle(
    'CotizacionNota',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=7.5,
    leading=10,
    textColor=GRIS_INFORMACION,
)

OBSERVACIONES_TEXTO = ParagraphStyle(
    'ObservacionesTexto',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=8.5,
    leading=11,
    textColor=GRIS_OSCURO_TEXTO,
)

# Cabeceras y celdas de las tablas de conceptos (gris claro + texto oscuro).
COTIZACION_ENCABEZADO = ParagraphStyle(
    'CotizacionEncabezado',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=8.5,
    leading=10.5,
    alignment=TA_LEFT,
    textColor=NEGRO,
)

COTIZACION_ENCABEZADO_DERECHA = ParagraphStyle(
    'CotizacionEncabezadoDerecha',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=8.5,
    leading=10.5,
    alignment=TA_RIGHT,
    textColor=NEGRO,
)

CELDA_IZQUIERDA = ParagraphStyle(
    'CeldaIzquierda',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=8.5,
    leading=10.5,
    alignment=TA_LEFT,
    textColor=GRIS_OSCURO_TEXTO,
)

CELDA_DERECHA = ParagraphStyle(
    'CeldaDerecha',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=8.5,
    leading=10.5,
    alignment=TA_RIGHT,
    textColor=GRIS_OSCURO_TEXTO,
)

# Bloque de totales.
TOTAL_ETIQUETA = ParagraphStyle(
    'TotalEtiqueta',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=9,
    leading=11.5,
    alignment=TA_RIGHT,
    textColor=GRIS_IDENTIDAD,
)

TOTAL_VALOR = ParagraphStyle(
    'TotalValor',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=9,
    leading=11.5,
    alignment=TA_RIGHT,
    textColor=GRIS_OSCURO_TEXTO,
)

TOTAL_ETIQUETA_DESTACADA = ParagraphStyle(
    'TotalDestacadoEtiqueta',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=10.5,
    leading=14,
    alignment=TA_RIGHT,
    textColor=NEGRO,
)

TOTAL_VALOR_DESTACADA = ParagraphStyle(
    'TotalDestacadoValor',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=12.5,
    leading=16,
    alignment=TA_RIGHT,
    textColor=AZUL_MARINO,  # único acento de marca del documento
)


# ---------------------------------------------------------------------------
# FORMATO (helpers intactos: el formateo se aplica antes del Paragraph)
# ---------------------------------------------------------------------------

def _money(valor) -> str:
    """``$1,234.56`` (mismo formato que los reportes de listados)."""
    try:
        monto = Decimal(str(valor if valor is not None else 0))
    except (TypeError, ValueError, ArithmeticError):
        monto = Decimal('0.00')
    return f'${monto:,.2f}'


def _decimal(valor, decimales=2) -> str:
    try:
        numero = Decimal(str(valor if valor is not None else 0))
    except (TypeError, ValueError, ArithmeticError):
        numero = Decimal('0')
    return f'{numero:,.{decimales}f}'


def _porcentaje(valor) -> str:
    """``0.1500`` → ``15%``."""
    try:
        pct = Decimal(str(valor if valor is not None else 0)) * 100
    except (TypeError, ValueError, ArithmeticError):
        pct = Decimal('0')
    return f'{pct.normalize():f}%'


def _fecha(valor) -> str:
    return valor.strftime('%d/%m/%Y') if valor else ''


def _fecha_hora(valor) -> str:
    return valor.strftime('%d/%m/%Y %H:%M') if valor else ''


def _nombre_persona(persona) -> str:
    if not persona:
        return ''
    return persona.get_full_name() or getattr(persona, 'username', '') or ''


def _nombre_asesor(asesor) -> str:
    return _nombre_persona(asesor)


# ---------------------------------------------------------------------------
# CONSOLIDACIÓN DE ÍTEMS
# ---------------------------------------------------------------------------

def _como_decimal(valor) -> Decimal:
    try:
        return Decimal(str(valor if valor is not None else 0))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal('0')


def _consolidar(items, campo_cantidad: str, campo_codigo: str, campo_precio: str) -> list:
    """
    Agrupa ítems idénticos y SUMA sus cantidades.

    Se consideran idénticos los que comparten código, descripción, precio
    unitario, IVA y la marca de opcional (un servicio opcional nunca se mezcla
    con uno obligatorio). El importe del grupo es la suma de los importes de las
    líneas, por lo que el total del PDF no se altera al consolidar.

    :returns: lista de dicts con ``codigo``, ``descripcion``, ``cantidad``,
        ``precio``, ``descuento``, ``iva``, ``subtotal`` y ``es_opcional``.
    """
    grupos: dict = {}
    orden: list = []

    for item in items or []:
        clave = (
            getattr(item, campo_codigo, None) or '',
            (getattr(item, 'descripcion', '') or '').strip(),
            _como_decimal(getattr(item, campo_precio, None)),
            _como_decimal(getattr(item, 'iva_porcentaje', None)),
            bool(getattr(item, 'es_opcional', False)),
        )
        if clave not in grupos:
            grupos[clave] = {
                'codigo': clave[0],
                'descripcion': clave[1],
                'cantidad': Decimal('0'),
                'precio': clave[2],
                'descuento': Decimal('0.00'),
                'iva': clave[3],
                'subtotal': Decimal('0.00'),
                'es_opcional': clave[4],
            }
            orden.append(clave)

        grupo = grupos[clave]
        grupo['cantidad'] += _como_decimal(getattr(item, campo_cantidad, None))
        grupo['descuento'] += _como_decimal(getattr(item, 'descuento', None))
        grupo['subtotal'] += _como_decimal(getattr(item, 'subtotal', None))

    return [grupos[clave] for clave in orden]


# ---------------------------------------------------------------------------
# TÍTULO: Table invisible de 2 columnas (número y estado NUNCA se enciman)
# ---------------------------------------------------------------------------

def _bloque_titulo(cotizacion, ancho_util) -> Table:
    """
    Columna 1 alineada a la izquierda con "COTIZACIÓN <numero>"; columna 2
    alineada a la derecha con el estado. Sin bordes ni fondos.

    El número va en un ``<font>`` con el color oscuro y shares el ``leading``
    del título, así que ambas columnas comparten línea base.
    """
    izquierda = Paragraph(
        f'<b>COTIZACIÓN</b> <font color="{COLOR_NUMERO}">'
        f'{_escape_xml(cotizacion.numero_cotizacion or "")}</font>',
        COTIZACION_TITULO,
    )
    derecha = Paragraph(_escape_xml(cotizacion.get_estado_display()), COTIZACION_ESTADO)

    tabla = Table([[izquierda, derecha]], colWidths=[ancho_util * 0.68, ancho_util * 0.32])
    tabla.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    return tabla


# ---------------------------------------------------------------------------
# CLIENTE / VEHÍCULO: Table invisible de 3 columnas
# ---------------------------------------------------------------------------

def _bloque_cliente_vehiculo(cotizacion, ancho_util) -> Table:
    """
    Datos repartidos en 3 pares etiqueta/valor (6 columnas) para no crecer
    verticalmente:

      * Col 1: Cliente, Identificación, Correo
      * Col 2: Placa, Marca / Modelo, Kilometraje
      * Col 3: Teléfono, Color

    Cada columna declara su ``colWidths``: etiqueta gris pequeña (0.6-0.8in) y
    valor oscuro. Sin bordes ni fondos.
    """
    cliente = cotizacion.cliente
    vehiculo = cotizacion.vehiculo

    columnas = [
        [
            ('Cliente', getattr(cliente, 'nombre', '') or ''),
            ('Identificación', getattr(cliente, 'identificacion', '') or ''),
            ('Correo', getattr(cliente, 'email', '') or ''),
        ],
        [
            ('Placa', getattr(vehiculo, 'placa', '') or ''),
            ('Marca/Modelo', ' '.join(
                p for p in [
                    (getattr(vehiculo, 'marca', '') or '').strip(),
                    (getattr(vehiculo, 'modelo', '') or '').strip(),
                ] if p
            )),
            ('Kilometraje', (
                f"{_decimal(getattr(vehiculo, 'kilometraje_actual', None), 0)} km"
                if getattr(vehiculo, 'kilometraje_actual', None) else ''
            )),
        ],
        [
            ('Teléfono', getattr(cliente, 'telefono', '') or ''),
            ('Color', getattr(vehiculo, 'color', '') or ''),
            ('', ''),
        ],
    ]

    # (ancho de etiqueta, ancho de valor) por cada uno de los 3 pares.
    anchos_pares = [(0.85, 1.60), (0.95, 1.45), (0.70, 1.68)]
    filas = []
    for indice in range(max(len(columna) for columna in columnas)):
        fila = []
        for columna in columnas:
            etiqueta, valor = columna[indice] if indice < len(columna) else ('', '')
            fila.append(Paragraph(_escape_xml(etiqueta), COTIZACION_ETIQUETA))
            fila.append(Paragraph(_escape_xml(valor), COTIZACION_VALOR))
        filas.append(fila)

    anchos = _ajustar_anchos(
        [ancho for par in anchos_pares for ancho in par],
        ancho_util,
    )
    tabla = Table(filas, colWidths=[a * 72 for a in anchos])
    tabla.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        # Separación visual entre el valor de un par y la etiqueta del siguiente.
        ('RIGHTPADDING', (0, 0), (0, -1), 12),
        ('RIGHTPADDING', (2, 0), (2, -1), 12),
        ('RIGHTPADDING', (4, 0), (4, -1), 12),
        ('LEFTPADDING', (2, 0), (2, -1), 6),
        ('LEFTPADDING', (4, 0), (4, -1), 6),
    ]))
    return tabla


# ---------------------------------------------------------------------------
# TABLAS DE CONCEPTOS
# ---------------------------------------------------------------------------

def _columnas_detalle():
    """(título, ancho en pulgadas, alineación) de la tabla de conceptos."""
    return [
        ('Código', 0.8, TA_LEFT),
        ('Descripción', 2.5, TA_LEFT),
        ('Cant./Horas', 0.85, TA_RIGHT),
        ('P. Unitario', 0.95, TA_RIGHT),
        ('Descuento', 0.85, TA_RIGHT),
        ('IVA', 0.55, TA_RIGHT),
        ('Importe', 0.95, TA_RIGHT),
    ]


def _estilo_celda(alineacion):
    return CELDA_DERECHA if alineacion == TA_RIGHT else CELDA_IZQUIERDA


def _estilo_encabezado(alineacion):
    return COTIZACION_ENCABEZADO_DERECHA if alineacion == TA_RIGHT else COTIZACION_ENCABEZADO


def _descripcion_con_opcional(descripcion, es_opcional) -> str:
    texto = (descripcion or '').strip()
    return f'{texto} (opcional)' if es_opcional else texto


def _estilo_tabla_detalle() -> TableStyle:
    """
    Estilo minimalista original: fondo blanco en los datos, cabecera en gris
    muy claro (#D7E0E9), texto oscuro en negrita y solo divisorias
    horizontales de 0.5pt. Sin GRID, sin zebra, sin fondos de color.
    """
    return TableStyle(ESTILO_TABLA_INK_FRIENDLY + [
        # La cabecera de columnas va en negrita y respira un poco más.
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        # Refuerzo del filete bajo la cabecera.
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, GRIS_LINEA_FINA),
    ])


def _tabla_detalle(titulo, grupos, ancho_util) -> list:
    columnas = _columnas_detalle()
    anchos = _ajustar_anchos([ancho for _, ancho, _ in columnas], ancho_util)

    filas = [[
        Paragraph(_escape_xml(titulo_columna), _estilo_encabezado(alineacion))
        for titulo_columna, _, alineacion in columnas
    ]]

    for grupo in grupos:
        filas.append([
            Paragraph(_escape_xml(valor), _estilo_celda(alineacion))
            for valor, (_, _, alineacion) in zip(
                [
                    grupo['codigo'],
                    _descripcion_con_opcional(grupo['descripcion'], grupo['es_opcional']),
                    _decimal(grupo['cantidad']),
                    _money(grupo['precio']),
                    _money(grupo['descuento']),
                    _porcentaje(grupo['iva']),
                    _money(grupo['subtotal']),
                ],
                columnas,
            )
        ])

    tabla = Table(filas, colWidths=[a * 72 for a in anchos], repeatRows=1)
    tabla.setStyle(_estilo_tabla_detalle())

    return [Paragraph(_escape_xml(titulo), COTIZACION_SECCION), tabla]


def _bloques_conceptos(cotizacion, ancho_util) -> list:
    story = []

    servicios = _consolidar(
        cotizacion.servicios.all(),
        campo_cantidad='horas_estimadas',
        campo_codigo='codigo',
        campo_precio='precio_unitario',
    )
    if servicios:
        story.extend(_tabla_detalle('Mano de obra / Servicios', servicios, ancho_util))

    repuestos = _consolidar(
        cotizacion.repuestos.all(),
        campo_cantidad='cantidad',
        campo_codigo='codigo_repuesto',
        campo_precio='precio_unitario_referencial',
    )
    if repuestos:
        if servicios:
            story.append(Spacer(1, ESPACIO))
        story.extend(_tabla_detalle('Repuestos', repuestos, ancho_util))

    if not servicios and not repuestos:
        story.append(Paragraph(
            'Esta cotización no tiene conceptos registrados.',
            OBSERVACIONES_TEXTO,
        ))

    return story


# ---------------------------------------------------------------------------
# TOTALES
# ---------------------------------------------------------------------------

def _bloque_totales(cotizacion, ancho_util) -> Table:
    """Tabla compacta alineada a la derecha; el TOTAL se apoya en un filete."""
    filas_datos = [
        ('Subtotal mano de obra', _money(cotizacion.subtotal_servicios), False),
        ('Subtotal repuestos', _money(cotizacion.subtotal_repuestos), False),
    ]
    if cotizacion.descuento > Decimal('0.00'):
        filas_datos.append(('Descuentos aplicados', f'-{_money(cotizacion.descuento)}', False))
    # Las bases solo aparecen cuando hay monto en ellas (evita ruido con $0.00).
    if cotizacion.subtotal_base_0 > Decimal('0.00'):
        filas_datos.append(('Base 0%', _money(cotizacion.subtotal_base_0), False))
    if cotizacion.subtotal_base_gravada > Decimal('0.00'):
        filas_datos.append(('Base gravada', _money(cotizacion.subtotal_base_gravada), False))

    filas_datos.append(('Base imponible', _money(cotizacion.subtotal_neto), False))
    filas_datos.append(('IVA', _money(cotizacion.total_iva), False))
    filas_datos.append(('TOTAL', _money(cotizacion.total), True))

    filas = [
        [
            Paragraph(
                _escape_xml(etiqueta),
                TOTAL_ETIQUETA_DESTACADA if es_total else TOTAL_ETIQUETA,
            ),
            Paragraph(
                _escape_xml(valor),
                TOTAL_VALOR_DESTACADA if es_total else TOTAL_VALOR,
            ),
        ]
        for etiqueta, valor, es_total in filas_datos
    ]

    ancho_tabla = min(ancho_util, 3.2 * 72)
    tabla = Table(
        filas,
        colWidths=[ancho_tabla - 1.35 * 72, 1.35 * 72],
        hAlign='RIGHT',
    )
    tabla.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, GRIS_LINEA_FINA),
        # Filete oscuro bajo el TOTAL: cierra el documento sin recargarlo.
        ('LINEABOVE', (0, -1), (-1, -1), 0.8, NEGRO),
        ('TOPPADDING', (0, -1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 4),
    ]))
    return tabla


# ---------------------------------------------------------------------------
# OBSERVACIONES Y ACEPTACIÓN
# ---------------------------------------------------------------------------

def _bloque_aceptacion(cotizacion, ancho_util) -> list:
    detalles = [('Fecha', _fecha_hora(cotizacion.fecha_aceptacion))]
    if cotizacion.aceptada_por_id:
        detalles.append(('Aceptada por', _nombre_persona(cotizacion.aceptada_por)))
    if cotizacion.metodo_aceptacion:
        detalles.append(('Método', cotizacion.get_metodo_aceptacion_display()))

    filas = [
        [
            Paragraph(_escape_xml(etiqueta), COTIZACION_ETIQUETA),
            Paragraph(_escape_xml(valor), OBSERVACIONES_TEXTO),
        ]
        for etiqueta, valor in detalles
        if valor
    ]

    anchos = _ajustar_anchos([0.9, 3.2], ancho_util)
    tabla = Table(filas, colWidths=[a * 72 for a in anchos])
    tabla.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LINEBELOW', (0, -1), (-1, -1), 0.5, GRIS_LINEA_FINA),
    ]))
    return tabla


# ---------------------------------------------------------------------------
# API PÚBLICA
# ---------------------------------------------------------------------------

def _metadatos_cotizacion(cotizacion) -> str:
    """Línea de metadatos: emisión, validez, taller y asesor."""
    emission = cotizacion.created_at
    validez = (emission + timedelta(days=cotizacion.validez_dias or 0)) if emission else None

    partes = [f'Emitida el {_fecha_hora(emission)}']
    if validez:
        partes.append(f'Válida hasta el {_fecha(validez)}')
    if cotizacion.sucursal_id and cotizacion.sucursal:
        partes.append(f"Taller: {cotizacion.sucursal.nombre}")
    asesor = _nombre_asesor(getattr(cotizacion, 'asesor', None))
    if asesor:
        partes.append(f'Asesor: {asesor}')

    return '  ·  '.join(partes)


def _linea_origenes(cotizacion) -> str:
    """Trazabilidad: de dónde salió la cotización (si tiene origen)."""
    partes = []
    if cotizacion.recepcion_origen_id:
        partes.append(f"Recepción {cotizacion.recepcion_origen.numero_recepcion}")
    if cotizacion.inspeccion_origen_id:
        partes.append(f"Inspección {cotizacion.inspeccion_origen.numero_inspeccion}")
    if cotizacion.orden_trabajo_origen_id:
        partes.append(f"Orden {cotizacion.orden_trabajo_origen.numero_orden}")
    return 'Origen: ' + ' · '.join(partes) if partes else ''


def exportar_cotizacion_pdf(
    buffer: BytesIO,
    cotizacion,
    *,
    empresa=None,
    taller=None,
    usuario: str = '',
    logo=None,
) -> None:
    """
    Escribe en ``buffer`` el PDF de UNA cotización (una sola hoja A4 en el
    caso habitual).

    ``empresa``/``taller``/``logo`` son opcionales: si no se pasan, la cabecera
    compartida resuelve el logo a partir de la identidad indicada.
    """
    titulo = f'Cotización {cotizacion.numero_cotizacion}'
    doc, ancho_util = _crear_documento(buffer, titulo, empresa, taller, usuario, logo)

    story = [
        NextPageTemplate('cn'),
        _bloque_titulo(cotizacion, ancho_util),
        Paragraph(_escape_xml(_metadatos_cotizacion(cotizacion)), COTIZACION_METADATO),
    ]

    origenes = _linea_origenes(cotizacion)
    if origenes:
        story.append(Paragraph(_escape_xml(origenes), COTIZACION_NOTA))

    # Cliente / vehículo: bloque compacto de 3 columnas, 10pt antes y después.
    story.append(Spacer(1, ESPACIO))
    story.append(_bloque_cliente_vehiculo(cotizacion, ancho_util))
    story.append(Spacer(1, ESPACIO))

    story.extend(_bloques_conceptos(cotizacion, ancho_util))

    # Totales + observaciones + aceptación viajan juntos: si no cupieran, el
    # conjunto pasa íntegro a la hoja siguiente en vez de partirse.
    cierre = [Spacer(1, ESPACIO), _bloque_totales(cotizacion, ancho_util)]

    if cotizacion.observaciones:
        cierre.extend([
            Spacer(1, ESPACIO),
            Paragraph('Observaciones', COTIZACION_SECCION),
            Paragraph(_escape_xml(cotizacion.observaciones), OBSERVACIONES_TEXTO),
        ])

    if cotizacion.fecha_aceptacion:
        cierre.append(Spacer(1, ESPACIO))
        cierre.append(Paragraph('Aceptación', COTIZACION_SECCION))
        cierre.append(_bloque_aceptacion(cotizacion, ancho_util))

    cierre.append(Spacer(1, ESPACIO_COMPACTO))
    cierre.append(Paragraph(
        _escape_xml(
            'Precios en dólares de los Estados Unidos de América (USD), sujeta a '
            'validación del taller.'
        ),
        COTIZACION_NOTA,
    ))

    story.append(KeepTogether(cierre))

    doc.build(story, canvasmaker=NumberedCanvas)
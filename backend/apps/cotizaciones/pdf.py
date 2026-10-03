"""
PDF de UNA cotización: formato PROFORMA tradicional de taller automotriz.
Todo en blanco y negro, líneas finas, sin fondos de color ni zebra striping.
"""

from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
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
    GRIS_CABECERA_TABLA,
    GRIS_LINEA_FINA,
    GRIS_OSCURO_TEXTO,
    NEGRO,
    NumberedCanvas,
    _ajustar_anchos,
    _crear_documento,
    _escape_xml,
)

# Espaciados compactos
ESPACIO = 8
ESPACIO_COMPACTO = 3
COLOR_MARCA = colors.HexColor('#2B4352')

# ---------------------------------------------------------------------------
# ESTILOS
# ---------------------------------------------------------------------------

_NORMAL = getSampleStyleSheet()['Normal']

# Título de la cotización - derecha, 14pt, bold
PROFORMA_TITULO = ParagraphStyle(
    'ProformaTitulo',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=14,
    leading=17,
    alignment=TA_RIGHT,
    textColor=COLOR_MARCA,
)

# Etiquetas dentro de cajas (Cliente, Fecha, etc.) - 7.5pt, gris
CAJA_ETIQUETA = ParagraphStyle(
    'CajaEtiqueta',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=7.5,
    leading=9.5,
    textColor=colors.HexColor('#555555'),
)

# Valores dentro de cajas - 9pt, negro
CAJA_VALOR = ParagraphStyle(
    'CajaValor',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=9,
    leading=11,
    textColor=GRIS_OSCURO_TEXTO,
)

# Encabezados de tabla de ítems - 9pt, bold, negro
ITEM_ENCABEZADO = ParagraphStyle(
    'ItemEncabezado',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=9,
    leading=11,
    alignment=TA_CENTER,
    textColor=COLOR_MARCA,
)

ITEM_ENCABEZADO_IZQ = ParagraphStyle(
    'ItemEncabezadoIzq',
    parent=ITEM_ENCABEZADO,
    alignment=TA_LEFT,
)

ITEM_ENCABEZADO_DER = ParagraphStyle(
    'ItemEncabezadoDer',
    parent=ITEM_ENCABEZADO,
    alignment=TA_RIGHT,
)

# Celdas de datos de la tabla - 8pt
ITEM_CELDA_IZQ = ParagraphStyle(
    'ItemCeldaIzq',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=8,
    leading=10,
    alignment=TA_LEFT,
    textColor=GRIS_OSCURO_TEXTO,
)

ITEM_CELDA_CENTRO = ParagraphStyle(
    'ItemCeldaCentro',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=8,
    leading=10,
    alignment=TA_CENTER,
    textColor=GRIS_OSCURO_TEXTO,
)

ITEM_CELDA_DER = ParagraphStyle(
    'ItemCeldaDer',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=8,
    leading=10,
    alignment=TA_RIGHT,
    textColor=GRIS_OSCURO_TEXTO,
)

ITEM_CELDA_TOTAL = ParagraphStyle(
    'ItemCeldaTotal',
    parent=ITEM_CELDA_DER,
    fontName='Helvetica-Bold',
    textColor=COLOR_MARCA,
)

# Texto de condiciones - 8pt
CONDICIONES_TEXTO = ParagraphStyle(
    'CondicionesTexto',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=8,
    leading=10,
    textColor=GRIS_OSCURO_TEXTO,
)

CONDICIONES_TITULO = ParagraphStyle(
    'CondicionesTitulo',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=8,
    leading=10,
    textColor=COLOR_MARCA,
)

SECCION_TITULO = ParagraphStyle(
    'SeccionTitulo',
    parent=_NORMAL,
    fontName='Helvetica-Bold',
    fontSize=9,
    leading=11,
    textColor=COLOR_MARCA,
)

# Firmas - 8pt
FIRMA_ETIQUETA = ParagraphStyle(
    'FirmaEtiqueta',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=8,
    leading=10,
    alignment=TA_CENTER,
    textColor=GRIS_OSCURO_TEXTO,
)

# Totales - etiquetas 9pt, valores 9pt, total destacado 10.5pt bold
TOTAL_ETIQUETA = ParagraphStyle(
    'TotalEtiqueta',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=9,
    leading=11.5,
    alignment=TA_RIGHT,
    textColor=colors.HexColor('#555555'),
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
    fontSize=10.5,
    leading=14,
    alignment=TA_RIGHT,
    textColor=NEGRO,
)

# Nota final
NOTA_FINAL = ParagraphStyle(
    'NotaFinal',
    parent=_NORMAL,
    fontName='Helvetica',
    fontSize=7.5,
    leading=9.5,
    alignment=TA_CENTER,
    textColor=colors.HexColor('#888888'),
)

# ---------------------------------------------------------------------------
# FORMATO (helpers nativos)
# ---------------------------------------------------------------------------

def _money(valor) -> str:
    """$1,234.56"""
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
    """0.1500 → 15%"""
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
    Consolida ítems idénticos sumando sus cantidades (compatibilidad con tests).
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


def _consolidar_todos(items_servicios, items_repuestos) -> list:
    """
    Consolida servicios y repuestos en una sola lista unificada.
    Servicios usan horas_estimadas como cantidad, repuestos usan cantidad.
    """
    grupos = {}
    orden = []

    # Procesar servicios
    for item in items_servicios or []:
        clave = (
            getattr(item, 'codigo', None) or '',
            (getattr(item, 'descripcion', '') or '').strip(),
            _como_decimal(getattr(item, 'precio_unitario', None)),
            _como_decimal(getattr(item, 'iva_porcentaje', None)),
            bool(getattr(item, 'es_opcional', False)),
            'SERVICIO',
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
                'monto_iva': Decimal('0.00'),
                'es_opcional': clave[4],
                'tipo': 'SERVICIO',
            }
            orden.append(clave)
        grupo = grupos[clave]
        grupo['cantidad'] += _como_decimal(getattr(item, 'horas_estimadas', None))
        grupo['descuento'] += _como_decimal(getattr(item, 'descuento', None))
        grupo['subtotal'] += _como_decimal(getattr(item, 'subtotal', None))
        grupo['monto_iva'] += _como_decimal(getattr(item, 'monto_iva', None))

    # Procesar repuestos
    for item in items_repuestos or []:
        clave = (
            getattr(item, 'codigo_repuesto', None) or '',
            (getattr(item, 'descripcion', '') or '').strip(),
            _como_decimal(getattr(item, 'precio_unitario_referencial', None)),
            _como_decimal(getattr(item, 'iva_porcentaje', None)),
            bool(getattr(item, 'es_opcional', False)),
            'REPUESTO',
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
                'monto_iva': Decimal('0.00'),
                'es_opcional': clave[4],
                'tipo': 'REPUESTO',
            }
            orden.append(clave)
        grupo = grupos[clave]
        grupo['cantidad'] += _como_decimal(getattr(item, 'cantidad', None))
        grupo['descuento'] += _como_decimal(getattr(item, 'descuento', None))
        grupo['subtotal'] += _como_decimal(getattr(item, 'subtotal', None))
        grupo['monto_iva'] += _como_decimal(getattr(item, 'monto_iva', None))

    return [grupos[clave] for clave in orden]


# ---------------------------------------------------------------------------
# BLOQUES DEL DOCUMENTO
# ---------------------------------------------------------------------------

def _bloque_titulo_proforma(cotizacion, ancho_util) -> Table:
    """
    Título alineado a la derecha: PROFORMA TALLER I Nº [número]
    """
    texto = f'Cotización Nº {_escape_xml(cotizacion.numero_cotizacion or "")}'
    titulo = Paragraph(texto, PROFORMA_TITULO)

    tabla = Table([[titulo]], colWidths=[ancho_util])
    tabla.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -1), 1, COLOR_MARCA),
    ]))
    return tabla


def _bloque_cajas_cliente_fechas(cotizacion, ancho_util) -> Table:
    """
    Dos cajas paralelas con borde perimetral negro 0.5pt:
    - Izquierda (ancha): Cliente, Dirección, Teléfono, Vendedor
    - Derecha (angosta): Fecha, Forma de Pago, Vencimiento
    """
    cliente = cotizacion.cliente
    vehiculo = cotizacion.vehiculo
    asesor = getattr(cotizacion, 'asesor', None)

    # Datos cliente
    cliente_nombre = getattr(cliente, 'nombre', '') or ''
    cliente_direccion = getattr(cliente, 'direccion', '') or ''
    cliente_telefono = getattr(cliente, 'telefono', '') or ''
    asesor_nombre = _nombre_persona(asesor)

    # Datos fechas
    fecha_emision = _fecha_hora(cotizacion.created_at) if getattr(cotizacion, 'created_at', None) else ''
    forma_pago = 'Contado'  # Valor por defecto, se puede ajustar según modelo
    validez_dias = getattr(cotizacion, 'validez_dias', 0) or 0
    from datetime import timedelta
    fecha_venc = (cotizacion.created_at + timedelta(days=validez_dias)) if getattr(cotizacion, 'created_at', None) else None
    fecha_vencimiento = _fecha(fecha_venc)

    # Caja izquierda (Cliente)
    filas_izq = [
        [Paragraph('Cliente:', CAJA_ETIQUETA), Paragraph(_escape_xml(cliente_nombre), CAJA_VALOR)],
        [Paragraph('Dirección:', CAJA_ETIQUETA), Paragraph(_escape_xml(cliente_direccion), CAJA_VALOR)],
        [Paragraph('Teléfono:', CAJA_ETIQUETA), Paragraph(_escape_xml(cliente_telefono), CAJA_VALOR)],
        [Paragraph('Vendedor:', CAJA_ETIQUETA), Paragraph(_escape_xml(asesor_nombre), CAJA_VALOR)],
    ]

    anchos_izq = _ajustar_anchos([1.0, 3.5], ancho_util * 0.68)
    tabla_izq = Table(filas_izq, colWidths=[a * 72 for a in anchos_izq])
    tabla_izq.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), GRIS_CABECERA_TABLA),
        ('BOX', (0, 0), (-1, -1), 0.5, GRIS_LINEA_FINA),
        ('LINEBELOW', (0, 0), (-1, -2), 0.3, GRIS_LINEA_FINA),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))

    # Caja derecha (Fechas)
    filas_der = [
        [Paragraph('Fecha:', CAJA_ETIQUETA), Paragraph(_escape_xml(fecha_emision), CAJA_VALOR)],
        [Paragraph('Forma de Pago:', CAJA_ETIQUETA), Paragraph(_escape_xml(forma_pago), CAJA_VALOR)],
        [Paragraph('Vencimiento:', CAJA_ETIQUETA), Paragraph(_escape_xml(fecha_vencimiento), CAJA_VALOR)],
    ]

    anchos_der = _ajustar_anchos([1.2, 2.0], ancho_util * 0.30)
    tabla_der = Table(filas_der, colWidths=[a * 72 for a in anchos_der])
    tabla_der.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), GRIS_CABECERA_TABLA),
        ('BOX', (0, 0), (-1, -1), 0.5, GRIS_LINEA_FINA),
        ('LINEBELOW', (0, 0), (-1, -2), 0.3, GRIS_LINEA_FINA),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))

    # Tabla contenedora de ambas cajas
    contenedor = Table(
        [[tabla_izq, tabla_der]],
        colWidths=[ancho_util * 0.68, ancho_util * 0.30],
    )
    contenedor.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return contenedor


def _bloque_items_unificado(cotizacion, ancho_util) -> list:
    """
    Tabla única con todos los ítems (servicios + repuestos).
    Columnas: cantidad, código, descripción, precio unitario, descuento, neto, IVA y total.
    """
    # Consolidar todo junto
    grupos = _consolidar_todos(
        cotizacion.servicios.all(),
        cotizacion.repuestos.all(),
    )

    if not grupos:
        return [
            Paragraph('DETALLE DE SERVICIOS Y REPUESTOS', SECCION_TITULO),
            Spacer(1, ESPACIO_COMPACTO),
            Paragraph('Esta cotización no tiene conceptos registrados.', CONDICIONES_TEXTO),
        ]

    # Definir columnas: (título, ancho_pulgadas, estilo_encabezado, estilo_celda)
    columnas = [
        ('Cant.', 0.50, ITEM_ENCABEZADO_DER, ITEM_CELDA_CENTRO),
        ('Código', 0.75, ITEM_ENCABEZADO_IZQ, ITEM_CELDA_IZQ),
        ('Descripción', 2.10, ITEM_ENCABEZADO_IZQ, ITEM_CELDA_IZQ),
        ('P. unitario', 0.75, ITEM_ENCABEZADO_DER, ITEM_CELDA_DER),
        ('Descuento', 0.68, ITEM_ENCABEZADO_DER, ITEM_CELDA_DER),
        ('Neto', 0.75, ITEM_ENCABEZADO_DER, ITEM_CELDA_DER),
        ('IVA % / valor', 0.82, ITEM_ENCABEZADO_DER, ITEM_CELDA_CENTRO),
        ('Total', 0.82, ITEM_ENCABEZADO_DER, ITEM_CELDA_TOTAL),
    ]

    anchos_pulg = [ancho for _, ancho, _, _ in columnas]
    anchos_ajustados = _ajustar_anchos(anchos_pulg, ancho_util)
    col_widths = [a * 72 for a in anchos_ajustados]

    # Encabezado
    encabezado = [
        Paragraph(titulo, estilo_enc)
        for titulo, _, estilo_enc, _ in columnas
    ]

    filas = [encabezado]

    for g in grupos:
        cantidad = _decimal(g['cantidad'], 2)
        codigo = g['codigo'] or ''
        desc = g['descripcion']
        if g['es_opcional']:
            desc = f'{desc} (opcional)'
        precio = _money(g['precio'])
        descuento = _money(g['descuento']) if g['descuento'] > 0 else '$0.00'
        iva = f'{_porcentaje(g["iva"])}<br/>{_money(g["monto_iva"])}'
        total = _money(g['subtotal'] + g['monto_iva'])

        fila = [
            Paragraph(cantidad, ITEM_CELDA_CENTRO),
            Paragraph(_escape_xml(codigo), ITEM_CELDA_IZQ),
            Paragraph(_escape_xml(desc), ITEM_CELDA_IZQ),
            Paragraph(precio, ITEM_CELDA_DER),
            Paragraph(descuento, ITEM_CELDA_DER),
            Paragraph(_money(g['subtotal']), ITEM_CELDA_DER),
            Paragraph(iva, ITEM_CELDA_CENTRO),
            Paragraph(total, ITEM_CELDA_TOTAL),
        ]
        filas.append(fila)

    tabla = Table(filas, colWidths=col_widths, repeatRows=1)

    # Tabla sobria: cabecera gris-azulada y divisorias finas como los reportes.
    estilo = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), GRIS_CABECERA_TABLA),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, GRIS_LINEA_FINA),
        # Padding encabezado
        ('TOPPADDING', (0, 0), (-1, 0), 4),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        # Padding datos
        ('TOPPADDING', (0, 1), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('LINEBELOW', (0, 1), (-1, -2), 0.3, GRIS_LINEA_FINA),
        ('LINEBELOW', (0, -1), (-1, -1), 0.5, GRIS_LINEA_FINA),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])
    tabla.setStyle(estilo)

    return [
        Paragraph('DETALLE DE SERVICIOS Y REPUESTOS', SECCION_TITULO),
        Spacer(1, ESPACIO_COMPACTO),
        tabla,
    ]


def _bloque_observaciones(cotizacion, ancho_util) -> list:
    observaciones = (getattr(cotizacion, 'observaciones', None) or '').strip()
    texto = _escape_xml(observaciones or 'Sin observaciones registradas.')
    texto = texto.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')
    tabla = Table(
        [
            [Paragraph('OBSERVACIONES', CONDICIONES_TITULO)],
            [Paragraph(texto, CONDICIONES_TEXTO)],
        ],
        colWidths=[ancho_util],
    )
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), GRIS_CABECERA_TABLA),
        ('BOX', (0, 0), (-1, -1), 0.5, GRIS_LINEA_FINA),
        ('LINEBELOW', (0, 0), (-1, 0), 0.3, GRIS_LINEA_FINA),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return [tabla, Spacer(1, ESPACIO)]


def _bloque_condiciones(cotizacion, ancho_util) -> list:
    """
    Bloque CONDICIONES + firmas (izquierda) + totales (derecha).
    """
    story = []

    story.append(Paragraph('CONDICIONES', CONDICIONES_TITULO))
    story.append(Spacer(1, ESPACIO_COMPACTO))

    condiciones_texto = (
        'Los precios son válidos por el período indicado. '
        'La mano de obra incluye diagnóstico y prueba. '
        'Los repuestos cuentan con garantía del fabricante. '
        'El taller no se responsabiliza por objetos dejados en el vehículo. '
        'Trabajos adicionales requieren autorización previa del cliente.'
    )
    story.append(Paragraph(condiciones_texto, CONDICIONES_TEXTO))
    story.append(Spacer(1, ESPACIO))

    # Tabla de firmas (izquierda) y totales (derecha)
    # Firmas
    filas_firmas = [
        [
            Paragraph('____________________________', FIRMA_ETIQUETA),
            Paragraph('____________________________', FIRMA_ETIQUETA),
        ],
        [
            Paragraph('Aprobado por Cliente', FIRMA_ETIQUETA),
            Paragraph('Elaborado Por', FIRMA_ETIQUETA),
        ],
    ]
    tabla_firmas = Table(
        filas_firmas,
        colWidths=[ancho_util * 0.35, ancho_util * 0.35],
        hAlign='CENTER',
    )
    tabla_firmas.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))

    # Totales alineados a la derecha
    subtotal_neto = _como_decimal(getattr(cotizacion, 'subtotal_neto', Decimal('0')))
    descuento_total = _como_decimal(getattr(cotizacion, 'descuento', Decimal('0')))
    subtotal_base_0 = _como_decimal(getattr(cotizacion, 'subtotal_base_0', Decimal('0')))
    subtotal_base_gravada = _como_decimal(getattr(cotizacion, 'subtotal_base_gravada', Decimal('0')))
    total_iva = _como_decimal(getattr(cotizacion, 'total_iva', Decimal('0')))
    total = _como_decimal(getattr(cotizacion, 'total', Decimal('0')))

    filas_totales = [
        ('Subtotal antes de descuentos', _money(subtotal_neto + descuento_total), False),
        ('Descuento aplicado', f'-{_money(descuento_total)}', False) if descuento_total > 0 else None,
        ('Base imponible 0%', _money(subtotal_base_0), False),
        ('Base imponible gravada', _money(subtotal_base_gravada), False),
        ('IVA total', _money(total_iva), False),
        ('VALOR TOTAL', _money(total), True),
    ]
    filas_totales = [f for f in filas_totales if f]

    filas_t = []
    for etiqueta, valor, es_total in filas_totales:
        filas_t.append([
            Paragraph(_escape_xml(etiqueta), TOTAL_ETIQUETA_DESTACADA if es_total else TOTAL_ETIQUETA),
            Paragraph(_escape_xml(valor), TOTAL_VALOR_DESTACADA if es_total else TOTAL_VALOR),
        ])

    ancho_totales = min(ancho_util * 0.45, 3.2 * 72)
    tabla_totales = Table(
        filas_t,
        colWidths=[ancho_totales - 1.35 * 72, 1.35 * 72],
        hAlign='RIGHT',
    )
    tabla_totales.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        # Líneas horizontales en totales
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, GRIS_LINEA_FINA),
        ('BACKGROUND', (0, -1), (-1, -1), GRIS_CABECERA_TABLA),
        ('LINEABOVE', (0, -1), (-1, -1), 0.8, COLOR_MARCA),
        ('TOPPADDING', (0, -1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 4),
    ]))

    story.append(tabla_totales)
    story.append(Spacer(1, ESPACIO))
    story.append(Spacer(1, ESPACIO))
    story.append(Spacer(1, ESPACIO))
    story.append(Spacer(1, ESPACIO))
    story.append(Spacer(1, ESPACIO))
    story.append(tabla_firmas)
    return story


# ---------------------------------------------------------------------------
# API PÚBLICA
# ---------------------------------------------------------------------------

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
    Genera el PDF de UNA cotización en formato PROFORMA tradicional.
    """
    titulo = f'Proforma {cotizacion.numero_cotizacion}'
    doc, ancho_util = _crear_documento(buffer, titulo, empresa, taller, usuario, logo)

    story = [
        NextPageTemplate('cn'),
        _bloque_titulo_proforma(cotizacion, ancho_util),
        Spacer(1, ESPACIO),
        _bloque_cajas_cliente_fechas(cotizacion, ancho_util),
        Spacer(1, ESPACIO),
    ]

    # Tabla única de ítems
    story.extend(_bloque_items_unificado(cotizacion, ancho_util))
    story.append(Spacer(1, ESPACIO))

    # Observaciones del asesor antes de las condiciones de la oferta.
    story.extend(_bloque_observaciones(cotizacion, ancho_util))

    # Condiciones, firmas y totales
    story.extend(_bloque_condiciones(cotizacion, ancho_util))

    doc.build(story, canvasmaker=NumberedCanvas)
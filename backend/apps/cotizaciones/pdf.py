"""
PDF de UNA cotización (documento formal para el cliente).

Comparte la línea gráfica de la plantilla estándar de listados
(``apps/core/utils/pdf_export.py``, sección 7): márgenes, cabecera con logo e
identidad a la izquierda y título a la derecha, filetes negros, divisorias
grises finas y casi sin rellenos (mínimo consumo de tinta).

Página 1:
  ┌──────────────────────────────────────────────────────────────────┐
  │ [logo] EMPRESA                                       COTIZACIÓN   │
  │        RUC · Taller · Dirección · Telf.            Nº COT-000123  │
  │                                       Emitida … · Válida hasta …  │
  ├──────────────────────────────────────────────────────────────────┤
  │ CLIENTE            │ VEHÍCULO            │ COTIZACIÓN             │
  ├──────────────────────────────────────────────────────────────────┤
  │ # CÓDIGO DESCRIPCIÓN                CANT. P. UNIT. DESC.   TOTAL  │
  │ MANO DE OBRA                                         Subtotal …  │
  │ REPUESTOS                                            Subtotal …  │
  ├──────────────────────────────────────────────────────────────────┤
  │ Son: … dólares                         Subtotal / Desc. / IVA     │
  │ Observaciones · Condiciones             TOTAL                     │
  │ ___________________       ___________________                     │
  └──────────────────────────────────────────────────────────────────┘
Páginas 2+: "EMPRESA · COTIZACIÓN Nº … (continuación)" y la cabecera de la
tabla repetida. Pie en todas: generado por / "Página X de Y".
"""

import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
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
    _recortar_texto,
)

# ---------------------------------------------------------------------------
# MEDIDAS Y COLORES
# ---------------------------------------------------------------------------

# Navy de la marca (brand-900): solo número del documento y total.
COLOR_MARCA = colors.HexColor('#111C2E')
GRIS_SUAVE = colors.HexColor('#6B7280')       # etiquetas
GRIS_FONDO = colors.HexColor('#F3F6FA')       # brand-50: único relleno (filas de grupo)

LOGO_MAX = 56
ESPACIO_LOGO = 10
ALTO_PIE = 16              # banda del pie (generado por / paginado)
ALTO_CONTINUACION = 20     # banda de la cabecera de las páginas 2+
ESPACIO = 10

ETIQUETAS_TIPO = {'SERVICIO': 'Mano de obra', 'REPUESTO': 'Repuestos'}

# ---------------------------------------------------------------------------
# ESTILOS
# ---------------------------------------------------------------------------


def _estilo(nombre, tamano=8, fuente='Helvetica', color=NEGRO, alineacion=TA_LEFT, interlinea=None, **extra):
    return ParagraphStyle(
        nombre,
        fontName=fuente,
        fontSize=tamano,
        leading=interlinea or round(tamano * 1.25, 1),
        textColor=color,
        alignment=alineacion,
        **extra,
    )


EMPRESA_NOMBRE = _estilo('CotEmpresa', 12, 'Helvetica-Bold', interlinea=14.5)
EMPRESA_DATO = _estilo('CotEmpresaDato', 8, color=LST_GRIS_TEXTO, interlinea=10)
DOC_TIPO = _estilo('CotTipo', 9, 'Helvetica-Bold', LST_GRIS_TEXTO, TA_RIGHT, charSpace=1.5)
DOC_NUMERO = _estilo('CotNumero', 17, 'Helvetica-Bold', COLOR_MARCA, TA_RIGHT, interlinea=20)
DOC_META = _estilo('CotMeta', 8, color=LST_GRIS_TEXTO, alineacion=TA_RIGHT, interlinea=10)

BLOQUE_TITULO = _estilo('CotBloqueTitulo', 7, 'Helvetica-Bold', GRIS_SUAVE, charSpace=0.8)
BLOQUE_PRINCIPAL = _estilo('CotBloquePrincipal', 10, 'Helvetica-Bold', interlinea=12.5)
BLOQUE_ETIQUETA = _estilo('CotBloqueEtiqueta', 7.5, color=GRIS_SUAVE, interlinea=10)
BLOQUE_VALOR = _estilo('CotBloqueValor', 8, interlinea=10)

COLUMNA = _estilo('CotColumna', 7.5, 'Helvetica-Bold')
COLUMNA_DER = _estilo('CotColumnaDer', 7.5, 'Helvetica-Bold', alineacion=TA_RIGHT)
CELDA = _estilo('CotCelda', 8, interlinea=10)
CELDA_GRIS = _estilo('CotCeldaGris', 8, color=LST_GRIS_TEXTO, interlinea=10)
CELDA_DER = _estilo('CotCeldaDer', 8, alineacion=TA_RIGHT, interlinea=10)
CELDA_TOTAL = _estilo('CotCeldaTotal', 8, 'Helvetica-Bold', alineacion=TA_RIGHT, interlinea=10)
GRUPO = _estilo('CotGrupo', 7.5, 'Helvetica-Bold', charSpace=0.8)
GRUPO_DER = _estilo('CotGrupoDer', 7.5, 'Helvetica-Bold', alineacion=TA_RIGHT)
NOTA_TABLA = _estilo('CotNotaTabla', 7, color=GRIS_SUAVE, interlinea=9)
VACIO = _estilo('CotVacio', 8, 'Helvetica-Oblique', LST_GRIS_TEXTO)

TEXTO = _estilo('CotTexto', 8, color=LST_GRIS_TEXTO, interlinea=10.5)
SON = _estilo('CotSon', 8, interlinea=10.5)
TOTAL_ETIQUETA = _estilo('CotTotalEtiqueta', 8.5, color=LST_GRIS_TEXTO, alineacion=TA_RIGHT, interlinea=11)
TOTAL_VALOR = _estilo('CotTotalValor', 8.5, alineacion=TA_RIGHT, interlinea=11)
TOTAL_FINAL_ETIQUETA = _estilo('CotTotalFinalEtiqueta', 11, 'Helvetica-Bold', COLOR_MARCA, TA_RIGHT, interlinea=14)
TOTAL_FINAL_VALOR = _estilo('CotTotalFinalValor', 13, 'Helvetica-Bold', COLOR_MARCA, TA_RIGHT, interlinea=16)
FIRMA_NOMBRE = _estilo('CotFirmaNombre', 8, 'Helvetica-Bold', alineacion=TA_CENTER, interlinea=10)
FIRMA_ROL = _estilo('CotFirmaRol', 7.5, color=GRIS_SUAVE, alineacion=TA_CENTER, interlinea=9.5)

SIN_PADDING = [
    ('LEFTPADDING', (0, 0), (-1, -1), 0),
    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ('TOPPADDING', (0, 0), (-1, -1), 0),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
]

# ---------------------------------------------------------------------------
# FORMATO
# ---------------------------------------------------------------------------


def _como_decimal(valor) -> Decimal:
    try:
        return Decimal(str(valor if valor is not None else 0))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal('0')


def _money(valor) -> str:
    """$1,234.56"""
    return f'${_como_decimal(valor):,.2f}'


def _cantidad(valor) -> str:
    """1 · 2.5 · 0.25 (sin ceros sobrantes)."""
    numero = _como_decimal(valor).quantize(Decimal('0.01'))
    texto = f'{numero:,.2f}'
    return texto.rstrip('0').rstrip('.') if '.' in texto else texto


def _porcentaje(valor) -> str:
    """0.1500 → 15%"""
    pct = (_como_decimal(valor) * 100).normalize()
    return f'{pct:f}%'


def _local(valor):
    if valor is None:
        return None
    return timezone.localtime(valor) if timezone.is_aware(valor) else valor


def _fecha(valor) -> str:
    valor = _local(valor)
    return valor.strftime('%d/%m/%Y') if valor else ''


def _nombre_persona(persona) -> str:
    if not persona:
        return ''
    return persona.get_full_name() or getattr(persona, 'username', '') or ''


def _texto(valor) -> str:
    """Texto escapado para ``Paragraph`` con los saltos de línea respetados."""
    texto = _escape_xml(str(valor or '').strip())
    return texto.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')


# -- Monto en letras (dólares) -----------------------------------------------

_UNIDADES = (
    '', 'uno', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve',
    'diez', 'once', 'doce', 'trece', 'catorce', 'quince', 'dieciséis', 'diecisiete',
    'dieciocho', 'diecinueve', 'veinte', 'veintiuno', 'veintidós', 'veintitrés',
    'veinticuatro', 'veinticinco', 'veintiséis', 'veintisiete', 'veintiocho', 'veintinueve',
)
_DECENAS = ('', '', '', 'treinta', 'cuarenta', 'cincuenta', 'sesenta', 'setenta', 'ochenta', 'noventa')
_CENTENAS = (
    '', 'ciento', 'doscientos', 'trescientos', 'cuatrocientos', 'quinientos',
    'seiscientos', 'setecientos', 'ochocientos', 'novecientos',
)


def _hasta_mil(numero: int) -> str:
    if numero == 100:
        return 'cien'
    centenas, resto = divmod(numero, 100)
    partes = [_CENTENAS[centenas]] if centenas else []
    if resto < 30:
        partes.append(_UNIDADES[resto])
    else:
        decenas, unidades = divmod(resto, 10)
        partes.append(_DECENAS[decenas] + (f' y {_UNIDADES[unidades]}' if unidades else ''))
    return ' '.join(p for p in partes if p)


def _numero_en_letras(numero: int) -> str:
    if numero == 0:
        return 'cero'
    millones, resto = divmod(numero, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    partes = []
    if millones:
        partes.append('un millón' if millones == 1 else f'{_numero_en_letras(millones)} millones')
    if miles:
        partes.append('mil' if miles == 1 else f'{_hasta_mil(miles)} mil')
    if unidades:
        partes.append(_hasta_mil(unidades))
    texto = ' '.join(partes)
    # Apócope ante «mil»/«millones»: "veintiuno mil" → "veintiún mil".
    texto = re.sub(r'\bveintiuno (?=mil)', 'veintiún ', texto)
    return re.sub(r'\buno (?=mil)', 'un ', texto)


def monto_en_letras(valor) -> str:
    """248.91 → 'Doscientos cuarenta y ocho con 91/100 dólares'."""
    monto = _como_decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if monto < 0:
        monto = -monto
    enteros = int(monto)
    centavos = int((monto - enteros) * 100)
    texto = _numero_en_letras(enteros)
    if texto.endswith('uno'):
        texto = texto[:-1]  # "veintiuno dólares" → "veintiún"; "uno" → "un"
        if texto.endswith('veintiun'):
            texto = texto[:-2] + 'ún'
    texto = f'{texto} con {centavos:02d}/100 dólares'
    return texto[:1].upper() + texto[1:]


# ---------------------------------------------------------------------------
# CONSOLIDACIÓN DE ÍTEMS
# ---------------------------------------------------------------------------

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
    Consolida servicios (cantidad = horas estimadas) y repuestos en una lista
    con su ``tipo``; los ítems idénticos se suman en una sola línea.
    """
    fuentes = (
        ('SERVICIO', items_servicios, 'horas_estimadas', 'codigo', 'precio_unitario'),
        ('REPUESTO', items_repuestos, 'cantidad', 'codigo_repuesto', 'precio_unitario_referencial'),
    )
    lineas = []
    for tipo, items, campo_cantidad, campo_codigo, campo_precio in fuentes:
        items = list(items or [])
        iva_por_clave: dict = {}
        for item in items:
            clave = (
                getattr(item, campo_codigo, None) or '',
                (getattr(item, 'descripcion', '') or '').strip(),
                _como_decimal(getattr(item, campo_precio, None)),
                _como_decimal(getattr(item, 'iva_porcentaje', None)),
                bool(getattr(item, 'es_opcional', False)),
            )
            iva_por_clave[clave] = iva_por_clave.get(clave, Decimal('0')) + _como_decimal(
                getattr(item, 'monto_iva', None)
            )
        for grupo, monto_iva in zip(
            _consolidar(items, campo_cantidad, campo_codigo, campo_precio), iva_por_clave.values()
        ):
            grupo['monto_iva'] = monto_iva
            grupo['tipo'] = tipo
            lineas.append(grupo)
    return lineas


# ---------------------------------------------------------------------------
# CABECERA, BLOQUES DE DATOS Y PÁGINA
# ---------------------------------------------------------------------------

class _Documento:
    """Datos derivados de la cotización que comparten bloques y callbacks."""

    def __init__(self, cotizacion, empresa, taller, usuario, logo):
        self.cotizacion = cotizacion
        self.empresa = empresa
        self.taller = taller
        self.identidad = _datos_identidad(empresa, taller)
        self.usuario = (usuario or '').strip()
        self.generado = timezone.localtime()
        self.numero = cotizacion.numero_cotizacion or ''

        if logo is None:
            logo = _coincidir_logo(empresa, taller)
        self.logo_bin, self.logo_ancho, self.logo_alto = _leer_logo(logo, LOGO_MAX * 2.2, LOGO_MAX)

        self.emitida = getattr(cotizacion, 'created_at', None)
        self.validez = getattr(cotizacion, 'validez_dias', 0) or 0
        self.vence = self.emitida + timedelta(days=self.validez) if self.emitida else None

    # -- cabecera de la página 1 -------------------------------------------

    def cabecera(self, ancho: float) -> Table:
        datos = self.identidad
        empresa = []
        if datos['nombre']:
            empresa.append(Paragraph(_escape_xml(datos['nombre']), EMPRESA_NOMBRE))
        lineas = []
        if datos['ruc']:
            lineas.append(f"RUC {datos['ruc']}")
        nombre_taller = getattr(self.taller, 'nombre', '') or ''
        if nombre_taller and nombre_taller != datos['nombre']:
            lineas.append(nombre_taller)
        if datos['direccion']:
            lineas.append(datos['direccion'])
        contacto = []
        if datos['telefono']:
            contacto.append(f"Telf. {datos['telefono']}")
        correo = getattr(self.empresa, 'email_contacto', '') or ''
        if correo:
            contacto.append(correo)
        if contacto:
            lineas.append('  ·  '.join(contacto))
        empresa.extend(Paragraph(_escape_xml(linea), EMPRESA_DATO) for linea in lineas)
        if not empresa:
            empresa.append(Paragraph('', EMPRESA_DATO))

        ancho_der = ancho * 0.38
        ancho_izq = ancho - ancho_der
        if self.logo_bin and self.logo_ancho and self.logo_alto:
            ancho_logo = self.logo_ancho + ESPACIO_LOGO
            izquierda = Table(
                [[Image(BytesIO(self.logo_bin), width=self.logo_ancho, height=self.logo_alto), empresa]],
                colWidths=[ancho_logo, ancho_izq - ancho_logo],
            )
        else:
            izquierda = Table([[empresa]], colWidths=[ancho_izq])
        izquierda.setStyle(TableStyle(SIN_PADDING + [('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))

        meta = [f'Emitida: {_fecha(self.emitida)}'] if self.emitida else []
        if self.vence:
            meta.append(f'Válida hasta: {_fecha(self.vence)}')
        derecha = [
            Paragraph('COTIZACIÓN', DOC_TIPO),
            Paragraph(_escape_xml(f'Nº {self.numero}' if self.numero else ''), DOC_NUMERO),
            Spacer(1, 2),
            *[Paragraph(_escape_xml(linea), DOC_META) for linea in meta],
        ]

        tabla = Table([[izquierda, derecha]], colWidths=[ancho_izq, ancho_der])
        tabla.setStyle(TableStyle(SIN_PADDING + [
            ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
            ('VALIGN', (1, 0), (1, 0), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LINEBELOW', (0, 0), (-1, -1), 1.0, NEGRO),
        ]))
        return tabla

    # -- cliente · vehículo · cotización ------------------------------------

    @staticmethod
    def _bloque(titulo, principal, filas, ancho):
        contenido = [Paragraph(titulo, BLOQUE_TITULO), Spacer(1, 3)]
        contenido.append(Paragraph(_texto(principal) or '—', BLOQUE_PRINCIPAL))
        contenido.append(Spacer(1, 2))
        pares = [(e, v) for e, v in filas if v not in (None, '')]
        if pares:
            ancho_etiqueta = 52
            tabla = Table(
                [[Paragraph(e, BLOQUE_ETIQUETA), Paragraph(_texto(v), BLOQUE_VALOR)] for e, v in pares],
                colWidths=[ancho_etiqueta, ancho - ancho_etiqueta],
            )
            tabla.setStyle(TableStyle(SIN_PADDING + [
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
            ]))
            contenido.append(tabla)
        return contenido

    def datos(self, ancho: float) -> Table:
        c = self.cotizacion
        cliente = c.cliente
        tipo_id = ''
        if cliente is not None and hasattr(cliente, 'get_tipo_identificacion_display'):
            tipo_id = cliente.get_tipo_identificacion_display()
        filas_cliente = [
            (tipo_id or 'Identificación', getattr(cliente, 'identificacion', '')),
            ('Teléfono', getattr(cliente, 'telefono', '')),
            ('Correo', getattr(cliente, 'email', '')),
            ('Dirección', getattr(cliente, 'direccion', '')),
        ]

        vehiculo = getattr(c, 'vehiculo', None)
        if vehiculo is not None:
            from apps.ordenes.models import OrdenTrabajo

            descripcion = ' '.join(
                str(p) for p in (
                    getattr(vehiculo, 'marca', ''), getattr(vehiculo, 'modelo', ''), getattr(vehiculo, 'anio', ''),
                ) if p
            )
            km = OrdenTrabajo.kilometraje_de_visita(
                vehiculo, getattr(c, 'recepcion_origen', None), getattr(c, 'inspeccion_origen', None),
            )
            principal_vehiculo = getattr(vehiculo, 'placa', '') or ''
            filas_vehiculo = [
                ('Vehículo', descripcion),
                ('Color', getattr(vehiculo, 'color', '')),
                ('VIN', getattr(vehiculo, 'vin', '')),
                ('Kilometraje', f'{km:,} km'.replace(',', '.') if km else ''),
            ]
        else:
            principal_vehiculo, filas_vehiculo = 'Sin vehículo asignado', []

        referencias = [
            getattr(getattr(c, 'recepcion_origen', None), 'numero_recepcion', ''),
            getattr(getattr(c, 'inspeccion_origen', None), 'numero_inspeccion', ''),
        ]
        estado = c.get_estado_display() if hasattr(c, 'get_estado_display') else ''
        filas_doc = [
            ('Validez', f'{self.validez} días'),
            ('Asesor', _nombre_persona(getattr(c, 'asesor', None))),
            ('Estado', estado),
            ('Origen', ' · '.join(r for r in referencias if r)),
        ]

        separacion = 12
        anchos = [ancho * 0.36, ancho * 0.32, ancho * 0.32]
        internos = [a - separacion * 2 for a in anchos]
        celdas = [
            self._bloque('CLIENTE', getattr(cliente, 'nombre', ''), filas_cliente, internos[0]),
            self._bloque('VEHÍCULO', principal_vehiculo, filas_vehiculo, internos[1]),
            self._bloque('COTIZACIÓN', getattr(self.taller, 'nombre', '') or 'Datos del documento',
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
        ]))
        return tabla

    # -- callbacks de página -------------------------------------------------

    def _pie(self, canvas, doc) -> None:
        x0 = doc.leftMargin
        x1 = doc.pagesize[0] - doc.rightMargin
        y = LST_MARGEN_INFERIOR
        canvas.setStrokeColor(LST_GRIS_FILA)
        canvas.setLineWidth(0.4)
        canvas.line(x0, y + 10, x1, y + 10)
        partes = [f"Generado el {self.generado.strftime('%d/%m/%Y %H:%M')}"]
        if self.usuario:
            partes.append(f'por {self.usuario}')
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(GRIS_SUAVE)
        canvas.drawString(x0, y, ' '.join(partes))
        canvas._gm_paginado = (x1, y, 7, 'Página %s de %s', 'R', GRIS_SUAVE)

    def dibujar_primera(self, canvas, doc) -> None:
        canvas.saveState()
        self._pie(canvas, doc)
        canvas.restoreState()

    def dibujar_continuacion(self, canvas, doc) -> None:
        canvas.saveState()
        x0 = doc.leftMargin
        x1 = doc.pagesize[0] - doc.rightMargin
        y = doc.pagesize[1] - LST_MARGEN_SUPERIOR - 8
        texto = ' · '.join(p for p in (self.identidad['nombre'], f'COTIZACIÓN Nº {self.numero}') if p)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(LST_GRIS_TEXTO)
        canvas.drawString(x0, y, _recortar_texto(f'{texto} (continuación)', 'Helvetica', 8, x1 - x0))
        self._pie(canvas, doc)
        canvas.restoreState()


# ---------------------------------------------------------------------------
# DETALLE
# ---------------------------------------------------------------------------

def _bloque_detalle(cotizacion, ancho: float) -> list:
    """
    Tabla única agrupada en «Mano de obra» y «Repuestos», cada grupo con su
    subtotal. Columnas: #, código, descripción, cantidad, precio, descuento y
    total de la línea (neto, sin IVA: el IVA va en el resumen).
    """
    lineas = _consolidar_todos(cotizacion.servicios.all(), cotizacion.repuestos.all())

    columnas = [
        ('#', 0.28, 'L'),
        ('Código', 0.85, 'L'),
        ('Descripción', 3.05, 'L'),
        ('Cant.', 0.50, 'R'),
        ('P. unit.', 0.75, 'R'),
        ('Desc.', 0.65, 'R'),
        ('Total', 0.85, 'R'),
    ]
    factor = ancho / sum(a for _, a, _ in columnas)
    anchos = [a * factor for _, a, _ in columnas]
    cabecera = [Paragraph(t.upper(), COLUMNA_DER if al == 'R' else COLUMNA) for t, _, al in columnas]

    filas = [cabecera]
    comandos = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (0, -1), 2),
        ('RIGHTPADDING', (-1, 0), (-1, -1), 2),
        ('LINEABOVE', (0, 0), (-1, 0), 1.0, NEGRO),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, NEGRO),
    ]

    if not lineas:
        filas.append([Paragraph('Esta cotización aún no tiene servicios ni repuestos.', VACIO)] + [''] * 6)
        comandos += [('SPAN', (0, 1), (-1, 1)), ('TOPPADDING', (0, 1), (-1, 1), 10),
                     ('BOTTOMPADDING', (0, 1), (-1, 1), 10)]
    else:
        tasas = {l['iva'] for l in lineas}
        marcar_sin_iva = len(tasas) > 1 and Decimal('0') in tasas
        numero = 0
        for tipo in ('SERVICIO', 'REPUESTO'):
            grupo = [l for l in lineas if l['tipo'] == tipo]
            if not grupo:
                continue
            subtotal = sum((l['subtotal'] for l in grupo), Decimal('0'))
            fila_grupo = len(filas)
            filas.append(
                [Paragraph(ETIQUETAS_TIPO[tipo].upper(), GRUPO)] + [''] * 5
                + [Paragraph(_money(subtotal), GRUPO_DER)]
            )
            comandos += [
                ('SPAN', (0, fila_grupo), (5, fila_grupo)),
                ('BACKGROUND', (0, fila_grupo), (-1, fila_grupo), GRIS_FONDO),
                ('TOPPADDING', (0, fila_grupo), (-1, fila_grupo), 4),
                ('BOTTOMPADDING', (0, fila_grupo), (-1, fila_grupo), 4),
            ]
            for linea in grupo:
                numero += 1
                descripcion = _texto(linea['descripcion'])
                extras = []
                if linea['es_opcional']:
                    extras.append('Opcional')
                if marcar_sin_iva and linea['iva'] == 0:
                    extras.append('IVA 0%')
                if extras:
                    descripcion += f' <font color="#6B7280" size="7">· {" · ".join(extras)}</font>'
                descuento = linea['descuento']
                filas.append([
                    Paragraph(str(numero), CELDA_GRIS),
                    Paragraph(_escape_xml(linea['codigo']) or '—', CELDA_GRIS),
                    Paragraph(descripcion, CELDA),
                    Paragraph(_cantidad(linea['cantidad']), CELDA_DER),
                    Paragraph(_money(linea['precio']), CELDA_DER),
                    Paragraph(f'-{_money(descuento)}' if descuento > 0 else '—', CELDA_DER),
                    Paragraph(_money(linea['subtotal']), CELDA_TOTAL),
                ])
                if linea is not grupo[-1]:
                    comandos.append(('LINEBELOW', (0, len(filas) - 1), (-1, len(filas) - 1), 0.3, LST_GRIS_FILA))
    comandos.append(('LINEBELOW', (0, -1), (-1, -1), 0.8, NEGRO))

    tabla = Table(filas, colWidths=anchos, repeatRows=1)
    tabla.setStyle(TableStyle(comandos))

    flujo = [tabla]
    if lineas and any(l['tipo'] == 'SERVICIO' for l in lineas):
        flujo += [Spacer(1, 3), Paragraph(
            'En mano de obra, la cantidad corresponde a horas estimadas. '
            'Los totales por línea no incluyen IVA.', NOTA_TABLA,
        )]
    return flujo


# ---------------------------------------------------------------------------
# CIERRE: SON, NOTAS, TOTALES Y FIRMAS
# ---------------------------------------------------------------------------

def _tabla_totales(cotizacion, lineas_tasas, ancho: float) -> Table:
    neto = _como_decimal(getattr(cotizacion, 'subtotal_neto', None))
    descuento = _como_decimal(getattr(cotizacion, 'descuento', None))
    base_0 = _como_decimal(getattr(cotizacion, 'subtotal_base_0', None))
    gravada = _como_decimal(getattr(cotizacion, 'subtotal_base_gravada', None))
    iva = _como_decimal(getattr(cotizacion, 'total_iva', None))
    total = _como_decimal(getattr(cotizacion, 'total', None))

    tasas = sorted(t for t in lineas_tasas if t > 0)
    tasa = f' {_porcentaje(tasas[0])}' if len(tasas) == 1 else ''

    filas = [('Subtotal', _money(neto + descuento))]
    if descuento > 0:
        filas.append(('Descuento', f'-{_money(descuento)}'))
    if base_0 > 0:
        filas.append(('Subtotal IVA 0%', _money(base_0)))
    filas.append((f'Subtotal IVA{tasa}', _money(gravada)))
    filas.append((f'IVA{tasa}', _money(iva)))

    contenido = [[Paragraph(e, TOTAL_ETIQUETA), Paragraph(v, TOTAL_VALOR)] for e, v in filas]
    contenido.append([Paragraph('TOTAL', TOTAL_FINAL_ETIQUETA), Paragraph(_money(total), TOTAL_FINAL_VALOR)])

    tabla = Table(contenido, colWidths=[ancho - 82, 82])
    tabla.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LINEBELOW', (0, 0), (-1, -3), 0.3, LST_GRIS_FILA),
        ('LINEABOVE', (0, -1), (-1, -1), 1.2, COLOR_MARCA),
        ('TOPPADDING', (0, -1), (-1, -1), 6),
    ]))
    return tabla


CONDICIONES_POR_DEFECTO = (
    'Los valores son referenciales y pueden variar si al desarmar se encuentran daños ocultos; '
    'todo trabajo adicional requiere autorización previa del cliente.',
    'El taller no se responsabiliza por objetos de valor dejados en el vehículo.',
)


def condiciones_de_empresa(empresa) -> list:
    """
    Condiciones configuradas en ``DetalleEmpresa.condiciones`` (una por línea,
    sin viñetas iniciales). Sin detalle o sin texto: las condiciones por defecto.
    """
    detalle = getattr(empresa, 'detalle', None) if empresa is not None else None
    texto = getattr(detalle, 'condiciones', '') or ''
    lineas = [
        re.sub(r'^[\s•\-*·]+', '', linea).strip()
        for linea in texto.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    ]
    return [linea for linea in lineas if linea] or list(CONDICIONES_POR_DEFECTO)


def _condiciones(doc: _Documento) -> list:
    """Validez de la cotización + opcionales (si hay) + condiciones de la empresa."""
    texto = [
        f'Precios válidos por {doc.validez} días'
        + (f', hasta el {_fecha(doc.vence)}.' if doc.vence else '.'),
    ]
    items = list(doc.cotizacion.servicios.all()) + list(doc.cotizacion.repuestos.all())
    if any(getattr(i, 'es_opcional', False) for i in items):
        texto.append('Los ítems marcados como «Opcional» son recomendaciones del taller y están incluidos en el total.')
    texto.extend(condiciones_de_empresa(doc.empresa))
    return [Paragraph(f'• {_escape_xml(t)}', TEXTO) for t in texto]


def _firmas(doc: _Documento, ancho: float) -> Table:
    cliente = getattr(doc.cotizacion.cliente, 'nombre', '') or ''
    asesor = _nombre_persona(getattr(doc.cotizacion, 'asesor', None))
    ancho_firma = min(ancho * 0.36, 190)
    hueco = ancho - 2 * ancho_firma

    def celda(nombre, rol):
        return [Paragraph(_escape_xml(nombre) or '&nbsp;', FIRMA_NOMBRE), Paragraph(rol, FIRMA_ROL)]

    tabla = Table(
        [[celda(cliente, 'Aceptado por el cliente'), '', celda(asesor, 'Asesor de servicio')]],
        colWidths=[ancho_firma, hueco, ancho_firma],
    )
    tabla.setStyle(TableStyle(SIN_PADDING + [
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('LINEABOVE', (0, 0), (0, 0), 0.6, NEGRO),
        ('LINEABOVE', (2, 0), (2, 0), 0.6, NEGRO),
    ]))
    return tabla


def _bloque_cierre(doc: _Documento, ancho: float) -> list:
    cotizacion = doc.cotizacion
    tasas = {
        _como_decimal(getattr(i, 'iva_porcentaje', None))
        for i in list(cotizacion.servicios.all()) + list(cotizacion.repuestos.all())
    }

    ancho_totales = min(ancho * 0.40, 220)
    ancho_texto = ancho - ancho_totales - 24

    izquierda = [
        Paragraph('SON', BLOQUE_TITULO), Spacer(1, 2),
        Paragraph(_escape_xml(monto_en_letras(getattr(cotizacion, 'total', 0))), SON),
    ]
    observaciones = _texto(getattr(cotizacion, 'observaciones', ''))
    if observaciones:
        izquierda += [Spacer(1, 8), Paragraph('OBSERVACIONES', BLOQUE_TITULO), Spacer(1, 2),
                      Paragraph(observaciones, TEXTO)]
    izquierda += [Spacer(1, 8), Paragraph('CONDICIONES', BLOQUE_TITULO), Spacer(1, 2), *_condiciones(doc)]

    resumen = Table(
        [[izquierda, '', _tabla_totales(cotizacion, tasas, ancho_totales)]],
        colWidths=[ancho_texto, 24, ancho_totales],
    )
    resumen.setStyle(TableStyle(SIN_PADDING + [('VALIGN', (0, 0), (-1, -1), 'TOP')]))

    return [KeepTogether([resumen, Spacer(1, 46), _firmas(doc, ancho)])]


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
    """Genera el PDF de UNA cotización en ``buffer``."""
    doc_datos = _Documento(cotizacion, empresa, taller, usuario, logo)
    ancho = PAGINA[0] - 2 * LST_MARGEN_LATERAL
    alto_util = PAGINA[1] - LST_MARGEN_SUPERIOR - LST_MARGEN_INFERIOR - ALTO_PIE

    doc = BaseDocTemplate(
        buffer,
        pagesize=PAGINA,
        leftMargin=LST_MARGEN_LATERAL,
        rightMargin=LST_MARGEN_LATERAL,
        topMargin=LST_MARGEN_SUPERIOR,
        bottomMargin=LST_MARGEN_INFERIOR,
        title=f'Cotización {doc_datos.numero}'.strip(),
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
        *_bloque_detalle(cotizacion, ancho),
        Spacer(1, ESPACIO * 1.6),
        *_bloque_cierre(doc_datos, ancho),
    ]
    doc.build(story, canvasmaker=NumberedCanvas)

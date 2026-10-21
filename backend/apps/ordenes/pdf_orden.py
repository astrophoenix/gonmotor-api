"""
PDF de UNA orden de trabajo (documento para el cliente).

Misma línea gráfica que la cotización (``apps/cotizaciones/pdf.py``) y el
informe de la inspección (``pdf_inspeccion.py``): cabecera con logo e
identidad, filetes negros, divisorias grises y casi sin rellenos. El cuerpo
sigue las dos secciones de la pantalla de la orden: trabajo y evidencia.

Página 1:
  ┌──────────────────────────────────────────────────────────────────┐
  │ [logo] EMPRESA                                 ORDEN DE TRABAJO   │
  │        RUC · Taller · Dirección · Telf.             Nº OT-000123  │
  │                                       Ingreso … · Entrega …       │
  ├──────────────────────────────────────────────────────────────────┤
  │ CLIENTE            │ VEHÍCULO            │ ORDEN DE TRABAJO       │
  ├──────────────────────────────────────────────────────────────────┤
  │ ① TRABAJO     servicios (técnico, hecho/pendiente) y repuestos    │
  │               Son · Notas para el cliente        Subtotal … TOTAL │
  │ ② EVIDENCIA   fotos del trabajo (3 por fila)                      │
  │ Condición · ___________________       ___________________         │
  └──────────────────────────────────────────────────────────────────┘
La condición y las firmas cierran el documento, tras la evidencia. Páginas 2+:
"EMPRESA · ORDEN DE TRABAJO Nº … (continuación)". Pie en todas: generado por /
"Página X de Y".
"""

from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from reportlab.platypus import (
    BaseDocTemplate,
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
from apps.cotizaciones.pdf import (
    ALTO_CONTINUACION,
    ALTO_PIE,
    BLOQUE_TITULO,
    CELDA,
    CELDA_DER,
    CELDA_GRIS,
    CELDA_TOTAL,
    COLUMNA,
    COLUMNA_DER,
    FIRMA_NOMBRE,
    FIRMA_ROL,
    GRIS_FONDO,
    GRUPO,
    GRUPO_DER,
    LOGO_MAX,
    NOTA_TABLA,
    SIN_PADDING,
    SON,
    TEXTO,
    VACIO,
    _cantidad,
    _como_decimal,
    _Documento,
    _money,
    _nombre_persona,
    _tabla_totales,
    _texto,
    monto_en_letras,
)

from .pdf_inspeccion import (
    CIERRE,
    ESPACIO_FASE,
    _caja_vacia,
    _EncabezadoFase,
    _fecha_hora,
    _km,
    _plural,
    filas_de_fotos,
)

# Servicio aún sin terminar: ámbar, igual que «Pendiente» en la web.
COLOR_PENDIENTE = '#B45309'

ESTADOS_CERRADOS = ('COMPLETADO', 'ENTREGADO')


def _inspeccion_de(orden):
    try:
        return orden.inspeccion
    except ObjectDoesNotExist:
        return None


# ---------------------------------------------------------------------------
# DOCUMENTO: CABECERA, DATOS Y PÁGINA (heredados de la cotización)
# ---------------------------------------------------------------------------

class _DocumentoOrden(_Documento):
    TIPO = 'ORDEN DE TRABAJO'

    def __init__(self, orden, empresa, taller, usuario, logo):  # noqa: super no aplica
        self.orden = orden
        self.empresa = empresa
        self.taller = taller
        self.identidad = _datos_identidad(empresa, taller)
        self.usuario = (usuario or '').strip()
        self.generado = timezone.localtime()
        self.numero = orden.numero_orden or ''

        if logo is None:
            logo = _coincidir_logo(empresa, taller)
        self.logo_bin, self.logo_ancho, self.logo_alto = _leer_logo(logo, LOGO_MAX * 2.2, LOGO_MAX)

        self.servicios = list(orden.servicios.all())
        self.repuestos = list(orden.repuestos.all())
        self.fotos = [f for f in orden.fotos.all() if f.imagen or f.miniatura]
        self.hechos = sum(1 for s in self.servicios if s.completado)
        self.horas = sum((_como_decimal(s.horas_aplicadas) for s in self.servicios), Decimal('0'))

        self.recepcion = next(iter(orden.recepciones.all()), None)
        self.inspeccion = _inspeccion_de(orden)
        self.cotizacion = orden.cotizacion_origen

    def meta(self) -> list:
        o = self.orden
        meta = [f'Ingreso: {_fecha_hora(o.fecha_ingreso)}'] if o.fecha_ingreso else []
        if o.fecha_entrega:
            etiqueta = 'Entrega' if o.estado == 'ENTREGADO' else 'Entrega prevista'
            meta.append(f'{etiqueta}: {_fecha_hora(o.fecha_entrega)}')
        else:
            meta.append(f'Estado: {o.get_estado_display()}')
        return meta

    def datos(self, ancho: float) -> Table:
        o = self.orden
        cliente = o.cliente
        tipo_id = ''
        if cliente is not None and hasattr(cliente, 'get_tipo_identificacion_display'):
            tipo_id = cliente.get_tipo_identificacion_display()
        filas_cliente = [
            (tipo_id or 'Identificación', getattr(cliente, 'identificacion', '')),
            ('Teléfono', getattr(cliente, 'telefono', '')),
            ('Correo', getattr(cliente, 'email', '')),
            ('Dirección', getattr(cliente, 'direccion', '')),
        ]

        vehiculo = o.vehiculo
        descripcion = ' '.join(
            str(p) for p in (vehiculo.marca, vehiculo.modelo, getattr(vehiculo, 'anio', '')) if p
        )
        filas_vehiculo = [
            ('Vehículo', descripcion),
            ('Color', getattr(vehiculo, 'color', '')),
            ('VIN', getattr(vehiculo, 'vin', '')),
            ('Kilometraje', _km(o.kilometraje or getattr(vehiculo, 'kilometraje_actual', None))),
        ]

        estado = o.get_estado_display()
        if o.estado == 'EN_ESPERA' and o.motivo_espera:
            estado += f' · {o.motivo_espera}'
        filas_doc = [
            ('Tipo', o.get_tipo_trabajo_display()),
            ('Estado', estado),
            ('Mecánico', _nombre_persona(o.mecanico_principal)),
            ('Asesor', _nombre_persona(o.asesor)),
            ('Recepción', getattr(self.recepcion, 'numero_recepcion', '') or ''),
            ('Inspección', getattr(self.inspeccion, 'numero_inspeccion', '') or ''),
            ('Cotización', getattr(self.cotizacion, 'numero_cotizacion', '') or ''),
        ]

        separacion = 12
        anchos = [ancho * 0.36, ancho * 0.32, ancho * 0.32]
        internos = [a - separacion * 2 for a in anchos]
        celdas = [
            self._bloque('CLIENTE', getattr(cliente, 'nombre', ''), filas_cliente, internos[0]),
            self._bloque('VEHÍCULO', vehiculo.placa or '', filas_vehiculo, internos[1]),
            self._bloque('ORDEN DE TRABAJO', getattr(self.taller, 'nombre', '') or 'Datos del documento',
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

    # -- resúmenes de cada sección ----------------------------------------

    def resumen_trabajo(self) -> str:
        partes = []
        if self.servicios:
            partes.append(f'{self.hechos} de {_plural(len(self.servicios), "servicio hecho", "servicios hechos")}')
        if self.repuestos:
            partes.append(_plural(len(self.repuestos), 'repuesto', 'repuestos'))
        return ' · '.join(partes) or 'Sin ítems'

    def resumen_evidencia(self) -> str:
        return _plural(len(self.fotos), 'foto', 'fotos') if self.fotos else 'Sin fotos'


# ---------------------------------------------------------------------------
# SECCIONES
# ---------------------------------------------------------------------------

def _descripcion_servicio(servicio) -> str:
    """Descripción + línea gris con el técnico y si el servicio ya se hizo."""
    texto = _texto(servicio.descripcion) or '—'
    detalle = []
    tecnico = _nombre_persona(servicio.mecanico_asignado)
    if tecnico:
        detalle.append(_escape_xml(tecnico))
    if servicio.completado:
        detalle.append('Hecho')
    else:
        detalle.append(f'<font color="{COLOR_PENDIENTE}">Pendiente</font>')
    return f'{texto}<br/><font color="#6B7280" size="7">{" · ".join(detalle)}</font>'


def _tabla_trabajo(doc: _DocumentoOrden, ancho: float) -> list:
    """Tabla agrupada en «Mano de obra» y «Repuestos», cada grupo con su
    subtotal. Mismas columnas que la cotización; el total de línea va sin IVA."""
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
    filas = [[Paragraph(t.upper(), COLUMNA_DER if al == 'R' else COLUMNA) for t, _, al in columnas]]
    comandos = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (0, -1), 2),
        ('RIGHTPADDING', (-1, 0), (-1, -1), 2),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, NEGRO),
    ]

    grupos = [
        ('Mano de obra', doc.servicios, 'servicio_codigo', 'horas_aplicadas', _descripcion_servicio),
        ('Repuestos', doc.repuestos, 'codigo_repuesto', 'cantidad', lambda r: _texto(r.descripcion) or '—'),
    ]
    numero = 0
    for titulo, items, campo_codigo, campo_cantidad, descripcion in grupos:
        if not items:
            continue
        subtotal = sum((_como_decimal(i.subtotal) for i in items), Decimal('0'))
        fila_grupo = len(filas)
        filas.append([Paragraph(titulo.upper(), GRUPO)] + [''] * 5 + [Paragraph(_money(subtotal), GRUPO_DER)])
        comandos += [
            ('SPAN', (0, fila_grupo), (5, fila_grupo)),
            ('BACKGROUND', (0, fila_grupo), (-1, fila_grupo), GRIS_FONDO),
            ('TOPPADDING', (0, fila_grupo), (-1, fila_grupo), 4),
            ('BOTTOMPADDING', (0, fila_grupo), (-1, fila_grupo), 4),
        ]
        for item in items:
            numero += 1
            descuento = _como_decimal(item.descuento)
            filas.append([
                Paragraph(str(numero), CELDA_GRIS),
                Paragraph(_escape_xml(getattr(item, campo_codigo, '') or '') or '—', CELDA_GRIS),
                Paragraph(descripcion(item), CELDA),
                Paragraph(_cantidad(getattr(item, campo_cantidad)), CELDA_DER),
                Paragraph(_money(item.precio_unitario), CELDA_DER),
                Paragraph(f'-{_money(descuento)}' if descuento > 0 else '—', CELDA_DER),
                Paragraph(_money(item.subtotal), CELDA_TOTAL),
            ])
            if item is not items[-1]:
                comandos.append(('LINEBELOW', (0, len(filas) - 1), (-1, len(filas) - 1), 0.3, LST_GRIS_FILA))
    comandos.append(('LINEBELOW', (0, -1), (-1, -1), 0.8, NEGRO))

    tabla = Table(filas, colWidths=anchos, repeatRows=1)
    tabla.setStyle(TableStyle(comandos))
    flujo = [tabla]
    if doc.servicios:
        flujo += [Spacer(1, 3), Paragraph(
            'En mano de obra, la cantidad corresponde a horas aplicadas. '
            'Los totales por línea no incluyen IVA.', NOTA_TABLA,
        )]
    return flujo


def _resumen_valores(doc: _DocumentoOrden, ancho: float) -> Table:
    """Son y notas para el cliente a la izquierda; totales a la derecha."""
    o = doc.orden
    # Los totales de la cotización leen ``total_iva``; la orden lo llama ``monto_iva``.
    valores = SimpleNamespace(
        subtotal_neto=o.subtotal_neto, descuento=o.descuento, subtotal_base_0=o.subtotal_base_0,
        subtotal_base_gravada=o.subtotal_base_gravada, total_iva=o.monto_iva, total=o.total,
    )
    tasas = {_como_decimal(i.iva_porcentaje) for i in doc.servicios + doc.repuestos}

    ancho_totales = min(ancho * 0.40, 220)
    ancho_texto = ancho - ancho_totales - 24
    izquierda = [
        Paragraph('SON', BLOQUE_TITULO), Spacer(1, 2),
        Paragraph(_escape_xml(monto_en_letras(o.total)), SON),
    ]
    notas = _texto(o.observaciones)
    if notas:
        izquierda += [Spacer(1, 8), Paragraph('NOTAS PARA EL CLIENTE', BLOQUE_TITULO), Spacer(1, 2),
                      Paragraph(notas, TEXTO)]
    resumen = Table(
        [[izquierda, '', _tabla_totales(valores, tasas, ancho_totales)]],
        colWidths=[ancho_texto, 24, ancho_totales],
    )
    resumen.setStyle(TableStyle(SIN_PADDING + [('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    return resumen


def _seccion_trabajo(doc: _DocumentoOrden, ancho: float) -> list:
    encabezado = _EncabezadoFase(1, 'Trabajo', doc.resumen_trabajo(), ancho)
    if not (doc.servicios or doc.repuestos):
        return [KeepTogether([
            encabezado, Spacer(1, 9), _caja_vacia('Esta orden aún no tiene servicios ni repuestos.', ancho),
        ])]
    return [encabezado, Spacer(1, 9), *_tabla_trabajo(doc, ancho), Spacer(1, 12), _resumen_valores(doc, ancho)]


def _seccion_evidencia(doc: _DocumentoOrden, ancho: float) -> list:
    encabezado = _EncabezadoFase(2, 'Evidencia del trabajo', doc.resumen_evidencia(), ancho)
    if not doc.fotos:
        return [KeepTogether([
            encabezado, Spacer(1, 9), _caja_vacia('No se registraron fotos del trabajo.', ancho), Spacer(1, 10),
        ])]
    flujo = [encabezado, Spacer(1, 9), *filas_de_fotos(doc.fotos, ancho)]
    # El encabezado no se queda solo al pie de una página: viaja con la 1.ª fila.
    return [KeepTogether(flujo[:3]), *flujo[3:]]


def _cierre(doc: _DocumentoOrden, ancho: float) -> list:
    """Condición y firmas: cierran el documento, después de la evidencia."""
    cliente = getattr(doc.orden.cliente, 'nombre', '') or ''
    asesor = _nombre_persona(doc.orden.asesor)
    ancho_firma = min(ancho * 0.36, 190)
    hueco = ancho - 2 * ancho_firma

    def celda(nombre, rol):
        return [Paragraph(_escape_xml(nombre) or '&nbsp;', FIRMA_NOMBRE), Paragraph(rol, FIRMA_ROL)]

    cerrada = doc.orden.estado in ESTADOS_CERRADOS
    rol_cliente = 'Recibí conforme el vehículo' if cerrada else 'Autorizo el trabajo'
    firmas = Table(
        [[celda(asesor, 'Asesor de servicio'), '', celda(cliente, rol_cliente)]],
        colWidths=[ancho_firma, hueco, ancho_firma],
    )
    firmas.setStyle(TableStyle(SIN_PADDING + [
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('LINEABOVE', (0, 0), (0, 0), 0.6, NEGRO),
        ('LINEABOVE', (2, 0), (2, 0), 0.6, NEGRO),
    ]))
    if cerrada:
        texto = 'Con su firma, el cliente declara recibir el vehículo con el trabajo detallado en esta orden.'
    else:
        texto = 'Ningún trabajo adicional a los detallados en esta orden se realiza sin la aprobación del cliente.'
    return [KeepTogether([Paragraph(texto, CIERRE), Spacer(1, 34), firmas])]


# ---------------------------------------------------------------------------
# API PÚBLICA
# ---------------------------------------------------------------------------

def exportar_orden_pdf(
    buffer: BytesIO,
    orden,
    *,
    empresa=None,
    taller=None,
    usuario: str = '',
    logo=None,
) -> None:
    """Genera el PDF de UNA orden de trabajo en ``buffer``."""
    doc_datos = _DocumentoOrden(orden, empresa, taller, usuario, logo)
    ancho = PAGINA[0] - 2 * LST_MARGEN_LATERAL
    alto_util = PAGINA[1] - LST_MARGEN_SUPERIOR - LST_MARGEN_INFERIOR - ALTO_PIE

    doc = BaseDocTemplate(
        buffer,
        pagesize=PAGINA,
        leftMargin=LST_MARGEN_LATERAL,
        rightMargin=LST_MARGEN_LATERAL,
        topMargin=LST_MARGEN_SUPERIOR,
        bottomMargin=LST_MARGEN_INFERIOR,
        title=f'Orden de trabajo {doc_datos.numero}'.strip(),
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
        *_seccion_trabajo(doc_datos, ancho),
        Spacer(1, ESPACIO_FASE),
        *_seccion_evidencia(doc_datos, ancho),
        Spacer(1, ESPACIO_FASE * 1.4),
        *_cierre(doc_datos, ancho),
    ]
    doc.build(story, canvasmaker=NumberedCanvas)

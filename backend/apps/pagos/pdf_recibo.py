"""
PDF del recibo de UN movimiento de cobro (pago o devolución) de una orden.

Misma línea gráfica que la orden de trabajo (``apps/ordenes/pdf_orden.py``):
cabecera con logo e identidad, bloques cliente · vehículo · orden, filetes
negros y divisorias grises. Una sola página:

  ┌──────────────────────────────────────────────────────────────────┐
  │ [logo] EMPRESA                                  RECIBO DE PAGO    │
  │        RUC · Taller · Dirección · Telf.             Nº ABC123     │
  ├──────────────────────────────────────────────────────────────────┤
  │ CLIENTE            │ VEHÍCULO            │ ORDEN DE TRABAJO       │
  ├──────────────────────────────────────────────────────────────────┤
  │ Concepto · forma de pago · comprobante · registrado por   MONTO   │
  │ Son …                          Total orden · Pagado · Saldo       │
  │ ___________________                       ___________________     │
  └──────────────────────────────────────────────────────────────────┘
Un movimiento anulado lleva la marca «ANULADO» cruzada y el motivo.
"""

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

from apps.core.ids_publicos import referencia_corta
from apps.core.utils.pdf_export import (
    LST_GRIS_FILA,
    LST_MARGEN_INFERIOR,
    LST_MARGEN_LATERAL,
    LST_MARGEN_SUPERIOR,
    NEGRO,
    PAGINA,
    NumberedCanvas,
    _escape_xml,
)
from apps.cotizaciones.pdf import (
    ALTO_PIE,
    BLOQUE_TITULO,
    CELDA,
    CELDA_DER,
    CELDA_TOTAL,
    COLUMNA,
    COLUMNA_DER,
    FIRMA_NOMBRE,
    FIRMA_ROL,
    SIN_PADDING,
    SON,
    TEXTO,
    _money,
    _nombre_persona,
    monto_en_letras,
)
from apps.ordenes.pdf_inspeccion import ESPACIO_FASE, _fecha_hora, _km
from apps.ordenes.pdf_orden import _DocumentoOrden

ROJO_ANULADO = colors.HexColor('#B91C1C')


class _DocumentoRecibo(_DocumentoOrden):
    """Reutiliza la cabecera, el pie y los bloques de la orden; cambia el título."""

    def __init__(self, pago, estado, empresa, taller, usuario, logo):
        super().__init__(pago.orden_trabajo, empresa, taller, usuario, logo)
        self.pago = pago
        self.estado = estado
        self.TIPO = 'RECIBO DE PAGO' if pago.tipo == 'PAGO' else 'COMPROBANTE DE DEVOLUCIÓN'
        self.numero = referencia_corta(pago).lstrip('#')

    def meta(self) -> list:
        meta = [f'Fecha: {_fecha_hora(self.pago.fecha)}', f'Orden: {self.orden.numero_orden}']
        if self.pago.anulado:
            meta.append('ANULADO')
        return meta

    def datos(self, ancho: float) -> Table:
        o = self.orden
        cliente = o.cliente
        tipo_id = cliente.get_tipo_identificacion_display() if hasattr(cliente, 'get_tipo_identificacion_display') else ''
        filas_cliente = [
            (tipo_id or 'Identificación', getattr(cliente, 'identificacion', '')),
            ('Teléfono', getattr(cliente, 'telefono', '')),
            ('Correo', getattr(cliente, 'email', '')),
        ]
        vehiculo = o.vehiculo
        descripcion = ' '.join(str(p) for p in (vehiculo.marca, vehiculo.modelo, getattr(vehiculo, 'anio', '')) if p)
        filas_vehiculo = [('Vehículo', descripcion), ('Kilometraje', _km(o.kilometraje))]
        filas_orden = [
            ('Número', o.numero_orden),
            ('Estado', o.get_estado_display()),
            ('Comprobante', f'{o.get_tipo_comprobante_display()} {o.numero_factura}' if o.numero_factura else ''),
        ]
        separacion = 12
        anchos = [ancho * 0.36, ancho * 0.32, ancho * 0.32]
        internos = [a - separacion * 2 for a in anchos]
        tabla = Table([[
            self._bloque('CLIENTE', getattr(cliente, 'nombre', ''), filas_cliente, internos[0]),
            self._bloque('VEHÍCULO', vehiculo.placa or '', filas_vehiculo, internos[1]),
            self._bloque('ORDEN DE TRABAJO', getattr(self.taller, 'nombre', '') or '', filas_orden, internos[2]),
        ]], colWidths=anchos)
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

    def dibujar_primera(self, canvas, doc) -> None:
        super().dibujar_primera(canvas, doc)
        if self.pago.anulado:
            canvas.saveState()
            canvas.setFillColor(ROJO_ANULADO)
            canvas.setFillAlpha(0.15)
            canvas.setFont('Helvetica-Bold', 90)
            canvas.translate(doc.pagesize[0] / 2, doc.pagesize[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, 'ANULADO')
            canvas.restoreState()


def _detalle(doc: _DocumentoRecibo, ancho: float) -> Table:
    pago = doc.pago
    concepto = 'Devolución al cliente' if pago.tipo == 'DEVOLUCION' else (
        'Anticipo' if pago.es_anticipo else 'Pago'
    )
    concepto += f' de la orden {doc.orden.numero_orden}'
    filas = [
        [Paragraph('CONCEPTO', COLUMNA), Paragraph('FORMA DE PAGO', COLUMNA),
         Paragraph('COMPROBANTE', COLUMNA), Paragraph('MONTO', COLUMNA_DER)],
        [Paragraph(_escape_xml(concepto), CELDA),
         Paragraph(_escape_xml(pago.get_forma_pago_display()), CELDA),
         Paragraph(_escape_xml(pago.referencia) or '—', CELDA),
         Paragraph(_money(pago.monto), CELDA_TOTAL)],
    ]
    if pago.observacion:
        filas.append([Paragraph(f'<b>Observación:</b> {_escape_xml(pago.observacion)}', TEXTO), '', '', ''])
    tabla = Table(filas, colWidths=[ancho * 0.40, ancho * 0.20, ancho * 0.22, ancho * 0.18])
    comandos = SIN_PADDING + [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, NEGRO),
        ('LINEBELOW', (0, -1), (-1, -1), 0.8, NEGRO),
    ]
    if pago.observacion:
        comandos.append(('SPAN', (0, 2), (-1, 2)))
    tabla.setStyle(TableStyle(comandos))
    return tabla


def _resumen(doc: _DocumentoRecibo, ancho: float) -> Table:
    o = doc.orden
    pago = doc.pago
    ancho_totales = min(ancho * 0.42, 230)
    izquierda = [
        Paragraph('SON', BLOQUE_TITULO), Spacer(1, 2),
        Paragraph(_escape_xml(monto_en_letras(pago.monto)), SON), Spacer(1, 8),
        Paragraph(f'Registrado por {_escape_xml(_nombre_persona(pago.registrado_por) or "—")}.', TEXTO),
    ]
    if pago.anulado:
        motivo = _escape_xml(pago.motivo_anulacion or '')
        quien = _escape_xml(_nombre_persona(pago.anulado_por) or '—')
        cuando = _fecha_hora(pago.fecha_anulacion) if pago.fecha_anulacion else ''
        izquierda += [Spacer(1, 6), Paragraph(
            f'<font color="#B91C1C"><b>ANULADO</b> {cuando} por {quien}: {motivo}</font>', TEXTO,
        )]
    totales = Table([
        [Paragraph('Total de la orden', CELDA), Paragraph(_money(o.total), CELDA_DER)],
        [Paragraph('Pagado por el cliente', CELDA), Paragraph(_money(doc.estado['pagado_neto']), CELDA_DER)],
        [Paragraph('<b>Saldo pendiente</b>', CELDA), Paragraph(_money(doc.estado['saldo']), CELDA_TOTAL)],
    ], colWidths=[ancho_totales * 0.6, ancho_totales * 0.4])
    totales.setStyle(TableStyle(SIN_PADDING + [
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LINEABOVE', (0, -1), (-1, -1), 0.8, NEGRO),
    ]))
    nota = Paragraph('Valores de la orden después de este movimiento.', TEXTO)
    tabla = Table([[izquierda, '', [totales, Spacer(1, 3), nota]]],
                  colWidths=[ancho - ancho_totales - 24, 24, ancho_totales])
    tabla.setStyle(TableStyle(SIN_PADDING + [('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    return tabla


def _firmas(doc: _DocumentoRecibo, ancho: float) -> list:
    ancho_firma = min(ancho * 0.36, 190)

    def celda(nombre, rol):
        return [Paragraph(_escape_xml(nombre) or '&nbsp;', FIRMA_NOMBRE), Paragraph(rol, FIRMA_ROL)]

    entrega = doc.pago.tipo == 'DEVOLUCION'
    firmas = Table([[
        celda(_nombre_persona(doc.pago.registrado_por), 'Entregué conforme' if entrega else 'Recibí conforme'),
        '',
        celda(getattr(doc.orden.cliente, 'nombre', ''), 'Recibí conforme' if entrega else 'Cliente'),
    ]], colWidths=[ancho_firma, ancho - 2 * ancho_firma, ancho_firma])
    firmas.setStyle(TableStyle(SIN_PADDING + [
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('LINEABOVE', (0, 0), (0, 0), 0.6, NEGRO),
        ('LINEABOVE', (2, 0), (2, 0), 0.6, NEGRO),
    ]))
    return [KeepTogether([Spacer(1, 40), firmas])]


def exportar_recibo_pdf(buffer: BytesIO, pago, estado, *, usuario: str = '', logo=None) -> None:
    """Genera en ``buffer`` el recibo de ``pago``; ``estado`` = pagado neto y saldo tras él."""
    orden = pago.orden_trabajo
    doc_datos = _DocumentoRecibo(pago, estado, orden.empresa, orden.sucursal, usuario, logo)
    ancho = PAGINA[0] - 2 * LST_MARGEN_LATERAL
    alto_util = PAGINA[1] - LST_MARGEN_SUPERIOR - LST_MARGEN_INFERIOR - ALTO_PIE
    doc = BaseDocTemplate(
        buffer,
        pagesize=PAGINA,
        leftMargin=LST_MARGEN_LATERAL,
        rightMargin=LST_MARGEN_LATERAL,
        topMargin=LST_MARGEN_SUPERIOR,
        bottomMargin=LST_MARGEN_INFERIOR,
        title=f'{doc_datos.TIPO.capitalize()} {doc_datos.numero}',
        author=doc_datos.identidad['nombre'],
    )
    sin_padding = dict(leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(
        id='p1',
        frames=[Frame(LST_MARGEN_LATERAL, LST_MARGEN_INFERIOR + ALTO_PIE, ancho, alto_util, id='p1', **sin_padding)],
        onPage=doc_datos.dibujar_primera,
    )])
    doc.build([
        doc_datos.cabecera(ancho),
        doc_datos.datos(ancho),
        Spacer(1, ESPACIO_FASE),
        _detalle(doc_datos, ancho),
        Spacer(1, 12),
        _resumen(doc_datos, ancho),
        *_firmas(doc_datos, ancho),
    ], canvasmaker=NumberedCanvas)

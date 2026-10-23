"""PDF del cierre de caja: cabecera estándar, resumen del efectivo y detalle de
los movimientos de la jornada (misma línea gráfica que los listados)."""

from io import BytesIO

from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import NextPageTemplate, Paragraph, Spacer, Table, TableStyle

from apps.core.utils.pdf_export import (
    LST_GRIS_FILA,
    NEGRO,
    NumberedCanvas,
    _construir_tabla_listado,
    _crear_documento,
    _normalizar_columnas,
    celda_texto,
    estilo_celda_listado,
    fecha_hora_local,
)

from . import services

COLUMNAS = [
    ('Hora', 1.0), ('Movimiento', 1.6), ('Orden / motivo', 2.6), ('Forma', 1.2), ('Monto', 1.0, 'R'), ('Estado', 0.9),
]


def _dinero(valor):
    return f'$ {valor:,.2f}' if valor is not None else '-'


def _usuario(user):
    return (user.get_full_name() or user.username) if user else '-'


def exportar_cierre_pdf(sesion, request):
    from apps.pagos.models import PagoOrden

    caja = sesion.caja
    resumen = services.resumen(sesion)
    buffer = BytesIO()
    usuario = getattr(request.user, 'username', '') or ''
    doc, ancho = _crear_documento(
        buffer, f'Cierre de caja - {fecha_hora_local(sesion.fecha_apertura, "%d/%m/%Y")}',
        empresa=caja.taller.empresa, taller=caja.taller, usuario=usuario,
    )
    estilo = estilo_celda_listado()
    titulo = getSampleStyleSheet()['Heading4']

    filas_resumen = [
        ('Caja', f'{caja.nombre} · {caja.taller.nombre} · {sesion.get_estado_display()}'),
        ('Apertura', f'{fecha_hora_local(sesion.fecha_apertura)} por {_usuario(sesion.abierta_por)}'),
        ('Cierre', f'{fecha_hora_local(sesion.fecha_cierre)} por {_usuario(sesion.cerrada_por)}' if sesion.fecha_cierre else '-'),
        ('Monto inicial', _dinero(resumen['monto_inicial'])),
    ]
    for fila in resumen['por_forma']:
        texto = _dinero(fila['pagos'])
        if fila['devoluciones']:
            texto += f'  (devoluciones {_dinero(fila["devoluciones"])})'
        filas_resumen.append((f'Cobrado en {fila["forma_pago_display"].lower()}', texto))
    filas_resumen += [
        ('Ingresos de efectivo', _dinero(resumen['ingresos'])),
        ('Egresos de efectivo', _dinero(resumen['egresos'])),
        ('Efectivo esperado', _dinero(sesion.efectivo_esperado if sesion.efectivo_esperado is not None else resumen['efectivo_esperado'])),
        ('Efectivo contado', _dinero(sesion.efectivo_contado)),
        ('Diferencia', _dinero(sesion.diferencia)),
    ]
    if sesion.observacion_cierre:
        filas_resumen.append(('Observación', sesion.observacion_cierre))
    if sesion.reaperturas:
        filas_resumen.append(('Reaperturas', f'{sesion.reaperturas} (última: {sesion.motivo_reapertura})'))

    tabla_resumen = Table(
        [[celda_texto(etiqueta, estilo), celda_texto(valor, estilo)] for etiqueta, valor in filas_resumen],
        colWidths=[ancho * 0.32, ancho * 0.68],
    )
    tabla_resumen.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (-1, 0), 0.8, NEGRO),
        ('LINEBELOW', (0, -1), (-1, -1), 0.8, NEGRO),
        ('LINEBELOW', (0, 0), (-1, -2), 0.3, LST_GRIS_FILA),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))

    filas = []
    pagos = sesion.pagos.select_related('orden_trabajo__cliente').order_by('fecha', 'id')
    for pago in pagos:
        signo = '-' if pago.tipo == PagoOrden.Tipo.DEVOLUCION else ''
        filas.append([
            fecha_hora_local(pago.fecha, '%H:%M'),
            pago.get_tipo_display(),
            f'{pago.orden_trabajo.numero_orden} · {pago.orden_trabajo.cliente.nombre}',
            pago.get_forma_pago_display(),
            f'{signo}{_dinero(pago.monto)}',
            'Anulado' if pago.anulado else '',
        ])
    for mov in sesion.movimientos.order_by('fecha', 'id'):
        signo = '-' if mov.tipo == mov.Tipo.EGRESO else ''
        filas.append([
            fecha_hora_local(mov.fecha, '%H:%M'), mov.get_tipo_display(), mov.motivo, 'Efectivo',
            f'{signo}{_dinero(mov.monto)}', 'Anulado' if mov.anulado else '',
        ])
    filas.sort(key=lambda fila: fila[0])
    filas = [[celda_texto(valor, estilo) for valor in fila] for fila in filas]

    story = [NextPageTemplate('cn'), tabla_resumen, Spacer(1, 14), Paragraph('Movimientos de la jornada', titulo)]
    if filas:
        story.append(_construir_tabla_listado(_normalizar_columnas(COLUMNAS), filas, ancho))
    else:
        story.append(Paragraph('Sin movimientos.', estilo))
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer

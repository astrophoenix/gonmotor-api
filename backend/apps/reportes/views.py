import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.authentication.permissions import TienePermiso
from apps.authentication.utils import get_empresa_id_desde_request, talleres_filtro_de_request
from apps.gastos.models import Gasto
from apps.ordenes.models import (
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioOrdenTrabajo,
    OrdenTrabajo,
)


def _parse_report_filters(request):
    fecha_desde = request.query_params.get('fecha_desde') or None
    fecha_hasta = request.query_params.get('fecha_hasta') or None
    taller_id = request.query_params.get('taller_id') or None

    def fecha_valida(valor):
        try:
            return parse_date(valor) if valor else None
        except ValueError:
            return None

    parsed_desde = fecha_valida(fecha_desde)
    parsed_hasta = fecha_valida(fecha_hasta)
    if fecha_desde and parsed_desde is None:
        return None, Response({'error': 'fecha_desde debe tener formato YYYY-MM-DD'}, status=400)
    if fecha_hasta and parsed_hasta is None:
        return None, Response({'error': 'fecha_hasta debe tener formato YYYY-MM-DD'}, status=400)
    if parsed_desde and parsed_hasta and parsed_desde > parsed_hasta:
        return None, Response({'error': 'fecha_desde no puede ser posterior a fecha_hasta'}, status=400)

    # ID público del taller (UUID); el alcance lo valida `talleres_filtro_de_request`.
    parsed_taller_id = None
    if taller_id:
        try:
            parsed_taller_id = str(uuid.UUID(taller_id))
        except ValueError:
            return None, Response({'error': 'taller_id no es un identificador válido'}, status=400)

    return {
        'fecha_desde': fecha_desde,
        'fecha_hasta': fecha_hasta,
        'parsed_desde': parsed_desde,
        'parsed_hasta': parsed_hasta,
        'taller_id': taller_id,
        'parsed_taller_id': parsed_taller_id,
    }, None


MESES_EVOLUCION = 12
MESES_CORTOS = ('Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic')
CERO = Decimal('0.00')


def _talleres_alcance(request, empresa_id, taller_id):
    """Talleres a los que se recorta el reporte (misma política que el resto de la API).

    `taller_id` (filtro visible) manda; sin él se usa el taller activo
    (`X-Taller-ID`). Admin en «Todos» → ``None`` (toda la empresa); el resto
    de roles queda siempre dentro de sus talleres permitidos.
    """
    return talleres_filtro_de_request(request, empresa_id=empresa_id, explicito=taller_id)


def _bases(empresa_id, talleres):
    """OT entregadas y gastos activos de la empresa, recortados por taller (sin fechas)."""
    ots = OrdenTrabajo.objects.filter(
        empresa_id=empresa_id,
        is_active=True,
        estado=OrdenTrabajo.EstadoOrden.ENTREGADO,
    )
    gastos = Gasto.objects.filter(empresa_id=empresa_id, is_active=True)
    if talleres is not None:
        ots = ots.filter(sucursal_id__in=talleres)
        gastos = gastos.filter(taller_id__in=talleres)
    return ots, gastos


def _en_periodo(ots, gastos, fecha_desde, fecha_hasta):
    # OT por fecha de entrega; gastos por su fecha.
    if fecha_desde:
        ots = ots.filter(fecha_entrega__date__gte=fecha_desde)
        gastos = gastos.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        ots = ots.filter(fecha_entrega__date__lte=fecha_hasta)
        gastos = gastos.filter(fecha__lte=fecha_hasta)
    return ots, gastos


def _get_rentabilidad(empresa_id, talleres, fecha_desde, fecha_hasta):
    ots, gastos = _en_periodo(*_bases(empresa_id, talleres), fecha_desde, fecha_hasta)

    # subtotal_neto excludes IVA; the line costs are snapshots captured when
    # the order was entered, so later catalog price changes do not rewrite profit.
    ingresos = ots.aggregate(total=Sum('subtotal_neto'))['total'] or CERO
    ordenes = ots.values('pk')

    cmv_rep = DetalleRepuestoOrdenTrabajo.objects.filter(
        orden_trabajo_id__in=ordenes, aprobado=True,
    ).aggregate(total=Sum('costo_total'))['total'] or CERO
    cmv_serv = DetalleServicioOrdenTrabajo.objects.filter(
        orden_trabajo_id__in=ordenes, aprobado=True,
    ).aggregate(total=Sum('costo_total'))['total'] or CERO
    total_gastos = gastos.aggregate(total=Sum('monto'))['total'] or CERO

    total_cmv = cmv_rep + cmv_serv
    margen_bruto = ingresos - total_cmv
    utilidad_neta = margen_bruto - total_gastos

    return {
        'ingresos': {'total': ingresos},
        'costos': {
            'repuestos_vendidos': cmv_rep,
            'servicios': cmv_serv,
            'total_cmv': total_cmv,
        },
        'margen_bruto': margen_bruto,
        'gastos': {'total': total_gastos},
        'utilidad_neta': utilidad_neta,
    }


def _mes(valor):
    """Normaliza el resultado de `TruncMonth` (date o datetime aware) a `date`."""
    if isinstance(valor, datetime):
        return timezone.localtime(valor).date() if timezone.is_aware(valor) else valor.date()
    return valor


def _restar_meses(fecha, cantidad):
    indice = fecha.year * 12 + (fecha.month - 1) - cantidad
    return date(indice // 12, indice % 12 + 1, 1)


def _por_mes(queryset, campo_fecha, campo_suma):
    return {
        _mes(fila['mes']): fila['total'] or CERO
        for fila in queryset.annotate(mes=TruncMonth(campo_fecha))
        .values('mes').annotate(total=Sum(campo_suma))
        if fila['mes']
    }


def _get_evolucion_mensual(empresa_id, talleres, fecha_hasta):
    """Últimos `MESES_EVOLUCION` meses hasta `fecha_hasta` (o hoy), agregados por mes.

    Mismos criterios que los totales: OT entregadas por fecha de entrega
    (ingresos = `subtotal_neto`, costos = `costo_total` de sus líneas) y gastos
    por fecha. Los meses sin movimiento se devuelven en 0.

    Con «Hasta» el último mes se corta en esa fecha (igual que los totales); sin
    ella los totales no tienen tope, así que el último mes se toma completo.
    """
    hasta = fecha_hasta or timezone.localdate()
    inicio = _restar_meses(hasta.replace(day=1), MESES_EVOLUCION - 1)
    fin = fecha_hasta or (_restar_meses(hasta.replace(day=1), -1) - timedelta(days=1))
    ots, gastos = _en_periodo(*_bases(empresa_id, talleres), inicio, fin)
    ordenes = ots.values('pk')

    ingresos = _por_mes(ots, 'fecha_entrega', 'subtotal_neto')
    costos_rep = _por_mes(
        DetalleRepuestoOrdenTrabajo.objects.filter(orden_trabajo_id__in=ordenes, aprobado=True),
        'orden_trabajo__fecha_entrega', 'costo_total',
    )
    costos_serv = _por_mes(
        DetalleServicioOrdenTrabajo.objects.filter(orden_trabajo_id__in=ordenes, aprobado=True),
        'orden_trabajo__fecha_entrega', 'costo_total',
    )
    gastos_mes = _por_mes(gastos, 'fecha', 'monto')

    meses = []
    for indice in range(MESES_EVOLUCION):
        mes = _restar_meses(inicio, -indice)
        ingreso = ingresos.get(mes, CERO)
        costo = costos_rep.get(mes, CERO) + costos_serv.get(mes, CERO)
        gasto = gastos_mes.get(mes, CERO)
        utilidad = ingreso - costo - gasto
        if ingreso:
            margen = round(float(utilidad / ingreso) * 100, 1)
        else:
            # Sin ingresos el margen no existe; un mes vacío se muestra en 0.
            margen = None if (costo or gasto) else 0.0
        meses.append({
            'mes': mes.strftime('%Y-%m'),
            'etiqueta': f'{MESES_CORTOS[mes.month - 1]} {mes.year}',
            'ingresos': ingreso,
            'costos': costo,
            'gastos': gasto,
            'utilidad_neta': utilidad,
            'margen_neto_pct': margen,
        })
    return meses


def _respuesta(request, empresa_id, filters, **extra):
    talleres = _talleres_alcance(request, empresa_id, filters['parsed_taller_id'])
    return Response({
        **extra,
        'periodo': {
            'desde': filters['fecha_desde'],
            'hasta': filters['fecha_hasta'],
        },
        **_get_rentabilidad(empresa_id, talleres, filters['parsed_desde'], filters['parsed_hasta']),
        'evolucion_mensual': _get_evolucion_mensual(empresa_id, talleres, filters['parsed_hasta']),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, TienePermiso('reportes', 'ver')])
def rentabilidad_taller(request):
    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'empresa_id requerido'}, status=400)

    filters, error = _parse_report_filters(request)
    if error:
        return error
    if not filters['taller_id']:
        return Response({'error': 'taller_id requerido'}, status=400)

    return _respuesta(request, empresa_id, filters, taller_id=filters['taller_id'])


@api_view(['GET'])
@permission_classes([IsAuthenticated, TienePermiso('reportes', 'ver')])
def rentabilidad_empresa(request):
    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'empresa_id requerido'}, status=400)

    filters, error = _parse_report_filters(request)
    if error:
        return error

    return _respuesta(request, empresa_id, filters)


# ── Resumen del negocio (RF-07) ───────────────────────────────────────────
def _filtros_resumen(request):
    """Periodo obligatorio en días (por defecto, el mes en curso hasta hoy)."""
    filters, error = _parse_report_filters(request)
    if error:
        return None, error
    hoy = timezone.localdate()
    desde = filters['parsed_desde'] or hoy.replace(day=1)
    hasta = filters['parsed_hasta'] or hoy
    if desde > hasta:
        return None, Response({'error': 'fecha_desde no puede ser posterior a fecha_hasta'}, status=400)
    from .resumen import MAX_DIAS

    if (hasta - desde).days + 1 > MAX_DIAS:
        return None, Response({'error': f'El periodo no puede superar {MAX_DIAS} días.'}, status=400)
    return {**filters, 'desde': desde, 'hasta': hasta}, None


@api_view(['GET'])
@permission_classes([IsAuthenticated, TienePermiso('reportes', 'ver')])
def resumen_negocio(request):
    """Resumen del dueño: facturado, cobrado, por cobrar, gastos y utilidad del
    periodo frente al anterior de igual duración (ver `resumen.py`)."""
    from .resumen import calcular_resumen

    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'empresa_id requerido'}, status=400)
    filters, error = _filtros_resumen(request)
    if error:
        return error
    talleres = _talleres_alcance(request, empresa_id, filters['parsed_taller_id'])
    return Response(calcular_resumen(empresa_id, talleres, filters['desde'], filters['hasta']))


@api_view(['GET'])
@permission_classes([IsAuthenticated, TienePermiso('reportes', 'ver')])
def resumen_negocio_pdf(request):
    from io import BytesIO

    from django.http import FileResponse

    from apps.core.utils.pdf_export import contexto_reporte, generar_listado_pdf

    from .resumen import calcular_resumen

    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'empresa_id requerido'}, status=400)
    filters, error = _filtros_resumen(request)
    if error:
        return error
    talleres = _talleres_alcance(request, empresa_id, filters['parsed_taller_id'])
    datos = calcular_resumen(empresa_id, talleres, filters['desde'], filters['hasta'])

    def dinero(valor):
        return f'$ {Decimal(valor):,.2f}'

    nombres = {
        'facturado': 'Facturado (con IVA)', 'ingresos': 'Ingresos sin IVA', 'cobrado': 'Cobrado',
        'por_cobrar': 'Por cobrar al cierre', 'costo_trabajos': 'Costo de los trabajos', 'gastos': 'Gastos',
        'utilidad': 'Utilidad', 'ticket_promedio': 'Ticket promedio',
        'ordenes_entregadas': 'Órdenes entregadas', 'ordenes_creadas': 'Órdenes creadas',
    }
    filas = []
    for clave, nombre in nombres.items():
        kpi = datos['kpis'][clave]
        formato = str if clave.startswith('ordenes_') else dinero
        pct = f'{kpi["variacion_pct"]:+.1f} %' if kpi['variacion_pct'] is not None else '—'
        filas.append([nombre, formato(kpi['actual']), formato(kpi['anterior']), formato(kpi['variacion']), pct])
    for fila in datos['actual']['cobrado']['por_forma']:
        filas.append([f'Cobrado en {fila["forma_pago_display"].lower()}', dinero(fila['neto']), '', '', ''])
    for fila in datos['actual']['gastos']['por_categoria']:
        filas.append([f'Gasto: {fila["categoria_display"]}', dinero(fila['total']), '', '', ''])

    periodo = datos['periodo']
    anterior = datos['periodo_anterior']
    empresa, taller, usuario = contexto_reporte(request, empresa_id)
    buffer = BytesIO()
    generar_listado_pdf(
        buffer,
        titulo=f'Resumen del negocio {periodo["desde"]} a {periodo["hasta"]}',
        columnas=[
            ('Indicador', 2.4), ('Periodo', 1.3, 'R'), (f'Anterior ({anterior["desde"]} a {anterior["hasta"]})', 1.8, 'R'),
            ('Variación', 1.2, 'R'), ('%', 0.8, 'R'),
        ],
        filas=filas,
        empresa=empresa,
        taller=taller,
        usuario=usuario,
        total_registros=len(filas),
        orientacion='vertical',
    )
    buffer.seek(0)
    return FileResponse(
        buffer, content_type='application/pdf', as_attachment=True,
        filename=f'resumen_negocio_{periodo["desde"]}_{periodo["hasta"]}.pdf',
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, TienePermiso('reportes', 'ver')])
def productividad_mecanicos(request):
    from .productividad import calcular_productividad

    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'No se pudo determinar la empresa activa.'}, status=400)
    filtros, error = _filtros_resumen(request)
    if error:
        return error
    talleres = _talleres_alcance(request, empresa_id, filtros['parsed_taller_id'])
    return Response(calcular_productividad(empresa_id, talleres, filtros['desde'], filtros['hasta']))

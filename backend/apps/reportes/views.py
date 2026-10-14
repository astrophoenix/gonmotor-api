from decimal import Decimal

from django.db.models import Sum
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.authentication.utils import get_empresa_id_desde_request
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

    parsed_desde = parse_date(fecha_desde) if fecha_desde else None
    parsed_hasta = parse_date(fecha_hasta) if fecha_hasta else None
    if fecha_desde and parsed_desde is None:
        return None, Response({'error': 'fecha_desde debe tener formato YYYY-MM-DD'}, status=400)
    if fecha_hasta and parsed_hasta is None:
        return None, Response({'error': 'fecha_hasta debe tener formato YYYY-MM-DD'}, status=400)
    if parsed_desde and parsed_hasta and parsed_desde > parsed_hasta:
        return None, Response({'error': 'fecha_desde no puede ser posterior a fecha_hasta'}, status=400)

    parsed_taller_id = None
    if taller_id:
        try:
            parsed_taller_id = int(taller_id)
        except (TypeError, ValueError):
            return None, Response({'error': 'taller_id debe ser un entero positivo'}, status=400)
        if parsed_taller_id <= 0:
            return None, Response({'error': 'taller_id debe ser un entero positivo'}, status=400)

    return {
        'fecha_desde': fecha_desde,
        'fecha_hasta': fecha_hasta,
        'parsed_desde': parsed_desde,
        'parsed_hasta': parsed_hasta,
        'taller_id': taller_id,
        'parsed_taller_id': parsed_taller_id,
    }, None


def _get_rentabilidad(empresa_id, taller_id, fecha_desde, fecha_hasta):
    ots = OrdenTrabajo.objects.filter(
        empresa_id=empresa_id,
        is_active=True,
        estado=OrdenTrabajo.EstadoOrden.ENTREGADO,
    )
    if taller_id is not None:
        ots = ots.filter(sucursal_id=taller_id)
    if fecha_desde:
        ots = ots.filter(fecha_entrega__date__gte=fecha_desde)
    if fecha_hasta:
        ots = ots.filter(fecha_entrega__date__lte=fecha_hasta)

    # subtotal_neto excludes IVA; the line costs are snapshots captured when
    # the order was entered, so later catalog price changes do not rewrite profit.
    ingresos = ots.aggregate(total=Sum('subtotal_neto'))['total'] or Decimal('0.00')
    ordenes = ots.values('pk')

    cmv_rep = DetalleRepuestoOrdenTrabajo.objects.filter(
        orden_trabajo_id__in=ordenes,
    ).aggregate(total=Sum('costo_total'))['total'] or Decimal('0.00')
    cmv_serv = DetalleServicioOrdenTrabajo.objects.filter(
        orden_trabajo_id__in=ordenes,
    ).aggregate(total=Sum('costo_total'))['total'] or Decimal('0.00')

    gastos = Gasto.objects.filter(empresa_id=empresa_id, is_active=True)
    if taller_id is not None:
        gastos = gastos.filter(taller_id=taller_id)
    if fecha_desde:
        gastos = gastos.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        gastos = gastos.filter(fecha__lte=fecha_hasta)
    total_gastos = gastos.aggregate(total=Sum('monto'))['total'] or Decimal('0.00')

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


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def rentabilidad_taller(request):
    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'empresa_id requerido'}, status=400)

    filters, error = _parse_report_filters(request)
    if error:
        return error
    if not filters['taller_id']:
        return Response({'error': 'taller_id requerido'}, status=400)

    data = _get_rentabilidad(
        empresa_id,
        filters['parsed_taller_id'],
        filters['parsed_desde'],
        filters['parsed_hasta'],
    )
    return Response({
        'taller_id': filters['taller_id'],
        'periodo': {
            'desde': filters['fecha_desde'],
            'hasta': filters['fecha_hasta'],
        },
        **data,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def rentabilidad_empresa(request):
    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'empresa_id requerido'}, status=400)

    filters, error = _parse_report_filters(request)
    if error:
        return error

    data = _get_rentabilidad(
        empresa_id,
        filters['parsed_taller_id'],
        filters['parsed_desde'],
        filters['parsed_hasta'],
    )
    return Response({
        'periodo': {
            'desde': filters['fecha_desde'],
            'hasta': filters['fecha_hasta'],
        },
        **data,
    })

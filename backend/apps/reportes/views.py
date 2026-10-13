from decimal import Decimal

from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.authentication.utils import get_empresa_id_desde_request
from apps.gastos.models import Gasto
from apps.ordenes.models import OrdenTrabajo, DetalleRepuestoOrdenTrabajo, DetalleServicioOrdenTrabajo


@api_view(['GET'])
def rentabilidad_taller(request):
    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'empresa_id requerido'}, status=400)

    taller_id = request.query_params.get('taller_id')
    fecha_desde = request.query_params.get('fecha_desde')
    fecha_hasta = request.query_params.get('fecha_hasta')

    if not taller_id:
        return Response({'error': 'taller_id requerido'}, status=400)

    ots = OrdenTrabajo.objects.filter(
        empresa_id=empresa_id,
        sucursal_id=taller_id,
        is_active=True,
        estado=OrdenTrabajo.EstadoOrden.ENTREGADO,
    )

    if fecha_desde:
        ots = ots.filter(fecha_entrega__date__gte=fecha_desde) if False else ots.filter(fecha_entrega__isnull=False, fecha_entrega__date__gte=fecha_desde) or ots.filter(created_at__date__gte=fecha_desde)
    if fecha_hasta:
        ots = ots.filter(fecha_entrega__date__lte=fecha_hasta)

    ot_ids = list(ots.values_list('id', flat=True))
    ingresos = ots.aggregate(total=Sum('total'))['total'] or Decimal('0.00')

    cmv_rep = DetalleRepuestoOrdenTrabajo.objects.filter(orden_trabajo_id__in=ot_ids).aggregate(
        total=Sum('costo_total')
    )['total'] or Decimal('0.00')

    cmv_serv = DetalleServicioOrdenTrabajo.objects.filter(orden_trabajo_id__in=ot_ids).aggregate(
        total=Sum('costo_total')
    )['total'] or Decimal('0.00')

    gastos = Gasto.objects.filter(empresa_id=empresa_id, taller_id=taller_id, is_active=True)
    if fecha_desde:
        gastos = gastos.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        gastos = gastos.filter(fecha__lte=fecha_hasta)
    total_gastos = gastos.aggregate(total=Sum('monto'))['total'] or Decimal('0.00')

    margen_bruto = ingresos - (cmv_rep + cmv_serv)
    utilidad_neta = margen_bruto - total_gastos

    return Response({
        'taller_id': taller_id,
        'periodo': {'desde': fecha_desde, 'hasta': fecha_hasta},
        'ingresos': {'total': ingresos},
        'costos': {'repuestos_vendidos': cmv_rep, 'servicios': cmv_serv, 'total_cmv': cmv_rep + cmv_serv},
        'margen_bruto': margen_bruto,
        'gastos': {'total': total_gastos},
        'utilidad_neta': utilidad_neta,
    })


@api_view(['GET'])
def rentabilidad_empresa(request):
    empresa_id = get_empresa_id_desde_request(request)
    if not empresa_id:
        return Response({'error': 'empresa_id requerido'}, status=400)

    fecha_desde = request.query_params.get('fecha_desde')
    fecha_hasta = request.query_params.get('fecha_hasta')
    taller_id = request.query_params.get('taller_id')

    ots = OrdenTrabajo.objects.filter(
        empresa_id=empresa_id,
        is_active=True,
        estado=OrdenTrabajo.EstadoOrden.ENTREGADO,
    )
    if taller_id:
        ots = ots.filter(sucursal_id=taller_id)
    if fecha_desde:
        ots = ots.filter(fecha_entrega__isnull=False, fecha_entrega__date__gte=fecha_desde)
    if fecha_hasta:
        ots = ots.filter(fecha_entrega__isnull=False, fecha_entrega__date__lte=fecha_hasta)

    ot_ids = list(ots.values_list('id', flat=True))
    ingresos = ots.aggregate(total=Sum('total'))['total'] or Decimal('0.00')

    cmv_rep = DetalleRepuestoOrdenTrabajo.objects.filter(orden_trabajo_id__in=ot_ids).aggregate(total=Sum('costo_total'))['total'] or Decimal('0.00')
    cmv_serv = DetalleServicioOrdenTrabajo.objects.filter(orden_trabajo_id__in=ot_ids).aggregate(total=Sum('costo_total'))['total'] or Decimal('0.00')

    gastos = Gasto.objects.filter(empresa_id=empresa_id, is_active=True)
    if taller_id:
        gastos = gastos.filter(taller_id=taller_id)
    if fecha_desde:
        gastos = gastos.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        gastos = gastos.filter(fecha__lte=fecha_hasta)
    total_gastos = gastos.aggregate(total=Sum('monto'))['total'] or Decimal('0.00')

    margen_bruto = ingresos - (cmv_rep + cmv_serv)
    utilidad_neta = margen_bruto - total_gastos

    return Response({
        'periodo': {'desde': fecha_desde, 'hasta': fecha_hasta},
        'ingresos': {'total': ingresos},
        'costos': {'repuestos_vendidos': cmv_rep, 'servicios': cmv_serv, 'total_cmv': cmv_rep + cmv_serv},
        'margen_bruto': margen_bruto,
        'gastos': {'total': total_gastos},
        'utilidad_neta': utilidad_neta,
    })

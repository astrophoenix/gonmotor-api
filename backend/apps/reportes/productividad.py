"""RF-15: productividad y cálculo informativo de comisiones actuales."""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Case, Count, F, IntegerField, Sum, When
from django.db.models.functions import Coalesce

from apps.authentication.models import UsuarioEmpresa
from apps.core.ids_publicos import id_publico
from apps.ordenes.models import DetalleServicioOrdenTrabajo, OrdenTrabajo

CERO = Decimal('0.00')
DEFINICIONES = {
    'periodo': 'Órdenes entregadas en el rango, por fecha de entrega (ambos días incluidos).',
    'servicios': 'Solo servicios internos aprobados y completados; excluye repuestos y subcontratos.',
    'asignacion': 'Mecánico del servicio; si falta, mecánico principal de la orden. Sin ambos: Sin asignar, sin comisión.',
    'mano_obra': 'Subtotal de los servicios después del descuento y sin IVA, aunque exista saldo pendiente.',
    'comision': 'Mano de obra × porcentaje actual del empleado en esta empresa, redondeado a centavos por mecánico. Cambiar el porcentaje recalcula también periodos anteriores.',
    'pago': 'Cálculo informativo a pagar: no registra pagos, gastos ni egresos de caja.',
    'ordenes': 'Órdenes distintas con servicios incluidos por mecánico. Una misma OT puede figurar para varios mecánicos; el total de órdenes no las duplica.',
}


def calcular_productividad(empresa_id, talleres, desde, hasta):
    ordenes = OrdenTrabajo.objects.filter(
        empresa_id=empresa_id, is_active=True, estado='ENTREGADO',
        fecha_entrega__date__gte=desde, fecha_entrega__date__lte=hasta,
    )
    if talleres is not None:
        ordenes = ordenes.filter(sucursal_id__in=talleres)
    usuarios_empresa = UsuarioEmpresa.objects.filter(empresa_id=empresa_id).values('user_id')
    lineas = DetalleServicioOrdenTrabajo.objects.filter(
        orden_trabajo__in=ordenes,
        aprobado=True, completado=True, es_subcontratado=False,
    ).annotate(responsable=Coalesce('mecanico_asignado_id', 'orden_trabajo__mecanico_principal_id')).annotate(
        tecnico_id=Case(When(responsable__in=usuarios_empresa, then=F('responsable')), default=None, output_field=IntegerField()),
    )
    # Una consulta agrupada y otra de empleados; no consultas por cada mecánico.
    grupos = list(lineas.values('tecnico_id').annotate(
        ordenes=Count('orden_trabajo_id', distinct=True), servicios_completados=Count('pk'),
        horas_aplicadas=Sum('horas_aplicadas'), mano_obra=Sum('subtotal'),
    ).order_by('tecnico_id'))
    empleados = {
        e.user_id: e for e in UsuarioEmpresa.objects.filter(
            empresa_id=empresa_id, user_id__in=[g['tecnico_id'] for g in grupos if g['tecnico_id']],
        ).select_related('user__profile')
    }
    filas = []
    for grupo in grupos:
        empleado = empleados.get(grupo['tecnico_id'])
        porcentaje = empleado.comision_porcentaje if empleado else CERO
        mano_obra = (grupo['mano_obra'] or CERO).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        filas.append({
            'id': id_publico(empleado.user) if empleado else 'sin-asignar',
            'empleado_id': id_publico(empleado) if empleado else None,
            'nombre': (empleado.user.get_full_name().strip() or empleado.user.username) if empleado else 'Sin asignar',
            'ordenes': grupo['ordenes'], 'servicios_completados': grupo['servicios_completados'],
            'horas_aplicadas': grupo['horas_aplicadas'] or CERO, 'mano_obra': mano_obra,
            'comision_porcentaje': porcentaje,
            'comision': (mano_obra * porcentaje / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        })
    filas.sort(key=lambda f: (f['empleado_id'] is None, f['nombre'].casefold(), f['id']))
    resumen = {
        'ordenes': lineas.values('orden_trabajo_id').distinct().count(),
        'servicios_completados': sum(f['servicios_completados'] for f in filas),
        'horas_aplicadas': sum((f['horas_aplicadas'] for f in filas), CERO),
        'mano_obra': sum((f['mano_obra'] for f in filas), CERO),
        'comision': sum((f['comision'] for f in filas), CERO),
    }
    return {
        'periodo': {'desde': desde.isoformat(), 'hasta': hasta.isoformat()},
        'mecanicos': filas, 'resumen': resumen, 'definiciones': DEFINICIONES,
    }

"""Resumen del negocio para el dueño (RF-07): lo que hoy calcula a mano en su Excel.

Mismos criterios que la rentabilidad (`views._get_rentabilidad`): una OT cuenta
como venta cuando se **entrega** (por `fecha_entrega`), los ingresos sin IVA son
su `subtotal_neto`, el costo es el `costo_total` de sus líneas (repuestos y mano
de obra) y los gastos van por su fecha. Todo se compara contra el periodo
anterior de igual duración.

Definiciones (también se devuelven en la respuesta, `definiciones`):
- facturado: total con IVA de las OT entregadas en el periodo.
- cobrado: pagos − devoluciones vigentes (no anulados) con fecha en el periodo.
- por_cobrar: saldo, al cierre del periodo, de las OT entregadas hasta ese día
  (total − lo pagado hasta ese día).
- utilidad: ingresos sin IVA − costo de los trabajos − gastos.
Fechas por día en `America/Guayaquil`, ambos extremos incluidos.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth

from apps.gastos.models import Gasto
from apps.ordenes.models import DetalleServicioOrdenTrabajo, OrdenTrabajo
from apps.pagos.models import PagoOrden

from .views import CERO, MESES_CORTOS, _get_rentabilidad, _mes

MAX_DIAS = 366
# Hasta dos meses la serie del gráfico va por día; más largo, por mes.
MAX_DIAS_SERIE_DIARIA = 62

ESTADOS_ABIERTOS = ('PENDIENTE', 'EN_ESPERA', 'EN_PROCESO', 'COMPLETADO')

DEFINICIONES = {
    'facturado': 'Total con IVA de las órdenes entregadas en el periodo (por fecha de entrega).',
    'ingresos': 'Lo facturado sin IVA (subtotal neto de las órdenes entregadas).',
    'cobrado': 'Pagos menos devoluciones vigentes registrados en el periodo, de cualquier orden.',
    'por_cobrar': 'Saldo al cierre del periodo de las órdenes entregadas hasta ese día.',
    'costo_trabajos': 'Costo de repuestos y mano de obra de las órdenes entregadas.',
    'utilidad': 'Ingresos sin IVA − costo de los trabajos − gastos.',
    'ticket_promedio': 'Facturado ÷ órdenes entregadas.',
    'anuladas': 'Órdenes anuladas por fecha de anulación; los registros anteriores sin fecha usan su última modificación.',
}


def periodo_anterior(desde, hasta):
    dias = (hasta - desde).days + 1
    return desde - timedelta(days=dias), desde - timedelta(days=1)


def _ordenes(empresa_id, talleres):
    queryset = OrdenTrabajo.objects.filter(empresa_id=empresa_id, is_active=True)
    if talleres is not None:
        queryset = queryset.filter(sucursal_id__in=talleres)
    return queryset


def _pagos(empresa_id, talleres):
    queryset = PagoOrden.objects.filter(orden_trabajo__empresa_id=empresa_id, anulado=False)
    if talleres is not None:
        queryset = queryset.filter(orden_trabajo__sucursal_id__in=talleres)
    return queryset


def _por_cobrar_al(ordenes, fecha):
    """Saldo, al cierre de `fecha`, de las OT entregadas hasta ese día."""
    vigentes = PagoOrden.objects.filter(orden_trabajo=OuterRef('pk'), anulado=False, fecha__date__lte=fecha)

    def suma(tipo):
        sub = vigentes.filter(tipo=tipo).values('orden_trabajo').annotate(s=Sum('monto')).values('s')
        return Coalesce(Subquery(sub, output_field=DecimalField()), Value(CERO))

    entregadas = ordenes.filter(estado='ENTREGADO', fecha_entrega__date__lte=fecha).annotate(
        saldo_al=F('total') - suma(PagoOrden.Tipo.PAGO) + suma(PagoOrden.Tipo.DEVOLUCION),
    ).filter(saldo_al__gt=CERO)
    return entregadas.aggregate(total=Sum('saldo_al'), ordenes=Count('pk'))


def _metricas(empresa_id, talleres, desde, hasta):
    ordenes = _ordenes(empresa_id, talleres)
    entregadas = ordenes.filter(
        estado='ENTREGADO', fecha_entrega__date__gte=desde, fecha_entrega__date__lte=hasta,
    )
    factura = entregadas.aggregate(
        total=Sum('total'), base_0=Sum('subtotal_base_0'), base_gravada=Sum('subtotal_base_gravada'),
        iva=Sum('monto_iva'), cantidad=Count('pk'),
    )
    conteos = ordenes.annotate(anulada_en=Coalesce('fecha_anulacion', 'updated_at')).aggregate(
        creadas=Count('pk', filter=Q(created_at__date__gte=desde, created_at__date__lte=hasta)),
        anuladas=Count('pk', filter=Q(
            estado='CANCELADO', anulada_en__date__gte=desde, anulada_en__date__lte=hasta,
        )),
        # Abiertas al cierre: creadas hasta ese día, ni anuladas ni entregadas antes.
        abiertas=Count('pk', filter=Q(created_at__date__lte=hasta) & (
            Q(estado__in=ESTADOS_ABIERTOS) | Q(estado='ENTREGADO', fecha_entrega__date__gt=hasta)
        )),
    )

    pagos = _pagos(empresa_id, talleres).filter(fecha__date__gte=desde, fecha__date__lte=hasta)
    etiquetas = dict(PagoOrden.FormaPago.choices)
    por_forma = []
    cobrado = CERO
    for fila in pagos.values('forma_pago').annotate(
        pagos=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.PAGO)),
        devoluciones=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.DEVOLUCION)),
    ).order_by('forma_pago'):
        neto = (fila['pagos'] or CERO) - (fila['devoluciones'] or CERO)
        cobrado += neto
        por_forma.append({
            'forma_pago': fila['forma_pago'],
            'forma_pago_display': etiquetas.get(fila['forma_pago'], fila['forma_pago']),
            'pagos': fila['pagos'] or CERO,
            'devoluciones': fila['devoluciones'] or CERO,
            'neto': neto,
        })

    gastos = Gasto.objects.filter(empresa_id=empresa_id, is_active=True, fecha__gte=desde, fecha__lte=hasta)
    if talleres is not None:
        gastos = gastos.filter(taller_id__in=talleres)
    categorias = dict(Gasto.Categoria.choices)
    gastos_categoria = [
        {'categoria': fila['categoria'], 'categoria_display': categorias.get(fila['categoria'], fila['categoria']),
         'total': fila['total'] or CERO}
        for fila in gastos.values('categoria').annotate(total=Sum('monto')).order_by('-total')
    ]

    rentabilidad = _get_rentabilidad(empresa_id, talleres, desde, hasta)
    por_cobrar = _por_cobrar_al(ordenes, hasta)
    facturado = factura['total'] or CERO
    entregadas_n = factura['cantidad'] or 0
    return {
        'ordenes': {
            'abiertas': conteos['abiertas'],
            'creadas': conteos['creadas'],
            'entregadas': entregadas_n,
            'anuladas': conteos['anuladas'],
        },
        'facturado': {
            'total': facturado,
            'base_0': factura['base_0'] or CERO,
            'base_gravada': factura['base_gravada'] or CERO,
            'iva': factura['iva'] or CERO,
        },
        'ingresos': rentabilidad['ingresos']['total'],
        'costo_trabajos': {
            'repuestos': rentabilidad['costos']['repuestos_vendidos'],
            'servicios': rentabilidad['costos']['servicios'],
            'total': rentabilidad['costos']['total_cmv'],
        },
        'gastos': {'total': rentabilidad['gastos']['total'], 'por_categoria': gastos_categoria},
        'utilidad': rentabilidad['utilidad_neta'],
        'cobrado': {'total': cobrado, 'por_forma': por_forma},
        'por_cobrar': {'total': por_cobrar['total'] or CERO, 'ordenes': por_cobrar['ordenes']},
        'ticket_promedio': (facturado / entregadas_n).quantize(Decimal('0.01')) if entregadas_n else CERO,
    }


def _variacion(actual, anterior):
    actual, anterior = Decimal(actual), Decimal(anterior)
    pct = None
    if anterior:
        pct = round(float((actual - anterior) / abs(anterior)) * 100, 1)
    return {'actual': actual, 'anterior': anterior, 'variacion': actual - anterior, 'variacion_pct': pct}


def _kpis(actual, anterior):
    """Indicadores principales con su variación frente al periodo anterior."""
    rutas = {
        'facturado': ('facturado', 'total'),
        'ingresos': ('ingresos',),
        'cobrado': ('cobrado', 'total'),
        'por_cobrar': ('por_cobrar', 'total'),
        'gastos': ('gastos', 'total'),
        'costo_trabajos': ('costo_trabajos', 'total'),
        'utilidad': ('utilidad',),
        'ticket_promedio': ('ticket_promedio',),
        'ordenes_entregadas': ('ordenes', 'entregadas'),
        'ordenes_creadas': ('ordenes', 'creadas'),
    }

    def leer(datos, ruta):
        for clave in ruta:
            datos = datos[clave]
        return datos

    return {nombre: _variacion(leer(actual, ruta), leer(anterior, ruta)) for nombre, ruta in rutas.items()}


def _serie(empresa_id, talleres, desde, hasta):
    """Facturado y cobrado por día (o por mes si el rango supera dos meses)."""
    por_mes = (hasta - desde).days + 1 > MAX_DIAS_SERIE_DIARIA
    trunc = TruncMonth if por_mes else TruncDate
    entregadas = _ordenes(empresa_id, talleres).filter(
        estado='ENTREGADO', fecha_entrega__date__gte=desde, fecha_entrega__date__lte=hasta,
    )
    facturado = {
        _mes(fila['clave']): fila['total'] or CERO
        for fila in entregadas.annotate(clave=trunc('fecha_entrega')).values('clave').annotate(total=Sum('total'))
    }
    pagos = _pagos(empresa_id, talleres).filter(fecha__date__gte=desde, fecha__date__lte=hasta)
    cobrado = {
        _mes(fila['clave']): (fila['pagos'] or CERO) - (fila['devoluciones'] or CERO)
        for fila in pagos.annotate(clave=trunc('fecha')).values('clave').annotate(
            pagos=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.PAGO)),
            devoluciones=Sum('monto', filter=Q(tipo=PagoOrden.Tipo.DEVOLUCION)),
        )
    }

    puntos = []
    if por_mes:
        actual = desde.replace(day=1)
        while actual <= hasta:
            puntos.append((actual, f'{MESES_CORTOS[actual.month - 1]} {actual.year}'))
            actual = (actual.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        actual = desde
        while actual <= hasta:
            puntos.append((actual, actual.strftime('%d/%m')))
            actual += timedelta(days=1)
    return {
        'agrupacion': 'mes' if por_mes else 'dia',
        'puntos': [
            {'fecha': fecha.isoformat(), 'etiqueta': etiqueta,
             'facturado': facturado.get(fecha, CERO), 'cobrado': cobrado.get(fecha, CERO)}
            for fecha, etiqueta in puntos
        ],
    }


def _top_servicios(empresa_id, talleres, desde, hasta, limite=5):
    entregadas = _ordenes(empresa_id, talleres).filter(
        estado='ENTREGADO', fecha_entrega__date__gte=desde, fecha_entrega__date__lte=hasta,
    ).values('pk')
    return [
        {'descripcion': fila['descripcion'], 'veces': fila['veces'], 'total': fila['total'] or CERO}
        for fila in DetalleServicioOrdenTrabajo.objects.filter(orden_trabajo_id__in=entregadas, aprobado=True)
        .values('descripcion').annotate(total=Sum('subtotal'), veces=Count('pk')).order_by('-total', 'descripcion')[:limite]
    ]


def calcular_resumen(empresa_id, talleres, desde, hasta):
    anterior_desde, anterior_hasta = periodo_anterior(desde, hasta)
    actual = _metricas(empresa_id, talleres, desde, hasta)
    anterior = _metricas(empresa_id, talleres, anterior_desde, anterior_hasta)
    return {
        'periodo': {'desde': desde.isoformat(), 'hasta': hasta.isoformat(), 'dias': (hasta - desde).days + 1},
        'periodo_anterior': {'desde': anterior_desde.isoformat(), 'hasta': anterior_hasta.isoformat()},
        'kpis': _kpis(actual, anterior),
        'actual': actual,
        'anterior': anterior,
        'serie': _serie(empresa_id, talleres, desde, hasta),
        'top_servicios': _top_servicios(empresa_id, talleres, desde, hasta),
        'definiciones': DEFINICIONES,
    }

from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, Count, F, IntegerField, Q, Sum, Value, When
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.renderers import JSONRenderer

from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.services.text_improver import TextImproverError, mejorar_texto
from apps.inventario.models import Repuesto
from apps.ordenes.models import OrdenTrabajo, RecepcionVehiculo

MESES_CORTOS = (
    'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
    'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic',
)

MESES_LARGOS = (
    'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
)

# Ventana por defecto de las gráficas del dashboard (meses naturales).
MESES_TENDENCIA = 6


class HealthCheckView(APIView):
    """
    Endpoint para verificar que el servidor/backend y la API están funcionando correctamente.
    Útil para monitoreo, despliegues o validación inicial del frontend.
    """
    permission_classes = []  # Opcional: permite que sea público para herramientas de monitoreo

    def get(self, request):
        return Response(
            {
                "status": "ok",
                "message": "API de Gestión de Taller Automotriz operativa",
                "version": "1.0.0"
            },
            status=status.HTTP_200_OK
        )


class MejorarTextoView(APIView):
    """
    Endpoint genérico y reutilizable: mejora el texto de cualquier campo de
    texto libre (motivo, observaciones, etc.) usando un LLM.

    Body esperado:
        - texto: cadena con el texto a mejorar (obligatorio, máx. 500 chars).
        - contexto: pista opcional del tipo de campo (ej. "motivo de ingreso",
          "observaciones del tablero") para afinar la reescritura.

    Respuesta:
        - { "mejorado": "<texto mejorado>" }
    """
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        texto = data.get('texto') or ''
        contexto = data.get('contexto') or ''
        try:
            mejorado = mejorar_texto(texto, contexto)
        except TextImproverError as exc:
            return Response({'detail': str(exc)}, status=exc.status)
        return Response({'mejorado': mejorado})


def _dinero(valor):
    """Decimal/None → float redondeado a 2 decimales (JSON-friendly)."""
    if valor is None:
        return 0.0
    return float(round(Decimal(valor), 2))


def _primer_dia_mes(fecha):
    return fecha.replace(day=1)


def _restar_meses(fecha, cantidad):
    """Primer día del mes `cantidad` meses antes de `fecha` (sin dependencias externas)."""
    anio = fecha.year + (fecha.month - 1 - cantidad) // 12
    mes = (fecha.month - 1 - cantidad) % 12 + 1
    return fecha.replace(year=anio, month=mes, day=1)


def _etiqueta_mes(fecha, largo=False):
    nombre = MESES_LARGOS if largo else MESES_CORTOS
    return f'{nombre[fecha.month - 1]} {fecha.year}'


def _serializar_recepcion(recepcion):
    vehiculo = recepcion.vehiculo
    cliente = recepcion.cliente
    return {
        'id': recepcion.id,
        'numero_recepcion': recepcion.numero_recepcion,
        'fecha_ingreso': recepcion.fecha_ingreso.isoformat() if recepcion.fecha_ingreso else None,
        'hora': timezone.localtime(recepcion.fecha_ingreso).strftime('%H:%M') if recepcion.fecha_ingreso else None,
        'tipo_recepcion': recepcion.tipo_recepcion,
        'tipo_recepcion_display': recepcion.get_tipo_recepcion_display(),
        'estado': recepcion.estado,
        'estado_display': recepcion.get_estado_display(),
        'cliente': cliente.nombre if cliente else '—',
        'placa': vehiculo.placa if vehiculo else '—',
        'vehiculo': f'{vehiculo.marca} {vehiculo.modelo}' if vehiculo else '—',
        'kilometraje_ingreso': recepcion.kilometraje_ingreso,
    }


def _serializar_orden(orden):
    vehiculo = orden.vehiculo
    cliente = orden.cliente
    return {
        'id': orden.id,
        'numero_orden': orden.numero_orden,
        'estado': orden.estado,
        'estado_display': orden.get_estado_display(),
        'prioridad': orden.prioridad,
        'prioridad_display': orden.get_prioridad_display(),
        'tipo_trabajo': orden.tipo_trabajo,
        'tipo_trabajo_display': orden.get_tipo_trabajo_display(),
        'motivo_espera': orden.motivo_espera,
        'cliente': cliente.nombre if cliente else '—',
        'placa': vehiculo.placa if vehiculo else '—',
        'vehiculo': f'{vehiculo.marca} {vehiculo.modelo}' if vehiculo else '—',
        'total': _dinero(orden.total),
        'fecha_ingreso': orden.fecha_ingreso.isoformat() if orden.fecha_ingreso else None,
    }


class DashboardView(APIView):
    """Agregados del dashboard principal del taller.

    Devuelve en una sola llamada todo lo que necesita la pantalla de inicio:

    - `kpis`: vehículos en taller, órdenes pendientes de aprobación, facturación
      del mes y alertas de stock bajo (más contadores de apoyo).
    - `tendencia`: ingresos y cantidad de órdenes por mes (últimos 6 meses).
    - `distribucion_servicios`: órdenes agrupadas por tipo de trabajo (donut).
    - `recepciones_hoy` / `ultimos_ingresos`: ingresos de vehículos del día.
    - `ordenes_activas`: órdenes urgentes o en curso.
    - `stock_bajo`: repuestos que requieren reposición.

    Criterios de negocio:
    - "Vehículos en taller" = recepciones sin fecha de salida que no fueron
      rechazadas (el vehículo sigue bajo custodia del taller).
    - "Pendientes de aprobación" = órdenes en estado `EN_ESPERA` (motivo de
      espera: aprobación del cliente, repuestos, trabajo adicional, etc.).
    - "Facturación del mes" = suma de `OrdenTrabajo.total` de las órdenes
      creadas en el mes, excluyendo las anuladas.
    - "Stock bajo" = `stock_actual <= stock_minimo`.

    Todo el contenido se filtra por el `empresa_id` activo de la sesión (tenant).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No hay una empresa activa asociada al usuario.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        hoy = timezone.localdate()
        inicio_mes = _primer_dia_mes(hoy)
        mes_anterior = _restar_meses(inicio_mes, 1)
        fin_mes_anterior = inicio_mes - timedelta(days=1)
        inicio_tendencia = _restar_meses(inicio_mes, MESES_TENDENCIA - 1)

        ordenes = OrdenTrabajo.objects.filter(empresa_id=empresa_id, is_active=True)
        recepciones = RecepcionVehiculo.objects.filter(empresa_id=empresa_id, is_active=True)

        # ── KPIs ────────────────────────────────────────────────────────────
        vehiculos_en_taller = recepciones.filter(
            fecha_salida__isnull=True
        ).exclude(estado='NO_ACEPTADA').count()

        pendientes_aprobacion = ordenes.filter(
            estado=OrdenTrabajo.EstadoOrden.EN_ESPERA
        ).count()

        facturacion_mes = ordenes.filter(
            created_at__date__gte=inicio_mes,
            created_at__date__lte=hoy,
        ).exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO).aggregate(
            total=Sum('total')
        )['total']

        facturacion_anterior = ordenes.filter(
            created_at__date__gte=mes_anterior,
            created_at__date__lte=fin_mes_anterior,
        ).exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO).aggregate(
            total=Sum('total')
        )['total']

        facturacion_mes = facturacion_mes or Decimal('0')
        facturacion_anterior = facturacion_anterior or Decimal('0')

        if facturacion_anterior > 0:
            variacion = round(
                float((facturacion_mes - facturacion_anterior) / facturacion_anterior) * 100, 1
            )
        else:
            variacion = None

        ordenes_mes = ordenes.filter(
            created_at__date__gte=inicio_mes,
            created_at__date__lte=hoy,
        ).exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO).count()

        repuestos_bajo_stock = Repuesto.objects.filter(
            empresa_id=empresa_id,
            is_active=True,
        ).filter(stock_actual__lte=F('stock_minimo'))

        kpis = {
            'vehiculos_en_taller': vehiculos_en_taller,
            'ordenes_pendientes_aprobacion': pendientes_aprobacion,
            'facturacion_mes': _dinero(facturacion_mes),
            'facturacion_mes_anterior': _dinero(facturacion_anterior),
            'variacion_facturacion_pct': variacion,
            'ordenes_mes': ordenes_mes,
            'alertas_stock_bajo': repuestos_bajo_stock.count(),
            'recepciones_hoy': recepciones.filter(fecha_ingreso__date=hoy).count(),
            'ordenes_en_proceso': ordenes.filter(
                estado=OrdenTrabajo.EstadoOrden.EN_PROCESO
            ).count(),
            'ordenes_pendientes': ordenes.filter(
                estado=OrdenTrabajo.EstadoOrden.PENDIENTE
            ).count(),
        }

        # ── Tendencia de ingresos / reparaciones (últimos N meses) ──────────
        por_mes = {}
        for fila in ordenes.filter(
            created_at__date__gte=inicio_tendencia,
        ).exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO).annotate(
            mes=TruncMonth('created_at')
        ).values('mes').annotate(
            ingresos=Sum('total'),
            ordenes=Count('id'),
        ):
            if fila['mes']:
                por_mes[fila['mes'].date()] = fila

        tendencia = []
        for indice in range(MESES_TENDENCIA):
            dia_mes = _restar_meses(inicio_mes, MESES_TENDENCIA - 1 - indice)
            fila = por_mes.get(dia_mes)
            tendencia.append({
                'mes': dia_mes.strftime('%Y-%m'),
                'etiqueta': _etiqueta_mes(dia_mes),
                'etiqueta_larga': _etiqueta_mes(dia_mes, largo=True),
                'ingresos': _dinero(fila['ingresos'] if fila else 0),
                'ordenes': fila['ordenes'] if fila else 0,
            })

        # ── Distribución por tipo de trabajo ────────────────────────────────
        tipos = {
            fila['tipo_trabajo']: fila['total']
            for fila in ordenes.filter(
                created_at__date__gte=inicio_tendencia,
            ).exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO).values(
                'tipo_trabajo'
            ).annotate(total=Count('id')).order_by('-total')
        }

        distribucion_servicios = [
            {
                'clave': clave,
                'etiqueta': etiqueta,
                'ordenes': tipos[clave],
            }
            for clave, etiqueta in OrdenTrabajo.TipoTrabajo.choices
            if clave in tipos
        ]
        distribucion_servicios.sort(key=lambda fila: -fila['ordenes'])

        # ── Tabla: recepciones / ingresos del día ───────────────────────────
        recepciones_dia = list(
            recepciones.filter(fecha_ingreso__date=hoy)
            .select_related('cliente', 'vehiculo')
            .order_by('-fecha_ingreso')[:8]
        )
        ultimos_ingresos = list(
            recepciones.select_related('cliente', 'vehiculo')
            .order_by('-fecha_ingreso')[:8]
        )

        # ── Tabla: órdenes urgentes o en curso ──────────────────────────────
        orden_urgencia = Case(
            When(prioridad=OrdenTrabajo.Prioridad.URGENTE, then=Value(0)),
            When(prioridad=OrdenTrabajo.Prioridad.ALTA, then=Value(1)),
            When(estado=OrdenTrabajo.EstadoOrden.EN_PROCESO, then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
        ordenes_activas = list(
            ordenes.filter(
                Q(prioridad=OrdenTrabajo.Prioridad.URGENTE)
                | Q(estado=OrdenTrabajo.EstadoOrden.EN_PROCESO)
            )
            .select_related('cliente', 'vehiculo')
            .order_by(orden_urgencia, '-fecha_ingreso')[:8]
        )

        # ── Repuestos con stock bajo ────────────────────────────────────────
        stock_bajo = [
            {
                'id': repuesto.id,
                'codigo': repuesto.codigo,
                'nombre': repuesto.nombre,
                'categoria': repuesto.categoria,
                'categoria_display': repuesto.get_categoria_display(),
                'stock_actual': float(repuesto.stock_actual),
                'stock_minimo': float(repuesto.stock_minimo),
                'unidad_medida': repuesto.get_unidad_medida_display(),
            }
            for repuesto in repuestos_bajo_stock.order_by('stock_actual')[:5]
        ]

        return Response({
            'generado_en': timezone.now().isoformat(),
            'hoy': hoy.isoformat(),
            'periodo': {
                'desde': inicio_tendencia.isoformat(),
                'hasta': hoy.isoformat(),
                'mes_actual': inicio_mes.isoformat(),
            },
            'kpis': kpis,
            'tendencia': tendencia,
            'distribucion_servicios': distribucion_servicios,
            'recepciones_hoy': [_serializar_recepcion(r) for r in recepciones_dia],
            'ultimos_ingresos': [_serializar_recepcion(r) for r in ultimos_ingresos],
            'ordenes_activas': [_serializar_orden(o) for o in ordenes_activas],
            'stock_bajo': stock_bajo,
        })
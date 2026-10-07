from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Case, Count, F, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDay, TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.renderers import JSONRenderer

from apps.authentication.utils import get_empresa_id_desde_request
from apps.citas.models import Cita
from apps.core.services.text_improver import TextImproverError, mejorar_texto
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Taller
from apps.inventario.models import Repuesto, Servicio
from apps.ordenes.models import (
    DetalleServicioInspeccion,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)

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

# ═══════════════════════════════════════════════════════════════════════
# Dashboard V2 — panel ejecutivo con filtros de periodo y sucursal
# ═══════════════════════════════════════════════════════════════════════

MAX_DIAS_PERIODO = 730        # ventana máxima aceptada por ?fecha_desde/?fecha_hasta
MAX_BUCKETS_DIARIO = 31       # granularidad diaria hasta 31 días
MAX_BUCKETS_MENSUAL = 18      # después se agrega por mes (últimos 18 meses en la gráfica)
MAX_FILAS_TABLA = 10

# Orden de presentación del embudo operativo (el orden alfabético de los
# choices no comunica el flujo real de la orden).
ORDEN_ESTADOS_OT = (
    OrdenTrabajo.EstadoOrden.PENDIENTE,
    OrdenTrabajo.EstadoOrden.EN_ESPERA,
    OrdenTrabajo.EstadoOrden.EN_PROCESO,
    OrdenTrabajo.EstadoOrden.COMPLETADO,
    OrdenTrabajo.EstadoOrden.ENTREGADO,
    OrdenTrabajo.EstadoOrden.CANCELADO,
)

# Estados que significan "el trabajo sigue en el taller".
ESTADOS_OT_ABIERTAS = (
    OrdenTrabajo.EstadoOrden.PENDIENTE,
    OrdenTrabajo.EstadoOrden.EN_ESPERA,
    OrdenTrabajo.EstadoOrden.EN_PROCESO,
)

# Cotizaciones que ya fueron resueltas por el cliente (para la tasa de aprobación).
ESTADOS_OT_CERRADAS = (
    OrdenTrabajo.EstadoOrden.COMPLETADO,
    OrdenTrabajo.EstadoOrden.ENTREGADO,
)


def _sumar_meses(fecha, cantidad):
    """Primer día del mes `cantidad` meses después de `fecha`."""
    anio = fecha.year + (fecha.month - 1 + cantidad) // 12
    mes = (fecha.month - 1 + cantidad) % 12 + 1
    return fecha.replace(year=anio, month=mes, day=1)


def _etiqueta_dia(fecha):
    return f'{fecha.day:02d} {MESES_CORTOS[fecha.month - 1]}'


def _nombre_usuario(usuario):
    """Nombre completo del usuario o, en su defecto, el username."""
    if not usuario:
        return None
    nombre = (usuario.get_full_name() or '').strip()
    return nombre or usuario.username


def _serializar_recepcion_v2(recepcion, inspeccion_id=None):
    """Recepción de V1 + datos operativos del flujo del día."""
    vehiculo = recepcion.vehiculo
    fila = _serializar_recepcion(recepcion)
    fila.update({
        'nivel_combustible': recepcion.nivel_combustible,
        'nivel_combustible_display': recepcion.get_nivel_combustible_display(),
        'ingreso_en_grua': recepcion.ingreso_en_grua,
        'sucursal': recepcion.sucursal.nombre if recepcion.sucursal_id else None,
        'tiene_inspeccion': bool(inspeccion_id),
        'inspeccion_id': inspeccion_id,
        'puede_crear_inspeccion': recepcion.estado == 'ACEPTADA' and not inspeccion_id,
        'es_hoy': recepcion.fecha_ingreso.date() == timezone.localdate(),
    })
    if vehiculo is None:
        fila['placa'] = '—'
        fila['vehiculo'] = '—'
    return fila


def _serializar_cita(cita, hoy):
    vehiculo = cita.vehiculo
    cliente = cita.cliente
    return {
        'id': cita.id,
        'fecha_cita': cita.fecha_cita.isoformat(),
        'hora': cita.hora_cita.strftime('%H:%M'),
        'estado': cita.estado,
        'estado_display': cita.get_estado_display(),
        'motivo': cita.motivo,
        'motivo_display': cita.get_motivo_display(),
        'cliente': cliente.nombre if cliente else '—',
        'placa': vehiculo.placa if vehiculo else '—',
        'vehiculo': f'{vehiculo.marca} {vehiculo.modelo}' if vehiculo else '—',
        'sucursal': cita.taller.nombre if cita.taller_id else None,
        'es_hoy': cita.fecha_cita == hoy,
        'convertida': bool(cita.recepcion_generada_id),
        'recepcion_id': cita.recepcion_generada_id,
    }


def _serializar_orden_v2(orden, ahora):
    """Orden de V1 + asesor/mecánico, tiempo transcurrido y subtotal neto."""
    fila = _serializar_orden(orden)
    horas = None
    if orden.fecha_ingreso:
        horas = max(0.0, round((ahora - orden.fecha_ingreso).total_seconds() / 3600, 1))
    fila.update({
        'asesor': _nombre_usuario(orden.asesor),
        'mecanico': _nombre_usuario(orden.mecanico_principal),
        'subtotal_neto': _dinero(orden.subtotal_neto),
        'horas_transcurridas': horas,
        'sucursal': orden.sucursal.nombre if orden.sucursal_id else None,
    })
    return fila


def _periodo(queryset, campo, desde, hasta):
    """Filtra un queryset por rango de fechas inclusive sobre un campo Date/DateTime."""
    return queryset.filter(**{
        f'{campo}__date__gte': desde,
        f'{campo}__date__lte': hasta,
    })


def _con_sucursal(queryset, campo, sucursal):
    if sucursal is None:
        return queryset
    return queryset.filter(**{campo: sucursal.pk})


def _generar_buckets(desde, hasta):
    """Divide el periodo en casillas de la gráfica.

    Ventanas cortas (≤31 días) se agregan por día; las largas por mes. Como
    mucho se emiten `MAX_BUCKETS_*` casillas (las más recientes) para no
    saturar el eje X.
    """
    dias = (hasta - desde).days + 1
    if dias <= MAX_BUCKETS_DIARIO:
        return 'dia', [desde + timedelta(days=indice) for indice in range(dias)]

    buckets = []
    actual = _primer_dia_mes(desde)
    fin = _primer_dia_mes(hasta)
    while actual <= fin:
        buckets.append(actual)
        actual = _sumar_meses(actual, 1)
    if len(buckets) > MAX_BUCKETS_MENSUAL:
        buckets = buckets[-MAX_BUCKETS_MENSUAL:]
    return 'mes', buckets


def _truncador(expresion, granularidad):
    return TruncDay(expresion) if granularidad == 'dia' else TruncMonth(expresion)


class DashboardV2View(APIView):
    """Agregados del Dashboard V2 con filtros de periodo y sucursal.

    Soporta tres query params opcionales:

    - `fecha_desde` / `fecha_hasta` (YYYY-MM-DD): periodo de análisis. Por
      defecto es el mes en curso. Las métricas de "stock" (vehículos en
      taller, alertas de inventario, órdenes abiertas y cotizaciones
      pendientes) **ignoran** el periodo porque describen el estado actual.
    - `sucursal` (id de `Taller`): recorta todos los consultables del taller.

    Contenido:

    - `kpis`: 5 tarjetas ejecutivas (vehículos en taller, facturación del mes
      y ticket promedio, pipeline de citas, cotizaciones por aprobar, alertas
      de inventario).
    - `tendencia`: ingresos vs. órdenes creadas/completadas, agregada por día
      (≤31 días) o por mes.
    - `distribucion`: trabajos por tipo de trabajo (donut), por estado de la
      orden (barras) y por categoría del catálogo de servicios (barras).
    - `recepciones` / `citas`: flujo operativo del periodo.
    - `ordenes_activas`: órdenes abiertas, más críticas primero.
    - `stock_bajo`: repuestos por debajo del mínimo.

    Criterios de negocio:
    - "Vehículos en taller" = recepciones sin fecha de salida no rechazadas.
    - "Facturación" = `Sum(OrdenTrabajo.total)` excluyendo anuladas.
    - "Ticket promedio" = facturación / número de órdenes (None si no hay).
    - "Tasa de conversión de cita" = citas convertidas a recepción / citas
      creadas en el periodo.
    - "Tasa de aprobación" = cotizaciones aceptadas o convertidas /
      cotizaciones resueltas (aceptadas + rechazadas) en el periodo.
    - "Stock bajo" = `stock_actual <= stock_minimo`.

    Todo se filtra por el `empresa_id` activo de la sesión (tenant).
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

        error, fecha_desde = self._leer_fecha(request, 'fecha_desde', inicio_mes)
        if error:
            return error
        error, fecha_hasta = self._leer_fecha(request, 'fecha_hasta', hoy)
        if error:
            return error

        if fecha_desde > fecha_hasta:
            return Response(
                {'detail': 'El campo fecha_desde no puede ser posterior a fecha_hasta.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (fecha_hasta - fecha_desde).days + 1 > MAX_DIAS_PERIODO:
            return Response(
                {'detail': f'El periodo no puede superar {MAX_DIAS_PERIODO} días.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        error, sucursal = self._leer_sucursal(request, empresa_id)
        if error:
            return error

        # ── Consultas base (tenant + sucursal opcional) ────────────────────
        ordenes = OrdenTrabajo.objects.filter(empresa_id=empresa_id, is_active=True)
        recepciones = RecepcionVehiculo.objects.filter(empresa_id=empresa_id, is_active=True)
        citas = Cita.objects.filter(empresa_id=empresa_id, is_active=True)
        cotizaciones = Cotizacion.objects.filter(empresa_id=empresa_id, is_active=True)
        inspecciones = InspeccionVehiculo.objects.filter(empresa_id=empresa_id, is_active=True)
        repuestos = Repuesto.objects.filter(empresa_id=empresa_id, is_active=True)

        ordenes = _con_sucursal(ordenes, 'sucursal', sucursal)
        recepciones = _con_sucursal(recepciones, 'sucursal', sucursal)
        citas = _con_sucursal(citas, 'taller', sucursal)
        cotizaciones = _con_sucursal(cotizaciones, 'sucursal', sucursal)
        inspecciones = _con_sucursal(inspecciones, 'sucursal', sucursal)
        repuestos = _con_sucursal(repuestos, 'sucursal', sucursal)

        # ── KPIs ───────────────────────────────────────────────────────────
        vehiculos_en_taller = recepciones.filter(
            fecha_salida__isnull=True
        ).exclude(estado='NO_ACEPTADA').count()

        ordenes_periodo_qs = _periodo(
            ordenes.exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO),
            'created_at', fecha_desde, fecha_hasta,
        )
        facturacion_periodo = ordenes_periodo_qs.aggregate(total=Sum('total'))['total'] or Decimal('0')
        ordenes_periodo = ordenes_periodo_qs.count()
        ticket_promedio = (facturacion_periodo / ordenes_periodo) if ordenes_periodo else None

        fin_mes = hoy
        mes_anterior = _restar_meses(inicio_mes, 1)
        fin_mes_anterior = inicio_mes - timedelta(days=1)
        facturacion_mes = (
            _periodo(ordenes.exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO),
                     'created_at', inicio_mes, fin_mes).aggregate(total=Sum('total'))['total']
            or Decimal('0')
        )
        facturacion_anterior = (
            _periodo(ordenes.exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO),
                     'created_at', mes_anterior, fin_mes_anterior).aggregate(total=Sum('total'))['total']
            or Decimal('0')
        )
        if facturacion_anterior > 0:
            variacion = round(float((facturacion_mes - facturacion_anterior) / facturacion_anterior) * 100, 1)
        else:
            variacion = None
        ordenes_mes = _periodo(
            ordenes.exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO),
            'created_at', inicio_mes, fin_mes,
        ).count()

        # Pipeline de citas (estado actual + conversión dentro del periodo).
        citas_periodo_qs = citas.filter(fecha_cita__gte=fecha_desde, fecha_cita__lte=fecha_hasta)
        citas_periodo = citas_periodo_qs.count()
        citas_convertidas = citas_periodo_qs.filter(recepcion_generada__isnull=False).count()
        tasa_conversion = (
            round(citas_convertidas * 100.0 / citas_periodo, 1) if citas_periodo else None
        )

        # Cotizaciones por aprobar (estado actual) + aprobación del periodo.
        cotizaciones_pendientes_qs = cotizaciones.filter(estado=Cotizacion.EstadoCotizacion.ENVIADA)
        cotizaciones_pendientes = cotizaciones_pendientes_qs.count()
        monto_pendientes = cotizaciones_pendientes_qs.aggregate(total=Sum('total'))['total'] or Decimal('0')
        cot_periodo_qs = _periodo(cotizaciones, 'created_at', fecha_desde, fecha_hasta)
        cot_aceptadas = cot_periodo_qs.filter(
            estado=Cotizacion.EstadoCotizacion.ACEPTADA
        ).count()
        cot_rechazadas = cot_periodo_qs.filter(
            estado=Cotizacion.EstadoCotizacion.RECHAZADA
        ).count()
        cot_resueltas = cot_aceptadas + cot_rechazadas
        tasa_aprobacion = round(cot_aceptadas * 100.0 / cot_resueltas, 1) if cot_resueltas else None

        repuestos_bajo_stock = repuestos.filter(stock_actual__lte=F('stock_minimo'))

        kpis = {
            'vehiculos_en_taller': vehiculos_en_taller,
            'facturacion_mes': _dinero(facturacion_mes),
            'facturacion_mes_anterior': _dinero(facturacion_anterior),
            'variacion_facturacion_pct': variacion,
            'ordenes_mes': ordenes_mes,
            'ticket_promedio': _dinero(ticket_promedio) if ticket_promedio is not None else None,
            'facturacion_periodo': _dinero(facturacion_periodo),
            'ordenes_periodo': ordenes_periodo,
            'citas_hoy': citas.filter(fecha_cita=hoy).count(),
            'citas_pendientes_hoy': citas.filter(
                fecha_cita=hoy,
                estado__in=(Cita.EstadoCita.PROGRAMADA, Cita.EstadoCita.CONFIRMADA),
            ).count(),
            'citas_periodo': citas_periodo,
            'citas_convertidas_periodo': citas_convertidas,
            'tasa_conversion_cita_pct': tasa_conversion,
            'cotizaciones_pendientes': cotizaciones_pendientes,
            'monto_cotizaciones_pendientes': _dinero(monto_pendientes),
            'cotizaciones_periodo': cot_periodo_qs.count(),
            'cotizaciones_aceptadas_periodo': cot_aceptadas,
            'tasa_aprobacion_pct': tasa_aprobacion,
            'alertas_stock_bajo': repuestos_bajo_stock.count(),
            'repuestos_agotados': repuestos.filter(stock_actual__lte=0).count(),
        }

        # ── Tendencia: ingresos vs. órdenes creadas / completadas ──────────
        granularidad, buckets = _generar_buckets(fecha_desde, fecha_hasta)

        creadas_qs = _periodo(
            ordenes.exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO),
            'created_at', fecha_desde, fecha_hasta,
        )
        por_bucket = {
            fila['b'].date(): fila
            for fila in creadas_qs.annotate(
                b=_truncador('created_at', granularidad)
            ).values('b').annotate(ingresos=Sum('total'), ordenes=Count('id'))
            if fila['b']
        }

        # Completadas = entregadas por fecha de entrega (si no se registró, se
        # aproxima con la última actualización de la orden).
        cerradas_qs = ordenes.filter(estado__in=ESTADOS_OT_CERRADAS).filter(
            Q(fecha_entrega__isnull=False,
              fecha_entrega__date__gte=fecha_desde,
              fecha_entrega__date__lte=fecha_hasta)
            | Q(fecha_entrega__isnull=True,
                updated_at__date__gte=fecha_desde,
                updated_at__date__lte=fecha_hasta)
        )
        cierre_expr = Coalesce('fecha_entrega', 'updated_at')
        por_cierre = {
            fila['b'].date(): fila['n']
            for fila in cerradas_qs.annotate(
                b=_truncador(cierre_expr, granularidad)
            ).values('b').annotate(n=Count('id'))
            if fila['b']
        }

        tendencia = []
        for dia in buckets:
            creada = por_bucket.get(dia)
            largo = f'{dia.day} de {MESES_LARGOS[dia.month - 1]}'
            if granularidad == 'mes':
                largo = _etiqueta_mes(dia, largo=True)
                etiqueta = _etiqueta_mes(dia)
                clave = dia.strftime('%Y-%m')
            else:
                etiqueta = _etiqueta_dia(dia)
                clave = dia.isoformat()
                largo = f'{dia.day} de {MESES_LARGOS[dia.month - 1]} {dia.year}'
            tendencia.append({
                'clave': clave,
                'etiqueta': etiqueta,
                'etiqueta_larga': largo,
                'ingresos': _dinero(creada['ingresos']) if creada else 0.0,
                'ordenes_creadas': creada['ordenes'] if creada else 0,
                'ordenes_completadas': por_cierre.get(dia, 0),
            })

        # ── Distribuciones ─────────────────────────────────────────────────
        por_tipo = {
            fila['tipo_trabajo']: fila['n']
            for fila in _periodo(ordenes, 'created_at', fecha_desde, fecha_hasta)
            .exclude(estado=OrdenTrabajo.EstadoOrden.CANCELADO)
            .values('tipo_trabajo').annotate(n=Count('id'))
        }
        distribucion_tipo = [
            {'clave': clave, 'etiqueta': etiqueta, 'ordenes': por_tipo[clave]}
            for clave, etiqueta in OrdenTrabajo.TipoTrabajo.choices
            if clave in por_tipo
        ]
        distribucion_tipo.sort(key=lambda fila: -fila['ordenes'])

        por_estado = {
            fila['estado']: fila['n']
            for fila in _periodo(ordenes, 'created_at', fecha_desde, fecha_hasta)
            .values('estado').annotate(n=Count('id'))
        }
        distribucion_estado = [
            {'clave': clave, 'etiqueta': etiqueta, 'ordenes': por_estado[clave]}
            for clave, etiqueta in OrdenTrabajo.EstadoOrden.choices
            if clave in por_estado
        ]
        distribucion_estado.sort(
            key=lambda fila: ORDEN_ESTADOS_OT.index(fila['clave'])
        )

        # Categorías del catálogo de servicios usadas en las inspecciones del
        # periodo (MECANICA, ELECTRICO, MANTENIMIENTO, DIAGNOSTICO, ...).
        # `values('servicio__categoria')` expone el valor bajo esa misma clave.
        inspecciones_periodo = _periodo(inspecciones, 'fecha_inspeccion', fecha_desde, fecha_hasta)
        por_categoria = {
            fila['servicio__categoria']: fila['n']
            for fila in DetalleServicioInspeccion.objects.filter(
                inspeccion__in=inspecciones_periodo,
                servicio__isnull=False,
            ).values('servicio__categoria').annotate(n=Count('id'))
            if fila['servicio__categoria']
        }
        distribucion_categoria = [
            {'clave': clave, 'etiqueta': etiqueta, 'servicios': por_categoria[clave]}
            for clave, etiqueta in Servicio.Categoria.choices
            if clave in por_categoria
        ]
        distribucion_categoria.sort(key=lambda fila: -fila['servicios'])

        # ── Tabla: flujo operativo (recepciones del periodo) ───────────────
        recepciones_periodo = list(
            _periodo(recepciones, 'fecha_ingreso', fecha_desde, fecha_hasta)
            .select_related('cliente', 'vehiculo', 'sucursal')
            .order_by('-fecha_ingreso')[:MAX_FILAS_TABLA]
        )
        ids_recepcion = [recepcion.pk for recepcion in recepciones_periodo]
        inspecciones_por_recepcion = dict(
            InspeccionVehiculo.objects.filter(
                recepcion_id__in=ids_recepcion,
                is_active=True,
            ).values_list('recepcion_id', 'id')
        ) if ids_recepcion else {}

        # ── Tabla: citas del periodo ───────────────────────────────────────
        citas_periodo_lista = list(
            citas.filter(fecha_cita__gte=fecha_desde, fecha_cita__lte=fecha_hasta)
            .select_related('cliente', 'vehiculo', 'taller')
            .order_by('fecha_cita', 'hora_cita')[:MAX_FILAS_TABLA]
        )

        # ── Tabla: órdenes críticas / en proceso (estado actual) ───────────
        orden_critica = Case(
            When(prioridad=OrdenTrabajo.Prioridad.URGENTE, then=Value(0)),
            When(prioridad=OrdenTrabajo.Prioridad.ALTA, then=Value(1)),
            When(prioridad=OrdenTrabajo.Prioridad.MEDIA, then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
        orden_estado = Case(
            When(estado=OrdenTrabajo.EstadoOrden.EN_PROCESO, then=Value(0)),
            When(estado=OrdenTrabajo.EstadoOrden.EN_ESPERA, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
        ordenes_activas = list(
            ordenes.filter(estado__in=ESTADOS_OT_ABIERTAS)
            .select_related('cliente', 'vehiculo', 'asesor', 'mecanico_principal', 'sucursal')
            .order_by(orden_critica, orden_estado, 'fecha_ingreso')[:MAX_FILAS_TABLA]
        )

        # ── Repuestos con stock bajo ───────────────────────────────────────
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
                'agotado': repuesto.stock_actual <= 0,
            }
            for repuesto in repuestos_bajo_stock.order_by('stock_actual')[:5]
        ]

        ahora = timezone.now()
        return Response({
            'generado_en': ahora.isoformat(),
            'hoy': hoy.isoformat(),
            'periodo': {
                'desde': fecha_desde.isoformat(),
                'hasta': fecha_hasta.isoformat(),
                'dias': (fecha_hasta - fecha_desde).days + 1,
                'granularidad': granularidad,
            },
            'filtros': {
                'sucursal': sucursal.pk if sucursal else None,
                'sucursal_nombre': sucursal.nombre if sucursal else None,
            },
            'kpis': kpis,
            'tendencia': tendencia,
            'distribucion': {
                'tipo_trabajo': distribucion_tipo,
                'estado_orden': distribucion_estado,
                'categoria_servicio': distribucion_categoria,
            },
            'recepciones': [
                _serializar_recepcion_v2(
                    recepcion,
                    inspecciones_por_recepcion.get(recepcion.pk),
                )
                for recepcion in recepciones_periodo
            ],
            'citas': [_serializar_cita(cita, hoy) for cita in citas_periodo_lista],
            'ordenes_activas': [
                _serializar_orden_v2(orden, ahora) for orden in ordenes_activas
            ],
            'stock_bajo': stock_bajo,
        })

    # ── lectura de parámetros ──────────────────────────────────────────────
    @staticmethod
    def _leer_fecha(request, nombre, por_defecto):
        """Devuelve `(None, fecha)` o `(Response 400, None)`."""
        valor = request.query_params.get(nombre)
        if not valor:
            return None, por_defecto
        fecha = parse_date(valor)
        if not isinstance(fecha, date):
            return Response(
                {'detail': f'El campo {nombre} debe tener formato YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            ), None
        return None, fecha

    @staticmethod
    def _leer_sucursal(request, empresa_id):
        """Devuelve `(None, taller)` o `(Response 400, None)`."""
        valor = request.query_params.get('sucursal')
        if not valor:
            return None, None
        try:
            sucursal_id = int(valor)
        except (TypeError, ValueError):
            return Response(
                {'detail': 'El campo sucursal debe ser un identificador numérico.'},
                status=status.HTTP_400_BAD_REQUEST,
            ), None
        taller = Taller.objects.filter(
            pk=sucursal_id, empresa_id=empresa_id, is_active=True
        ).first()
        if taller is None:
            return Response(
                {'detail': 'La sucursal indicada no existe o no pertenece a la empresa.'},
                status=status.HTTP_400_BAD_REQUEST,
            ), None
        return None, taller

"""Cálculo y programación del próximo mantenimiento al entregar una OT.

Se dispara cuando la orden pasa a ENTREGADO (`notificaciones/signals.py`):

    km_base      = max(orden.kilometraje, vehiculo.kilometraje_actual)
    delta_km     = menor `intervalo_km` de los servicios de la OT, o el de la preferencia
    meses        = menor `intervalo_meses` de los servicios de la OT, o el de la preferencia
    km_objetivo  = km_base + delta_km
    fecha_tope   = fecha de entrega + meses

    km_por_dia   = (km_base - km de la entrega anterior) / días entre ambas
    fecha_por_km = fecha de entrega + delta_km / km_por_dia   (si hay historial)

    fecha_objetivo   = la menor entre fecha_tope y fecha_por_km
    fecha_programada = fecha_objetivo - dias_antelacion, a las HORA_ENVIO

El odómetro solo se lee cuando el vehículo vuelve al taller, por eso los km se
convierten en una fecha estimada con el ritmo de uso del propio vehículo.
"""

import calendar
import logging
import math
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.notificaciones.models import NotificacionProgramada
from .mensajes import construir_mensaje_mantenimiento
from .recordatorios import get_preferencia

logger = logging.getLogger('apps.notificaciones.mantenimiento')

# Tipos de trabajo que no implican un mantenimiento periódico.
TIPOS_SIN_MANTENIMIENTO = ('GARANTIA', 'ESTETICA', 'SINIESTRO')

# Hora local a la que salen los recordatorios de mantenimiento.
HORA_ENVIO = time(9, 0)

# Mínimo de días entre dos lecturas para que el ritmo de uso sea fiable.
DIAS_MINIMOS_HISTORIAL = 7


def sumar_meses(fecha, meses):
    """Suma meses de calendario; el día se ajusta al último del mes si no existe."""
    mes_total = fecha.month - 1 + meses
    anio = fecha.year + mes_total // 12
    mes = mes_total % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


def _intervalos_de_servicios(orden):
    """Menores intervalos (km, meses) definidos en los servicios de la OT."""
    kms, meses = [], []
    for detalle in orden.servicios.select_related('servicio'):
        servicio = detalle.servicio
        if servicio is None:
            continue
        if servicio.intervalo_km:
            kms.append(servicio.intervalo_km)
        if servicio.intervalo_meses:
            meses.append(servicio.intervalo_meses)
    return (min(kms) if kms else None), (min(meses) if meses else None)


def _km_por_dia(orden, km_base, fecha_entrega):
    """Ritmo de uso según la entrega anterior del mismo vehículo en la empresa."""
    from apps.ordenes.models import OrdenTrabajo

    anterior = (
        OrdenTrabajo.objects
        .filter(
            empresa_id=orden.empresa_id,
            vehiculo_id=orden.vehiculo_id,
            estado=OrdenTrabajo.EstadoOrden.ENTREGADO,
            fecha_entrega__lt=orden.fecha_entrega,
        )
        .exclude(pk=orden.pk)
        .order_by('-fecha_entrega')
        .values('kilometraje', 'fecha_entrega')
        .first()
    )
    if not anterior or anterior['kilometraje'] is None:
        return None
    dias = (fecha_entrega - timezone.localdate(anterior['fecha_entrega'])).days
    recorrido = km_base - anterior['kilometraje']
    if dias < DIAS_MINIMOS_HISTORIAL or recorrido <= 0:
        return None
    return recorrido / dias


def calcular_proximo_mantenimiento(orden, preferencia):
    """Devuelve el plan del próximo mantenimiento de una orden entregada."""
    vehiculo = orden.vehiculo
    fecha_entrega = timezone.localdate(orden.fecha_entrega or timezone.now())
    km_base = max(orden.kilometraje or 0, vehiculo.kilometraje_actual or 0)

    km_servicio, meses_servicio = _intervalos_de_servicios(orden)
    delta_km = km_servicio or preferencia.intervalo_km
    meses = meses_servicio or preferencia.intervalo_meses

    km_objetivo = km_base + delta_km
    fecha_tope = sumar_meses(fecha_entrega, meses)

    km_por_dia = _km_por_dia(orden, km_base, fecha_entrega)
    fecha_por_km = None
    if km_por_dia:
        fecha_por_km = fecha_entrega + timedelta(days=math.ceil(delta_km / km_por_dia))

    fecha_objetivo = min(f for f in (fecha_tope, fecha_por_km) if f is not None)
    dia_aviso = max(fecha_objetivo - timedelta(days=preferencia.dias_antelacion), fecha_entrega)
    fecha_programada = timezone.make_aware(
        datetime.combine(dia_aviso, HORA_ENVIO),
        timezone.get_current_timezone(),
    )

    return {
        'km_base': km_base,
        'delta_km': delta_km,
        'meses': meses,
        'km_objetivo': km_objetivo,
        'fecha_tope': fecha_tope,
        'km_por_dia': round(km_por_dia, 2) if km_por_dia else None,
        'fecha_por_km': fecha_por_km,
        'fecha_objetivo': fecha_objetivo,
        'motivo': 'km' if fecha_por_km and fecha_por_km < fecha_tope else 'tiempo',
        'fecha_programada': fecha_programada,
    }


def mensaje_mantenimiento(*, empresa, vehiculo, cliente, preferencia, km_objetivo, fecha_objetivo):
    return construir_mensaje_mantenimiento(
        cliente_nombre=cliente.nombre if cliente else '',
        placa=vehiculo.placa,
        marca=vehiculo.marca,
        modelo=vehiculo.modelo,
        kilometraje=vehiculo.kilometraje_actual,
        empresa_nombre=empresa.nombre_comercial,
        plantilla=preferencia.mensaje_plantilla,
        motivo_km=km_objetivo,
        motivo_fecha=fecha_objetivo,
    )


def reemplazar_mantenimiento_pendiente(*, empresa, vehiculo, km_objetivo, fecha_tope, fecha_objetivo, **campos):
    """Cancela el mantenimiento pendiente del vehículo y crea el nuevo.

    También deja el próximo mantenimiento (km y fecha) en la ficha del
    vehículo para que se vea en la pantalla y en el listado.
    """
    with transaction.atomic():
        NotificacionProgramada.objects.filter(
            empresa=empresa,
            vehiculo=vehiculo,
            tipo=NotificacionProgramada.Tipo.MANTENIMIENTO,
            estado=NotificacionProgramada.Estado.PENDIENTE,
        ).update(estado=NotificacionProgramada.Estado.CANCELADA, updated_at=timezone.now())

        notificacion = NotificacionProgramada.objects.create(
            empresa=empresa,
            vehiculo=vehiculo,
            tipo=NotificacionProgramada.Tipo.MANTENIMIENTO,
            km_objetivo=km_objetivo,
            fecha_tope=fecha_tope,
            **campos,
        )

        vehiculo.proximo_mantenimiento_km = km_objetivo
        vehiculo.proxima_mantenimiento_fecha = fecha_objetivo
        vehiculo.save(update_fields=['proximo_mantenimiento_km', 'proxima_mantenimiento_fecha', 'updated_at'])
    return notificacion


def programar_mantenimiento(orden):
    """Crea la alerta de próximo mantenimiento de una orden ENTREGADA.

    Returns:
        NotificacionProgramada creada, o None si no corresponde (tipo de
        trabajo sin mantenimiento o notificaciones desactivadas).
    """
    if orden.tipo_trabajo in TIPOS_SIN_MANTENIMIENTO:
        return None
    preferencia = get_preferencia(orden.empresa)
    if not preferencia.notificaciones_activas:
        return None

    plan = calcular_proximo_mantenimiento(orden, preferencia)
    return reemplazar_mantenimiento_pendiente(
        empresa=orden.empresa,
        vehiculo=orden.vehiculo,
        km_objetivo=plan['km_objetivo'],
        fecha_tope=plan['fecha_tope'],
        fecha_objetivo=plan['fecha_objetivo'],
        cliente=orden.cliente,
        orden=orden,
        fecha_programada=plan['fecha_programada'],
        origen=NotificacionProgramada.Origen.AUTO,
        asunto='Recordatorio de mantenimiento',
        mensaje=mensaje_mantenimiento(
            empresa=orden.empresa,
            vehiculo=orden.vehiculo,
            cliente=orden.cliente,
            preferencia=preferencia,
            km_objetivo=plan['km_objetivo'],
            fecha_objetivo=plan['fecha_objetivo'],
        ),
    )


def programar_mantenimiento_seguro(orden_pk):
    """Versión para `on_commit`: un fallo se registra en el log y no rompe la entrega."""
    from apps.ordenes.models import OrdenTrabajo

    try:
        orden = OrdenTrabajo.objects.select_related('empresa', 'vehiculo', 'cliente').get(pk=orden_pk)
        programar_mantenimiento(orden)
    except Exception:
        logger.exception('No se pudo programar el mantenimiento de la orden %s', orden_pk)


def programar_mantenimiento_manual(
    *, empresa, vehiculo, cliente, km_objetivo=None, fecha_objetivo=None,
    fecha_programada=None, canal='whatsapp', mensaje='', user=None,
):
    """Alta manual del próximo mantenimiento de un vehículo.

    Sin fecha objetivo se usa el tope por tiempo de la preferencia desde hoy
    (un objetivo solo en km no dice cuándo avisar). Sin `fecha_programada`, se
    avisa `dias_antelacion` antes de la fecha objetivo (nunca antes de hoy).
    """
    preferencia = get_preferencia(empresa)
    hoy = timezone.localdate()
    fecha_objetivo = fecha_objetivo or sumar_meses(hoy, preferencia.intervalo_meses)
    if fecha_programada is None:
        dia_aviso = max(fecha_objetivo - timedelta(days=preferencia.dias_antelacion), hoy)
        fecha_programada = timezone.make_aware(
            datetime.combine(dia_aviso, HORA_ENVIO),
            timezone.get_current_timezone(),
        )
    return reemplazar_mantenimiento_pendiente(
        empresa=empresa,
        vehiculo=vehiculo,
        km_objetivo=km_objetivo,
        fecha_tope=fecha_objetivo,
        fecha_objetivo=fecha_objetivo,
        cliente=cliente,
        canal=canal,
        fecha_programada=fecha_programada,
        origen=NotificacionProgramada.Origen.MANUAL,
        asunto='Recordatorio de mantenimiento',
        creado_por=user,
        mensaje=(mensaje or '').strip() or mensaje_mantenimiento(
            empresa=empresa,
            vehiculo=vehiculo,
            cliente=cliente,
            preferencia=preferencia,
            km_objetivo=km_objetivo,
            fecha_objetivo=fecha_objetivo,
        ),
    )

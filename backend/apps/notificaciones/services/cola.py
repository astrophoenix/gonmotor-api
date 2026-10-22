"""Procesamiento de la cola `NotificacionProgramada`.

`procesar_pendientes()` lo ejecutan el comando `procesar_notificaciones` y el
endpoint `POST /api/notificaciones/cron/procesar/` (pensado para un cron
externo cada 15 minutos). Cada notificación se toma en su propia transacción
con `select_for_update(skip_locked=True)`, así dos ejecuciones simultáneas
nunca envían la misma fila dos veces.

Reintentos: un fallo reintentable deja la fila PENDIENTE y la reprograma
`ESPERA_REINTENTO * intentos` más tarde; al llegar a `MAX_INTENTOS` (o ante un
fallo sin reintento, como un cliente sin contacto) queda en ERROR.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.notificaciones.models import NotificacionProgramada
from .despacho import enviar_notificacion

MAX_INTENTOS = 3
ESPERA_REINTENTO = timedelta(minutes=15)
LIMITE_POR_EJECUCION = 100


def pendientes_vencidas(ahora=None):
    """Notificaciones listas para enviarse (empresa activa, fecha ya alcanzada)."""
    ahora = ahora or timezone.now()
    return NotificacionProgramada.objects.filter(
        estado=NotificacionProgramada.Estado.PENDIENTE,
        fecha_programada__lte=ahora,
        empresa__is_active=True,
    )


def aplicar_resultado(notificacion, resultado, registro, ahora=None):
    """Actualiza estado, intentos y error de la notificación según el envío."""
    ahora = ahora or timezone.now()
    notificacion.intentos += 1
    if registro is not None:
        notificacion.registro = registro

    if resultado.get('ok'):
        notificacion.estado = NotificacionProgramada.Estado.ENVIADA
        notificacion.fecha_envio = ahora
        notificacion.ultimo_error = ''
    else:
        notificacion.ultimo_error = resultado.get('error') or resultado.get('descripcion') or 'Error desconocido'
        if resultado.get('reintentable') and notificacion.intentos < MAX_INTENTOS:
            notificacion.fecha_programada = ahora + ESPERA_REINTENTO * notificacion.intentos
        else:
            notificacion.estado = NotificacionProgramada.Estado.ERROR

    notificacion.save(update_fields=[
        'intentos', 'registro', 'estado', 'fecha_envio', 'ultimo_error',
        'fecha_programada', 'updated_at',
    ])
    return notificacion


def motivo_de_cancelacion(notificacion):
    """Razón para no enviar una notificación cuyo documento ya no la justifica."""
    cita = notificacion.cita
    if cita is not None and (cita.deleted_at is not None or cita.estado not in ('PROGRAMADA', 'CONFIRMADA')):
        return 'La cita ya no está programada.'
    campana = notificacion.campana
    if campana is not None and campana.estado == 'CANCELADA':
        return 'La campaña fue cancelada.'
    return ''


def procesar_notificacion(notificacion, *, user=None, origen='PROGRAMADO'):
    """Envía una notificación ya bloqueada y guarda el resultado."""
    motivo = motivo_de_cancelacion(notificacion)
    if motivo:
        notificacion.estado = NotificacionProgramada.Estado.CANCELADA
        notificacion.ultimo_error = motivo
        notificacion.save(update_fields=['estado', 'ultimo_error', 'updated_at'])
        return {'ok': False, 'cancelada': True, 'error': motivo}

    resultado, registro = enviar_notificacion(notificacion, user=user, origen=origen)
    aplicar_resultado(notificacion, resultado, registro)
    return resultado


def procesar_pendientes(*, ahora=None, limite=LIMITE_POR_EJECUCION):
    """Envía las notificaciones vencidas, una transacción por fila.

    Returns:
        dict con `procesadas`, `enviadas`, `reprogramadas`, `errores` y `canceladas`.
    """
    ahora = ahora or timezone.now()
    resumen = {'procesadas': 0, 'enviadas': 0, 'reprogramadas': 0, 'errores': 0, 'canceladas': 0}

    while resumen['procesadas'] < limite:
        with transaction.atomic():
            # `of=('self',)`: bloquea solo la fila de la cola, no la empresa.
            notificacion = (
                pendientes_vencidas(ahora)
                .select_for_update(skip_locked=True, of=('self',))
                .order_by('fecha_programada', 'pk')
                .first()
            )
            if notificacion is None:
                break
            procesar_notificacion(notificacion)

        resumen['procesadas'] += 1
        if notificacion.estado == NotificacionProgramada.Estado.ENVIADA:
            resumen['enviadas'] += 1
        elif notificacion.estado == NotificacionProgramada.Estado.ERROR:
            resumen['errores'] += 1
        elif notificacion.estado == NotificacionProgramada.Estado.CANCELADA:
            resumen['canceladas'] += 1
        else:
            resumen['reprogramadas'] += 1

    return resumen

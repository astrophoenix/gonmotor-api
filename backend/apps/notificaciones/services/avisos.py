"""Disparadores de avisos: retiro del vehículo y recordatorios de cita."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.notificaciones.models import NotificacionProgramada
from .cola import procesar_notificacion
from .mensajes import construir_mensaje_cita, construir_mensaje_retiro, construir_mensaje_saldo
from .recordatorios import get_preferencia

Tipo = NotificacionProgramada.Tipo
Estado = NotificacionProgramada.Estado

# Recordatorios de cita: tipo → antelación.
RECORDATORIOS_CITA = {
    Tipo.CITA_24H: timedelta(hours=24),
    Tipo.CITA_2H: timedelta(hours=2),
}
ESTADOS_CITA_ACTIVA = ('PROGRAMADA', 'CONFIRMADA')


class AvisoNoPermitido(Exception):
    """El aviso no puede enviarse en el estado actual del documento."""


# ── Retiro ────────────────────────────────────────────────────────────────
def ultimo_retiro_enviado(orden):
    return orden.notificaciones_programadas.filter(
        tipo=Tipo.RETIRO, estado=Estado.ENVIADA,
    ).order_by('-fecha_envio').first()


def notificar_retiro(orden, *, user=None, canal='whatsapp'):
    """Envía en el acto el aviso de "vehículo listo" de una orden COMPLETADA.

    Returns:
        tuple(NotificacionProgramada, dict): la notificación y el resultado del envío.
    """
    if orden.estado != 'COMPLETADO':
        raise AvisoNoPermitido(
            'Solo se avisa el retiro de una orden en estado Completado.'
        )
    with transaction.atomic():
        notificacion = NotificacionProgramada.objects.create(
            empresa=orden.empresa,
            tipo=Tipo.RETIRO,
            canal=canal,
            origen=NotificacionProgramada.Origen.MANUAL,
            fecha_programada=timezone.now(),
            cliente=orden.cliente,
            vehiculo=orden.vehiculo,
            orden=orden,
            asunto='Tu vehículo está listo',
            mensaje=construir_mensaje_retiro(orden),
            creado_por=user,
        )
        resultado = procesar_notificacion(notificacion, user=user, origen='MANUAL')
    return notificacion, resultado


# ── Saldo pendiente ───────────────────────────────────────────────────────
def notificar_saldo(orden, saldo, *, user=None, canal='whatsapp'):
    """Envía en el acto el recordatorio de saldo de una orden entregada a crédito.

    `saldo` lo calcula quien llama (`apps.pagos.services`) con la orden bloqueada.

    Returns:
        tuple(NotificacionProgramada, dict): la notificación y el resultado del envío.
    """
    if orden.estado != 'ENTREGADO' or saldo <= 0:
        raise AvisoNoPermitido('Solo se recuerda el saldo de una orden entregada con saldo pendiente.')
    with transaction.atomic():
        notificacion = NotificacionProgramada.objects.create(
            empresa=orden.empresa,
            tipo=Tipo.SALDO,
            canal=canal,
            origen=NotificacionProgramada.Origen.MANUAL,
            fecha_programada=timezone.now(),
            cliente=orden.cliente,
            vehiculo=orden.vehiculo,
            orden=orden,
            asunto='Saldo pendiente en el taller',
            mensaje=construir_mensaje_saldo(orden, saldo),
            creado_por=user,
        )
        resultado = procesar_notificacion(notificacion, user=user, origen='MANUAL')
    return notificacion, resultado


# ── Citas ─────────────────────────────────────────────────────────────────
def _cita_activa(cita):
    return (
        cita.estado in ESTADOS_CITA_ACTIVA
        and cita.deleted_at is None
        and cita.fecha_hora_programada is not None
    )


def sincronizar_recordatorios_cita(cita, ahora=None):
    """Deja los recordatorios de la cita acordes a su estado y fecha actuales.

    Cita activa (programada/confirmada): crea o mueve los avisos de 24 h y 2 h
    cuya hora aún no pasó, y cancela los que ya no llegan a tiempo. Cualquier
    otro estado (o la cita eliminada) cancela los pendientes.
    """
    ahora = ahora or timezone.now()
    pendientes = {
        n.tipo: n
        for n in NotificacionProgramada.objects.filter(cita=cita, estado=Estado.PENDIENTE)
    }
    activa = _cita_activa(cita) and get_preferencia(cita.empresa).recordatorios_cita_activos

    for tipo, antelacion in RECORDATORIOS_CITA.items():
        existente = pendientes.get(tipo)
        fecha = cita.fecha_hora_programada - antelacion if cita.fecha_hora_programada else None

        if not activa or fecha is None or fecha <= ahora:
            if existente:
                existente.estado = Estado.CANCELADA
                existente.save(update_fields=['estado', 'updated_at'])
            continue

        mensaje = construir_mensaje_cita(cita, int(antelacion.total_seconds() // 3600))
        if existente:
            existente.fecha_programada = fecha
            existente.mensaje = mensaje
            existente.cliente = cita.cliente
            existente.vehiculo = cita.vehiculo
            existente.save(update_fields=['fecha_programada', 'mensaje', 'cliente', 'vehiculo', 'updated_at'])
        else:
            # Un aviso ya enviado no se repite: su hora quedó atrás y cae en la
            # rama de arriba. Solo una cita reprogramada a futuro vuelve aquí.
            NotificacionProgramada.objects.create(
                empresa=cita.empresa,
                tipo=tipo,
                cita=cita,
                cliente=cita.cliente,
                vehiculo=cita.vehiculo,
                fecha_programada=fecha,
                asunto='Recordatorio de tu cita',
                mensaje=mensaje,
            )


def notificar_cita(cita, *, user=None, canal='whatsapp'):
    """Envía en el acto un aviso de la cita (acción «Enviar por WhatsApp/correo»).

    Returns:
        tuple(NotificacionProgramada, dict): la notificación y el resultado del envío.
    """
    if not _cita_activa(cita):
        raise AvisoNoPermitido('Solo se avisa una cita programada o confirmada.')
    with transaction.atomic():
        # Un aviso manual anterior que quedó reintentándose se reemplaza.
        NotificacionProgramada.objects.filter(
            cita=cita, tipo=Tipo.CITA, estado=Estado.PENDIENTE,
        ).update(estado=Estado.CANCELADA, updated_at=timezone.now())
        notificacion = NotificacionProgramada.objects.create(
            empresa=cita.empresa,
            tipo=Tipo.CITA,
            canal=canal,
            origen=NotificacionProgramada.Origen.MANUAL,
            fecha_programada=timezone.now(),
            cita=cita,
            cliente=cita.cliente,
            vehiculo=cita.vehiculo,
            asunto='Tu cita en el taller',
            mensaje=construir_mensaje_cita(cita),
            creado_por=user,
        )
        resultado = procesar_notificacion(notificacion, user=user, origen='MANUAL')
    return notificacion, resultado

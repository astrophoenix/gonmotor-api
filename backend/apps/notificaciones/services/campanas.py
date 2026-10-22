"""Audiencia, lanzamiento y cancelación de campañas masivas.

Solo reciben campañas los clientes activos de la empresa que aceptaron
promociones (`Cliente.acepta_promociones`) y tienen teléfono o correo.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.notificaciones.models import Campana, NotificacionProgramada
from .mensajes import construir_mensaje_campana


class CampanaNoEditable(Exception):
    """La campaña no admite la operación en su estado actual."""


def audiencia(campana):
    """QuerySet de clientes que recibirían la campaña según su segmento."""
    from apps.ordenes.models import OrdenTrabajo
    from apps.vehiculos.models import propietarios_actuales_en

    segmento = campana.segmento or {}
    clientes = (
        Cliente.objects
        .filter(empresa_id=campana.empresa_id, is_active=True, acepta_promociones=True)
        .filter(~Q(telefono='') | ~Q(email=''))
    )

    ids = segmento.get('clientes')
    if ids:
        clientes = clientes.filter(uuid__in=ids)

    marca = (segmento.get('marca') or '').strip()
    if marca:
        con_marca = propietarios_actuales_en(campana.empresa_id).filter(
            cliente=OuterRef('pk'), vehiculo__marca__icontains=marca,
        )
        clientes = clientes.filter(Exists(con_marca))

    def entregas_desde(dias):
        return OrdenTrabajo.objects.filter(
            cliente=OuterRef('pk'),
            estado=OrdenTrabajo.EstadoOrden.ENTREGADO,
            fecha_entrega__gte=timezone.now() - timedelta(days=int(dias)),
        )

    if segmento.get('sin_visita_dias'):
        clientes = clientes.exclude(Exists(entregas_desde(segmento['sin_visita_dias'])))
    if segmento.get('con_visita_dias'):
        clientes = clientes.filter(Exists(entregas_desde(segmento['con_visita_dias'])))

    return clientes.order_by('nombre')


def lanzar(campana, *, user=None):
    """Encola un mensaje por cliente de la audiencia y marca la campaña LANZADA."""
    if campana.estado != Campana.Estado.BORRADOR:
        raise CampanaNoEditable('Solo se lanza una campaña en borrador.')

    ahora = timezone.now()
    fecha = campana.fecha_programada if campana.fecha_programada and campana.fecha_programada > ahora else ahora
    with transaction.atomic():
        destinatarios = list(audiencia(campana))
        NotificacionProgramada.objects.bulk_create([
            NotificacionProgramada(
                empresa_id=campana.empresa_id,
                tipo=NotificacionProgramada.Tipo.MASIVA,
                canal=campana.canal,
                origen=NotificacionProgramada.Origen.MANUAL,
                fecha_programada=fecha,
                cliente=cliente,
                campana=campana,
                asunto=campana.asunto or campana.nombre,
                mensaje=construir_mensaje_campana(campana, cliente),
                creado_por=user,
            )
            for cliente in destinatarios
        ])
        campana.estado = Campana.Estado.LANZADA
        campana.fecha_lanzamiento = ahora
        campana.total_destinatarios = len(destinatarios)
        campana.save(update_fields=['estado', 'fecha_lanzamiento', 'total_destinatarios', 'updated_at'])
    return campana


def cancelar(campana):
    """Cancela la campaña y sus mensajes aún pendientes. Devuelve cuántos canceló."""
    if campana.estado == Campana.Estado.CANCELADA:
        raise CampanaNoEditable('La campaña ya está cancelada.')
    with transaction.atomic():
        canceladas = campana.notificaciones.filter(
            estado=NotificacionProgramada.Estado.PENDIENTE,
        ).update(estado=NotificacionProgramada.Estado.CANCELADA, updated_at=timezone.now())
        campana.estado = Campana.Estado.CANCELADA
        campana.save(update_fields=['estado', 'updated_at'])
    return canceladas

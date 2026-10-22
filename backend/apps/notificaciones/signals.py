"""Disparadores automáticos de notificaciones al cliente.

- Orden de trabajo que pasa a ENTREGADO → próximo mantenimiento programado.
- Cita creada, reprogramada, cancelada o eliminada → recordatorios 24 h / 2 h.

Todo corre en `on_commit`: si la operación del documento se revierte no queda
ninguna notificación, y un fallo aquí no rompe el guardado del documento.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.citas.models import Cita
from apps.core.models import post_soft_delete
from apps.ordenes.models import OrdenTrabajo

from .services.avisos import sincronizar_recordatorios_cita
from .services.mantenimiento import programar_mantenimiento_seguro

logger = logging.getLogger('apps.notificaciones.signals')

ENTREGADO = OrdenTrabajo.EstadoOrden.ENTREGADO


@receiver(pre_save, sender=OrdenTrabajo)
def _recordar_estado_anterior(sender, instance, raw=False, **kwargs):
    if raw:
        return
    instance._estado_anterior_notif = (
        OrdenTrabajo.all_objects.filter(pk=instance.pk).values_list('estado', flat=True).first()
        if instance.pk else None
    )


@receiver(post_save, sender=OrdenTrabajo)
def _programar_mantenimiento_al_entregar(sender, instance, raw=False, **kwargs):
    """Solo en la transición a ENTREGADO, no en cada guardado de una orden entregada."""
    if raw or instance.estado != ENTREGADO:
        return
    if getattr(instance, '_estado_anterior_notif', None) == ENTREGADO:
        return
    orden_pk = instance.pk
    transaction.on_commit(lambda: programar_mantenimiento_seguro(orden_pk))


def _sincronizar_cita_seguro(cita_pk):
    try:
        cita = Cita.all_objects.select_related('empresa', 'cliente', 'vehiculo', 'taller').get(pk=cita_pk)
        sincronizar_recordatorios_cita(cita)
    except Exception:
        logger.exception('No se pudieron sincronizar los recordatorios de la cita %s', cita_pk)


@receiver(post_save, sender=Cita)
def _recordatorios_al_guardar_cita(sender, instance, raw=False, **kwargs):
    if raw:
        return
    cita_pk = instance.pk
    transaction.on_commit(lambda: _sincronizar_cita_seguro(cita_pk))


@receiver(post_soft_delete, sender=Cita)
def _recordatorios_al_eliminar_cita(sender, instance, **kwargs):
    cita_pk = instance.pk
    transaction.on_commit(lambda: _sincronizar_cita_seguro(cita_pk))

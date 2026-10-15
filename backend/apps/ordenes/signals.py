from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import OrdenTrabajo, RecepcionVehiculo
from .relaciones_flujo import recepciones_de_orden


@receiver(pre_save, sender=OrdenTrabajo)
def _fecha_entrega_al_entregar(sender, instance, raw=False, **kwargs):
    """Una orden ENTREGADA siempre tiene fecha de entrega (por defecto, ahora)."""
    if raw:
        return
    if instance.estado == OrdenTrabajo.EstadoOrden.ENTREGADO and not instance.fecha_entrega:
        instance.fecha_entrega = timezone.now()


@receiver(post_save, sender=OrdenTrabajo)
def _salida_de_recepcion_al_entregar(sender, instance, raw=False, **kwargs):
    """Al entregar la orden, la recepción vinculada registra la salida del vehículo.

    La fecha de salida de las recepciones del flujo (directa, vía inspección o
    vía cotización; ver `recepciones_de_orden`) queda igual a la fecha de
    entrega de la orden, también si luego se corrige esa fecha.
    """
    if raw or instance.estado != OrdenTrabajo.EstadoOrden.ENTREGADO or not instance.fecha_entrega:
        return
    ids = list(recepciones_de_orden(instance).values_list('pk', flat=True))
    if not ids:
        return
    # `update()` no dispara `auto_now`: se actualiza `updated_at` a mano.
    RecepcionVehiculo.objects.filter(pk__in=ids).exclude(
        fecha_salida=instance.fecha_entrega
    ).update(fecha_salida=instance.fecha_entrega, updated_at=timezone.now())

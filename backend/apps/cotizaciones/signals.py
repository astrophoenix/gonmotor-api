from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import DetalleRepuestoCotizacion, DetalleServicioCotizacion


@receiver([post_save, post_delete], sender=DetalleServicioCotizacion)
def _recalcular_totales_servicios(sender, instance, **kwargs):
    if getattr(instance, 'cotizacion_id', None) is not None:
        cotizacion = instance.cotizacion
        if cotizacion is not None:
            cotizacion.recalcular_totales()


@receiver([post_save, post_delete], sender=DetalleRepuestoCotizacion)
def _recalcular_totales_repuestos(sender, instance, **kwargs):
    if getattr(instance, 'cotizacion_id', None) is not None:
        cotizacion = instance.cotizacion
        if cotizacion is not None:
            cotizacion.recalcular_totales()
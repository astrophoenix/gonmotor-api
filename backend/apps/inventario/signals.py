from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import MovimientoInventario, Repuesto


@receiver(post_save, sender=Repuesto)
def _stock_inicial(sender, instance, created, raw=False, **kwargs):
    """El stock con que nace un repuesto (pantalla, importación o admin) abre su kardex."""
    if raw or not created or not instance.stock_actual:
        return
    MovimientoInventario.objects.create(
        empresa_id=instance.empresa_id,
        repuesto=instance,
        tipo=MovimientoInventario.Tipo.AJUSTE,
        cantidad=instance.stock_actual,
        stock_resultante=instance.stock_actual,
        costo_unitario=instance.costo_referencial,
        motivo='Stock inicial',
    )

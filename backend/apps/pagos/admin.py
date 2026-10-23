from django.contrib import admin

from .models import PagoOrden


@admin.register(PagoOrden)
class PagoOrdenAdmin(admin.ModelAdmin):
    list_display = ('id', 'orden_trabajo', 'tipo', 'forma_pago', 'monto', 'fecha', 'anulado')
    list_filter = ('tipo', 'forma_pago', 'anulado')
    search_fields = ('referencia', 'orden_trabajo__numero_orden')
    raw_id_fields = ('orden_trabajo', 'registrado_por', 'anulado_por')

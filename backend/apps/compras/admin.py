from django.contrib import admin

from .models import Compra, DetalleCompra, PagoCompra


class DetalleCompraInline(admin.TabularInline):
    model = DetalleCompra
    extra = 0
    raw_id_fields = ('repuesto',)


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'proveedor', 'numero_factura', 'fecha', 'condicion', 'total', 'estado')
    list_filter = ('estado', 'condicion')
    search_fields = ('numero_factura', 'proveedor__nombre')
    raw_id_fields = ('proveedor', 'registrado_por', 'anulada_por')
    inlines = [DetalleCompraInline]


@admin.register(PagoCompra)
class PagoCompraAdmin(admin.ModelAdmin):
    list_display = ('id', 'compra', 'forma_pago', 'monto', 'fecha', 'anulado')
    raw_id_fields = ('compra', 'registrado_por', 'anulado_por')

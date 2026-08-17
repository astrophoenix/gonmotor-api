from django.contrib import admin

from .models import Cotizacion, DetalleRepuestoCotizacion, DetalleServicioCotizacion


@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    list_display = ('numero_cotizacion', 'empresa', 'cliente', 'vehiculo', 'estado', 'total', 'created_at')
    list_filter = ('estado', 'empresa', 'created_at')
    search_fields = ('numero_cotizacion', 'cliente__nombre', 'vehiculo__placa', 'empresa__nombre_comercial')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DetalleServicioCotizacion)
class DetalleServicioCotizacionAdmin(admin.ModelAdmin):
    list_display = ('cotizacion', 'descripcion', 'horas_estimadas', 'precio_unitario', 'subtotal')
    search_fields = ('descripcion', 'cotizacion__numero_cotizacion')


@admin.register(DetalleRepuestoCotizacion)
class DetalleRepuestoCotizacionAdmin(admin.ModelAdmin):
    list_display = ('cotizacion', 'descripcion', 'cantidad', 'precio_unitario_referencial', 'subtotal', 'es_opcional')
    list_filter = ('es_opcional',)
    search_fields = ('descripcion', 'codigo_repuesto', 'cotizacion__numero_cotizacion')

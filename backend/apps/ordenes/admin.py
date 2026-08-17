from django.contrib import admin

from .models import (
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioOrdenTrabajo,
    FotoRecepcion,
    OrdenTrabajo,
    RecepcionVehiculo,
)


@admin.register(OrdenTrabajo)
class OrdenTrabajoAdmin(admin.ModelAdmin):
    list_display = ('numero_orden', 'empresa', 'cliente', 'vehiculo', 'estado', 'prioridad', 'total', 'created_at')
    list_filter = ('estado', 'prioridad', 'tipo_trabajo', 'empresa')
    search_fields = ('numero_orden', 'cliente__nombre', 'vehiculo__placa', 'empresa__nombre_comercial')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DetalleServicioOrdenTrabajo)
class DetalleServicioOrdenTrabajoAdmin(admin.ModelAdmin):
    list_display = ('orden_trabajo', 'descripcion', 'mecanico_asignado', 'precio_unitario', 'subtotal', 'completado')
    list_filter = ('completado',)
    search_fields = ('descripcion', 'orden_trabajo__numero_orden')


@admin.register(DetalleRepuestoOrdenTrabajo)
class DetalleRepuestoOrdenTrabajoAdmin(admin.ModelAdmin):
    list_display = ('orden_trabajo', 'descripcion', 'cantidad', 'precio_unitario', 'subtotal', 'codigo_repuesto')
    search_fields = ('descripcion', 'codigo_repuesto', 'orden_trabajo__numero_orden')


@admin.register(RecepcionVehiculo)
class RecepcionVehiculoAdmin(admin.ModelAdmin):
    list_display = ('orden_trabajo', 'ingreso_en_grua', 'tiene_extintor', 'tiene_botiquin')
    search_fields = ('orden_trabajo__numero_orden',)


@admin.register(FotoRecepcion)
class FotoRecepcionAdmin(admin.ModelAdmin):
    list_display = ('recepcion', 'descripcion', 'created_at')
    search_fields = ('descripcion', 'recepcion__orden_trabajo__numero_orden')

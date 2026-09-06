from django.contrib import admin

from .models import Repuesto, Servicio


@admin.register(Repuesto)
class RepuestoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'categoria', 'marca', 'stock_actual', 'stock_minimo', 'precio_venta', 'is_active')
    list_filter = ('categoria', 'aplica_iva', 'is_active', 'empresa')
    search_fields = ('codigo', 'nombre', 'marca', 'numero_parte', 'proveedor')
    list_editable = ('stock_actual',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Servicio)
class ServicioAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'categoria', 'tiempo_estimado_minutos', 'precio_referencial', 'is_active')
    list_filter = ('categoria', 'is_active', 'empresa')
    search_fields = ('codigo', 'nombre', 'descripcion')
    readonly_fields = ('created_at', 'updated_at')
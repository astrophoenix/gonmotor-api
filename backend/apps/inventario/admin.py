from django.contrib import admin

from apps.core.admin import SoftDeleteAdminMixin

from .models import Repuesto, RepuestoImagen, Servicio


class RepuestoImagenInline(admin.TabularInline):
    model = RepuestoImagen
    extra = 1
    fields = ('imagen', 'es_principal')


@admin.register(Repuesto)
class RepuestoAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'categoria', 'marca', 'stock_actual', 'stock_minimo', 'precio_venta', 'is_active')
    list_filter = ('categoria', 'iva_porcentaje_defecto', 'is_active', 'empresa')
    search_fields = ('codigo', 'nombre', 'marca', 'numero_parte', 'proveedor')
    list_editable = ('stock_actual',)
    readonly_fields = ('created_at', 'updated_at')
    inlines = (RepuestoImagenInline,)


@admin.register(RepuestoImagen)
class RepuestoImagenAdmin(admin.ModelAdmin):
    list_display = ('id', 'repuesto', 'es_principal', 'created_at')
    list_filter = ('es_principal',)
    search_fields = ('repuesto__codigo', 'repuesto__nombre')
    readonly_fields = ('created_at',)


@admin.register(Servicio)
class ServicioAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'categoria', 'tiempo_estimado_minutos', 'precio_referencial', 'is_active')
    list_filter = ('categoria', 'is_active', 'empresa')
    search_fields = ('codigo', 'nombre', 'descripcion')
    readonly_fields = ('created_at', 'updated_at')
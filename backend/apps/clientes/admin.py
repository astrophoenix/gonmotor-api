from django.contrib import admin
from .models import Cliente
from apps.vehiculos.models import Vehiculo


class VehiculoInline(admin.TabularInline):
    """
    Permite ver y agregar vehículos directamente en la pantalla del Cliente.
    """
    model = Vehiculo
    extra = 1
    fields = ('placa', 'marca', 'modelo', 'anio', 'color', 'kilometraje_actual')
    show_change_link = True


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = (
        'identificacion',
        'nombre',
        'tipo_identificacion',
        'telefono',
        'email',
        'is_active',
        'created_at'
    )
    list_filter = ('tipo_identificacion', 'is_active', 'created_at')
    search_fields = ('identificacion', 'nombre', 'email', 'telefono', 'contifico_id')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [VehiculoInline]
    
    fieldsets = (
        ('Información Principal', {
            'fields': ('tipo_identificacion', 'identificacion', 'nombre')
        }),
        ('Contacto & Ubicación', {
            'fields': ('telefono', 'email', 'direccion')
        }),
        ('Integraciones & Estado', {
            'fields': ('contifico_id', 'is_active', 'created_at', 'updated_at')
        }),
    )
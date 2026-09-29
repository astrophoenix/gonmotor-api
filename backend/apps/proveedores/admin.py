from django.contrib import admin

from .models import Proveedor


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = (
        'identificacion',
        'nombre',
        'tipo_identificacion',
        'telefono',
        'email',
        'contacto',
        'is_active',
        'created_at'
    )
    list_filter = ('tipo_identificacion', 'is_active', 'created_at')
    search_fields = ('identificacion', 'nombre', 'email', 'telefono', 'contacto')
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        ('Información Principal', {
            'fields': ('empresa', 'tipo_identificacion', 'identificacion', 'nombre')
        }),
        ('Contacto & Ubicación', {
            'fields': ('telefono', 'email', 'direccion', 'contacto')
        }),
        ('Estado', {
            'fields': ('is_active', 'created_at', 'updated_at')
        }),
    )

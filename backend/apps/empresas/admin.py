from django.contrib import admin
from django.utils.html import format_html

from apps.core.admin import SoftDeleteAdminMixin
from .models import DetalleEmpresa, Empresa, Taller


class DetalleEmpresaInline(admin.StackedInline):
    model = DetalleEmpresa
    can_delete = False
    fields = ('condiciones',)


class TallerInline(admin.TabularInline):
    """
    Permite ver y agregar talleres directamente en la pantalla de Empresa.
    """
    model = Taller
    extra = 1
    fields = ('nombre', 'codigo_sucursal', 'direccion', 'telefono', 'is_active')
    show_change_link = True


@admin.register(Empresa)
class EmpresaAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        'nombre_comercial',
        'ruc',
        'email_contacto',
        'telefono',
        'logo_thumb',
        'is_active',
        'created_at'
    )
    list_filter = ('is_active', 'created_at')
    search_fields = ('nombre_comercial', 'razon_social', 'ruc', 'email_contacto')
    readonly_fields = ('created_at', 'updated_at', 'logo_preview')
    inlines = [DetalleEmpresaInline, TallerInline]
    
    fieldsets = (
        ('Información Principal', {
            'fields': ('nombre_comercial', 'razon_social', 'ruc')
        }),
        ('Contacto', {
            'fields': ('email_contacto', 'telefono')
        }),
        ('Logo', {
            'fields': ('logo', 'logo_preview')
        }),
        ('Estado', {
            'fields': ('is_active', 'deleted_at', 'created_at', 'updated_at')
        }),
    )

    @admin.display(description='Logo')
    def logo_thumb(self, obj):
        if obj.logo:
            return format_html('<img src="{}" style="height:40px;width:auto;" />', obj.logo.url)
        return '—'

    @admin.display(description='Vista previa')
    def logo_preview(self, obj):
        if obj.logo:
            return format_html('<img src="{}" style="max-height:200px;width:auto;" />', obj.logo.url)
        return 'Sin logo cargado.'


@admin.register(Taller)
class TallerAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        'nombre',
        'empresa',
        'codigo_sucursal',
        'direccion',
        'telefono',
        'is_active'
    )
    list_filter = ('empresa', 'is_active', 'created_at')
    search_fields = ('nombre', 'codigo_sucursal', 'direccion', 'empresa__nombre_comercial')
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        ('Empresa & Información', {
            'fields': ('empresa', 'nombre', 'codigo_sucursal')
        }),
        ('Ubicación & Contacto', {
            'fields': ('ciudad', 'direccion', 'telefono')
        }),
        ('Capacidad y Horario de Citas', {
            'fields': (
                ('capacidad_citas_dia', 'capacidad_simultanea'),
                ('hora_apertura', 'hora_cierre'),
            ),
        }),
        ('Secuencias de Documentos', {
            'fields': (
                (
                    'prefijo_recepcion', 'siguiente_recepcion', 'digitos_recepcion',
                ),
                (
                    'prefijo_inspeccion', 'siguiente_inspeccion', 'digitos_inspeccion',
                ),
                (
                    'prefijo_cotizacion', 'siguiente_cotizacion', 'digitos_cotizacion',
                ),
                (
                    'prefijo_ot', 'siguiente_ot', 'digitos_ot',
                ),
            ),
            'description': (
                'La numeración de Recepciones, Inspecciones, Cotizaciones y Órdenes '
                'de Trabajo de este taller se genera automáticamente con el prefijo, '
                'contador y dígitos configurados aquí.'
            ),
        }),
        ('Estado', {
            'fields': ('is_active', 'deleted_at', 'created_at', 'updated_at')
        }),
    )

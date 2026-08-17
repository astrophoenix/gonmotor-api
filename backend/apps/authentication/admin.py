from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'empresa',
        'rol',
        'taller_activo',
        'telefono',
        'is_active'
    )
    list_filter = ('rol', 'empresa', 'is_active', 'created_at')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'empresa__nombre_comercial', 'telefono')
    readonly_fields = ('created_at', 'updated_at')
    filter_horizontal = ('talleres',)  # Interfaz mejorada para seleccionar múltiples talleres
    
    fieldsets = (
        ('Usuario', {
            'fields': ('user', 'rol', 'telefono')
        }),
        ('Empresa & Talleres', {
            'fields': ('empresa', 'talleres', 'taller_activo')
        }),
        ('Estado', {
            'fields': ('is_active', 'created_at', 'updated_at')
        }),
    )

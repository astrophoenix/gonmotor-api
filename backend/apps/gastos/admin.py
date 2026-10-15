from django.contrib import admin

from apps.core.admin import SoftDeleteAdminMixin

from .models import Gasto


@admin.register(Gasto)
class GastoAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'empresa', 'taller', 'fecha', 'categoria', 'monto', 'descripcion')
    list_filter = ('empresa', 'taller', 'categoria', 'fecha')
    search_fields = ('descripcion',)

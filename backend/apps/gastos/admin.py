from django.contrib import admin

from .models import Gasto


@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'taller', 'fecha', 'categoria', 'monto', 'descripcion')
    list_filter = ('empresa', 'taller', 'categoria', 'fecha')
    search_fields = ('descripcion',)

from django.contrib import admin

from .models import Caja, MovimientoCaja, SesionCaja


@admin.register(Caja)
class CajaAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'taller', 'nombre')
    list_filter = ('empresa',)


@admin.register(SesionCaja)
class SesionCajaAdmin(admin.ModelAdmin):
    list_display = ('id', 'caja', 'estado', 'fecha_apertura', 'fecha_cierre', 'efectivo_esperado', 'diferencia')
    list_filter = ('estado',)
    raw_id_fields = ('abierta_por', 'cerrada_por', 'reabierta_por')


@admin.register(MovimientoCaja)
class MovimientoCajaAdmin(admin.ModelAdmin):
    list_display = ('id', 'sesion', 'tipo', 'monto', 'motivo', 'anulado')
    list_filter = ('tipo', 'anulado')
    raw_id_fields = ('sesion', 'gasto', 'registrado_por', 'anulado_por')

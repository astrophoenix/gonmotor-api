from django.contrib import admin, messages

# La app 'core' provee infraestructura base (BaseModel, SoftDeleteModel).
# No contiene modelos concretos con tabla propia para registrar en el admin.


class EstadoBorradoFilter(admin.SimpleListFilter):
    title = 'borrado'
    parameter_name = 'borrado'

    def lookups(self, request, model_admin):
        return (('vivos', 'No eliminados'), ('eliminados', 'Eliminados'))

    def queryset(self, request, queryset):
        if self.value() == 'vivos':
            return queryset.filter(deleted_at__isnull=True)
        if self.value() == 'eliminados':
            return queryset.filter(deleted_at__isnull=False)
        return queryset


class SoftDeleteAdminMixin:
    """Admin de modelos `SoftDeleteModel`: ve también los eliminados.

    La acción estándar "Eliminar" del admin es lógica (pasa por
    `QuerySet.delete`). El borrado físico solo está disponible con la acción
    explícita "Eliminar definitivamente".
    """

    actions = ['restaurar_seleccionados', 'eliminar_definitivamente']

    def get_queryset(self, request):
        qs = self.model.all_objects.get_queryset()
        ordering = self.get_ordering(request)
        if ordering:
            qs = qs.order_by(*ordering)
        return qs

    def get_list_filter(self, request):
        return (EstadoBorradoFilter, *super().get_list_filter(request))

    def get_readonly_fields(self, request, obj=None):
        campos = tuple(super().get_readonly_fields(request, obj))
        return campos if 'deleted_at' in campos else (*campos, 'deleted_at')

    @admin.action(description='Restaurar seleccionados')
    def restaurar_seleccionados(self, request, queryset):
        total = queryset.restaurar()
        self.message_user(request, f'{total} registro(s) restaurado(s).', messages.SUCCESS)

    @admin.action(description='Eliminar definitivamente (borrado físico)')
    def eliminar_definitivamente(self, request, queryset):
        total, _ = queryset.hard_delete()
        self.message_user(request, f'{total} fila(s) eliminada(s) físicamente.', messages.WARNING)

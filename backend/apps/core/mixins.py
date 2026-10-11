import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class FiltroTallerMixin:
    """Restringe el queryset al taller activo de la sesión.

    El taller se toma de la cabecera ``X-Taller-ID`` (que el frontend envía en
    toda petición) y se valida contra los talleres permitidos del usuario con
    ``talleres_filtro_de_request``. También admite un query param explícito que,
    si viene, tiene prioridad (útil para filtros manuales por taller).

    El filtro es **estricto**: solo se devuelven los registros cuyo campo de
    taller está entre los talleres aplicables. Los registros sin taller (NULL)
    quedan fuera.

    Política de alcance (``talleres_filtro_de_request``):

    - Administración (``ADMIN_EMPRESA`` / ``ADMIN_SISTEMA`` o superusuario):
      sin taller declarado no se filtra («Todos»); con taller, se recorta a ese.
    - Resto de roles: siempre se recortan a sus talleres permitidos (vacío → sin
      resultados), aunque no envíen la cabecera. Nunca ven toda la empresa.

    Atributos configurables por ViewSet:
        taller_filter_field: nombre del campo FK al taller (``'sucursal'`` o
            ``'taller'``). Si es ``None`` (por defecto) el mixin no hace nada.
        taller_query_param: query param que, de venir, manda (por defecto
            ``'sucursal'``).
    """

    taller_filter_field = None
    taller_query_param = 'sucursal'

    def filtrar_por_taller(self, queryset):
        """Aplica el recorte por taller activo si corresponde."""
        if not self.taller_filter_field:
            return queryset
        talleres = self._talleres_para_filtro()
        if talleres is None:
            return queryset
        if not talleres:
            return queryset.none()
        return queryset.filter(**{f'{self.taller_filter_field}_id__in': talleres})

    def _talleres_para_filtro(self):
        from apps.authentication.utils import talleres_filtro_de_request

        explicito = self.request.query_params.get(self.taller_query_param)
        return talleres_filtro_de_request(self.request, explicito=explicito)


class SoftDeleteDestroyMixin:
    """
    Estandariza la eliminación de registros con Soft Delete / Hard Delete.

    Algoritmo:
        1. Extrae el identificador visible del registro (nombre, placa, etc.).
           Si el atributo no existe, se utiliza el ID.
        2. Comprueba si el registro tiene relaciones operativas o financieras
           asociadas (órdenes, facturas, vehículos, etc.).
           - Con historial  -> Soft Delete (is_active = False).
           - Sin historial  -> Hard Delete (instance.delete()).
        3. Retorna HTTP 200 con un mensaje en español personalizado.
        4. Captura cualquier error inesperado, lo registra en logs y retorna
           HTTP 500 con un mensaje amigable.

    Atributos configurables por ViewSet:
        delete_identifier_fields: list[str]
            Campos del modelo a intentar para obtener el identificador visible.
            El primer campo que devuelva un valor no vacío se utiliza.
            Por defecto: ['nombre'].

        delete_relation_fields: list[str]
            Nombres de managers / relaciones a inspeccionar con .exists().
            Si alguna contiene registros, se realiza Soft Delete.
            Por defecto: [] (siempre Hard Delete a menos que se sobreescriba
            has_related_records).

    Métodos sobrescribibles:
        get_entity_name(instance): nombre singular de la entidad (ej. "cliente").
        get_delete_identifier(instance): identificador visible del registro.
        has_related_records(instance): True si el registro tiene relaciones.
    """

    delete_identifier_fields = ['nombre']
    delete_relation_fields = []

    def get_entity_name(self, instance):
        return str(instance._meta.verbose_name).lower()

    def get_delete_identifier(self, instance):
        for field_name in self.delete_identifier_fields:
            value = getattr(instance, field_name, None)
            if value is not None and str(value).strip():
                return str(value)
        return str(instance.pk)

    def has_related_records(self, instance):
        for field_name in self.delete_relation_fields:
            manager = getattr(instance, field_name, None)
            if manager is not None and hasattr(manager, 'exists'):
                if manager.exists():
                    return True
        return False

    def destroy(self, request, *args, **kwargs):
        identifier = None
        entity_name = None

        try:
            instance = self.get_object()
            entity_name = self.get_entity_name(instance)
            identifier = self.get_delete_identifier(instance)

            # raise Exception("Error forzado para pruebas de manejo de errores.")

            if self.has_related_records(instance):
                instance.is_active = False
                instance.save(update_fields=['is_active'])
                action_type = 'soft_delete'
            else:
                instance.delete()
                action_type = 'hard_delete'

            return Response(
                {
                    'status': 'success',
                    'action': action_type,
                    'entity': entity_name,
                    'identifier': identifier,
                    'message': f"El {entity_name} '{identifier}' fue eliminado correctamente.",
                },
                status=status.HTTP_200_OK
            )

        except APIException:
            raise

        except Exception as e:
            logger.error(
                f"Error al eliminar {entity_name or 'registro'} "
                f"(id={kwargs.get('pk', '?')}): {str(e)}"
            )

            display_name = identifier if identifier else 'este registro'

            return Response(
                {
                    'status': 'error',
                    'entity': entity_name,
                    'identifier': identifier,
                    'message': (
                        f"No pudimos completar la eliminación de '{display_name}' "
                        f"en este momento. Por favor, intente nuevamente en unos minutos."
                    ),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

logger = logging.getLogger(__name__)


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

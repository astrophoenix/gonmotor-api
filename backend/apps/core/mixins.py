import logging

from rest_framework import serializers as drf_serializers
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
    Estandariza la eliminación de registros: el borrado es SIEMPRE lógico.

    Algoritmo:
        1. Extrae el identificador visible del registro (nombre, placa, etc.).
           Si el atributo no existe, se utiliza el ID.
        2. Elimina lógicamente (`is_active=False` + `deleted_at=now()`, ver
           `SoftDeleteModel.delete`). El registro deja de aparecer en los
           listados y libera sus campos únicos; el historial que lo referencia
           se conserva intacto. El borrado físico solo existe vía `hard_delete()`.
        3. Retorna HTTP 200 con un mensaje en español personalizado.
        4. Captura cualquier error inesperado, lo registra en logs y retorna
           HTTP 500 con un mensaje amigable.

    Atributos configurables por ViewSet:
        delete_identifier_fields: list[str]
            Campos del modelo a intentar para obtener el identificador visible.
            El primer campo que devuelva un valor no vacío se utiliza.
            Por defecto: ['nombre'].

    Métodos sobrescribibles:
        get_entity_name(instance): nombre singular de la entidad (ej. "cliente").
        get_delete_identifier(instance): identificador visible del registro.
        perform_soft_delete(instance): ejecuta el borrado lógico (p. ej. el
            vehículo elimina su vínculo con la empresa, no el vehículo global).
    """

    delete_identifier_fields = ['nombre']

    def get_entity_name(self, instance):
        return str(instance._meta.verbose_name).lower()

    def get_delete_identifier(self, instance):
        for field_name in self.delete_identifier_fields:
            value = getattr(instance, field_name, None)
            if value is not None and str(value).strip():
                return str(value)
        return str(instance.pk)

    def perform_soft_delete(self, instance):
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        identifier = None
        entity_name = None

        try:
            instance = self.get_object()
            entity_name = self.get_entity_name(instance)
            identifier = self.get_delete_identifier(instance)

            self.perform_soft_delete(instance)
            action_type = 'soft_delete'

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


class ValidarTenantMixin:
    """Valida que las FKs del payload pertenezcan al tenant de la sesión.

    Mixin de serializer (va antes de ``serializers.ModelSerializer``). Evita
    escrituras cruzadas entre empresas: un id de cliente, vehículo o taller de
    otra empresa enviado en el payload se rechaza con 400 en vez de quedar
    enlazado al documento.

    - ``tenant_campos_empresa``: FKs a modelos con ``empresa_id`` directo; deben
      ser de la empresa activa y estar operativos (no suspendidos).
    - ``tenant_campos_vehiculo``: FKs a ``Vehiculo``; deben tener vínculo
      operativo con la empresa activa.
    - ``tenant_campo_taller``: FK al taller; debe estar entre los talleres
      permitidos del usuario (empresa + alcance del rol).

    Un valor igual al que ya tiene la instancia no se revalida: editar un
    documento antiguo no falla porque su cliente se haya eliminado después.

    Se engancha en ``to_internal_value`` (no en ``validate``) para que un
    ``validate()`` propio del serializer no lo anule.
    """

    tenant_campos_empresa = ('cliente',)
    tenant_campos_vehiculo = ('vehiculo',)
    tenant_campo_taller = 'sucursal'

    def _sin_cambio(self, campo, valor):
        if self.instance is None or isinstance(self.instance, (list, tuple)):
            return False
        return getattr(self.instance, f'{campo}_id', None) == getattr(valor, 'pk', valor)

    def to_internal_value(self, data):
        attrs = super().to_internal_value(data)
        request = self.context.get('request')
        if request is None:
            return attrs

        from apps.authentication.utils import get_contexto_desde_request, talleres_permitidos
        from apps.vehiculos.models import Vehiculo

        contexto = get_contexto_desde_request(request)
        if not contexto.hay_contexto:
            raise drf_serializers.ValidationError(
                'No hay una empresa activa para la sesión.'
            )

        errores = {}
        for campo in self.tenant_campos_empresa:
            valor = attrs.get(campo)
            if valor is None or self._sin_cambio(campo, valor):
                continue
            if valor.empresa_id != contexto.empresa_id or not getattr(valor, 'esta_operativo', True):
                errores[campo] = 'No pertenece a la empresa activa o no está disponible.'

        for campo in self.tenant_campos_vehiculo:
            valor = attrs.get(campo)
            if valor is None or self._sin_cambio(campo, valor):
                continue
            if not Vehiculo.objects.operativos_en(contexto.empresa_id).filter(pk=valor.pk).exists():
                errores[campo] = 'El vehículo no está registrado o activo en la empresa.'

        campo = self.tenant_campo_taller
        taller = attrs.get(campo) if campo else None
        if taller is not None and not self._sin_cambio(campo, taller):
            permitidos = talleres_permitidos(getattr(request, 'user', None), contexto)
            if not permitidos.filter(pk=taller.pk).exists():
                errores[campo] = 'No tienes acceso a ese taller.'

        if errores:
            raise drf_serializers.ValidationError(errores)
        return attrs

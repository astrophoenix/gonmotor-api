"""Serializers base con el ID público (ver ``apps/core/ids_publicos.py``).

``ModelSerializerPublico`` reemplaza a ``serializers.ModelSerializer`` en toda
la API. Para un modelo con ``uuid`` (``UuidPublicoModel``):

- ``id`` sale del ``uuid`` (el entero no se expone);
- toda FK generada automáticamente se lee y escribe por el UUID del modelo
  relacionado (``RelacionPublicaField``).

Para un modelo sin ``uuid`` se comporta exactamente como DRF (pk entera). Así
cada modelo pasa a UUID agregando el mixin, sin tocar los serializers que lo
referencian.
"""
import uuid

from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from rest_framework import serializers
from rest_framework.relations import PKOnlyObject

from .ids_publicos import campo_uuid, es_usuario, id_publico, tiene_id_publico, uuid_de_usuario


class RelacionPublicaField(serializers.PrimaryKeyRelatedField):
    """FK por UUID si el modelo relacionado lo tiene; si no, por pk."""

    default_error_messages = {
        'does_not_exist': 'El registro "{pk_value}" no existe.',
        'incorrect_type': 'Identificador no válido: se esperaba un ID, se recibió {data_type}.',
    }

    def _modelo_relacionado(self):
        if self.queryset is not None:
            return self.queryset.model
        # Campo de solo lectura: el modelo sale de la FK del serializer padre.
        serializer = self.parent
        if isinstance(serializer, serializers.ManyRelatedField):
            serializer = serializer.parent
        modelo = getattr(getattr(serializer, 'Meta', None), 'model', None)
        if modelo is None or not self.source_attrs:
            return None
        try:
            return modelo._meta.get_field(self.source_attrs[0]).related_model
        except FieldDoesNotExist:
            return None

    def _usa_uuid(self):
        modelo = self._modelo_relacionado()
        return modelo is not None and tiene_id_publico(modelo)

    def use_pk_only_optimization(self):
        # Con UUID hace falta el objeto (el uuid no está en la fila que lo
        # referencia): los ViewSets deben hacer `select_related` de esas FKs.
        # El usuario es la excepción: su UUID se resuelve por pk con caché.
        modelo = self._modelo_relacionado()
        return not self._usa_uuid() or es_usuario(modelo)

    def to_internal_value(self, data):
        if not self._usa_uuid():
            return super().to_internal_value(data)
        if isinstance(data, bool):
            self.fail('incorrect_type', data_type=type(data).__name__)
        try:
            valor = uuid.UUID(str(data))
        except ValueError:
            self.fail('incorrect_type', data_type=type(data).__name__)
        try:
            return self.get_queryset().get(**{campo_uuid(self._modelo_relacionado()): valor})
        except ObjectDoesNotExist:
            self.fail('does_not_exist', pk_value=data)

    def to_representation(self, value):
        request = self.context.get('request')
        if isinstance(value, PKOnlyObject):
            if es_usuario(self._modelo_relacionado()):
                return uuid_de_usuario(value.pk, request)
            return value.pk
        return id_publico(value, request)


class ModelSerializerPublico(serializers.ModelSerializer):
    serializer_related_field = RelacionPublicaField

    def get_fields(self):
        fields = super().get_fields()
        if 'id' in fields and tiene_id_publico(self.Meta.model):
            fuente = 'profile.uuid' if es_usuario(self.Meta.model) else 'uuid'
            fields['id'] = serializers.UUIDField(source=fuente, read_only=True)
        return fields

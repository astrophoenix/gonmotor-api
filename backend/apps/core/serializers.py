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

from .ids_publicos import campo_uuid, es_usuario, id_publico, tiene_id_publico, uuid_por_pk
from .utils.images import ImagenInvalida, archivo_desde_data_url, procesar_imagen


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
        # Siempre pk-only: el uuid se toma del objeto si ya está cargado
        # (`select_related`/prefetch) o se resuelve por pk con caché
        # (`get_attribute` y `to_representation`).
        return True

    def get_attribute(self, instance):
        """FK directa con ID público: el objeto si ya está en memoria; si no,
        solo su pk (sin consultar la fila completa del relacionado)."""
        if self._usa_uuid() and len(self.source_attrs) == 1:
            try:
                campo = instance._meta.get_field(self.source_attrs[0])
            except (AttributeError, FieldDoesNotExist):
                campo = None
            if campo is not None and campo.concrete and (campo.many_to_one or campo.one_to_one):
                if campo.is_cached(instance):
                    return campo.get_cached_value(instance)
                pk = getattr(instance, campo.attname)
                return None if pk is None else PKOnlyObject(pk=pk)
        return super().get_attribute(instance)

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
            if self._usa_uuid():
                return uuid_por_pk(self._modelo_relacionado(), value.pk, request)
            return value.pk
        return id_publico(value, request)


class ImagenProcesadaField(serializers.ImageField):
    """Imagen que pasa por ``procesar_imagen`` con el perfil indicado.

    Con ``campo_miniatura``, ``ModelSerializerPublico`` guarda también la
    miniatura en ese campo del modelo y, al leer, la reemplaza por la imagen
    completa si falta (fotos subidas antes de existir las miniaturas).
    """

    def __init__(self, perfil, campo_miniatura=None, **kwargs):
        self.perfil = perfil
        self.campo_miniatura = campo_miniatura
        super().__init__(**kwargs)

    def _archivo(self, data):
        # Sin la validación de Pillow de DRF: `procesar_imagen` hace la suya
        # (incluye HEIC, píxeles máximos y archivos falsos).
        return serializers.FileField.to_internal_value(self, data)

    def to_internal_value(self, data):
        try:
            procesada = procesar_imagen(self._archivo(data), self.perfil)
        except ImagenInvalida as error:
            raise serializers.ValidationError(str(error))
        procesada.archivo.miniatura = procesada.miniatura
        return procesada.archivo


class FirmaField(ImagenProcesadaField):
    """Firma del canvas: acepta un archivo (app, multipart) o un data URL
    ``data:image/png;base64,...`` (web). Se guarda como PNG."""

    def __init__(self, **kwargs):
        super().__init__(perfil='firma', **kwargs)

    def _archivo(self, data):
        try:
            archivo = archivo_desde_data_url(data, nombre='firma.png')
        except ImagenInvalida as error:
            raise serializers.ValidationError(str(error))
        return archivo if archivo is not None else super()._archivo(data)


def _fuera_de_alcance(objeto, request):
    """`objeto` es de un modelo con tenant y no está en el alcance de la petición.

    Mismo criterio que los listados (`para_request`): empresa activa y, si el
    modelo cuelga de un taller, talleres permitidos del usuario.
    """
    modelo = type(objeto)
    if not getattr(modelo, 'empresa_lookup', None) or not hasattr(modelo.objects, 'para_request'):
        return False
    return not modelo.objects.para_request(request).filter(pk=objeto.pk).exists()


def _campos_imagen_con_miniatura(serializer):
    return [
        campo for campo in serializer.fields.values()
        if isinstance(campo, ImagenProcesadaField) and campo.campo_miniatura
    ]


class ModelSerializerPublico(serializers.ModelSerializer):
    """Base de todos los serializers de la API.

    Además del ID público, aplica dos reglas de aislamiento que ningún
    serializer puede olvidar:

    - `empresa` es siempre de solo lectura: sale del contexto de la sesión.
    - Toda FK escrita hacia un registro con tenant (cliente, documento del
      flujo, repuesto…) debe estar en el alcance del usuario: su empresa y, si
      el registro cuelga de un taller, sus talleres permitidos. Un valor igual
      al que ya tiene la instancia no se revalida.
    """

    serializer_related_field = RelacionPublicaField

    def get_extra_kwargs(self):
        extra = super().get_extra_kwargs()
        extra.setdefault('empresa', {})['read_only'] = True
        return extra

    def get_fields(self):
        fields = super().get_fields()
        if 'id' in fields and tiene_id_publico(self.Meta.model):
            fuente = 'profile.uuid' if es_usuario(self.Meta.model) else 'uuid'
            fields['id'] = serializers.UUIDField(source=fuente, read_only=True)
        for campo in list(fields.values()):
            if isinstance(campo, ImagenProcesadaField) and campo.campo_miniatura:
                fields[campo.campo_miniatura] = serializers.ImageField(read_only=True)
        return fields

    def run_validation(self, data=serializers.empty):
        # Después de las validaciones propias del serializer: si alguna ya
        # rechaza el valor, su mensaje (más específico) es el que se ve. Esta
        # es la red de seguridad para lo que ninguna validación propia cubra.
        validados = super().run_validation(data)
        if validados is not None:
            self._validar_alcance_relaciones(validados)
        return validados

    def to_internal_value(self, data):
        validados = super().to_internal_value(data)
        for campo in _campos_imagen_con_miniatura(self):
            if campo.source in validados:
                validados[campo.campo_miniatura] = getattr(validados[campo.source], 'miniatura', None)
        return validados

    def _validar_alcance_relaciones(self, validados):
        request = self.context.get('request')
        if request is None:
            return
        instancia = None if isinstance(self.instance, (list, tuple)) else self.instance
        errores = {}
        for nombre, campo in self.fields.items():
            if campo.read_only or campo.source not in validados:
                continue
            if not isinstance(campo, (serializers.RelatedField, serializers.ManyRelatedField)):
                continue
            valor = validados[campo.source]
            muchos = isinstance(campo, serializers.ManyRelatedField)
            objetos = list(valor or []) if muchos else [valor]
            actual = None if (muchos or instancia is None) else getattr(instancia, f'{campo.source}_id', None)
            for objeto in objetos:
                if objeto is None or getattr(objeto, 'pk', None) is None:
                    continue
                if actual is not None and objeto.pk == actual:
                    continue
                if _fuera_de_alcance(objeto, request):
                    errores[nombre] = ['El registro indicado no existe o está fuera de tu alcance.']
                    break
        if errores:
            raise serializers.ValidationError(errores)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        for campo in _campos_imagen_con_miniatura(self):
            if campo.campo_miniatura in rep and not rep[campo.campo_miniatura]:
                rep[campo.campo_miniatura] = rep.get(campo.field_name)
        return rep

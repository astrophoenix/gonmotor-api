"""
ID público de los registros expuestos por la API.

El ``id`` entero sigue siendo la clave interna (FKs, joins, índices). Los
modelos que heredan ``UuidPublicoModel`` tienen además una columna ``uuid``
(UUIDv7) que es lo ÚNICO que sale y entra por la API: un id secuencial deja
recorrer registros probando números y revela el volumen del negocio.

Piezas:
- ``UuidPublicoModel``: mixin abstracto de modelo con la columna ``uuid``.
- ``id_publico(obj)``: id para respuestas armadas a mano (uuid si el modelo lo
  tiene, si no la pk). Así un módulo pasa a UUID sin tocar a quien lo cita.
- ``por_id_publico(qs, valor)``: el registro con ese UUID (entradas que no
  pasan por un serializer: query params, cuerpos de acciones).
- ``filtrar_por_id_publico(qs, campo, valor)``: filtros por query param
  (``?cliente=<uuid>``) sobre una FK.
- ``rellenar_uuid(app_label, modelo)``: paso ``RunPython`` para las
  migraciones que agregan la columna a tablas con datos.

Los serializers y ViewSets correspondientes viven en ``apps/core/serializers.py``
y ``apps/core/mixins.py``.

UUIDv7 (RFC 9562) empieza por el timestamp en milisegundos: se inserta
ordenado en el índice (como un autoincremental) en lugar de fragmentarlo como
un UUIDv4 aleatorio.
"""
import os
import time
import uuid

from django.core.exceptions import ValidationError
from django.db import models


def nuevo_uuid():
    """UUIDv7. Python 3.14 lo trae; en versiones previas se arma igual (RFC 9562)."""
    if hasattr(uuid, 'uuid7'):
        return uuid.uuid7()
    return _uuid7_respaldo()


def _uuid7_respaldo():
    """UUIDv7 sin `uuid.uuid7` (Python < 3.14): timestamp en ms + 74 bits aleatorios."""
    milisegundos = time.time_ns() // 1_000_000
    aleatorio = int.from_bytes(os.urandom(10), 'big')
    valor = (milisegundos & ((1 << 48) - 1)) << 80
    valor |= 0x7 << 76                                  # versión 7
    valor |= ((aleatorio >> 62) & 0xFFF) << 64          # rand_a (12 bits)
    valor |= 0b10 << 62                                 # variante RFC
    valor |= aleatorio & ((1 << 62) - 1)                # rand_b (62 bits)
    return uuid.UUID(int=valor)


class UuidPublicoModel(models.Model):
    """Agrega el ID público (``uuid``). Va antes de la base del modelo:
    ``class Cliente(UuidPublicoModel, BaseEmpresaModel)``."""

    uuid = models.UUIDField(
        default=nuevo_uuid,
        unique=True,
        editable=False,
        verbose_name='ID público',
    )

    class Meta:
        abstract = True


def tiene_id_publico(modelo):
    return any(f.name == 'uuid' for f in modelo._meta.concrete_fields)


def id_publico(obj):
    """ID que la API expone para `obj` (None si no hay objeto)."""
    if obj is None:
        return None
    return str(obj.uuid) if tiene_id_publico(type(obj)) else obj.pk


def referencia_corta(obj):
    """Referencia breve para mostrar cuando un registro no tiene número propio
    (``#A3F9C2``). Usa el final del UUID: el inicio es el timestamp y se repite
    entre registros creados en el mismo instante."""
    return f'#{str(obj.uuid)[-6:].upper()}'


def por_id_publico(queryset, valor):
    """``queryset`` acotado al registro con ese UUID (vacío si no es un UUID)."""
    try:
        return queryset.filter(uuid=uuid.UUID(str(valor)))
    except ValueError:
        return queryset.none()


def filtrar_por_id_publico(queryset, campo, valor):
    """``queryset`` filtrado por la FK ``campo`` con el id que envía el cliente.

    Si el modelo relacionado tiene ID público solo se acepta el UUID; un valor
    mal formado no encuentra nada (en vez de un 500).
    """
    if valor in (None, ''):
        return queryset
    relacionado = queryset.model._meta.get_field(campo).related_model
    if tiene_id_publico(relacionado):
        try:
            valor = uuid.UUID(str(valor))
        except ValueError:
            return queryset.none()
        return queryset.filter(**{f'{campo}__uuid': valor})
    try:
        return queryset.filter(**{f'{campo}_id': int(valor)})
    except (TypeError, ValueError, ValidationError):
        return queryset.none()


def rellenar_uuid(app_label, nombre_modelo):
    """Paso ``RunPython``: asigna un UUIDv7 a cada fila existente."""

    def _rellenar(apps, schema_editor):
        modelo = apps.get_model(app_label, nombre_modelo)
        manager = modelo._base_manager
        pendientes = manager.filter(uuid__isnull=True).order_by('pk').values_list('pk', flat=True)
        lote = []
        for pk in pendientes.iterator(chunk_size=2000):
            lote.append(modelo(pk=pk, uuid=nuevo_uuid()))
            if len(lote) == 2000:
                manager.bulk_update(lote, ['uuid'])
                lote = []
        if lote:
            manager.bulk_update(lote, ['uuid'])

    return _rellenar

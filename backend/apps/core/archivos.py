"""Borrado de archivos subidos que dejan de estar referenciados.

Django no borra el archivo de un `FileField`/`ImageField` al reemplazarlo ni
al borrar la fila. Estas señales, conectadas para todos los modelos con
archivos, lo hacen **después de confirmar la transacción**: si se revierte,
el archivo sigue ahí. El borrado lógico (`deleted_at`) no toca archivos,
porque el registro se puede restaurar.
"""
import logging

from django.apps import apps
from django.db import models, transaction
from django.db.models.signals import post_delete, post_init, post_save

logger = logging.getLogger(__name__)

ATRIBUTO = '_archivos_originales'


def campos_archivo(modelo):
    return [f for f in modelo._meta.concrete_fields if isinstance(f, models.FileField)]


def _borrar_al_confirmar(campo, nombre):
    if not nombre:
        return

    def borrar():
        try:
            campo.storage.delete(nombre)
        except OSError:
            logger.warning('No se pudo borrar el archivo %s', nombre, exc_info=True)

    transaction.on_commit(borrar)


def _nombre_guardado(valor):
    """Nombre en el storage, o '' si aún no hay archivo guardado."""
    if isinstance(valor, str):
        return valor
    if getattr(valor, '_committed', False):  # FieldFile ya guardado
        return valor.name or ''
    return ''  # None o un archivo nuevo sin guardar


def _recordar(sender, instance, **kwargs):
    # Solo los campos ya cargados: leer uno diferido (`.only()`) consultaría la BD.
    instance.__dict__[ATRIBUTO] = {
        campo.attname: _nombre_guardado(instance.__dict__[campo.attname])
        for campo in campos_archivo(sender)
        if campo.attname in instance.__dict__
    }


def _al_guardar(sender, instance, created, update_fields=None, **kwargs):
    originales = instance.__dict__.get(ATRIBUTO, {})
    for campo in campos_archivo(sender):
        if update_fields is not None and campo.name not in update_fields:
            continue
        anterior = originales.get(campo.attname)
        if anterior and anterior != _nombre_guardado(getattr(instance, campo.attname)):
            _borrar_al_confirmar(campo, anterior)
    _recordar(sender, instance)


def _al_borrar(sender, instance, **kwargs):
    for campo in campos_archivo(sender):
        if campo.attname in instance.__dict__:
            _borrar_al_confirmar(campo, getattr(instance, campo.attname).name)


def conectar():
    for modelo in apps.get_models():
        if modelo._meta.abstract or modelo._meta.proxy or not campos_archivo(modelo):
            continue
        post_init.connect(_recordar, sender=modelo, dispatch_uid=f'archivos-init-{modelo._meta.label}')
        post_save.connect(_al_guardar, sender=modelo, dispatch_uid=f'archivos-save-{modelo._meta.label}')
        post_delete.connect(_al_borrar, sender=modelo, dispatch_uid=f'archivos-delete-{modelo._meta.label}')

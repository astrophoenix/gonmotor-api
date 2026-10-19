"""Guardián estructural de los serializers de la API.

`ModelSerializerPublico` aplica las reglas de aislamiento (empresa de solo
lectura y FKs dentro del alcance del usuario). Estos tests recorren todos los
serializers del proyecto para que un serializer nuevo no pueda saltárselas.
"""

import importlib
import pkgutil

from django.apps import apps as django_apps
from django.test import SimpleTestCase
from rest_framework import serializers

from apps.core.serializers import ModelSerializerPublico


def _serializers_del_proyecto():
    """Clases `ModelSerializer` definidas en los módulos `serializers` de `apps.*`."""
    for config in django_apps.get_app_configs():
        if not config.name.startswith('apps.'):
            continue
        try:
            paquete = importlib.import_module(f'{config.name}.serializers')
        except ModuleNotFoundError:
            continue
        modulos = [paquete]
        if hasattr(paquete, '__path__'):
            modulos += [
                importlib.import_module(f'{paquete.__name__}.{nombre}')
                for _, nombre, _ in pkgutil.iter_modules(paquete.__path__)
            ]
        for modulo in modulos:
            for valor in vars(modulo).values():
                if (
                    isinstance(valor, type)
                    and issubclass(valor, serializers.ModelSerializer)
                    and valor.__module__ == modulo.__name__
                    and hasattr(valor, 'Meta')
                ):
                    yield valor


class GuardianSerializersTests(SimpleTestCase):

    def test_todos_heredan_de_model_serializer_publico(self):
        fuera = [
            f'{clase.__module__}.{clase.__name__}'
            for clase in _serializers_del_proyecto()
            if not issubclass(clase, ModelSerializerPublico)
        ]
        self.assertEqual(fuera, [], 'Heredar de ModelSerializerPublico (reglas de aislamiento).')

    def test_empresa_nunca_es_escribible(self):
        escribibles = []
        for clase in _serializers_del_proyecto():
            campos = clase().fields
            if 'empresa' in campos and not campos['empresa'].read_only:
                escribibles.append(f'{clase.__module__}.{clase.__name__}')
        self.assertEqual(escribibles, [], 'La empresa sale de la sesión, nunca del payload.')

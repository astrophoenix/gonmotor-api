from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'apps.core'

    def ready(self):
        from . import archivos
        from .lookups import registrar

        registrar()
        archivos.conectar()

from django.apps import AppConfig


class OrdenesConfig(AppConfig):
    name = 'apps.ordenes'

    def ready(self):
        from . import signals  # noqa: F401

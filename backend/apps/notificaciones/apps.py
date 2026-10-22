from django.apps import AppConfig


class NotificacionesConfig(AppConfig):
    name = 'apps.notificaciones'
    verbose_name = 'Notificaciones al cliente'

    def ready(self):
        from . import signals  # noqa: F401
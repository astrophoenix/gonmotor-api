from django.apps import AppConfig


class AuthenticationConfig(AppConfig):
    name = 'apps.authentication'

    def ready(self):
        from django.db.models.signals import post_migrate

        from .signals import sembrar_permisos_tras_el_migrate

        post_migrate.connect(sembrar_permisos_tras_el_migrate, sender=self)

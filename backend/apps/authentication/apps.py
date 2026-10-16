from django.apps import AppConfig


class AuthenticationConfig(AppConfig):
    name = 'apps.authentication'

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_migrate, post_save

        from .signals import crear_perfil_de_usuario, sembrar_permisos_tras_el_migrate

        post_migrate.connect(sembrar_permisos_tras_el_migrate, sender=self)
        post_save.connect(crear_perfil_de_usuario, sender=get_user_model(), dispatch_uid='crear_perfil_de_usuario')

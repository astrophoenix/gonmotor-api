from .permisos_repo import sembrar_permisos_si_vacio


def sembrar_permisos_tras_el_migrate(sender, **kwargs):
    """Se siembra la matriz en la BD tras aplicar migraciones (dev y tests)."""
    sembrar_permisos_si_vacio()

def crear_perfil_de_usuario(sender, instance, created, raw=False, **kwargs):
    """Todo usuario tiene perfil: su `uuid` es el ID público del usuario."""
    if created and not raw:
        from .models import UserProfile

        # Por `user_id`: con `user=instance` el perfil recién creado quedaría en
        # caché dentro del usuario y una actualización posterior no se vería.
        UserProfile.objects.get_or_create(user_id=instance.pk)

from .permisos_repo import sembrar_permisos_si_vacio


def sembrar_permisos_tras_el_migrate(sender, **kwargs):
    """Se siembra la matriz en la BD tras aplicar migraciones (dev y tests)."""
    sembrar_permisos_si_vacio()
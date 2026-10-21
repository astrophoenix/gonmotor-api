from django.db import migrations


def crear_recurso(apps, schema_editor):
    Recurso = apps.get_model('authentication', 'Recurso')
    Rol = apps.get_model('authentication', 'Rol')
    Permiso = apps.get_model('authentication', 'Permiso')
    # En una base nueva post_migrate siembra todo el catálogo de una vez.
    if not Rol.objects.exists():
        return
    recurso, _ = Recurso.objects.get_or_create(
        codigo='auditoria', defaults={'nombre': 'Auditoría', 'tipo': 'MODULO', 'orden': 100},
    )
    for rol in Rol.objects.filter(codigo__in=['ADMIN_SISTEMA', 'ADMIN_EMPRESA']):
        Permiso.objects.get_or_create(rol=rol, recurso=recurso, defaults={'ver': True, 'modificar': False})


class Migration(migrations.Migration):
    dependencies = [('authentication', '0017_acciones_dashboard')]
    operations = [migrations.RunPython(crear_recurso, migrations.RunPython.noop)]

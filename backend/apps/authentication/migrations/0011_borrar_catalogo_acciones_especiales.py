"""Vacía el catálogo de acciones especiales: aún no hay funcionalidad que lo use.

`AccionEspecial` se sembraba desde `ACCIONES_CATALOGO` en `sembrar_permisos_si_vacio`,
pero fuera de los recursos no existe todavía ninguna acción del sistema real.
Se conserva el esquema (modelo + overrides por rol/empresa) para cuando existan.
"""

from django.db import migrations


def borrar_catalogo_acciones(apps, schema_editor):
    AccionEspecial = apps.get_model('authentication', 'AccionEspecial')
    AccionEspecial.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0010_permisoempresa_permisoespecialempresa'),
    ]

    operations = [
        migrations.RunPython(borrar_catalogo_acciones, migrations.RunPython.noop),
    ]
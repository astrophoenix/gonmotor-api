"""Crea el perfil de los usuarios que no lo tienen.

El ID público del usuario (UUID) vive en su `UserProfile` (ver
`0015_uuid_publico.py`). Migración aparte: Postgres no permite alterar la tabla
en la misma transacción en que se insertaron filas con FKs diferidas.
"""
from django.db import migrations


def crear_perfiles_faltantes(apps, schema_editor):
    """El ID público del usuario vive en su perfil: todo usuario debe tener uno."""
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('authentication', 'UserProfile')
    UserProfile.objects.bulk_create(
        [UserProfile(user_id=pk) for pk in User.objects.filter(profile__isnull=True).values_list('pk', flat=True)]
    )


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0013_recursos_gastos_notificaciones'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(crear_perfiles_faltantes, migrations.RunPython.noop),
    ]

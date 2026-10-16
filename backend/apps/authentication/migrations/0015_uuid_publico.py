"""ID público (UUIDv7) de UserProfile, UsuarioEmpresa; ver `apps/core/ids_publicos.py`.

Por modelo: columna nullable, relleno fila por fila y luego única y obligatoria
(igual que `clientes/migrations/0007_cliente_uuid.py`).
"""
from django.db import migrations, models

import apps.core.ids_publicos



class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0014_perfiles_faltantes'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('authentication', 'UserProfile'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='usuarioempresa',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('authentication', 'UsuarioEmpresa'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='usuarioempresa',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
    ]

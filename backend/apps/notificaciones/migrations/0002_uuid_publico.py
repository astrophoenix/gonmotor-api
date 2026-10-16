"""ID público (UUIDv7) de PreferenciaMantenimiento, RegistroMensajeWhatsApp; ver `apps/core/ids_publicos.py`.

Por modelo: columna nullable, relleno fila por fila y luego única y obligatoria
(igual que `clientes/migrations/0007_cliente_uuid.py`).
"""
from django.db import migrations, models

import apps.core.ids_publicos


class Migration(migrations.Migration):

    dependencies = [
        ('notificaciones', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='preferenciamantenimiento',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('notificaciones', 'PreferenciaMantenimiento'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='preferenciamantenimiento',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='registromensajewhatsapp',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('notificaciones', 'RegistroMensajeWhatsApp'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='registromensajewhatsapp',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
    ]

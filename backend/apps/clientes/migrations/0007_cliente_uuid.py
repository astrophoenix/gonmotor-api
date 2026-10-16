"""ID público (UUIDv7) del cliente; ver `apps/core/ids_publicos.py`.

En tres pasos para tablas con datos: columna nullable, relleno fila por fila
(un default callable en `AddField` daría el mismo valor a todas) y luego
única y obligatoria.
"""
from django.db import migrations, models

import apps.core.ids_publicos


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0006_remove_cliente_unique_cliente_per_empresa_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('clientes', 'Cliente'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='cliente',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
    ]

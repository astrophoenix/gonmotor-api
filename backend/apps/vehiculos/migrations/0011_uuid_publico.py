"""ID público (UUIDv7) de Vehiculo; ver `apps/core/ids_publicos.py`.

Por modelo: columna nullable, relleno fila por fila y luego única y obligatoria
(igual que `clientes/migrations/0007_cliente_uuid.py`).
"""
from django.db import migrations, models

import apps.core.ids_publicos


class Migration(migrations.Migration):

    dependencies = [
        ('vehiculos', '0010_backfill_vinculos_eliminados'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiculo',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('vehiculos', 'Vehiculo'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='vehiculo',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
    ]

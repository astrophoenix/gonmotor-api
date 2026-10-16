"""ID público (UUIDv7) de Gasto; ver `apps/core/ids_publicos.py`.

Por modelo: columna nullable, relleno fila por fila y luego única y obligatoria
(igual que `clientes/migrations/0007_cliente_uuid.py`).
"""
from django.db import migrations, models

import apps.core.ids_publicos


class Migration(migrations.Migration):

    dependencies = [
        ('gastos', '0002_gasto_deleted_at_gasto_gasto_tal_vivos_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='gasto',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('gastos', 'Gasto'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='gasto',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
    ]

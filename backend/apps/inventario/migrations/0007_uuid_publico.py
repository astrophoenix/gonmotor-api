"""ID público (UUIDv7) de Repuesto, RepuestoImagen, Servicio; ver `apps/core/ids_publicos.py`.

Por modelo: columna nullable, relleno fila por fila y luego única y obligatoria
(igual que `clientes/migrations/0007_cliente_uuid.py`).
"""
from django.db import migrations, models

import apps.core.ids_publicos


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0006_remove_repuesto_repuesto_codigo_activo_por_empresa_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='repuesto',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('inventario', 'Repuesto'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='repuesto',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='repuestoimagen',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('inventario', 'RepuestoImagen'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='repuestoimagen',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='servicio',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('inventario', 'Servicio'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='servicio',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
    ]

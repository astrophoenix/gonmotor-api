"""ID público (UUIDv7) de Cotizacion, DetalleServicioCotizacion, DetalleRepuestoCotizacion; ver `apps/core/ids_publicos.py`.

Por modelo: columna nullable, relleno fila por fila y luego única y obligatoria
(igual que `clientes/migrations/0007_cliente_uuid.py`).
"""
from django.db import migrations, models

import apps.core.ids_publicos


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0021_indices_empresa_created_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='cotizacion',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('cotizaciones', 'Cotizacion'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='cotizacion',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='detalleserviciocotizacion',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('cotizaciones', 'DetalleServicioCotizacion'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='detalleserviciocotizacion',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='detallerepuestocotizacion',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('cotizaciones', 'DetalleRepuestoCotizacion'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='detallerepuestocotizacion',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
    ]

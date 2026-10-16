"""ID público (UUIDv7) de OrdenTrabajo, DetalleServicioOrdenTrabajo, DetalleRepuestoOrdenTrabajo, RecepcionVehiculo, InspeccionVehiculo, DetalleServicioInspeccion, DetalleRepuestoInspeccion, FotoRecepcion, FotoInspeccion, FotoOrdenTrabajo; ver `apps/core/ids_publicos.py`.

Por modelo: columna nullable, relleno fila por fila y luego única y obligatoria
(igual que `clientes/migrations/0007_cliente_uuid.py`).
"""
from django.db import migrations, models

import apps.core.ids_publicos


class Migration(migrations.Migration):

    dependencies = [
        ('ordenes', '0050_indices_empresa_created_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordentrabajo',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'OrdenTrabajo'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='ordentrabajo',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='detalleservicioordentrabajo',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'DetalleServicioOrdenTrabajo'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='detalleservicioordentrabajo',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='detallerepuestoordentrabajo',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'DetalleRepuestoOrdenTrabajo'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='detallerepuestoordentrabajo',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='recepcionvehiculo',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'RecepcionVehiculo'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='recepcionvehiculo',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='inspeccionvehiculo',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'InspeccionVehiculo'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='inspeccionvehiculo',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='detalleservicioinspeccion',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'DetalleServicioInspeccion'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='detalleservicioinspeccion',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='detallerepuestoinspeccion',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'DetalleRepuestoInspeccion'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='detallerepuestoinspeccion',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='fotorecepcion',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'FotoRecepcion'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='fotorecepcion',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='fotoinspeccion',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'FotoInspeccion'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='fotoinspeccion',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
        migrations.AddField(
            model_name='fotoordentrabajo',
            name='uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='ID público'),
        ),
        migrations.RunPython(
            apps.core.ids_publicos.rellenar_uuid('ordenes', 'FotoOrdenTrabajo'),
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='fotoordentrabajo',
            name='uuid',
            field=models.UUIDField(
                default=apps.core.ids_publicos.nuevo_uuid,
                editable=False,
                unique=True,
                verbose_name='ID público',
            ),
        ),
    ]

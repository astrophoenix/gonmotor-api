"""Kilometraje obligatorio en la orden de trabajo.

Se agrega nullable, se rellena con la lectura más alta de la visita (recepción
o inspección enlazada) o, si no hay, con el kilometraje actual del vehículo, y
luego se vuelve obligatorio.
"""
from django.db import migrations, models


def rellenar_kilometraje(apps, schema_editor):
    OrdenTrabajo = apps.get_model('ordenes', 'OrdenTrabajo')
    RecepcionVehiculo = apps.get_model('ordenes', 'RecepcionVehiculo')
    InspeccionVehiculo = apps.get_model('ordenes', 'InspeccionVehiculo')

    lecturas = {}
    for ot_id, km in (
        RecepcionVehiculo._base_manager.filter(
            orden_trabajo__isnull=False, kilometraje_ingreso__isnull=False
        ).values_list('orden_trabajo_id', 'kilometraje_ingreso')
    ):
        lecturas[ot_id] = max(km, lecturas.get(ot_id, 0))
    for ot_id, km in (
        InspeccionVehiculo._base_manager.filter(
            orden_trabajo__isnull=False, kilometraje_diagnostico__isnull=False
        ).values_list('orden_trabajo_id', 'kilometraje_diagnostico')
    ):
        lecturas[ot_id] = max(km, lecturas.get(ot_id, 0))

    pendientes = OrdenTrabajo._base_manager.filter(kilometraje__isnull=True).values_list(
        'pk', 'vehiculo__kilometraje_actual'
    )
    for pk, km_vehiculo in pendientes.iterator():
        km = lecturas.get(pk, km_vehiculo or 0)
        OrdenTrabajo._base_manager.filter(pk=pk).update(kilometraje=km)


class Migration(migrations.Migration):

    dependencies = [
        ('ordenes', '0053_fecha_ingreso_editable'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordentrabajo',
            name='kilometraje',
            field=models.PositiveIntegerField(
                null=True,
                verbose_name='Kilometraje',
                help_text='Lectura del odómetro del vehículo en esta orden de trabajo',
            ),
        ),
        migrations.RunPython(rellenar_kilometraje, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='ordentrabajo',
            name='kilometraje',
            field=models.PositiveIntegerField(
                verbose_name='Kilometraje',
                help_text='Lectura del odómetro del vehículo en esta orden de trabajo',
            ),
        ),
    ]

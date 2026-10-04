from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0015_remove_cotizacion_vehiculo_vigente_unica'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='cotizacion',
            name='cotizacion_recepcion_vigente_unica',
        ),
        migrations.RemoveConstraint(
            model_name='cotizacion',
            name='cotizacion_inspeccion_vigente_unica',
        ),
    ]
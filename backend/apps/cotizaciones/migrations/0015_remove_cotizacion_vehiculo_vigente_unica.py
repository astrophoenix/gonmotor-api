from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0014_cotizacion_vehiculo_vigente_unica'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='cotizacion',
            name='cotizacion_vehiculo_vigente_unica',
        ),
    ]
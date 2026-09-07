from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0006_detalle_base_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='detalleserviciocotizacion',
            name='es_opcional',
            field=models.BooleanField(default=False, help_text='Para sugerencias adicionales al cliente'),
        ),
    ]
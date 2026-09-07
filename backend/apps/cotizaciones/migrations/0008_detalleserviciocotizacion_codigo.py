from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0007_detalleserviciocotizacion_es_opcional'),
    ]

    operations = [
        migrations.AddField(
            model_name='detalleserviciocotizacion',
            name='codigo',
            field=models.CharField(blank=True, max_length=50, null=True, verbose_name='Código'),
        ),
    ]
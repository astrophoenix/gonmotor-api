import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0005_backfill_sucursal'),
    ]

    operations = [
        migrations.AddField(
            model_name='detallerepuestocotizacion',
            name='created_at',
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
                verbose_name='Fecha de creación',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='detallerepuestocotizacion',
            name='is_active',
            field=models.BooleanField(
                default=True,
                verbose_name='Activo',
                help_text='Indica si el registro está activo o fue deshabilitado/eliminado lógicamente',
            ),
        ),
        migrations.AddField(
            model_name='detallerepuestocotizacion',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, verbose_name='Fecha de última modificación'),
        ),
        migrations.AddField(
            model_name='detalleserviciocotizacion',
            name='created_at',
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
                verbose_name='Fecha de creación',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='detalleserviciocotizacion',
            name='is_active',
            field=models.BooleanField(
                default=True,
                verbose_name='Activo',
                help_text='Indica si el registro está activo o fue deshabilitado/eliminado lógicamente',
            ),
        ),
        migrations.AddField(
            model_name='detalleserviciocotizacion',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, verbose_name='Fecha de última modificación'),
        ),
    ]
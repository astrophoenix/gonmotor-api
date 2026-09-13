from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ordenes', '0030_alter_inspeccionvehiculo_estado_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='FotoOrdenTrabajo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('imagen', models.ImageField(upload_to='ordenes/orden_fotos/', verbose_name='Imagen')),
                ('descripcion', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'orden_trabajo',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='fotos',
                        to='ordenes.ordentrabajo',
                        verbose_name='Orden de Trabajo',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Foto de la Orden de Trabajo',
                'verbose_name_plural': 'Fotos de las Ordenes de Trabajo',
                'ordering': ['created_at', 'id'],
            },
        ),
    ]
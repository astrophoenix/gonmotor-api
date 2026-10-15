# Convierte el M2M automático Vehiculo.empresas en el through explícito
# VehiculoEmpresa (con estado propio) reutilizando la tabla existente, y
# reemplaza la unicidad global de la placa por una parcial (no eliminados).

import django.db.models.deletion
from django.db import migrations, models

TABLA = 'vehiculos_vehiculo_empresas'

# Añade las columnas de auditoría/borrado a la tabla del M2M y elimina la
# unique implícita (vehiculo_id, empresa_id), cuyo nombre lo generó Django con
# un hash: se busca en el catálogo para que funcione en cualquier entorno.
SQL_ADELANTE = f"""
ALTER TABLE {TABLA}
    ADD COLUMN is_active boolean NOT NULL DEFAULT true,
    ADD COLUMN created_at timestamp with time zone NOT NULL DEFAULT now(),
    ADD COLUMN updated_at timestamp with time zone NOT NULL DEFAULT now(),
    ADD COLUMN deleted_at timestamp with time zone NULL;
ALTER TABLE {TABLA}
    ALTER COLUMN is_active DROP DEFAULT,
    ALTER COLUMN created_at DROP DEFAULT,
    ALTER COLUMN updated_at DROP DEFAULT;
DO $$
DECLARE nombre text;
BEGIN
    FOR nombre IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = '{TABLA}'::regclass AND contype = 'u'
    LOOP
        EXECUTE format('ALTER TABLE {TABLA} DROP CONSTRAINT %I', nombre);
    END LOOP;
END $$;
"""

SQL_ATRAS = f"""
ALTER TABLE {TABLA}
    DROP COLUMN is_active,
    DROP COLUMN created_at,
    DROP COLUMN updated_at,
    DROP COLUMN deleted_at;
ALTER TABLE {TABLA}
    ADD CONSTRAINT {TABLA}_vehiculo_id_empresa_id_uniq UNIQUE (vehiculo_id, empresa_id);
"""


class Migration(migrations.Migration):

    dependencies = [
        ('empresas', '0008_remove_taller_una_sucursal_activa_por_empresa_codigo_and_more'),
        ('vehiculos', '0008_vehiculo_proxima_mantenimiento_fecha_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiculo',
            name='deleted_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Fecha de eliminación'),
        ),
        migrations.AlterField(
            model_name='vehiculo',
            name='placa',
            field=models.CharField(help_text='Ejemplo: PBA1234 (Sin guiones)', max_length=10, verbose_name='Placa'),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(SQL_ADELANTE, SQL_ATRAS),
            ],
            state_operations=[
                migrations.CreateModel(
                    name='VehiculoEmpresa',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('is_active', models.BooleanField(default=True, help_text='Indica si el registro está activo o fue deshabilitado/eliminado lógicamente', verbose_name='Activo')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')),
                        ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Fecha de última modificación')),
                        ('deleted_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Fecha de eliminación')),
                        ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vinculos_vehiculo', to='empresas.empresa')),
                        ('vehiculo', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vinculos_empresa', to='vehiculos.vehiculo')),
                    ],
                    options={
                        'verbose_name': 'Vínculo Vehículo-Empresa',
                        'verbose_name_plural': 'Vínculos Vehículo-Empresa',
                        'db_table': TABLA,
                    },
                ),
                migrations.AlterField(
                    model_name='vehiculo',
                    name='empresas',
                    field=models.ManyToManyField(blank=True, related_name='vehiculos', through='vehiculos.VehiculoEmpresa', to='empresas.empresa', verbose_name='Empresas'),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name='vehiculo',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('placa',), name='vehiculo_placa_viva_unica'),
        ),
        migrations.AddIndex(
            model_name='vehiculoempresa',
            index=models.Index(condition=models.Q(('deleted_at__isnull', True)), fields=['empresa', 'vehiculo'], name='vehemp_emp_vivos_idx'),
        ),
        migrations.AddConstraint(
            model_name='vehiculoempresa',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('vehiculo', 'empresa'), name='vehiculo_empresa_vivo_unico'),
        ),
    ]

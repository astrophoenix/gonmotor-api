from django.db import migrations


def _indexes():
    """(nombre, tabla, columna) de las columnas de `search_fields` que aún no
    tenían índice trigram (ver 0005). Con `BusquedaIndexadaFilter` cada campo
    buscado se consulta en su propia tabla, así que todas las ramas del OR
    pueden resolverse por índice si todas lo tienen.
    """
    return [
        ('idx_recepcion_numero_trgm', 'ordenes_recepcionvehiculo', 'numero_recepcion'),
        ('idx_inspeccion_numero_trgm', 'ordenes_inspeccionvehiculo', 'numero_inspeccion'),
        ('idx_vehiculos_marca_trgm', 'vehiculos_vehiculo', 'marca'),
        ('idx_vehiculos_modelo_trgm', 'vehiculos_vehiculo', 'modelo'),
        ('idx_vehiculos_vin_trgm', 'vehiculos_vehiculo', 'vin'),
        ('idx_vehiculos_motor_trgm', 'vehiculos_vehiculo', 'numero_motor'),
        ('idx_clientes_email_trgm', 'clientes_cliente', 'email'),
        ('idx_clientes_telefono_trgm', 'clientes_cliente', 'telefono'),
        ('idx_citas_motivo_trgm', 'citas_cita', 'motivo_descripcion'),
    ]


def create_trigram_indexes(apps, schema_editor):
    statements = ['CREATE EXTENSION IF NOT EXISTS pg_trgm;']
    for name, table, column in _indexes():
        statements.append(
            f'CREATE INDEX IF NOT EXISTS {name} '
            f'ON {table} USING gin (upper({column}) gin_trgm_ops);'
        )
    schema_editor.execute('\n'.join(statements))


def drop_trigram_indexes(apps, schema_editor):
    statements = [f'DROP INDEX IF EXISTS {name};' for name, _, _ in _indexes()]
    schema_editor.execute('\n'.join(statements))


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_add_trigram_search_indexes'),
        ('clientes', '0006_remove_cliente_unique_cliente_per_empresa_and_more'),
        ('vehiculos', '0010_backfill_vinculos_eliminados'),
        ('ordenes', '0050_indices_empresa_created_at'),
        ('citas', '0003_cita_deleted_at_cita_cita_tal_vivos_idx'),
    ]

    operations = [
        migrations.RunPython(create_trigram_indexes, reverse_code=drop_trigram_indexes),
    ]

from django.db import migrations

# Columnas de búsqueda con índice trigram (0005 + 0006). Se recrean sobre
# `upper(f_unaccent(col))` para que la búsqueda sin tildes (lookup
# `sintilde_icontains`, apps/core/lookups.py) siga usando índice.
INDICES = [
    ('idx_clientes_nombre_trgm', 'clientes_cliente', 'nombre'),
    ('idx_clientes_identificacion_trgm', 'clientes_cliente', 'identificacion'),
    ('idx_clientes_email_trgm', 'clientes_cliente', 'email'),
    ('idx_clientes_telefono_trgm', 'clientes_cliente', 'telefono'),
    ('idx_vehiculos_placa_trgm', 'vehiculos_vehiculo', 'placa'),
    ('idx_vehiculos_marca_trgm', 'vehiculos_vehiculo', 'marca'),
    ('idx_vehiculos_modelo_trgm', 'vehiculos_vehiculo', 'modelo'),
    ('idx_vehiculos_vin_trgm', 'vehiculos_vehiculo', 'vin'),
    ('idx_vehiculos_motor_trgm', 'vehiculos_vehiculo', 'numero_motor'),
    ('idx_repuestos_codigo_trgm', 'inventario_repuesto', 'codigo'),
    ('idx_repuestos_nombre_trgm', 'inventario_repuesto', 'nombre'),
    ('idx_servicios_codigo_trgm', 'inventario_servicio', 'codigo'),
    ('idx_servicios_nombre_trgm', 'inventario_servicio', 'nombre'),
    ('idx_ordentrabajo_numero_trgm', 'ordenes_ordentrabajo', 'numero_orden'),
    ('idx_cotizacion_numero_trgm', 'cotizaciones_cotizacion', 'numero_cotizacion'),
    ('idx_recepcion_numero_trgm', 'ordenes_recepcionvehiculo', 'numero_recepcion'),
    ('idx_inspeccion_numero_trgm', 'ordenes_inspeccionvehiculo', 'numero_inspeccion'),
    ('idx_citas_motivo_trgm', 'citas_cita', 'motivo_descripcion'),
]

FUNCION = """
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;
"""


def _recrear(expresion):
    def operacion(apps, schema_editor):
        sentencias = []
        for nombre, tabla, columna in INDICES:
            sentencias.append(f'DROP INDEX IF EXISTS {nombre};')
            sentencias.append(
                f'CREATE INDEX {nombre} ON {tabla} '
                f'USING gin ({expresion.format(col=columna)}) gin_trgm_ops);'
            )
        schema_editor.execute('\n'.join(sentencias))
    return operacion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_add_trigram_search_indexes_2'),
    ]

    operations = [
        migrations.RunSQL(FUNCION, reverse_sql='DROP FUNCTION IF EXISTS f_unaccent(text);'),
        migrations.RunPython(
            _recrear('upper(f_unaccent(({col})::text)'),
            reverse_code=_recrear('upper({col}'),
        ),
    ]

"""Acciones especiales del historial de cambios para las bases ya sembradas.

En una base nueva las crea `sembrar_permisos_si_vacio` (señal post_migrate).
Idempotente, con los valores copiados (no importados) como en
`0017_acciones_dashboard`. Solo los roles de administración las reciben.
"""

from django.db import migrations

ACCIONES = (
    (
        'historial_recepciones',
        'Ver historial de cambios',
        'Muestra en cada recepción quién cambió qué y cuándo (estado, firma, kilometraje).',
        'recepciones',
    ),
    (
        'historial_inspecciones',
        'Ver historial de cambios',
        'Muestra en cada inspección quién cambió qué y cuándo (estado y plan de trabajo).',
        'inspecciones',
    ),
    (
        'historial_cotizaciones',
        'Ver historial de cambios',
        'Muestra en cada cotización quién cambió qué y cuándo (estado, precios, descuentos y líneas).',
        'cotizaciones',
    ),
    (
        'historial_ordenes',
        'Ver historial de cambios',
        'Muestra en cada orden de trabajo quién cambió qué y cuándo (estado, precios, descuentos y líneas).',
        'ordenes',
    ),
)

ROLES_CON_ACCIONES = ('ADMIN_SISTEMA', 'ADMIN_EMPRESA', 'ADMIN_TALLER')


def crear_acciones(apps, schema_editor):
    Recurso = apps.get_model('authentication', 'Recurso')
    AccionEspecial = apps.get_model('authentication', 'AccionEspecial')
    PermisoEspecial = apps.get_model('authentication', 'PermisoEspecial')
    Rol = apps.get_model('authentication', 'Rol')

    recursos = {r.codigo: r for r in Recurso.objects.all()}
    if not recursos:
        return
    roles = list(Rol.objects.filter(codigo__in=ROLES_CON_ACCIONES))
    for codigo, nombre, descripcion, recurso in ACCIONES:
        if recurso not in recursos:
            continue
        accion, _ = AccionEspecial.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'descripcion': descripcion, 'recurso': recursos[recurso]},
        )
        for rol in roles:
            PermisoEspecial.objects.get_or_create(rol=rol, accion=accion)


def quitar_acciones(apps, schema_editor):
    AccionEspecial = apps.get_model('authentication', 'AccionEspecial')
    AccionEspecial.objects.filter(codigo__in=[codigo for codigo, *_ in ACCIONES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0018_permiso_auditoria'),
    ]

    operations = [
        migrations.RunPython(crear_acciones, quitar_acciones),
    ]

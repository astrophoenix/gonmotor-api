"""Backfill: asigna los talleres activos a las asignaciones que no tienen ninguno.

Antes de la línea base de alcance por taller no existía restricción: todos los
usuarios de la empresa veían todos los talleres. Con la política nueva
("lista vacía = sin acceso a ningún taller"), las filas creadas antes de este
cambio quedarían sin acceso. Este backfill les otorga todos los talleres
activos de su empresa, que es exactamente lo que tenían antes; a partir de aquí
la asignación es explícita.
"""

from django.db import migrations

# Roles que ven todos los talleres de la empresa sin necesitar asignación.
# Se repite aquí a propósito: una migración no debe depender de módulos que
# pueden cambiar en el futuro.
ROLES_SIN_ASIGNACION = ('ADMIN_SISTEMA', 'ADMIN_EMPRESA')


def backfill_talleres(UsuarioEmpresa, Taller):
    """Asigna los talleres activos de la empresa a las filas sin talleres.

    Devuelve la cantidad de asignaciones completadas.
    """
    pendientes = (
        UsuarioEmpresa.objects.filter(is_active=True, talleres__isnull=True)
        .exclude(rol__in=ROLES_SIN_ASIGNACION)
        .distinct()
    )

    completadas = 0
    for asignacion in pendientes:
        talleres = list(
            Taller.objects.filter(
                empresa_id=asignacion.empresa_id, is_active=True
            ).values_list('pk', flat=True)
        )
        if not talleres:
            continue
        asignacion.talleres.set(talleres)
        completadas += 1
    return completadas


def aplicar_backfill(apps, schema_editor):
    UsuarioEmpresa = apps.get_model('authentication', 'UsuarioEmpresa')
    Taller = apps.get_model('empresas', 'Taller')
    backfill_talleres(UsuarioEmpresa, Taller)


def revertir_backfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0005_userprofile_avatar'),
        ('empresas', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(aplicar_backfill, revertir_backfill),
    ]

"""Persistencia de la seguridad: roles, recursos, permisos y acciones especiales.

Roles y recursos viven en sus propias tablas (`Rol`, `Recurso`). Los permisos
generales (ver/modificar) de un rol sobre un recurso viven en `Permiso`, y las
acciones especiales concedidas en `PermisoEspecial`. El catálogo de
`matriz_roles` (base declarativa) sigue siendo la semilla del sistema.

`UsuarioEmpresa.rol` guarda el `codigo` del rol (los de sistema conservan sus
códigos históricos), por lo que el contexto y el resto del código no cambian.
"""

import random
import string

from django.core.exceptions import ValidationError

from .matriz_roles import (
    ACCIONES,
    MATRIZ_ROLES,
    NIVELES_ROLES,
    RECURSOS,
    ROLES_TODOS_TALLERES,
)
from .models import (
    AccionEspecial,
    Permiso,
    PermisoEmpresa,
    PermisoEspecial,
    PermisoEspecialEmpresa,
    Recurso,
    Rol,
    UsuarioEmpresa,
)

NOMBRES_RECURSOS = {
    'empresa': 'Empresa',
    'talleres': 'Talleres',
    'usuarios': 'Usuarios',
    'empleados': 'Empleados',
    'clientes': 'Clientes',
    'vehiculos': 'Vehículos',
    'proveedores': 'Proveedores',
    'citas': 'Citas',
    'recepciones': 'Recepciones',
    'inspecciones': 'Inspecciones',
    'cotizaciones': 'Cotizaciones',
    'ordenes': 'Órdenes de trabajo',
    'inventario': 'Inventario',
    'facturacion': 'Facturación',
    'reportes': 'Reportes',
}

ACCIONES_CATALOGO = [
    ('anular_cliente', 'Anular cliente', 'Da de baja un cliente sin borrar su historial.', 'clientes'),
    ('exportar_informacion_sensible', 'Exportar información sensible', 'Permite exportar datos personales completos.', 'clientes'),
    ('aprobar_descuentos', 'Aprobar descuentos', 'Autoriza cotizaciones y órdenes con descuento.', 'cotizaciones'),
    ('reabrir_orden', 'Reabrir orden de trabajo', 'Permite volver a abrir una orden cerrada.', 'ordenes'),
    ('cerrar_orden', 'Cerrar orden de trabajo', 'Permite marcar una orden de trabajo como cerrada.', 'ordenes'),
]


def _nombre_recursos_por_codigo():
    return {r.codigo: r.nombre for r in Recurso.objects.all()}


def sembrar_permisos_si_vacio(forzar=False):
    """Siembra la base de seguridad si falta: roles de sistema, recursos,
    permisos base (desde `MATRIZ_ROLES`) y catálogo de acciones especiales.

    Si ya existen roles no re-puebla nada: los cambios del administrador son
    intencionales y no deben revertirse en despliegues.
    """
    if forzar:
        PermisoEspecial.objects.all().delete()
        Permiso.objects.all().delete()
        AccionEspecial.objects.all().delete()
        Recurso.objects.all().delete()
        Rol.objects.all().delete()
    elif Rol.objects.exists():
        return

    roles = [
        Rol(
            codigo=codigo,
            nombre=etiqueta,
            es_sistema=True,
            nivel=NIVELES_ROLES.get(codigo, 1),
            ver_todos_talleres=codigo in ROLES_TODOS_TALLERES,
        )
        for codigo, etiqueta in UsuarioEmpresa.ROLES
    ]
    Rol.objects.bulk_create(roles)
    roles_por_codigo = {r.codigo: r for r in roles}

    recursos = [
        Recurso(codigo=codigo, nombre=NOMBRES_RECURSOS.get(codigo, codigo), tipo='MODULO', orden=orden)
        for orden, codigo in enumerate(RECURSOS)
    ]
    Recurso.objects.bulk_create(recursos)
    recursos_por_codigo = {r.codigo: r for r in recursos}

    permisos = []
    for codigo_rol, recurso_codigo, acciones in _baseline_permisos():
        permisos.append(
            Permiso(
                rol=roles_por_codigo[codigo_rol],
                recurso=recursos_por_codigo[recurso_codigo],
                ver='ver' in acciones,
                modificar='modificar' in acciones,
            )
        )
    Permiso.objects.bulk_create(permisos)

    acciones = [
        AccionEspecial(
            codigo=codigo,
            nombre=nombre,
            descripcion=descripcion,
            recurso=recursos_por_codigo[recurso_codigo],
        )
        for codigo, nombre, descripcion, recurso_codigo in ACCIONES_CATALOGO
    ]
    AccionEspecial.objects.bulk_create(acciones)


def _baseline_permisos():
    """Permisos de la matriz declarativa como (rol, recurso, acciones)."""
    for codigo_rol, recursos in MATRIZ_ROLES.items():
        for recurso, acciones in recursos.items():
            yield codigo_rol, recurso, set(acciones)


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def _rol_por_codigo(codigo):
    if not codigo:
        return None
    return Rol.objects.filter(codigo=codigo).first()


def nombre_rol(codigo):
    rol = _rol_por_codigo(codigo)
    return rol.nombre if rol else codigo


def nivel_rol(codigo):
    if codigo in NIVELES_ROLES:
        return NIVELES_ROLES[codigo]
    rol = _rol_por_codigo(codigo)
    return rol.nivel if rol else 999


def ver_todos_talleres_rol(codigo):
    """Alcance de talleres del rol (los de sistema + los personalizados)."""
    if codigo in ROLES_TODOS_TALLERES:
        return True
    rol = _rol_por_codigo(codigo)
    return bool(rol and rol.ver_todos_talleres)


def puede_otorgar_bd(emisor_codigo, otorgado_codigo):
    """Jefatura: solo se otorgan roles de nivel igual o inferior al propio."""
    return nivel_rol(otorgado_codigo) <= nivel_rol(emisor_codigo)


def roles_visible_para(empresa_id, incluir_sistema=True):
    """Roles que un usuario de `empresa_id` puede ver: sistema + propios."""
    qs = Rol.objects.all()
    if empresa_id:
        qs = qs.filter(empresa_id=empresa_id)
        if incluir_sistema:
            qs = Rol.objects.filter(empresa_id=empresa_id) | Rol.objects.filter(es_sistema=True)
    else:
        qs = qs.filter(es_sistema=True)
    return qs.order_by('-es_sistema', 'nivel', 'nombre')


def roles_otorgables_para(contexto):
    """Códigos de roles activos que el usuario del contexto puede asignar."""
    roles = roles_visible_para(contexto.empresa_id).filter(is_active=True)
    codigos = []
    for rol in roles:
        if contexto.es_superusuario or puede_otorgar_bd(contexto.rol, rol.codigo):
            codigos.append(rol.codigo)
    return codigos


def _acciones_desde_fila(ver, modificar):
    acciones = []
    if ver:
        acciones.append('ver')
    if modificar:
        acciones.append('modificar')
    return acciones


def _permisos_empresa_overlay(rol, empresa_id):
    """Overrides de la empresa como `{recurso_codigo: acciones}`.

    Ausencia de fila = hereda el global; fila presente = reemplaza (la fila
    con ver/modificar en falso deniega explícitamente ese recurso).
    """
    filas = PermisoEmpresa.objects.filter(rol=rol, empresa_id=empresa_id).values(
        'recurso__codigo', 'ver', 'modificar'
    )
    return {
        fila['recurso__codigo']: _acciones_desde_fila(fila['ver'], fila['modificar'])
        for fila in filas
    }


def permisos_de_rol(codigo, empresa_id=None):
    """Permisos generales vigentes de un rol: `{recurso: [acciones]}`.

    Si `empresa_id` viene junto a un rol del sistema, los overrides de esa
    empresa reemplazan el valor global recurso por recurso.
    """
    rol = _rol_por_codigo(codigo)
    if not rol:
        return {}
    resultado = {}
    filas = (
        Permiso.objects.filter(rol=rol)
        .select_related('recurso')
        .values('recurso__codigo', 'ver', 'modificar')
    )
    for fila in filas:
        resultado[fila['recurso__codigo']] = _acciones_desde_fila(
            fila['ver'], fila['modificar']
        )
    if empresa_id and rol.es_sistema:
        resultado.update(_permisos_empresa_overlay(rol, empresa_id))
    return resultado


def acciones_especiales_de_rol(codigo, empresa_id=None):
    """Acciones especiales concedidas a un rol (con overrides por empresa)."""
    rol = _rol_por_codigo(codigo)
    if not rol:
        return []
    concedidas = set(
        PermisoEspecial.objects.filter(rol=rol).values_list('accion__codigo', flat=True)
    )
    if empresa_id and rol.es_sistema:
        overrides = PermisoEspecialEmpresa.objects.filter(
            rol=rol, empresa_id=empresa_id
        ).values_list('accion__codigo', 'permitido')
        for accion_codigo, permitido in overrides:
            if permitido:
                concedidas.add(accion_codigo)
            else:
                concedidas.discard(accion_codigo)
    return sorted(concedidas)


def permisos_en_request(request, codigo, empresa_id=None):
    """Permisos del rol vigente cacheados en el request (evita la query)."""
    cacheado = getattr(request, '_permisos_rol_gonmotor', None)
    if isinstance(cacheado, dict) and cacheado.get('rol') == codigo and cacheado.get('empresa') == empresa_id:
        return cacheado['permisos']
    permisos = permisos_de_rol(codigo, empresa_id)
    try:
        request._permisos_rol_gonmotor = {
            'rol': codigo, 'empresa': empresa_id, 'permisos': permisos
        }
    except AttributeError:  # objetos sin soporte de atributos (defensivo)
        return permisos
    return permisos


def catalogo_recursos():
    return list(
        Recurso.objects.order_by('orden', 'codigo').values(
            'id', 'codigo', 'nombre', 'tipo', 'padre__codigo', 'orden'
        )
    )


def catalogo_acciones():
    return list(
        AccionEspecial.objects.order_by('recurso__orden', 'nombre').values(
            'codigo', 'nombre', 'descripcion', 'recurso__codigo'
        )
    )


# ---------------------------------------------------------------------------
# Escritura (roles personalizados)
# ---------------------------------------------------------------------------

def _normalizar_permiso(ver, modificar):
    """Invariante ver/modificar: modificar implica ver."""
    if modificar:
        ver = True
    if not ver:
        modificar = False
    return ver, modificar


def guardar_permisos(rol, items):
    """Reemplaza los permisos generales de `rol` (cada item: recurso+ver+modificar)."""
    recursos = {r.codigo: r for r in Recurso.objects.all()}
    nuevo = {}
    for item in items or []:
        recurso_codigo = item.get('recurso')
        if recurso_codigo not in recursos:
            raise ValidationError(f'Recurso desconocido: {recurso_codigo!r}.')
        ver, modificar = _normalizar_permiso(
            bool(item.get('ver')), bool(item.get('modificar'))
        )
        nuevo[recurso_codigo] = (recursos[recurso_codigo], ver, modificar)

    Permiso.objects.filter(rol=rol).delete()
    filas = [
        Permiso(rol=rol, recurso=recurso, ver=ver, modificar=modificar)
        for recurso, ver, modificar in nuevo.values()
        if ver or modificar
    ]
    Permiso.objects.bulk_create(filas)


def guardar_acciones(rol, codigos):
    """Reemplaza las acciones especiales concedidas a `rol` por sus códigos."""
    validas = {
        a.codigo: a
        for a in AccionEspecial.objects.filter(codigo__in=(codigos or []))
    }
    desconocidas = set(codigos or []) - set(validas)
    if desconocidas:
        raise ValidationError(
            f'Acciones desconocidas: {", ".join(sorted(desconocidas))!r}.'
        )
    PermisoEspecial.objects.filter(rol=rol).delete()
    PermisoEspecial.objects.bulk_create(
        PermisoEspecial(rol=rol, accion=accion) for accion in validas.values()
    )


def generar_codigo_rol(nombre):
    """Código único para un rol personalizado a partir de su nombre."""
    base = ''.join(
        c if c.isalnum() else '-' for c in (' '.join(nombre.lower().split()))
    ).lower()
    base = base[:30] or 'rol'
    while Rol.objects.filter(codigo=base).exists():
        sufijo = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        base = f'{base[:26]}-{sufijo}'
    return base


# ---------------------------------------------------------------------------
# Overrides por empresa (roles de sistema ajustables por cada cuenta)
# ---------------------------------------------------------------------------

def tiene_override_empresa(rol, empresa_id):
    """¿La empresa ajustó los permisos de este rol (algún override)?"""
    if not empresa_id or not rol.es_sistema:
        return False
    return (
        PermisoEmpresa.objects.filter(rol=rol, empresa_id=empresa_id).exists()
        or PermisoEspecialEmpresa.objects.filter(rol=rol, empresa_id=empresa_id).exists()
    )


def guardar_permisos_empresa(rol, empresa_id, items):
    """Reemplaza los overrides de `rol` en `empresa_id`.

    A diferencia de `guardar_permisos` (que omite las filas sin permiso),
    aquí se guarda fila por recurso recibido con sus valores explícitos:
    una fila con ver/modificar en falso significa "denegar en esta empresa".
    """
    recursos = {r.codigo: r for r in Recurso.objects.all()}
    nuevo = {}
    for item in items or []:
        recurso_codigo = item.get('recurso')
        if recurso_codigo not in recursos:
            raise ValidationError(f'Recurso desconocido: {recurso_codigo!r}.')
        ver, modificar = _normalizar_permiso(
            bool(item.get('ver')), bool(item.get('modificar'))
        )
        nuevo[recurso_codigo] = (recursos[recurso_codigo], ver, modificar)

    PermisoEmpresa.objects.filter(rol=rol, empresa_id=empresa_id).delete()
    filas = [
        PermisoEmpresa(
            rol=rol, empresa_id=empresa_id, recurso=recurso, ver=ver, modificar=modificar
        )
        for recurso, ver, modificar in nuevo.values()
    ]
    PermisoEmpresa.objects.bulk_create(filas)


def guardar_acciones_empresa(rol, empresa_id, codigos):
    """Reemplaza los overrides de acciones especiales de `rol` en `empresa_id`.

    Concede lo recibido y añade filas de denegación explícita para lo que el
    global otorga y la empresa ya no quiere (permitido=False).
    """
    todas = {a.codigo: a for a in AccionEspecial.objects.all()}
    desconocidas = set(codigos or []) - set(todas)
    if desconocidas:
        raise ValidationError(
            f'Acciones desconocidas: {", ".join(sorted(desconocidas))!r}.'
        )
    concedidas_globales = set(
        PermisoEspecial.objects.filter(rol=rol).values_list('accion__codigo', flat=True)
    )
    deseadas = set(codigos or [])
    PermisoEspecialEmpresa.objects.filter(rol=rol, empresa_id=empresa_id).delete()
    filas = [
        PermisoEspecialEmpresa(
            rol=rol, empresa_id=empresa_id, accion=todas[codigo], permitido=True
        )
        for codigo in deseadas
    ]
    filas += [
        PermisoEspecialEmpresa(
            rol=rol, empresa_id=empresa_id, accion=todas[codigo], permitido=False
        )
        for codigo in (concedidas_globales - deseadas)
        if codigo in todas
    ]
    PermisoEspecialEmpresa.objects.bulk_create(filas)


def restablecer_permisos_empresa(rol, empresa_id):
    """Borra los overrides de la empresa: vuelve a la matriz global."""
    PermisoEmpresa.objects.filter(rol=rol, empresa_id=empresa_id).delete()
    PermisoEspecialEmpresa.objects.filter(rol=rol, empresa_id=empresa_id).delete()
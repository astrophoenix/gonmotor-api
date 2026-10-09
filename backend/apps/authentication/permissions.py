"""Permisos DRF sobre el catálogo persistido de roles (`Rol`/`Permiso`).

Escritura: antes la matriz vivía solo en `matriz_roles` (código). Ahora la
fuente de verdad en tiempo de ejecución son las tablas `Permiso` (y
`PermisoEspecial` para acciones especiales), sembradas desde
`matriz_roles.MATRIZ_ROLES` tras el migrate y editables en la pantalla de
roles. `matriz_roles.puede` conserva la semilla y las validaciones de tests.

Uso en un viewset:

    permission_classes = [IsAuthenticated, TienePermiso('facturacion', 'modificar')]
"""

from rest_framework import permissions

from .permisos_repo import permisos_en_request
from .utils import get_contexto_desde_request


def TienePermiso(recurso, accion='modificar'):
    """Devuelve una clase de permiso para `accion` sobre `recurso`.

    DRF instancia las clases con `()`, por eso la clase se construye aquí y no
    se pasa el recurso al `__init__`.
    """
    mensaje = f'No tienes permiso para {accion} {recurso} en esta empresa.'

    class _TienePermiso(permissions.BasePermission):
        message = mensaje

        def has_permission(self, request, view):
            contexto = get_contexto_desde_request(request)
            if not contexto.hay_contexto:
                return False
            permisos = permisos_en_request(request, contexto.rol, contexto.empresa_id)
            return accion in permisos.get(recurso, ())

    _TienePermiso.__name__ = f'TienePermiso_{recurso}'
    _TienePermiso.__qualname__ = _TienePermiso.__name__
    return _TienePermiso


class EsAdminDeEmpresa(permissions.BasePermission):
    """Escritura solo para roles de administración de la empresa."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        contexto = get_contexto_desde_request(request)
        if not contexto.hay_contexto:
            return False
        permisos = permisos_en_request(request, contexto.rol, contexto.empresa_id)
        return 'modificar' in permisos.get('empresa', ())


def TieneRecurso(recurso):
    """Permiso sobre un recurso: lectura = 'ver', escritura = 'modificar'.

    Útil para el caso común donde un verbo GET es listar/detallar y el resto
    del viewset ya está filtrado por empresa. Para vistas donde el GET muta
    estado conviene `TienePermiso(recurso, 'ver')` explícito.
    """
    clase_ver = TienePermiso(recurso, 'ver')
    clase_modificar = TienePermiso(recurso, 'modificar')

    class _TieneRecurso(permissions.BasePermission):
        message = f'No tienes permiso para operar en {recurso} en esta empresa.'

        def has_permission(self, request, view):
            if request.method in permissions.SAFE_METHODS:
                return clase_ver().has_permission(request, view)
            return clase_modificar().has_permission(request, view)

    _TieneRecurso.__name__ = f'TieneRecurso_{recurso}'
    _TieneRecurso.__qualname__ = _TieneRecurso.__name__
    return _TieneRecurso

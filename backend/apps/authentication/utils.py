"""Contexto de acceso: empresa activa, rol vigente y taller de la sesión.

Reglas de la línea base de usuarios (F1):

1. El rol sale SIEMPRE de la base (`UsuarioEmpresa.rol`), nunca de la claim
   `rol` del JWT. El token se firma al iniciar sesión y vive 60 minutos: si un
   cajero pasa a solo-lectura, no debe seguir facturando hasta que se vuelva a
   loguear. La claim `rol` queda solo informativa.
2. El alcance de talleres es una matriz explícita por rol (`permisos_repo`):
   `ADMIN_SISTEMA` y `ADMIN_EMPRESA` ven todos los talleres activos de la
   empresa; el resto solo los asignados en `UsuarioEmpresa.talleres` (lista
   vacía = sin acceso a ningún taller).
3. El taller de la sesión (cabecera `X-Taller-ID` o `UserProfile.taller_activo`)
   se valida contra la empresa activa Y contra ese alcance. Jamás se confía en
   lo que envía el cliente sin comprobarlo.

Toda la API consume estas funciones; no se reimplementa filtrado por tenant en
otro archivo.
"""

from typing import NamedTuple, Optional

from apps.authentication.permisos_repo import ver_todos_talleres_rol
from apps.authentication.models import UsuarioEmpresa
from apps.empresas.models import Empresa, Taller

HEADER_EMPRESA = 'X-Empresa-ID'
HEADER_TALLER = 'X-Taller-ID'

class Contexto(NamedTuple):
    """Empresa y rol vigentes del usuario para la petición actual."""

    empresa_id: Optional[int]
    rol: Optional[str]
    es_superusuario: bool

    @property
    def hay_contexto(self):
        return self.empresa_id is not None


def _normalizar_id(valor):
    """Convierte un id venido de cabecera/claim a entero (o None)."""
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


# Alias histórico: el nombre original se mantenía solo dentro de este archivo.
_normalizar_empresa_id = _normalizar_id


def _empresa_candidata(request):
    """empresa_id declarado por la petición: claim del JWT y, si no, cabecera."""
    empresa_id = None
    if hasattr(request, 'auth') and isinstance(request.auth, dict):
        empresa_id = _normalizar_id(request.auth.get('empresa_id'))
    if not empresa_id:
        empresa_id = _normalizar_id(request.headers.get(HEADER_EMPRESA))
    return empresa_id


def _resolver_contexto(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return Contexto(None, None, False)

    candidata = _empresa_candidata(request)

    # (empresa_id, rol) de las relaciones activas del usuario: una sola query
    # que sirve tanto para validar la empresa candidata como para el fallback.
    relaciones = dict(
        UsuarioEmpresa.objects.filter(
            user=user, is_active=True, tiene_acceso=True, empresa__is_active=True
        ).values_list('empresa_id', 'rol')
    )

    if candidata:
        if user.is_superuser:
            if Empresa.objects.filter(pk=candidata, is_active=True).exists():
                return Contexto(candidata, 'ADMIN_SISTEMA', True)
        elif candidata in relaciones:
            return Contexto(candidata, relaciones[candidata], False)
        else:
            # El usuario pidió una empresa a la que no pertenece: nada de
            # fallback silencioso, se niega el contexto.
            return Contexto(None, None, False)

    # Sin empresa explícita: la única empresa asignada entra directo.
    if len(relaciones) == 1:
        empresa_id, rol = next(iter(relaciones.items()))
        return Contexto(empresa_id, rol, user.is_superuser)

    if user.is_superuser:
        if relaciones:
            return Contexto(next(iter(relaciones)), 'ADMIN_SISTEMA', True)
        primera = Empresa.objects.filter(is_active=True).order_by('pk').first()
        if primera:
            return Contexto(primera.pk, 'ADMIN_SISTEMA', True)

    # Varias empresas sin elegir (o ninguna): sin contexto de tenant.
    return Contexto(None, None, user.is_superuser)


def get_contexto_desde_request(request):
    """Contexto (empresa_id, rol) resuelto desde la base, cacheado por petición.

    El cache evita repetir la query cuando el mismo request llama varias veces
    (p. ej. `get_empresa_id_desde_request` dentro de un viewset).
    """
    cacheado = getattr(request, '_contexto_gonmotor', None)
    if cacheado is not None:
        return cacheado
    contexto = _resolver_contexto(request)
    try:
        request._contexto_gonmotor = contexto
    except AttributeError:  # objetos sin soporte de atributos (defensivo)
        return contexto
    return contexto


def get_empresa_id_desde_request(request):
    """empresa_id activo para el usuario en sesión, o None si no tiene acceso."""
    return get_contexto_desde_request(request).empresa_id


def get_rol_desde_request(request):
    """Rol vigente del usuario en la empresa activa, leído de la base."""
    return get_contexto_desde_request(request).rol


def contexto_de_usuario(user, empresa_id):
    """Contexto para un usuario y empresa concretos (sin depender del request)."""
    empresa_id = _normalizar_id(empresa_id)
    if not user or not user.is_authenticated or not empresa_id:
        return Contexto(None, None, False)
    relacion = UsuarioEmpresa.objects.filter(
        user=user, empresa_id=empresa_id, is_active=True, tiene_acceso=True, empresa__is_active=True
    ).values_list('rol', flat=True).first()
    if relacion:
        return Contexto(empresa_id, relacion, user.is_superuser)
    if user.is_superuser and Empresa.objects.filter(pk=empresa_id, is_active=True).exists():
        return Contexto(empresa_id, 'ADMIN_SISTEMA', True)
    return Contexto(None, None, user.is_superuser)


def talleres_permitidos(user, contexto, empresa_id=None):
    """Talleres activos en los que `user` puede operar para el contexto dado.

    - Roles de administración (y superusuarios): todos los activos de la empresa.
    - Resto de roles: los asignados en `UsuarioEmpresa.talleres`; vacío = ninguno.
    """
    empresa_id = _normalizar_id(empresa_id or contexto.empresa_id)
    if not empresa_id:
        return Taller.objects.none()

    base = Taller.objects.filter(empresa_id=empresa_id, is_active=True).order_by('pk')
    if contexto.es_superusuario or ver_todos_talleres_rol(contexto.rol):
        return base
    if not contexto.rol or user is None or not user.is_authenticated:
        return Taller.objects.none()
    return base.filter(
        usuarios_asignados__user=user,
        usuarios_asignados__empresa_id=empresa_id,
        usuarios_asignados__is_active=True,
    )


def validar_talleres_asignables(user, contexto, talleres_ids):
    """Separa ids de talleres que la empresa activa puede asignar.

    Devuelve `(permitidos, rechazados)`: los rechazados no pertenecen a la
    empresa de la sesión o están inactivos. Se usa antes de `M2M.set()` para que
    nadie asigne un taller de otra empresa a un empleado.
    """
    talleres_ids = [_normalizar_id(t) for t in (talleres_ids or [])]
    talleres_ids = [t for t in talleres_ids if t is not None]
    if not talleres_ids:
        return [], []
    empresa_id = _normalizar_id(contexto.empresa_id)
    if not empresa_id:
        return [], talleres_ids
    validos = set(
        Taller.objects.filter(
            pk__in=talleres_ids, empresa_id=empresa_id, is_active=True
        ).values_list('pk', flat=True)
    )
    permitidos = [t for t in talleres_ids if t in validos]
    rechazados = [t for t in talleres_ids if t not in validos]
    return permitidos, rechazados


def taller_de_usuario(user, empresa_id, candidato=None):
    """Taller efectivo de `user` en `empresa_id`, validado contra su alcance."""
    contexto = contexto_de_usuario(user, empresa_id)
    if not contexto.hay_contexto:
        return None
    return _taller_efectivo(user, contexto, candidato)


def _taller_efectivo(user, contexto, candidato=None):
    """Taller válido de la sesión, o None si no queda ninguno permitido.

    Precedencia: candidato (cabecera) → `taller_activo` del perfil → primer
    taller permitido. Cualquier candidato fuera de la empresa o del alcance se
    descarta sin error: la respuesta sigue siendo un taller permitido.
    """
    permitidos = list(talleres_permitidos(user, contexto).values_list('pk', flat=True))
    if not permitidos:
        return None
    if candidato and candidato in permitidos:
        return candidato
    profile = getattr(user, 'profile', None)
    activo = getattr(profile, 'taller_activo_id', None)
    if activo and activo in permitidos:
        return activo
    return permitidos[0]


def get_taller_desde_request(request, empresa_id=None):
    """Taller de la sesión validado (cabecera `X-Taller-ID` > perfil > primero)."""
    contexto = get_contexto_desde_request(request)
    if empresa_id is not None:
        contexto = contexto._replace(empresa_id=_normalizar_id(empresa_id))
    if not contexto.hay_contexto:
        return None
    candidato = _normalizar_id(request.headers.get(HEADER_TALLER))
    return _taller_efectivo(getattr(request, 'user', None), contexto, candidato)

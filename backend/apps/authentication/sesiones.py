"""Revocación de sesiones (refresh tokens) del usuario.

Al restablecer o cambiar la contraseña se cierran todas las sesiones abiertas:
si alguien había robado una, deja de servirle. Los access tokens ya emitidos
siguen valiendo hasta caducar (`ACCESS_TOKEN_LIFETIME`, unos minutos).
"""

from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

# Claims propias que se copian a la sesión nueva (empresa activa y rol informativo).
CLAIMS_SESION = ('empresa_id', 'rol')


def revocar_sesiones(user):
    """Pone en la lista negra todos los refresh tokens vigentes del usuario."""
    pendientes = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in pendientes],
        ignore_conflicts=True,
    )


def nueva_sesion(user, token_actual=None):
    """Par access/refresh nuevo, con la misma empresa que la sesión actual."""
    refresh = RefreshToken.for_user(user)
    for claim in CLAIMS_SESION:
        valor = token_actual.get(claim) if token_actual is not None else None
        if valor is not None:
            refresh[claim] = valor
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}

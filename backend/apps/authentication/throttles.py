"""Límites de intentos de los endpoints públicos de autenticación.

Se aplican por IP y por cuenta/correo (hash, no se guarda el dato en claro en
la caché): la IP frena a un atacante que prueba muchas cuentas y la cuenta
frena a uno que reparte intentos entre muchas IP. Las tasas salen de
`settings.LIMITES_AUTENTICACION` en cada petición, así se ajustan por `.env`.

Nota: la caché por defecto es local a cada proceso; con varios workers de
gunicorn el límite efectivo se multiplica por su número. Con una caché
compartida (Redis) el límite pasa a ser exacto.
"""

import hashlib

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class _LimiteConfigurable(SimpleRateThrottle):
    def get_rate(self):
        return settings.LIMITES_AUTENTICACION[self.scope]


class _PorIP(_LimiteConfigurable):
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class _PorIdentificador(_LimiteConfigurable):
    """Limita por el valor de `campo` del cuerpo (usuario o correo)."""

    campo = None

    def get_cache_key(self, request, view):
        valor = str(request.data.get(self.campo) or '').strip().lower()
        if not valor:
            return None
        huella = hashlib.sha256(valor.encode('utf-8')).hexdigest()
        return self.cache_format % {'scope': self.scope, 'ident': huella}


class LoginPorIPThrottle(_PorIP):
    scope = 'login_ip'


class LoginPorCuentaThrottle(_PorIdentificador):
    scope = 'login_cuenta'
    campo = 'username'


class RecuperacionPorIPThrottle(_PorIP):
    scope = 'recuperacion_ip'


class RecuperacionPorCorreoThrottle(_PorIdentificador):
    scope = 'recuperacion_correo'
    campo = 'email'


class MensajeLimiteMixin:
    """Mensaje en español cuando se supera el límite (DRF añade el tiempo de espera)."""

    def throttled(self, request, wait):
        from rest_framework.exceptions import Throttled

        raise Throttled(wait, detail='Demasiados intentos. Espera un momento antes de volver a intentarlo.')

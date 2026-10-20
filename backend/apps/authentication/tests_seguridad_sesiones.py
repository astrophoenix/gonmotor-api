"""Seguridad de sesiones y de los endpoints públicos de autenticación.

- Restablecer o cambiar la contraseña cierra todas las sesiones abiertas.
- Login y recuperación tienen límites de intentos por IP y por cuenta/correo.
- Las contraseñas nuevas exigen 12 caracteres como mínimo.
- El enlace público del .ics caduca unos días después de la cita.
"""

from datetime import time, timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.authentication.models import UsuarioEmpresa
from apps.citas.ics import token_enlace_ics
from apps.citas.models import Cita
from apps.clientes.models import Cliente
from apps.empresas.models import Empresa, Taller
from apps.vehiculos.models import Vehiculo, vincular_empresa

User = get_user_model()

CLAVE = 'Clave-Segura-2026'
CLAVE_NUEVA = 'Otra-Clave-Segura-2026'


def _empresa():
    return Empresa.objects.create(nombre_comercial='Taller Sesiones', ruc=str(uuid4().int % 10**13).zfill(13))


class _ConUsuario(TestCase):
    def setUp(self):
        cache.clear()
        self.empresa = _empresa()
        self.taller = Taller.objects.create(empresa=self.empresa, nombre='Central', codigo_sucursal='001', direccion='x')
        self.user = User.objects.create_user(username='sesiones', email='sesiones@taller.ec', password=CLAVE)
        relacion = UsuarioEmpresa.objects.create(user=self.user, empresa=self.empresa, rol='ASESOR')
        relacion.talleres.set([self.taller])

    def _login(self, ip='10.0.0.1', password=CLAVE):
        return APIClient().post(
            '/api/auth/login/', {'username': 'sesiones', 'password': password},
            format='json', REMOTE_ADDR=ip,
        )

    def _refresca(self, refresh):
        return APIClient().post('/api/auth/refresh/', {'refresh': refresh}, format='json')


class RevocarSesionesTests(_ConUsuario):

    def test_restablecer_contrasena_cierra_todas_las_sesiones(self):
        sesion = self._login().json()
        # El refresh rota: se sigue con el token nuevo, que es el vigente.
        rotado = self._refresca(sesion['refresh'])
        self.assertEqual(rotado.status_code, 200)
        vigente = rotado.json()['refresh']

        respuesta = APIClient().post('/api/auth/password-reset-confirm/', {
            'uid': urlsafe_base64_encode(force_bytes(self.user.pk)),
            'token': PasswordResetTokenGenerator().make_token(self.user),
            'new_password': CLAVE_NUEVA,
        }, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(self._refresca(vigente).status_code, 401)

    def test_cambiar_contrasena_cierra_las_demas_y_entrega_una_nueva(self):
        otro_dispositivo = self._login(ip='10.0.0.2').json()
        actual = self._login().json()
        cliente = APIClient()
        cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {actual['access']}")

        respuesta = cliente.post('/api/auth/change-password/', {
            'old_password': CLAVE, 'new_password': CLAVE_NUEVA,
        }, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        datos = respuesta.json()

        self.assertEqual(self._refresca(otro_dispositivo['refresh']).status_code, 401)
        self.assertEqual(self._refresca(actual['refresh']).status_code, 401)
        self.assertEqual(self._refresca(datos['refresh']).status_code, 200)
        # La sesión nueva conserva la empresa activa.
        self.assertEqual(
            AccessToken(datos['access'])['empresa_id'],
            AccessToken(actual['access'])['empresa_id'],
        )

    def test_el_enlace_de_recuperacion_es_de_un_solo_uso(self):
        datos = {
            'uid': urlsafe_base64_encode(force_bytes(self.user.pk)),
            'token': PasswordResetTokenGenerator().make_token(self.user),
            'new_password': CLAVE_NUEVA,
        }
        self.assertEqual(APIClient().post('/api/auth/password-reset-confirm/', datos, format='json').status_code, 200)
        datos['new_password'] = 'Tercera-Clave-2026'
        self.assertEqual(APIClient().post('/api/auth/password-reset-confirm/', datos, format='json').status_code, 400)


class LongitudMinimaTests(_ConUsuario):

    def test_contrasena_de_menos_de_12_caracteres_se_rechaza(self):
        respuesta = APIClient().post('/api/auth/password-reset-confirm/', {
            'uid': urlsafe_base64_encode(force_bytes(self.user.pk)),
            'token': PasswordResetTokenGenerator().make_token(self.user),
            'new_password': 'Corta-2026',
        }, format='json')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertTrue(self.user.check_password(CLAVE))


@override_settings(LIMITES_AUTENTICACION={
    'login_ip': '3/min',
    'login_cuenta': '5/hour',
    'recuperacion_ip': '5/hour',
    'recuperacion_correo': '2/hour',
})
class LimitesDeIntentosTests(_ConUsuario):

    def test_login_limitado_por_ip(self):
        for _ in range(3):
            self.assertNotEqual(self._login(password='mala').status_code, 429)
        respuesta = self._login(password='mala')
        self.assertEqual(respuesta.status_code, 429)
        self.assertIn('Demasiados intentos', str(respuesta.json()))

    def test_login_limitado_por_cuenta_aunque_cambie_la_ip(self):
        for indice in range(5):
            self.assertNotEqual(self._login(ip=f'10.1.0.{indice}', password='mala').status_code, 429)
        self.assertEqual(self._login(ip='10.1.0.99', password='mala').status_code, 429)

    def test_recuperacion_limitada_por_correo(self):
        def pedir(ip):
            return APIClient().post(
                '/api/auth/password-reset/', {'email': 'sesiones@taller.ec'}, format='json', REMOTE_ADDR=ip,
            )
        self.assertEqual(pedir('10.2.0.1').status_code, 200)
        self.assertEqual(pedir('10.2.0.2').status_code, 200)
        self.assertEqual(pedir('10.2.0.3').status_code, 429)


@override_settings(DIAS_VIGENCIA_ENLACE_CITA=7)
class CaducidadEnlaceCitaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = _empresa()
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Central', codigo_sucursal='001', direccion='x')
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000051')
        cls.vehiculo = Vehiculo.objects.create(placa='ICS0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)

    def _descargar(self, dias_desde_hoy):
        cita = Cita.objects.create(
            empresa=self.empresa, taller=self.taller, cliente=self.cliente, vehiculo=self.vehiculo,
            fecha_cita=timezone.localdate() + timedelta(days=dias_desde_hoy), hora_cita=time(9, 0),
        )
        return APIClient().get(f'/api/citas/compartir/{token_enlace_ics(cita.uuid)}/')

    def test_vigente_antes_y_poco_despues_de_la_cita(self):
        self.assertEqual(self._descargar(10).status_code, 200)
        self.assertEqual(self._descargar(-7).status_code, 200)

    def test_caduca_pasados_los_dias_de_vigencia(self):
        self.assertEqual(self._descargar(-8).status_code, 404)

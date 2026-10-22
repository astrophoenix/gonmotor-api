from smtplib import (
    SMTPAuthenticationError, SMTPException, SMTPRecipientsRefused,
    SMTPSenderRefused, SMTPServerDisconnected, SMTPNotSupportedError,
)
from ssl import SSLError
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.authentication.models import UserProfile, UsuarioEmpresa
from apps.authentication.tests import EscenarioMixin, _asignar
from apps.core.testing import fijar_empresa


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class InvitacionEmpleadoTests(EscenarioMixin, APITestCase):
    def setUp(self):
        super().setUp()
        admin = User.objects.create_user(username='admin-invitaciones', password='x')
        _asignar(admin, self.empresa_a, 'ADMIN_EMPRESA', [self.a1])
        self.client.force_authenticate(admin)
        fijar_empresa(self.client, self.empresa_a)
        self.payload = {
            'first_name': 'Nuevo',
            'last_name': 'Empleado',
            'email': 'invitado@taller.ec',
            'rol': 'MECANICO',
            'talleres': [str(self.a1.uuid)],
            'tiene_acceso': True,
        }

    def crear(self):
        return self.client.post('/api/auth/empleados/', self.payload, format='json')

    def test_desconexion_smtp_revierte_alta_y_permite_reintentar(self):
        cantidades = [model.objects.count() for model in (User, UserProfile, UsuarioEmpresa)]
        with patch(
            'apps.authentication.views.EmailMultiAlternatives.send',
            side_effect=SMTPServerDisconnected('please run connect() first'),
        ):
            respuesta = self.crear()
        self.assertEqual(respuesta.status_code, 503)
        self.assertIn('El empleado no fue guardado', respuesta.data['detail'])
        self.assertNotIn('please run connect()', respuesta.data['detail'])
        self.assertEqual(
            [model.objects.count() for model in (User, UserProfile, UsuarioEmpresa)],
            cantidades,
        )
        respuesta = self.crear()
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        empleado = UsuarioEmpresa.objects.get(user__email=self.payload['email'])
        self.assertEqual(list(empleado.talleres.all()), [self.a1])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/authentication/reset-password/', mail.outbox[0].body)

    def test_timeout_de_correo_revierte_alta(self):
        with patch('apps.authentication.views.EmailMultiAlternatives.send', side_effect=TimeoutError):
            respuesta = self.crear()
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.data['code'], 'correo_tiempo_agotado')
        self.assertFalse(User.objects.filter(email=self.payload['email']).exists())

    def test_correo_no_aceptado_revierte_alta(self):
        with patch('apps.authentication.views.EmailMultiAlternatives.send', return_value=0):
            respuesta = self.crear()
        self.assertEqual(respuesta.status_code, 503)
        self.assertFalse(User.objects.filter(email=self.payload['email']).exists())

    def test_sin_acceso_no_depende_del_correo(self):
        self.payload.update(tiene_acceso=False, rol=None, talleres=[])
        with patch('apps.authentication.views.EmailMultiAlternatives.send') as enviar:
            respuesta = self.crear()
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        enviar.assert_not_called()

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.example.test', DEFAULT_FROM_EMAIL='Taller <avisos@example.test>',
        EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='', EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
    )
    def test_configuracion_incompleta_se_detecta_antes_de_crear_o_enviar(self):
        casos = [
            ({'EMAIL_HOST': ''}, 'no está configurado'),
            ({'EMAIL_HOST': '   '}, 'no está configurado'),
            ({'DEFAULT_FROM_EMAIL': ''}, 'Falta configurar el remitente'),
            ({'DEFAULT_FROM_EMAIL': 'correo-invalido'}, 'remitente de las invitaciones no es válido'),
            ({'EMAIL_HOST_USER': 'usuario'}, 'configuración de acceso'),
            ({'EMAIL_HOST_PASSWORD': 'secreto'}, 'configuración de acceso'),
            ({'EMAIL_USE_SSL': True}, 'configuración de seguridad'),
        ]
        for cambios, mensaje in casos:
            with self.subTest(cambios=list(cambios)), self.settings(**cambios):
                with patch.object(User.objects, 'create_user') as crear, patch(
                    'apps.authentication.views.EmailMultiAlternatives.send'
                ) as enviar:
                    respuesta = self.crear()
                self.assertEqual(respuesta.status_code, 503)
                self.assertEqual(respuesta.data['code'], 'correo_no_configurado')
                self.assertIn(mensaje, respuesta.data['detail'])
                crear.assert_not_called()
                enviar.assert_not_called()

    def test_fallos_de_envio_tienen_motivos_especificos_sin_exponer_datos_del_proveedor(self):
        casos = [
            (SMTPAuthenticationError(535, b'dato-privado'), 'correo_autenticacion_fallida', 'autenticación'),
            (SMTPRecipientsRefused({'privado@example.test': (550, b'dato-privado')}), 'correo_destinatario_rechazado', 'destinatario'),
            (SMTPSenderRefused(550, b'dato-privado', 'privado@example.test'), 'correo_remitente_rechazado', 'remitente'),
            (SSLError('dato-privado'), 'correo_seguridad_fallida', 'conexión segura'),
            (SMTPNotSupportedError('dato-privado'), 'correo_seguridad_fallida', 'conexión segura'),
            (ConnectionRefusedError('dato-privado'), 'correo_conexion_fallida', 'conexión'),
            (SMTPServerDisconnected('dato-privado'), 'correo_conexion_fallida', 'conexión'),
            (SMTPException('dato-privado'), 'invitacion_no_enviada', 'correo de invitación'),
        ]
        for fallo, codigo, mensaje in casos:
            with self.subTest(codigo=codigo), patch(
                'apps.authentication.views.EmailMultiAlternatives.send', side_effect=fallo
            ):
                respuesta = self.crear()
            self.assertEqual(respuesta.status_code, 503)
            self.assertEqual(respuesta.data['code'], codigo)
            self.assertIn(mensaje, respuesta.data['detail'])
            self.assertNotIn('dato-privado', str(respuesta.data))
            self.assertNotIn('privado@example.test', str(respuesta.data))
            self.assertFalse(User.objects.filter(email=self.payload['email']).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='')
    def test_sin_acceso_permite_alta_con_smtp_sin_configurar(self):
        self.payload.update(tiene_acceso=False, rol=None, talleres=[])
        with patch('apps.authentication.views.EmailMultiAlternatives.send') as enviar:
            respuesta = self.crear()
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        enviar.assert_not_called()

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.example.test', DEFAULT_FROM_EMAIL='Taller <avisos@example.test>',
        EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='', EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
    )
    def test_smtp_configurado_permite_enviar_invitacion(self):
        with patch('apps.authentication.views.EmailMultiAlternatives.send', return_value=1) as enviar:
            respuesta = self.crear()
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        enviar.assert_called_once()

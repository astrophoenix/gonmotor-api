"""Tests de la cola `NotificacionProgramada`: despacho por canal, reintentos y cron.

Cubre: elección de canal con respaldo, envío por WhatsApp (mock) y por correo,
cliente sin contacto, reintentos con espera creciente, filas no vencidas o de
empresas inactivas, unicidad del mantenimiento pendiente, el comando
`procesar_notificaciones` y el endpoint `cron/procesar/`.
"""

import uuid
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.empresas.models import Empresa
from apps.vehiculos.models import Vehiculo

from .models import NotificacionProgramada, RegistroMensajeWhatsApp
from .services import cola
from .services.despacho import elegir_canal
from .services.whatsapp import WhatsAppError, whatsapp_enviar_y_registrar
from .tests import MOCK_CONFIG

Estado = NotificacionProgramada.Estado


def _sufijo():
    return uuid.uuid4().hex[:8]


def _ruc_unico():
    return str(uuid.uuid4().int % 10_000_000_000_000).zfill(13)


def _cliente(empresa, **campos):
    return Cliente.objects.create(
        empresa=empresa,
        tipo_identificacion='C',
        identificacion=f'171{_sufijo()}',
        nombre=campos.pop('nombre', 'Cliente Cola'),
        **campos,
    )


@override_settings(**MOCK_CONFIG)
class ColaNotificacionesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Cola',
            ruc=_ruc_unico(),
            email_contacto='cola@test.com',
        )
        cls.cliente = _cliente(cls.empresa, telefono='0991234567', email='cliente@test.com')
        cls.vehiculo = Vehiculo.objects.create(
            placa=f'COL{_sufijo()[:4].upper()}', marca='Kia', modelo='Rio',
        )

    def _notificacion(self, **campos):
        datos = {
            'empresa': self.empresa,
            'tipo': NotificacionProgramada.Tipo.RETIRO,
            'cliente': self.cliente,
            'vehiculo': self.vehiculo,
            'mensaje': 'Tu vehículo está listo.',
            'fecha_programada': timezone.now() - timedelta(minutes=1),
        }
        datos.update(campos)
        return NotificacionProgramada.objects.create(**datos)

    # ── Elección de canal ──────────────────────────────────────────────────
    def test_elegir_canal_preferido_y_respaldo(self):
        self.assertEqual(elegir_canal('whatsapp', self.cliente), ('whatsapp', '593991234567'))
        self.assertEqual(elegir_canal('correo', self.cliente), ('correo', 'cliente@test.com'))

        solo_correo = _cliente(self.empresa, email='solo@test.com')
        self.assertEqual(elegir_canal('whatsapp', solo_correo), ('correo', 'solo@test.com'))

        sin_contacto = _cliente(self.empresa)
        self.assertEqual(elegir_canal('whatsapp', sin_contacto), (None, None))
        self.assertEqual(elegir_canal('whatsapp', None), (None, None))

    # ── Envío ──────────────────────────────────────────────────────────────
    def test_procesa_whatsapp_vencida(self):
        notificacion = self._notificacion()
        resumen = cola.procesar_pendientes()

        self.assertEqual(resumen, {'procesadas': 1, 'enviadas': 1, 'reprogramadas': 0, 'errores': 0, 'canceladas': 0})
        notificacion.refresh_from_db()
        self.assertEqual(notificacion.estado, Estado.ENVIADA)
        self.assertEqual(notificacion.intentos, 1)
        self.assertIsNotNone(notificacion.fecha_envio)
        self.assertEqual(notificacion.registro.canal, 'whatsapp')
        self.assertEqual(notificacion.registro.estado, RegistroMensajeWhatsApp.EstadoMensaje.SIMULADO)
        self.assertEqual(notificacion.registro.origen, RegistroMensajeWhatsApp.OrigenMensaje.PROGRAMADO)
        self.assertEqual(notificacion.registro.vehiculo, self.vehiculo)

    def test_procesa_correo_cuando_es_el_canal_preferido(self):
        notificacion = self._notificacion(canal='correo', asunto='Listo para retiro')
        cola.procesar_pendientes()

        notificacion.refresh_from_db()
        self.assertEqual(notificacion.estado, Estado.ENVIADA)
        self.assertEqual(notificacion.registro.canal, 'correo')
        self.assertEqual(notificacion.registro.correo, 'cliente@test.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Listo para retiro')
        self.assertEqual(mail.outbox[0].to, ['cliente@test.com'])

    def test_cliente_sin_contacto_queda_en_error_sin_reintento(self):
        notificacion = self._notificacion(cliente=_cliente(self.empresa))
        resumen = cola.procesar_pendientes()

        self.assertEqual(resumen['errores'], 1)
        notificacion.refresh_from_db()
        self.assertEqual(notificacion.estado, Estado.ERROR)
        self.assertEqual(notificacion.intentos, 1)
        self.assertIn('teléfono ni correo', notificacion.ultimo_error)
        self.assertIsNone(notificacion.registro)

    def test_fallo_del_proveedor_reintenta_y_luego_marca_error(self):
        notificacion = self._notificacion()
        fallo = WhatsAppError('Proveedor caído')
        with mock.patch('apps.notificaciones.services.whatsapp.enviar_mensaje', side_effect=fallo):
            antes = timezone.now()
            resumen = cola.procesar_pendientes()
            self.assertEqual(resumen['reprogramadas'], 1)
            notificacion.refresh_from_db()
            self.assertEqual(notificacion.estado, Estado.PENDIENTE)
            self.assertEqual(notificacion.intentos, 1)
            self.assertGreaterEqual(notificacion.fecha_programada, antes + cola.ESPERA_REINTENTO)
            self.assertEqual(notificacion.registro.estado, RegistroMensajeWhatsApp.EstadoMensaje.ERROR)

            # Ya no está vencida: una nueva ejecución no la toca.
            self.assertEqual(cola.procesar_pendientes()['procesadas'], 0)

            for _ in range(cola.MAX_INTENTOS - 1):
                NotificacionProgramada.objects.filter(pk=notificacion.pk).update(
                    fecha_programada=timezone.now() - timedelta(minutes=1)
                )
                cola.procesar_pendientes()

        notificacion.refresh_from_db()
        self.assertEqual(notificacion.estado, Estado.ERROR)
        self.assertEqual(notificacion.intentos, cola.MAX_INTENTOS)
        self.assertIn('Proveedor caído', notificacion.ultimo_error)

    def test_no_procesa_futuras_canceladas_ni_de_empresa_inactiva(self):
        futura = self._notificacion(fecha_programada=timezone.now() + timedelta(hours=1))
        cancelada = self._notificacion(estado=Estado.CANCELADA)
        inactiva = Empresa.objects.create(
            nombre_comercial='Inactiva', ruc=_ruc_unico(), email_contacto='i@test.com', is_active=False,
        )
        de_inactiva = self._notificacion(empresa=inactiva, cliente=_cliente(inactiva, telefono='0991112233'))

        self.assertEqual(cola.procesar_pendientes()['procesadas'], 0)
        for notificacion, estado in ((futura, Estado.PENDIENTE), (cancelada, Estado.CANCELADA), (de_inactiva, Estado.PENDIENTE)):
            notificacion.refresh_from_db()
            self.assertEqual(notificacion.estado, estado)
            self.assertEqual(notificacion.intentos, 0)

    def test_respeta_el_limite_y_el_orden_por_fecha(self):
        ahora = timezone.now()
        tardia = self._notificacion(fecha_programada=ahora - timedelta(minutes=1))
        temprana = self._notificacion(fecha_programada=ahora - timedelta(hours=1))

        self.assertEqual(cola.procesar_pendientes(limite=1)['procesadas'], 1)
        temprana.refresh_from_db()
        tardia.refresh_from_db()
        self.assertEqual(temprana.estado, Estado.ENVIADA)
        self.assertEqual(tardia.estado, Estado.PENDIENTE)

    # ── Restricciones ──────────────────────────────────────────────────────
    def test_un_solo_mantenimiento_pendiente_por_vehiculo(self):
        tipo = NotificacionProgramada.Tipo.MANTENIMIENTO
        self._notificacion(tipo=tipo)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._notificacion(tipo=tipo)

        # Uno ya resuelto no bloquea al siguiente pendiente.
        NotificacionProgramada.objects.filter(tipo=tipo).update(estado=Estado.ENVIADA)
        self._notificacion(tipo=tipo)

    # ── Comando y endpoint ─────────────────────────────────────────────────
    def test_comando_procesar_notificaciones(self):
        notificacion = self._notificacion()
        salida = StringIO()
        call_command('procesar_notificaciones', stdout=salida)

        self.assertIn('enviadas=1', salida.getvalue())
        notificacion.refresh_from_db()
        self.assertEqual(notificacion.estado, Estado.ENVIADA)

    def test_endpoint_cron(self):
        client = APIClient()
        url = '/api/notificaciones/cron/procesar/'
        self._notificacion()

        with self.settings(NOTIFICACIONES_CRON_TOKEN=''):
            self.assertEqual(client.post(url, HTTP_X_CRON_TOKEN='x').status_code, 503)

        with self.settings(NOTIFICACIONES_CRON_TOKEN='secreto'):
            self.assertEqual(client.post(url).status_code, 403)
            self.assertEqual(client.post(url, HTTP_X_CRON_TOKEN='otro').status_code, 403)
            self.assertEqual(client.get(url, HTTP_X_CRON_TOKEN='secreto').status_code, 405)

            respuesta = client.post(url, HTTP_X_CRON_TOKEN='secreto')
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.data['enviadas'], 1)

    # ── Regresión del wrapper de WhatsApp ──────────────────────────────────
    def test_error_inesperado_devuelve_respuesta_y_no_none(self):
        with mock.patch(
            'apps.notificaciones.services.whatsapp.enviar_mensaje',
            side_effect=RuntimeError('boom'),
        ):
            resultado = whatsapp_enviar_y_registrar(
                empresa=self.empresa, celular='0991234567', mensaje='hola',
            )
        self.assertFalse(resultado['ok'])
        self.assertFalse(resultado['registrado'])
        self.assertIn('boom', resultado['error'])


META_CONFIG = {
    **MOCK_CONFIG,
    'WHATSAPP_MODE': 'meta_api',
    'WHATSAPP_META_TOKEN': 'token-de-prueba',
    'WHATSAPP_META_PHONE_NUMBER_ID': '123',
    'WHATSAPP_META_PLANTILLA_AVISO': 'aviso_taller',
    'WHATSAPP_META_PLANTILLA_PROMOCION': 'promocion_taller',
    'WHATSAPP_META_IDIOMA': 'es',
}


@override_settings(**META_CONFIG)
class PlantillasMetaTests(TestCase):
    """Con `meta_api` y plantillas configuradas, los avisos salen como plantilla."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Meta', ruc=_ruc_unico(), email_contacto='meta@test.com',
        )
        cls.cliente = _cliente(cls.empresa, telefono='0991234567')

    def _enviar(self, **campos):
        notificacion = NotificacionProgramada.objects.create(
            empresa=self.empresa,
            tipo=campos.pop('tipo', NotificacionProgramada.Tipo.RETIRO),
            cliente=self.cliente,
            mensaje='Hola Ana,\nya puedes   retirar tu vehículo.',
            fecha_programada=timezone.now() - timedelta(minutes=1),
        )
        respuesta = mock.Mock(status_code=200)
        respuesta.json.return_value = {'messages': [{'id': 'wamid.1'}]}
        with mock.patch('apps.notificaciones.services.whatsapp.requests.post', return_value=respuesta) as post:
            cola.procesar_pendientes()
        notificacion.refresh_from_db()
        return notificacion, post.call_args.kwargs['json']

    def test_aviso_usa_la_plantilla_de_utilidad(self):
        notificacion, payload = self._enviar()
        self.assertEqual(notificacion.estado, Estado.ENVIADA)
        self.assertEqual(payload['type'], 'template')
        self.assertEqual(payload['template']['name'], 'aviso_taller')
        self.assertEqual(payload['template']['language'], {'code': 'es'})
        parametros = [p['text'] for p in payload['template']['components'][0]['parameters']]
        # Meta rechaza saltos de línea y espacios repetidos en las variables.
        self.assertEqual(parametros, ['Taller Meta', 'Hola Ana, ya puedes retirar tu vehículo.'])
        self.assertEqual(notificacion.registro.metadatos['plantilla'], 'aviso_taller')
        self.assertEqual(notificacion.registro.id_externo, 'wamid.1')

    def test_campana_usa_la_plantilla_de_marketing(self):
        _, payload = self._enviar(tipo=NotificacionProgramada.Tipo.MASIVA)
        self.assertEqual(payload['template']['name'], 'promocion_taller')

    def test_sin_plantilla_configurada_envia_texto_libre(self):
        with self.settings(WHATSAPP_META_PLANTILLA_AVISO=''):
            _, payload = self._enviar()
        self.assertEqual(payload['type'], 'text')

"""Tests del módulo de notificaciones WhatsApp (recordatorios de mantenimiento).

Cubre: normalización de teléfonos, plantillas de mensajes, proveedor mock,
bitácora `RegistroMensajeWhatsApp`, detección de mantenimiento próximo
(fecha/km/sin programar), deduplicación y los endpoints de `/api/notificaciones/`.
"""

import os
import tempfile
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.empresas.models import Empresa
from apps.vehiculos.models import Vehiculo, VehiculoPropietario

from .models import RegistroMensajeWhatsApp
from .services import recordatorios as svc
from .services.mensajes import construir_mensaje_mantenimiento
from .services.telefonos import celular_para_wa_link, normalizar_celular
from .services.whatsapp import (
    enviar_mensaje,
    resumen_configuracion,
    whatsapp_enviar_y_registrar,
)

User = get_user_model()

MOCK_CONFIG = {
    'WHATSAPP_MODE': 'mock',
    'WHATSAPP_MOCK_LOG_FILE': '',
    'WHATSAPP_TWILIO_ACCOUNT_SID': '',
    'WHATSAPP_TWILIO_AUTH_TOKEN': '',
    'WHATSAPP_META_TOKEN': '',
    'WHATSAPP_META_PHONE_NUMBER_ID': '',
}


def _id_prefijo():
    return uuid.uuid4().hex[:8]


def _ruc_unico():
    return str(uuid.uuid4().int % 10_000_000_000_000).zfill(13)


# ────────────────────────────────────────────────────────────────────────────
# Utilidades puras (sin base de datos)
# ────────────────────────────────────────────────────────────────────────────
class TelefonosYPlantillasTests(TestCase):
    def test_normalizar_celular_ecuador(self):
        casos = {
            '0991234567': '593991234567',
            '9 912 345 67': '593991234567',
            '+593 99 123 4567': '593991234567',
            '+593991234567': '593991234567',
            '593991234567': '593991234567',
            '': '',
            None: '',
        }
        for entrada, esperado in casos.items():
            self.assertEqual(normalizar_celular(entrada), esperado, msg=entrada)

    def test_celular_para_wa_link(self):
        self.assertEqual(celular_para_wa_link('0991234567'), '+593991234567')
        self.assertEqual(celular_para_wa_link(''), '')

    @override_settings(**MOCK_CONFIG)
    def test_resumen_configuracion_mock(self):
        info = resumen_configuracion()
        self.assertEqual(info['modo'], 'mock')
        self.assertTrue(info['configurado'])

    def test_construir_mensaje_mantenimiento_placeholders(self):
        texto = construir_mensaje_mantenimiento(
            cliente_nombre='Juan Pérez',
            placa='PBA1234',
            marca='Toyota',
            modelo='Corolla',
            kilometraje=45000,
            empresa_nombre='GonMotor',
        )
        for esperado in ('Juan Pérez', 'Toyota Corolla', 'PBA1234', 'GonMotor'):
            self.assertIn(esperado, texto)

    def test_construir_mensaje_mantenimiento_motivos(self):
        texto = construir_mensaje_mantenimiento(
            cliente_nombre='A', placa='P1', marca='M', modelo='Mo',
            kilometraje=50000, empresa_nombre='E',
            motivo_km=50000,
            motivo_fecha=timezone.localdate() + timedelta(days=3),
        )
        self.assertIn('50000 km', texto)
        self.assertIn('fecha sugerida', texto)

    def test_construir_mensaje_usa_plantilla_de_empresa(self):
        texto = construir_mensaje_mantenimiento(
            cliente_nombre='A', placa='P1', marca='M', modelo='Mo',
            kilometraje=1, empresa_nombre='E',
            plantilla='Hola {cliente} de {empresa} placa {placa}',
        )
        self.assertEqual(texto, 'Hola A de E placa P1')


# ────────────────────────────────────────────────────────────────────────────
# Servicio WhatsApp (proveedor mock) + dominio de recordatorios
# ────────────────────────────────────────────────────────────────────────────
@override_settings(**MOCK_CONFIG)
class RecordatoriosServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        suf = _id_prefijo()
        cls.user = User.objects.create_superuser(
            username=f'wa_user_{suf}', email='wa@test.com', password='x'
        )
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Prueba',
            ruc=_ruc_unico(),
            email_contacto='taller@test.com',
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            tipo_identificacion='C',
            identificacion=f'171{_id_prefijo()}',
            nombre='Cliente Prueba',
            telefono='0991234567',
        )
        cls.vehiculo = Vehiculo.objects.create(
            placa=f'TEST{suf[:4].upper()}',
            marca='Toyota',
            modelo='Corolla',
            kilometraje_actual=30000,
        )
        cls.vehiculo.empresas.add(cls.empresa)
        VehiculoPropietario.objects.create(
            vehiculo=cls.vehiculo, cliente=cls.cliente, es_actual=True
        )

    def test_get_preferencia_crea_con_defaults(self):
        preferencia = svc.get_preferencia(self.empresa)
        self.assertEqual(preferencia.intervalo_km, 5000)
        self.assertEqual(preferencia.dias_antelacion, 7)
        self.assertEqual(preferencia.frecuencia_dias, 30)
        self.assertTrue(preferencia.notificaciones_activas)
        self.assertFalse(preferencia.notificar_vehiculos_sin_programar)
        self.assertTrue(preferencia.mensaje_plantilla)

    def test_enviar_mensaje_mock(self):
        resultado = enviar_mensaje('0999876543', 'Hola de prueba')
        self.assertTrue(resultado['ok'])
        self.assertTrue(resultado['simulado'])
        self.assertEqual(resultado['proveedor'], 'mock')
        self.assertEqual(resultado['celular_normalizado'], '593999876543')
        self.assertIn('wa.me/+593999876543', resultado['wa_link'])

    def test_enviar_mensaje_mock_escribe_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, 'wa.log')
            with self.settings(WHATSAPP_MOCK_LOG_FILE=log_path):
                resultado = enviar_mensaje('0991234567', 'Mensaje de prueba')
            self.assertTrue(resultado['ok'])
            self.assertTrue(os.path.exists(log_path))
            with open(log_path, encoding='utf-8') as fh:
                contenido = fh.read()
            self.assertIn('Mensaje de prueba', contenido)
            self.assertIn('+593991234567', contenido)

    def test_enviar_mensaje_sin_telefono_lanza_error(self):
        from .services.whatsapp import WhatsAppError
        with self.assertRaises(WhatsAppError):
            enviar_mensaje('', 'hola')

    def test_whatsapp_enviar_y_registrar_simulado(self):
        resultado = whatsapp_enviar_y_registrar(
            empresa=self.empresa,
            celular='0987654321',
            mensaje='Hola',
            origen='PRUEBA',
            vehiculo=self.vehiculo,
            cliente_nombre='Cliente Prueba',
            user=self.user,
        )
        self.assertTrue(resultado['ok'])
        self.assertTrue(resultado['registrado'])
        registro = RegistroMensajeWhatsApp.objects.get(
            empresa=self.empresa, vehiculo=self.vehiculo
        )
        self.assertEqual(registro.estado, RegistroMensajeWhatsApp.EstadoMensaje.SIMULADO)
        self.assertEqual(registro.origen, RegistroMensajeWhatsApp.OrigenMensaje.PRUEBA)
        self.assertEqual(registro.celular, '593987654321')
        self.assertEqual(registro.enviado_por, self.user)

    def test_enviar_recordatorio_vehiculo(self):
        preferencia = svc.get_preferencia(self.empresa)
        resultado = svc.enviar_recordatorio_vehiculo(
            vehiculo=self.vehiculo,
            empresa=self.empresa,
            preferencia=preferencia,
            cliente=self.cliente,
            celular=normalizar_celular(self.cliente.telefono),
            origen='MANUAL',
            user=self.user,
        )
        self.assertTrue(resultado['ok'])
        self.assertEqual(
            RegistroMensajeWhatsApp.objects.filter(vehiculo=self.vehiculo).count(), 1
        )

    def test_candidato_por_fecha(self):
        Vehiculo.objects.filter(pk=self.vehiculo.pk).update(
            proxima_mantenimiento_fecha=timezone.localdate() + timedelta(days=2)
        )
        candidatos = svc.vehiculos_proximos_mantenimiento(
            self.empresa.pk, svc.get_preferencia(self.empresa)
        )
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(candidatos[0]['vehiculo'], self.vehiculo)
        self.assertEqual(candidatos[0]['motivo'], 'fecha')

    def test_candidato_por_km(self):
        Vehiculo.objects.filter(pk=self.vehiculo.pk).update(
            proximo_mantenimiento_km=30000, kilometraje_actual=30500
        )
        candidatos = svc.vehiculos_proximos_mantenimiento(
            self.empresa.pk, svc.get_preferencia(self.empresa)
        )
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(candidatos[0]['motivo'], 'km')

    def test_sin_programar_solo_con_preferencia(self):
        preferencia = svc.get_preferencia(self.empresa)
        self.assertEqual(
            svc.vehiculos_proximos_mantenimiento(self.empresa.pk, preferencia), []
        )
        preferencia.notificar_vehiculos_sin_programar = True
        preferencia.save()
        candidatos = svc.vehiculos_proximos_mantenimiento(
            self.empresa.pk, preferencia
        )
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(candidatos[0]['motivo'], 'sin_programar')

    def test_sin_telefono_no_es_candidato(self):
        Cliente.objects.filter(pk=self.cliente.pk).update(telefono='')
        preferencia = svc.get_preferencia(self.empresa)
        preferencia.notificar_vehiculos_sin_programar = True
        preferencia.save()
        self.assertEqual(
            svc.vehiculos_proximos_mantenimiento(self.empresa.pk, preferencia), []
        )

    def test_deduplicacion_por_frecuencia(self):
        preferencia = svc.get_preferencia(self.empresa)
        RegistroMensajeWhatsApp.objects.create(
            empresa=self.empresa,
            vehiculo=self.vehiculo,
            celular='593991234567',
            mensaje='ya enviado',
            estado=RegistroMensajeWhatsApp.EstadoMensaje.SIMULADO,
            origen=RegistroMensajeWhatsApp.OrigenMensaje.PROGRAMADO,
        )
        Vehiculo.objects.filter(pk=self.vehiculo.pk).update(
            proxima_mantenimiento_fecha=timezone.localdate() + timedelta(days=2)
        )
        self.assertEqual(
            svc.vehiculos_proximos_mantenimiento(self.empresa.pk, preferencia), []
        )

    def test_ejecutar_preview_devuelve_candidatos_sin_enviar(self):
        Vehiculo.objects.filter(pk=self.vehiculo.pk).update(
            proxima_mantenimiento_fecha=timezone.localdate() + timedelta(days=2)
        )
        resultado = svc.ejecutar_para_empresa(
            empresa=self.empresa,
            preferencia=svc.get_preferencia(self.empresa),
            preview=True,
        )
        self.assertTrue(resultado['preview'])
        self.assertEqual(resultado['pendientes'], 1)
        self.assertEqual(resultado['enviados'], 0)
        self.assertEqual(resultado['candidatos'][0]['placa'], self.vehiculo.placa)

    def test_ejecutar_envia_y_registra(self):
        Vehiculo.objects.filter(pk=self.vehiculo.pk).update(
            proxima_mantenimiento_fecha=timezone.localdate()
        )
        resultado = svc.ejecutar_para_empresa(
            empresa=self.empresa,
            preferencia=svc.get_preferencia(self.empresa),
            user=self.user,
        )
        self.assertFalse(resultado['preview'])
        self.assertEqual(resultado['enviados'], 1)
        self.assertTrue(resultado['simulado'])
        self.assertEqual(
            RegistroMensajeWhatsApp.objects.filter(
                vehiculo=self.vehiculo, origen=RegistroMensajeWhatsApp.OrigenMensaje.PROGRAMADO
            ).count(),
            1,
        )

    def test_ejecutar_detenido_si_notificaciones_inactivas(self):
        preferencia = svc.get_preferencia(self.empresa)
        preferencia.notificaciones_activas = False
        preferencia.save()
        resultado = svc.ejecutar_para_empresa(
            empresa=self.empresa, preferencia=preferencia
        )
        self.assertTrue(resultado['detenido'])
        self.assertEqual(resultado['enviados'], 0)


# ────────────────────────────────────────────────────────────────────────────
# API /api/notificaciones/
# ────────────────────────────────────────────────────────────────────────────
@override_settings(**MOCK_CONFIG)
class NotificacionesApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        suf = _id_prefijo()
        cls.user = User.objects.create_superuser(
            username=f'wa_api_{suf}', email='api@test.com', password='x'
        )
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller API',
            ruc=_ruc_unico(),
            email_contacto='taller-api@test.com',
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            tipo_identificacion='C',
            identificacion=f'171{_id_prefijo()}',
            nombre='Cliente API',
            telefono='0998887777',
        )
        cls.vehiculo = Vehiculo.objects.create(
            placa=f'API{suf[:4].upper()}',
            marca='Kia',
            modelo='Sportage',
            kilometraje_actual=20000,
        )
        cls.vehiculo.empresas.add(cls.empresa)
        VehiculoPropietario.objects.create(
            vehiculo=cls.vehiculo, cliente=cls.cliente, es_actual=True
        )

        # Empresa ajena + vehículo ajeno (para probar aislamiento multi-tenant)
        cls.otra_empresa = Empresa.objects.create(
            nombre_comercial='Taller Ajeno',
            ruc=_ruc_unico(),
            email_contacto='ajeno@test.com',
        )
        cls.vehiculo_ajeno = Vehiculo.objects.create(
            placa=f'XTR{suf[:4].upper()}', marca='Otro', modelo='Modelo'
        )
        cls.vehiculo_ajeno.empresas.add(cls.otra_empresa)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = self.empresa.pk

    def _marcar_fecha_cercana(self):
        Vehiculo.objects.filter(pk=self.vehiculo.pk).update(
            proxima_mantenimiento_fecha=timezone.localdate() + timedelta(days=2)
        )

    def test_estado_ok(self):
        resp = self.client.get('/api/notificaciones/estado/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['modo'], 'mock')
        self.assertIn('preferencia', data)

    def test_estado_requiere_autenticacion(self):
        c = APIClient()
        c.defaults['HTTP_X_EMPRESA_ID'] = self.empresa.pk
        resp = c.get('/api/notificaciones/estado/')
        self.assertEqual(resp.status_code, 401)

    def test_preferencias_get(self):
        resp = self.client.get('/api/notificaciones/preferencias/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['notificaciones_activas'])
        self.assertEqual(resp.json()['dias_antelacion'], 7)

    def test_preferencias_patch(self):
        resp = self.client.patch(
            '/api/notificaciones/preferencias/',
            {'dias_antelacion': 14, 'notificar_vehiculos_sin_programar': True},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['dias_antelacion'], 14)
        self.assertTrue(data['notificar_vehiculos_sin_programar'])
        self.assertEqual(data['empresa'], self.empresa.pk)

    def test_enviar_prueba_ok(self):
        resp = self.client.post(
            '/api/notificaciones/recordatorios/enviar-prueba/',
            {'celular': '0997776655'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['simulado'])
        self.assertIn('wa.me/+593997776655', data['wa_link'])
        self.assertEqual(
            RegistroMensajeWhatsApp.objects.filter(
                origen=RegistroMensajeWhatsApp.OrigenMensaje.PRUEBA
            ).count(),
            1,
        )

    def test_enviar_prueba_validacion_celular(self):
        resp = self.client.post(
            '/api/notificaciones/recordatorios/enviar-prueba/',
            {'celular': '123'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_ejecutar_preview(self):
        self._marcar_fecha_cercana()
        resp = self.client.post(
            '/api/notificaciones/recordatorios/ejecutar/',
            {'preview': True},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['preview'])
        self.assertEqual(data['pendientes'], 1)
        self.assertEqual(data['candidatos'][0]['placa'], self.vehiculo.placa)

    def test_ejecutar_envia(self):
        self._marcar_fecha_cercana()
        resp = self.client.post(
            '/api/notificaciones/recordatorios/ejecutar/',
            {'preview': False},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['enviados'], 1)

    def test_ejecutar_vehiculos_ids_filtra(self):
        self._marcar_fecha_cercana()
        resp = self.client.post(
            '/api/notificaciones/recordatorios/ejecutar/',
            {'preview': True, 'vehiculos_ids': [999999]},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['pendientes'], 0)

    def test_enviar_vehiculo_ok(self):
        resp = self.client.post(
            '/api/notificaciones/recordatorios/enviar-vehiculo/',
            {'vehiculo_id': self.vehiculo.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['estado'], 'SIMULADO')
        self.assertEqual(data['placa'], self.vehiculo.placa)
        self.assertEqual(data['cliente'], 'Cliente API')

    def test_enviar_vehiculo_omitido_si_reciente(self):
        self.client.post(
            '/api/notificaciones/recordatorios/enviar-vehiculo/',
            {'vehiculo_id': self.vehiculo.pk},
            format='json',
        )
        resp = self.client.post(
            '/api/notificaciones/recordatorios/enviar-vehiculo/',
            {'vehiculo_id': self.vehiculo.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['omitido'])

    def test_enviar_vehiculo_no_en_empresa(self):
        resp = self.client.post(
            '/api/notificaciones/recordatorios/enviar-vehiculo/',
            {'vehiculo_id': self.vehiculo_ajeno.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_enviar_vehiculo_sin_propietario(self):
        vehiculo = Vehiculo.objects.create(
            placa=f'SOL{_id_prefijo()[:4].upper()}',
            marca='Solo',
            modelo='Vehículo',
        )
        vehiculo.empresas.add(self.empresa)
        resp = self.client.post(
            '/api/notificaciones/recordatorios/enviar-vehiculo/',
            {'vehiculo_id': vehiculo.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_recordatorios_lista_y_filtros(self):
        self.client.post(
            '/api/notificaciones/recordatorios/enviar-prueba/',
            {'celular': '0991112221'},
            format='json',
        )
        resp = self.client.get('/api/notificaciones/recordatorios/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.json()['total'], 1)

        resp = self.client.get(
            '/api/notificaciones/recordatorios/', {'origen': 'PRUEBA'}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total'], 1)

        resp = self.client.get(
            '/api/notificaciones/recordatorios/', {'estado': 'NOEXISTE'}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total'], 0)

    def test_recordatorios_aislados_por_empresa(self):
        RegistroMensajeWhatsApp.objects.create(
            empresa=self.otra_empresa,
            celular='593999999999',
            mensaje='ajeno',
            estado=RegistroMensajeWhatsApp.EstadoMensaje.SIMULADO,
        )
        resp = self.client.get('/api/notificaciones/recordatorios/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(RegistroMensajeWhatsApp.objects.filter(empresa=self.otra_empresa).exists())
        self.assertEqual(resp.json()['total'], 0)
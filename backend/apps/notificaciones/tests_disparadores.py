"""Tests de los disparadores de notificaciones (Fase 2).

Cubre: cálculo del próximo mantenimiento al entregar la OT (por tiempo, por
ritmo de km, intervalos del servicio, tipos excluidos), aviso de retiro,
recordatorios de cita, la API de la cola (`programadas/`) y las campañas.
"""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.citas.models import Cita
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa
from apps.inventario.models import Servicio
from apps.ordenes.models import DetalleServicioOrdenTrabajo, OrdenTrabajo
from apps.vehiculos.models import Vehiculo, VehiculoPropietario, vincular_empresa

from .models import Campana, NotificacionProgramada
from .services import cola
from .services.mantenimiento import calcular_proximo_mantenimiento, sumar_meses
from .services.mensajes import renderizar_plantilla
from .services.recordatorios import get_preferencia
from .tests import MOCK_CONFIG

User = get_user_model()
Tipo = NotificacionProgramada.Tipo
Estado = NotificacionProgramada.Estado

URL_PROGRAMADAS = '/api/notificaciones/programadas/'
URL_CAMPANAS = '/api/notificaciones/campanas/'


def _sufijo():
    return uuid.uuid4().hex[:8]


def _ruc_unico():
    return str(uuid.uuid4().int % 10_000_000_000_000).zfill(13)


def _a_las_9(dia):
    return timezone.make_aware(datetime.combine(dia, time(9, 0)), timezone.get_current_timezone())


class BaseDisparadores(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            username=f'notif_{_sufijo()}', email='n@test.com', password='x',
        )
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Disparadores', ruc=_ruc_unico(), email_contacto='d@test.com',
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre_comercial='Otro Taller', ruc=_ruc_unico(), email_contacto='o@test.com',
        )
        cls.cliente = cls._crear_cliente(cls.empresa, telefono='0991234567', email='c@test.com')
        cls.vehiculo = cls._crear_vehiculo(cls.empresa, cls.cliente, km=45000)

    @staticmethod
    def _crear_cliente(empresa, **campos):
        return Cliente.objects.create(
            empresa=empresa,
            tipo_identificacion='C',
            identificacion=f'171{_sufijo()}',
            nombre=campos.pop('nombre', 'Cliente Disparador'),
            **campos,
        )

    @staticmethod
    def _crear_vehiculo(empresa, cliente, km=0, marca='Toyota'):
        vehiculo = Vehiculo.objects.create(
            placa=f'D{_sufijo()[:6].upper()}', marca=marca, modelo='Hilux', kilometraje_actual=km,
        )
        vincular_empresa(vehiculo, empresa.pk)
        VehiculoPropietario.objects.create(vehiculo=vehiculo, cliente=cliente, es_actual=True)
        return vehiculo

    def _orden(self, **campos):
        datos = {
            'empresa': self.empresa,
            'cliente': self.cliente,
            'vehiculo': self.vehiculo,
            'numero_orden': f'OT-{_sufijo()}',
            'kilometraje': 45000,
            'estado': OrdenTrabajo.EstadoOrden.COMPLETADO,
        }
        datos.update(campos)
        return OrdenTrabajo.objects.create(**datos)

    def _entregar(self, orden, fecha_entrega=None):
        orden.estado = OrdenTrabajo.EstadoOrden.ENTREGADO
        orden.fecha_entrega = fecha_entrega or timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            orden.save()
        return orden

    def _api(self):
        client = APIClient()
        client.force_authenticate(self.user)
        fijar_empresa(client, self.empresa)
        return client


# ────────────────────────────────────────────────────────────────────────────
# Próximo mantenimiento al entregar la OT
# ────────────────────────────────────────────────────────────────────────────
@override_settings(**MOCK_CONFIG)
class MantenimientoAlEntregarTests(BaseDisparadores):
    def _mantenimientos(self):
        return NotificacionProgramada.objects.filter(vehiculo=self.vehiculo, tipo=Tipo.MANTENIMIENTO)

    def test_sumar_meses_ajusta_fin_de_mes(self):
        self.assertEqual(sumar_meses(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(sumar_meses(date(2026, 8, 15), 6), date(2027, 2, 15))
        self.assertEqual(sumar_meses(date(2026, 12, 1), 12), date(2027, 12, 1))

    def test_entrega_programa_por_tiempo_sin_historial(self):
        entrega = timezone.now()
        orden = self._entregar(self._orden(), entrega)

        notificacion = self._mantenimientos().get()
        dia_entrega = timezone.localdate(entrega)
        tope = sumar_meses(dia_entrega, 6)
        self.assertEqual(notificacion.estado, Estado.PENDIENTE)
        self.assertEqual(notificacion.origen, NotificacionProgramada.Origen.AUTO)
        self.assertEqual(notificacion.orden, orden)
        self.assertEqual(notificacion.cliente, self.cliente)
        self.assertEqual(notificacion.km_objetivo, 50000)
        self.assertEqual(notificacion.fecha_tope, tope)
        self.assertEqual(notificacion.fecha_programada, _a_las_9(tope - timedelta(days=7)))
        self.assertIn('50000 km', notificacion.mensaje)

        self.vehiculo.refresh_from_db()
        self.assertEqual(self.vehiculo.proximo_mantenimiento_km, 50000)
        self.assertEqual(self.vehiculo.proxima_mantenimiento_fecha, tope)

    def test_volver_a_guardar_una_orden_entregada_no_duplica(self):
        orden = self._entregar(self._orden())
        orden.observaciones = 'Nota posterior'
        with self.captureOnCommitCallbacks(execute=True):
            orden.save()
        self.assertEqual(self._mantenimientos().count(), 1)

    def test_ritmo_de_km_adelanta_la_fecha(self):
        ahora = timezone.now()
        self._entregar(self._orden(kilometraje=40000), ahora - timedelta(days=60))
        # 6000 km en 60 días = 100 km/día → 5000 km en 50 días (antes que 6 meses).
        orden = self._entregar(self._orden(kilometraje=46000), ahora)

        plan = calcular_proximo_mantenimiento(orden, get_preferencia(self.empresa))
        self.assertEqual(plan['km_por_dia'], 100)
        self.assertEqual(plan['motivo'], 'km')
        self.assertEqual(plan['fecha_objetivo'], timezone.localdate(ahora) + timedelta(days=50))

        pendiente = self._mantenimientos().get(estado=Estado.PENDIENTE)
        self.assertEqual(pendiente.orden, orden)
        self.assertEqual(pendiente.km_objetivo, 51000)
        self.assertEqual(pendiente.fecha_programada, _a_las_9(plan['fecha_objetivo'] - timedelta(days=7)))
        # La alerta de la entrega anterior quedó cancelada.
        self.assertEqual(self._mantenimientos().filter(estado=Estado.CANCELADA).count(), 1)

    def test_intervalos_del_servicio_tienen_prioridad(self):
        orden = self._orden()
        for codigo, km, meses in (('CAMBIO-ACEITE', 3000, 4), ('ALINEACION', 10000, None)):
            servicio = Servicio.objects.create(
                empresa=self.empresa, codigo=f'{codigo}-{_sufijo()}', nombre=codigo,
                intervalo_km=km, intervalo_meses=meses,
            )
            DetalleServicioOrdenTrabajo.objects.create(
                orden_trabajo=orden, servicio=servicio, descripcion=codigo, precio_unitario=Decimal('10'),
            )
        entrega = timezone.now()
        self._entregar(orden, entrega)

        notificacion = self._mantenimientos().get()
        self.assertEqual(notificacion.km_objetivo, 48000)
        self.assertEqual(notificacion.fecha_tope, sumar_meses(timezone.localdate(entrega), 4))

    def test_tipos_sin_mantenimiento_y_notificaciones_apagadas(self):
        self._entregar(self._orden(tipo_trabajo='GARANTIA'))
        self.assertFalse(self._mantenimientos().exists())

        preferencia = get_preferencia(self.empresa)
        preferencia.notificaciones_activas = False
        preferencia.save()
        self._entregar(self._orden())
        self.assertFalse(self._mantenimientos().exists())

    def test_el_mantenimiento_programado_sale_por_la_cola(self):
        self._entregar(self._orden())
        notificacion = self._mantenimientos().get()
        self.assertEqual(cola.procesar_pendientes()['procesadas'], 0)

        resumen = cola.procesar_pendientes(ahora=notificacion.fecha_programada)
        self.assertEqual(resumen['enviadas'], 1)


# ────────────────────────────────────────────────────────────────────────────
# Aviso de retiro
# ────────────────────────────────────────────────────────────────────────────
@override_settings(**MOCK_CONFIG)
class NotificarRetiroTests(BaseDisparadores):
    def _url(self, orden):
        return f'/api/ordenes/ordenes-trabajo/{orden.uuid}/notificar-retiro/'

    def test_solo_con_la_orden_completada(self):
        orden = self._orden(estado=OrdenTrabajo.EstadoOrden.EN_PROCESO)
        respuesta = self._api().post(self._url(orden), {}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(NotificacionProgramada.objects.filter(orden=orden).exists())

    def test_envia_y_pide_confirmar_el_reenvio(self):
        orden = self._orden()
        client = self._api()

        respuesta = client.post(self._url(orden), {}, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertTrue(respuesta.data['resultado']['ok'])
        self.assertEqual(respuesta.data['notificacion']['estado'], Estado.ENVIADA)
        notificacion = NotificacionProgramada.objects.get(orden=orden)
        self.assertEqual(notificacion.tipo, Tipo.RETIRO)
        self.assertEqual(notificacion.creado_por, self.user)
        self.assertIn('listo para ser retirado', notificacion.mensaje)
        self.assertEqual(notificacion.registro.origen, 'MANUAL')

        self.assertEqual(client.post(self._url(orden), {}, format='json').status_code, 409)
        respuesta = client.post(self._url(orden), {'reenviar': True, 'canal': 'correo'}, format='json')
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.data['resultado']['canal'], 'correo')

    def test_orden_de_otra_empresa_responde_404(self):
        cliente = self._crear_cliente(self.otra_empresa, telefono='0991112222')
        orden = self._orden(empresa=self.otra_empresa, cliente=cliente)
        self.assertEqual(self._api().post(self._url(orden), {}, format='json').status_code, 404)


# ────────────────────────────────────────────────────────────────────────────
# Recordatorios de cita
# ────────────────────────────────────────────────────────────────────────────
@override_settings(**MOCK_CONFIG)
class RecordatoriosCitaTests(BaseDisparadores):
    def _cita(self, inicio, **campos):
        local = timezone.localtime(inicio)
        datos = {
            'empresa': self.empresa,
            'cliente': self.cliente,
            'vehiculo': self.vehiculo,
            'fecha_cita': local.date(),
            'hora_cita': local.time().replace(second=0, microsecond=0),
        }
        datos.update(campos)
        with self.captureOnCommitCallbacks(execute=True):
            return Cita.objects.create(**datos)

    def _guardar(self, cita):
        with self.captureOnCommitCallbacks(execute=True):
            cita.save()

    def _recordatorios(self, cita, estado=Estado.PENDIENTE):
        return {n.tipo: n for n in NotificacionProgramada.objects.filter(cita=cita, estado=estado)}

    def test_crea_recordatorios_24h_y_2h(self):
        cita = self._cita(timezone.now() + timedelta(days=3))
        recordatorios = self._recordatorios(cita)
        self.assertEqual(set(recordatorios), {Tipo.CITA_24H, Tipo.CITA_2H})
        self.assertEqual(recordatorios[Tipo.CITA_24H].fecha_programada, cita.fecha_hora_programada - timedelta(hours=24))
        self.assertEqual(recordatorios[Tipo.CITA_2H].fecha_programada, cita.fecha_hora_programada - timedelta(hours=2))
        self.assertIn('te recordamos tu cita', recordatorios[Tipo.CITA_2H].mensaje)

    def test_reprogramar_mueve_los_recordatorios(self):
        cita = self._cita(timezone.now() + timedelta(days=3))
        cita.fecha_cita = cita.fecha_cita + timedelta(days=2)
        self._guardar(cita)

        recordatorios = self._recordatorios(cita)
        self.assertEqual(len(recordatorios), 2)
        self.assertEqual(recordatorios[Tipo.CITA_24H].fecha_programada, cita.fecha_hora_programada - timedelta(hours=24))
        self.assertEqual(NotificacionProgramada.objects.filter(cita=cita).count(), 2)

    def test_cita_cercana_solo_lleva_el_de_2h(self):
        cita = self._cita(timezone.now() + timedelta(hours=5))
        self.assertEqual(set(self._recordatorios(cita)), {Tipo.CITA_2H})

    def test_cancelar_o_eliminar_cancela_los_pendientes(self):
        cita = self._cita(timezone.now() + timedelta(days=3))
        cita.estado = Cita.EstadoCita.CANCELADA
        self._guardar(cita)
        self.assertEqual(self._recordatorios(cita), {})
        self.assertEqual(len(self._recordatorios(cita, Estado.CANCELADA)), 2)

        otra = self._cita(timezone.now() + timedelta(days=4))
        with self.captureOnCommitCallbacks(execute=True):
            otra.delete()
        self.assertEqual(self._recordatorios(otra), {})

    def test_preferencia_desactivada_no_programa(self):
        preferencia = get_preferencia(self.empresa)
        preferencia.recordatorios_cita_activos = False
        preferencia.save()
        cita = self._cita(timezone.now() + timedelta(days=3))
        self.assertEqual(self._recordatorios(cita), {})

    def test_aviso_manual_de_cita(self):
        cita = self._cita(timezone.now() + timedelta(days=3))
        client = self._api()
        url = f'/api/citas/{cita.uuid}/notificar/'

        respuesta = client.post(url, {'canal': 'correo'}, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data['resultado']['canal'], 'correo')
        aviso = NotificacionProgramada.objects.get(cita=cita, tipo=Tipo.CITA)
        self.assertEqual(aviso.estado, Estado.ENVIADA)
        self.assertIn('te recordamos tu cita del', aviso.mensaje)
        # Los recordatorios automáticos siguen pendientes.
        self.assertEqual(set(self._recordatorios(cita)), {Tipo.CITA_24H, Tipo.CITA_2H})

        cita.estado = Cita.EstadoCita.CANCELADA
        self._guardar(cita)
        self.assertEqual(client.post(url, {}, format='json').status_code, 400)

    def test_la_cola_no_envia_si_la_cita_cambio_sin_senal(self):
        cita = self._cita(timezone.now() + timedelta(days=3))
        Cita.objects.filter(pk=cita.pk).update(estado=Cita.EstadoCita.CANCELADA)

        resumen = cola.procesar_pendientes(ahora=cita.fecha_hora_programada)
        self.assertEqual(resumen['canceladas'], 2)
        self.assertEqual(resumen['enviadas'], 0)


# ────────────────────────────────────────────────────────────────────────────
# API de la cola: /api/notificaciones/programadas/
# ────────────────────────────────────────────────────────────────────────────
@override_settings(**MOCK_CONFIG)
class ProgramadasApiTests(BaseDisparadores):
    def _notificacion(self, empresa=None, **campos):
        datos = {
            'empresa': empresa or self.empresa,
            'tipo': Tipo.RETIRO,
            'cliente': self.cliente,
            'mensaje': 'Hola',
            'fecha_programada': timezone.now() + timedelta(days=1),
        }
        datos.update(campos)
        return NotificacionProgramada.objects.create(**datos)

    def test_listado_filtra_por_empresa_y_estado(self):
        propia = self._notificacion()
        self._notificacion(estado=Estado.ENVIADA)
        self._notificacion(empresa=self.otra_empresa, cliente=self._crear_cliente(self.otra_empresa))

        datos = self._api().get(URL_PROGRAMADAS, {'estado': 'PENDIENTE'}).data
        self.assertEqual(datos['count'], 1)
        self.assertEqual(datos['results'][0]['id'], str(propia.uuid))
        self.assertEqual(self._api().get(URL_PROGRAMADAS).data['count'], 2)
        self.assertEqual(self._api().get(URL_PROGRAMADAS, {'vehiculo': 'no-es-uuid'}).data['count'], 0)

    def test_alta_manual_de_mantenimiento(self):
        client = self._api()
        anterior = self._notificacion(tipo=Tipo.MANTENIMIENTO, vehiculo=self.vehiculo)
        objetivo = timezone.localdate() + timedelta(days=30)

        respuesta = client.post(URL_PROGRAMADAS, {
            'vehiculo_id': str(self.vehiculo.uuid), 'km_objetivo': 52000, 'fecha_objetivo': objetivo.isoformat(),
        }, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data['origen'], 'MANUAL')
        self.assertEqual(respuesta.data['km_objetivo'], 52000)
        anterior.refresh_from_db()
        self.assertEqual(anterior.estado, Estado.CANCELADA)
        self.vehiculo.refresh_from_db()
        self.assertEqual(self.vehiculo.proxima_mantenimiento_fecha, objetivo)

        self.assertEqual(client.post(URL_PROGRAMADAS, {'vehiculo_id': str(self.vehiculo.uuid)}, format='json').status_code, 400)
        ajeno = Vehiculo.objects.create(placa=f'X{_sufijo()[:6].upper()}', marca='M', modelo='X')
        vincular_empresa(ajeno, self.otra_empresa.pk)
        respuesta = client.post(URL_PROGRAMADAS, {'vehiculo_id': str(ajeno.uuid), 'km_objetivo': 1000}, format='json')
        self.assertEqual(respuesta.status_code, 404)

    def test_editar_cancelar_y_enviar_ahora(self):
        client = self._api()
        notificacion = self._notificacion()
        url = f'{URL_PROGRAMADAS}{notificacion.uuid}/'

        respuesta = client.patch(url, {'mensaje': 'Texto editado', 'estado': 'ENVIADA'}, format='json')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['mensaje'], 'Texto editado')
        self.assertEqual(respuesta.data['estado'], Estado.PENDIENTE)

        respuesta = client.post(f'{url}enviar-ahora/')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data['notificacion']['estado'], Estado.ENVIADA)

        self.assertEqual(client.patch(url, {'mensaje': 'otra vez'}, format='json').status_code, 400)
        self.assertEqual(client.post(f'{url}cancelar/').status_code, 400)

        otra = self._notificacion()
        respuesta = client.post(f'{URL_PROGRAMADAS}{otra.uuid}/cancelar/')
        self.assertEqual(respuesta.data['estado'], Estado.CANCELADA)

    def test_no_se_ve_ni_se_toca_la_de_otra_empresa(self):
        ajena = self._notificacion(empresa=self.otra_empresa, cliente=self._crear_cliente(self.otra_empresa))
        client = self._api()
        self.assertEqual(client.get(f'{URL_PROGRAMADAS}{ajena.uuid}/').status_code, 404)
        self.assertEqual(client.post(f'{URL_PROGRAMADAS}{ajena.uuid}/enviar-ahora/').status_code, 404)


# ────────────────────────────────────────────────────────────────────────────
# Campañas
# ────────────────────────────────────────────────────────────────────────────
@override_settings(**MOCK_CONFIG)
class CampanasTests(BaseDisparadores):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.acepta = cls._crear_cliente(cls.empresa, nombre='Ana Acepta', telefono='0990000001', acepta_promociones=True)
        cls.solo_correo = cls._crear_cliente(cls.empresa, nombre='Beto Correo', email='b@test.com', acepta_promociones=True)
        cls._crear_cliente(cls.empresa, nombre='Sin Consentimiento', telefono='0990000002')
        cls._crear_cliente(cls.empresa, nombre='Sin Contacto', acepta_promociones=True)
        cls._crear_vehiculo(cls.empresa, cls.acepta, marca='Chevrolet')

    def _crear(self, client, **campos):
        datos = {'nombre': 'Promo', 'mensaje': 'Hola {cliente}, {empresa} te regala {un lavado}.'}
        datos.update(campos)
        respuesta = client.post(URL_CAMPANAS, datos, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        return respuesta.data

    def test_renderizar_plantilla_tolera_llaves_sueltas(self):
        self.assertEqual(
            renderizar_plantilla('Hola {cliente} {x} {', cliente='Ana'),
            'Hola Ana {x} {',
        )

    def test_audiencia_y_segmentos(self):
        client = self._api()
        campana = self._crear(client)
        datos = client.get(f'{URL_CAMPANAS}{campana["id"]}/audiencia/').data
        self.assertEqual(datos['total'], 2)
        self.assertEqual([c['nombre'] for c in datos['muestra']], ['Ana Acepta', 'Beto Correo'])

        campana = self._crear(client, segmento={'marca': 'chevro'})
        self.assertEqual(client.get(f'{URL_CAMPANAS}{campana["id"]}/audiencia/').data['total'], 1)

        orden = self._orden(cliente=self.acepta)
        self._entregar(orden)
        campana = self._crear(client, segmento={'sin_visita_dias': 30})
        datos = client.get(f'{URL_CAMPANAS}{campana["id"]}/audiencia/').data
        self.assertEqual([c['nombre'] for c in datos['muestra']], ['Beto Correo'])

        respuesta = client.post(URL_CAMPANAS, {'nombre': 'X', 'mensaje': 'Y', 'segmento': {'sin_visita_dias': 0}}, format='json')
        self.assertEqual(respuesta.status_code, 400)

    def test_lanzar_y_cancelar(self):
        client = self._api()
        campana = self._crear(client)
        url = f'{URL_CAMPANAS}{campana["id"]}/'

        respuesta = client.post(f'{url}lanzar/')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data['estado'], 'LANZADA')
        self.assertEqual(respuesta.data['total_destinatarios'], 2)
        self.assertEqual(respuesta.data['pendientes'], 2)

        mensajes = NotificacionProgramada.objects.filter(campana__uuid=campana['id'], tipo=Tipo.MASIVA)
        self.assertEqual(
            mensajes.get(cliente=self.acepta).mensaje,
            'Hola Ana Acepta, Taller Disparadores te regala {un lavado}.',
        )
        self.assertEqual(client.post(f'{url}lanzar/').status_code, 400)
        self.assertEqual(client.patch(url, {'nombre': 'Otro'}, format='json').status_code, 400)
        self.assertEqual(client.delete(url).status_code, 400)

        # Se envía uno y se cancela el resto.
        cola.procesar_pendientes(limite=1)
        respuesta = client.post(f'{url}cancelar/')
        self.assertEqual(respuesta.data['canceladas'], 1)
        self.assertEqual(respuesta.data['enviadas'], 1)
        self.assertEqual(respuesta.data['estado'], 'CANCELADA')

    def test_borrador_se_elimina_y_otra_empresa_no_la_ve(self):
        client = self._api()
        campana = self._crear(client)
        ajena = Campana.objects.create(empresa=self.otra_empresa, nombre='Ajena', mensaje='x')

        self.assertEqual(client.get(f'{URL_CAMPANAS}{ajena.uuid}/').status_code, 404)
        self.assertEqual(client.post(f'{URL_CAMPANAS}{ajena.uuid}/lanzar/').status_code, 404)
        self.assertEqual(client.delete(f'{URL_CAMPANAS}{campana["id"]}/').status_code, 204)

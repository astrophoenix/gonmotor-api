"""Tests de agenda de citas: horario de atención y capacidad del taller.

Cubre las tres restricciones que evitan agendar vehículos sin cupo físico:
horario de atención configurable por sucursal, cupo máximo diario y cupo de
atenciones simultáneas (incluyendo vehículos ya ingresados al taller), más el
endpoint `GET /api/citas/disponibilidad/`.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.citas.models import Cita
from apps.clientes.models import Cliente
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import RecepcionVehiculo
from apps.vehiculos.models import Vehiculo

User = get_user_model()

LISTADO = '/api/citas/'
DISPONIBILIDAD = '/api/citas/disponibilidad/'


def _ruc_unico():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class AgendaCitasBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='agenda_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Agenda',
            ruc=_ruc_unico(),
            email_contacto='agenda@test.com',
        )
        cls.empresa_ajena = Empresa.objects.create(
            nombre_comercial='Taller Ajeno',
            ruc=_ruc_unico(),
            email_contacto='ajeno@test.com',
        )
        cls.taller = Taller.objects.create(
            empresa=cls.empresa,
            nombre='Central',
            codigo_sucursal='001',
            direccion='Av. Siempre Viva 742',
            hora_apertura='08:00',
            hora_cierre='17:00',
        )
        cls.taller_ajeno = Taller.objects.create(
            empresa=cls.empresa_ajena,
            nombre='Ajeno',
            codigo_sucursal='009',
            direccion='Otra ciudad',
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            tipo_identificacion='C',
            identificacion='1712345678',
            nombre='Cliente Agenda',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='AGD001', marca='Kia', modelo='Sportage')
        cls.vehiculo.empresas.add(cls.empresa)
        cls.manana = timezone.localdate() + timedelta(days=1)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def payload(self, **kwargs):
        datos = {
            'taller': self.taller.pk,
            'cliente': self.cliente.pk,
            'vehiculo': self.vehiculo.pk,
            'fecha_cita': self.manana.isoformat(),
            'hora_cita': '09:00',
            'duracion_minutos': 60,
            'motivo': 'MANTENIMIENTO',
        }
        datos.update(kwargs)
        return datos

    def crear(self, **kwargs):
        return self.client.post(LISTADO, self.payload(**kwargs), format='json')


class HorarioAtencionTest(AgendaCitasBase):
    def test_crea_cita_dentro_del_horario(self):
        resp = self.crear()
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['hora_fin'], '10:00')

    def test_rechaza_hora_anterior_a_la_apertura(self):
        resp = self.crear(hora_cita='07:00')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('abre a las 08:00', resp.json()['hora_cita'][0])

    def test_rechaza_cita_que_termina_despues_del_cierre(self):
        resp = self.crear(hora_cita='16:30', duracion_minutos=60)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('cierra a las 17:00', resp.json()['hora_cita'][0])

    def test_acepta_la_ultima_cita_que_ajusta_al_cierre(self):
        resp = self.crear(hora_cita='16:00', duracion_minutos=60)
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_no_valida_horario_si_la_empresa_no_tiene_taller(self):
        sin_taller = Empresa.objects.create(
            nombre_comercial='Sin Sucursales',
            ruc=_ruc_unico(),
            email_contacto='sinsucursal@test.com',
        )
        cliente = Cliente.objects.create(
            empresa=sin_taller,
            tipo_identificacion='C',
            identificacion='1799999999',
            nombre='Cliente Sin Sucursal',
        )
        vehiculo = Vehiculo.objects.create(placa='NSU001', marca='Chevy', modelo='Aveo')
        vehiculo.empresas.add(sin_taller)

        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(sin_taller.pk)
        resp = self.crear(
            taller=None,
            hora_cita='03:00',
            cliente=cliente.pk,
            vehiculo=vehiculo.pk,
        )
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_rechaza_duracion_minima_invalida(self):
        resp = self.crear(duracion_minutos=5)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('duracion_minutos', resp.json())


class CupoDiarioTest(AgendaCitasBase):
    def test_bloquea_al_superar_la_capacidad_diaria(self):
        self.taller.capacidad_citas_dia = 2
        self.taller.save()

        self.assertEqual(self.crear(hora_cita='09:00').status_code, 201)
        self.assertEqual(self.crear(hora_cita='11:00').status_code, 201)

        resp = self.crear(hora_cita='15:00')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('2 de 2 citas', resp.json()['fecha_cita'][0])

    def test_las_citas_canceladas_no_consumen_cupo(self):
        self.taller.capacidad_citas_dia = 1
        self.taller.save()

        creada = self.crear(hora_cita='09:00')
        self.assertEqual(creada.status_code, 201)
        self.client.patch(
            f'{LISTADO}{creada.json()["id"]}/',
            {'estado': 'CANCELADA'},
            format='json',
        )

        resp = self.crear(hora_cita='12:00')
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_editar_una_cita_no_consume_un_cupo_adicional(self):
        self.taller.capacidad_citas_dia = 1
        self.taller.save()

        creada = self.crear(hora_cita='09:00')
        self.assertEqual(creada.status_code, 201)

        resp = self.client.patch(
            f'{LISTADO}{creada.json()["id"]}/',
            {'hora_cita': '14:00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_las_citas_de_otro_taller_no_ocupan_el_cupo(self):
        self.taller.capacidad_citas_dia = 1
        self.taller.save()
        otro_taller = Taller.objects.create(
            empresa=self.empresa,
            nombre='Sucursal Norte',
            codigo_sucursal='002',
            direccion='Norte 123',
        )

        self.assertEqual(self.crear(hora_cita='09:00').status_code, 201)
        resp = self.crear(hora_cita='15:00', taller=otro_taller.pk)
        self.assertEqual(resp.status_code, 201, resp.content)


class CapacidadSimultaneaTest(AgendaCitasBase):
    def test_bloquea_citas_que_se_solapan(self):
        self.taller.capacidad_simultanea = 1
        self.taller.save()

        self.assertEqual(self.crear(hora_cita='09:00', duracion_minutos=60).status_code, 201)

        solapada = self.crear(hora_cita='09:30', duracion_minutos=60)
        self.assertEqual(solapada.status_code, 400)
        self.assertIn('máximo permitido: 1', solapada.json()['hora_cita'][0])

        contigua = self.crear(hora_cita='10:00', duracion_minutos=60)
        self.assertEqual(contigua.status_code, 201, contigua.content)

    def test_no_permite_superar_el_pico_de_ocupacion(self):
        self.taller.capacidad_simultanea = 2
        self.taller.save()

        self.assertEqual(self.crear(hora_cita='09:00', duracion_minutos=120).status_code, 201)
        self.assertEqual(self.crear(hora_cita='10:00', duracion_minutos=60).status_code, 201)

        # A las 10:00 ya hay 2 vehículos: la tercera no cabe en ningún punto.
        resp = self.crear(hora_cita='10:30', duracion_minutos=60)
        self.assertEqual(resp.status_code, 400)

    def test_el_vehiculo_ingresado_ocupa_cupo_fisico(self):
        self.taller.capacidad_simultanea = 1
        self.taller.save()
        RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-AGD-0001',
            tipo_recepcion='REPARACION',
            estado='PENDIENTE',
            fecha_ingreso=timezone.now() - timedelta(days=2),
        )

        resp = self.crear(hora_cita='09:00')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('atendiéndose', resp.json()['hora_cita'][0])

    def test_ambas_capacidades_se_acumulan(self):
        self.taller.capacidad_citas_dia = 5
        self.taller.capacidad_simultanea = 1
        self.taller.save()

        self.assertEqual(self.crear(hora_cita='09:00').status_code, 201)
        resp = self.crear(hora_cita='09:30')
        self.assertEqual(resp.status_code, 400)

    def test_las_citas_de_otro_taller_no_ocupan_la_bahia(self):
        self.taller.capacidad_simultanea = 1
        self.taller.save()
        otro_taller = Taller.objects.create(
            empresa=self.empresa,
            nombre='Sucursal Norte',
            codigo_sucursal='002',
            direccion='Norte 123',
        )

        self.assertEqual(self.crear(hora_cita='09:00').status_code, 201)
        resp = self.crear(hora_cita='09:30', taller=otro_taller.pk)
        self.assertEqual(resp.status_code, 201, resp.content)


class AislamientoPorEmpresaTest(AgendaCitasBase):
    def test_no_admite_el_taller_de_otra_empresa(self):
        resp = self.crear(taller=self.taller_ajeno.pk)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no pertenece a tu empresa', resp.json()['taller'][0])

    def test_disponibilidad_no_expone_el_taller_de_otra_empresa(self):
        resp = self.client.get(f'{DISPONIBILIDAD}?taller={self.taller_ajeno.pk}')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no pertenece a tu empresa', resp.json()['taller'][0])

    def test_ignora_las_citas_de_otra_empresa(self):
        Cita.objects.create(
            empresa=self.empresa_ajena,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            fecha_cita=self.manana,
            hora_cita='09:00',
            estado='PROGRAMADA',
        )
        data = self.client.get(f'{DISPONIBILIDAD}?fecha={self.manana.isoformat()}').json()
        self.assertEqual(data['citas_ocupadas'], 0)


class DisponibilidadEndpointTest(AgendaCitasBase):
    def test_reporta_ocupacion_del_dia(self):
        self.taller.capacidad_citas_dia = 3
        self.taller.capacidad_simultanea = 2
        self.taller.save()
        self.assertEqual(self.crear(hora_cita='09:00').status_code, 201)
        self.assertEqual(self.crear(hora_cita='09:30').status_code, 201)

        data = self.client.get(f'{DISPONIBILIDAD}?fecha={self.manana.isoformat()}').json()

        self.assertTrue(data['aplicable'])
        self.assertEqual(data['taller']['id'], self.taller.pk)
        self.assertEqual(data['horario'], {'apertura': '08:00', 'cierre': '17:00'})
        self.assertEqual(data['capacidad_dia'], 3)
        self.assertEqual(data['citas_ocupadas'], 2)
        self.assertEqual(data['citas_disponibles'], 1)
        self.assertEqual(data['capacidad_simultanea'], 2)
        self.assertEqual(data['concurrentes_max'], 2)
        self.assertEqual(len(data['intervalos']), 2)

    def test_evalua_una_concrecion_puntual(self):
        self.taller.capacidad_simultanea = 1
        self.taller.save()
        self.crear(hora_cita='09:00', duracion_minutos=60)

        params = f'?fecha={self.manana.isoformat()}&hora=09:30&duracion=60'
        solicitud = self.client.get(f'{DISPONIBILIDAD}{params}').json()['solicitud']
        self.assertFalse(solicitud['disponible'])
        self.assertEqual(solicitud['campo'], 'hora_cita')
        self.assertIn('máximo permitido', solicitud['motivo'])

        params = f'?fecha={self.manana.isoformat()}&hora=11:00&duracion=60'
        self.assertTrue(self.client.get(f'{DISPONIBILIDAD}{params}').json()['solicitud']['disponible'])

    def test_excluye_la_cita_que_se_esta_editando(self):
        self.taller.capacidad_citas_dia = 1
        self.taller.save()
        creada = self.crear(hora_cita='09:00').json()

        params = f'?fecha={self.manana.isoformat()}&cita={creada["id"]}&hora=14:00'
        data = self.client.get(f'{DISPONIBILIDAD}{params}').json()
        self.assertEqual(data['citas_ocupadas'], 0)
        self.assertTrue(data['solicitud']['disponible'])

    def test_marca_como_no_aplicable_sin_taller_configurado(self):
        sin_taller = Empresa.objects.create(
            nombre_comercial='Otra Sin Sucursales',
            ruc=_ruc_unico(),
            email_contacto='otra@test.com',
        )
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(sin_taller.pk)

        data = self.client.get(DISPONIBILIDAD).json()
        self.assertFalse(data['aplicable'])
        self.assertIsNone(data['taller'])
        self.assertIsNone(data['horario'])

    def test_rechaza_fecha_con_formato_invalido(self):
        resp = self.client.get(f'{DISPONIBILIDAD}?fecha=31-12-2026')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('fecha', resp.json())

    def test_rechaza_hora_con_formato_invalido(self):
        resp = self.client.get(f'{DISPONIBILIDAD}?hora=25:99')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('hora', resp.json())


class PaginacionCalendarioTest(AgendaCitasBase):
    """El calendario necesita traer un rango de fechas completo de una sola vez."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for indice in range(12):
            Cita.objects.create(
                empresa=cls.empresa,
                cliente=cls.cliente,
                vehiculo=cls.vehiculo,
                taller=cls.taller,
                fecha_cita=cls.manana,
                hora_cita=f'{8 + indice // 2:02d}:{30 * (indice % 2):02d}',
                duracion_minutos=60,
                motivo='MANTENIMIENTO',
            )

    def test_por_defecto_sigue_paginando_a_10(self):
        data = self.client.get(LISTADO).json()
        self.assertEqual(len(data['results']), 10)
        self.assertEqual(data['count'], 12)
        self.assertIsNotNone(data['next'])

    def test_page_size_permite_traer_todo_el_rango(self):
        data = self.client.get(f'{LISTADO}?page_size=50').json()
        self.assertEqual(len(data['results']), 12)
        self.assertEqual(data['count'], 12)
        self.assertIsNone(data['next'])
        self.assertIsNone(data['previous'])

    def test_page_size_respeta_el_tope(self):
        data = self.client.get(f'{LISTADO}?page_size=5000').json()
        self.assertEqual(len(data['results']), 12)

    def test_rango_de_fechas_del_calendario(self):
        otra_fecha = self.manana + timedelta(days=30)
        Cita.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            taller=self.taller,
            fecha_cita=otra_fecha,
            hora_cita='10:00',
            duracion_minutos=60,
            motivo='DIAGNOSTICO',
        )

        params = f'?desde={self.manana.isoformat()}&hasta={self.manana.isoformat()}&page_size=500'
        data = self.client.get(f'{LISTADO}{params}').json()
        self.assertEqual(data['count'], 12)
        self.assertTrue(all(item['fecha_cita'] == self.manana.isoformat() for item in data['results']))


class CompartirIcsTest(AgendaCitasBase):
    """Exportación de la cita al calendario del cliente (.ics y enlace público)."""

    def descargar_autenticada(self, cita_id):
        return self.client.get(f'{LISTADO}{cita_id}/ics/')

    def test_el_listado_expone_el_enlace_ics(self):
        creada = self.crear().json()
        enlace = creada['enlace_ics']
        self.assertTrue(enlace.startswith('http'))
        self.assertIn('/api/citas/compartir/', enlace)

    def test_descarga_autentica_devuelve_archivo_ics(self):
        creada = self.crear().json()
        resp = self.descargar_autenticada(creada['id'])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn('text/calendar', resp['Content-Type'])
        self.assertIn('.ics', resp['Content-Disposition'])
        self.assertIn(b'BEGIN:VCALENDAR', resp.content)
        self.assertIn(b'\r\nBEGIN:VEVENT', resp.content)

    def test_enlace_publico_descarga_sin_sesion(self):
        creada = self.crear().json()
        anonimo = APIClient()
        resp = anonimo.get(creada['enlace_ics'])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn('text/calendar', resp['Content-Type'])
        self.assertEqual(
            resp.content,
            self.descargar_autenticada(creada['id']).content,
        )

    def test_enlace_firmado_invalido_responde_404(self):
        resp = APIClient().get(f'{LISTADO}compartir/esto-no-es-un-token/')
        self.assertEqual(resp.status_code, 404)

    def test_enlace_no_entrega_citas_inactivas(self):
        creada = self.crear().json()
        Cita.objects.filter(pk=creada['id']).update(is_active=False)

        resp = APIClient().get(creada['enlace_ics'])
        self.assertEqual(resp.status_code, 404)

    def test_no_permite_descargar_citas_de_otra_empresa(self):
        ajena = Cita.objects.create(
            empresa=self.empresa_ajena,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            taller=self.taller_ajeno,
            fecha_cita=self.manana,
            hora_cita='09:00',
            duracion_minutos=60,
            motivo='MANTENIMIENTO',
        )
        resp = self.descargar_autenticada(ajena.pk)
        self.assertEqual(resp.status_code, 404)

    def test_los_datos_de_la_cita_aparecen_en_el_archivo(self):
        creada = self.crear().json()
        contenido = self.descargar_autenticada(creada['id']).content
        compacta = self.manana.isoformat().replace('-', '')

        self.assertIn(f'DTSTART:{compacta}T090000'.encode(), contenido)
        self.assertIn(f'DTEND:{compacta}T100000'.encode(), contenido)
        self.assertIn(b'SUMMARY:Cita en Central - AGD001', contenido)
        self.assertIn(b'LOCATION:Av. Siempre Viva 742\\, Central', contenido)
        self.assertIn(b'STATUS:CONFIRMED', contenido)
        self.assertIn(b'BEGIN:VALARM', contenido)

    def test_cita_cancelada_se_marca_como_cancelada(self):
        creada = self.crear(estado='CANCELADA').json()
        contenido = self.descargar_autenticada(creada['id']).content

        self.assertIn(b'STATUS:CANCELLED', contenido)
        self.assertNotIn(b'BEGIN:VALARM', contenido)

    def test_escapa_los_textos_libres(self):
        creada = self.crear(motivo_descripcion='Ruido; fuerte, al frenar\nsiempre').json()
        contenido = self.descargar_autenticada(creada['id']).content
        # Se deshace el plegado de líneas antes de comprobar el escapado.
        contenido = contenido.replace(b'\r\n ', b'')

        self.assertIn(b'Ruido\\; fuerte\\, al frenar\\nsiempre', contenido)

    def test_lineas_respetan_el_pliego_del_rfc(self):
        self.crear(motivo_descripcion='A' * 400)
        contenido = self.descargar_autenticada(
            Cita.objects.get(vehiculo=self.vehiculo).pk
        ).content

        for linea in contenido.split(b'\r\n'):
            self.assertLessEqual(len(linea), 75, linea)

    def test_el_enlace_usa_https_detras_de_un_proxy(self):
        self.crear()
        resp = self.client.get(LISTADO, HTTP_X_FORWARDED_PROTO='https')
        enlace = resp.json()['results'][0]['enlace_ics']
        self.assertTrue(enlace.startswith('https://'), enlace)


class SoloFuturoTest(AgendaCitasBase):
    """Solo se pueden crear o reprogramar citas en el futuro."""

    def _ayer(self):
        return (timezone.localdate() - timedelta(days=1)).isoformat()

    def test_rechaza_crear_una_cita_en_el_pasado(self):
        resp = self.crear(fecha_cita=self._ayer())
        self.assertEqual(resp.status_code, 400)
        self.assertIn('pasado', resp.json()['fecha_hora_programada'][0])

    def test_rechaza_mover_una_cita_existente_al_pasado(self):
        creada = self.crear().json()
        resp = self.client.patch(
            f'{LISTADO}{creada["id"]}/',
            {'fecha_cita': self._ayer()},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('pasado', resp.json()['fecha_hora_programada'][0])

    def test_permite_editar_una_cita_que_ya_paso(self):
        pasada = Cita.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            taller=self.taller,
            fecha_cita=timezone.localdate() - timedelta(days=2),
            hora_cita='09:00',
            duracion_minutos=60,
            motivo='MANTENIMIENTO',
        )

        resp = self.client.patch(
            f'{LISTADO}{pasada.pk}/',
            {'estado': 'NO_ASISTIO', 'notas_internas': 'No se presentó.'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        pasada.refresh_from_db()
        self.assertEqual(pasada.estado, 'NO_ASISTIO')

    def test_no_valida_el_pasado_si_el_horario_no_cambia(self):
        pasada = Cita.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            taller=self.taller,
            fecha_cita=timezone.localdate() - timedelta(days=2),
            hora_cita='09:00',
            duracion_minutos=60,
            motivo='MANTENIMIENTO',
        )

        resp = self.client.patch(
            f'{LISTADO}{pasada.pk}/',
            {'fecha_cita': pasada.fecha_cita.isoformat(), 'hora_cita': '09:00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)


class HoraLocalTest(AgendaCitasBase):
    """`hora_cita` es hora local del taller (America/Guayaquil), no UTC.

    Regresión: con TIME_ZONE='UTC' una cita para las 09:15 de hoy se comparaba
    contra las 09:15 UTC (04:15 en Guayaquil) y se rechazaba como pasada
    aunque a esa hora aún faltaran horas.
    """

    # 13:49 UTC = 08:49 en Guayaquil.
    AHORA = datetime(2026, 9, 30, 13, 49, tzinfo=dt_timezone.utc)

    def test_acepta_una_cita_futura_en_hora_local(self):
        with mock.patch('django.utils.timezone.now', return_value=self.AHORA):
            resp = self.crear(
                fecha_cita='2026-09-30',
                hora_cita='09:15',
                duracion_minutos=15,
            )

        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['hora_fin'], '09:30')
        self.assertEqual(resp.json()['fecha_cita'], '2026-09-30')
        self.assertEqual(resp.json()['hora_cita'][:5], '09:15')

    def test_rechaza_una_cita_ya_transcurrida_en_hora_local(self):
        with mock.patch('django.utils.timezone.now', return_value=self.AHORA):
            resp = self.crear(fecha_cita='2026-09-30', hora_cita='08:00')

        self.assertEqual(resp.status_code, 400)
        self.assertIn('pasado', resp.json()['fecha_hora_programada'][0])


class CitaConversionVinculaCotizacionTests(TestCase):
    """La cita heredada de una cotización vigente arrastra el vínculo al
    convertirla en recepción, para que el flujo de WhatsApp no se corte."""

    @classmethod
    def setUpTestData(cls):
        from apps.clientes.models import Cliente
        from apps.cotizaciones.models import Cotizacion
        from apps.empresas.models import Empresa, Taller
        from apps.vehiculos.models import Vehiculo

        User = get_user_model()
        cls.user = User.objects.create_superuser(username='cita_cot_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Cita Cot', ruc=_ruc_unico(), email_contacto='cc@test.com'
        )
        cls.taller = Taller.objects.create(
            empresa=cls.empresa, nombre='Central', direccion='Av. Test 100'
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            tipo_identificacion='C',
            identificacion='1787654321',
            nombre='Cliente Cita Cot',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='CC-001', marca='Kia', modelo='Rio')
        cls.vehiculo.empresas.add(cls.empresa)
        cls.manana = timezone.localdate() + timedelta(days=1)

    def _cita(self):
        return Cita.objects.create(
            empresa=self.empresa,
            taller=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            fecha_cita=self.manana,
            hora_cita=datetime(2026, 1, 1, 9, 0).time(),
            duracion_minutos=60,
            motivo='MANTENIMIENTO',
        )

    def _cotizacion(self, estado='ACEPTADA', numero='COT-0100'):
        from apps.cotizaciones.models import Cotizacion

        return Cotizacion.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_cotizacion=numero,
            estado=estado,
        )

    def test_la_conversion_vincula_la_cotizacion_vigente(self):
        cotizacion = self._cotizacion()
        cita = self._cita()

        recepcion = cita.convertir_a_recepcion()

        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.recepcion_origen_id, recepcion.id)

    def test_no_se_vincula_una_cotizacion_vencida(self):
        cotizacion = self._cotizacion(estado='VENCIDA', numero='COT-0101')
        cita = self._cita()

        cita.convertir_a_recepcion()

        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.recepcion_origen_id)

    def test_no_pisa_un_vinculo_de_recepcion_ya_asignado(self):
        from apps.ordenes.models import RecepcionVehiculo

        cotizacion = self._cotizacion(numero='COT-0102')
        otra = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-0102',
        )
        cotizacion.recepcion_origen = otra
        cotizacion.save(update_fields=['recepcion_origen', 'updated_at'])

        cita = self._cita()
        recepcion = cita.convertir_a_recepcion()

        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.recepcion_origen_id, otra.id)
        self.assertNotEqual(otra.id, recepcion.id)


class CitaSearchSelectFiltersTests(AgendaCitasBase):
    def test_busca_por_numero_id_y_filtra_citas_sin_recepcion(self):
        creada = self.crear()
        self.assertEqual(creada.status_code, 201, creada.content)
        cita_id = creada.json()['id']

        por_numero = self.client.get(LISTADO, {'numero': cita_id})
        self.assertEqual(por_numero.status_code, 200, por_numero.content)
        self.assertEqual([item['id'] for item in por_numero.json()['results']], [cita_id])

        sin_recepcion = self.client.get(LISTADO, {'sin_recepcion': '1'})
        self.assertEqual(sin_recepcion.status_code, 200, sin_recepcion.content)
        self.assertIn(cita_id, [item['id'] for item in sin_recepcion.json()['results']])

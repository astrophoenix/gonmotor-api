"""ID público (UUIDv7) en la API: generación, lookup y FKs por UUID."""

import time
import uuid
from datetime import date, timedelta
from datetime import time as hora

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.citas.ics import SALT_ENLACE_CITA_ICS
from apps.citas.models import Cita
from apps.clientes.models import Cliente
from apps.core.ids_publicos import _uuid7_respaldo, nuevo_uuid
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo
from apps.vehiculos.models import Vehiculo, VehiculoPropietario, vincular_empresa


class GeneracionUuidTests(SimpleTestCase):

    def _verificar_v7(self, valor):
        self.assertEqual(valor.version, 7)
        self.assertEqual(valor.variant, uuid.RFC_4122)
        milisegundos = valor.int >> 80
        self.assertLess(abs(milisegundos - time.time_ns() // 1_000_000), 5_000)

    def test_nuevo_uuid_es_v7_con_el_instante_actual(self):
        self._verificar_v7(nuevo_uuid())

    def test_respaldo_sin_python_314_es_v7_y_no_repite(self):
        valores = [_uuid7_respaldo() for _ in range(500)]
        for valor in valores[:5]:
            self._verificar_v7(valor)
        self.assertEqual(len(set(valores)), len(valores))


class ApiIdPublicoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Pública', ruc='1790000000999')
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Ana', identificacion='0101010101')
        cls.vehiculo = Vehiculo.objects.create(placa='PUB0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        VehiculoPropietario.objects.create(vehiculo=cls.vehiculo, cliente=cls.cliente)
        cls.user = get_user_model().objects.create_superuser(username='publico', password='x')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def test_el_listado_expone_el_uuid_como_id(self):
        fila = self.client.get('/api/clientes/').json()['results'][0]
        self.assertEqual(fila['id'], str(self.cliente.uuid))

    def test_el_detalle_se_busca_por_uuid_y_no_por_entero(self):
        self.assertEqual(self.client.get(f'/api/clientes/{self.cliente.uuid}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/clientes/{self.cliente.pk}/').status_code, 404)
        self.assertEqual(self.client.get('/api/clientes/no-es-un-uuid/').status_code, 404)

    def test_eliminar_un_inexistente_responde_404(self):
        respuesta = self.client.delete(f'/api/clientes/{uuid.uuid4()}/')
        self.assertEqual(respuesta.status_code, 404)

    def test_fk_se_escribe_y_lee_por_uuid(self):
        respuesta = self.client.post('/api/ordenes/ordenes-trabajo/', {
            'cliente': str(self.cliente.uuid), 'vehiculo': str(self.vehiculo.uuid),
            'prioridad': 'MEDIA', 'tipo_trabajo': 'MANTENIMIENTO',
        }, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertEqual(respuesta.json()['cliente']['id'], str(self.cliente.uuid))

    def test_fk_rechaza_el_entero(self):
        respuesta = self.client.post('/api/ordenes/ordenes-trabajo/', {
            'cliente': self.cliente.pk, 'vehiculo': str(self.vehiculo.uuid),
            'prioridad': 'MEDIA', 'tipo_trabajo': 'MANTENIMIENTO',
        }, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('cliente', respuesta.json())

    def test_filtro_de_vehiculos_por_cliente_usa_uuid(self):
        placas = lambda valor: [v['placa'] for v in self.client.get('/api/vehiculos/', {'cliente': valor}).json()['results']]
        self.assertEqual(placas(str(self.cliente.uuid)), ['PUB0001'])
        self.assertEqual(placas(self.cliente.pk), [])
        self.assertEqual(placas('basura'), [])

    def test_vehiculo_expone_el_cliente_por_uuid(self):
        fila = self.client.get('/api/vehiculos/').json()['results'][0]
        self.assertEqual(fila['cliente_id'], str(self.cliente.uuid))

    def test_detalle_de_cada_recurso_por_uuid_y_no_por_entero(self):
        taller = Taller.objects.create(empresa=self.empresa, nombre='T', codigo_sucursal='001', direccion='x')
        comun = {'empresa': self.empresa, 'sucursal': taller, 'cliente': self.cliente, 'vehiculo': self.vehiculo}
        recursos = {
            '/api/vehiculos/': self.vehiculo,
            '/api/citas/': Cita.objects.create(
                empresa=self.empresa, taller=taller, cliente=self.cliente, vehiculo=self.vehiculo,
                fecha_cita=date.today() + timedelta(days=3), hora_cita=hora(10, 0),
            ),
            '/api/recepciones/': RecepcionVehiculo.objects.create(**comun, numero_recepcion='R-1'),
            '/api/ordenes/inspecciones/': InspeccionVehiculo.objects.create(**comun, numero_inspeccion='I-1'),
            '/api/cotizaciones/': Cotizacion.objects.create(**comun, numero_cotizacion='C-1'),
            '/api/ordenes/ordenes-trabajo/': OrdenTrabajo.objects.create(**comun, numero_orden='O-1'),
        }
        for url, objeto in recursos.items():
            with self.subTest(url=url):
                respuesta = self.client.get(f'{url}{objeto.uuid}/')
                self.assertEqual(respuesta.status_code, 200, respuesta.content)
                self.assertEqual(respuesta.json()['id'], str(objeto.uuid))
                self.assertEqual(self.client.get(f'{url}{objeto.pk}/').status_code, 404)

    def test_el_token_ics_lleva_el_uuid_y_no_la_pk(self):
        taller = Taller.objects.create(empresa=self.empresa, nombre='T', codigo_sucursal='001', direccion='x')
        cita = Cita.objects.create(
            empresa=self.empresa, taller=taller, cliente=self.cliente, vehiculo=self.vehiculo,
            fecha_cita=date.today() + timedelta(days=3), hora_cita=hora(10, 0),
        )
        enlace = self.client.get(f'/api/citas/{cita.uuid}/').json()['enlace_ics']
        token = enlace.rstrip('/').rsplit('/', 1)[-1]
        self.assertEqual(signing.loads(token, salt=SALT_ENLACE_CITA_ICS), str(cita.uuid))
        self.assertEqual(APIClient().get(enlace).status_code, 200)

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
from apps.authentication.models import Rol, UserProfile, UsuarioEmpresa
from apps.authentication.serializers import emitir_token_seleccion
from apps.gastos.models import Gasto
from apps.inventario.models import Repuesto, Servicio
from apps.proveedores.models import Proveedor
from apps.vehiculos.models import Vehiculo, VehiculoPropietario, vincular_empresa
from apps.core.testing import fijar_empresa


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
        fijar_empresa(self.client, str(self.empresa.uuid))
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


class ApiIdPublicoFase2Tests(TestCase):
    """Catálogos, gastos, talleres y usuarios por ID público."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.empresa = Empresa.objects.create(nombre_comercial='Fase 2', ruc='1790000000888')
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Centro', codigo_sucursal='001', direccion='x')
        cls.otro_taller = Taller.objects.create(empresa=cls.empresa, nombre='Norte', codigo_sucursal='002', direccion='x')
        cls.admin = User.objects.create_superuser(username='fase2', password='x')
        cls.asesor = User.objects.create_user(username='asesor-f2', password='x', first_name='Ana')
        cls.empleado = UsuarioEmpresa.objects.create(user=cls.asesor, empresa=cls.empresa, rol='ASESOR')
        cls.empleado.talleres.set([cls.taller])
        cls.gasto_centro = Gasto.objects.create(empresa=cls.empresa, taller=cls.taller, fecha=date.today(), monto=10)
        cls.gasto_norte = Gasto.objects.create(empresa=cls.empresa, taller=cls.otro_taller, fecha=date.today(), monto=20)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        fijar_empresa(self.client, str(self.empresa.uuid))
    def test_todo_usuario_nuevo_tiene_perfil_con_uuid(self):
        usuario = get_user_model().objects.create_user(username='nuevo-f2', password='x')
        self.assertIsNotNone(UserProfile.objects.get(user=usuario).uuid)

    def test_detalle_por_uuid_y_no_por_entero(self):
        recursos = {
            '/api/proveedores/': Proveedor.objects.create(empresa=self.empresa, nombre='Prov', identificacion='0101010101'),
            '/api/repuestos/': Repuesto.objects.create(empresa=self.empresa, codigo='R-1', nombre='Filtro'),
            '/api/servicios/': Servicio.objects.create(empresa=self.empresa, codigo='S-1', nombre='Alineación'),
            '/api/gastos/': self.gasto_centro,
            '/api/configuracion/sucursales/': self.taller,
            '/api/auth/empleados/': self.empleado,
        }
        for url, objeto in recursos.items():
            with self.subTest(url=url):
                respuesta = self.client.get(f'{url}{objeto.uuid}/')
                self.assertEqual(respuesta.status_code, 200, respuesta.content)
                self.assertEqual(respuesta.json()['id'], str(objeto.uuid))
                self.assertEqual(self.client.get(f'{url}{objeto.pk}/').status_code, 404)

    def test_cabecera_de_taller_por_uuid(self):
        def ids(cabecera):
            respuesta = self.client.get('/api/gastos/', HTTP_X_TALLER_ID=cabecera)
            return {fila['id'] for fila in respuesta.json()['results']}

        self.assertEqual(ids(str(self.taller.uuid)), {str(self.gasto_centro.uuid)})
        # El id interno ya no identifica un taller: no amplía ni elige alcance.
        self.assertEqual(ids(str(self.otro_taller.pk)), set())

    def test_usuario_expuesto_por_el_uuid_de_su_perfil(self):
        perfil_uuid = str(self.asesor.profile.uuid)
        empleado = self.client.get(f'/api/auth/empleados/{self.empleado.uuid}/').json()
        self.assertEqual(empleado['user']['id'], perfil_uuid)

        cliente = Cliente.objects.create(empresa=self.empresa, nombre='Luis', identificacion='0202020202')
        respuesta = self.client.post('/api/cotizaciones/', {
            'cliente': str(cliente.uuid), 'asesor': perfil_uuid, 'validez_dias': 15,
        }, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertEqual(respuesta.json()['asesor'], perfil_uuid)

        con_entero = self.client.post('/api/cotizaciones/', {
            'cliente': str(cliente.uuid), 'asesor': self.asesor.pk, 'validez_dias': 15,
        }, format='json')
        self.assertEqual(con_entero.status_code, 400)
        self.assertIn('asesor', con_entero.json())

    def test_me_expone_el_uuid_del_usuario(self):
        self.client.force_authenticate(user=self.asesor)
        respuesta = self.client.get('/api/auth/me/')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()['id'], str(self.asesor.profile.uuid))


class ApiIdPublicoFase3Tests(TestCase):
    """Empresas y roles por ID público; la empresa de la sesión, por UUID."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.empresa_a = Empresa.objects.create(nombre_comercial='Fase 3 A', ruc='1790000000777')
        cls.empresa_b = Empresa.objects.create(nombre_comercial='Fase 3 B', ruc='1790000000666')
        cls.saas = User.objects.create_superuser(username='saas-f3', password='x')
        cls.usuario = User.objects.create_user(username='multi-f3', password='x')
        for empresa in (cls.empresa_a, cls.empresa_b):
            UsuarioEmpresa.objects.create(user=cls.usuario, empresa=empresa, rol='ADMIN_EMPRESA')

    def _cliente(self, user, empresa=None):
        client = APIClient()
        client.force_authenticate(user=user)
        if empresa is not None:
            fijar_empresa(client, str(empresa))
        return client

    def test_empresa_y_rol_por_uuid_y_no_por_entero(self):
        saas = self._cliente(self.saas, self.empresa_a.uuid)
        self.assertEqual(saas.get(f'/api/empresas/{self.empresa_a.uuid}/').status_code, 200)
        self.assertEqual(saas.get(f'/api/empresas/{self.empresa_a.pk}/').status_code, 404)

        rol = Rol.objects.filter(es_sistema=True).first()
        self.assertEqual(saas.get(f'/api/auth/roles/{rol.uuid}/').status_code, 200)
        self.assertEqual(saas.get(f'/api/auth/roles/{rol.pk}/').status_code, 404)

    def test_la_empresa_sale_solo_del_token(self):
        # Token atado a la empresa A: una cabecera X-Empresa-ID con la B se ignora.
        client = self._cliente(self.usuario, self.empresa_a.uuid)
        respuesta = client.get('/api/auth/me/', HTTP_X_EMPRESA_ID=str(self.empresa_b.uuid))
        self.assertEqual(respuesta.json()['empresa_id'], str(self.empresa_a.uuid))

        # Sin claim de empresa, la cabecera tampoco da contexto (usuario con dos empresas).
        sin_claim = self._cliente(self.usuario)
        respuesta = sin_claim.get('/api/auth/me/', HTTP_X_EMPRESA_ID=str(self.empresa_b.uuid))
        self.assertIsNone(respuesta.json()['empresa_id'])

    def test_la_claim_de_tokens_previos_con_entero_sigue_valiendo(self):
        # Tokens emitidos antes del ID público llevan la pk (firmada por el servidor).
        client = APIClient()
        client.force_authenticate(user=self.usuario, token={'empresa_id': self.empresa_b.pk})
        self.assertEqual(client.get('/api/auth/me/').json()['empresa_id'], str(self.empresa_b.uuid))

    def test_quitar_el_acceso_tiene_efecto_aunque_el_token_siga_vigente(self):
        client = self._cliente(self.usuario, self.empresa_b.uuid)
        UsuarioEmpresa.objects.filter(user=self.usuario, empresa=self.empresa_b).update(tiene_acceso=False)
        self.assertIsNone(client.get('/api/auth/me/').json()['empresa_id'])

    def test_seleccion_de_empresa_por_uuid_y_jwt_sin_pk(self):
        client = APIClient()
        token = emitir_token_seleccion(self.usuario)
        con_entero = client.post('/api/auth/select-company/', {
            'selection_token': token, 'empresa_id': self.empresa_b.pk,
        }, format='json')
        self.assertEqual(con_entero.status_code, 400)

        respuesta = client.post('/api/auth/select-company/', {
            'selection_token': token, 'empresa_id': str(self.empresa_b.uuid),
        }, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()['user']['empresa_id'], str(self.empresa_b.uuid))
        from rest_framework_simplejwt.tokens import AccessToken

        self.assertEqual(AccessToken(respuesta.json()['access'])['empresa_id'], str(self.empresa_b.uuid))

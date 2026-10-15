"""Tests del borrado lógico híbrido (`is_active` + `deleted_at`) multi-tenant.

Cubre los estados operativo/suspendido/eliminado, las uniques parciales, el
recorte por tenant de `para_request`, el borrado por empresa de los vehículos
compartidos, la numeración de documentos y las fugas de escritura entre
empresas en los documentos del taller.
"""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.empresas.models import Empresa, Taller
from apps.empresas.services import generar_codigo_secuencial
from apps.ordenes.models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo, VehiculoEmpresa

User = get_user_model()

OT_URL = '/api/ordenes/ordenes-trabajo/'


def _ruc_unico():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


def _empresa(nombre):
    return Empresa.objects.create(nombre_comercial=nombre, ruc=_ruc_unico(), email_contacto='e@test.com')


def _cliente(empresa, identificacion, **extra):
    return Cliente.objects.create(
        empresa=empresa, tipo_identificacion='C', identificacion=identificacion,
        nombre=f'Cliente {identificacion}', **extra,
    )


class SoftDeleteModeloTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = _empresa('Empresa SD')

    def test_delete_de_instancia_es_logico(self):
        cliente = _cliente(self.empresa, '1700000001')

        cliente.delete()

        self.assertFalse(Cliente.objects.filter(pk=cliente.pk).exists())
        eliminado = Cliente.all_objects.get(pk=cliente.pk)
        self.assertFalse(eliminado.is_active)
        self.assertIsNotNone(eliminado.deleted_at)
        self.assertTrue(eliminado.esta_eliminado)

    def test_suspendido_sigue_visible_en_objects(self):
        cliente = _cliente(self.empresa, '1700000002', is_active=False)

        self.assertTrue(Cliente.objects.filter(pk=cliente.pk).exists())
        self.assertTrue(Cliente.objects.suspendidos().filter(pk=cliente.pk).exists())
        self.assertFalse(Cliente.objects.operativos().filter(pk=cliente.pk).exists())

    def test_queryset_delete_es_logico_y_hard_delete_es_fisico(self):
        a = _cliente(self.empresa, '1700000003')
        b = _cliente(self.empresa, '1700000004')

        total, _ = Cliente.objects.filter(pk__in=[a.pk, b.pk]).delete()
        self.assertEqual(total, 2)
        self.assertEqual(Cliente.all_objects.eliminados().filter(pk__in=[a.pk, b.pk]).count(), 2)

        Cliente.all_objects.filter(pk=a.pk).hard_delete()
        self.assertFalse(Cliente.all_objects.filter(pk=a.pk).exists())

    def test_restaurar(self):
        cliente = _cliente(self.empresa, '1700000005')
        cliente.delete()

        cliente.restaurar()

        cliente.refresh_from_db()
        self.assertTrue(cliente.esta_operativo)

    def test_unique_parcial_permite_recrear_eliminado(self):
        _cliente(self.empresa, '1700000006').delete()

        nuevo = _cliente(self.empresa, '1700000006')

        self.assertTrue(nuevo.esta_operativo)

    def test_unique_parcial_bloquea_duplicar_suspendido(self):
        _cliente(self.empresa, '1700000007', is_active=False)

        with self.assertRaises(IntegrityError), transaction.atomic():
            _cliente(self.empresa, '1700000007')

    def test_fk_hacia_adelante_resuelve_padre_eliminado(self):
        taller = Taller.objects.create(empresa=self.empresa, nombre='T', codigo_sucursal='001', direccion='x')
        cliente = _cliente(self.empresa, '1700000008')
        vehiculo = Vehiculo.objects.create(placa='SDF0001', marca='Kia', modelo='Rio')
        orden = OrdenTrabajo.objects.create(
            empresa=self.empresa, sucursal=taller, cliente=cliente, vehiculo=vehiculo, numero_orden='OT-1'
        )

        cliente.delete()

        orden.refresh_from_db()
        self.assertEqual(orden.cliente.pk, cliente.pk)


class NumeracionDocumentosTest(TestCase):
    def test_no_reutiliza_numero_de_ot_eliminada(self):
        empresa = _empresa('Empresa Numeración')
        taller = Taller.objects.create(
            empresa=empresa, nombre='T', codigo_sucursal='001', direccion='x', prefijo_ot='OT-'
        )
        cliente = _cliente(empresa, '1700000010')
        vehiculo = Vehiculo.objects.create(placa='NUM0001', marca='Kia', modelo='Rio')
        primera = OrdenTrabajo.objects.create(
            empresa=empresa, sucursal=taller, cliente=cliente, vehiculo=vehiculo,
            numero_orden=generar_codigo_secuencial(taller, 'ot'),
        )
        primera.delete()
        Taller.objects.filter(pk=taller.pk).update(siguiente_ot=1)
        taller.refresh_from_db()

        siguiente = generar_codigo_secuencial(taller, 'ot')

        self.assertNotEqual(siguiente, primera.numero_orden)


class ParaRequestTest(TestCase):
    """`TenantSoftDeleteQuerySet.para_request` delega en authentication.utils."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = _empresa('Empresa Tenant')
        cls.otra = _empresa('Empresa Otra')
        cls.taller_a = Taller.objects.create(empresa=cls.empresa, nombre='A', codigo_sucursal='001', direccion='x')
        cls.taller_b = Taller.objects.create(empresa=cls.empresa, nombre='B', codigo_sucursal='002', direccion='x')
        cls.taller_otra = Taller.objects.create(empresa=cls.otra, nombre='X', codigo_sucursal='001', direccion='x')
        cliente = _cliente(cls.empresa, '1700000020')
        cliente_otra = _cliente(cls.otra, '1700000021')
        vehiculo = Vehiculo.objects.create(placa='TEN0001', marca='Kia', modelo='Rio')
        cls.ot_a = OrdenTrabajo.objects.create(
            empresa=cls.empresa, sucursal=cls.taller_a, cliente=cliente, vehiculo=vehiculo, numero_orden='A-1'
        )
        cls.ot_b = OrdenTrabajo.objects.create(
            empresa=cls.empresa, sucursal=cls.taller_b, cliente=cliente, vehiculo=vehiculo, numero_orden='B-1'
        )
        cls.ot_otra = OrdenTrabajo.objects.create(
            empresa=cls.otra, sucursal=cls.taller_otra, cliente=cliente_otra, vehiculo=vehiculo, numero_orden='X-1'
        )
        cls.asesor = User.objects.create_user(username='sd_asesor', password='x')
        relacion = UsuarioEmpresa.objects.create(user=cls.asesor, empresa=cls.empresa, rol='ASESOR')
        relacion.talleres.set([cls.taller_a])

    def _request(self, user, empresa_id=None, taller_id=None):
        headers = {}
        if empresa_id:
            headers['HTTP_X_EMPRESA_ID'] = str(empresa_id)
        if taller_id:
            headers['HTTP_X_TALLER_ID'] = str(taller_id)
        request = RequestFactory().get('/', **headers)
        request.user = user
        return request

    def test_sin_contexto_no_devuelve_nada(self):
        sin_empresa = User.objects.create_user(username='sd_sin_empresa', password='x')

        qs = OrdenTrabajo.objects.para_request(self._request(sin_empresa))

        self.assertFalse(qs.exists())

    def test_rol_no_admin_solo_ve_sus_talleres(self):
        qs = OrdenTrabajo.objects.para_request(self._request(self.asesor, self.empresa.pk))

        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.ot_a.pk})

    def test_taller_ajeno_en_cabecera_no_devuelve_nada(self):
        for taller in (self.taller_b, self.taller_otra):
            qs = OrdenTrabajo.objects.para_request(
                self._request(self.asesor, self.empresa.pk, taller.pk)
            )
            self.assertFalse(qs.exists(), taller)

    def test_empresa_ajena_en_cabecera_no_devuelve_nada(self):
        qs = OrdenTrabajo.objects.para_request(self._request(self.asesor, self.otra.pk))

        self.assertFalse(qs.exists())

    def test_excluye_eliminados(self):
        self.ot_a.delete()

        qs = OrdenTrabajo.objects.para_request(self._request(self.asesor, self.empresa.pk))

        self.assertFalse(qs.exists())


class ApiSoftDeleteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='sd_admin', password='x')
        cls.empresa = _empresa('Empresa API')
        cls.otra = _empresa('Empresa API Otra')
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='T', codigo_sucursal='001', direccion='x')
        cls.taller_otra = Taller.objects.create(empresa=cls.otra, nombre='X', codigo_sucursal='001', direccion='x')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self._como(self.empresa, self.taller)

    def _como(self, empresa, taller=None):
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(empresa.pk)
        if taller:
            self.client.defaults['HTTP_X_TALLER_ID'] = str(taller.pk)
        else:
            self.client.defaults.pop('HTTP_X_TALLER_ID', None)

    def test_borrar_y_recrear_cliente_con_misma_identificacion(self):
        cliente = _cliente(self.empresa, '1700000030')

        resp = self.client.delete(f'/api/clientes/{cliente.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['action'], 'soft_delete')

        resp = self.client.post('/api/clientes/', {
            'tipo_identificacion': 'C', 'identificacion': '1700000030', 'nombre': 'Recreado',
        }, format='multipart')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Cliente.all_objects.filter(identificacion='1700000030').count(), 2)

    def test_borrar_ot_es_logico(self):
        cliente = _cliente(self.empresa, '1700000031')
        vehiculo = Vehiculo.objects.create(placa='API0001', marca='Kia', modelo='Rio')
        vehiculo.empresas.add(self.empresa)
        orden = OrdenTrabajo.objects.create(
            empresa=self.empresa, sucursal=self.taller, cliente=cliente, vehiculo=vehiculo, numero_orden='OT-9'
        )

        resp = self.client.delete(f'{OT_URL}{orden.pk}/')

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(OrdenTrabajo.all_objects.get(pk=orden.pk).esta_eliminado)
        self.assertEqual(self.client.get(f'{OT_URL}{orden.pk}/').status_code, 404)

    def test_vehiculo_compartido_se_elimina_solo_en_una_empresa(self):
        vehiculo = Vehiculo.objects.create(placa='SHR0001', marca='Kia', modelo='Rio')
        vehiculo.empresas.add(self.empresa, self.otra)

        resp = self.client.delete(f'/api/vehiculos/{vehiculo.pk}/')
        self.assertEqual(resp.status_code, 200)

        vehiculo.refresh_from_db()
        self.assertTrue(vehiculo.esta_operativo)
        ids = [v['id'] for v in self.client.get('/api/vehiculos/').data['results']]
        self.assertNotIn(vehiculo.pk, ids)

        self._como(self.otra)
        ids = [v['id'] for v in self.client.get('/api/vehiculos/').data['results']]
        self.assertIn(vehiculo.pk, ids)

    def test_recrear_placa_eliminada_vuelve_a_vincular(self):
        vehiculo = Vehiculo.objects.create(placa='REL0001', marca='Kia', modelo='Rio')
        vehiculo.empresas.add(self.empresa)
        self.client.delete(f'/api/vehiculos/{vehiculo.pk}/')

        resp = self.client.post('/api/vehiculos/', {
            'placa': 'rel0001', 'marca': 'Kia', 'modelo': 'Rio',
        }, format='json')

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['id'], vehiculo.pk)
        self.assertTrue(resp.data['is_active'])
        self.assertEqual(VehiculoEmpresa.objects.filter(vehiculo=vehiculo, empresa=self.empresa).count(), 1)

    def test_suspender_y_reactivar_vehiculo_por_empresa(self):
        vehiculo = Vehiculo.objects.create(placa='SUS0001', marca='Kia', modelo='Rio')
        vehiculo.empresas.add(self.empresa, self.otra)

        resp = self.client.patch(f'/api/vehiculos/{vehiculo.pk}/', {'is_active': False}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data['is_active'])
        self.assertTrue(vehiculo.activo_en(self.otra.pk))

        resp = self.client.post('/api/vehiculos/', {'placa': 'SUS0001', 'marca': 'Kia', 'modelo': 'Rio'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('inactive_duplicate', resp.data)

        resp = self.client.patch(f'/api/vehiculos/{vehiculo.pk}/', {'is_active': True}, format='json')
        self.assertTrue(resp.data['is_active'])

    def test_ot_rechaza_fks_de_otra_empresa(self):
        cliente = _cliente(self.empresa, '1700000032')
        cliente_otra = _cliente(self.otra, '1700000033')
        vehiculo = Vehiculo.objects.create(placa='LEAK001', marca='Kia', modelo='Rio')
        vehiculo.empresas.add(self.empresa)
        vehiculo_otra = Vehiculo.objects.create(placa='LEAK002', marca='Kia', modelo='Rio')
        vehiculo_otra.empresas.add(self.otra)
        base = {'cliente': cliente.pk, 'vehiculo': vehiculo.pk, 'estado': 'PENDIENTE',
                'prioridad': 'MEDIA', 'tipo_trabajo': 'MANTENIMIENTO'}

        casos = {
            'cliente': {**base, 'cliente': cliente_otra.pk},
            'vehiculo': {**base, 'vehiculo': vehiculo_otra.pk},
            'sucursal': {**base, 'sucursal': self.taller_otra.pk},
        }
        for campo, payload in casos.items():
            resp = self.client.post(OT_URL, payload, format='json')
            self.assertEqual(resp.status_code, 400, (campo, resp.data))
            self.assertIn(campo, resp.data)

        resp = self.client.post(OT_URL, base, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_ot_rechaza_cliente_eliminado(self):
        cliente = _cliente(self.empresa, '1700000034')
        vehiculo = Vehiculo.objects.create(placa='DEL0001', marca='Kia', modelo='Rio')
        vehiculo.empresas.add(self.empresa)
        cliente.delete()

        resp = self.client.post(OT_URL, {
            'cliente': cliente.pk, 'vehiculo': vehiculo.pk, 'estado': 'PENDIENTE',
            'prioridad': 'MEDIA', 'tipo_trabajo': 'MANTENIMIENTO',
        }, format='json')

        self.assertEqual(resp.status_code, 400)
        self.assertIn('cliente', resp.data)

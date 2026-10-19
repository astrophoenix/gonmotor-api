"""Tests del buscador global (`GET /api/core/buscar/`).

Verifica el contrato de la respuesta (grupos + acotado por tenant), el umbral
mínimo de caracteres y el aislamiento multi-empresa.
"""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo
from apps.core.testing import fijar_empresa

User = get_user_model()

ENDPOINT = '/api/core/buscar/'


def _ruc_unico():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class BusquedaGlobalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='busq_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Búsqueda',
            ruc=_ruc_unico(),
            email_contacto='busqueda@test.com',
        )
        cls.empresa_ajena = Empresa.objects.create(
            nombre_comercial='Taller Ajeno',
            ruc=_ruc_unico(),
            email_contacto='ajeno@test.com',
        )

        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            tipo_identificacion='C',
            identificacion='1799999999',
            nombre='Roberto Buscable',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='BUS1234', marca='Toyota', modelo='Hilux')
        cls.vehiculo.empresas.add(cls.empresa)

        # Registros de la otra empresa: nunca deben aparecer.
        cls.cliente_ajeno = Cliente.objects.create(
            empresa=cls.empresa_ajena,
            tipo_identificacion='C',
            identificacion='1700000000',
            nombre='Roberto Ajeno',
        )
        cls.vehiculo_ajeno = Vehiculo.objects.create(placa='BUS9999', marca='Toyota', modelo='Yaris')
        cls.vehiculo_ajeno.empresas.add(cls.empresa_ajena)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        fijar_empresa(self.client, str(self.empresa.uuid))
    def test_requiere_autenticacion(self):
        anonimo = APIClient()
        respuesta = anonimo.get(ENDPOINT, {'q': 'bus'})
        self.assertIn(respuesta.status_code, (401, 403))

    def test_usuario_sin_empresa_devuelve_403(self):
        sin_empresa = User.objects.create_user(username='busq_sin_empresa', password='x')
        cliente = APIClient()
        cliente.force_authenticate(user=sin_empresa)
        respuesta = cliente.get(ENDPOINT, {'q': 'bus'})
        self.assertEqual(respuesta.status_code, 403)

    def test_consulta_corta_no_busca(self):
        respuesta = self.client.get(ENDPOINT, {'q': 'b'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['total'], 0)
        self.assertEqual(respuesta.data['grupos'], [])

    def test_encuentra_cliente_y_vehiculo_agrupados(self):
        respuesta = self.client.get(ENDPOINT, {'q': 'bus'})
        self.assertEqual(respuesta.status_code, 200)

        tipos = {grupo['tipo']: grupo for grupo in respuesta.data['grupos']}
        self.assertIn('clientes', tipos)
        self.assertIn('vehiculos', tipos)

        cliente = tipos['clientes']['resultados'][0]
        self.assertEqual(cliente['id'], str(self.cliente.uuid))
        self.assertEqual(cliente['titulo'], 'Roberto Buscable')

        vehiculo = tipos['vehiculos']['resultados'][0]
        self.assertEqual(vehiculo['id'], str(self.vehiculo.uuid))
        self.assertEqual(vehiculo['titulo'], 'BUS1234')

    def test_encuentra_vehiculo_por_placa_con_guion(self):
        respuesta = self.client.get(ENDPOINT, {'q': 'BUS-1234'})
        tipos = {grupo['tipo']: grupo for grupo in respuesta.data['grupos']}
        self.assertIn('vehiculos', tipos)
        self.assertEqual(tipos['vehiculos']['resultados'][0]['id'], str(self.vehiculo.uuid))

    def test_aislamiento_por_empresa(self):
        respuesta = self.client.get(ENDPOINT, {'q': 'Roberto'})
        ids = [
            resultado['id']
            for grupo in respuesta.data['grupos']
            for resultado in grupo['resultados']
        ]
        self.assertIn(str(self.cliente.uuid), ids)
        self.assertNotIn(str(self.cliente_ajeno.uuid), ids)


class BusquedaGlobalTallerTest(TestCase):
    """El buscador acota las entidades con taller al taller activo (`X-Taller-ID`)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='busq_taller_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Filtro',
            ruc=_ruc_unico(),
            email_contacto='filtro@test.com',
        )
        cls.central = Taller.objects.create(
            empresa=cls.empresa, nombre='Central', codigo_sucursal='001', direccion='Av. 1',
        )
        cls.norte = Taller.objects.create(
            empresa=cls.empresa, nombre='Norte', codigo_sucursal='002', direccion='Av. 2',
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            tipo_identificacion='C',
            identificacion='1788888888',
            nombre='Marta Buscable',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='FIL1234', marca='Mazda', modelo='3')
        cls.vehiculo.empresas.add(cls.empresa)
        cls.orden_central = OrdenTrabajo.objects.create(
            empresa=cls.empresa, sucursal=cls.central, cliente=cls.cliente,
            vehiculo=cls.vehiculo, numero_orden='FIL-C-0001', tipo_trabajo='REPARACION',
        )
        cls.orden_norte = OrdenTrabajo.objects.create(
            empresa=cls.empresa, sucursal=cls.norte, cliente=cls.cliente,
            vehiculo=cls.vehiculo, numero_orden='FIL-N-0001', tipo_trabajo='REPARACION',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        fijar_empresa(self.client, str(self.empresa.uuid))
    def _ids_ordenes(self, respuesta):
        grupos = {grupo['tipo']: grupo for grupo in respuesta.data['grupos']}
        return {fila['id'] for fila in grupos.get('ordenes', {}).get('resultados', [])}

    def test_sin_cabecera_busca_en_todos_los_talleres(self):
        respuesta = self.client.get(ENDPOINT, {'q': 'Marta'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            self._ids_ordenes(respuesta),
            {str(self.orden_central.uuid), str(self.orden_norte.uuid)},
        )

    def test_con_cabecera_solo_del_taller_activo(self):
        respuesta = self.client.get(
            ENDPOINT, {'q': 'Marta'}, HTTP_X_TALLER_ID=str(self.central.uuid)
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._ids_ordenes(respuesta), {str(self.orden_central.uuid)})

    def _cliente_no_admin(self, talleres):
        user = User.objects.create_user(username=f'busq_meca_{uuid4().hex[:8]}', password='x')
        relacion = UsuarioEmpresa.objects.create(
            user=user, empresa=self.empresa, rol='MECANICO'
        )
        relacion.talleres.set(talleres)
        client = APIClient()
        client.force_authenticate(user=user)
        fijar_empresa(client, str(self.empresa.uuid))
        return client

    def test_no_admin_sin_cabecera_solo_busca_en_sus_talleres(self):
        client = self._cliente_no_admin([self.central])
        respuesta = client.get(ENDPOINT, {'q': 'Marta'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._ids_ordenes(respuesta), {str(self.orden_central.uuid)})

    def test_no_admin_sin_talleres_no_encuentra_ordenes(self):
        client = self._cliente_no_admin([])
        respuesta = client.get(ENDPOINT, {'q': 'Marta'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._ids_ordenes(respuesta), set())


    def test_encuentra_documento_inactivo_del_taller_activo(self):
        inactiva = OrdenTrabajo.objects.create(
            empresa=self.empresa, sucursal=self.central, cliente=self.cliente,
            vehiculo=self.vehiculo, numero_orden='FIL-C-0002', tipo_trabajo='REPARACION',
            is_active=False,
        )
        respuesta = self.client.get(
            ENDPOINT, {'q': 'FIL-C-0002'}, HTTP_X_TALLER_ID=str(self.central.uuid)
        )
        grupos = {grupo['tipo']: grupo for grupo in respuesta.data['grupos']}
        resultado = grupos['ordenes']['resultados'][0]
        self.assertEqual(resultado['id'], str(inactiva.uuid))
        self.assertEqual(resultado['meta'], inactiva.get_estado_display())
        self.assertEqual(resultado['estado'], inactiva.estado)

    def test_documento_inactivo_de_otro_taller_no_aparece(self):
        OrdenTrabajo.objects.create(
            empresa=self.empresa, sucursal=self.norte, cliente=self.cliente,
            vehiculo=self.vehiculo, numero_orden='FIL-N-0002', tipo_trabajo='REPARACION',
            is_active=False,
        )
        respuesta = self.client.get(
            ENDPOINT, {'q': 'FIL-N-0002'}, HTTP_X_TALLER_ID=str(self.central.uuid)
        )
        self.assertEqual(respuesta.data['total'], 0)


class BusquedaGlobalPermisosTest(TestCase):
    """Solo aparecen los grupos que el rol puede consultar (permiso `ver`)."""

    @classmethod
    def setUpTestData(cls):
        from apps.inventario.models import Repuesto
        from apps.proveedores.models import Proveedor

        cls.empresa = Empresa.objects.create(nombre_comercial='Taller Permisos', ruc=_ruc_unico())
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Central', codigo_sucursal='001', direccion='x')
        Cliente.objects.create(empresa=cls.empresa, identificacion='1711111111', nombre='Zafiro Cliente')
        Proveedor.objects.create(empresa=cls.empresa, identificacion='1792222222001', nombre='Zafiro Proveedor')
        Repuesto.objects.create(empresa=cls.empresa, codigo='ZAF-01', nombre='Zafiro Filtro')

    def _buscar_como(self, rol):
        user = User.objects.create_user(username=f'busq_{rol}_{uuid4().hex[:6]}', password='x')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=self.empresa, rol=rol)
        relacion.talleres.set([self.taller])
        cliente = APIClient()
        cliente.force_authenticate(user=user)
        fijar_empresa(cliente, str(self.empresa.uuid))
        respuesta = cliente.get(ENDPOINT, {'q': 'zafiro'})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return {grupo['tipo'] for grupo in respuesta.json()['grupos']}

    def test_mecanico_no_ve_proveedores(self):
        tipos = self._buscar_como('MECANICO')
        self.assertIn('clientes', tipos)
        self.assertIn('repuestos', tipos)
        self.assertNotIn('proveedores', tipos)

    def test_asesor_ve_proveedores(self):
        self.assertIn('proveedores', self._buscar_como('ASESOR'))

    def test_sin_inventario_no_ve_repuestos(self):
        from apps.authentication.models import Rol
        from apps.authentication.permisos_repo import guardar_permisos_empresa

        guardar_permisos_empresa(
            Rol.objects.get(codigo='MECANICO'),
            self.empresa.id,
            [{'recurso': 'inventario', 'ver': False, 'modificar': False}],
        )
        self.assertNotIn('repuestos', self._buscar_como('MECANICO'))

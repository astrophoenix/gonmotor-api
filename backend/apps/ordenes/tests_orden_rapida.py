"""Captura rápida de una OT (RF-08): datos del vehículo por placa y visitas
anteriores para copiar sus trabajos."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.vehiculos.models import Vehiculo, asignar_propietario_actual, vincular_empresa

from .models import DetalleRepuestoOrdenTrabajo, DetalleServicioOrdenTrabajo, OrdenTrabajo

User = get_user_model()
URL = '/api/ordenes/ordenes-trabajo/datos-vehiculo/'


def _ruc():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class DatosVehiculoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Taller Rápido', ruc=_ruc())
        cls.otra = Empresa.objects.create(nombre_comercial='Otra', ruc=_ruc())
        cls.a1 = Taller.objects.create(empresa=cls.empresa, nombre='A1', codigo_sucursal='001', direccion='x')
        cls.a2 = Taller.objects.create(empresa=cls.empresa, nombre='A2', codigo_sucursal='002', direccion='x')
        cls.taller_otra = Taller.objects.create(empresa=cls.otra, nombre='B1', codigo_sucursal='001', direccion='x')

        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa, nombre='Ana Dueña', identificacion='1700000201', telefono='0991112222',
        )
        cls.cliente_otra = Cliente.objects.create(empresa=cls.otra, nombre='Dueño Ajeno', identificacion='1700000202')
        cls.vehiculo = Vehiculo.objects.create(placa='RAP0001', marca='Kia', modelo='Rio', kilometraje_actual=45200)
        for empresa, cliente in ((cls.empresa, cls.cliente), (cls.otra, cls.cliente_otra)):
            vincular_empresa(cls.vehiculo, empresa.pk)
            asignar_propietario_actual(cls.vehiculo, cliente)
        cls.solo_otra = Vehiculo.objects.create(placa='AJE0001', marca='Fiat', modelo='Uno')
        vincular_empresa(cls.solo_otra, cls.otra.pk)

        cls.asesor = cls._usuario('rapida_asesor', cls.empresa, 'ASESOR', [cls.a1])
        cls.admin = cls._usuario('rapida_admin', cls.empresa, 'ADMIN_EMPRESA')
        cls.sin_flujo = cls._usuario('rapida_cajero_sin_taller', cls.empresa, 'CAJERO')

        ahora = timezone.now()
        cls.vieja = cls._orden('A1-OT1', cls.a1, ahora - timedelta(days=200), km=30000)
        cls.reciente = cls._orden('A1-OT2', cls.a1, ahora - timedelta(days=10), km=45000)
        cls.otro_taller = cls._orden('A2-OT1', cls.a2, ahora - timedelta(days=5), km=45100)
        cls.anulada = cls._orden('A1-OT3', cls.a1, ahora - timedelta(days=1), km=45150, estado='CANCELADO')
        OrdenTrabajo.objects.create(
            empresa=cls.otra, sucursal=cls.taller_otra, cliente=cls.cliente_otra, vehiculo=cls.vehiculo,
            numero_orden='B-OT1', fecha_ingreso=ahora, kilometraje=45200,
        )
        DetalleServicioOrdenTrabajo.objects.create(
            orden_trabajo=cls.reciente, descripcion='Cambio de aceite', horas_aplicadas=Decimal('0.5'),
            precio_unitario=Decimal('15'), descuento=Decimal('1'), servicio_codigo='SRV-01', completado=True,
        )
        DetalleRepuestoOrdenTrabajo.objects.create(
            orden_trabajo=cls.reciente, codigo_repuesto='FIL-01', descripcion='Filtro de aceite',
            cantidad=Decimal('1'), precio_unitario=Decimal('8.50'), iva_porcentaje=Decimal('0.15'),
        )

    @classmethod
    def _orden(cls, numero, taller, fecha, km, estado='ENTREGADO'):
        return OrdenTrabajo.objects.create(
            empresa=cls.empresa, sucursal=taller, cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_orden=numero, fecha_ingreso=fecha, kilometraje=km, estado=estado,
        )

    @staticmethod
    def _usuario(username, empresa, rol, talleres=None):
        user = User.objects.create_user(username=username, password='x', email=f'{username}@test.ec')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=empresa, rol=rol)
        if talleres:
            relacion.talleres.set(talleres)
        return user

    def _get(self, user, vehiculo=None, **params):
        client = APIClient()
        client.force_authenticate(user=user)
        fijar_empresa(client, str(self.empresa.uuid))
        return client.get(URL, {'vehiculo': str((vehiculo or self.vehiculo).uuid), **params})

    def test_dueno_de_la_empresa_activa_y_kilometraje(self):
        respuesta = self._get(self.asesor)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['cliente']['id'], str(self.cliente.uuid))
        self.assertEqual(respuesta.data['cliente']['nombre'], 'Ana Dueña')
        self.assertEqual(respuesta.data['vehiculo']['kilometraje_actual'], 45200)

    def test_visitas_del_taller_al_alcance_sin_anuladas_ni_de_otra_empresa(self):
        numeros = [v['numero_orden'] for v in self._get(self.asesor).data['visitas']]
        # El asesor solo ve A1; la anulada y la de la otra empresa no aparecen.
        self.assertEqual(numeros, ['A1-OT2', 'A1-OT1'])
        numeros_admin = [v['numero_orden'] for v in self._get(self.admin).data['visitas']]
        self.assertEqual(numeros_admin, ['A2-OT1', 'A1-OT2', 'A1-OT1'])

    def test_lineas_solo_con_datos_comerciales(self):
        visita = self._get(self.asesor).data['visitas'][0]
        servicio, = visita['servicios']
        repuesto, = visita['repuestos']
        self.assertEqual(servicio, {
            'descripcion': 'Cambio de aceite', 'horas_aplicadas': '0.50', 'precio_unitario': '15.00',
            'descuento': '1.00', 'iva_porcentaje': '0.1500', 'servicio_codigo': 'SRV-01',
            'garantia_dias': 0, 'garantia_km': 0,
        })
        self.assertEqual(repuesto['codigo_repuesto'], 'FIL-01')
        self.assertEqual(repuesto['precio_unitario'], '8.50')
        self.assertNotIn('completado', servicio)
        self.assertNotIn('cantidad_despachada', repuesto)

    def test_excluir_la_orden_que_se_edita(self):
        numeros = [v['numero_orden'] for v in self._get(self.asesor, excluir=str(self.reciente.uuid)).data['visitas']]
        self.assertEqual(numeros, ['A1-OT1'])

    def test_vehiculo_de_otra_empresa_no_existe(self):
        self.assertEqual(self._get(self.asesor, vehiculo=self.solo_otra).status_code, 404)
        client = APIClient()
        client.force_authenticate(user=self.asesor)
        fijar_empresa(client, str(self.empresa.uuid))
        self.assertEqual(client.get(URL, {'vehiculo': 'no-es-uuid'}).status_code, 404)

    def test_sin_permiso_de_ordenes(self):
        sin_permisos = self._usuario('rapida_sin_rol', self.otra, 'MECANICO')
        client = APIClient()
        client.force_authenticate(user=sin_permisos)
        fijar_empresa(client, str(self.empresa.uuid))
        self.assertEqual(client.get(URL, {'vehiculo': str(self.vehiculo.uuid)}).status_code, 403)

    def test_usuario_sin_talleres_no_ve_visitas(self):
        respuesta = self._get(self.sin_flujo)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['visitas'], [])

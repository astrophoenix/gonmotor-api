"""Auditoría de aislamiento: un usuario no accede a datos fuera de su alcance.

Cada test pide un recurso que NO le corresponde al usuario (otra empresa, otro
taller u otro rol) y exige que la API lo niegue. Cubren los huecos que deja el
filtrado por tenant cuando una vista o serializer no pasa por las piezas
centrales (`para_request`, `FiltroTallerMixin`, `ValidarTenantMixin`,
`TieneRecurso`).
"""

from datetime import date, time, timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.citas.models import Cita
from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion, DetalleServicioCotizacion
from apps.empresas.models import Empresa, Taller
from apps.gastos.models import Gasto
from apps.ordenes.models import (
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioOrdenTrabajo,
    FotoOrdenTrabajo,
    OrdenTrabajo,
    RecepcionVehiculo,
)
from apps.vehiculos.models import Vehiculo, vincular_empresa
from apps.core.testing import fijar_empresa

User = get_user_model()

EMPRESA_INEXISTENTE = '999999'


def _ruc():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class AuditoriaTenantTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa_a = Empresa.objects.create(nombre_comercial='Empresa A', ruc=_ruc())
        cls.empresa_b = Empresa.objects.create(nombre_comercial='Empresa B', ruc=_ruc())
        cls.taller_a1 = Taller.objects.create(empresa=cls.empresa_a, nombre='A1', codigo_sucursal='001', direccion='x')
        cls.taller_a2 = Taller.objects.create(empresa=cls.empresa_a, nombre='A2', codigo_sucursal='002', direccion='x')
        cls.taller_b = Taller.objects.create(empresa=cls.empresa_b, nombre='B1', codigo_sucursal='001', direccion='x')

        cls.cliente_a = Cliente.objects.create(empresa=cls.empresa_a, nombre='Cliente A', identificacion='1700000001')
        cls.cliente_b = Cliente.objects.create(empresa=cls.empresa_b, nombre='Cliente B', identificacion='1700000002')
        cls.vehiculo = Vehiculo.objects.create(placa='AUD0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa_a.pk)
        vincular_empresa(cls.vehiculo, cls.empresa_b.pk)

        cls.ot_a1 = OrdenTrabajo.objects.create(
            empresa=cls.empresa_a, sucursal=cls.taller_a1, cliente=cls.cliente_a,
            vehiculo=cls.vehiculo, numero_orden='A1-OT',
        )
        cls.ot_a2 = OrdenTrabajo.objects.create(
            empresa=cls.empresa_a, sucursal=cls.taller_a2, cliente=cls.cliente_a,
            vehiculo=cls.vehiculo, numero_orden='A2-OT',
        )
        cls.ot_b = OrdenTrabajo.objects.create(
            empresa=cls.empresa_b, sucursal=cls.taller_b, cliente=cls.cliente_b,
            vehiculo=cls.vehiculo, numero_orden='B-OT',
        )
        cls.linea_a2 = DetalleServicioOrdenTrabajo.objects.create(
            orden_trabajo=cls.ot_a2, descripcion='Servicio taller A2', precio_unitario=Decimal('10'),
        )
        cls.foto_a2 = FotoOrdenTrabajo.objects.create(orden_trabajo=cls.ot_a2, imagen='ordenes/orden_fotos/a2.jpg')

        cls.cot_a = Cotizacion.objects.create(
            empresa=cls.empresa_a, sucursal=cls.taller_a1, cliente=cls.cliente_a,
            vehiculo=cls.vehiculo, numero_cotizacion='A-COT',
        )
        cls.cot_b = Cotizacion.objects.create(
            empresa=cls.empresa_b, sucursal=cls.taller_b, cliente=cls.cliente_b,
            vehiculo=cls.vehiculo, numero_cotizacion='B-COT',
        )
        cls.gasto_b = Gasto.objects.create(
            empresa=cls.empresa_b, taller=cls.taller_b, fecha=date(2026, 1, 10), monto=Decimal('99'),
        )

        cls.admin_a = cls._usuario('aud_admin_a', cls.empresa_a, 'ADMIN_EMPRESA')
        cls.asesor_a1 = cls._usuario('aud_asesor_a1', cls.empresa_a, 'ASESOR', [cls.taller_a1])
        cls.mecanico_a1 = cls._usuario('aud_mecanico_a1', cls.empresa_a, 'MECANICO', [cls.taller_a1])
        cls.cajero_a1 = cls._usuario('aud_cajero_a1', cls.empresa_a, 'CAJERO', [cls.taller_a1])
        cls.admin_taller_a1 = cls._usuario('aud_admin_taller_a1', cls.empresa_a, 'ADMIN_TALLER', [cls.taller_a1])
        cls.empleado_b_user = cls._usuario('aud_empleado_b', cls.empresa_b, 'ASESOR', [cls.taller_b])
        cls.empleado_b = UsuarioEmpresa.objects.get(user=cls.empleado_b_user)

        cls.cita_a = Cita.objects.create(
            empresa=cls.empresa_a, taller=cls.taller_a1, cliente=cls.cliente_a, vehiculo=cls.vehiculo,
            fecha_cita=date.today() + timedelta(days=7), hora_cita=time(10, 0),
        )
        cls.recepcion_b = RecepcionVehiculo.objects.create(
            empresa=cls.empresa_b, sucursal=cls.taller_b, cliente=cls.cliente_b,
            vehiculo=cls.vehiculo, numero_recepcion='B-REC',
        )

    @staticmethod
    def _usuario(username, empresa, rol, talleres=None):
        user = User.objects.create_user(username=username, password='x', email=f'{username}@test.ec')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=empresa, rol=rol)
        if talleres:
            relacion.talleres.set(talleres)
        return user

    def _cliente(self, user, empresa_id=None):
        client = APIClient()
        client.force_authenticate(user=user)
        if empresa_id:
            fijar_empresa(client, str(empresa_id))
        return client

    # --- Otra empresa: lectura ---------------------------------------------

    def test_gastos_con_empresa_ajena_en_cabecera_no_listan_otras_empresas(self):
        respuesta = self._cliente(self.admin_a, EMPRESA_INEXISTENTE).get('/api/gastos/')
        if respuesta.status_code == 200:
            datos = respuesta.json()
            filas = datos['results'] if isinstance(datos, dict) else datos
            self.assertNotIn(self.gasto_b.pk, [g['id'] for g in filas])
        else:
            self.assertIn(respuesta.status_code, (403, 404))

    def test_empleados_no_acepta_empresa_por_query_param(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        respuesta = client.get('/api/auth/empleados/', {'empresa': str(self.empresa_b.uuid)})
        self.assertEqual(respuesta.status_code, 200)
        ids = [e['id'] for e in respuesta.json()['results']]
        self.assertNotIn(self.empleado_b.pk, ids)

    def test_empleados_no_modifica_empleado_de_otra_empresa(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        url = f'/api/auth/empleados/{self.empleado_b.uuid}/?empresa={self.empresa_b.pk}'
        respuesta = client.patch(url, {'tiene_acceso': False}, format='json')
        self.assertEqual(respuesta.status_code, 404)
        self.empleado_b.refresh_from_db()
        self.assertTrue(self.empleado_b.tiene_acceso)

    # --- Otra empresa: escritura vía FK del payload --------------------------

    def test_no_crea_detalle_en_cotizacion_de_otra_empresa(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        respuesta = client.post('/api/cotizaciones/servicios/', {
            'cotizacion': str(self.cot_b.uuid), 'codigo': 'X', 'descripcion': 'Intruso',
            'precio_unitario': '1.00',
        }, format='json')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertFalse(DetalleServicioCotizacion.objects.filter(cotizacion=self.cot_b).exists())

    def test_no_crea_servicio_en_orden_de_otra_empresa(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        respuesta = client.post('/api/ordenes/ot-servicios/', {
            'orden_trabajo': str(self.ot_b.uuid), 'descripcion': 'Intruso', 'precio_unitario': '1.00',
        }, format='json')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertFalse(DetalleServicioOrdenTrabajo.objects.filter(orden_trabajo=self.ot_b).exists())

    def test_no_crea_repuesto_en_orden_de_otra_empresa(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        respuesta = client.post('/api/ordenes/ot-repuestos/', {
            'orden_trabajo': str(self.ot_b.uuid), 'codigo_repuesto': 'X', 'descripcion': 'Intruso',
            'cantidad': 1, 'precio_unitario': '1.00',
        }, format='json')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertFalse(DetalleRepuestoOrdenTrabajo.objects.filter(orden_trabajo=self.ot_b).exists())

    def test_no_reasigna_cotizacion_a_cliente_de_otra_empresa(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        respuesta = client.patch(
            f'/api/cotizaciones/{self.cot_a.uuid}/', {'cliente': str(self.cliente_b.uuid)}, format='json'
        )
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.cot_a.refresh_from_db()
        self.assertEqual(self.cot_a.cliente_id, self.cliente_a.pk)

    # --- Otro taller de la misma empresa -------------------------------------

    def test_lineas_de_orden_de_otro_taller_no_se_listan(self):
        client = self._cliente(self.asesor_a1, self.empresa_a.uuid)
        respuesta = client.get('/api/ordenes/ot-servicios/', {'orden': str(self.ot_a2.uuid)})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['results'], [])

    def test_linea_de_orden_de_otro_taller_no_se_modifica(self):
        client = self._cliente(self.asesor_a1, self.empresa_a.uuid)
        respuesta = client.patch(
            f'/api/ordenes/ot-servicios/{self.linea_a2.uuid}/', {'descripcion': 'Cambiado'}, format='json'
        )
        self.assertEqual(respuesta.status_code, 404)

    def test_fotos_de_orden_de_otro_taller_no_se_ven(self):
        client = self._cliente(self.asesor_a1, self.empresa_a.uuid)
        respuesta = client.get(f'/api/ordenes/orden-fotos/{self.foto_a2.uuid}/')
        self.assertEqual(respuesta.status_code, 404)

    # --- Rol sin permiso -----------------------------------------------------

    def test_mecanico_no_ve_reportes_de_rentabilidad(self):
        client = self._cliente(self.mecanico_a1, self.empresa_a.uuid)
        respuesta = client.get('/api/reportes/rentabilidad-empresa/')
        self.assertEqual(respuesta.status_code, 403)

    def test_mecanico_no_ve_gastos(self):
        client = self._cliente(self.mecanico_a1, self.empresa_a.uuid)
        respuesta = client.get('/api/gastos/')
        self.assertEqual(respuesta.status_code, 403)

    def test_mecanico_no_cambia_preferencias_de_whatsapp(self):
        client = self._cliente(self.mecanico_a1, self.empresa_a.uuid)
        respuesta = client.patch('/api/notificaciones/preferencias/', {'activo': False}, format='json')
        self.assertEqual(respuesta.status_code, 403)

    def test_cajero_no_ve_gastos(self):
        respuesta = self._cliente(self.cajero_a1, self.empresa_a.uuid).get('/api/gastos/')
        self.assertEqual(respuesta.status_code, 403)

    def test_admin_taller_ve_gastos(self):
        respuesta = self._cliente(self.admin_taller_a1, self.empresa_a.uuid).get('/api/gastos/')
        self.assertEqual(respuesta.status_code, 200)

    def test_asesor_consulta_notificaciones_sin_configurarlas(self):
        client = self._cliente(self.asesor_a1, self.empresa_a.uuid)
        self.assertEqual(client.get('/api/notificaciones/preferencias/').status_code, 200)
        self.assertEqual(client.get('/api/notificaciones/recordatorios/').status_code, 200)
        self.assertEqual(
            client.patch('/api/notificaciones/preferencias/', {'activo': False}, format='json').status_code, 403
        )
        self.assertEqual(
            client.post('/api/notificaciones/recordatorios/enviar-prueba/', {'celular': '0991234567'}, format='json').status_code,
            403,
        )

    # --- Usuarios asignados (asesor, responsable, técnico) -------------------

    def test_no_asigna_asesor_de_otra_empresa(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        casos = (
            (f'/api/citas/{self.cita_a.uuid}/', 'asesor'),
            (f'/api/cotizaciones/{self.cot_a.uuid}/', 'asesor'),
            (f'/api/ordenes/ordenes-trabajo/{self.ot_a1.uuid}/', 'asesor'),
            (f'/api/ordenes/ordenes-trabajo/{self.ot_a1.uuid}/', 'mecanico_principal'),
        )
        for url, campo in casos:
            with self.subTest(url=url, campo=campo):
                respuesta = client.patch(url, {campo: str(self.empleado_b_user.profile.uuid)}, format='json')
                self.assertEqual(respuesta.status_code, 400, respuesta.content)
                self.assertIn(campo, respuesta.json())
                self.assertNotIn(self.empleado_b_user.email, respuesta.content.decode())

    def test_asigna_asesor_de_la_propia_empresa(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        respuesta = client.patch(
            f'/api/cotizaciones/{self.cot_a.uuid}/', {'asesor': str(self.asesor_a1.profile.uuid)}, format='json'
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    def test_recepcion_generada_de_la_cita_no_se_escribe(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        client.patch(f'/api/citas/{self.cita_a.uuid}/', {'recepcion_generada': str(self.recepcion_b.uuid)}, format='json')
        self.cita_a.refresh_from_db()
        self.assertIsNone(self.cita_a.recepcion_generada_id)

    # --- Configuración de la empresa -----------------------------------------

    def test_configuracion_de_empresa_no_alcanza_otra_empresa(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        url = f'/api/configuracion/empresa/{self.empresa_b.uuid}/'
        self.assertEqual(client.get(url).status_code, 404)
        self.assertEqual(client.patch(url, {'nombre_comercial': 'Intruso'}, format='json').status_code, 404)
        self.assertEqual(client.delete(url).status_code, 405)
        self.empresa_b.refresh_from_db()
        self.assertEqual(self.empresa_b.nombre_comercial, 'Empresa B')
        self.assertTrue(self.empresa_b.is_active)

    def test_configuracion_de_empresa_edita_la_propia(self):
        client = self._cliente(self.admin_a, self.empresa_a.uuid)
        respuesta = client.patch(
            f'/api/configuracion/empresa/{self.empresa_a.uuid}/', {'nombre_comercial': 'Empresa A2'}, format='json'
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)

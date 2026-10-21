"""Auditoría de permisos y alcance de la recepción.

Cada test intenta, como un usuario real, algo que su empresa, su taller o su
rol no le permiten, y exige que la API lo rechace sin modificar datos.
"""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import Rol, UsuarioEmpresa
from apps.authentication.permisos_repo import guardar_permisos_empresa
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo
from apps.vehiculos.models import Vehiculo, vincular_empresa

User = get_user_model()


def _ruc():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class AuditoriaRecepcionesTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa_a = Empresa.objects.create(nombre_comercial='Empresa A', ruc=_ruc())
        cls.empresa_b = Empresa.objects.create(nombre_comercial='Empresa B', ruc=_ruc())
        cls.taller_a1 = Taller.objects.create(empresa=cls.empresa_a, nombre='A1', codigo_sucursal='001', direccion='x')
        cls.taller_a2 = Taller.objects.create(empresa=cls.empresa_a, nombre='A2', codigo_sucursal='002', direccion='x')
        cls.taller_b = Taller.objects.create(empresa=cls.empresa_b, nombre='B1', codigo_sucursal='001', direccion='x')

        cls.cliente_a = Cliente.objects.create(empresa=cls.empresa_a, nombre='Cliente A', identificacion='1700000011')
        cls.cliente_b = Cliente.objects.create(empresa=cls.empresa_b, nombre='Cliente B', identificacion='1700000012')
        cls.vehiculo = Vehiculo.objects.create(placa='REC0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa_a.pk)
        vincular_empresa(cls.vehiculo, cls.empresa_b.pk)

        cls.ot_a2 = OrdenTrabajo.objects.create(
            empresa=cls.empresa_a, sucursal=cls.taller_a2, cliente=cls.cliente_a,
            vehiculo=cls.vehiculo, numero_orden='A2-OT',
        )
        cls.ot_b = OrdenTrabajo.objects.create(
            empresa=cls.empresa_b, sucursal=cls.taller_b, cliente=cls.cliente_b,
            vehiculo=cls.vehiculo, numero_orden='B-OT',
        )
        cls.cot_a1 = Cotizacion.objects.create(
            empresa=cls.empresa_a, sucursal=cls.taller_a1, cliente=cls.cliente_a,
            vehiculo=cls.vehiculo, numero_cotizacion='A1-COT',
        )
        cls.cot_a2 = Cotizacion.objects.create(
            empresa=cls.empresa_a, sucursal=cls.taller_a2, cliente=cls.cliente_a,
            vehiculo=cls.vehiculo, numero_cotizacion='A2-COT',
        )

        cls.asesor_a1 = cls._usuario('rec_asesor_a1', cls.empresa_a, 'ASESOR', [cls.taller_a1])
        cls.mecanico_a1 = cls._usuario('rec_mecanico_a1', cls.empresa_a, 'MECANICO', [cls.taller_a1])

    def setUp(self):
        self.recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa_a, sucursal=self.taller_a1, cliente=self.cliente_a,
            vehiculo=self.vehiculo, numero_recepcion=f'A1-REC-{uuid4().hex[:6]}',
            motivo_ingreso='Mantenimiento',
        )

    @staticmethod
    def _usuario(username, empresa, rol, talleres):
        user = User.objects.create_user(username=username, password='x', email=f'{username}@test.ec')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=empresa, rol=rol)
        relacion.talleres.set(talleres)
        return user

    def _cliente(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        fijar_empresa(client, str(self.empresa_a.uuid))
        return client

    def _url(self, sufijo=''):
        return f'/api/recepciones/{self.recepcion.uuid}/{sufijo}'

    def _restringir_asesor(self, recurso):
        """El ASESOR de la empresa A conserva `recurso` solo para consultar."""
        guardar_permisos_empresa(
            Rol.objects.get(codigo='ASESOR'),
            self.empresa_a.id,
            [{'recurso': recurso, 'ver': True, 'modificar': False}],
        )

    # --- Edición directa (PATCH) ----------------------------------------------

    def test_patch_no_mueve_la_recepcion_a_otra_empresa(self):
        self._cliente(self.asesor_a1).patch(
            self._url(), {'empresa': str(self.empresa_b.uuid), 'estado': 'NO_ACEPTADA', 'motivo_no_recepcion': 'Prueba'}, format='json',
        )
        self.recepcion.refresh_from_db()
        self.assertEqual(self.recepcion.empresa_id, self.empresa_a.pk)

    def test_patch_no_vincula_orden_de_otra_empresa(self):
        self._cliente(self.asesor_a1).patch(
            self._url(), {'orden_trabajo': str(self.ot_b.uuid), 'estado': 'NO_ACEPTADA', 'motivo_no_recepcion': 'Prueba'}, format='json',
        )
        self.recepcion.refresh_from_db()
        self.assertIsNone(self.recepcion.orden_trabajo_id)

    def test_patch_no_vincula_orden_de_otro_taller(self):
        self._cliente(self.asesor_a1).patch(
            self._url(), {'orden_trabajo': str(self.ot_a2.uuid), 'estado': 'NO_ACEPTADA', 'motivo_no_recepcion': 'Prueba'}, format='json',
        )
        self.recepcion.refresh_from_db()
        self.assertIsNone(self.recepcion.orden_trabajo_id)

    # --- Vínculos del flujo (acción `relaciones`) ---------------------------

    def test_relaciones_no_vincula_documento_de_otro_taller(self):
        self._cliente(self.asesor_a1).post(
            self._url('relaciones/'),
            {'tipo': 'cotizacion', 'id': str(self.cot_a2.uuid), 'accion': 'vincular'},
            format='json',
        )
        self.cot_a2.refresh_from_db()
        self.assertIsNone(self.cot_a2.recepcion_origen_id)

    def test_relaciones_exige_modificar_el_documento_relacionado(self):
        self._restringir_asesor('cotizaciones')
        respuesta = self._cliente(self.asesor_a1).post(
            self._url('relaciones/'),
            {'tipo': 'cotizacion', 'id': str(self.cot_a1.uuid), 'accion': 'vincular'},
            format='json',
        )
        self.assertEqual(respuesta.status_code, 403, respuesta.content)
        self.cot_a1.refresh_from_db()
        self.assertIsNone(self.cot_a1.recepcion_origen_id)

    def test_relaciones_no_edita_cotizacion_desde_recepcion(self):
        respuesta = self._cliente(self.asesor_a1).post(
            self._url('relaciones/'),
            {'tipo': 'cotizacion', 'id': str(self.cot_a1.uuid), 'accion': 'vincular'},
            format='json',
        )
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.cot_a1.refresh_from_db()
        self.assertIsNone(self.cot_a1.recepcion_origen_id)

    def test_solo_consulta_no_cambia_vinculos(self):
        respuesta = self._cliente(self.mecanico_a1).post(
            self._url('relaciones/'),
            {'tipo': 'cotizacion', 'id': str(self.cot_a1.uuid), 'accion': 'vincular'},
            format='json',
        )
        self.assertEqual(respuesta.status_code, 403, respuesta.content)

    # --- Crear inspección desde la recepción --------------------------------

    def test_crear_inspeccion_exige_modificar_inspecciones(self):
        self._restringir_asesor('inspecciones')
        RecepcionVehiculo.objects.filter(pk=self.recepcion.pk).update(estado='ACEPTADA')
        respuesta = self._cliente(self.asesor_a1).post(self._url('crear-inspeccion/'), {}, format='json')
        self.assertEqual(respuesta.status_code, 403, respuesta.content)
        self.assertFalse(InspeccionVehiculo.objects.filter(recepcion=self.recepcion).exists())

    def test_crear_inspeccion_con_permisos(self):
        RecepcionVehiculo.objects.filter(pk=self.recepcion.pk).update(estado='ACEPTADA')
        respuesta = self._cliente(self.asesor_a1).post(self._url('crear-inspeccion/'), {}, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)

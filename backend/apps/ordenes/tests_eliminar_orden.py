"""Solo se elimina una orden de trabajo pendiente (sin trabajo iniciado)."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo, vincular_empresa


class EliminarOrdenTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Borrado', ruc=str(uuid4().int % 10**13).zfill(13),
        )
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Central', codigo_sucursal='001', direccion='x')
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000031')
        cls.vehiculo = Vehiculo.objects.create(placa='DEL0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        cls.admin = get_user_model().objects.create_superuser(username='del-admin', password='x')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        fijar_empresa(self.client, str(self.empresa.uuid))

    def _orden(self, estado):
        return OrdenTrabajo.objects.create(
            empresa=self.empresa, sucursal=self.taller, cliente=self.cliente,
            vehiculo=self.vehiculo, estado=estado, numero_orden=f'DEL-{estado}',
        )

    def _url(self, orden):
        return f'/api/ordenes/ordenes-trabajo/{orden.uuid}/'

    def test_elimina_pendiente(self):
        orden = self._orden(OrdenTrabajo.EstadoOrden.PENDIENTE)
        respuesta = self.client.delete(self._url(orden))
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertFalse(OrdenTrabajo.objects.filter(pk=orden.pk).exists())

    def test_no_elimina_con_trabajo_iniciado_o_cerrada(self):
        for estado in OrdenTrabajo.EstadoOrden.values:
            if estado == OrdenTrabajo.EstadoOrden.PENDIENTE:
                continue
            with self.subTest(estado=estado):
                orden = self._orden(estado)
                respuesta = self.client.delete(self._url(orden))
                self.assertEqual(respuesta.status_code, 400, respuesta.content)
                self.assertIn('Solo se puede eliminar una orden de trabajo pendiente', str(respuesta.json()))
                self.assertTrue(OrdenTrabajo.objects.filter(pk=orden.pk).exists())

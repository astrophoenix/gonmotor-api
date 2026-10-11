"""Tests del buscador global (`GET /api/core/buscar/`).

Verifica el contrato de la respuesta (grupos + acotado por tenant), el umbral
mínimo de caracteres y el aislamiento multi-empresa.
"""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.empresas.models import Empresa
from apps.vehiculos.models import Vehiculo

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
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

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
        self.assertEqual(cliente['id'], self.cliente.pk)
        self.assertEqual(cliente['titulo'], 'Roberto Buscable')

        vehiculo = tipos['vehiculos']['resultados'][0]
        self.assertEqual(vehiculo['id'], self.vehiculo.pk)
        self.assertEqual(vehiculo['titulo'], 'BUS1234')

    def test_encuentra_vehiculo_por_placa_con_guion(self):
        respuesta = self.client.get(ENDPOINT, {'q': 'BUS-1234'})
        tipos = {grupo['tipo']: grupo for grupo in respuesta.data['grupos']}
        self.assertIn('vehiculos', tipos)
        self.assertEqual(tipos['vehiculos']['resultados'][0]['id'], self.vehiculo.pk)

    def test_aislamiento_por_empresa(self):
        respuesta = self.client.get(ENDPOINT, {'q': 'Roberto'})
        ids = [
            resultado['id']
            for grupo in respuesta.data['grupos']
            for resultado in grupo['resultados']
        ]
        self.assertIn(self.cliente.pk, ids)
        self.assertNotIn(self.cliente_ajeno.pk, ids)

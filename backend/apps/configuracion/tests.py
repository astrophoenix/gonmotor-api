from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework.test import APIClient, APITestCase

from apps.authentication.tests import EscenarioMixin, _asignar
from apps.core.testing import fijar_empresa
from apps.cotizaciones.pdf import condiciones_de_empresa
from apps.empresas.models import DetalleEmpresa


class CondicionesEmpresaApiTests(EscenarioMixin, APITestCase):
    """``condiciones`` de la configuración de empresa vive en ``DetalleEmpresa``."""

    def setUp(self):
        super().setUp()
        dueno = User.objects.create_user(username='dueno-condiciones', password='x')
        _asignar(dueno, self.empresa_a, 'ADMIN_EMPRESA', [self.a1])
        self.cliente = APIClient()
        self.cliente.force_authenticate(dueno)
        fijar_empresa(self.cliente, str(self.empresa_a.uuid))
        self.url = f'/api/configuracion/empresa/{self.empresa_a.uuid}/'

    def test_sin_detalle_devuelve_condiciones_vacias(self):
        respuesta = self.cliente.get('/api/configuracion/empresa/')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['condiciones'], '')

    def test_guardar_crea_y_actualiza_el_detalle(self):
        respuesta = self.cliente.patch(self.url, {'condiciones': 'Anticipo del 60%.\r\nGarantía 30 días.  '}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['condiciones'], 'Anticipo del 60%.\nGarantía 30 días.')

        self.cliente.patch(self.url, {'condiciones': 'Solo contado.'}, format='json')
        self.assertEqual(DetalleEmpresa.objects.filter(empresa=self.empresa_a).count(), 1)
        self.assertEqual(self.empresa_a.detalle.condiciones, 'Solo contado.')

    def test_editar_otros_campos_no_borra_las_condiciones(self):
        DetalleEmpresa.objects.create(empresa=self.empresa_a, condiciones='Solo contado.')
        respuesta = self.cliente.patch(self.url, {'nombre_comercial': 'Otro nombre'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['condiciones'], 'Solo contado.')

    def test_quita_caracteres_no_permitidos(self):
        respuesta = self.cliente.patch(self.url, {'condiciones': 'Pago <b>contado</b>~'}, format='json')
        self.assertEqual(respuesta.data['condiciones'], 'Pago bcontado/b')

    def test_maximo_1000_caracteres(self):
        respuesta = self.cliente.patch(self.url, {'condiciones': 'a' * 1001}, format='json')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('condiciones', respuesta.data)


class CondicionesEnPdfTests(SimpleTestCase):
    """El PDF imprime una condición por línea; sin configurar, ninguna extra."""

    def test_una_condicion_por_linea_sin_vinetas(self):
        empresa = SimpleNamespace(detalle=SimpleNamespace(condiciones='- Anticipo 60%.\n\n• Validez sujeta a stock.'))
        self.assertEqual(condiciones_de_empresa(empresa), ['Anticipo 60%.', 'Validez sujeta a stock.'])

    def test_sin_condiciones_no_agrega_ninguna(self):
        for empresa in (None, SimpleNamespace(), SimpleNamespace(detalle=SimpleNamespace(condiciones='  '))):
            with self.subTest(empresa=empresa):
                self.assertEqual(condiciones_de_empresa(empresa), [])

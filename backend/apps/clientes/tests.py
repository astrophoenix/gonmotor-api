from django.contrib.auth.models import User
from rest_framework.test import APITestCase, APIClient
from apps.empresas.models import Empresa
from apps.clientes.models import Cliente


class ClienteEstadoFilterTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='tester', password='x')
        self.empresa = Empresa.objects.create(
            nombre_comercial='Taller GON',
            razon_social='GON SA',
            ruc='1750000000004',
        )
        self.activo = Cliente.objects.create(
            empresa=self.empresa,
            tipo_identificacion='C',
            identificacion='1111',
            nombre='Cliente Activo',
        )
        self.inactivo = Cliente.objects.create(
            empresa=self.empresa,
            tipo_identificacion='C',
            identificacion='2222',
            nombre='Cliente Inactivo',
            is_active=False,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = self.empresa.pk

    def test_listado_sin_filtro_muestra_activos_e_inactivos(self):
        resp = self.client.get('/api/clientes/')
        ids = [c['id'] for c in resp.json()['results']]
        self.assertIn(self.activo.id, ids)
        self.assertIn(self.inactivo.id, ids)

    def test_filtro_estado_inactivo(self):
        resp = self.client.get('/api/clientes/', {'estado': 'inactivo'})
        ids = [c['id'] for c in resp.json()['results']]
        self.assertEqual(ids, [self.inactivo.id])

    def test_filtro_estado_activo(self):
        resp = self.client.get('/api/clientes/', {'estado': 'activo'})
        ids = [c['id'] for c in resp.json()['results']]
        self.assertEqual(ids, [self.activo.id])

    def test_reactivar_cliente(self):
        resp = self.client.post(f'/api/clientes/{self.inactivo.id}/reactivar/')
        self.assertEqual(resp.status_code, 200)
        self.inactivo.refresh_from_db()
        self.assertTrue(self.inactivo.is_active)

    def test_reactivar_cliente_activo_devuelve_400(self):
        resp = self.client.post(f'/api/clientes/{self.activo.id}/reactivar/')
        self.assertEqual(resp.status_code, 400)

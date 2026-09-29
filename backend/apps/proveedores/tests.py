from django.contrib.auth.models import User
from rest_framework.test import APITestCase, APIClient

from apps.empresas.models import Empresa
from apps.proveedores.models import Proveedor


class ProveedorApiTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='tester', password='x')
        self.empresa = Empresa.objects.create(
            nombre_comercial='Taller GON',
            razon_social='GON SA',
            ruc='1750000000004',
        )
        self.empresa_otra = Empresa.objects.create(
            nombre_comercial='Otro Taller',
            razon_social='OTRO SA',
            ruc='1750000000005',
        )
        self.activo = Proveedor.objects.create(
            empresa=self.empresa,
            tipo_identificacion='R',
            identificacion='1790000000001',
            nombre='Repuestos Andes',
            contacto='Juan Pérez',
        )
        self.inactivo = Proveedor.objects.create(
            empresa=self.empresa,
            tipo_identificacion='C',
            identificacion='1111222233',
            nombre='Distribuidora Norte',
            is_active=False,
        )
        self.otra_empresa = Proveedor.objects.create(
            empresa=self.empresa_otra,
            tipo_identificacion='R',
            identificacion='1790000000001',
            nombre='Repuestos Andes (otro taller)',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = self.empresa.pk

    def test_listado_sin_filtro_muestra_activos_e_inactivos(self):
        resp = self.client.get('/api/proveedores/')
        self.assertEqual(resp.status_code, 200)
        ids = [p['id'] for p in resp.json()['results']]
        self.assertIn(self.activo.id, ids)
        self.assertIn(self.inactivo.id, ids)
        self.assertNotIn(self.otra_empresa.id, ids)

    def test_filtro_estado_inactivo(self):
        resp = self.client.get('/api/proveedores/', {'estado': 'inactivo'})
        ids = [p['id'] for p in resp.json()['results']]
        self.assertEqual(ids, [self.inactivo.id])

    def test_filtro_estado_activo(self):
        resp = self.client.get('/api/proveedores/', {'estado': 'activo'})
        ids = [p['id'] for p in resp.json()['results']]
        self.assertEqual(ids, [self.activo.id])

    def test_filtro_tipo_identificacion(self):
        resp = self.client.get('/api/proveedores/', {'tipo_identificacion': 'C'})
        ids = [p['id'] for p in resp.json()['results']]
        self.assertEqual(ids, [self.inactivo.id])

    def test_busqueda_por_nombre(self):
        resp = self.client.get('/api/proveedores/', {'search': 'Andes'})
        ids = [p['id'] for p in resp.json()['results']]
        self.assertEqual(ids, [self.activo.id])

    def test_crear_proveedor(self):
        payload = {
            'tipo_identificacion': 'R',
            'identificacion': '1791112223001',
            'nombre': 'Lubricantes QC',
            'email': 'ventas@lubqc.com',
            'telefono': '0991234567',
            'direccion': 'Av. Principal 123',
            'contacto': 'María López',
        }
        resp = self.client.post('/api/proveedores/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        creado = Proveedor.objects.get(identificacion='1791112223001')
        self.assertEqual(creado.empresa, self.empresa)
        self.assertTrue(creado.is_active)

    def test_crear_proveedor_requiere_nombre_e_identificacion(self):
        resp = self.client.post('/api/proveedores/', {}, format='json')
        self.assertEqual(resp.status_code, 400)
        errores = resp.json()
        self.assertIn('identificacion', errores)
        self.assertIn('nombre', errores)

    def test_crear_duplicado_activo_devuelve_error(self):
        payload = {
            'tipo_identificacion': 'R',
            'identificacion': self.activo.identificacion,
            'nombre': 'Duplicado Activo',
        }
        resp = self.client.post('/api/proveedores/', payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('identificacion', resp.json())

    def test_crear_duplicado_inactivo_informa_inactive_duplicate(self):
        payload = {
            'tipo_identificacion': 'C',
            'identificacion': self.inactivo.identificacion,
            'nombre': 'Duplicado Inactivo',
        }
        resp = self.client.post('/api/proveedores/', payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('inactive_duplicate', resp.json())
        self.assertEqual(
            str(resp.json()['inactive_duplicate']['id']),
            str(self.inactivo.id),
        )

    def test_reactivar_proveedor(self):
        resp = self.client.post(f'/api/proveedores/{self.inactivo.id}/reactivar/')
        self.assertEqual(resp.status_code, 200)
        self.inactivo.refresh_from_db()
        self.assertTrue(self.inactivo.is_active)

    def test_reactivar_proveedor_activo_devuelve_400(self):
        resp = self.client.post(f'/api/proveedores/{self.activo.id}/reactivar/')
        self.assertEqual(resp.status_code, 400)

    def test_eliminar_proveedor_sin_relaciones_hace_hard_delete(self):
        resp = self.client.delete(f'/api/proveedores/{self.activo.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Proveedor.objects.filter(id=self.activo.id).exists())

    def test_exportar_pdf(self):
        resp = self.client.get('/api/proveedores/exportar-pdf/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')

    def test_exportar_excel(self):
        resp = self.client.get('/api/proveedores/export-excel/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheet', resp['Content-Type'])

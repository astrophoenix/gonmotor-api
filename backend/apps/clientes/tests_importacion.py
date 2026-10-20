from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook
from rest_framework.test import APIClient, APITestCase

from apps.clientes.excel_import import CLIENT_COLUMNS
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa
from apps.vehiculos.models import Vehiculo

URL = '/api/clientes/importar/'


def _xlsx(filas, columnas=None):
    columnas = columnas or [header for _, header in CLIENT_COLUMNS]
    claves = {header: key for key, header in CLIENT_COLUMNS}
    wb = Workbook()
    ws = wb.active
    ws.append(columnas)
    for fila in filas:
        ws.append([fila.get(claves[c], '') for c in columnas])
    buffer = BytesIO()
    wb.save(buffer)
    return SimpleUploadedFile(
        'clientes.xlsx', buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


class ImportacionClientesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='importador', password='x')
        self.empresa = Empresa.objects.create(
            nombre_comercial='Taller GON', razon_social='GON SA', ruc='1750000000004',
        )
        Cliente.objects.create(
            empresa=self.empresa, tipo_identificacion='C',
            identificacion='1710987654', nombre='Nombre anterior',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        fijar_empresa(self.client, str(self.empresa.uuid))
        self.filas = [
            {'identificacion': '1723492458', 'nombre': 'Andres Vinueza', 'placa': 'PBA1234', 'marca': 'Kia', 'modelo': 'Rio'},
            {'identificacion': '1710987654', 'nombre': 'Carlos Jaramillo', 'placa': 'GSD8812', 'marca': 'BMW'},
            {'identificacion': '1710987654', 'nombre': 'Carlos Jaramillo', 'placa': 'PCQ4410'},
            {'identificacion': '', 'nombre': 'Sin identificacion'},
            {'identificacion': '0925841736', 'nombre': 'Maria Rosales', 'placa': 'GBC1022', 'anio': 'dos mil'},
        ]

    def test_validar_no_guarda_y_devuelve_estado_por_fila(self):
        resp = self.client.post(URL + '?validar=1', {'archivo': _xlsx(self.filas)}, format='multipart')

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['total'], 5)
        # María se guarda aunque su vehículo falle: cuenta como nueva.
        self.assertEqual(data['nuevos'], 2)
        self.assertEqual(data['actualizados'], 1)
        self.assertEqual(data['exitosos'], 3)
        self.assertEqual(data['vehiculos'], 3)
        self.assertEqual(len(data['errores']), 2)
        estados = {f['fila']: f['estado'] for f in data['filas']}
        self.assertEqual(estados, {2: 'nuevo', 3: 'actualiza', 4: 'actualiza', 5: 'error', 6: 'error'})
        self.assertEqual(data['filas'][0]['vehiculo'], 'PBA1234 · Kia Rio')
        self.assertTrue(data['filas'][3]['motivo'])

        # Nada se escribió.
        self.assertFalse(Cliente.objects.filter(identificacion='1723492458').exists())
        self.assertEqual(Cliente.objects.get(identificacion='1710987654').nombre, 'Nombre anterior')
        self.assertFalse(Vehiculo.objects.filter(placa='PBA1234').exists())

    def test_validar_coincide_con_importacion_real(self):
        previa = self.client.post(URL + '?validar=1', {'archivo': _xlsx(self.filas)}, format='multipart').json()
        real = self.client.post(URL, {'archivo': _xlsx(self.filas)}, format='multipart')

        self.assertEqual(real.status_code, 200)
        data = real.json()
        self.assertNotIn('filas', data)
        for clave in ('total', 'exitosos', 'nuevos', 'actualizados', 'vehiculos'):
            self.assertEqual(data[clave], previa[clave], clave)
        self.assertEqual(Cliente.objects.get(identificacion='1710987654').nombre, 'Carlos Jaramillo')
        self.assertTrue(Vehiculo.objects.filter(placa='PBA1234').exists())

    def test_grupo_con_cliente_invalido_reporta_todas_sus_filas(self):
        filas = [{'identificacion': '1799999999', 'nombre': ''}, {'identificacion': '1799999999', 'nombre': '', 'placa': 'ABC1234'}]
        data = self.client.post(URL + '?validar=1', {'archivo': _xlsx(filas)}, format='multipart').json()
        self.assertEqual(sorted(e['fila'] for e in data['errores']), [2, 3])

    def test_columnas_faltantes(self):
        columnas = [h for _, h in CLIENT_COLUMNS if h not in ('Chasis/VIN', 'País de Origen')]
        resp = self.client.post(URL + '?validar=1', {'archivo': _xlsx(self.filas, columnas)}, format='multipart')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['columnas_faltantes'], ['Chasis/VIN', 'País de Origen'])

    def test_descarga_plantilla_y_errores(self):
        plantilla = self.client.get(URL + 'plantilla/')
        self.assertEqual(plantilla.status_code, 200)
        self.assertIn('spreadsheetml', plantilla['Content-Type'])

        errores = self.client.post(
            URL + 'errores/',
            {'errores': [{'fila': 5, 'identificacion': '', 'datos': {'nombre': 'X'}, 'motivo': 'Falta la identificación.'}]},
            format='json',
        )
        self.assertEqual(errores.status_code, 200)
        self.assertIn('spreadsheetml', errores['Content-Type'])

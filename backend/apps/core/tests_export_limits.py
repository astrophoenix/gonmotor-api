from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.export_limits import ExporteDemasiadoGrande, validar_limite_exporte
from apps.core.utils.pdf_export import PdfExportConfig, PdfExportService
from apps.core.testing import fijar_empresa


class ValidarLimiteExporteTests(SimpleTestCase):
    @override_settings(EXPORT_MAX_FILAS_PDF=5000)
    def test_en_el_limite_no_falla(self):
        validar_limite_exporte(5000, 'pdf')

    @override_settings(EXPORT_MAX_FILAS_EXCEL=20000)
    def test_sobre_el_limite_lanza_400_con_mensaje(self):
        with self.assertRaises(ExporteDemasiadoGrande) as ctx:
            validar_limite_exporte(20001, 'excel')
        self.assertEqual(ctx.exception.status_code, 400)
        mensaje = str(ctx.exception.detail)
        self.assertIn('20.001', mensaje)
        self.assertIn('20.000', mensaje)
        self.assertIn('Excel', mensaje)

    @override_settings(EXPORT_MAX_FILAS_PDF=1, EXPORT_MAX_FILAS_EXCEL=1)
    def test_servicios_validan_antes_de_generar(self):
        filas = ['a', 'b']
        with self.assertRaises(ExporteDemasiadoGrande):
            PdfExportService(PdfExportConfig('T', 't.pdf', [('Col', 1.0)]), filas).generate_response()
        with self.assertRaises(ExporteDemasiadoGrande):
            ExcelExportService(ExcelExportConfig('T', 't.xlsx', [('Col', 1.0)]), filas).generate_response()

    @override_settings(EXPORT_MAX_FILAS_PDF=2, EXPORT_MAX_FILAS_EXCEL=2)
    def test_servicios_generan_dentro_del_limite(self):
        filas = ['a', 'b']
        respuesta = PdfExportService(PdfExportConfig('T', 't.pdf', [('Col', 1.0)]), filas).generate_response()
        self.assertTrue(b''.join(respuesta.streaming_content).startswith(b'%PDF'))
        respuesta = ExcelExportService(ExcelExportConfig('T', 't.xlsx', [('Col', 1.0)]), filas).generate_response()
        self.assertEqual(respuesta.status_code, 200)


@override_settings(EXPORT_MAX_FILAS_PDF=0, EXPORT_MAX_FILAS_EXCEL=0)
class ExportesSobreElLimiteApiTests(TestCase):
    """Los exportes antiguos responden 400 (no 500) al superar el máximo."""

    def setUp(self):
        from apps.clientes.models import Cliente
        from apps.empresas.models import Empresa
        from apps.proveedores.models import Proveedor

        self.empresa = Empresa.objects.create(nombre_comercial='Límites', ruc='1750000000099')
        Cliente.objects.create(empresa=self.empresa, nombre='Cliente Uno', identificacion='0102030405')
        Proveedor.objects.create(
            empresa=self.empresa, tipo_identificacion='R', identificacion='1790000000099', nombre='Prov Uno'
        )
        user = get_user_model().objects.create_superuser(username='limites', password='x')
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        fijar_empresa(self.client, str(self.empresa.uuid))
    def test_responden_400_con_detalle(self):
        for url in (
            '/api/clientes/exportar-pdf/',
            '/api/clientes/export-excel/',
            '/api/proveedores/exportar-pdf/',
            '/api/proveedores/export-excel/',
        ):
            with self.subTest(url=url):
                respuesta = self.client.get(url)
                self.assertEqual(respuesta.status_code, 400, respuesta.content)
                self.assertIn('máximo', respuesta.json()['detail'])

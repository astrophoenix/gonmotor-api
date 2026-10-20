"""Panel de inicio por rol (`GET /api/core/dashboard-v2/`).

Todo usuario de la empresa tiene inicio. Cada bloque exige el permiso `ver` de
su dato, y tendencia financiera, facturación del mes y cotizaciones por aprobar
exigen además su acción especial. Lo que el rol no puede ver no viaja.
"""

from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import Rol, UsuarioEmpresa
from apps.authentication.permisos_repo import guardar_acciones_empresa
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo, vincular_empresa

ENDPOINT = '/api/core/dashboard-v2/'
KPIS_DINERO = ('facturacion_mes', 'facturacion_mes_anterior', 'variacion_facturacion_pct',
               'ticket_promedio', 'facturacion_periodo')
KPIS_COTIZACIONES = ('cotizaciones_pendientes', 'monto_cotizaciones_pendientes', 'tasa_aprobacion_pct')


class DashboardPorRolTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Taller Panel', ruc=str(uuid4().int % 10**13).zfill(13))
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Central', codigo_sucursal='001', direccion='x')
        cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000041')
        vehiculo = Vehiculo.objects.create(placa='PAN0001', marca='Kia', modelo='Rio')
        vincular_empresa(vehiculo, cls.empresa.pk)
        base = dict(empresa=cls.empresa, sucursal=cls.taller, cliente=cliente, vehiculo=vehiculo)
        OrdenTrabajo.objects.create(**base, numero_orden='PAN-OT', total=Decimal('150.00'))
        Cotizacion.objects.create(**base, numero_cotizacion='PAN-COT', estado='ENVIADA')

    def _get_como(self, rol):
        user = get_user_model().objects.create_user(username=f'panel_{rol}_{uuid4().hex[:6]}', password='x')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=self.empresa, rol=rol)
        relacion.talleres.set([self.taller])
        cliente = APIClient()
        cliente.force_authenticate(user=user)
        fijar_empresa(cliente, str(self.empresa.uuid))
        respuesta = cliente.get(ENDPOINT)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()

    def test_mecanico_tiene_inicio_operativo_sin_dinero(self):
        datos = self._get_como('MECANICO')
        secciones = datos['secciones']
        self.assertFalse(secciones['facturacion'])
        self.assertFalse(secciones['tendencia'])
        self.assertFalse(secciones['cotizaciones_por_aprobar'])
        self.assertTrue(secciones['ordenes'])
        self.assertTrue(secciones['vehiculos_en_taller'])
        for clave in KPIS_DINERO + KPIS_COTIZACIONES:
            self.assertNotIn(clave, datos['kpis'])
        self.assertEqual(datos['tendencia'], [])
        self.assertIn('vehiculos_en_taller', datos['kpis'])
        self.assertTrue(all('facturado' not in fila for fila in datos['distribucion']['tipo_trabajo']))

    def test_asesor_ve_el_panel_completo(self):
        datos = self._get_como('ASESOR')
        self.assertTrue(all(datos['secciones'].values()), datos['secciones'])
        self.assertEqual(datos['kpis']['facturacion_mes'], 150.0)
        self.assertEqual(datos['kpis']['cotizaciones_pendientes'], 1)
        self.assertTrue(datos['tendencia'])

    def test_la_empresa_puede_quitar_una_accion_al_rol(self):
        guardar_acciones_empresa(
            Rol.objects.get(codigo='ASESOR'),
            self.empresa.id,
            ['dashboard_tendencia_financiera', 'dashboard_cotizaciones_por_aprobar'],
        )
        datos = self._get_como('ASESOR')
        self.assertFalse(datos['secciones']['facturacion'])
        self.assertNotIn('facturacion_mes', datos['kpis'])
        self.assertTrue(datos['tendencia'])
        self.assertEqual(datos['kpis']['cotizaciones_pendientes'], 1)

    def test_la_empresa_puede_conceder_una_accion_al_mecanico(self):
        guardar_acciones_empresa(
            Rol.objects.get(codigo='MECANICO'), self.empresa.id, ['dashboard_cotizaciones_por_aprobar'],
        )
        datos = self._get_como('MECANICO')
        self.assertTrue(datos['secciones']['cotizaciones_por_aprobar'])
        self.assertEqual(datos['kpis']['cotizaciones_pendientes'], 1)
        self.assertNotIn('facturacion_mes', datos['kpis'])

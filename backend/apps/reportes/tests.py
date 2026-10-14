from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.empresas.models import Empresa, Taller
from apps.gastos.models import Gasto
from apps.ordenes.models import (
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioOrdenTrabajo,
    OrdenTrabajo,
)
from apps.vehiculos.models import Vehiculo


class RentabilidadReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_superuser(username='reportes_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Empresa Reportes',
            razon_social='Empresa Reportes SA',
            ruc='1799999999001',
            email_contacto='reportes@example.com',
        )
        cls.taller = Taller.objects.create(
            empresa=cls.empresa,
            nombre='Taller Central',
            direccion='Av. Principal 123',
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            nombre='Cliente de prueba',
            identificacion='0912345678',
        )
        cls.vehiculo = Vehiculo.objects.create(
            placa='REP-001',
            marca='Toyota',
            modelo='Corolla',
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def _orden(self, *, fecha_entrega, numero='OT-001', empresa=None, taller=None):
        orden = OrdenTrabajo.objects.create(
            empresa=empresa or self.empresa,
            sucursal=taller or self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden=numero,
            estado=OrdenTrabajo.EstadoOrden.ENTREGADO,
            fecha_entrega=fecha_entrega,
        )
        DetalleRepuestoOrdenTrabajo.objects.create(
            orden_trabajo=orden,
            descripcion='Repuesto',
            cantidad=Decimal('1.00'),
            precio_unitario=Decimal('40.00'),
            costo_unitario=Decimal('20.00'),
            descuento=Decimal('0.00'),
            subtotal=Decimal('0.00'),
        )
        DetalleServicioOrdenTrabajo.objects.create(
            orden_trabajo=orden,
            descripcion='Mano de obra',
            horas_aplicadas=Decimal('1.00'),
            precio_unitario=Decimal('60.00'),
            costo_unitario=Decimal('30.00'),
            descuento=Decimal('0.00'),
            subtotal=Decimal('0.00'),
        )
        orden.calcular_totales()
        return orden

    def _get(self, path, params=None):
        return self.api.get(
            path,
            params or {},
            HTTP_X_EMPRESA_ID=str(self.empresa.pk),
        )

    def test_taller_report_uses_net_revenue_and_line_cost_snapshots(self):
        self._orden(fecha_entrega=timezone.now())
        response = self._get(
            '/api/reportes/rentabilidad-taller/',
            {'taller_id': self.taller.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data['ingresos']['total']), Decimal('100.00'))
        self.assertEqual(Decimal(response.data['costos']['repuestos_vendidos']), Decimal('20.00'))
        self.assertEqual(Decimal(response.data['costos']['servicios']), Decimal('30.00'))
        self.assertEqual(Decimal(response.data['margen_bruto']), Decimal('50.00'))
        self.assertEqual(Decimal(response.data['utilidad_neta']), Decimal('50.00'))

    def test_period_filters_use_delivery_date_for_orders_and_date_for_expenses(self):
        hoy = timezone.localdate()
        self._orden(fecha_entrega=timezone.now(), numero='OT-001')
        self._orden(
            fecha_entrega=timezone.now() - timedelta(days=30),
            numero='OT-002',
        )
        Gasto.objects.create(
            empresa=self.empresa,
            taller=self.taller,
            fecha=hoy,
            monto=Decimal('10.00'),
        )
        Gasto.objects.create(
            empresa=self.empresa,
            taller=self.taller,
            fecha=hoy - timedelta(days=30),
            monto=Decimal('99.00'),
        )

        response = self._get(
            '/api/reportes/rentabilidad-taller/',
            {
                'taller_id': self.taller.pk,
                'fecha_desde': hoy.isoformat(),
                'fecha_hasta': hoy.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data['ingresos']['total']), Decimal('100.00'))
        self.assertEqual(Decimal(response.data['gastos']['total']), Decimal('10.00'))
        self.assertEqual(Decimal(response.data['utilidad_neta']), Decimal('40.00'))

    def test_delivery_date_filter_does_not_fall_back_to_order_creation_date(self):
        hoy = timezone.localdate()
        self._orden(fecha_entrega=timezone.now() - timedelta(days=30))

        response = self._get(
            '/api/reportes/rentabilidad-taller/',
            {
                'taller_id': self.taller.pk,
                'fecha_desde': hoy.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data['ingresos']['total']), Decimal('0.00'))

    def test_company_report_can_filter_by_taller_without_leaking_other_company_data(self):
        otra_empresa = Empresa.objects.create(
            nombre_comercial='Otra Empresa',
            razon_social='Otra Empresa SA',
            ruc='1799999999002',
            email_contacto='otra@example.com',
        )
        otro_taller = Taller.objects.create(
            empresa=otra_empresa,
            nombre='Otro Taller',
            direccion='Calle Secundaria 456',
        )
        self._orden(fecha_entrega=timezone.now())
        self._orden(
            fecha_entrega=timezone.now(),
            numero='OT-OTRA',
            empresa=otra_empresa,
            taller=otro_taller,
        )

        response = self._get(
            '/api/reportes/rentabilidad-empresa/',
            {'taller_id': self.taller.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data['ingresos']['total']), Decimal('100.00'))

    def test_invalid_filter_values_return_bad_request(self):
        response = self._get(
            '/api/reportes/rentabilidad-taller/',
            {
                'taller_id': self.taller.pk,
                'fecha_desde': 'not-a-date',
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('fecha_desde', response.data['error'])

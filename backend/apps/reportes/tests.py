from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
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

    # ── evolución mensual ───────────────────────────────────────────────────
    def test_evolucion_mensual_tiene_12_meses_y_cuadra_con_los_totales(self):
        hoy = timezone.localdate()
        self._orden(fecha_entrega=timezone.now())
        Gasto.objects.create(empresa=self.empresa, taller=self.taller, fecha=hoy, monto=Decimal('10.00'))

        response = self._get(
            '/api/reportes/rentabilidad-taller/',
            {
                'taller_id': self.taller.pk,
                'fecha_desde': hoy.replace(day=1).isoformat(),
                'fecha_hasta': hoy.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        meses = response.data['evolucion_mensual']
        self.assertEqual(len(meses), 12)
        self.assertEqual(meses[-1]['mes'], hoy.strftime('%Y-%m'))
        actual = meses[-1]
        # El mes del filtro coincide con las tarjetas KPI.
        self.assertEqual(Decimal(actual['ingresos']), Decimal(response.data['ingresos']['total']))
        self.assertEqual(Decimal(actual['costos']), Decimal(response.data['costos']['total_cmv']))
        self.assertEqual(Decimal(actual['gastos']), Decimal(response.data['gastos']['total']))
        self.assertEqual(Decimal(actual['utilidad_neta']), Decimal('40.00'))
        self.assertEqual(actual['margen_neto_pct'], 40.0)
        # Meses sin movimiento: en 0, no se omiten.
        self.assertEqual(Decimal(meses[0]['ingresos']), Decimal('0.00'))
        self.assertEqual(meses[0]['margen_neto_pct'], 0.0)

    def test_evolucion_termina_en_el_mes_de_fecha_hasta(self):
        response = self._get(
            '/api/reportes/rentabilidad-empresa/',
            {'fecha_hasta': '2026-03-15'},
        )
        meses = [fila['mes'] for fila in response.data['evolucion_mensual']]
        self.assertEqual(meses[0], '2025-04')
        self.assertEqual(meses[-1], '2026-03')

    def test_mes_con_gastos_y_sin_ingresos_no_tiene_margen(self):
        hoy = timezone.localdate()
        Gasto.objects.create(empresa=self.empresa, taller=self.taller, fecha=hoy, monto=Decimal('10.00'))
        response = self._get('/api/reportes/rentabilidad-empresa/')
        actual = response.data['evolucion_mensual'][-1]
        self.assertEqual(Decimal(actual['utilidad_neta']), Decimal('-10.00'))
        self.assertIsNone(actual['margen_neto_pct'])

    # ── alcance por taller ──────────────────────────────────────────────────
    def _api_no_admin(self, talleres):
        usuario = get_user_model().objects.create_user(username='reportes_cajero', password='x')
        relacion = UsuarioEmpresa.objects.create(user=usuario, empresa=self.empresa, rol='CAJERO')
        relacion.talleres.set(talleres)
        api = APIClient()
        api.force_authenticate(user=usuario)
        return api

    def test_no_admin_solo_ve_sus_talleres(self):
        otro_taller = Taller.objects.create(
            empresa=self.empresa, nombre='Taller Norte', codigo_sucursal='099', direccion='Norte'
        )
        self._orden(fecha_entrega=timezone.now(), numero='OT-A')
        self._orden(fecha_entrega=timezone.now(), numero='OT-B', taller=otro_taller)
        api = self._api_no_admin([self.taller])

        # Sin taller declarado: solo sus talleres permitidos.
        response = api.get('/api/reportes/rentabilidad-empresa/', HTTP_X_EMPRESA_ID=str(self.empresa.pk))
        self.assertEqual(Decimal(response.data['ingresos']['total']), Decimal('100.00'))

        # Forzar un taller no permitido no devuelve sus datos.
        response = api.get(
            '/api/reportes/rentabilidad-taller/',
            {'taller_id': otro_taller.pk},
            HTTP_X_EMPRESA_ID=str(self.empresa.pk),
        )
        self.assertEqual(Decimal(response.data['ingresos']['total']), Decimal('0.00'))
        self.assertEqual(
            sum(Decimal(fila['ingresos']) for fila in response.data['evolucion_mensual']),
            Decimal('0.00'),
        )

    def test_taller_de_otra_empresa_no_devuelve_datos(self):
        otra_empresa = Empresa.objects.create(
            nombre_comercial='Ajena', razon_social='Ajena SA',
            ruc='1799999999003', email_contacto='ajena@example.com',
        )
        taller_ajeno = Taller.objects.create(empresa=otra_empresa, nombre='Ajeno', direccion='X')
        self._orden(fecha_entrega=timezone.now(), numero='OT-AJ', empresa=otra_empresa, taller=taller_ajeno)

        response = self._get('/api/reportes/rentabilidad-taller/', {'taller_id': taller_ajeno.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data['ingresos']['total']), Decimal('0.00'))

    def test_sin_hasta_el_mes_actual_se_toma_completo(self):
        # Una entrega más adelante en el mes (aún sin «Hasta») cuenta en los
        # totales, así que también debe contar en el último mes de la evolución.
        hoy = timezone.localdate()
        fin_mes = (hoy.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        entrega = timezone.make_aware(datetime.combine(fin_mes, time(12, 0)))
        self._orden(fecha_entrega=entrega)

        response = self._get('/api/reportes/rentabilidad-empresa/')
        actual = response.data['evolucion_mensual'][-1]
        self.assertEqual(Decimal(actual['ingresos']), Decimal(response.data['ingresos']['total']))
        self.assertEqual(Decimal(actual['ingresos']), Decimal('100.00'))

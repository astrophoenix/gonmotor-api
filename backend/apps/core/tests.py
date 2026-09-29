"""Tests del endpoint del dashboard principal (`GET /api/core/dashboard/`).

Cubre la agregación de KPIs (vehículos en taller, pendientes de aprobación,
facturación del mes y stock bajo), la tendencia mensual, la distribución por
tipo de trabajo, las tablas del día y el aislamiento multi-tenant.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.empresas.models import Empresa
from apps.inventario.models import Repuesto
from apps.ordenes.models import OrdenTrabajo, RecepcionVehiculo
from apps.vehiculos.models import Vehiculo

User = get_user_model()

ENDPOINT = '/api/core/dashboard/'


def _ruc_unico():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class DashboardEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='dash_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Dashboard',
            ruc=_ruc_unico(),
            email_contacto='dashboard@test.com',
        )
        cls.empresa_ajena = Empresa.objects.create(
            nombre_comercial='Taller Ajeno',
            ruc=_ruc_unico(),
            email_contacto='ajeno@test.com',
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            tipo_identificacion='C',
            identificacion='1712345678',
            nombre='Cliente Dashboard',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='DSH001', marca='Kia', modelo='Sportage')
        cls.vehiculo.empresas.add(cls.empresa)

        cls.recepcion_hoy = RecepcionVehiculo.objects.create(
            empresa=cls.empresa,
            cliente=cls.cliente,
            vehiculo=cls.vehiculo,
            numero_recepcion='REC-DSH-0001',
            tipo_recepcion='REPARACION',
            estado='PENDIENTE',
        )
        cls.recepcion_vieja = RecepcionVehiculo.objects.create(
            empresa=cls.empresa,
            cliente=cls.cliente,
            vehiculo=cls.vehiculo,
            numero_recepcion='REC-DSH-0002',
            tipo_recepcion='MANTENIMIENTO',
            estado='ACEPTADA',
        )
        RecepcionVehiculo.objects.filter(pk=cls.recepcion_vieja.pk).update(
            fecha_ingreso=timezone.now() - timedelta(days=3)
        )
        cls.recepcion_rechazada = RecepcionVehiculo.objects.create(
            empresa=cls.empresa,
            cliente=cls.cliente,
            vehiculo=cls.vehiculo,
            numero_recepcion='REC-DSH-0003',
            estado='NO_ACEPTADA',
        )
        cls.recepcion_ajena = RecepcionVehiculo.objects.create(
            empresa=cls.empresa_ajena,
            cliente=cls.cliente,
            vehiculo=cls.vehiculo,
            numero_recepcion='REC-AJE-0001',
            estado='PENDIENTE',
        )

        cls.orden_proceso = cls._crear_orden('OT-DSH-0001', estado='EN_PROCESO', total='150.00')
        cls.orden_espera = cls._crear_orden(
            'OT-DSH-0002',
            estado='EN_ESPERA',
            motivo_espera='Aprobación del cliente',
            total='75.50',
        )
        cls.orden_urgent = cls._crear_orden(
            'OT-DSH-0003',
            estado='PENDIENTE',
            prioridad='URGENTE',
            tipo_trabajo='DIAGNOSTICO',
            total='40.00',
        )
        cls.orden_anulada = cls._crear_orden('OT-DSH-0004', estado='CANCELADO', total='999.99')
        cls.orden_mes_anterior = cls._crear_orden('OT-DSH-0005', estado='ENTREGADO', total='200.00')

        mes_anterior = (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)
        OrdenTrabajo.objects.filter(pk=cls.orden_mes_anterior.pk).update(
            created_at=timezone.now().replace(
                year=mes_anterior.year, month=mes_anterior.month, day=15
            )
        )

        cls.orden_ajena = OrdenTrabajo.objects.create(
            empresa=cls.empresa_ajena,
            cliente=cls.cliente,
            vehiculo=cls.vehiculo,
            numero_orden='OT-AJE-0001',
            estado='EN_PROCESO',
            total=Decimal('888.88'),
        )

        cls.repuesto_bajo = Repuesto.objects.create(
            empresa=cls.empresa,
            codigo='DSH-F001',
            nombre='Filtro de aceite',
            categoria='FILTROS',
            stock_actual=Decimal('2.00'),
            stock_minimo=Decimal('5.00'),
        )
        cls.repuesto_ok = Repuesto.objects.create(
            empresa=cls.empresa,
            codigo='DSH-F002',
            nombre='Filtro de aire',
            categoria='FILTROS',
            stock_actual=Decimal('20.00'),
            stock_minimo=Decimal('5.00'),
        )

    @classmethod
    def _crear_orden(cls, numero, **kwargs):
        return OrdenTrabajo.objects.create(
            empresa=cls.empresa,
            cliente=cls.cliente,
            vehiculo=cls.vehiculo,
            numero_orden=numero,
            prioridad=kwargs.pop('prioridad', 'MEDIA'),
            tipo_trabajo=kwargs.pop('tipo_trabajo', 'REPARACION'),
            motivo_espera=kwargs.pop('motivo_espera', None),
            total=Decimal(kwargs.pop('total', '0.00')),
            **kwargs,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def test_requiere_autenticacion(self):
        anonimo = APIClient()
        resp = anonimo.get(ENDPOINT)
        self.assertIn(resp.status_code, (401, 403))

    def test_devuelve_estructura_completa(self):
        resp = self.client.get(ENDPOINT)
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        for clave in (
            'generado_en', 'hoy', 'periodo', 'kpis', 'tendencia',
            'distribucion_servicios', 'recepciones_hoy', 'ultimos_ingresos',
            'ordenes_activas', 'stock_bajo',
        ):
            self.assertIn(clave, data)

        self.assertEqual(len(data['tendencia']), 6)
        for fila in data['tendencia']:
            self.assertEqual(
                set(fila), {'mes', 'etiqueta', 'etiqueta_larga', 'ingresos', 'ordenes'}
            )

    def test_kpi_vehiculos_en_taller(self):
        kpis = self.client.get(ENDPOINT).json()['kpis']
        # Cuenta las recepciones sin salida que no fueron rechazadas.
        self.assertEqual(kpis['vehiculos_en_taller'], 2)

    def test_kpi_pendientes_de_aprobacion(self):
        kpis = self.client.get(ENDPOINT).json()['kpis']
        self.assertEqual(kpis['ordenes_pendientes_aprobacion'], 1)

    def test_kpi_facturacion_del_mes_excluye_anuladas_y_mes_anterior(self):
        kpis = self.client.get(ENDPOINT).json()['kpis']
        # 150.00 + 75.50 + 40.00 (sin la anulada ni la del mes anterior).
        self.assertEqual(Decimal(str(kpis['facturacion_mes'])), Decimal('265.50'))
        self.assertEqual(Decimal(str(kpis['facturacion_mes_anterior'])), Decimal('200.00'))
        self.assertIsNotNone(kpis['variacion_facturacion_pct'])
        self.assertEqual(kpis['ordenes_mes'], 3)

    def test_kpi_alertas_de_stock_bajo(self):
        kpis = self.client.get(ENDPOINT).json()['kpis']
        self.assertEqual(kpis['alertas_stock_bajo'], 1)

        stock_bajo = self.client.get(ENDPOINT).json()['stock_bajo']
        self.assertEqual([fila['codigo'] for fila in stock_bajo], ['DSH-F001'])

    def test_tendencia_refleja_ordenes_del_mes_actual(self):
        tendencia = self.client.get(ENDPOINT).json()['tendencia']
        actual, anterior = tendencia[-1], tendencia[-2]

        self.assertEqual(actual['ordenes'], 3)
        self.assertEqual(Decimal(str(actual['ingresos'])), Decimal('265.50'))
        self.assertEqual(anterior['ordenes'], 1)
        self.assertEqual(Decimal(str(anterior['ingresos'])), Decimal('200.00'))
        self.assertTrue(all(fila['ordenes'] == 0 for fila in tendencia[:-2]))

    def test_distribucion_agrupa_por_tipo_de_trabajo(self):
        distribucion = self.client.get(ENDPOINT).json()['distribucion_servicios']
        por_clave = {fila['clave']: fila['ordenes'] for fila in distribucion}

        self.assertEqual(por_clave['DIAGNOSTICO'], 1)
        # Reparación: dos órdenes del mes actual + la del mes anterior.
        self.assertEqual(por_clave['REPARACION'], 3)
        self.assertNotIn('CANCELADO', por_clave)
        # Orden descendente por cantidad de órdenes.
        self.assertEqual(
            distribucion,
            sorted(distribucion, key=lambda fila: -fila['ordenes']),
        )

    def test_recepciones_del_dia_excluye_las_antiguas(self):
        data = self.client.get(ENDPOINT).json()

        # Hoy se registraron dos: la pendiente y la rechazada (la de hace 3 días no cuenta).
        self.assertEqual(data['kpis']['recepciones_hoy'], 2)
        self.assertEqual(
            sorted(fila['numero_recepcion'] for fila in data['recepciones_hoy']),
            ['REC-DSH-0001', 'REC-DSH-0003'],
        )
        self.assertLessEqual(len(data['ultimos_ingresos']), 8)
        self.assertIn(
            'REC-DSH-0002',
            [fila['numero_recepcion'] for fila in data['ultimos_ingresos']],
        )
        fila = next(
            item for item in data['recepciones_hoy']
            if item['numero_recepcion'] == 'REC-DSH-0001'
        )
        self.assertEqual(fila['cliente'], 'Cliente Dashboard')
        self.assertEqual(fila['placa'], 'DSH001')
        self.assertEqual(fila['estado_display'], 'Pendiente')

    def test_ordenes_activas_solo_urgentes_o_en_proceso(self):
        activas = self.client.get(ENDPOINT).json()['ordenes_activas']
        numeros = [fila['numero_orden'] for fila in activas]

        self.assertIn('OT-DSH-0001', numeros)  # en proceso
        self.assertIn('OT-DSH-0003', numeros)  # urgente
        self.assertNotIn('OT-DSH-0002', numeros)  # en espera sin prioridad urgente
        self.assertNotIn('OT-DSH-0004', numeros)  # anulada
        # La urgente encabeza la tabla.
        self.assertEqual(activas[0]['numero_orden'], 'OT-DSH-0003')
        self.assertEqual(activas[0]['prioridad_display'], 'Urgente / Emergencia')

    def test_aislamiento_por_empresa(self):
        data = self.client.get(ENDPOINT).json()

        numeros = [fila['numero_orden'] for fila in data['ordenes_activas']]
        self.assertNotIn('OT-AJE-0001', numeros)
        self.assertNotIn(
            'REC-AJE-0001',
            [fila['numero_recepcion'] for fila in data['ultimos_ingresos']],
        )
        self.assertNotIn(self.orden_ajena.id, [fila['id'] for fila in data['ordenes_activas']])

    def test_usuario_sin_empresa_devuelve_403(self):
        sin_empresa = User.objects.create_user(username='sin_empresa', password='x')
        cliente = APIClient()
        cliente.force_authenticate(user=sin_empresa)

        resp = cliente.get(ENDPOINT)
        self.assertEqual(resp.status_code, 403)

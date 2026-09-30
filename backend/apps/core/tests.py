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

from apps.citas.models import Cita
from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Empresa, Taller
from apps.inventario.models import Repuesto, Servicio
from apps.ordenes.models import (
    DetalleServicioInspeccion,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)
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


ENDPOINT_V2 = '/api/core/dashboard-v2/'


class DashboardV2EndpointTest(TestCase):
    """Tests del panel ejecutivo (`GET /api/core/dashboard-v2/`).

    Cubre el filtro de periodo (`fecha_desde`/`fecha_hasta`), el filtro por
    sucursal, los 5 KPIs, las distribuciones, las tablas del flujo operativo,
    la validación de parámetros y el aislamiento multi-tenant.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='dashv2_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Dashboard V2',
            ruc=_ruc_unico(),
            email_contacto='dashboardv2@test.com',
        )
        cls.empresa_ajena = Empresa.objects.create(
            nombre_comercial='Taller Ajeno V2',
            ruc=_ruc_unico(),
            email_contacto='ajenov2@test.com',
        )

        cls.taller = Taller.objects.create(
            empresa=cls.empresa,
            nombre='Taller Centro',
            codigo_sucursal='001',
            direccion='Av. Principal 123',
        )
        cls.taller_otro = Taller.objects.create(
            empresa=cls.empresa,
            nombre='Taller Norte',
            codigo_sucursal='002',
            direccion='Av. Norte 456',
        )
        cls.taller_ajeno = Taller.objects.create(
            empresa=cls.empresa_ajena,
            nombre='Taller Ajeno',
            codigo_sucursal='001',
            direccion='Otra ciudad',
        )

        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            tipo_identificacion='C',
            identificacion='1799988877',
            nombre='Cliente V2',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='DSV002', marca='Mazda', modelo='CX-5')
        cls.vehiculo.empresas.add(cls.empresa)

        ahora = timezone.now()

        # ── Recepciones ─────────────────────────────────────────────────────
        cls.rec_hoy = RecepcionVehiculo.objects.create(
            empresa=cls.empresa, sucursal=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_recepcion='REC-V2-0001', estado='PENDIENTE',
            nivel_combustible='1/2',
        )
        cls.rec_rechazada = RecepcionVehiculo.objects.create(
            empresa=cls.empresa, sucursal=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_recepcion='REC-V2-0002', estado='NO_ACEPTADA',
        )
        cls.rec_aceptada = RecepcionVehiculo.objects.create(
            empresa=cls.empresa, sucursal=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_recepcion='REC-V2-0003', estado='ACEPTADA',
        )
        cls.rec_inspeccionada = RecepcionVehiculo.objects.create(
            empresa=cls.empresa, sucursal=cls.taller_otro,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_recepcion='REC-V2-0004', estado='ACEPTADA',
        )
        cls.rec_ajena = RecepcionVehiculo.objects.create(
            empresa=cls.empresa_ajena,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_recepcion='REC-AJE-V2', estado='PENDIENTE',
        )
        RecepcionVehiculo.objects.filter(pk=cls.rec_rechazada.pk).update(
            fecha_ingreso=ahora - timedelta(minutes=5)
        )
        RecepcionVehiculo.objects.filter(pk=cls.rec_aceptada.pk).update(
            fecha_ingreso=ahora - timedelta(days=2)
        )
        RecepcionVehiculo.objects.filter(pk=cls.rec_inspeccionada.pk).update(
            fecha_ingreso=ahora - timedelta(days=3)
        )

        # ── Inspección con un servicio del catálogo ─────────────────────────
        cls.servicio = Servicio.objects.create(
            empresa=cls.empresa, codigo='SV-V2-001',
            nombre='Cambio de aceite', categoria='MECANICA',
        )
        cls.inspeccion = InspeccionVehiculo.objects.create(
            empresa=cls.empresa, sucursal=cls.taller_otro,
            recepcion=cls.rec_inspeccionada,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_inspeccion='INS-V2-0001',
            tipo_inspeccion='MANTENIMIENTO', estado='FINALIZADA',
            motivo_ingreso='Mantenimiento preventivo',
        )
        DetalleServicioInspeccion.objects.create(
            inspeccion=cls.inspeccion,
            servicio=cls.servicio,
            descripcion='Cambio de aceite',
        )

        # ── Órdenes de trabajo ──────────────────────────────────────────────
        cls.ot_proceso = cls._crear_orden('OT-V2-0001', estado='EN_PROCESO', total='150.00')
        cls.ot_espera = cls._crear_orden(
            'OT-V2-0002', estado='EN_ESPERA', total='75.50', sucursal=cls.taller_otro,
        )
        cls.ot_urgente = cls._crear_orden(
            'OT-V2-0003', estado='PENDIENTE', prioridad='URGENTE',
            tipo_trabajo='DIAGNOSTICO', total='40.00', subtotal_neto='40.00',
        )
        cls.ot_entregada = cls._crear_orden('OT-V2-0004', estado='ENTREGADO', total='200.00')
        cls.ot_anulada = cls._crear_orden('OT-V2-0005', estado='CANCELADO', total='999.99')
        cls.ot_vieja = cls._crear_orden('OT-V2-0006', estado='COMPLETADO', total='500.00')
        OrdenTrabajo.objects.filter(pk=cls.ot_vieja.pk).update(
            created_at=timezone.now() - timedelta(days=40),
            updated_at=timezone.now() - timedelta(days=40),
        )
        OrdenTrabajo.objects.filter(pk=cls.ot_entregada.pk).update(
            fecha_entrega=timezone.now()
        )
        cls.ot_ajena = OrdenTrabajo.objects.create(
            empresa=cls.empresa_ajena, cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_orden='OT-AJE-V2', estado='EN_PROCESO', total=Decimal('888.88'),
        )

        # ── Citas ───────────────────────────────────────────────────────────
        hoy = timezone.localdate()
        cls.cita_hoy = Cita.objects.create(
            empresa=cls.empresa, taller=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            fecha_cita=hoy, hora_cita='10:00', estado='PROGRAMADA',
            motivo='MANTENIMIENTO',
        )
        cls.cita_convertida = Cita.objects.create(
            empresa=cls.empresa, taller=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            fecha_cita=hoy, hora_cita='11:30', estado='COMPLETADA',
            motivo='REPARACION', recepcion_generada=cls.rec_aceptada,
        )
        cls.cita_cancelada = Cita.objects.create(
            empresa=cls.empresa, taller=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            fecha_cita=hoy, hora_cita='16:00', estado='CANCELADA',
            motivo='DIAGNOSTICO',
        )
        cls.cita_ajena = Cita.objects.create(
            empresa=cls.empresa_ajena,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            fecha_cita=hoy, hora_cita='09:00', estado='PROGRAMADA',
        )

        # ── Cotizaciones ────────────────────────────────────────────────────
        cls.cot_enviada = Cotizacion.objects.create(
            empresa=cls.empresa, sucursal=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_cotizacion='COT-V2-0001', estado='ENVIADA',
            total=Decimal('300.00'),
        )
        cls.cot_aceptada = Cotizacion.objects.create(
            empresa=cls.empresa, sucursal=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_cotizacion='COT-V2-0002', estado='ACEPTADA',
            total=Decimal('150.00'),
        )
        cls.cot_rechazada = Cotizacion.objects.create(
            empresa=cls.empresa, sucursal=cls.taller,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_cotizacion='COT-V2-0003', estado='RECHAZADA',
            total=Decimal('90.00'),
        )
        cls.cot_ajena = Cotizacion.objects.create(
            empresa=cls.empresa_ajena,
            cliente=cls.cliente, vehiculo=cls.vehiculo,
            numero_cotizacion='COT-AJE-V2', estado='ENVIADA',
            total=Decimal('777.00'),
        )

        # ── Inventario ──────────────────────────────────────────────────────
        cls.repuesto_bajo = Repuesto.objects.create(
            empresa=cls.empresa, sucursal=cls.taller,
            codigo='V2-F001', nombre='Filtro de aceite',
            categoria='FILTROS',
            stock_actual=Decimal('2.00'), stock_minimo=Decimal('5.00'),
        )
        cls.repuesto_ok = Repuesto.objects.create(
            empresa=cls.empresa, sucursal=cls.taller,
            codigo='V2-F002', nombre='Filtro de aire',
            categoria='FILTROS',
            stock_actual=Decimal('20.00'), stock_minimo=Decimal('5.00'),
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
            sucursal=kwargs.pop('sucursal', cls.taller),
            total=Decimal(kwargs.pop('total', '0.00')),
            **kwargs,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    # ── contrato ────────────────────────────────────────────────────────────
    def test_requiere_autenticacion(self):
        anonimo = APIClient()
        resp = anonimo.get(ENDPOINT_V2)
        self.assertIn(resp.status_code, (401, 403))

    def test_devuelve_estructura_completa(self):
        resp = self.client.get(ENDPOINT_V2)
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        for clave in (
            'generado_en', 'hoy', 'periodo', 'filtros', 'kpis', 'tendencia',
            'distribucion', 'recepciones', 'citas', 'ordenes_activas', 'stock_bajo',
        ):
            self.assertIn(clave, data)

        for clave in ('tipo_trabajo', 'estado_orden', 'categoria_servicio'):
            self.assertIn(clave, data['distribucion'])

        self.assertEqual(
            set(data['periodo']), {'desde', 'hasta', 'dias', 'granularidad'}
        )
        self.assertEqual(
            set(data['filtros']), {'sucursal', 'sucursal_nombre'}
        )
        self.assertIsNone(data['filtros']['sucursal'])
        self.assertEqual(data['periodo']['granularidad'], 'dia')

    def test_usuario_sin_empresa_devuelve_403(self):
        sin_empresa = User.objects.create_user(username='sin_empresa_v2', password='x')
        cliente = APIClient()
        cliente.force_authenticate(user=sin_empresa)

        self.assertEqual(cliente.get(ENDPOINT_V2).status_code, 403)

    # ── KPIs ────────────────────────────────────────────────────────────────
    def test_kpi_vehiculos_en_taller(self):
        kpis = self.client.get(ENDPOINT_V2).json()['kpis']
        # Sin filtro de sucursal: las 4 recepciones de la empresa salvo la rechazada.
        self.assertEqual(kpis['vehiculos_en_taller'], 3)

    def test_kpi_facturacion_y_ticket_promedio_del_periodo(self):
        kpis = self.client.get(ENDPOINT_V2).json()['kpis']
        # Por defecto el periodo es el mes en curso y excluye anuladas.
        self.assertEqual(Decimal(str(kpis['facturacion_periodo'])), Decimal('465.50'))
        self.assertEqual(kpis['ordenes_periodo'], 4)
        self.assertAlmostEqual(kpis['ticket_promedio'], 116.38, places=2)

    def test_kpi_pipeline_de_citas(self):
        kpis = self.client.get(ENDPOINT_V2).json()['kpis']
        self.assertEqual(kpis['citas_hoy'], 3)
        self.assertEqual(kpis['citas_pendientes_hoy'], 1)
        self.assertEqual(kpis['citas_periodo'], 3)
        self.assertEqual(kpis['citas_convertidas_periodo'], 1)
        self.assertEqual(kpis['tasa_conversion_cita_pct'], 33.3)

    def test_kpi_cotizaciones_por_aprobar(self):
        kpis = self.client.get(ENDPOINT_V2).json()['kpis']
        self.assertEqual(kpis['cotizaciones_pendientes'], 1)
        self.assertEqual(Decimal(str(kpis['monto_cotizaciones_pendientes'])), Decimal('300.00'))
        self.assertEqual(kpis['cotizaciones_aceptadas_periodo'], 1)
        self.assertEqual(kpis['tasa_aprobacion_pct'], 50.0)

    def test_kpi_alertas_de_inventario(self):
        kpis = self.client.get(ENDPOINT_V2).json()['kpis']
        self.assertEqual(kpis['alertas_stock_bajo'], 1)
        self.assertEqual(kpis['repuestos_agotados'], 0)

        stock_bajo = self.client.get(ENDPOINT_V2).json()['stock_bajo']
        self.assertEqual([fila['codigo'] for fila in stock_bajo], ['V2-F001'])

    # ── tendencia ───────────────────────────────────────────────────────────
    def test_tendencia_agrupa_por_dia_en_periodos_cortos(self):
        data = self.client.get(ENDPOINT_V2).json()
        hoy = timezone.localdate()

        self.assertEqual(data['periodo']['granularidad'], 'dia')
        self.assertEqual(len(data['tendencia']), hoy.day)
        self.assertEqual(data['tendencia'][-1]['clave'], hoy.isoformat())
        # Órdenes creadas hoy (las 4 del mes) y una entregada hoy.
        self.assertEqual(data['tendencia'][-1]['ordenes_creadas'], 4)
        self.assertEqual(data['tendencia'][-1]['ordenes_completadas'], 1)
        self.assertEqual(
            Decimal(str(data['tendencia'][-1]['ingresos'])), Decimal('465.50')
        )

    def test_tendencia_agrupa_por_mes_en_periodos_largos(self):
        hoy = timezone.localdate()
        desde = hoy - timedelta(days=40)
        resp = self.client.get(
            f'{ENDPOINT_V2}?fecha_desde={desde.isoformat()}&fecha_hasta={hoy.isoformat()}'
        )
        data = resp.json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['periodo']['granularidad'], 'mes')
        self.assertGreaterEqual(len(data['tendencia']), 2)
        self.assertLessEqual(len(data['tendencia']), 18)
        # La orden de hace 40 días entra en el periodo ampliado.
        self.assertEqual(Decimal(str(data['kpis']['facturacion_periodo'])), Decimal('965.50'))

    # ── distribuciones ──────────────────────────────────────────────────────
    def test_distribucion_por_tipo_y_estado(self):
        distribucion = self.client.get(ENDPOINT_V2).json()['distribucion']

        por_tipo = {fila['clave']: fila['ordenes'] for fila in distribucion['tipo_trabajo']}
        self.assertEqual(por_tipo['REPARACION'], 3)
        self.assertEqual(por_tipo['DIAGNOSTICO'], 1)
        self.assertNotIn('CANCELADO', por_tipo)

        estados = [fila['clave'] for fila in distribucion['estado_orden']]
        self.assertEqual(estados, ['PENDIENTE', 'EN_ESPERA', 'EN_PROCESO', 'ENTREGADO', 'CANCELADO'])

    def test_distribucion_por_categoria_del_catalogo(self):
        categorias = self.client.get(ENDPOINT_V2).json()['distribucion']['categoria_servicio']
        self.assertEqual(categorias, [
            {'clave': 'MECANICA', 'etiqueta': 'Mecánica General', 'servicios': 1},
        ])

    # ── tablas ──────────────────────────────────────────────────────────────
    def test_flujo_operativo_incluye_inspeccion_y_combustible(self):
        data = self.client.get(ENDPOINT_V2).json()
        recepciones = data['recepciones']

        self.assertEqual(
            [fila['numero_recepcion'] for fila in recepciones],
            ['REC-V2-0001', 'REC-V2-0002', 'REC-V2-0003', 'REC-V2-0004'],
        )
        por_numero = {fila['numero_recepcion']: fila for fila in recepciones}
        self.assertEqual(por_numero['REC-V2-0001']['nivel_combustible_display'], '1/2')

        # La recepción con inspección no permite crear otra.
        self.assertTrue(por_numero['REC-V2-0004']['tiene_inspeccion'])
        self.assertFalse(por_numero['REC-V2-0004']['puede_crear_inspeccion'])
        # La aceptada sin inspección sí.
        self.assertFalse(por_numero['REC-V2-0003']['tiene_inspeccion'])
        self.assertTrue(por_numero['REC-V2-0003']['puede_crear_inspeccion'])
        # La rechazada no.
        self.assertFalse(por_numero['REC-V2-0002']['puede_crear_inspeccion'])

    def test_citas_del_periodo(self):
        citas = self.client.get(ENDPOINT_V2).json()['citas']

        self.assertEqual(len(citas), 3)
        self.assertEqual([fila['hora'] for fila in citas], ['10:00', '11:30', '16:00'])
        convertida = next(fila for fila in citas if fila['id'] == self.cita_convertida.pk)
        self.assertTrue(convertida['convertida'])
        self.assertEqual(convertida['recepcion_id'], self.rec_aceptada.pk)

    def test_ordenes_criticas_ordenadas_por_urgencia(self):
        ordenes = self.client.get(ENDPOINT_V2).json()['ordenes_activas']

        self.assertEqual(
            [fila['numero_orden'] for fila in ordenes],
            ['OT-V2-0003', 'OT-V2-0001', 'OT-V2-0002'],
        )
        self.assertEqual(ordenes[0]['prioridad_display'], 'Urgente / Emergencia')
        self.assertIsNotNone(ordenes[0]['horas_transcurridas'])
        self.assertLess(ordenes[0]['horas_transcurridas'], 1)
        self.assertEqual(Decimal(str(ordenes[0]['subtotal_neto'])), Decimal('40.00'))
        self.assertIsNotNone(ordenes[0]['sucursal'])

    # ── filtros ─────────────────────────────────────────────────────────────
    def test_filtro_por_sucursal(self):
        resp = self.client.get(f'{ENDPOINT_V2}?sucursal={self.taller.pk}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data['filtros']['sucursal'], self.taller.pk)
        self.assertEqual(data['filtros']['sucursal_nombre'], 'Taller Centro')
        # Solo las recepciones de este taller (la del Taller Norte queda fuera).
        self.assertEqual(data['kpis']['vehiculos_en_taller'], 2)
        self.assertEqual(
            sorted(fila['numero_recepcion'] for fila in data['recepciones']),
            ['REC-V2-0001', 'REC-V2-0002', 'REC-V2-0003'],
        )
        # Solo órdenes de este taller: 150.00 + 40.00 + 200.00 (la de espera está
        # en el Taller Norte).
        self.assertEqual(Decimal(str(data['kpis']['facturacion_periodo'])), Decimal('390.00'))
        self.assertEqual(
            [fila['numero_orden'] for fila in data['ordenes_activas']],
            ['OT-V2-0003', 'OT-V2-0001'],
        )
        self.assertEqual(data['kpis']['alertas_stock_bajo'], 1)

    def test_filtro_por_fechas_acota_el_periodo(self):
        hoy = timezone.localdate()
        resp = self.client.get(
            f'{ENDPOINT_V2}?fecha_desde={hoy.isoformat()}&fecha_hasta={hoy.isoformat()}'
        )
        data = resp.json()

        self.assertEqual(data['periodo']['dias'], 1)
        # La orden de hace 40 días queda fuera del día seleccionado.
        self.assertEqual(Decimal(str(data['kpis']['facturacion_periodo'])), Decimal('465.50'))
        self.assertEqual(len(data['tendencia']), 1)

    def test_parametros_invalidos_devuelven_400(self):
        casos = (
            'fecha_desde=31/09/2026',
            'fecha_desde=2026-09-10&fecha_hasta=2026-09-01',
            'fecha_desde=2020-01-01&fecha_hasta=2026-01-01',
            'sucursal=abc',
            f'sucursal={self.taller_ajeno.pk}',
        )
        for consulta in casos:
            with self.subTest(consulta=consulta):
                self.assertEqual(self.client.get(f'{ENDPOINT_V2}?{consulta}').status_code, 400)

    # ── multi-tenant ────────────────────────────────────────────────────────
    def test_aislamiento_por_empresa(self):
        data = self.client.get(ENDPOINT_V2).json()

        self.assertNotIn(
            'REC-AJE-V2',
            [fila['numero_recepcion'] for fila in data['recepciones']],
        )
        self.assertNotIn(
            self.cita_ajena.pk,
            [fila['id'] for fila in data['citas']],
        )
        self.assertNotIn(
            self.ot_ajena.pk,
            [fila['id'] for fila in data['ordenes_activas']],
        )
        self.assertEqual(data['kpis']['cotizaciones_periodo'], 3)
        self.assertEqual(data['kpis']['cotizaciones_pendientes'], 1)
        self.assertEqual(data['kpis']['citas_hoy'], 3)

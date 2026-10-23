"""RF-13: autorización, totales, stock, auditoría y aislamiento."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.authentication.models import UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.core.models import EventoDocumento
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.inventario.models import Repuesto
from apps.pagos.models import PagoOrden
from apps.pagos.services import resumen_pagos
from apps.vehiculos.models import Vehiculo, vincular_empresa
from .models import DetalleServicioOrdenTrabajo, OrdenTrabajo


class AdicionalesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Adicionales', ruc=str(uuid4().int)[:13])
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='A1', codigo_sucursal='001', direccion='x')
        cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000034')
        cls.vehiculo = Vehiculo.objects.create(placa='ADC0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        cls.user = get_user_model().objects.create_user(username='adicional_admin', password='x')
        UsuarioEmpresa.objects.create(user=cls.user, empresa=cls.empresa, rol='ADMIN_EMPRESA')
        cls.orden = OrdenTrabajo.objects.create(empresa=cls.empresa, sucursal=cls.taller, cliente=cliente, vehiculo=cls.vehiculo, numero_orden='ADC-OT1', estado='EN_PROCESO', kilometraje=100)
        cls.base = DetalleServicioOrdenTrabajo.objects.create(orden_trabajo=cls.orden, descripcion='Base', precio_unitario=100, costo_unitario=20)
        cls.orden.calcular_totales()
        cls.repuesto = Repuesto.objects.create(empresa=cls.empresa, codigo='ADC-01', nombre='Pastillas', precio_venta=50, stock_actual=10, costo_referencial=10)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        fijar_empresa(self.api, self.empresa)
        self.api.credentials(HTTP_X_TALLER_ID=str(self.taller.uuid))

    def url(self, tipo, id=None, accion=''):
        return f'/api/ordenes/ot-{tipo}/' + (f'{id}/' if id else '') + (f'{accion}/' if accion else '')

    def crear(self, tipo='servicios', **extra):
        datos = dict(orden_trabajo=str(self.orden.uuid), descripcion='Propuesta nueva', precio_unitario='50', es_adicional=True)
        if tipo == 'repuestos': datos.update(codigo_repuesto=self.repuesto.codigo, cantidad='2')
        res = self.api.post(self.url(tipo), {**datos, **extra}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data

    def aprobar(self, tipo, linea, **datos):
        return self.api.post(self.url(tipo, linea['id'], 'aprobar'), {'medio_aprobacion': 'WHATSAPP', 'version_aprobacion': linea['version_aprobacion'], **datos}, format='json')

    def patch(self, tipo, linea, datos):
        return self.api.patch(self.url(tipo, linea['id']), datos, format='json')

    def test_propuestas_no_suman_totales_bases_iva_descuentos_ni_saldo(self):
        for tipo in ('servicios', 'repuestos'):
            linea = self.crear(tipo, descuento='10')
            self.assertFalse(linea['aprobado'])
            self.assertIsNone(linea['fecha_aprobacion'])
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.total, Decimal('115'))
        self.assertEqual(self.orden.descuento, 0)
        self.assertEqual(self.orden.subtotal_base_gravada, 100)
        self.assertEqual(resumen_pagos(self.orden)['saldo'], Decimal('115'))

    def test_lineas_anteriores_y_normales_se_conservan_aprobadas(self):
        self.assertTrue(self.base.aprobado)
        self.assertFalse(self.base.es_adicional)
        self.assertTrue(self.crear(es_adicional=False)['aprobado'])
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.total, Decimal('172.50'))

    def test_crud_no_autoriza_ni_cambia_condicion_adicional(self):
        linea = self.crear(aprobado=True, medio_aprobacion='WHATSAPP', aprobado_por=str(self.user.profile.uuid))
        self.assertFalse(linea['aprobado'])
        res = self.patch('servicios', linea, {'aprobado': True})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data['aprobado'])
        self.assertEqual(self.patch('servicios', linea, {'es_adicional': False}).status_code, 400)

    def test_aprobacion_sella_datos_y_suma_saldo_sin_mover_stock(self):
        fecha = timezone.now() - timedelta(minutes=10)
        for tipo in ('servicios', 'repuestos'):
            linea = self.crear(tipo)
            res = self.aprobar(tipo, linea, fecha_aprobacion=fecha.isoformat())
            self.assertEqual(res.status_code, 200, res.data)
            self.assertTrue(res.data['aprobado'])
            self.assertEqual(res.data['aprobado_por'], str(self.user.profile.uuid))
            self.assertEqual(res.data['medio_aprobacion'], 'WHATSAPP')
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.total, Decimal('287.50'))
        self.assertEqual(resumen_pagos(self.orden)['saldo'], Decimal('287.50'))
        self.repuesto.refresh_from_db()
        self.assertEqual(self.repuesto.stock_actual, 10)
        self.assertTrue(EventoDocumento.objects.filter(etiqueta__contains='Medio de aprobación').exists())

    def test_reintento_no_reescribe_aprobacion_ni_duplica_historial(self):
        linea = self.crear()
        uno = self.aprobar('servicios', linea)
        eventos = EventoDocumento.objects.count()
        dos = self.aprobar('servicios', linea, medio_aprobacion='LLAMADA')
        self.assertEqual(dos.status_code, 200, dos.data)
        self.assertEqual(dos.data['fecha_aprobacion'], uno.data['fecha_aprobacion'])
        self.assertEqual(dos.data['medio_aprobacion'], 'WHATSAPP')
        self.assertEqual(EventoDocumento.objects.count(), eventos)

    def test_medio_obligatorio_valido_y_fecha_no_futura(self):
        linea = self.crear()
        for datos in ({}, {'medio_aprobacion': 'SMS'}, {'medio_aprobacion': 'EMAIL', 'fecha_aprobacion': (timezone.now() + timedelta(days=1)).isoformat()}):
            res = self.api.post(self.url('servicios', linea['id'], 'aprobar'), {'version_aprobacion': linea['version_aprobacion'], **datos}, format='json')
            self.assertEqual(res.status_code, 400, res.data)
            campo = 'fecha_aprobacion' if 'fecha_aprobacion' in datos else 'medio_aprobacion'
            self.assertIn(campo, res.data)

    def test_pendiente_no_se_completa_ni_nace_completado(self):
        linea = self.crear()
        self.assertEqual(self.patch('servicios', linea, {'completado': True}).status_code, 400)
        res = self.api.post(self.url('servicios'), dict(orden_trabajo=str(self.orden.uuid), descripcion='X', precio_unitario=1, es_adicional=True, completado=True), format='json')
        self.assertEqual(res.status_code, 400, res.data)

    def test_repuesto_pendiente_no_despacha_y_aprobado_si(self):
        linea = self.crear('repuestos')
        url = self.url('repuestos', linea['id'], 'despachar')
        self.assertEqual(self.api.post(url, {}, format='json').status_code, 400)
        self.repuesto.refresh_from_db()
        self.assertEqual(self.repuesto.stock_actual, 10)
        self.assertEqual(self.aprobar('repuestos', linea).status_code, 200)
        res = self.api.post(url, {}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.repuesto.refresh_from_db()
        self.assertEqual(self.repuesto.stock_actual, 8)

    def test_cambios_comerciales_revocan_aprobacion(self):
        for tipo, campo, valor in [('servicios', 'precio_unitario', '60'), ('servicios', 'descripcion', 'Otra'), ('servicios', 'horas_aplicadas', '2'), ('repuestos', 'cantidad', '3'), ('repuestos', 'descuento', '1'), ('repuestos', 'iva_porcentaje', '0')]:
            with self.subTest(tipo=tipo, campo=campo):
                linea = self.crear(tipo)
                self.assertEqual(self.aprobar(tipo, linea).status_code, 200)
                res = self.patch(tipo, linea, {campo: valor})
                self.assertEqual(res.status_code, 200, res.data)
                self.assertFalse(res.data['aprobado'])
                self.assertEqual(res.data['medio_aprobacion'], '')
                self.assertIsNone(res.data['fecha_aprobacion'])
                self.assertIsNone(res.data['aprobado_por'])
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.total, Decimal('115'))

    def test_guardado_equivalente_y_avance_no_revocan_aprobacion(self):
        linea = self.crear()
        self.aprobar('servicios', linea)
        for datos in ({'precio_unitario': '50.00'}, {'completado': True}):
            res = self.patch('servicios', linea, datos)
            self.assertEqual(res.status_code, 200, res.data)
            self.assertTrue(res.data['aprobado'])

        repuesto = self.crear('repuestos', codigo_repuesto=None)
        aprobada = self.aprobar('repuestos', repuesto)
        self.assertEqual(aprobada.status_code, 200, aprobada.data)
        res = self.patch('repuestos', repuesto, {'codigo_repuesto': ''})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['aprobado'])
        self.assertEqual(res.data['version_aprobacion'], aprobada.data['version_aprobacion'])

    def test_completado_se_desmarca_antes_de_modificarlo(self):
        linea = self.crear()
        self.aprobar('servicios', linea)
        self.patch('servicios', linea, {'completado': True})
        self.assertEqual(self.patch('servicios', linea, {'precio_unitario': '60'}).status_code, 400)
        res = self.patch('servicios', linea, {'precio_unitario': '60', 'completado': False})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data['aprobado'])

    def test_despachado_se_devuelve_antes_de_modificarlo(self):
        linea = self.crear('repuestos')
        self.aprobar('repuestos', linea)
        self.api.post(self.url('repuestos', linea['id'], 'despachar'), {}, format='json')
        self.assertEqual(self.patch('repuestos', linea, {'precio_unitario': '60'}).status_code, 400)
        self.repuesto.refresh_from_db()
        self.assertEqual(self.repuesto.stock_actual, 8)

    def test_no_saldo_a_favor_y_rollback_del_cambio_y_su_auditoria(self):
        linea = self.crear()
        self.aprobar('servicios', linea)
        PagoOrden.objects.create(orden_trabajo=self.orden, monto=150, forma_pago='TRANSFERENCIA')
        eventos = EventoDocumento.objects.count()
        self.assertEqual(self.patch('servicios', linea, {'precio_unitario': '60'}).status_code, 400)
        actual = DetalleServicioOrdenTrabajo.objects.get(uuid=linea['id'])
        self.assertTrue(actual.aprobado)
        self.assertEqual(actual.precio_unitario, 50)
        self.assertEqual(EventoDocumento.objects.count(), eventos)

    def test_pendientes_impiden_completar_y_entregar(self):
        linea = self.crear()
        for estado in ('COMPLETADO', 'ENTREGADO'):
            res = self.api.patch(f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/', {'estado': estado, 'entrega_a_credito': True}, format='json')
            self.assertEqual(res.status_code, 400, res.data)
        self.aprobar('servicios', linea)
        res = self.api.patch(f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/', {'estado': 'COMPLETADO'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)

    def test_retirar_propuesta_no_cambia_total(self):
        linea = self.crear()
        self.assertEqual(self.api.delete(self.url('servicios', linea['id'])).status_code, 204)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.total, 115)

    def test_cerrada_no_admite_proponer_aprobar_modificar_o_retirar(self):
        linea = self.crear()
        for estado in ('COMPLETADO', 'ENTREGADO', 'CANCELADO'):
            OrdenTrabajo.objects.filter(pk=self.orden.pk).update(estado=estado)
            for metodo, url, datos in [('post', self.url('servicios'), {'orden_trabajo': str(self.orden.uuid), 'es_adicional': True, 'precio_unitario': '1', 'descripcion': 'X'}), ('post', self.url('servicios', linea['id'], 'aprobar'), {'medio_aprobacion': 'EMAIL'}), ('patch', self.url('servicios', linea['id']), {'descripcion': 'X'}), ('delete', self.url('servicios', linea['id']), {})]:
                res = getattr(self.api, metodo)(url, datos, format='json')
                self.assertEqual(res.status_code, 400, (estado, metodo, res.data))

    def test_consulta_no_aprueba(self):
        linea = self.crear()
        lector = get_user_model().objects.create_user(username='adicional_cajero')
        rel = UsuarioEmpresa.objects.create(user=lector, empresa=self.empresa, rol='CAJERO')
        rel.talleres.set([self.taller])
        self.api.force_authenticate(lector)
        self.assertEqual(self.aprobar('servicios', linea).status_code, 403)

    def test_empresa_y_taller_ajenos_no_aprueban(self):
        linea = self.crear()
        otro = Taller.objects.create(empresa=self.empresa, nombre='A2', codigo_sucursal='002', direccion='x')
        self.api.credentials(HTTP_X_TALLER_ID=str(otro.uuid))
        self.assertEqual(self.aprobar('servicios', linea).status_code, 404)
        otra = Empresa.objects.create(nombre_comercial='Ajena', ruc=str(uuid4().int)[:13])
        UsuarioEmpresa.objects.create(user=self.user, empresa=otra, rol='ADMIN_EMPRESA')
        fijar_empresa(self.api, otra)
        self.assertEqual(self.aprobar('servicios', linea).status_code, 404)
        self.assertEqual(self.patch('servicios', linea, {'descripcion': 'X'}).status_code, 404)

    def test_pdf_separa_propuestas_y_copia_rapida_las_excluye(self):
        linea = self.crear()
        from .pdf_orden import _DocumentoOrden
        documento = _DocumentoOrden(self.orden, self.empresa, self.taller, '', None)
        self.assertEqual(len(documento.pendientes), 1)
        self.assertEqual(len(documento.servicios), 1)
        self.assertEqual(self.api.get(f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/exportar-pdf/').status_code, 200)
        res = self.api.get(f'/api/ordenes/ordenes-trabajo/datos-vehiculo/?vehiculo={self.vehiculo.uuid}')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertNotIn(linea['descripcion'], str(res.data['visitas']))

    def test_propuesta_cambiada_no_se_aprueba_con_version_anterior(self):
        linea = self.crear()
        self.patch('servicios', linea, {'precio_unitario': '80'})
        res = self.aprobar('servicios', linea)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(DetalleServicioOrdenTrabajo.objects.get(uuid=linea['id']).aprobado)

    def test_costos_y_top_servicios_excluyen_pendientes(self):
        self.crear(costo_unitario='500')
        self.crear('repuestos', costo_unitario='500')
        # Verifica el cálculo incluso ante una orden histórica inconsistente.
        OrdenTrabajo.objects.filter(pk=self.orden.pk).update(estado='ENTREGADO', fecha_entrega=timezone.now())
        from apps.reportes.views import _get_rentabilidad
        from apps.reportes.resumen import _top_servicios
        resultado = _get_rentabilidad(self.empresa.pk, [self.taller.pk], None, None)
        self.assertEqual(resultado['costos']['total_cmv'], Decimal('20'))
        top = _top_servicios(self.empresa.pk, [self.taller.pk], timezone.localdate(), timezone.localdate())
        self.assertEqual([fila['descripcion'] for fila in top], ['Base'])

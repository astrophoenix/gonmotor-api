"""RF-12: precios/costos, stock, proveedores, adicionales y documentos."""
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from apps.authentication.models import UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.core.models import EventoDocumento
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.inventario.models import Repuesto, MovimientoInventario
from apps.pagos.models import PagoOrden
from apps.proveedores.models import Proveedor
from apps.vehiculos.models import Vehiculo, vincular_empresa
from .models import DetalleServicioOrdenTrabajo, DetalleRepuestoOrdenTrabajo, OrdenTrabajo


class LineasEspecialesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Especiales', ruc=str(uuid4().int)[:13])
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='A1', codigo_sucursal='001', direccion='x')
        cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000081')
        cls.vehiculo = Vehiculo.objects.create(placa='EXT0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        cls.user = get_user_model().objects.create_user(username='externo_admin', password='x')
        UsuarioEmpresa.objects.create(user=cls.user, empresa=cls.empresa, rol='ADMIN_EMPRESA')
        cls.orden = OrdenTrabajo.objects.create(empresa=cls.empresa, sucursal=cls.taller, cliente=cliente, vehiculo=cls.vehiculo, numero_orden='EXT-OT1', estado='EN_PROCESO', kilometraje=100)
        cls.base = DetalleServicioOrdenTrabajo.objects.create(orden_trabajo=cls.orden, descripcion='Trabajo interno', precio_unitario=100, costo_unitario=20)
        cls.orden.calcular_totales()
        cls.repuesto = Repuesto.objects.create(empresa=cls.empresa, codigo='EXT-01', nombre='Pastillas', precio_venta=50, stock_actual=10, costo_referencial=10)
        cls.proveedor = Proveedor.objects.create(empresa=cls.empresa, nombre='Rectificadora', identificacion='1700000082')

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        fijar_empresa(self.api, self.empresa)
        self.api.credentials(HTTP_X_TALLER_ID=str(self.taller.uuid))

    def url(self, tipo, linea=None, accion=''):
        return f'/api/ordenes/ot-{tipo}/' + (f"{linea['id']}/" if linea else '') + (f'{accion}/' if accion else '')

    def crear(self, tipo='servicios', esperado=201, **extra):
        datos = dict(orden_trabajo=str(self.orden.uuid), descripcion='Trabajo externo', precio_unitario='50')
        if tipo == 'servicios': datos.update(es_subcontratado=True, cantidad='2', costo_unitario='30', proveedor=str(self.proveedor.uuid))
        else: datos.update(provisto_cliente=True, codigo_repuesto=self.repuesto.codigo, cantidad='2')
        res = self.api.post(self.url(tipo), {**datos, **extra}, format='json')
        self.assertEqual(res.status_code, esperado, res.data)
        return res.data

    def patch(self, tipo, linea, datos, esperado=200):
        res = self.api.patch(self.url(tipo, linea), datos, format='json')
        self.assertEqual(res.status_code, esperado, res.data)
        return res.data

    def aprobar(self, tipo, linea):
        res = self.api.post(self.url(tipo, linea, 'aprobar'), {'version_aprobacion': linea['version_aprobacion'], 'medio_aprobacion': 'PRESENCIAL'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_cliente_precio_costo_descuento_iva_y_margen_cero(self):
        linea = self.crear('repuestos', precio_unitario='999', costo_unitario='88', descuento='4')
        for campo in ('precio_unitario', 'costo_unitario', 'costo_total', 'descuento', 'monto_iva', 'subtotal', 'margen_linea', 'cantidad_pendiente_despacho'):
            self.assertEqual(Decimal(linea[campo]), 0, campo)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.total, 115)
        self.patch('repuestos', linea, {'precio_unitario': '70', 'costo_unitario': '80'})
        actual = DetalleRepuestoOrdenTrabajo.objects.get(uuid=linea['id'])
        self.assertEqual(actual.precio_unitario, 0)
        self.assertEqual(actual.costo_unitario, 0)

    def test_modelo_tambien_normaliza_repuesto_cliente(self):
        linea = DetalleRepuestoOrdenTrabajo.objects.create(orden_trabajo=self.orden, descripcion='Cliente', provisto_cliente=True, precio_unitario=70, costo_unitario=50)
        linea.refresh_from_db()
        self.assertEqual(linea.subtotal, 0)
        self.assertEqual(linea.costo_total, 0)

    def test_cliente_admite_omitir_precio_normal_lo_exige(self):
        res = self.api.post(self.url('repuestos'), {'orden_trabajo': str(self.orden.uuid), 'descripcion': 'Cliente', 'provisto_cliente': True}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        res = self.api.post(self.url('repuestos'), {'orden_trabajo': str(self.orden.uuid), 'descripcion': 'Normal'}, format='json')
        self.assertEqual(res.status_code, 400, res.data)

    def test_cliente_con_catalogo_no_despacha_ni_mueve_stock(self):
        cantidad = MovimientoInventario.objects.count()
        linea = self.crear('repuestos', repuesto=str(self.repuesto.uuid))
        res = self.api.post(self.url('repuestos', linea, 'despachar'), {'autorizar_stock_negativo': True}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(self.api.delete(self.url('repuestos', linea)).status_code, 204)
        self.repuesto.refresh_from_db()
        self.assertEqual(self.repuesto.stock_actual, 10)
        self.assertEqual(MovimientoInventario.objects.count(), cantidad)

    def test_despachado_se_devuelve_antes_de_cambiar_origen(self):
        linea = self.crear('repuestos', provisto_cliente=False)
        res = self.api.post(self.url('repuestos', linea, 'despachar'), {}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.patch('repuestos', linea, {'provisto_cliente': True}, esperado=400)
        self.api.post(self.url('repuestos', linea, 'devolver'), {}, format='json')
        actual = self.patch('repuestos', linea, {'provisto_cliente': True})
        self.assertTrue(actual['provisto_cliente'])
        self.assertEqual(Decimal(actual['subtotal']), 0)
        self.repuesto.refresh_from_db()
        self.assertEqual(self.repuesto.stock_actual, 10)

    def test_origen_cliente_no_deja_saldo_a_favor_y_hace_rollback(self):
        linea = self.crear('repuestos', provisto_cliente=False)
        PagoOrden.objects.create(orden_trabajo=self.orden, monto=200, forma_pago='TRANSFERENCIA')
        eventos = EventoDocumento.objects.count()
        self.patch('repuestos', linea, {'provisto_cliente': True}, esperado=400)
        actual = DetalleRepuestoOrdenTrabajo.objects.get(uuid=linea['id'])
        self.assertFalse(actual.provisto_cliente)
        self.assertEqual(actual.precio_unitario, 50)
        self.assertEqual(EventoDocumento.objects.count(), eventos)

    def test_subcontrato_multiplica_cantidad_no_horas_y_calcula_margen(self):
        linea = self.crear(horas_aplicadas='9')
        self.assertEqual(Decimal(linea['subtotal']), 100)
        self.assertEqual(Decimal(linea['costo_total']), 60)
        self.assertEqual(Decimal(linea['margen_linea']), 40)
        self.assertEqual(linea['proveedor'], str(self.proveedor.uuid))
        self.assertEqual(linea['proveedor_nombre'], 'Rectificadora')
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.total, 230)
        from .pdf_orden import _DocumentoOrden
        doc = _DocumentoOrden(self.orden, self.empresa, self.taller, '', None)
        self.assertEqual(doc.horas, 1)

    def test_proveedor_y_costo_obligatorios(self):
        self.crear(proveedor=None, esperado=400)
        datos = {'orden_trabajo': str(self.orden.uuid), 'descripcion': 'Externo', 'precio_unitario': '50', 'es_subcontratado': True, 'proveedor': str(self.proveedor.uuid)}
        res = self.api.post(self.url('servicios'), datos, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('costo_unitario', res.data)

    def test_cantidades_e_importes_externos_validos(self):
        for campo, valor in [('cantidad', '0'), ('cantidad', '-1'), ('precio_unitario', '-1'), ('costo_unitario', '-1')]:
            with self.subTest(campo=campo, valor=valor): self.crear(esperado=400, **{campo: valor})
        linea = self.crear(cantidad='1', precio_unitario='0', costo_unitario='0')
        self.assertEqual(Decimal(linea['subtotal']), 0)

    def test_proveedor_ajeno_y_id_interno_se_rechazan(self):
        otra = Empresa.objects.create(nombre_comercial='Otra', ruc=str(uuid4().int)[:13])
        ajeno = Proveedor.objects.create(empresa=otra, nombre='Ajeno', identificacion='1700000083')
        self.crear(proveedor=str(ajeno.uuid), esperado=400)
        self.crear(proveedor=self.proveedor.pk, esperado=400)

    def test_proveedor_inactivo_no_se_selecciona_pero_se_conserva_historia(self):
        linea = self.crear()
        self.proveedor.is_active = False
        self.proveedor.save()
        self.crear(esperado=400)
        actual = self.patch('servicios', linea, {'completado': True})
        self.assertTrue(actual['completado'])
        self.assertEqual(actual['proveedor_nombre'], 'Rectificadora')

    def test_servicio_interno_no_acepta_proveedor(self):
        self.crear(es_subcontratado=False, esperado=400)
        linea = self.crear()
        actual = self.patch('servicios', linea, {'es_subcontratado': False})
        self.assertIsNone(actual['proveedor'])

    def test_mecanico_no_selecciona_proveedor_pero_completa_existente(self):
        linea = self.crear()
        mecanico = get_user_model().objects.create_user(username='externo_mecanico')
        rel = UsuarioEmpresa.objects.create(user=mecanico, empresa=self.empresa, rol='MECANICO')
        rel.talleres.set([self.taller])
        self.api.force_authenticate(mecanico)
        self.crear(esperado=403)
        self.patch('servicios', linea, {'completado': True})

    def test_cajero_no_crea_lineas(self):
        cajero = get_user_model().objects.create_user(username='externo_cajero')
        rel = UsuarioEmpresa.objects.create(user=cajero, empresa=self.empresa, rol='CAJERO')
        rel.talleres.set([self.taller])
        self.api.force_authenticate(cajero)
        self.crear(esperado=403)
        self.crear('repuestos', esperado=403)

    def test_subcontrato_no_crea_movimientos_de_dinero(self):
        from apps.caja.models import MovimientoCaja
        from apps.gastos.models import Gasto
        from apps.compras.models import Compra, PagoCompra
        modelos = (MovimientoCaja, Gasto, Compra, PagoCompra, PagoOrden)
        antes = [m.objects.count() for m in modelos]
        self.crear()
        self.assertEqual([m.objects.count() for m in modelos], antes)

    def test_adicional_externo_necesita_aprobacion_y_reaprueba_cantidad_y_proveedor(self):
        linea = self.crear(es_adicional=True)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.total, 115)
        linea = self.aprobar('servicios', linea)
        linea = self.patch('servicios', linea, {'cantidad': '3'})
        self.assertFalse(linea['aprobado'])
        linea = self.aprobar('servicios', linea)
        otro = Proveedor.objects.create(empresa=self.empresa, nombre='Otro', identificacion='1700000084')
        actual = self.patch('servicios', linea, {'proveedor': str(otro.uuid)})
        self.assertFalse(actual['aprobado'])
        self.assertNotEqual(actual['version_aprobacion'], linea['version_aprobacion'])

    def test_cambiar_origen_de_adicional_revoca_aprobacion(self):
        linea = self.crear('repuestos', provisto_cliente=False, es_adicional=True)
        linea = self.aprobar('repuestos', linea)
        actual = self.patch('repuestos', linea, {'provisto_cliente': True})
        self.assertFalse(actual['aprobado'])
        self.assertEqual(Decimal(actual['subtotal']), 0)

    def test_costos_externos_y_cliente_en_rentabilidad(self):
        self.crear('repuestos', costo_unitario='500')
        self.crear()
        from django.utils import timezone
        OrdenTrabajo.objects.filter(pk=self.orden.pk).update(estado='ENTREGADO', fecha_entrega=timezone.now())
        from apps.reportes.views import _get_rentabilidad
        resultado = _get_rentabilidad(self.empresa.pk, [self.taller.pk], None, None)
        self.assertEqual(resultado['ingresos']['total'], 200)
        self.assertEqual(resultado['costos']['total_cmv'], 80)
        self.assertEqual(resultado['utilidad_neta'], 120)

    def test_pdf_etiqueta_origen_y_subcontrato_y_copia_conserva_datos(self):
        self.crear('repuestos')
        externo = self.crear()
        from .pdf_orden import _DocumentoOrden, _descripcion_servicio
        doc = _DocumentoOrden(self.orden, self.empresa, self.taller, '', None)
        self.assertTrue(doc.repuestos[0].provisto_cliente)
        self.assertIn('Subcontratado: Rectificadora', _descripcion_servicio(DetalleServicioOrdenTrabajo.objects.get(uuid=externo['id'])))
        res = self.api.get(f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/exportar-pdf/')
        self.assertEqual(res.status_code, 200)
        res = self.api.get(f'/api/ordenes/ordenes-trabajo/datos-vehiculo/?vehiculo={self.vehiculo.uuid}')
        visita = res.data['visitas'][0]
        copiado = next(s for s in visita['servicios'] if s.get('es_subcontratado'))
        self.assertEqual(copiado['cantidad'], '2.00')
        self.assertEqual(copiado['costo_unitario'], '30.00')
        self.assertEqual(copiado['proveedor'], str(self.proveedor.uuid))
        self.assertTrue(visita['repuestos'][0]['provisto_cliente'])

    def test_historial_registra_origen_proveedor_costo_y_cantidad(self):
        linea = self.crear()
        self.patch('servicios', linea, {'costo_unitario': '40', 'cantidad': '3'})
        self.assertTrue(EventoDocumento.objects.filter(valor_nuevo__contains='Subcontratado: Rectificadora').exists())
        etiquetas = list(EventoDocumento.objects.values_list('etiqueta', flat=True))
        self.assertTrue(any('Costo unitario' in etiqueta for etiqueta in etiquetas))
        self.assertTrue(any('Cantidad externa' in etiqueta for etiqueta in etiquetas))

from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone

from apps.core.models import EventoDocumento
from apps.notificaciones.models import NotificacionProgramada
from apps.notificaciones.tests import MOCK_CONFIG
from apps.ordenes.models import DetalleRepuestoOrdenTrabajo

from .tests import ENDPOINT, PagosBase

URL_ORDENES = '/api/ordenes/ordenes-trabajo/'


class ReciboPdfTests(PagosBase):

    def test_descarga_el_recibo_de_un_pago(self):
        pago = self._pagar('40')
        respuesta = self.client.get(f'{ENDPOINT}{pago.data["id"]}/recibo/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        contenido = b''.join(respuesta.streaming_content)
        self.assertTrue(contenido.startswith(b'%PDF'))
        self.assertIn('recibo_A1-OT_', respuesta['Content-Disposition'])

    def test_recibo_de_devolucion_y_de_movimiento_anulado(self):
        self._pagar('40')
        devolucion = self._devolver('10')
        self.client.post(f'{ENDPOINT}{devolucion.data["id"]}/anular/', {'motivo': 'error'}, format='json')
        respuesta = self.client.get(f'{ENDPOINT}{devolucion.data["id"]}/recibo/')
        self.assertEqual(respuesta.status_code, 200)

    def test_recibo_fuera_de_alcance_responde_404(self):
        orden_a2 = self._orden(self.empresa, self.taller_a2, self.cliente, 'A2-OT')
        DetalleRepuestoOrdenTrabajo.objects.create(orden_trabajo=orden_a2, descripcion='X', precio_unitario=Decimal('10'))
        orden_a2.calcular_totales()
        pago = self._pagar('5', client=self._cliente(self.admin), orden=orden_a2)
        self.assertEqual(self.client.get(f'{ENDPOINT}{pago.data["id"]}/recibo/').status_code, 404)
        self.assertEqual(self._cliente(self.asesor).get(f'{ENDPOINT}{pago.data["id"]}/recibo/').status_code, 403)

    def test_saldo_tras_cada_movimiento(self):
        from .models import PagoOrden
        from .services import estado_tras_movimiento

        primero = PagoOrden.objects.get(uuid=self._pagar('40').data['id'])
        self._pagar('30')
        estado = estado_tras_movimiento(primero)
        self.assertEqual(estado['pagado_neto'], Decimal('40.00'))
        self.assertEqual(estado['saldo'], Decimal('75.00'))


@override_settings(**MOCK_CONFIG)
class EnviarReciboTests(PagosBase):

    def setUp(self):
        super().setUp()
        self.cliente.telefono = '0991234567'
        self.cliente.save()

    def test_envia_la_confirmacion_del_pago(self):
        pago = self._pagar('40', forma_pago='TRANSFERENCIA', referencia='TRX-1')
        respuesta = self.client.post(f'{ENDPOINT}{pago.data["id"]}/enviar-recibo/', {}, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        aviso = NotificacionProgramada.objects.get(orden=self.orden, tipo='RECIBO')
        self.assertIn('pago de $ 40.00 (Transferencia, comprobante TRX-1)', aviso.mensaje)
        self.assertIn('Saldo pendiente: $ 75.00', aviso.mensaje)

    def test_no_envia_un_movimiento_anulado(self):
        pago = self._pagar('40')
        self.client.post(f'{ENDPOINT}{pago.data["id"]}/anular/', {'motivo': 'error'}, format='json')
        respuesta = self.client.post(f'{ENDPOINT}{pago.data["id"]}/enviar-recibo/', {}, format='json')
        self.assertEqual(respuesta.status_code, 400)

    def test_asesor_sin_facturacion_no_envia(self):
        pago = self._pagar('40')
        respuesta = self._cliente(self.asesor).post(f'{ENDPOINT}{pago.data["id"]}/enviar-recibo/', {}, format='json')
        self.assertEqual(respuesta.status_code, 403)


class ComprobanteOrdenTests(PagosBase):

    def _url(self, orden=None):
        return f'{URL_ORDENES}{(orden or self.orden).uuid}/factura/'

    def _registrar(self, numero='001-001-000000123', client=None, orden=None, **extra):
        datos = {'tipo_comprobante': 'FACTURA', 'numero_factura': numero,
                 'fecha_factura': timezone.localdate().isoformat(), **extra}
        return (client or self.client).post(self._url(orden), datos, format='json')

    def test_cajero_registra_la_factura(self):
        respuesta = self._registrar()
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['numero_factura'], '001-001-000000123')
        self.assertEqual(respuesta.data['tipo_comprobante_display'], 'Factura')
        self.assertTrue(EventoDocumento.objects.filter(
            documento=self.orden.uuid, etiqueta='Número de comprobante', valor_nuevo='001-001-000000123',
        ).exists())

    def test_asesor_con_ordenes_pero_sin_facturacion_no_puede(self):
        self.assertEqual(self._registrar(client=self._cliente(self.asesor)).status_code, 403)

    def test_no_se_escribe_por_el_patch_de_la_orden(self):
        respuesta = self._cliente(self.admin).patch(
            f'{URL_ORDENES}{self.orden.uuid}/', {'numero_factura': '001-001-000000999'}, format='json',
        )
        self.assertEqual(respuesta.status_code, 200)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.numero_factura, '')

    def test_formato_y_fecha(self):
        for numero in ('1-1-123', '001001000000123', '001-001-00000012A'):
            respuesta = self._registrar(numero)
            self.assertEqual(respuesta.status_code, 400, numero)
            self.assertIn('numero_factura', respuesta.data)
        respuesta = self._registrar(fecha_factura=(timezone.localdate() + timedelta(days=1)).isoformat())
        self.assertIn('fecha_factura', respuesta.data)
        respuesta = self._registrar(fecha_factura=None)
        self.assertIn('fecha_factura', respuesta.data)

    def test_numero_unico_por_empresa_y_tipo(self):
        otra = self._orden(self.empresa, self.taller_a1, self.cliente, 'A1-OT2')
        self._registrar()
        respuesta = self._registrar(orden=otra)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('A1-OT', str(respuesta.data['numero_factura']))
        # La nota de venta lleva otra secuencia.
        self.assertEqual(self._registrar(orden=otra, tipo_comprobante='NOTA_VENTA').status_code, 200)
        # Volver a guardar la misma orden no choca consigo misma.
        self.assertEqual(self._registrar().status_code, 200)

    def test_quitar_el_comprobante(self):
        self._registrar()
        respuesta = self._registrar('', fecha_factura=None)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['numero_factura'], '')
        self.assertIsNone(respuesta.data['fecha_factura'])
        self.assertIsNone(respuesta.data['tipo_comprobante_display'])


class SinFacturaTests(PagosBase):

    def _numeros(self):
        respuesta = self._cliente(self.admin).get(URL_ORDENES, {'sin_factura': '1'})
        self.assertEqual(respuesta.status_code, 200)
        return {fila['numero_orden'] for fila in respuesta.data['results']}

    def test_cobradas_sin_comprobante(self):
        facturada = self._orden(self.empresa, self.taller_a1, self.cliente, 'A1-FAC')
        DetalleRepuestoOrdenTrabajo.objects.create(orden_trabajo=facturada, descripcion='X', precio_unitario=Decimal('10'))
        facturada.calcular_totales()
        self._orden(self.empresa, self.taller_a1, self.cliente, 'A1-SIN-PAGO')
        self.assertEqual(self._numeros(), set())

        self._pagar('20')
        self._pagar('5', orden=facturada)
        self.client.post(f'{URL_ORDENES}{facturada.uuid}/factura/', {
            'numero_factura': '001-001-000000001', 'fecha_factura': timezone.localdate().isoformat(),
        }, format='json')
        self.assertEqual(self._numeros(), {'A1-OT'})

    def test_un_pago_anulado_no_cuenta(self):
        pago = self._pagar('20')
        self.client.post(f'{ENDPOINT}{pago.data["id"]}/anular/', {'motivo': 'x'}, format='json')
        self.assertEqual(self._numeros(), set())

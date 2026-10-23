from decimal import Decimal

from django.test import TestCase

from apps.caja.models import MovimientoCaja, SesionCaja
from apps.gastos.models import Gasto
from apps.pagos.models import PagoOrden
from apps.pagos.tests import ENDPOINT as PAGOS, PagosBase

URL = '/api/caja/'


class CajaBase(PagosBase):
    """Parte de la caja de A1 cerrada (PagosBase la deja abierta)."""

    def setUp(self):
        super().setUp()
        SesionCaja.objects.filter(caja__taller=self.taller_a1).delete()
        self.admin_client = self._cliente(self.admin)

    def _abrir(self, monto='20', client=None, **extra):
        return (client or self.client).post(f'{URL}abrir/', {'monto_inicial': monto, **extra}, format='json')

    def _estado(self, client=None, **params):
        return (client or self.client).get(f'{URL}estado/', params)

    def _sesion_id(self):
        return self._estado().data['sesion']['id']

    def _cerrar(self, sesion_id, contado, observacion='', client=None):
        return (client or self.client).post(
            f'{URL}sesiones/{sesion_id}/cerrar/', {'efectivo_contado': contado, 'observacion': observacion}, format='json',
        )


class AbrirCajaTests(CajaBase):

    def test_sin_caja_abierta_no_se_cobra(self):
        respuesta = self._pagar('10')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('caja del taller A1 está cerrada', str(respuesta.data))

    def test_abrir_y_cobrar(self):
        respuesta = self._abrir('20')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertEqual(respuesta.data['taller']['nombre'], 'A1')
        self.assertEqual(respuesta.data['caja_nombre'], 'Caja principal')
        pago = self._pagar('10')
        self.assertEqual(pago.status_code, 201, pago.content)
        self.assertEqual(PagoOrden.objects.get(uuid=pago.data['id']).sesion_caja.caja.taller, self.taller_a1)

    def test_no_se_abre_dos_veces(self):
        self._abrir()
        self.assertEqual(self._abrir().status_code, 400)

    def test_admin_en_todos_los_talleres_debe_elegir_taller(self):
        respuesta = self._abrir(client=self.admin_client)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('taller', respuesta.data)
        respuesta = self._abrir(client=self.admin_client, taller=str(self.taller_a1.uuid))
        self.assertEqual(respuesta.status_code, 201, respuesta.content)

    def test_cajero_no_abre_caja_de_otro_taller(self):
        respuesta = self._abrir(taller=str(self.taller_a2.uuid))
        self.assertEqual(respuesta.status_code, 400)

    def test_asesor_sin_facturacion_no_entra(self):
        self.assertEqual(self._estado(client=self._cliente(self.asesor)).status_code, 403)

    def test_estado_propone_el_ultimo_contado(self):
        self._abrir('20')
        self._cerrar(self._sesion_id(), '20')
        estado = self._estado().data
        self.assertIsNone(estado['sesion'])
        self.assertEqual(estado['ultimo_cierre']['efectivo_contado'], '20.00')


class ResumenYCierreTests(CajaBase):

    def setUp(self):
        super().setUp()
        self._abrir('20')
        self.sesion_id = self._sesion_id()
        self._pagar('50')
        self._pagar('30', forma_pago='TRANSFERENCIA', referencia='TRX-1')
        self._devolver('5')

    def test_resumen_por_forma_y_efectivo_esperado(self):
        resumen = self._estado().data['sesion']['resumen']
        formas = {f['forma_pago']: f for f in resumen['por_forma']}
        self.assertEqual(formas['EFECTIVO']['pagos'], '50.00')
        self.assertEqual(formas['EFECTIVO']['devoluciones'], '5.00')
        self.assertEqual(formas['TRANSFERENCIA']['pagos'], '30.00')
        self.assertEqual(resumen['total_pagos'], '80.00')
        # 20 inicial + 50 - 5: la transferencia no entra en el efectivo.
        self.assertEqual(resumen['efectivo_esperado'], '65.00')

    def test_cierre_cuadrado(self):
        respuesta = self._cerrar(self.sesion_id, '65')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['estado'], 'CERRADA')
        self.assertEqual(respuesta.data['diferencia'], '0.00')
        self.assertEqual(respuesta.data['cerrada_por_nombre'], 'pagos_cajero')
        self.assertEqual(self._pagar('1').status_code, 400)

    def test_faltante_exige_observacion(self):
        respuesta = self._cerrar(self.sesion_id, '60')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('faltante de $ 5.00', str(respuesta.data['observacion']))
        respuesta = self._cerrar(self.sesion_id, '60', 'Vuelto mal dado')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['diferencia'], '-5.00')

    def test_no_se_anula_un_pago_de_una_caja_cerrada(self):
        pago = PagoOrden.objects.filter(sesion_caja__uuid=self.sesion_id, forma_pago='TRANSFERENCIA').get()
        self._cerrar(self.sesion_id, '65')
        respuesta = self.client.post(f'{PAGOS}{pago.uuid}/anular/', {'motivo': 'x'}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('reabrirla', str(respuesta.data))

    def test_devolucion_en_efectivo_no_supera_lo_que_hay(self):
        self._pagar('20', forma_pago='TRANSFERENCIA', referencia='TRX-2')
        respuesta = self._devolver('70')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('solo hay $ 65.00', str(respuesta.data))

    def test_reabrir_solo_admin_con_motivo(self):
        self._cerrar(self.sesion_id, '65')
        url = f'{URL}sesiones/{self.sesion_id}/reabrir/'
        self.assertEqual(self.client.post(url, {'motivo': 'x'}, format='json').status_code, 403)
        self.assertEqual(self.admin_client.post(url, {}, format='json').status_code, 400)
        respuesta = self.admin_client.post(url, {'motivo': 'Corregir un pago'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['estado'], 'ABIERTA')
        self.assertEqual(respuesta.data['reaperturas'], 1)
        self.assertEqual(self._pagar('1').status_code, 201)

    def test_no_se_reabre_si_hay_otra_abierta(self):
        self._cerrar(self.sesion_id, '65')
        self._abrir('65')
        respuesta = self.admin_client.post(f'{URL}sesiones/{self.sesion_id}/reabrir/', {'motivo': 'x'}, format='json')
        self.assertEqual(respuesta.status_code, 400)

    def test_historial_y_pdf(self):
        self._cerrar(self.sesion_id, '65')
        listado = self.client.get(f'{URL}sesiones/')
        self.assertEqual(listado.status_code, 200)
        self.assertEqual(listado.data['count'], 1)
        self.assertNotIn('resumen', listado.data['results'][0])
        detalle = self.client.get(f'{URL}sesiones/{self.sesion_id}/')
        self.assertEqual(len(detalle.data['pagos']), 3)
        pdf = self.client.get(f'{URL}sesiones/{self.sesion_id}/pdf/')
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')

    def test_cajero_no_ve_sesiones_de_otro_taller(self):
        respuesta = self.client.get(f'{URL}sesiones/')
        talleres = {fila['taller']['nombre'] for fila in respuesta.data['results']}
        self.assertEqual(talleres, {'A1'})


class MovimientosCajaTests(CajaBase):

    def setUp(self):
        super().setUp()
        self._abrir('20')
        self.sesion_id = self._sesion_id()
        self.url = f'{URL}sesiones/{self.sesion_id}/movimientos/'

    def _mover(self, tipo, monto, client=None, **extra):
        return (client or self.client).post(
            self.url, {'tipo': tipo, 'monto': monto, 'motivo': 'Compra de guaipe', **extra}, format='json',
        )

    def test_egreso_e_ingreso_cambian_el_efectivo(self):
        self.assertEqual(self._mover('EGRESO', '8').status_code, 201)
        self.assertEqual(self._mover('INGRESO', '3').status_code, 201)
        self.assertEqual(self._estado().data['sesion']['resumen']['efectivo_esperado'], '15.00')

    def test_egreso_no_supera_el_efectivo(self):
        respuesta = self._mover('EGRESO', '20.01')
        self.assertEqual(respuesta.status_code, 400)

    def test_egreso_como_gasto_exige_permiso_de_gastos(self):
        respuesta = self._mover('EGRESO', '5', categoria_gasto='INSUMOS')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('categoria_gasto', respuesta.data)

    def test_egreso_como_gasto_y_su_anulacion(self):
        admin = self.admin_client
        respuesta = self._mover('EGRESO', '5', client=admin, categoria_gasto='INSUMOS')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        movimiento = MovimientoCaja.objects.get(uuid=respuesta.data['id'])
        self.assertEqual(movimiento.gasto.monto, Decimal('5'))
        self.assertEqual(movimiento.gasto.taller, self.taller_a1)
        anulado = admin.post(f'{URL}movimientos/{movimiento.uuid}/anular/', {'motivo': 'Error'}, format='json')
        self.assertEqual(anulado.status_code, 200, anulado.content)
        self.assertFalse(Gasto.objects.filter(pk=movimiento.gasto_id).exists())
        self.assertEqual(self._estado().data['sesion']['resumen']['efectivo_esperado'], '20.00')

    def test_no_se_mueve_una_caja_cerrada(self):
        self._cerrar(self.sesion_id, '20')
        self.assertEqual(self._mover('INGRESO', '1').status_code, 400)

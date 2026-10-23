from decimal import Decimal
from django.contrib.auth import get_user_model
from apps.authentication.models import PermisoEmpresa, Recurso, Rol
from apps.caja.models import MovimientoCaja, SesionCaja
from apps.caja.services import resumen
from apps.gastos.models import Gasto
from apps.inventario.models import MovimientoInventario
from .tests import ComprasBase, ENDPOINT, PAGOS
from .models import Compra, PagoCompra


class CompraCajaTests(ComprasBase):
    def abrir(self, monto='200'):
        r = self.client.post('/api/caja/abrir/', {'monto_inicial':monto}, format='json')
        self.assertEqual(r.status_code,201,r.data)
        return SesionCaja.objects.get(uuid=r.data['id'])

    def comprar(self):
        r = self._comprar(condicion='CONTADO', forma_pago='EFECTIVO')
        self.assertEqual(r.status_code,201,r.data)
        return Compra.objects.get(uuid=r.data['id'])

    def test_sin_caja_o_sin_efectivo_no_persisten_compra_pago_stock_costo(self):
        antes = MovimientoInventario.objects.count()
        for preparar in [lambda:None,lambda:self.abrir('100')]:
            preparar()
            r = self._comprar(condicion='CONTADO', forma_pago='EFECTIVO')
            self.assertEqual(r.status_code,400,r.data)
            self.assertFalse(Compra.objects.exists()); self.assertFalse(PagoCompra.objects.exists())
            self.assertFalse(MovimientoCaja.objects.exists())
            self.assertEqual(MovimientoInventario.objects.count(),antes)
            self.filtro.refresh_from_db(); self.aceite.refresh_from_db()
            self.assertEqual(self.filtro.stock_actual,10)
            self.assertEqual(self.aceite.stock_actual,0)
            self.assertEqual(self.filtro.costo_referencial,4)

    def test_egreso_unico_vinculado_sin_gasto_y_cierre(self):
        sesion = self.abrir(); compra = self.comprar()
        pago = compra.pagos.get(); movimiento = pago.movimiento_caja
        self.assertIsNotNone(movimiento)
        self.assertEqual(movimiento.sesion_id,sesion.pk)
        self.assertEqual(movimiento.monto,Decimal('109'))
        self.assertEqual(movimiento.tipo,'EGRESO')
        self.assertIsNone(movimiento.gasto_id); self.assertFalse(Gasto.objects.exists())
        self.assertEqual(resumen(sesion)['efectivo_esperado'],Decimal('91'))
        r = self.client.post(f'/api/caja/sesiones/{sesion.uuid}/cerrar/',{'efectivo_contado':'91'},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(r.data['diferencia'],'0.00')
        api = self.client.get(f'{ENDPOINT}{compra.uuid}/')
        self.assertEqual(api.data['pagos'][0]['movimiento_caja'],str(movimiento.uuid))

    def test_anular_compra_revierte_egreso_stock_pago(self):
        sesion = self.abrir(); compra = self.comprar()
        r = self.client.post(f'{ENDPOINT}{compra.uuid}/anular/',{'motivo':'Error de factura'},format='json')
        self.assertEqual(r.status_code,200,r.data)
        pago = compra.pagos.get(); self.assertTrue(pago.anulado)
        self.assertTrue(pago.movimiento_caja.anulado)
        self.assertEqual(resumen(sesion)['efectivo_esperado'],200)
        self.filtro.refresh_from_db(); self.assertEqual(self.filtro.stock_actual,10)
        self.assertEqual(MovimientoCaja.objects.count(),1)

    def test_anular_pago_revierte_egreso_y_libera_saldo(self):
        sesion = self.abrir(); compra = self.comprar(); pago = compra.pagos.get()
        r = self.client.post(f'{PAGOS}{pago.uuid}/anular/',{'motivo':'Pago equivocado'},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(resumen(sesion)['efectivo_esperado'],200)
        self.assertEqual(self.client.get(f'{ENDPOINT}{compra.uuid}/').data['saldo'],'109.00')
        self.filtro.refresh_from_db(); self.assertEqual(self.filtro.stock_actual,20)
        r = self.client.post(f'{PAGOS}{pago.uuid}/anular/',{'motivo':'Otra vez'},format='json')
        self.assertEqual(r.status_code,400)
        self.assertEqual(resumen(sesion)['efectivo_esperado'],200)

    def test_sesion_cerrada_bloquea_anulacion_completa_y_pago_hasta_reabrir(self):
        sesion = self.abrir(); compra = self.comprar(); pago = compra.pagos.get()
        self.client.post(f'/api/caja/sesiones/{sesion.uuid}/cerrar/',{'efectivo_contado':'91'},format='json')
        for url in [f'{ENDPOINT}{compra.uuid}/anular/',f'{PAGOS}{pago.uuid}/anular/']:
            r = self.client.post(url,{'motivo':'Corregir'},format='json')
            self.assertEqual(r.status_code,400,r.data)
            pago.refresh_from_db(); compra.refresh_from_db()
            self.assertFalse(pago.anulado); self.assertEqual(compra.estado,'REGISTRADA')
            self.filtro.refresh_from_db(); self.assertEqual(self.filtro.stock_actual,20)
            self.assertFalse(pago.movimiento_caja.anulado)
        admin = self._cliente(self.admin)
        r=admin.post(f'/api/caja/sesiones/{sesion.uuid}/reabrir/',{'motivo':'Corregir compra'},format='json')
        self.assertEqual(r.status_code,200,r.data)
        r=self.client.post(f'{ENDPOINT}{compra.uuid}/anular/',{'motivo':'Corregir'},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(resumen(sesion)['efectivo_esperado'],200)

    def test_no_anula_egreso_directamente_desde_caja(self):
        self.abrir(); compra=self.comprar(); movimiento=compra.pagos.get().movimiento_caja
        r=self.client.post(f'/api/caja/movimientos/{movimiento.uuid}/anular/',{'motivo':'Intento directo'},format='json')
        self.assertEqual(r.status_code,400,r.data)
        self.assertIn('Compras',str(r.data))
        estado=self.client.get('/api/caja/estado/').data
        self.assertFalse(estado['sesion']['movimientos'][0]['anulable_desde_caja'])
        movimiento.refresh_from_db(); self.assertFalse(movimiento.anulado)

    def test_otro_taller_abierto_no_sirve_para_la_compra(self):
        admin=self._cliente(self.admin)
        admin.post('/api/caja/abrir/',{'monto_inicial':'500','taller':str(self.taller_a2.uuid)},format='json')
        r=self._comprar(condicion='CONTADO')
        self.assertEqual(r.status_code,400,r.data)
        self.assertFalse(Compra.objects.exists())

    def test_transferencia_y_credito_sin_caja_no_crean_egresos(self):
        r=self._comprar(condicion='CONTADO',forma_pago='TRANSFERENCIA')
        self.assertEqual(r.status_code,201,r.data)
        r=self._comprar(factura='OTRA',condicion='CREDITO')
        self.assertEqual(r.status_code,201,r.data)
        r=self.client.post(PAGOS,{'compra':r.data['id'],'monto':'10','forma_pago':'EFECTIVO'},format='json')
        self.assertEqual(r.status_code,201,r.data)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_mantiene_permiso_compras_sin_exigir_facturacion(self):
        self.abrir()
        PermisoEmpresa.objects.update_or_create(empresa=self.empresa,rol=Rol.objects.get(codigo='CAJERO'),recurso=Recurso.objects.get(codigo='facturacion'),defaults={'ver':False,'modificar':False})
        self.comprar()
        self.assertEqual(MovimientoCaja.objects.count(),1)

    def test_nuevo_pago_tras_anular_contado_crea_su_propio_egreso(self):
        sesion = self.abrir(); compra=self.comprar(); pago=compra.pagos.get()
        self.client.post(f'{PAGOS}{pago.uuid}/anular/',{'motivo':'Corregir pago'},format='json')
        r=self.client.post(PAGOS,{'compra':str(compra.uuid),'monto':'50','forma_pago':'EFECTIVO'},format='json')
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(resumen(sesion)['efectivo_esperado'],150)
        self.assertEqual(MovimientoCaja.objects.filter(anulado=False).count(),1)
        self.assertNotEqual(r.data['movimiento_caja'],str(pago.movimiento_caja.uuid))

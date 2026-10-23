from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.authentication.permisos_repo import sembrar_permisos_si_vacio
from apps.clientes.models import Cliente
from apps.core.models import EventoDocumento
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import DetalleRepuestoOrdenTrabajo, OrdenTrabajo
from apps.vehiculos.models import Vehiculo, vincular_empresa

from .models import MovimientoInventario, Repuesto

User = get_user_model()
Tipo = MovimientoInventario.Tipo


def _ruc():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class StockBase(TestCase):
    """Repuesto FIL-001 con 10 en stock y una OT del taller A1 que lo usa (3 u.)."""

    @classmethod
    def setUpTestData(cls):
        sembrar_permisos_si_vacio()
        cls.empresa = Empresa.objects.create(nombre_comercial='Taller Stock', ruc=_ruc())
        cls.otra_empresa = Empresa.objects.create(nombre_comercial='Ajena', ruc=_ruc())
        cls.taller_a1 = Taller.objects.create(empresa=cls.empresa, nombre='A1', codigo_sucursal='001', direccion='x')
        cls.taller_a2 = Taller.objects.create(empresa=cls.empresa, nombre='A2', codigo_sucursal='002', direccion='x')
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000021')
        cls.vehiculo = Vehiculo.objects.create(placa='STK0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)

        cls.admin = cls._usuario('stock_admin', cls.empresa, 'ADMIN_EMPRESA')
        cls.mecanico = cls._usuario('stock_mecanico', cls.empresa, 'MECANICO', [cls.taller_a1])
        cls.asesor = cls._usuario('stock_asesor', cls.empresa, 'ASESOR', [cls.taller_a1])
        cls.mecanico_a2 = cls._usuario('stock_mecanico_a2', cls.empresa, 'MECANICO', [cls.taller_a2])

    @staticmethod
    def _usuario(username, empresa, rol, talleres=None):
        user = User.objects.create_user(username=username, password='x', email=f'{username}@test.ec')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=empresa, rol=rol)
        if talleres:
            relacion.talleres.set(talleres)
        return user

    def setUp(self):
        self.repuesto = Repuesto.objects.create(
            empresa=self.empresa, codigo='FIL-001', nombre='Filtro', stock_actual=Decimal('10'),
            costo_referencial=Decimal('4.00'), precio_venta=Decimal('8.00'),
        )
        self.orden = OrdenTrabajo.objects.create(
            empresa=self.empresa, sucursal=self.taller_a1, cliente=self.cliente, vehiculo=self.vehiculo,
            numero_orden='A1-OT', estado='EN_PROCESO',
        )
        self.linea = DetalleRepuestoOrdenTrabajo.objects.create(
            orden_trabajo=self.orden, codigo_repuesto='fil-001', descripcion='Filtro',
            precio_unitario=Decimal('8'), cantidad=Decimal('3'),
        )
        self.orden.calcular_totales()
        self.client = self._cliente(self.mecanico)

    def _cliente(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        fijar_empresa(client, str(self.empresa.uuid))
        return client

    def _url(self, accion, linea=None):
        return f'/api/ordenes/ot-repuestos/{(linea or self.linea).uuid}/{accion}/'

    def _stock(self):
        self.repuesto.refresh_from_db()
        return self.repuesto.stock_actual


class DespachoTests(StockBase):

    def test_la_linea_se_vincula_al_catalogo_por_codigo(self):
        self.assertEqual(self.linea.repuesto_id, self.repuesto.pk)

    def test_cambiar_el_codigo_revincula_el_catalogo(self):
        otro = Repuesto.objects.create(empresa=self.empresa, codigo='ACE-001', nombre='Aceite')
        url = f'/api/ordenes/ot-repuestos/{self.linea.uuid}/'
        respuesta = self._cliente(self.admin).patch(url, {'codigo_repuesto': 'ACE-001'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['repuesto'], str(otro.uuid))
        respuesta = self._cliente(self.admin).patch(url, {'codigo_repuesto': ''}, format='json')
        self.assertIsNone(respuesta.data['repuesto'])

    def test_registrar_la_linea_no_mueve_stock(self):
        self.assertEqual(self._stock(), Decimal('10'))

    def test_despachar_descuenta_y_deja_movimiento(self):
        respuesta = self.client.post(self._url('despachar'), {}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['cantidad_despachada'], '3.00')
        self.assertEqual(respuesta.data['cantidad_pendiente_despacho'], '0.00')
        self.assertEqual(respuesta.data['despachado_por_nombre'], 'stock_mecanico')
        self.assertEqual(self._stock(), Decimal('7'))
        mov = MovimientoInventario.objects.get(repuesto=self.repuesto, tipo=Tipo.SALIDA_ORDEN)
        self.assertEqual(mov.cantidad, Decimal('-3'))
        self.assertEqual(mov.stock_resultante, Decimal('7'))
        self.assertEqual(mov.linea_orden_id, self.linea.pk)
        self.assertEqual(mov.usuario, self.mecanico)
        self.assertTrue(EventoDocumento.objects.filter(documento=self.orden.uuid, evento='despacho').exists())

    def test_despacho_parcial_y_tope_de_la_linea(self):
        self.assertEqual(self.client.post(self._url('despachar'), {'cantidad': '1'}).status_code, 200)
        respuesta = self.client.post(self._url('despachar'), {'cantidad': '3'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('cantidad', respuesta.data)
        self.assertEqual(self.client.post(self._url('despachar'), {}).status_code, 200)
        self.assertEqual(self._stock(), Decimal('7'))
        self.assertEqual(self.client.post(self._url('despachar'), {}).status_code, 400)

    def test_sin_stock_no_despacha(self):
        self.repuesto.stock_actual = Decimal('2')
        self.repuesto.save()
        respuesta = self.client.post(self._url('despachar'), {}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('stock_insuficiente', respuesta.data)
        self.assertEqual(self._stock(), Decimal('2'))

    def test_mecanico_no_puede_autorizar_stock_negativo(self):
        self.repuesto.stock_actual = Decimal('2')
        self.repuesto.save()
        respuesta = self.client.post(self._url('despachar'), {'autorizar_stock_negativo': True}, format='json')
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self._stock(), Decimal('2'))

    def test_admin_autoriza_stock_negativo(self):
        self.repuesto.stock_actual = Decimal('2')
        self.repuesto.save()
        respuesta = self._cliente(self.admin).post(
            self._url('despachar'), {'autorizar_stock_negativo': True}, format='json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(self._stock(), Decimal('-1'))

    def test_devolver_suma_al_almacen(self):
        self.client.post(self._url('despachar'), {})
        respuesta = self.client.post(self._url('devolver'), {'cantidad': '2', 'motivo': 'Sobró'})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['cantidad_despachada'], '1.00')
        self.assertEqual(self._stock(), Decimal('9'))
        mov = MovimientoInventario.objects.get(tipo=Tipo.DEVOLUCION_ORDEN)
        self.assertEqual((mov.cantidad, mov.motivo), (Decimal('2'), 'Sobró'))
        self.assertEqual(self.client.post(self._url('devolver'), {'cantidad': '5'}).status_code, 400)

    def test_linea_sin_catalogo_no_mueve_stock(self):
        libre = DetalleRepuestoOrdenTrabajo.objects.create(
            orden_trabajo=self.orden, descripcion='Repuesto traído por el cliente', precio_unitario=Decimal('0'),
        )
        respuesta = self.client.post(self._url('despachar', libre), {})
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('catálogo', str(respuesta.data))
        self.assertEqual(MovimientoInventario.objects.filter(tipo=Tipo.SALIDA_ORDEN).count(), 0)

    def test_linea_despachada_no_se_quita_ni_baja_de_lo_despachado(self):
        self.client.post(self._url('despachar'), {'cantidad': '2'})
        url = f'/api/ordenes/ot-repuestos/{self.linea.uuid}/'
        admin = self._cliente(self.admin)
        self.assertEqual(admin.delete(url).status_code, 400)
        respuesta = admin.patch(url, {'cantidad': '1'}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('cantidad', respuesta.data)
        self.assertEqual(admin.patch(url, {'cantidad': '5'}, format='json').status_code, 200)
        self.client.post(self._url('devolver'), {})
        self.assertEqual(admin.delete(url).status_code, 204)
        self.assertEqual(self._stock(), Decimal('10'))

    def test_anular_la_orden_exige_devolver_lo_despachado(self):
        self.client.post(self._url('despachar'), {})
        admin = self._cliente(self.admin)
        url = f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/'
        respuesta = admin.patch(url, {'estado': 'CANCELADO'}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('despachados', str(respuesta.data))
        self.client.post(self._url('devolver'), {})
        self.assertEqual(admin.patch(url, {'estado': 'CANCELADO'}, format='json').status_code, 200)
        # Una orden anulada no despacha.
        self.assertEqual(self.client.post(self._url('despachar'), {}).status_code, 400)

    def test_permisos_y_taller(self):
        # El asesor solo ve el inventario: no despacha.
        self.assertEqual(self._cliente(self.asesor).post(self._url('despachar'), {}).status_code, 403)
        # Un mecánico de otro taller no encuentra la línea.
        self.assertEqual(self._cliente(self.mecanico_a2).post(self._url('despachar'), {}).status_code, 404)
        self.assertEqual(self._stock(), Decimal('10'))


class AjusteYKardexTests(StockBase):

    def _url_rep(self, accion):
        return f'/api/repuestos/{self.repuesto.uuid}/{accion}/'

    def test_stock_inicial_abre_el_kardex(self):
        mov = MovimientoInventario.objects.get(repuesto=self.repuesto)
        self.assertEqual((mov.tipo, mov.cantidad, mov.motivo), (Tipo.AJUSTE, Decimal('10'), 'Stock inicial'))

    def test_ajuste_por_conteo(self):
        admin = self._cliente(self.admin)
        respuesta = admin.post(self._url_rep('ajustar-stock'), {'stock_contado': '8', 'motivo': 'Conteo mensual'})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['stock_actual'], '8.00')
        self.assertEqual(admin.post(self._url_rep('ajustar-stock'), {'stock_contado': '8', 'motivo': 'x'}).status_code, 400)
        self.assertEqual(admin.post(self._url_rep('ajustar-stock'), {'stock_contado': '5'}).status_code, 400)

        kardex = admin.get(self._url_rep('movimientos')).data['results']
        self.assertEqual([(m['tipo'], m['cantidad']) for m in kardex], [('AJUSTE', '-2.00'), ('AJUSTE', '10.00')])
        self.assertEqual(kardex[0]['usuario_nombre'], 'stock_admin')

    def test_el_stock_no_se_edita_directo(self):
        admin = self._cliente(self.admin)
        url = f'/api/repuestos/{self.repuesto.uuid}/'
        respuesta = admin.patch(url, {'stock_actual': '50'}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('stock_actual', respuesta.data)
        # Reenviar el mismo valor (el formulario manda todo) no falla.
        self.assertEqual(admin.patch(url, {'stock_actual': '10.00', 'nombre': 'Filtro X'}, format='json').status_code, 200)

    def test_asesor_no_ajusta(self):
        respuesta = self._cliente(self.asesor).post(self._url_rep('ajustar-stock'), {'stock_contado': '1', 'motivo': 'x'})
        self.assertEqual(respuesta.status_code, 403)

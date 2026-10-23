from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.inventario.models import MovimientoInventario, Repuesto
from apps.proveedores.models import Proveedor

from .models import Compra, PagoCompra

User = get_user_model()
ENDPOINT = '/api/compras/'
PAGOS = '/api/compras/pagos/'


def _ruc():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class ComprasBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Taller Compras', ruc=_ruc())
        cls.otra_empresa = Empresa.objects.create(nombre_comercial='Ajena', ruc=_ruc())
        cls.taller_a1 = Taller.objects.create(empresa=cls.empresa, nombre='A1', codigo_sucursal='001', direccion='x')
        cls.taller_a2 = Taller.objects.create(empresa=cls.empresa, nombre='A2', codigo_sucursal='002', direccion='x')
        cls.proveedor = Proveedor.objects.create(empresa=cls.empresa, nombre='Repuestos del Valle', identificacion=_ruc())
        cls.proveedor_ajeno = Proveedor.objects.create(empresa=cls.otra_empresa, nombre='Ajeno', identificacion=_ruc())
        cls.admin = cls._usuario('compras_admin', cls.empresa, 'ADMIN_EMPRESA')
        cls.cajero = cls._usuario('compras_cajero', cls.empresa, 'CAJERO', [cls.taller_a1])
        cls.mecanico = cls._usuario('compras_mecanico', cls.empresa, 'MECANICO', [cls.taller_a1])
        cls.cajero_a2 = cls._usuario('compras_cajero_a2', cls.empresa, 'CAJERO', [cls.taller_a2])

    @staticmethod
    def _usuario(username, empresa, rol, talleres=None):
        user = User.objects.create_user(username=username, password='x', email=f'{username}@test.ec')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=empresa, rol=rol)
        if talleres:
            relacion.talleres.set(talleres)
        return user

    def setUp(self):
        # 10 u. a $4: el promedio parte de aquí.
        self.filtro = Repuesto.objects.create(
            empresa=self.empresa, codigo='FIL-001', nombre='Filtro', stock_actual=Decimal('10'),
            costo_referencial=Decimal('4.00'),
        )
        self.aceite = Repuesto.objects.create(empresa=self.empresa, codigo='ACE-001', nombre='Aceite')
        self.client = self._cliente(self.cajero)

    def _cliente(self, user, empresa=None):
        client = APIClient()
        client.force_authenticate(user=user)
        fijar_empresa(client, str((empresa or self.empresa).uuid))
        return client

    def _comprar(self, client=None, condicion='CREDITO', factura='001-001-000000123', **extra):
        datos = {
            'proveedor': str(self.proveedor.uuid),
            'numero_factura': factura,
            'fecha': '2026-10-20',
            'condicion': condicion,
            'detalles': [
                {'repuesto': str(self.filtro.uuid), 'cantidad': '10', 'costo_unitario': '6.00', 'iva_porcentaje': '0.15'},
                {'repuesto': str(self.aceite.uuid), 'cantidad': '4', 'costo_unitario': '10.00', 'iva_porcentaje': '0'},
            ],
            **extra,
        }
        return (client or self.client).post(ENDPOINT, datos, format='json')

    def _refrescar(self):
        self.filtro.refresh_from_db()
        self.aceite.refresh_from_db()


class RegistrarCompraTests(ComprasBase):

    def test_compra_a_credito_suma_stock_y_costo_promedio(self):
        respuesta = self._comprar()
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        # 60 + 9 de IVA + 40 = 109
        self.assertEqual(respuesta.data['total'], '109.00')
        self.assertEqual(respuesta.data['saldo'], '109.00')
        self.assertEqual(respuesta.data['pagos'], [])
        self._refrescar()
        self.assertEqual(self.filtro.stock_actual, Decimal('20'))
        # (10 × 4 + 10 × 6) / 20 = 5
        self.assertEqual(self.filtro.costo_referencial, Decimal('5.00'))
        # Sin stock previo manda el costo de la compra.
        self.assertEqual((self.aceite.stock_actual, self.aceite.costo_referencial), (Decimal('4'), Decimal('10.00')))
        mov = MovimientoInventario.objects.get(repuesto=self.filtro, tipo='ENTRADA_COMPRA')
        self.assertEqual((mov.cantidad, mov.costo_unitario, mov.stock_resultante), (Decimal('10'), Decimal('6'), Decimal('20')))
        self.assertEqual(Compra.objects.get().taller, self.taller_a1)

    def test_compra_de_contado_queda_pagada(self):
        respuesta = self._comprar(condicion='CONTADO', forma_pago='TRANSFERENCIA')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertEqual(respuesta.data['saldo'], '0.00')
        self.assertEqual(respuesta.data['pagos'][0]['forma_pago'], 'TRANSFERENCIA')

    def test_validaciones(self):
        self.assertEqual(self._comprar(detalles=[]).status_code, 400)
        repetido = [{'repuesto': str(self.filtro.uuid), 'cantidad': '1', 'costo_unitario': '1'}] * 2
        self.assertEqual(self._comprar(detalles=repetido).status_code, 400)
        iva_mal = [{'repuesto': str(self.filtro.uuid), 'cantidad': '1', 'costo_unitario': '1', 'iva_porcentaje': '0.12'}]
        self.assertEqual(self._comprar(detalles=iva_mal).status_code, 400)
        self.assertEqual(self._comprar(proveedor=str(self.proveedor_ajeno.uuid)).status_code, 400)
        self.assertEqual(Compra.objects.count(), 0)
        self._refrescar()
        self.assertEqual(self.filtro.stock_actual, Decimal('10'))

    def test_factura_duplicada(self):
        self._comprar()
        respuesta = self._comprar()
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('numero_factura', respuesta.data)

    def test_no_se_edita_ni_se_borra(self):
        compra = self._comprar().data
        url = f'{ENDPOINT}{compra["id"]}/'
        self.assertEqual(self.client.patch(url, {'observaciones': 'x'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)


class PagosYSaldoTests(ComprasBase):

    def setUp(self):
        super().setUp()
        self.compra = self._comprar().data

    def _pagar(self, monto, **extra):
        return self.client.post(
            PAGOS, {'compra': self.compra['id'], 'forma_pago': 'EFECTIVO', 'monto': monto, **extra}, format='json',
        )

    def test_abono_y_tope_del_saldo(self):
        self.assertEqual(self._pagar('100').status_code, 201)
        respuesta = self._pagar('9.01')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('monto', respuesta.data)
        self.assertEqual(self._pagar('9').status_code, 201)
        self.assertEqual(self.client.get(f'{ENDPOINT}{self.compra["id"]}/').data['saldo'], '0.00')

    def test_cuentas_por_pagar(self):
        self._pagar('9')
        otra = self._comprar(factura='002')
        self.assertEqual(otra.status_code, 201)
        respuesta = self.client.get(ENDPOINT, {'con_saldo': 'true'})
        resumen = respuesta.data['resumen']
        self.assertEqual(resumen['total_por_pagar'], '209.00')
        self.assertEqual(resumen['compras_con_saldo'], 2)
        self.assertEqual(resumen['por_proveedor'][0]['nombre'], 'Repuestos del Valle')

    def test_anular_pago_libera_saldo(self):
        pago = self._pagar('50').data
        respuesta = self.client.post(f'{PAGOS}{pago["id"]}/anular/', {'motivo': 'Error'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(self.client.get(f'{ENDPOINT}{self.compra["id"]}/').data['saldo'], '109.00')

    def test_transferencia_exige_referencia(self):
        self.assertEqual(self._pagar('1', forma_pago='TRANSFERENCIA').status_code, 400)


class AnularCompraTests(ComprasBase):

    def test_anular_revierte_stock_y_anula_pagos(self):
        abierta = self.client.post('/api/caja/abrir/', {'monto_inicial': '200'}, format='json')
        self.assertEqual(abierta.status_code, 201, abierta.data)
        compra = self._comprar(condicion='CONTADO').data
        respuesta = self.client.post(f'{ENDPOINT}{compra["id"]}/anular/', {'motivo': 'Factura equivocada'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['estado'], 'ANULADA')
        self.assertEqual(respuesta.data['saldo'], '0.00')
        self.assertTrue(all(p['anulado'] for p in respuesta.data['pagos']))
        self._refrescar()
        self.assertEqual((self.filtro.stock_actual, self.aceite.stock_actual), (Decimal('10'), Decimal('0')))
        self.assertEqual(MovimientoInventario.objects.filter(tipo='ANULACION_COMPRA').count(), 2)
        # La misma factura se puede volver a registrar.
        self.assertEqual(self._comprar(condicion='CONTADO').status_code, 201)

    def test_no_se_anula_si_el_stock_ya_salio(self):
        compra = self._comprar().data
        self.aceite.refresh_from_db()
        self.aceite.stock_actual = Decimal('1')
        self.aceite.save()
        respuesta = self.client.post(f'{ENDPOINT}{compra["id"]}/anular/', {'motivo': 'x'}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Compra.objects.get().estado, 'REGISTRADA')
        self.filtro.refresh_from_db()
        self.assertEqual(self.filtro.stock_actual, Decimal('20'))

    def test_anular_exige_motivo(self):
        compra = self._comprar().data
        self.assertEqual(self.client.post(f'{ENDPOINT}{compra["id"]}/anular/', {}, format='json').status_code, 400)


class PermisosComprasTests(ComprasBase):

    def test_mecanico_sin_acceso(self):
        mecanico = self._cliente(self.mecanico)
        self.assertEqual(mecanico.get(ENDPOINT).status_code, 403)
        self.assertEqual(self._comprar(client=mecanico).status_code, 403)

    def test_cajero_de_otro_taller_no_ve_la_compra(self):
        compra = self._comprar().data
        otro = self._cliente(self.cajero_a2)
        self.assertEqual(otro.get(ENDPOINT).data['count'], 0)
        self.assertEqual(otro.get(f'{ENDPOINT}{compra["id"]}/').status_code, 404)
        pago = otro.post(PAGOS, {'compra': compra['id'], 'forma_pago': 'EFECTIVO', 'monto': '1'}, format='json')
        self.assertEqual(pago.status_code, 400)
        self.assertEqual(PagoCompra.objects.count(), 0)

    def test_otra_empresa_no_usa_mis_repuestos(self):
        cajero_b = self._usuario('compras_cajero_b', self.otra_empresa, 'CAJERO')
        ajeno = self._cliente(cajero_b, self.otra_empresa)
        respuesta = self._comprar(client=ajeno, proveedor=str(self.proveedor_ajeno.uuid))
        self.assertEqual(respuesta.status_code, 400)
        self._refrescar()
        self.assertEqual(self.filtro.stock_actual, Decimal('10'))

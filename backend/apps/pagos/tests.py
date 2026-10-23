from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.caja.services import abrir as abrir_caja
from apps.clientes.models import Cliente
from apps.core.models import EventoDocumento
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.notificaciones.models import NotificacionProgramada
from apps.notificaciones.tests import MOCK_CONFIG
from apps.ordenes.models import DetalleRepuestoOrdenTrabajo, OrdenTrabajo
from apps.vehiculos.models import Vehiculo, vincular_empresa

from .models import PagoOrden

User = get_user_model()
ENDPOINT = '/api/pagos/'


def _ruc():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class PagosBase(TestCase):
    """Orden de $115 (repuesto de $100 + IVA 15 %) en el taller A1."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Taller Pagos', ruc=_ruc())
        cls.otra_empresa = Empresa.objects.create(nombre_comercial='Ajena', ruc=_ruc())
        cls.taller_a1 = Taller.objects.create(empresa=cls.empresa, nombre='A1', codigo_sucursal='001', direccion='x')
        cls.taller_a2 = Taller.objects.create(empresa=cls.empresa, nombre='A2', codigo_sucursal='002', direccion='x')
        cls.taller_ajeno = Taller.objects.create(
            empresa=cls.otra_empresa, nombre='B1', codigo_sucursal='001', direccion='x',
        )
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000011')
        cls.cliente_ajeno = Cliente.objects.create(
            empresa=cls.otra_empresa, nombre='Ajeno', identificacion='1700000012',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='PAG0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        vincular_empresa(cls.vehiculo, cls.otra_empresa.pk)

        cls.cajero = cls._usuario('pagos_cajero', cls.empresa, 'CAJERO', [cls.taller_a1])
        cls.asesor = cls._usuario('pagos_asesor', cls.empresa, 'ASESOR', [cls.taller_a1])
        cls.admin = cls._usuario('pagos_admin', cls.empresa, 'ADMIN_EMPRESA')
        cls.cajero_ajeno = cls._usuario('pagos_cajero_b', cls.otra_empresa, 'CAJERO', [cls.taller_ajeno])
        # Con la caja cerrada no se cobra: los tests de pagos parten con la caja abierta.
        for taller in (cls.taller_a1, cls.taller_a2, cls.taller_ajeno):
            abrir_caja(taller, monto_inicial=Decimal('0'), usuario=None)

    @staticmethod
    def _usuario(username, empresa, rol, talleres=None):
        user = User.objects.create_user(username=username, password='x', email=f'{username}@test.ec')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=empresa, rol=rol)
        if talleres:
            relacion.talleres.set(talleres)
        return user

    def setUp(self):
        self.orden = self._orden(self.empresa, self.taller_a1, self.cliente, 'A1-OT')
        self.repuesto = DetalleRepuestoOrdenTrabajo.objects.create(
            orden_trabajo=self.orden, descripcion='Kit de embrague', precio_unitario=Decimal('100'),
        )
        self.orden.calcular_totales()
        self.client = self._cliente(self.cajero)

    def _orden(self, empresa, taller, cliente, numero, **extra):
        return OrdenTrabajo.objects.create(
            empresa=empresa, sucursal=taller, cliente=cliente, vehiculo=self.vehiculo, numero_orden=numero, **extra,
        )

    def _cliente(self, user, empresa=None):
        client = APIClient()
        client.force_authenticate(user=user)
        fijar_empresa(client, str((empresa or self.empresa).uuid))
        return client

    def _pagar(self, monto, client=None, orden=None, **extra):
        datos = {
            'orden_trabajo': str((orden or self.orden).uuid), 'forma_pago': 'EFECTIVO', 'monto': str(monto), **extra,
        }
        return (client or self.client).post(ENDPOINT, datos, format='json')

    def _devolver(self, monto, **extra):
        return self._pagar(monto, tipo='DEVOLUCION', **extra)

    def _resumen(self):
        respuesta = self._cliente(self.admin).get(f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.data


class RegistrarPagoTests(PagosBase):

    def test_abono_parcial_y_saldo(self):
        respuesta = self._pagar('40')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertTrue(respuesta.data['es_anticipo'])
        self.assertEqual(respuesta.data['registrado_por_nombre'], 'pagos_cajero')
        resumen = self._resumen()
        self.assertEqual(resumen['total'], '115.00')
        self.assertEqual(resumen['pagado_neto'], '40.00')
        self.assertEqual(resumen['saldo'], '75.00')
        self.assertEqual(resumen['estado_pago'], 'PARCIAL')

    def test_pago_completo_deja_la_orden_pagada(self):
        self.orden.estado = 'COMPLETADO'
        self.orden.save()
        respuesta = self._pagar('115')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertFalse(respuesta.data['es_anticipo'])
        resumen = self._resumen()
        self.assertEqual(resumen['saldo'], '0.00')
        self.assertEqual(resumen['estado_pago'], 'PAGADO')

    def test_orden_sin_pagos_esta_pendiente(self):
        self.assertEqual(self._resumen()['estado_pago'], 'PENDIENTE')

    def test_no_se_acepta_pagar_mas_que_el_saldo(self):
        self._pagar('100')
        respuesta = self._pagar('15.01')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('saldo pendiente', str(respuesta.data['monto']))
        self.assertEqual(PagoOrden.objects.filter(orden_trabajo=self.orden).count(), 1)

    def test_orden_pagada_no_admite_mas_pagos(self):
        self._pagar('115')
        respuesta = self._pagar('1')
        self.assertEqual(respuesta.status_code, 400)

    def test_monto_debe_ser_positivo(self):
        for monto in ('0', '-5'):
            respuesta = self._pagar(monto)
            self.assertEqual(respuesta.status_code, 400, monto)
            self.assertIn('monto', respuesta.data)

    def test_transferencia_exige_referencia(self):
        respuesta = self._pagar('10', forma_pago='TRANSFERENCIA')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('referencia', respuesta.data)
        respuesta = self._pagar('10', forma_pago='TRANSFERENCIA', referencia=' 000123 ')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertEqual(respuesta.data['referencia'], '000123')

    def test_la_fecha_la_fija_el_servidor(self):
        respuesta = self._pagar('10', fecha='2020-01-01T10:00:00Z')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertFalse(respuesta.data['fecha'].startswith('2020'))

    def test_orden_anulada_no_admite_pagos(self):
        self.orden.estado = 'CANCELADO'
        self.orden.save()
        respuesta = self._pagar('10')
        self.assertEqual(respuesta.status_code, 400)

    def test_el_pago_queda_en_el_historial_de_la_orden(self):
        self._pagar('20', forma_pago='TRANSFERENCIA', referencia='TRX-9')
        evento = EventoDocumento.objects.get(documento=self.orden.uuid, evento=EventoDocumento.Evento.PAGO)
        self.assertEqual(evento.valor_nuevo, 'Transferencia $ 20.00 (TRX-9)')

    def test_listar_pagos_de_una_orden(self):
        otra = self._orden(self.empresa, self.taller_a1, self.cliente, 'A1-OT2')
        DetalleRepuestoOrdenTrabajo.objects.create(orden_trabajo=otra, descripcion='X', precio_unitario=Decimal('10'))
        otra.calcular_totales()
        self._pagar('10')
        self._pagar('5', orden=otra)
        respuesta = self.client.get(ENDPOINT, {'orden': str(self.orden.uuid)})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual([fila['monto'] for fila in respuesta.data['results']], ['10.00'])


class DevolucionTests(PagosBase):

    def test_devolucion_hasta_lo_pagado(self):
        self._pagar('50')
        self.assertEqual(self._devolver('50.01').status_code, 400)
        respuesta = self._devolver('20', forma_pago='EFECTIVO')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertFalse(respuesta.data['es_anticipo'])
        resumen = self._resumen()
        self.assertEqual(resumen['total_devuelto'], '20.00')
        self.assertEqual(resumen['pagado_neto'], '30.00')
        self.assertEqual(resumen['saldo'], '85.00')

    def test_bajar_el_total_bajo_lo_pagado_exige_devolucion_primero(self):
        self._pagar('115')
        linea = f'/api/ordenes/ot-repuestos/{self.repuesto.uuid}/'
        admin = self._cliente(self.admin)

        respuesta = admin.patch(linea, {'precio_unitario': '60'}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('devolución de $ 46.00', str(respuesta.data))
        self.repuesto.refresh_from_db()
        self.assertEqual(self.repuesto.precio_unitario, Decimal('100.00'))

        self.assertEqual(self._devolver('46').status_code, 201)
        respuesta = admin.patch(linea, {'precio_unitario': '60'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        resumen = self._resumen()
        self.assertEqual(resumen['total'], '69.00')
        self.assertEqual(resumen['saldo'], '0.00')
        self.assertEqual(resumen['estado_pago'], 'PAGADO')

    def test_quitar_una_linea_pagada_exige_devolucion(self):
        self._pagar('10')
        respuesta = self._cliente(self.admin).delete(f'/api/ordenes/ot-repuestos/{self.repuesto.uuid}/')
        self.assertEqual(respuesta.status_code, 400)
        self.assertTrue(DetalleRepuestoOrdenTrabajo.objects.filter(pk=self.repuesto.pk).exists())

    def test_anular_la_orden_exige_devolver_lo_cobrado(self):
        self._pagar('30')
        admin = self._cliente(self.admin)
        url = f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/'
        respuesta = admin.patch(url, {'estado': 'CANCELADO'}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('estado', respuesta.data)

        self._devolver('30')
        respuesta = admin.patch(url, {'estado': 'CANCELADO'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        # La orden anulada todavía admite devoluciones, nunca pagos.
        self.assertEqual(self._pagar('1').status_code, 400)

    def test_no_se_elimina_una_orden_con_pagos(self):
        pago = self._pagar('10')
        self._cliente(self.admin).post(f'{ENDPOINT}{pago.data["id"]}/anular/', {'motivo': 'error'}, format='json')
        respuesta = self._cliente(self.admin).delete(f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('pagos registrados', str(respuesta.data))


class AnularPagoTests(PagosBase):

    def _anular(self, pago_id, motivo='Monto mal digitado'):
        return self.client.post(f'{ENDPOINT}{pago_id}/anular/', {'motivo': motivo}, format='json')

    def test_anular_pago_libera_el_saldo(self):
        pago = self._pagar('115')
        respuesta = self._anular(pago.data['id'])
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertTrue(respuesta.data['anulado'])
        self.assertEqual(respuesta.data['motivo_anulacion'], 'Monto mal digitado')
        self.assertEqual(respuesta.data['anulado_por_nombre'], 'pagos_cajero')
        self.assertEqual(self._resumen()['saldo'], '115.00')
        self.assertTrue(
            EventoDocumento.objects.filter(documento=self.orden.uuid, evento=EventoDocumento.Evento.PAGO_ANULADO).exists()
        )

    def test_anular_exige_motivo(self):
        pago = self._pagar('10')
        respuesta = self._anular(pago.data['id'], motivo='  ')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('motivo', respuesta.data)

    def test_no_se_anula_dos_veces(self):
        pago = self._pagar('10')
        self._anular(pago.data['id'])
        self.assertEqual(self._anular(pago.data['id']).status_code, 400)

    def test_anular_devolucion_no_puede_dejar_saldo_a_favor(self):
        self._pagar('115')
        devolucion = self._devolver('15')
        self._pagar('15')
        respuesta = self._anular(devolucion.data['id'])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('superaría el total', str(respuesta.data))

    def test_no_se_edita_ni_se_borra(self):
        pago = self._pagar('10')
        url = f'{ENDPOINT}{pago.data["id"]}/'
        self.assertEqual(self.client.patch(url, {'monto': '1'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)


class PermisosPagosTests(PagosBase):

    def test_asesor_no_ve_ni_registra_pagos(self):
        asesor = self._cliente(self.asesor)
        self.assertEqual(asesor.get(ENDPOINT).status_code, 403)
        self.assertEqual(self._pagar('10', client=asesor).status_code, 403)

    def test_asesor_ve_el_saldo_en_la_orden(self):
        self._pagar('15')
        respuesta = self._cliente(self.asesor).get(f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['saldo'], '100.00')

    def test_cajero_no_paga_en_un_taller_no_asignado(self):
        orden_a2 = self._orden(self.empresa, self.taller_a2, self.cliente, 'A2-OT')
        respuesta = self._pagar('10', orden=orden_a2)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('orden_trabajo', respuesta.data)

    def test_cajero_no_ve_pagos_de_otro_taller(self):
        orden_a2 = self._orden(self.empresa, self.taller_a2, self.cliente, 'A2-OT')
        DetalleRepuestoOrdenTrabajo.objects.create(orden_trabajo=orden_a2, descripcion='X', precio_unitario=Decimal('10'))
        orden_a2.calcular_totales()
        pago = self._pagar('5', client=self._cliente(self.admin), orden=orden_a2)
        self.assertEqual(pago.status_code, 201, pago.content)
        self.assertEqual(self.client.get(f'{ENDPOINT}{pago.data["id"]}/').status_code, 404)
        self.assertEqual(self.client.get(ENDPOINT).data['count'], 0)

    def test_otra_empresa_no_paga_ni_ve(self):
        self._pagar('10')
        ajeno = self._cliente(self.cajero_ajeno, self.otra_empresa)
        self.assertEqual(ajeno.get(ENDPOINT).data['count'], 0)
        self.assertEqual(self._pagar('10', client=ajeno).status_code, 400)


class EntregaConSaldoTests(PagosBase):

    def setUp(self):
        super().setUp()
        self.orden.estado = 'COMPLETADO'
        self.orden.save()
        self.admin_client = self._cliente(self.admin)
        self.url = f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/'

    def _entregar(self, **extra):
        return self.admin_client.patch(self.url, {'estado': 'ENTREGADO', **extra}, format='json')

    def test_entregar_con_saldo_exige_confirmar_credito(self):
        self._pagar('15')
        respuesta = self._entregar()
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('$ 100.00', str(respuesta.data['entrega_a_credito']))
        self.assertEqual(respuesta.data['saldo'], ['100.00'])
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, 'COMPLETADO')

    def test_entrega_a_credito_guarda_saldo_y_quien_autoriza(self):
        self._pagar('15')
        respuesta = self._entregar(entrega_a_credito=True)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['saldo_al_entregar'], '100.00')
        self.assertEqual(respuesta.data['entrega_credito_autorizada_por_nombre'], 'pagos_admin')
        self.assertEqual(respuesta.data['estado_pago'], 'PARCIAL')
        self.assertNotIn('entrega_a_credito', respuesta.data)
        self.assertTrue(EventoDocumento.objects.filter(
            documento=self.orden.uuid, etiqueta='Entregada a crédito con saldo', valor_nuevo='100.00',
        ).exists())
        # Lo que queda se cobra después de la entrega.
        self.assertEqual(self._pagar('100').status_code, 201)
        self.assertEqual(self._resumen()['estado_pago'], 'PAGADO')

    def test_entrega_pagada_no_pide_confirmacion(self):
        self._pagar('115')
        respuesta = self._entregar()
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertIsNone(respuesta.data['saldo_al_entregar'])
        self.assertIsNone(respuesta.data['entrega_credito_autorizada_por_nombre'])

    def test_saldo_al_entregar_no_se_escribe_desde_el_cliente(self):
        self._pagar('115')
        respuesta = self._entregar(saldo_al_entregar='5.00')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertIsNone(respuesta.data['saldo_al_entregar'])


class EstadoPagoListadoTests(PagosBase):

    def setUp(self):
        super().setUp()
        self.parcial = self.orden
        self._pagar('15')
        self.pagada = self._con_total('A1-PAG')
        self._pagar('10', orden=self.pagada)
        self.pendiente = self._con_total('A1-PEN')
        self.devuelta = self._con_total('A1-DEV')
        self._pagar('10', orden=self.devuelta)
        self._pagar('10', orden=self.devuelta, tipo='DEVOLUCION')
        self.anulada = self._con_total('A1-ANU', estado='CANCELADO')

    def _con_total(self, numero, **extra):
        orden = self._orden(self.empresa, self.taller_a1, self.cliente, numero)
        DetalleRepuestoOrdenTrabajo.objects.create(
            orden_trabajo=orden, descripcion='X', precio_unitario=Decimal('10'), iva_porcentaje=Decimal('0'),
        )
        orden.calcular_totales()
        for campo, valor in extra.items():
            setattr(orden, campo, valor)
        orden.save()
        return orden

    def _numeros(self, estado_pago):
        respuesta = self._cliente(self.admin).get('/api/ordenes/ordenes-trabajo/', {'estado_pago': estado_pago})
        self.assertEqual(respuesta.status_code, 200)
        return {fila['numero_orden'] for fila in respuesta.data['results']}

    def test_filtro_por_estado_de_pago(self):
        self.assertEqual(self._numeros('PAGADO'), {'A1-PAG'})
        self.assertEqual(self._numeros('PARCIAL'), {'A1-OT'})
        self.assertEqual(self._numeros('PENDIENTE'), {'A1-PEN', 'A1-DEV'})
        self.assertEqual(self._numeros('SIN_COBRO'), {'A1-ANU'})
        self.assertEqual(len(self._numeros('OTRO')), 5)

    def test_orden_anulada_no_tiene_saldo(self):
        respuesta = self._cliente(self.admin).get(f'/api/ordenes/ordenes-trabajo/{self.anulada.uuid}/')
        self.assertEqual(respuesta.data['estado_pago'], 'SIN_COBRO')
        self.assertEqual(respuesta.data['saldo'], '0.00')

    def test_no_se_anula_la_devolucion_de_una_orden_anulada(self):
        orden = self._con_total('A1-X')
        self._pagar('10', orden=orden)
        devolucion = self._pagar('10', orden=orden, tipo='DEVOLUCION')
        self._cliente(self.admin).patch(
            f'/api/ordenes/ordenes-trabajo/{orden.uuid}/', {'estado': 'CANCELADO'}, format='json',
        )
        respuesta = self.client.post(f'{ENDPOINT}{devolucion.data["id"]}/anular/', {'motivo': 'x'}, format='json')
        self.assertEqual(respuesta.status_code, 400)


class CuentasPorCobrarTests(PagosBase):
    """Solo cuentan las órdenes entregadas con saldo (entregas a crédito)."""

    URL = f'{ENDPOINT}cuentas-por-cobrar/'

    def setUp(self):
        super().setUp()
        # A1-OT: $115, pagó $15 y se entregó a crédito hace 10 días.
        self._pagar('15')
        self._entregar_a_credito(self.orden, dias=10)
        # Pagada y entregada: no es deuda.
        self.pagada = self._entregada('A1-PAG', pagar='10')
        # En proceso con anticipo: aún no se cobra.
        self.en_proceso = self._con_lineas('A1-PRO')
        self._pagar('5', orden=self.en_proceso)
        # Otro taller, entregada a crédito.
        self.orden_a2 = self._entregada('A2-CRE', taller=self.taller_a2)
        self.cliente.telefono = '0991234567'
        self.cliente.save()

    def _con_lineas(self, numero, taller=None):
        orden = self._orden(self.empresa, taller or self.taller_a1, self.cliente, numero)
        DetalleRepuestoOrdenTrabajo.objects.create(
            orden_trabajo=orden, descripcion='X', precio_unitario=Decimal('10'), iva_porcentaje=Decimal('0'),
        )
        orden.calcular_totales()
        return orden

    def _entregar_a_credito(self, orden, dias=0):
        orden.estado = 'ENTREGADO'
        orden.fecha_entrega = timezone.now() - timedelta(days=dias)
        orden.save()

    def _entregada(self, numero, taller=None, pagar=None):
        orden = self._con_lineas(numero, taller)
        if pagar:
            self._pagar(pagar, client=self._cliente(self.admin), orden=orden)
        self._entregar_a_credito(orden)
        return orden

    def test_lista_solo_entregadas_con_saldo(self):
        respuesta = self._cliente(self.admin).get(self.URL)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        filas = {fila['numero_orden']: fila for fila in respuesta.data['results']}
        self.assertEqual(set(filas), {'A1-OT', 'A2-CRE'})
        self.assertEqual(filas['A1-OT']['saldo'], '100.00')
        self.assertEqual(filas['A1-OT']['pagado_neto'], '15.00')
        self.assertEqual(filas['A1-OT']['dias_atraso'], 10)
        self.assertEqual(filas['A1-OT']['cliente']['nombre'], 'Cliente')
        # La más antigua primero.
        self.assertEqual(respuesta.data['results'][0]['numero_orden'], 'A1-OT')
        resumen = respuesta.data['resumen']
        self.assertEqual(resumen['total_por_cobrar'], '110.00')
        self.assertEqual(resumen['ordenes'], 2)
        self.assertEqual(resumen['clientes'], 1)
        self.assertEqual(
            {(t['nombre'], t['saldo']) for t in resumen['por_taller']}, {('A1', '100.00'), ('A2', '10.00')},
        )

    def test_cajero_solo_ve_su_taller(self):
        respuesta = self.client.get(self.URL)
        self.assertEqual([f['numero_orden'] for f in respuesta.data['results']], ['A1-OT'])
        self.assertEqual(respuesta.data['resumen']['total_por_cobrar'], '100.00')

    def test_asesor_sin_facturacion_no_entra(self):
        self.assertEqual(self._cliente(self.asesor).get(self.URL).status_code, 403)

    def test_filtros_por_cliente_y_busqueda(self):
        otro = Cliente.objects.create(empresa=self.empresa, nombre='Otro', identificacion='1700000099')
        admin = self._cliente(self.admin)
        self.assertEqual(admin.get(self.URL, {'cliente': str(otro.uuid)}).data['resumen']['ordenes'], 0)
        self.assertEqual(admin.get(self.URL, {'cliente': str(self.cliente.uuid)}).data['resumen']['ordenes'], 2)
        respuesta = admin.get(self.URL, {'search': 'A2-CRE'})
        self.assertEqual([f['numero_orden'] for f in respuesta.data['results']], ['A2-CRE'])

    def test_al_pagar_el_saldo_sale_de_la_lista(self):
        self._pagar('100')
        numeros = [f['numero_orden'] for f in self._cliente(self.admin).get(self.URL).data['results']]
        self.assertEqual(numeros, ['A2-CRE'])

    @override_settings(**MOCK_CONFIG)
    def test_recordar_saldo_por_whatsapp(self):
        url = f'{self.URL}{self.orden.uuid}/recordar/'
        respuesta = self.client.post(url, {}, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        notificacion = NotificacionProgramada.objects.get(orden=self.orden, tipo='SALDO')
        self.assertEqual(notificacion.estado, 'ENVIADA')
        self.assertIn('saldo pendiente de $ 100.00', notificacion.mensaje)
        fila = self.client.get(self.URL).data['results'][0]
        self.assertIsNotNone(fila['ultimo_recordatorio'])

    @override_settings(**MOCK_CONFIG)
    def test_no_se_recuerda_sin_saldo_ni_fuera_del_alcance(self):
        self.assertEqual(self.client.post(f'{self.URL}{self.pagada.uuid}/recordar/', {}).status_code, 400)
        self.assertEqual(self.client.post(f'{self.URL}{self.en_proceso.uuid}/recordar/', {}).status_code, 400)
        # El cajero de A1 no ve la orden del taller A2.
        self.assertEqual(self.client.post(f'{self.URL}{self.orden_a2.uuid}/recordar/', {}).status_code, 404)
        self.assertEqual(self._cliente(self.asesor).post(f'{self.URL}{self.orden.uuid}/recordar/', {}).status_code, 403)

    def test_historial_de_pagos_del_cliente(self):
        otro = Cliente.objects.create(empresa=self.empresa, nombre='Otro', identificacion='1700000098')
        respuesta = self._cliente(self.admin).get(ENDPOINT, {'cliente': str(self.cliente.uuid)})
        self.assertEqual(sorted(f['numero_orden'] for f in respuesta.data['results']), ['A1-OT', 'A1-PAG', 'A1-PRO'])
        self.assertEqual(self._cliente(self.admin).get(ENDPOINT, {'cliente': str(otro.uuid)}).data['count'], 0)

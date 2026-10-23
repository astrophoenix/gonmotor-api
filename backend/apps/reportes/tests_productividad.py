from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa, PermisoEmpresa, Rol, Recurso
from apps.caja.models import MovimientoCaja
from apps.compras.models import PagoCompra
from apps.gastos.models import Gasto
from apps.pagos.models import PagoOrden
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import OrdenTrabajo, DetalleServicioOrdenTrabajo, DetalleRepuestoOrdenTrabajo
from apps.proveedores.models import Proveedor
from apps.vehiculos.models import Vehiculo
from .productividad import calcular_productividad

URL = '/api/reportes/productividad/'
RANGO = {'fecha_desde': '2026-06-01', 'fecha_hasta': '2026-06-30'}


class ProductividadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Productividad', ruc='1799999988001')
        cls.otra = Empresa.objects.create(nombre_comercial='Ajena', ruc='1799999988002')
        cls.t1 = Taller.objects.create(empresa=cls.empresa, nombre='Uno', codigo_sucursal='001')
        cls.t2 = Taller.objects.create(empresa=cls.empresa, nombre='Dos', codigo_sucursal='002')
        cls.tb = Taller.objects.create(empresa=cls.otra, nombre='Ajeno', codigo_sucursal='001')
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000001')
        cls.vehiculo = Vehiculo.objects.create(placa='PRO0001', marca='Kia', modelo='Rio')
        cls.admin = cls.usuario('admin', 'ADMIN_EMPRESA')
        cls.jefe = cls.usuario('jefe', 'ADMIN_TALLER', [cls.t1])
        cls.m1 = cls.usuario('m1', 'MECANICO', [cls.t1])
        cls.m2 = cls.usuario('m2', 'MECANICO', [cls.t1, cls.t2])
        cls.ajeno = cls.usuario('ajeno', 'MECANICO', [cls.tb], empresa=cls.otra)
        cls.e1 = UsuarioEmpresa.objects.get(user=cls.m1, empresa=cls.empresa)
        cls.e2 = UsuarioEmpresa.objects.get(user=cls.m2, empresa=cls.empresa)
        cls.e1.comision_porcentaje = Decimal('10.00'); cls.e1.save()
        cls.e2.comision_porcentaje = Decimal('20.00'); cls.e2.save()
        cls.ot = cls.orden('OT-1', mecanico=cls.m1)
        cls.linea(cls.ot, precio='100', horas='2', descuento='20')  # principal: 180
        cls.linea(cls.ot, mecanico=cls.m2, precio='50', horas='3')   # asignado: 150
        cls.ot.calcular_totales()

    @classmethod
    def usuario(cls, nombre, rol, talleres=None, empresa=None):
        user = User.objects.create_user(username=nombre, first_name=nombre, password='x')
        e = UsuarioEmpresa.objects.create(user=user, empresa=empresa or cls.empresa, rol=rol)
        e.talleres.set(talleres or [])
        return user

    @classmethod
    def orden(cls, numero, *, mecanico=None, taller=None, empresa=None, fecha=None, estado='ENTREGADO'):
        return OrdenTrabajo.objects.create(
            empresa=empresa or cls.empresa, sucursal=taller or cls.t1,
            cliente=cls.cliente, vehiculo=cls.vehiculo, numero_orden=numero,
            mecanico_principal=mecanico, estado=estado,
            fecha_entrega=fecha or timezone.make_aware(datetime(2026, 6, 15, 12)),
        )

    @staticmethod
    def linea(ot, *, mecanico=None, precio='100', horas='1', descuento='0', **extra):
        return DetalleServicioOrdenTrabajo.objects.create(
            orden_trabajo=ot, descripcion='Mano de obra', mecanico_asignado=mecanico,
            precio_unitario=Decimal(precio), horas_aplicadas=Decimal(horas),
            descuento=Decimal(descuento), completado=extra.pop('completado', True), **extra,
        )

    def api(self, user=None, empresa=None):
        api = APIClient(); api.force_authenticate(user or self.admin)
        fijar_empresa(api, empresa or self.empresa)
        return api

    def reporte(self, **params):
        r = self.api().get(URL, {**RANGO, **params})
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_calculo_asignacion_descuento_iva_saldo_y_ordenes_distintas(self):
        r = self.reporte()
        filas = {f['nombre']: f for f in r['mecanicos']}
        self.assertEqual(Decimal(str(filas['m1']['comision'])), Decimal('18'))
        self.assertEqual(Decimal(str(filas['m2']['comision'])), Decimal('30'))
        self.assertEqual(r['resumen']['ordenes'], 1)
        self.assertEqual(sum(f['ordenes'] for f in filas.values()), 2)
        self.assertEqual(r['resumen']['servicios_completados'], 2)
        self.assertEqual(r['resumen']['horas_aplicadas'], Decimal('5'))
        self.assertEqual(r['resumen']['mano_obra'], Decimal('330'))
        self.assertEqual(r['resumen']['comision'], Decimal('48'))
        self.assertEqual(str(filas['m1']['id']), str(self.m1.profile.uuid))
        self.assertEqual(str(filas['m1']['empleado_id']), str(self.e1.uuid))
        self.assertFalse(self.ot.pagos.exists())  # no exige cobro ni genera pagos

    def test_excluye_pendientes_incompletos_subcontratos_y_repuestos(self):
        self.linea(self.ot, completado=False)
        self.linea(self.ot, es_adicional=True)
        prov = Proveedor.objects.create(empresa=self.empresa, nombre='Externo', identificacion='1799999988003')
        self.linea(self.ot, es_subcontratado=True, proveedor=prov, cantidad=3)
        DetalleRepuestoOrdenTrabajo.objects.create(orden_trabajo=self.ot, descripcion='Pieza', precio_unitario=100)
        self.assertEqual(self.reporte()['resumen']['mano_obra'], Decimal('330'))

    def test_adicional_aprobado_completado_si_cuenta(self):
        linea = self.linea(self.ot, es_adicional=True)
        DetalleServicioOrdenTrabajo.objects.filter(pk=linea.pk).update(aprobado=True)
        self.assertEqual(self.reporte()['resumen']['mano_obra'], Decimal('430'))

    def test_sin_asignar_no_comision(self):
        self.linea(self.orden('SIN'), precio='17')
        f = next(f for f in self.reporte()['mecanicos'] if f['nombre'] == 'Sin asignar')
        self.assertIsNone(f['empleado_id'])
        self.assertEqual(f['comision'], Decimal('0'))
        self.assertEqual(f['mano_obra'], Decimal('17'))

    def test_referencia_historica_ajena_no_filtra_identidad_ni_comision(self):
        self.linea(self.orden('AJENO', mecanico=self.ajeno))
        self.assertNotIn('ajeno', [f['nombre'] for f in self.reporte()['mecanicos']])

    def test_excluye_estados_ot_eliminadas_y_fuera_de_rango(self):
        for i, estado in enumerate(['PENDIENTE', 'EN_PROCESO', 'EN_ESPERA', 'COMPLETADO', 'CANCELADO']):
            self.linea(self.orden(f'EST-{i}', mecanico=self.m1, estado=estado))
        self.linea(self.orden('MAYO', mecanico=self.m1, fecha=timezone.make_aware(datetime(2026, 5, 31, 23, 59))))
        eliminada = self.orden('ELIM', mecanico=self.m1); self.linea(eliminada); eliminada.delete()
        suspendida = self.orden('INACT', mecanico=self.m1); self.linea(suspendida)
        OrdenTrabajo.objects.filter(pk=suspendida.pk).update(is_active=False)
        self.assertEqual(self.reporte()['resumen']['mano_obra'], Decimal('330'))

    def test_extremos_fecha_local_inclusivos(self):
        for i, dt in enumerate([datetime(2026, 6, 1, 0), datetime(2026, 6, 30, 23, 59)]):
            self.linea(self.orden(f'LIM-{i}', mecanico=self.m1, fecha=timezone.make_aware(dt)))
        self.assertEqual(self.reporte()['resumen']['mano_obra'], Decimal('530'))

    def test_tenant_taller_activo_y_filtro_explicito(self):
        self.linea(self.orden('T2', mecanico=self.m1, taller=self.t2))
        self.linea(self.orden('B', mecanico=self.ajeno, empresa=self.otra, taller=self.tb))
        self.assertEqual(self.reporte()['resumen']['mano_obra'], Decimal('430'))
        r = self.api().get(URL, RANGO, HTTP_X_TALLER_ID=str(self.t1.uuid))
        self.assertEqual(r.data['resumen']['mano_obra'], Decimal('330'))
        self.assertEqual(self.reporte(taller_id=str(self.t2.uuid))['resumen']['mano_obra'], Decimal('100'))
        for valor in [str(self.tb.uuid), str(uuid4())]:
            self.assertEqual(self.reporte(taller_id=valor)['mecanicos'], [])

    def test_jefe_no_amplia_alcance(self):
        self.linea(self.orden('T2', mecanico=self.m1, taller=self.t2))
        for params in [RANGO, {**RANGO, 'taller_id': str(self.t2.uuid)}]:
            r = self.api(self.jefe).get(URL, params)
            self.assertEqual(r.status_code, 200)
            self.assertLessEqual(r.data['resumen']['mano_obra'], Decimal('330'))

    def test_mecanico_y_anonimo_no_entran(self):
        self.assertEqual(self.api(self.m1).get(URL, RANGO).status_code, 403)
        self.assertEqual(APIClient().get(URL, RANGO).status_code, 401)

    def test_empresa_sin_acceso_no_entra(self):
        self.assertEqual(self.api(empresa=self.otra).get(URL, RANGO).status_code, 403)

    def test_fechas_invalidas_limite_y_uuid(self):
        for params in [
            {'fecha_desde': 'ayer'}, {'fecha_hasta': '2026-02-31'},
            {'fecha_desde': '2026-07-01'}, {'fecha_desde': '2024-01-01'},
            {'taller_id': str(self.t1.pk)},
        ]:
            r = self.api().get(URL, {**RANGO, **params})
            self.assertEqual(r.status_code, 400, r.data)

    def test_periodo_vacio_devuelve_ceros(self):
        r = self.reporte(fecha_desde='2026-07-01', fecha_hasta='2026-07-31')
        self.assertEqual(r['mecanicos'], [])
        self.assertEqual(r['resumen']['comision'], Decimal('0'))

    def test_porcentaje_actual_recalcula_historico_aun_empleado_inactivo(self):
        self.e1.comision_porcentaje = Decimal('25'); self.e1.is_active = False; self.e1.save()
        self.assertEqual(self.reporte()['resumen']['comision'], Decimal('75'))

    def test_redondeo_por_mecanico_sobre_total(self):
        self.e1.comision_porcentaje = Decimal('0.25'); self.e1.save()
        self.linea(self.ot, precio='2')
        f = next(f for f in self.reporte()['mecanicos'] if f['nombre'] == 'm1')
        self.assertEqual(f['comision'], Decimal('0.46'))  # 182 × 0.25 % = 0.455

    def test_consultas_no_crecen_por_empleado(self):
        def contar():
            with CaptureQueriesContext(connection) as queries:
                calcular_productividad(self.empresa.pk, None, date(2026, 6, 1), date(2026, 6, 30))
            return len(queries)
        base = contar()
        for i in range(4):
            self.linea(self.ot, mecanico=self.usuario(f'extra-{i}', 'MECANICO'))
        self.assertEqual(contar(), base)

    def test_configura_y_lee_porcentaje_sin_cambiar_otra_empresa(self):
        otra_relacion = UsuarioEmpresa.objects.create(user=self.m1, empresa=self.otra, comision_porcentaje=70)
        url = f'/api/auth/empleados/{self.e1.uuid}/'
        r = self.api().patch(url, {'comision_porcentaje': '12.50'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.api().get(url).data['comision_porcentaje'], '12.50')
        otra_relacion.refresh_from_db(); self.assertEqual(otra_relacion.comision_porcentaje, 70)
        r = self.api().patch(f'/api/auth/empleados/{otra_relacion.uuid}/', {'comision_porcentaje': 20}, format='json')
        self.assertEqual(r.status_code, 404)

    def test_porcentaje_limites_y_conservacion_patch(self):
        url = f'/api/auth/empleados/{self.e1.uuid}/'
        for valor in ['-0.01', '100.01', 'NaN', 'Infinity', '1.234', None]:
            r = self.api().patch(url, {'comision_porcentaje': valor}, format='json')
            self.assertEqual(r.status_code, 400, r.data)
        r = self.api().patch(url, {'first_name': 'Nuevo'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.e1.refresh_from_db(); self.assertEqual(self.e1.comision_porcentaje, Decimal('10'))
        for valor in ['0.00', '100.00']:
            self.assertEqual(self.api().patch(url, {'comision_porcentaje': valor}, format='json').status_code, 200)

    def test_alta_empleado_sin_acceso_con_comision_y_default_cero(self):
        for i, extra in enumerate([{}, {'comision_porcentaje': '15.25'}]):
            r = self.api().post('/api/auth/empleados/', {
                'first_name': 'Nuevo', 'last_name': 'Empleado', 'email': f'nuevo{i}@example.ec',
                'tiene_acceso': False, **extra,
            }, format='json')
            self.assertEqual(r.status_code, 201, r.data)
            self.assertEqual(r.data['comision_porcentaje'], extra.get('comision_porcentaje', '0.00'))

    def test_usuarios_modificar_no_basta_para_configurar_comision(self):
        # /usuarios/ hereda el CRUD de empleados: tampoco debe saltarse el permiso.
        PermisoEmpresa.objects.update_or_create(
            empresa=self.empresa, rol=Rol.objects.get(codigo='ADMIN_EMPRESA'),
            recurso=Recurso.objects.get(codigo='empleados'), defaults={'ver': True, 'modificar': False},
        )
        r = self.api().patch(f'/api/auth/usuarios/{self.e1.uuid}/', {'comision_porcentaje': '30'}, format='json')
        self.assertEqual(r.status_code, 400, r.data)
        self.e1.refresh_from_db(); self.assertEqual(self.e1.comision_porcentaje, 10)

    def test_consultar_y_configurar_no_crea_movimientos_financieros(self):
        modelos = (PagoOrden, MovimientoCaja, Gasto, PagoCompra)
        antes = [modelo.objects.count() for modelo in modelos]
        self.reporte()
        self.api().patch(f'/api/auth/empleados/{self.e1.uuid}/', {'comision_porcentaje': '15'}, format='json')
        self.reporte()
        self.assertEqual([modelo.objects.count() for modelo in modelos], antes)

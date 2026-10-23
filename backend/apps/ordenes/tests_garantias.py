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
from apps.inventario.models import Servicio, Repuesto, MovimientoInventario
from apps.proveedores.models import Proveedor
from apps.vehiculos.models import Vehiculo, vincular_empresa
from .models import OrdenTrabajo, DetalleServicioOrdenTrabajo, DetalleRepuestoOrdenTrabajo
from .garantias import condiciones

OT = '/api/ordenes/ordenes-trabajo/'
SVC = '/api/ordenes/ot-servicios/'
RPT = '/api/ordenes/ot-repuestos/'


class GarantiasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Garantías', ruc='1799999977001')
        cls.otra = Empresa.objects.create(nombre_comercial='Otra', ruc='1799999977002')
        cls.t1 = Taller.objects.create(empresa=cls.empresa, nombre='Uno', codigo_sucursal='001')
        cls.t2 = Taller.objects.create(empresa=cls.empresa, nombre='Dos', codigo_sucursal='002')
        cls.tb = Taller.objects.create(empresa=cls.otra, nombre='Ajeno', codigo_sucursal='001')
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000088')
        cls.vehiculo = Vehiculo.objects.create(placa='GAR0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        cls.admin = cls.usuario('gar_admin', 'ADMIN_EMPRESA')
        cls.jefe = cls.usuario('gar_jefe', 'ADMIN_TALLER', [cls.t1])
        cls.mecanico = cls.usuario('gar_mec', 'MECANICO', [cls.t1])
        cls.servicio = Servicio.objects.create(empresa=cls.empresa, codigo='GAR-S1', nombre='Reparación', garantia_dias=30, garantia_km=1000, costo_referencial=20)
        cls.original = cls.orden('ORIGINAL', estado='ENTREGADO')
        cls.linea = DetalleServicioOrdenTrabajo.objects.create(orden_trabajo=cls.original, servicio=cls.servicio, descripcion='Reparación original', precio_unitario=100, completado=True)
        cls.original.calcular_totales()
        cls.activa = cls.orden('NUEVA')
        cls.garantia = cls.orden('GARANTIA', tipo_trabajo='GARANTIA', orden_garantia_origen=cls.original)
        cls.repuesto = Repuesto.objects.create(empresa=cls.empresa, codigo='GAR-R1', nombre='Filtro', stock_actual=5, costo_referencial=10)

    @classmethod
    def usuario(cls, nombre, rol, talleres=None):
        user = get_user_model().objects.create_user(username=nombre, password='x')
        rel = UsuarioEmpresa.objects.create(user=user, empresa=cls.empresa, rol=rol)
        rel.talleres.set(talleres or [])
        return user

    @classmethod
    def orden(cls, numero, **extra):
        data = dict(empresa=cls.empresa, sucursal=cls.t1, cliente=cls.cliente, vehiculo=cls.vehiculo,
                    numero_orden=numero, kilometraje=1000, estado='EN_PROCESO', fecha_entrega=timezone.now() - timedelta(days=10))
        data.update(extra)
        return OrdenTrabajo.objects.create(**data)

    def setUp(self):
        self.api = APIClient(); self.api.force_authenticate(self.admin); fijar_empresa(self.api, self.empresa)

    def consulta(self, **extra):
        r = self.api.get(OT + 'garantias-vehiculo/', {'vehiculo': str(self.vehiculo.uuid), **extra})
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def crear_linea(self, orden=None, **extra):
        data = dict(orden_trabajo=str((orden or self.activa).uuid), descripcion='Servicio', precio_unitario='80', servicio=str(self.servicio.uuid))
        data.update(extra)
        r = self.api.post(SVC, data, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def payload_ot(self, **extra):
        data = dict(vehiculo=str(self.vehiculo.uuid), cliente=str(self.cliente.uuid), sucursal=str(self.t1.uuid),
                    mecanico_principal=str(self.mecanico.profile.uuid), fecha_ingreso=timezone.now().isoformat(),
                    kilometraje=1000, tipo_trabajo='GARANTIA', orden_garantia_origen=str(self.original.uuid))
        data.update(extra)
        return data

    def test_snapshot_catalogo_y_costo_por_fk(self):
        r = self.crear_linea()
        self.assertEqual((r['garantia_dias'], r['garantia_km']), (30, 1000))
        self.assertEqual(Decimal(r['costo_unitario']), 20)
        self.servicio.garantia_dias = 90; self.servicio.save()
        linea = DetalleServicioOrdenTrabajo.objects.get(uuid=r['id']); linea.completado = True; linea.save()
        self.assertEqual(linea.garantia_dias, 30)

    def test_codigo_y_modelo_capturan_condiciones_y_texto_libre_no(self):
        linea = DetalleServicioOrdenTrabajo.objects.create(orden_trabajo=self.activa, descripcion='Cotizado', servicio_codigo=self.servicio.codigo, precio_unitario=100)
        self.assertEqual(linea.garantia_dias, 30)
        libre = DetalleServicioOrdenTrabajo.objects.create(orden_trabajo=self.activa, descripcion='Libre', precio_unitario=100)
        self.assertEqual((libre.garantia_dias, libre.garantia_km), (0, 0))

    def test_override_cero_y_patch_conserva(self):
        r = self.crear_linea(garantia_dias=0, garantia_km=500)
        respuesta = self.api.patch(SVC + r['id'] + '/', {'descripcion': 'Otra'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual((respuesta.data['garantia_dias'], respuesta.data['garantia_km']), (0, 500))

    def test_validacion_enteros_no_negativos_y_catalogo(self):
        for campo in ('garantia_dias', 'garantia_km'):
            for valor in (-1, '1.5', None):
                r = self.api.post(SVC, dict(orden_trabajo=str(self.activa.uuid), descripcion='X', precio_unitario=1, **{campo: valor}), format='json')
                self.assertEqual(r.status_code, 400, r.data)
        r = self.api.patch(f'/api/servicios/{self.servicio.uuid}/', {'garantia_dias': -1}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api.get('/api/servicios/opciones/').data['results'][0]['garantia_dias'], 30)

    def test_limites_inclusivos_y_vence_cualquiera(self):
        inicio = timezone.localdate(self.original.fecha_entrega)
        self.assertIsNotNone(condiciones(self.linea, fecha=inicio + timedelta(days=30), kilometraje=2000))
        self.assertIsNone(condiciones(self.linea, fecha=inicio + timedelta(days=31), kilometraje=1000))
        self.assertIsNone(condiciones(self.linea, fecha=inicio, kilometraje=2001))
        self.assertIsNone(condiciones(self.linea, fecha=inicio - timedelta(days=1), kilometraje=1000))

    def test_limite_solo_dias_solo_km_y_sin_garantia(self):
        self.linea.garantia_km = 0
        self.assertEqual(condiciones(self.linea)['estado'], 'VIGENTE')
        self.linea.garantia_km = 1000; self.linea.garantia_dias = 0
        self.assertEqual(condiciones(self.linea)['estado'], 'POR_VERIFICAR')
        self.linea.garantia_km = 0
        self.assertIsNone(condiciones(self.linea))

    def test_km_desconocido_o_menor_base_pide_verificacion(self):
        self.assertEqual(self.consulta()['garantias'][0]['estado'], 'POR_VERIFICAR')
        self.assertEqual(self.consulta(kilometraje=1500)['garantias'][0]['estado'], 'VIGENTE')
        self.assertEqual(self.consulta(kilometraje=100)['garantias'][0]['estado'], 'POR_VERIFICAR')
        self.assertEqual(self.consulta(kilometraje=2001)['garantias'], [])

    def test_solo_original_entregada_servicio_hecho_aprobado(self):
        for cambios in [dict(completado=False), dict(aprobado=False), dict(garantia_dias=0, garantia_km=0)]:
            for k,v in cambios.items(): setattr(self.linea,k,v)
            self.assertIsNone(condiciones(self.linea))
            self.linea.refresh_from_db()
        for estado in ('PENDIENTE', 'COMPLETADO', 'CANCELADO'):
            self.linea.orden_trabajo.estado = estado
            self.assertIsNone(condiciones(self.linea))
        self.assertEqual(self.consulta()['garantias'][0]['servicio_id'], str(self.linea.uuid))

    def test_subcontrato_tambien_tiene_garantia(self):
        proveedor = Proveedor.objects.create(empresa=self.empresa, nombre='Externo', identificacion='1799999977003')
        l = DetalleServicioOrdenTrabajo.objects.create(orden_trabajo=self.original, descripcion='Externo', precio_unitario=100, es_subcontratado=True, proveedor=proveedor, garantia_dias=20, garantia_km=0, completado=True)
        self.assertEqual(len(self.consulta(kilometraje=1000)['garantias']), 2)
        self.assertEqual(condiciones(l)['estado'], 'VIGENTE')

    def test_aislamiento_empresa_taller_y_uuid(self):
        otra = self.orden('OT-OTRA', sucursal=self.t2, estado='ENTREGADO')
        DetalleServicioOrdenTrabajo.objects.create(orden_trabajo=otra, descripcion='Otra sede', precio_unitario=1, garantia_dias=20, completado=True)
        self.api.force_authenticate(self.jefe)
        self.assertEqual(len(self.consulta()['garantias']), 1)
        self.assertEqual(len(self.consulta()['ordenes_origen']), 1)
        self.api.credentials(HTTP_X_TALLER_ID=str(self.t2.uuid))
        self.assertEqual(self.consulta()['garantias'], [])
        for valor in [str(uuid4()), str(self.vehiculo.pk)]:
            self.assertEqual(self.api.get(OT+'garantias-vehiculo/', {'vehiculo': valor}).status_code, 404)

    def test_sin_empresa_o_permiso_no_consulta(self):
        fijar_empresa(self.api, self.otra)
        self.assertEqual(self.api.get(OT+'garantias-vehiculo/', {'vehiculo': str(self.vehiculo.uuid)}).status_code, 403)
        self.assertEqual(APIClient().get(OT+'garantias-vehiculo/').status_code, 401)

    def test_crear_garantia_valida_y_vinculo_publico(self):
        r = self.api.post(OT, self.payload_ot(), format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['orden_garantia_origen'], str(self.original.uuid))
        self.assertEqual(r.data['orden_garantia_numero'], self.original.numero_orden)

    def test_origen_requerido_entregado_mismo_vehiculo_no_otra_garantia(self):
        otro_vehiculo = Vehiculo.objects.create(placa='GAR0002', marca='Kia', modelo='Rio')
        otra_ot = self.orden('OT-VEH', vehiculo=otro_vehiculo, estado='ENTREGADO')
        for valor in (None, str(self.activa.uuid), str(self.garantia.uuid), str(otra_ot.uuid), str(self.original.pk)):
            r = self.api.post(OT, self.payload_ot(orden_garantia_origen=valor), format='json')
            self.assertEqual(r.status_code, 400, r.data)
        r = self.api.post(OT, self.payload_ot(tipo_trabajo='MANTENIMIENTO'), format='json')
        self.assertEqual(r.status_code, 400)

    def test_no_selecciona_origen_ot_fuera_de_alcance(self):
        ajena = self.orden('AJENA', empresa=self.otra, sucursal=self.tb, estado='ENTREGADO')
        r = self.api.post(OT, self.payload_ot(orden_garantia_origen=str(ajena.uuid)), format='json')
        self.assertEqual(r.status_code, 400)
        self.api.force_authenticate(self.jefe)
        sede = self.orden('SEDE', sucursal=self.t2, estado='ENTREGADO')
        r = self.api.post(OT, self.payload_ot(orden_garantia_origen=str(sede.uuid)), format='json')
        self.assertEqual(r.status_code, 400)

    def test_garantia_cero_cliente_conserva_costo_y_no_renueva(self):
        r = self.crear_linea(self.garantia, horas_aplicadas='2', garantia_dias=100, garantia_km=2000)
        self.assertEqual(Decimal(r['precio_unitario']), 0)
        self.assertEqual(Decimal(r['costo_total']), 40)
        self.assertEqual((r['garantia_dias'], r['garantia_km']), (0,0))
        self.garantia.refresh_from_db(); self.assertEqual(self.garantia.total, 0)
        self.assertEqual(self.original.servicios.get().garantia_dias, 30)
        self.assertIsNone(condiciones(DetalleServicioOrdenTrabajo.objects.get(uuid=r['id'])))
        patch = self.api.patch(SVC + r['id'] + '/', {'precio_unitario': '999', 'descuento': '1'}, format='json')
        self.assertEqual(patch.status_code, 200, patch.data)
        self.assertEqual(Decimal(patch.data['subtotal']), 0)

    def test_repuesto_garantia_despacha_stock_y_costo_sin_cobro(self):
        r = self.api.post(RPT, dict(orden_trabajo=str(self.garantia.uuid), codigo_repuesto=self.repuesto.codigo, descripcion='Repuesto', cantidad='2', precio_unitario='99'), format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Decimal(r.data['subtotal']),0)
        self.assertEqual(Decimal(r.data['costo_total']),20)
        self.assertEqual(self.repuesto.stock_actual,5)
        d = self.api.post(RPT+r.data['id']+'/despachar/', {'cantidad':'2'}, format='json')
        self.assertEqual(d.status_code,200,d.data)
        self.repuesto.refresh_from_db(); self.assertEqual(self.repuesto.stock_actual,3)
        self.assertTrue(MovimientoInventario.objects.filter(orden_trabajo=self.garantia).exists())

    def test_no_conversion_con_lineas_ni_cambiar_tipo_o_reasignar_garantia(self):
        r = self.api.patch(OT+str(self.original.uuid)+'/', self.payload_ot(), format='json')
        self.assertEqual(r.status_code,400)
        for data in [dict(tipo_trabajo='MANTENIMIENTO', orden_garantia_origen=None), dict(orden_garantia_origen=None)]:
            r=self.api.patch(OT+str(self.garantia.uuid)+'/',data,format='json')
            self.assertEqual(r.status_code,400,r.data)

    def test_cambiar_condiciones_revoca_aprobacion_de_adicional_y_deja_historial(self):
        r = self.crear_linea(es_adicional=True)
        aprobado = self.api.post(SVC+r['id']+'/aprobar/', {'version_aprobacion':r['version_aprobacion'], 'medio_aprobacion':'PRESENCIAL'}, format='json')
        self.assertEqual(aprobado.status_code,200,aprobado.data)
        editada=self.api.patch(SVC+r['id']+'/',{'garantia_dias':60},format='json')
        self.assertEqual(editada.status_code,200,editada.data)
        self.assertFalse(editada.data['aprobado'])
        self.assertTrue(EventoDocumento.objects.filter(documento=self.activa.uuid).exists())

    def test_pdf_con_garantias_y_vinculo(self):
        for orden in [self.original,self.garantia]:
            r=self.api.get(OT+str(orden.uuid)+'/exportar-pdf/')
            self.assertEqual(r.status_code,200)
            self.assertTrue(b''.join(r.streaming_content).startswith(b'%PDF'))

    def test_garantia_entregada_con_costo_reduce_utilidad_sin_ingresos(self):
        self.crear_linea(self.garantia, costo_unitario='25', horas_aplicadas='2')
        self.garantia.estado = 'ENTREGADO'; self.garantia.fecha_entrega = timezone.now(); self.garantia.save()
        from apps.reportes.views import _get_rentabilidad
        datos = _get_rentabilidad(self.empresa.pk, [self.t1.pk], timezone.localdate(), timezone.localdate())
        self.assertEqual(datos['ingresos']['total'], 0)
        self.assertEqual(datos['costos']['total_cmv'], 50)
        self.assertEqual(datos['utilidad_neta'], -50)

    def test_no_cobra_pago_en_garantia(self):
        self.crear_linea(self.garantia)
        r = self.api.post('/api/pagos/', {'orden_trabajo':str(self.garantia.uuid), 'monto':'1', 'forma_pago':'EFECTIVO'}, format='json')
        self.assertEqual(r.status_code,400,r.data)
        self.assertFalse(self.garantia.pagos.exists())

    def test_superusuario_tampoco_vincula_orden_de_otra_empresa(self):
        superuser = get_user_model().objects.create_superuser(username='gar_super', password='x')
        self.api.force_authenticate(superuser)
        ajena = self.orden('SUPER-AJENA', empresa=self.otra, sucursal=self.tb, estado='ENTREGADO')
        r = self.api.post(OT, self.payload_ot(orden_garantia_origen=str(ajena.uuid)), format='json')
        self.assertEqual(r.status_code,400,r.data)

    def test_kilometraje_invalido_no_genera_error_servidor(self):
        for valor in ('-1','abc','2.5'):
            r = self.api.get(OT+'garantias-vehiculo/', {'vehiculo':str(self.vehiculo.uuid), 'kilometraje':valor})
            self.assertEqual(r.status_code,400,r.data)

    def test_garantia_rechaza_costos_y_unidades_negativos(self):
        for campo in ('costo_unitario','horas_aplicadas'):
            r = self.api.post(SVC, {'orden_trabajo':str(self.garantia.uuid),'descripcion':'X','precio_unitario':0,campo:'-1'}, format='json')
            self.assertEqual(r.status_code,400,r.data)

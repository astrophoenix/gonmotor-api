from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .models import InspeccionVehiculo


class OrdenesCrudTests(SimpleTestCase):
    def test_imports_crud_components(self):
        from apps.ordenes.views import OrdenTrabajoViewSet
        from apps.ordenes.serializers import OrdenTrabajoSerializer

        self.assertIsNotNone(OrdenTrabajoViewSet)
        self.assertIsNotNone(OrdenTrabajoSerializer)


class InspeccionFechasTests(TestCase):
    def _inspeccion(self, **atributos):
        return InspeccionVehiculo.objects.create(
            numero_inspeccion=atributos.pop('numero_inspeccion', 'INS-TEST-0001'),
            **atributos,
        )

    def test_fecha_inspeccion_por_defecto(self):
        inspeccion = self._inspeccion()

        self.assertIsNotNone(inspeccion.fecha_inspeccion)

    def test_fecha_finalizacion_nula_mientras_no_este_finalizada(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')

        self.assertIsNone(inspeccion.fecha_finalizacion)

    def test_finalizar_sella_fecha_finalizacion(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')

        inspeccion.estado = 'FINALIZADA'
        inspeccion.save(update_fields=['estado', 'updated_at'])

        inspeccion.refresh_from_db()
        self.assertIsNotNone(inspeccion.fecha_finalizacion)
        self.assertEqual(inspeccion.estado, 'FINALIZADA')

    def test_finalizar_respeta_update_fields(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')

        inspeccion.estado = 'FINALIZADA'
        inspeccion.save(update_fields=['estado', 'updated_at'])

        # La fecha se escribe aunque no esté en update_fields.
        inspeccion.refresh_from_db()
        self.assertIsNotNone(inspeccion.fecha_finalizacion)

    def test_reabrir_limpia_fecha_finalizacion(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')
        inspeccion.estado = 'FINALIZADA'
        inspeccion.save()
        self.assertIsNotNone(inspeccion.fecha_finalizacion)

        inspeccion.estado = 'EN_PROCESO'
        inspeccion.save()

        inspeccion.refresh_from_db()
        self.assertIsNone(inspeccion.fecha_finalizacion)

    def test_no_sobrescribe_fecha_finalizacion_al_editar_una_finalizada(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')
        inspeccion.estado = 'FINALIZADA'
        inspeccion.save()
        cerrada = inspeccion.fecha_finalizacion

        inspeccion.diagnostico_tecnico = 'Ajuste posterior'
        inspeccion.save()

        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.fecha_finalizacion, cerrada)

    def test_duracion_inspeccion_en_minutos(self):
        inicio = timezone.now() - timedelta(hours=2, minutes=15)
        inspeccion = self._inspeccion(estado='EN_PROCESO', fecha_inspeccion=inicio)
        inspeccion.estado = 'FINALIZADA'
        inspeccion.save()

        self.assertEqual(inspeccion.duracion_inspeccion, 135)

    def test_duracion_calculada_entre_fechas(self):
        inicio = timezone.now() - timedelta(minutes=95)
        inspeccion = InspeccionVehiculo(
            numero_inspeccion='INS-TEST-0002',
            estado='FINALIZADA',
            fecha_inspeccion=inicio,
        )
        inspeccion.save()
        inspeccion.refresh_from_db()
        inspeccion.fecha_finalizacion = inicio + timedelta(minutes=95)
        inspeccion.save()

        self.assertEqual(inspeccion.duracion_inspeccion, 95)

    def test_duracion_es_none_sin_finalizacion(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')

        self.assertIsNone(inspeccion.duracion_inspeccion)


class InspeccionListadoFiltrosTests(TestCase):
    """El listado de inspecciones tiene panel de búsqueda: filtros por estado,
    tipo, taller y rango de fechas, y búsqueda por número, placa y cliente."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from apps.clientes.models import Cliente
        from apps.empresas.models import Empresa, Taller
        from apps.vehiculos.models import Vehiculo
        from .models import RecepcionVehiculo

        self.empresa = Empresa.objects.create(nombre_comercial='Taller Filtros', ruc='666666666666')
        self.otra_empresa = Empresa.objects.create(nombre_comercial='Taller Ajeno', ruc='555555555555')
        self.taller = Taller.objects.create(
            empresa=self.empresa, nombre='Taller Norte', direccion='Av. Norte 100', prefijo_inspeccion='INS-'
        )
        self.cliente = Cliente.objects.create(
            empresa=self.empresa, nombre='Cliente Filtros', identificacion='0912345678'
        )
        self.vehiculo = Vehiculo.objects.create(placa='FIL-1234', marca='Toyota', modelo='Corolla')

        hoy = timezone.now()
        # Una inspección por recepción (restricción del modelo), así que cada
        # caso de prueba prepara la suya.
        self.recepcion_1 = self._recepcion('REC-FIL-0001')
        self.recepcion_2 = self._recepcion('REC-FIL-0002')

        self.pendiente = self._inspeccion(
            numero_inspeccion='INS-FIL-0001',
            recepcion=self.recepcion_1,
            estado='PENDIENTE',
            tipo_inspeccion='DIAGNOSTICO',
            fecha_inspeccion=hoy,
        )
        self.finalizada = self._inspeccion(
            numero_inspeccion='INS-FIL-0002',
            recepcion=self.recepcion_2,
            estado='FINALIZADA',
            tipo_inspeccion='MANTENIMIENTO',
            fecha_inspeccion=hoy - timedelta(days=10),
            fecha_finalizacion=hoy - timedelta(days=9),
        )
        # Inspección creada sin recepción: el vehículo y el cliente están en la
        # propia inspección y deben ser alcanzables por la búsqueda.
        self.cliente_standalone = Cliente.objects.create(
            empresa=self.empresa, nombre='Cliente Avenida', identificacion='0998765432'
        )
        self.vehiculo_standalone = Vehiculo.objects.create(placa='ABC4321', marca='Mazda', modelo='CX-5')
        self.standalone = self._inspeccion(
            numero_inspeccion='INS-FIL-0003',
            recepcion=None,
            cliente=self.cliente_standalone,
            vehiculo=self.vehiculo_standalone,
            sucursal=None,
            fecha_inspeccion=hoy,
        )
        self.de_otra_empresa = self._inspeccion(
            numero_inspeccion='INS-FIL-0004',
            empresa=self.otra_empresa,
            recepcion=self._recepcion('REC-FIL-0009', empresa=self.otra_empresa),
            cliente=Cliente.objects.create(empresa=self.otra_empresa, nombre='Cliente Ajeno'),
            vehiculo=Vehiculo.objects.create(placa='ZZZ9999'),
            fecha_inspeccion=hoy,
        )

        self.usuario = get_user_model().objects.create_superuser(
            username='filtros', email='filtros@test.local', password='x'
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

    def _recepcion(self, numero, empresa=None):
        from .models import RecepcionVehiculo

        return RecepcionVehiculo.objects.create(
            empresa=empresa or self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion=numero,
        )

    def _inspeccion(self, empresa=None, recepcion=None, cliente=None, vehiculo=None, sucursal='__default__', **atributos):
        if sucursal == '__default__':
            sucursal = self.taller if recepcion is not None else None
        return InspeccionVehiculo.objects.create(
            empresa=empresa or self.empresa,
            sucursal=sucursal,
            cliente=cliente or self.cliente,
            vehiculo=vehiculo or self.vehiculo,
            recepcion=recepcion,
            orden_trabajo=None,
            motivo_ingreso='Ruido en el motor',
            numero_inspeccion=atributos.pop('numero_inspeccion', 'INS-FIL-9999'),
            **atributos,
        )

    def _listar(self, **params):
        respuesta = self.client.get('/api/ordenes/inspecciones/', params)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.json()

    def _numeros(self, **params):
        return sorted(item['numero_inspeccion'] for item in self._listar(**params)['results'])

    def test_no_muestra_inspecciones_de_otra_empresa(self):
        self.assertEqual(self._listar()['count'], 3)

    def test_filtra_por_estado(self):
        self.assertEqual(self._numeros(estado='PENDIENTE'), ['INS-FIL-0001', 'INS-FIL-0003'])
        self.assertEqual(self._numeros(estado='FINALIZADA'), ['INS-FIL-0002'])

    def test_filtra_por_tipo(self):
        self.assertEqual(self._numeros(tipo_inspeccion='MANTENIMIENTO'), ['INS-FIL-0002'])

    def test_filtra_por_taller(self):
        self.assertEqual(self._numeros(sucursal=self.taller.id), ['INS-FIL-0001', 'INS-FIL-0002'])
        self.assertEqual(self._listar(sucursal=999)['count'], 0)

    def test_filtra_por_rango_de_fechas(self):
        hoy = timezone.now().date().isoformat()
        ayer = (timezone.now().date() - timedelta(days=1)).isoformat()

        self.assertEqual(self._numeros(fecha_desde=hoy), ['INS-FIL-0001', 'INS-FIL-0003'])
        self.assertEqual(self._numeros(fecha_hasta=ayer), ['INS-FIL-0002'])
        self.assertEqual(
            self._numeros(fecha_desde=ayer, fecha_hasta=hoy),
            ['INS-FIL-0001', 'INS-FIL-0003'],
        )

    def test_ignora_fechas_invalidas(self):
        self.assertEqual(self._listar(fecha_desde='no-es-fecha')['count'], 3)

    def test_combina_filtros(self):
        self.assertEqual(
            self._numeros(estado='FINALIZADA', tipo_inspeccion='MANTENIMIENTO', sucursal=self.taller.id),
            ['INS-FIL-0002'],
        )

    def test_busca_por_numero_placa_cliente_y_recepcion(self):
        casos = {
            'INS-FIL-0001': ['INS-FIL-0001'],
            'FIL-1234': ['INS-FIL-0001', 'INS-FIL-0002'],
            'ABC4321': ['INS-FIL-0003'],
            'Cliente Filtros': ['INS-FIL-0001', 'INS-FIL-0002'],
            'Cliente Avenida': ['INS-FIL-0003'],
            'REC-FIL-0001': ['INS-FIL-0001'],
        }
        for termino, esperados in casos.items():
            with self.subTest(termino=termino):
                self.assertEqual(self._numeros(search=termino), esperados)

    def test_expone_la_orden_de_trabajo_en_la_relacion(self):
        from .models import OrdenTrabajo

        orden = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden='OT-FIL-0001',
            estado='EN_PROCESO',
            fecha_ingreso=timezone.now(),
        )
        self.finalizada.orden_trabajo = orden
        self.finalizada.save(update_fields=['orden_trabajo'])

        item = next(i for i in self._listar()['results'] if i['numero_inspeccion'] == 'INS-FIL-0002')

        self.assertEqual(item['orden_trabajo'], orden.id)
        self.assertEqual(item['orden_trabajo_numero'], 'OT-FIL-0001')
        self.assertEqual(item['orden_trabajo_estado'], 'EN_PROCESO')
        self.assertTrue(item['orden_trabajo_estado_display'])
        self.assertTrue(item['tiene_orden_trabajo'])

    def test_inspeccion_sin_recepcion_no_inventa_relaciones(self):
        item = next(i for i in self._listar()['results'] if i['numero_inspeccion'] == 'INS-FIL-0003')

        self.assertIsNone(item['recepcion'])
        self.assertIsNone(item['orden_trabajo'])
        self.assertIsNone(item['orden_trabajo_numero'])
        self.assertIsNone(item['cotizacion_numero'])
        self.assertEqual(item['vehiculo']['placa'], 'ABC4321')
        self.assertEqual(item['cliente']['nombre'], 'Cliente Avenida')

    def test_expone_el_kilometraje_del_vehiculo(self):
        self.vehiculo.kilometraje_actual = 72500
        self.vehiculo.save(update_fields=['kilometraje_actual'])
        self.vehiculo_standalone.kilometraje_actual = 12300
        self.vehiculo_standalone.save(update_fields=['kilometraje_actual'])

        resultados = {i['numero_inspeccion']: i for i in self._listar()['results']}

        # Con recepción: el vehículo de la propia inspección y el anidado.
        con_recepcion = resultados['INS-FIL-0001']
        self.assertEqual(con_recepcion['vehiculo']['kilometraje_actual'], 72500)
        self.assertEqual(con_recepcion['recepcion']['vehiculo']['kilometraje_actual'], 72500)
        # Sin recepción: solo el vehículo de la inspección.
        self.assertEqual(resultados['INS-FIL-0003']['vehiculo']['kilometraje_actual'], 12300)

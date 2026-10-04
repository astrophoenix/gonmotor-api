from datetime import timedelta
from decimal import Decimal

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
        # La fecha local del taller, no la de UTC: entre las 00:00 y las 05:00
        # UTC el filtro por fecha_inspeccion__date no ve el mismo día.
        hoy = timezone.localdate().isoformat()
        ayer = (timezone.localdate() - timedelta(days=1)).isoformat()

        self.assertEqual(self._numeros(fecha_desde=hoy), ['INS-FIL-0001', 'INS-FIL-0003'])
        self.assertEqual(self._numeros(fecha_hasta=ayer), ['INS-FIL-0002'])
        self.assertEqual(
            self._numeros(fecha_desde=ayer, fecha_hasta=hoy),
            ['INS-FIL-0001', 'INS-FIL-0003'],
        )

    def test_filtra_inspecciones_sin_recepcion(self):
        self.assertEqual(self._numeros(sin_recepcion='1'), ['INS-FIL-0003'])

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


class InspeccionKilometrajeDiagnosticoTests(TestCase):
    """La inspección guarda su propia lectura del odómetro porque el vehículo
    puede salir a prueba de ruta. Al finalizar la sube al vehículo, y el
    odómetro maestro nunca retrocede."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from apps.clientes.models import Cliente
        from apps.empresas.models import Empresa, Taller
        from apps.vehiculos.models import Vehiculo
        from .models import RecepcionVehiculo

        self.empresa = Empresa.objects.create(nombre_comercial='Taller Km', ruc='777777777777')
        self.taller = Taller.objects.create(
            empresa=self.empresa, nombre='Taller Centro', direccion='Av. Central 1', prefijo_inspeccion='KM-'
        )
        self.cliente = Cliente.objects.create(
            empresa=self.empresa, nombre='Cliente Km', identificacion='0911111111'
        )
        self.vehiculo = Vehiculo.objects.create(
            placa='KM-1234', marca='Toyota', modelo='Corolla', kilometraje_actual=50000
        )
        self.recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-KM-0001',
            kilometraje_ingreso=50000,
            estado='ACEPTADA',
        )
        self.inspeccion = self._inspeccion()

        self.usuario = get_user_model().objects.create_superuser(
            username='km', email='km@test.local', password='x'
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

    def _inspeccion(self, **atributos):
        from .models import InspeccionVehiculo

        atributos = {
            'empresa': self.empresa,
            'sucursal': self.taller,
            'cliente': self.cliente,
            'vehiculo': self.vehiculo,
            'recepcion': self.recepcion,
            'motivo_ingreso': 'Ruido en el motor',
            **atributos,
        }
        if atributos['recepcion'] is None:
            atributos['sucursal'] = None
        return InspeccionVehiculo.objects.create(**atributos)

    def _actualizar(self, payload):
        return self.client.patch(
            f'/api/ordenes/inspecciones/{self.inspeccion.id}/',
            data=payload,
            format='json',
        )

    def test_precarga_el_odometro_del_vehiculo_al_crear(self):
        self.assertEqual(self.inspeccion.kilometraje_diagnostico, 50000)

    def test_al_finalizar_sube_el_odometro_del_vehiculo(self):
        respuesta = self._actualizar({
            'kilometraje_diagnostico': 52400,
            'estado': 'EN_PROCESO',
        })
        self.assertEqual(respuesta.status_code, 200)
        respuesta = self._actualizar({
            'kilometraje_diagnostico': 52400,
            'estado': 'FINALIZADA',
        })
        self.assertEqual(respuesta.status_code, 200)

        self.vehiculo.refresh_from_db()
        self.assertEqual(self.vehiculo.kilometraje_actual, 52400)

    def test_una_lectura_menor_no_hace_retroceder_el_vehiculo(self):
        self.vehiculo.kilometraje_actual = 60000
        self.vehiculo.save(update_fields=['kilometraje_actual'])
        inspeccion = self._inspeccion(
            numero_inspeccion='KM-0002', recepcion=None, kilometraje_diagnostico=50000, estado='PENDIENTE'
        )

        inspeccion.kilometraje_diagnostico = 50000
        inspeccion.estado = 'FINALIZADA'
        inspeccion.save()

        self.vehiculo.refresh_from_db()
        self.assertEqual(self.vehiculo.kilometraje_actual, 60000)

    def test_la_api_rechaza_un_kilometraje_menor_al_del_vehiculo(self):
        respuesta = self._actualizar({'kilometraje_diagnostico': 40000})

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('kilometraje_diagnostico', respuesta.json())

    def test_la_api_permite_conservar_la_lectura_ya_guardada(self):
        # El vehículo puede haber subido en otra visita: la inspección conserva
        # su propia lectura sin que el guardado sea rechazado.
        self.vehiculo.kilometraje_actual = 58000
        self.vehiculo.save(update_fields=['kilometraje_actual'])

        respuesta = self._actualizar({'motivo_ingreso': 'Se reintervisita la falla'})

        self.assertEqual(respuesta.status_code, 200)
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.kilometraje_diagnostico, 50000)

    def test_el_listado_expone_la_lectura_de_la_inspeccion(self):
        self.inspeccion.kilometraje_diagnostico = 52310
        self.inspeccion.save(update_fields=['kilometraje_diagnostico'])

        respuesta = self.client.get('/api/ordenes/inspecciones/')

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['results'][0]['kilometraje_diagnostico'], 52310)

    def test_la_recepcion_no_hace_retroceder_el_odometro(self):
        self.vehiculo.kilometraje_actual = 70000
        self.vehiculo.save(update_fields=['kilometraje_actual'])
        self.recepcion.estado = 'PENDIENTE'
        self.recepcion.kilometraje_ingreso = 45000
        self.recepcion.save()

        # Se revalida el flujo de la vista, que es quien sincroniza.
        from apps.ordenes.views import RecepcionVehiculoViewSet

        RecepcionVehiculoViewSet()._sincronizar_kilometraje_vehiculo(self.recepcion)

        self.vehiculo.refresh_from_db()
        self.assertEqual(self.vehiculo.kilometraje_actual, 70000)


class OrdenTrabajoEstadosTests(TestCase):
    """Estados de la orden de trabajo: pendiente de inicio, en espera, en
    proceso, completado, entregado y cancelado, con transiciones validadas."""

    def setUp(self):
        from apps.clientes.models import Cliente
        from apps.empresas.models import Empresa
        from apps.vehiculos.models import Vehiculo

        self.empresa = Empresa.objects.create(nombre_comercial='Taller Estados', ruc='888888888888')
        self.cliente = Cliente.objects.create(
            empresa=self.empresa, nombre='Cliente Estados', identificacion='0912345679'
        )
        self.vehiculo = Vehiculo.objects.create(placa='EST-0001', marca='Kia', modelo='Sportage')
        self._contador = 0

    def _orden(self, estado='PENDIENTE', motivo_espera=None):
        from .models import OrdenTrabajo

        self._contador += 1
        return OrdenTrabajo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden=f'OT-EST-{self._contador:04d}',
            estado=estado,
            motivo_espera=motivo_espera,
        )

    def _serializer(self, orden, payload):
        from .serializers import OrdenTrabajoSerializer

        return OrdenTrabajoSerializer(orden, data=payload, partial=True)

    def test_estado_por_defecto_pendiente(self):
        from .models import OrdenTrabajo

        self._contador += 1
        orden = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden=f'OT-EST-{self._contador:04d}',
        )

        self.assertEqual(orden.estado, 'PENDIENTE')

    def test_espera_requiere_motivo(self):
        orden = self._orden(estado='EN_PROCESO')

        serializer = self._serializer(orden, {'estado': 'EN_ESPERA'})

        self.assertFalse(serializer.is_valid())
        self.assertIn('motivo_espera', serializer.errors)

    def test_espera_con_motivo_es_valida(self):
        orden = self._orden(estado='EN_PROCESO')

        serializer = self._serializer(orden, {
            'estado': 'EN_ESPERA',
            'motivo_espera': 'Esperando repuestos de frenos',
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_no_se_salta_etapas(self):
        orden = self._orden(estado='PENDIENTE')

        serializer = self._serializer(orden, {'estado': 'ENTREGADO'})

        self.assertFalse(serializer.is_valid())
        self.assertIn('estado', serializer.errors)

    def test_espera_puede_volver_a_proceso_o_cancelarse(self):
        orden = self._orden(estado='EN_ESPERA', motivo_espera='Esperando repuestos')
        serializer = self._serializer(orden, {'estado': 'CANCELADO'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        orden2 = self._orden(estado='EN_ESPERA', motivo_espera='Esperando aprobación')
        serializer2 = self._serializer(orden2, {'estado': 'EN_PROCESO'})
        self.assertTrue(serializer2.is_valid(), serializer2.errors)

    def test_entregar_sella_fecha_entrega(self):
        orden = self._orden(estado='COMPLETADO')
        serializer = self._serializer(orden, {'estado': 'ENTREGADO'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        guardada = serializer.save()
        self.assertIsNotNone(guardada.fecha_entrega)
        self.assertEqual(guardada.estado, 'ENTREGADO')

    def test_entregado_y_cancelado_son_terminales(self):
        entregada = self._orden(estado='ENTREGADO')
        serializer = self._serializer(entregada, {'estado': 'EN_PROCESO'})
        self.assertFalse(serializer.is_valid())

        cancelada = self._orden(estado='CANCELADO')
        serializer2 = self._serializer(cancelada, {'estado': 'PENDIENTE'})
        self.assertFalse(serializer2.is_valid())

    def test_completado_puede_reaperturarse_a_proceso(self):
        orden = self._orden(estado='COMPLETADO')
        serializer = self._serializer(orden, {'estado': 'EN_PROCESO'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['estado'], 'EN_PROCESO')

    def test_api_crea_orden_independiente_sin_cotizacion(self):
        """Una OT independiente se crea por POST sin referencia de cotización."""
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from apps.empresas.models import Taller
        from .models import OrdenTrabajo

        Taller.objects.create(empresa=self.empresa, nombre='Taller OT', prefijo_ot='OT-')
        usuario = get_user_model().objects.create_superuser(
            username='crea-ot', email='crea-ot@test.local', password='x'
        )
        client = APIClient()
        client.force_authenticate(usuario)
        response = client.post(
            '/api/ordenes/ordenes-trabajo/',
            {
                'cliente': self.cliente.id,
                'vehiculo': self.vehiculo.id,
                'estado': 'PENDIENTE',
                'prioridad': 'MEDIA',
                'tipo_trabajo': 'MANTENIMIENTO',
            },
            format='json',
            HTTP_X_EMPRESA_ID=str(self.empresa.id),
        )
        self.assertEqual(response.status_code, 201, response.data)
        creada = OrdenTrabajo.objects.get(pk=response.data['id'])
        self.assertEqual(creada.empresa_id, self.empresa.id)
        self.assertTrue(creada.numero_orden)
        self.assertIsNone(creada.cotizacion_origen_id)
        self.assertEqual(creada.estado, 'PENDIENTE')


class RecepcionRelacionesFlujoApiTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from apps.citas.models import Cita
        from apps.clientes.models import Cliente
        from apps.cotizaciones.models import Cotizacion
        from apps.empresas.models import Empresa
        from apps.vehiculos.models import Vehiculo
        from .models import OrdenTrabajo, RecepcionVehiculo

        self.empresa = Empresa.objects.create(nombre_comercial='Taller Relaciones', ruc='777777777777')
        self.cliente = Cliente.objects.create(empresa=self.empresa, nombre='Cliente Relaciones')
        self.vehiculo = Vehiculo.objects.create(placa='REL-0001', marca='Toyota', modelo='Corolla')
        self.vehiculo.empresas.add(self.empresa)
        self.recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-REL-0001',
        )
        self.cita = Cita.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            fecha_cita=timezone.localdate() + timedelta(days=1),
            hora_cita='10:00',
        )
        self.cotizacion = Cotizacion.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_cotizacion='COT-REL-0001',
        )
        self.orden = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden='OT-REL-0001',
        )
        user = get_user_model().objects.create_superuser(
            username='relaciones', email='relaciones@test.local', password='x'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def _actualizar(self, tipo, entidad_id, accion):
        return self.client.post(
            f'/api/recepciones/{self.recepcion.pk}/relaciones/',
            {'tipo': tipo, 'id': entidad_id, 'accion': accion},
            format='json',
        )

    def test_get_devuelve_relaciones_y_capacidades(self):
        self._actualizar('cita', self.cita.pk, 'vincular')
        self._actualizar('cotizacion', self.cotizacion.pk, 'vincular')
        self._actualizar('orden', self.orden.pk, 'vincular')
        respuesta = self.client.get(f'/api/recepciones/{self.recepcion.pk}/relaciones/')

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        datos = respuesta.json()
        self.assertEqual(datos['relaciones']['cita'][0]['id'], self.cita.pk)
        self.assertEqual(datos['relaciones']['cotizacion'][0]['id'], self.cotizacion.pk)
        self.assertEqual(datos['relaciones']['orden'][0]['id'], self.orden.pk)
        self.assertTrue(datos['puede_agregar']['inspeccion'])
        self.assertTrue(datos['puede_agregar']['cotizacion'])

    def test_vincula_y_desvincula_cita_sin_borrarla(self):
        vincular = self._actualizar('cita', self.cita.pk, 'vincular')
        self.assertEqual(vincular.status_code, 200, vincular.content)
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.recepcion_generada_id, self.recepcion.pk)
        self.assertEqual(self.cita.estado, 'PROGRAMADA')

        desvincular = self._actualizar('cita', self.cita.pk, 'desvincular')
        self.assertEqual(desvincular.status_code, 200, desvincular.content)
        self.cita.refresh_from_db()
        self.assertIsNone(self.cita.recepcion_generada_id)

    def test_vincula_y_desvincula_cotizacion_sin_borrarla(self):
        vincular = self._actualizar('cotizacion', self.cotizacion.pk, 'vincular')
        self.assertEqual(vincular.status_code, 200, vincular.content)
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.recepcion_origen_id, self.recepcion.pk)

        desvincular = self._actualizar('cotizacion', self.cotizacion.pk, 'desvincular')
        self.assertEqual(desvincular.status_code, 200, desvincular.content)
        self.cotizacion.refresh_from_db()
        self.assertIsNone(self.cotizacion.recepcion_origen_id)

    def test_vincula_varias_cotizaciones_activas_a_la_misma_recepcion(self):
        from apps.cotizaciones.models import Cotizacion

        segunda = Cotizacion.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_cotizacion='COT-REL-0002',
        )
        primera_resp = self._actualizar('cotizacion', self.cotizacion.pk, 'vincular')
        segunda_resp = self._actualizar('cotizacion', segunda.pk, 'vincular')

        self.assertEqual(primera_resp.status_code, 200, primera_resp.content)
        self.assertEqual(segunda_resp.status_code, 200, segunda_resp.content)
        relaciones = self.client.get(f'/api/recepciones/{self.recepcion.pk}/relaciones/').json()
        self.assertEqual(
            {item['id'] for item in relaciones['relaciones']['cotizacion']},
            {self.cotizacion.pk, segunda.pk},
        )
        self.assertTrue(relaciones['puede_agregar']['cotizacion'])

    def test_desvincula_cotizacion_relacionada_a_traves_de_inspeccion(self):
        from apps.cotizaciones.models import Cotizacion

        inspeccion = InspeccionVehiculo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            recepcion=self.recepcion,
            motivo_ingreso='Inspección relacionada',
            numero_inspeccion='INS-REL-0003',
        )
        cotizacion = Cotizacion.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            inspeccion_origen=inspeccion,
            numero_cotizacion='COT-REL-0002',
        )

        relaciones = self.client.get(f'/api/recepciones/{self.recepcion.pk}/relaciones/').json()
        self.assertIn(cotizacion.pk, [item['id'] for item in relaciones['relaciones']['cotizacion']])
        respuesta = self._actualizar('cotizacion', cotizacion.pk, 'desvincular')

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cotizacion.refresh_from_db()
        inspeccion.refresh_from_db()
        self.assertIsNone(cotizacion.inspeccion_origen_id)
        self.assertIsNone(cotizacion.recepcion_origen_id)
        self.assertEqual(inspeccion.recepcion_id, self.recepcion.pk)

    def test_vincula_y_desvincula_orden_sin_borrarla(self):
        vincular = self._actualizar('orden', self.orden.pk, 'vincular')
        self.assertEqual(vincular.status_code, 200, vincular.content)
        self.recepcion.refresh_from_db()
        self.assertEqual(self.recepcion.orden_trabajo_id, self.orden.pk)

        desvincular = self._actualizar('orden', self.orden.pk, 'desvincular')
        self.assertEqual(desvincular.status_code, 200, desvincular.content)
        self.recepcion.refresh_from_db()
        self.assertIsNone(self.recepcion.orden_trabajo_id)

    def test_no_permite_dos_inspecciones_en_la_misma_recepcion(self):
        primera = InspeccionVehiculo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            motivo_ingreso='Primera inspección',
            numero_inspeccion='INS-REL-0001',
        )
        segunda = InspeccionVehiculo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            motivo_ingreso='Segunda inspección',
            numero_inspeccion='INS-REL-0002',
        )
        self.assertEqual(self._actualizar('inspeccion', primera.pk, 'vincular').status_code, 200)
        respuesta = self._actualizar('inspeccion', segunda.pk, 'vincular')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        segunda.refresh_from_db()
        self.assertIsNone(segunda.recepcion_id)

class VinculoCotizacionFlujoTests(TestCase):
    """La cotización creada sin origen (WhatsApp, teléfono) se ata sola a la
    recepción y la inspección que nacen después, y la inspección arranca con los
    ítems de la cotización ya cargados."""

    @classmethod
    def setUpTestData(cls):
        from apps.clientes.models import Cliente
        from apps.cotizaciones.models import (
            Cotizacion,
            DetalleRepuestoCotizacion,
            DetalleServicioCotizacion,
        )
        from apps.empresas.models import Empresa, Taller
        from apps.vehiculos.models import Vehiculo

        from django.contrib.auth import get_user_model

        User = get_user_model()
        cls.user = User.objects.create_superuser(username='flujo_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Flujo', razon_social='FLUJO SA', ruc='555555555555'
        )
        cls.taller = Taller.objects.create(
            empresa=cls.empresa, nombre='Central', direccion='Av. Siempre Viva 742'
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa, nombre='Ana Lotus', identificacion='0912345678'
        )
        cls.vehiculo = Vehiculo.objects.create(placa='FLU-001', marca='Toyota', modelo='Corolla')
        cls.vehiculo.empresas.add(cls.empresa)
        cls.Cotizacion = Cotizacion
        cls.DetalleServicioCotizacion = DetalleServicioCotizacion
        cls.DetalleRepuestoCotizacion = DetalleRepuestoCotizacion

    def setUp(self):
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.user)
        self.api.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def _recepcion(self, estado='ACEPTADA', numero='REC-0001'):
        from .models import RecepcionVehiculo

        return RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion=numero,
            estado=estado,
            motivo_ingreso='Ruido en el motor',
        )

    def _cotizacion(self, estado='PENDIENTE', numero='COT-0001', con_items=True, es_opcional=False):
        cotizacion = self.Cotizacion.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_cotizacion=numero,
            estado=estado,
        )
        if con_items:
            self.DetalleServicioCotizacion.objects.create(
                cotizacion=cotizacion,
                codigo='SRV-001',
                descripcion='Cambio de aceite y filtros',
                horas_estimadas=Decimal('2.00'),
                precio_unitario=Decimal('45.00'),
                es_opcional=es_opcional,
            )
            self.DetalleRepuestoCotizacion.objects.create(
                cotizacion=cotizacion,
                codigo_repuesto='REP-001',
                descripcion='Filtro de aceite',
                cantidad=2,
                precio_unitario_referencial=Decimal('18.50'),
                es_opcional=es_opcional,
            )
        return cotizacion

    # --- Recepción ---

    def test_recepcion_se_vincula_con_la_cotizacion_vigente(self):
        from apps.cotizaciones.services import conectar_recepcion

        cotizacion = self._cotizacion()
        recepcion = self._recepcion()

        vinculada = conectar_recepcion(recepcion)

        self.assertEqual(vinculada.pk, cotizacion.pk)
        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.recepcion_origen_id, recepcion.id)

    def test_recepcion_no_pisa_un_vinculo_existente(self):
        from apps.cotizaciones.services import conectar_recepcion

        primera = self._recepcion(numero='REC-0001')
        cotizacion = self._cotizacion()
        conectar_recepcion(primera)

        segunda = self._recepcion(numero='REC-0002')
        conectar_recepcion(segunda)

        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.recepcion_origen_id, primera.id)

    def test_sin_cotizacion_vigente_no_hace_nada(self):
        from apps.cotizaciones.services import conectar_recepcion

        recepcion = self._recepcion()
        self.assertIsNone(conectar_recepcion(recepcion))
        self.assertEqual(self.Cotizacion.objects.count(), 0)

    def test_cotizacion_vencida_no_se_vincula(self):
        from apps.cotizaciones.services import conectar_recepcion

        cotizacion = self._cotizacion(estado='VENCIDA', numero='COT-0009')
        recepcion = self._recepcion()

        self.assertIsNone(conectar_recepcion(recepcion))
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.recepcion_origen_id)

    # --- Inspección desde la recepción ---

    def test_crear_inspeccion_vincula_y_siembra_los_items(self):
        from .models import InspeccionVehiculo

        cotizacion = self._cotizacion(estado='ACEPTADA')
        recepcion = self._recepcion()

        respuesta = self.api.post(f'/api/recepciones/{recepcion.id}/crear-inspeccion/', {}, format='json')

        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        # Regresión: la inspección hereda cliente y vehículo de la recepción.
        self.assertEqual(respuesta.data['cliente']['id'], self.cliente.id)
        self.assertEqual(respuesta.data['vehiculo']['id'], self.vehiculo.id)

        inspeccion = InspeccionVehiculo.objects.get(pk=respuesta.data['id'])
        self.assertEqual(inspeccion.recepcion_id, recepcion.id)

        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.recepcion_origen_id, recepcion.id)
        self.assertEqual(cotizacion.inspeccion_origen_id, inspeccion.id)

        servicio = inspeccion.servicios_detectados.get()
        self.assertEqual(servicio.descripcion, 'Cambio de aceite y filtros')
        self.assertEqual(servicio.horas_estimadas, Decimal('2.00'))
        self.assertEqual(servicio.precio_referencial, Decimal('45.00'))
        self.assertFalse(servicio.es_sugerido)

        repuesto = inspeccion.repuestos_sugeridos.get()
        self.assertEqual(repuesto.descripcion, 'Filtro de aceite')
        self.assertEqual(repuesto.cantidad, 2)
        self.assertEqual(repuesto.precio_referencial, Decimal('18.50'))

    def test_items_opcionales_de_la_cotizacion_se_marcan_sugeridos(self):
        from .models import InspeccionVehiculo

        cotizacion = self._cotizacion(estado='ACEPTADA', es_opcional=True)
        recepcion = self._recepcion()

        respuesta = self.api.post(f'/api/recepciones/{recepcion.id}/crear-inspeccion/', {}, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)

        inspeccion = InspeccionVehiculo.objects.get(pk=respuesta.data['id'])
        self.assertTrue(inspeccion.servicios_detectados.get().es_sugerido)
        self.assertTrue(inspeccion.repuestos_sugeridos.get().es_sugerido)
        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.inspeccion_origen_id, inspeccion.id)

    def test_crear_inspeccion_sin_cotizacion_no_siembra_items(self):
        from .models import InspeccionVehiculo

        recepcion = self._recepcion()

        respuesta = self.api.post(f'/api/recepciones/{recepcion.id}/crear-inspeccion/', {}, format='json')

        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        inspeccion = InspeccionVehiculo.objects.get(pk=respuesta.data['id'])
        self.assertEqual(inspeccion.servicios_detectados.count(), 0)
        self.assertEqual(inspeccion.repuestos_sugeridos.count(), 0)

    def test_la_siembra_no_duplica_items_si_la_inspeccion_ya_tiene(self):
        from apps.cotizaciones.services import sembrar_inspeccion_desde_cotizacion
        from .models import DetalleServicioInspeccion, InspeccionVehiculo

        cotizacion = self._cotizacion()
        inspeccion = InspeccionVehiculo.objects.create(
            empresa=self.empresa,
            numero_inspeccion='INS-9001',
            cliente=self.cliente,
            vehiculo=self.vehiculo,
        )
        DetalleServicioInspeccion.objects.create(
            inspeccion=inspeccion, descripcion='Revisión general', horas_estimadas=1
        )

        copiados = sembrar_inspeccion_desde_cotizacion(cotizacion, inspeccion)

        self.assertEqual(copiados, 0)
        self.assertEqual(inspeccion.servicios_detectados.count(), 1)

    def test_los_items_se_asocian_al_catalogo_por_codigo(self):
        from apps.inventario.models import Repuesto, Servicio
        from apps.cotizaciones.services import sembrar_inspeccion_desde_cotizacion
        from .models import InspeccionVehiculo

        servicio_catalogo = Servicio.objects.create(
            empresa=self.empresa, codigo='SRV-001', nombre='Cambio de aceite'
        )
        repuesto_catalogo = Repuesto.objects.create(
            empresa=self.empresa, codigo='REP-001', nombre='Filtro de aceite'
        )
        cotizacion = self._cotizacion()
        inspeccion = InspeccionVehiculo.objects.create(
            empresa=self.empresa,
            numero_inspeccion='INS-9002',
            cliente=self.cliente,
            vehiculo=self.vehiculo,
        )

        sembrar_inspeccion_desde_cotizacion(cotizacion, inspeccion)

        self.assertEqual(inspeccion.servicios_detectados.get().servicio_id, servicio_catalogo.id)
        self.assertEqual(inspeccion.repuestos_sugeridos.get().repuesto_id, repuesto_catalogo.id)

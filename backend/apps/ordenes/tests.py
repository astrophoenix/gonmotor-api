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
    proceso, completado, entregado y cancelado, con cambios libres entre
    estados (las únicas reglas obligatorias son el motivo en espera y la
    fecha de entrega al entregar)."""

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

    def test_se_puede_saltar_etapas(self):
        orden = self._orden(estado='PENDIENTE')

        serializer = self._serializer(orden, {'estado': 'ENTREGADO'})

        self.assertTrue(serializer.is_valid(), serializer.errors)
        guardada = serializer.save()
        self.assertEqual(guardada.estado, 'ENTREGADO')

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

    def test_entregado_y_cancelado_pueden_reabrirse(self):
        entregada = self._orden(estado='ENTREGADO')
        serializer = self._serializer(entregada, {'estado': 'EN_PROCESO'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['estado'], 'EN_PROCESO')

        cancelada = self._orden(estado='CANCELADO')
        serializer2 = self._serializer(cancelada, {'estado': 'PENDIENTE'})
        self.assertTrue(serializer2.is_valid(), serializer2.errors)
        self.assertEqual(serializer2.validated_data['estado'], 'PENDIENTE')

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


class _BaseRelacionesFlujoApiTests(TestCase):
    """Base con el escenario mínimo del flujo taller: empresa, cliente, vehículo."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from apps.citas.models import Cita
        from apps.clientes.models import Cliente
        from apps.cotizaciones.models import Cotizacion
        from apps.empresas.models import Empresa
        from apps.vehiculos.models import Vehiculo

        from .models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo

        self.Cita = Cita
        self.InspeccionVehiculo = InspeccionVehiculo
        self.OrdenTrabajo = OrdenTrabajo

        self.empresa = Empresa.objects.create(nombre_comercial='Taller Grafo', ruc='888888888888')
        self.cliente = Cliente.objects.create(empresa=self.empresa, nombre='Cliente Grafo')
        self.vehiculo = Vehiculo.objects.create(placa='GRA-0001', marca='Toyota', modelo='Corolla')
        self.vehiculo.empresas.add(self.empresa)
        self.recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-GRA-0001',
        )
        self.inspeccion = InspeccionVehiculo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_inspeccion='INS-GRA-0001',
        )
        self.cotizacion = Cotizacion.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_cotizacion='COT-GRA-0001',
        )
        self.orden = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden='OT-GRA-0001',
        )
        user = get_user_model().objects.create_superuser(
            username='grafo', email='grafo@test.local', password='x'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def _post(self, url, tipo, entidad_id, accion='vincular'):
        return self.client.post(
            url, {'tipo': tipo, 'id': entidad_id, 'accion': accion}, format='json'
        )


class InspeccionRelacionesFlujoApiTests(_BaseRelacionesFlujoApiTests):
    def _url(self, inspeccion=None):
        return f'/api/ordenes/inspecciones/{(inspeccion or self.inspeccion).pk}/relaciones/'

    def test_get_expone_recepcion_cotizacion_y_orden(self):
        respuesta = self.client.get(self._url())
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        datos = respuesta.json()
        self.assertEqual(set(datos['relaciones']), {'cita', 'recepcion', 'cotizacion', 'orden'})
        self.assertTrue(datos['puede_agregar']['recepcion'])

    def test_vincula_recepcion_y_cotizacion(self):
        self.assertEqual(
            self._post(self._url(), 'recepcion', self.recepcion.pk).status_code, 200
        )
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.recepcion_id, self.recepcion.pk)

        self.assertEqual(
            self._post(self._url(), 'cotizacion', self.cotizacion.pk).status_code, 200
        )
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.inspeccion_origen_id, self.inspeccion.pk)

    def test_no_permite_dos_inspecciones_en_la_misma_recepcion(self):
        otra = self.InspeccionVehiculo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_inspeccion='INS-GRA-0002',
        )
        self._post(self._url(self.inspeccion), 'recepcion', self.recepcion.pk)
        respuesta = self._post(self._url(otra), 'recepcion', self.recepcion.pk)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        otra.refresh_from_db()
        self.assertIsNone(otra.recepcion_id)

    def test_no_vincula_cotizacion_ya_convertida(self):
        # Una cotización con orden_trabajo_origen se considera "convertida"
        self.cotizacion.orden_trabajo_origen = self.orden
        self.cotizacion.save(update_fields=['orden_trabajo_origen', 'updated_at'])
        respuesta = self._post(self._url(), 'cotizacion', self.cotizacion.pk)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.cotizacion.refresh_from_db()
        self.assertIsNone(self.cotizacion.inspeccion_origen_id)

    def test_vincula_cita_a_traves_de_la_recepcion(self):
        cita = self.Cita.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            fecha_cita=timezone.localdate(),
            hora_cita='09:30',
        )
        self._post(self._url(), 'recepcion', self.recepcion.pk)
        respuesta = self._post(self._url(), 'cita', cita.pk)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cita.refresh_from_db()
        self.assertEqual(cita.recepcion_generada_id, self.recepcion.pk)

    def test_no_vincula_cita_sin_recepcion_en_la_inspeccion(self):
        cita = self.Cita.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            fecha_cita=timezone.localdate(),
            hora_cita='09:30',
        )
        respuesta = self._post(self._url(), 'cita', cita.pk)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)

    def test_desvincula_orden_desde_inspeccion(self):
        self.inspeccion.orden_trabajo = self.orden
        self.inspeccion.save(update_fields=['orden_trabajo'])
        respuesta = self._post(self._url(), 'orden', self.orden.pk, accion='desvincular')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.inspeccion.refresh_from_db()
        self.assertIsNone(self.inspeccion.orden_trabajo_id)


class CotizacionRelacionesFlujoApiTests(_BaseRelacionesFlujoApiTests):
    def _url(self, cotizacion=None):
        return f'/api/cotizaciones/{(cotizacion or self.cotizacion).pk}/relaciones/'

    def test_get_expone_recepcion_inspeccion_y_orden(self):
        respuesta = self.client.get(self._url())
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        datos = respuesta.json()
        self.assertEqual(set(datos['relaciones']), {'cita', 'recepcion', 'inspeccion', 'orden'})
        self.assertTrue(datos['puede_agregar']['recepcion'])

    def test_vincula_y_desvincula_recepcion(self):
        vincular = self._post(self._url(), 'recepcion', self.recepcion.pk)
        self.assertEqual(vincular.status_code, 200, vincular.content)
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.recepcion_origen_id, self.recepcion.pk)

        desvincular = self._post(self._url(), 'recepcion', self.recepcion.pk, accion='desvincular')
        self.assertEqual(desvincular.status_code, 200, desvincular.content)
        self.cotizacion.refresh_from_db()
        self.assertIsNone(self.cotizacion.recepcion_origen_id)

    def test_vincula_orden_en_ambos_sentidos(self):
        respuesta = self._post(self._url(), 'orden', self.orden.pk)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.cotizacion.refresh_from_db()
        self.orden.refresh_from_db()
        self.assertEqual(self.cotizacion.orden_trabajo_origen_id, self.orden.pk)
        self.assertEqual(self.orden.cotizacion_origen_id, self.cotizacion.pk)

        relaciones = self.client.get(self._url()).json()['relaciones']['orden']
        self.assertEqual([item['id'] for item in relaciones], [self.orden.pk])

    def test_no_vincula_orden_que_ya_tiene_cotizacion_origen(self):
        self.orden.cotizacion_origen = self.cotizacion
        self.orden.save(update_fields=['cotizacion_origen'])
        otra = self._nueva_cotizacion('COT-GRA-0002')
        respuesta = self._post(self._url(otra), 'orden', self.orden.pk)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        otra.refresh_from_db()
        self.assertIsNone(otra.orden_trabajo_origen_id)

    def test_muestra_recepcion_heredada_de_la_inspeccion_sin_borrarla(self):
        self.cotizacion.inspeccion_origen = self.inspeccion
        self.inspeccion.recepcion = self.recepcion
        self.inspeccion.save(update_fields=['recepcion'])
        self.cotizacion.save(update_fields=['inspeccion_origen'])

        datos = self.client.get(self._url()).json()
        recepcion = datos['relaciones']['recepcion'][0]
        self.assertEqual(recepcion['id'], self.recepcion.pk)
        self.assertFalse(recepcion['canDelete'])
        self.assertFalse(datos['puede_agregar']['recepcion'])

    def test_no_quita_el_vinculo_de_una_cotizacion_convertida(self):
        self._post(self._url(), 'orden', self.orden.pk)
        # La cotización ya generó una orden -> no se puede desvincular
        self.cotizacion.orden_trabajo_origen = self.orden
        self.cotizacion.save(update_fields=['orden_trabajo_origen', 'updated_at'])
        respuesta = self._post(self._url(), 'orden', self.orden.pk, accion='desvincular')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.cotizacion_origen_id, self.cotizacion.pk)

    def _nueva_cotizacion(self, numero):
        from apps.cotizaciones.models import Cotizacion

        return Cotizacion.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_cotizacion=numero,
        )


class OrdenRelacionesFlujoApiTests(_BaseRelacionesFlujoApiTests):
    def _url(self, orden=None):
        return f'/api/ordenes/ordenes-trabajo/{(orden or self.orden).pk}/relaciones/'

    def test_get_deriva_recepciones_e_inspeccion(self):
        self.recepcion.orden_trabajo = self.orden
        self.recepcion.save(update_fields=['orden_trabajo'])
        self.inspeccion.recepcion = self.recepcion
        self.inspeccion.orden_trabajo = self.orden
        self.inspeccion.save(update_fields=['recepcion', 'orden_trabajo'])

        datos = self.client.get(self._url()).json()
        self.assertEqual(
            [item['id'] for item in datos['relaciones']['recepcion']], [self.recepcion.pk]
        )
        self.assertEqual(
            [item['id'] for item in datos['relaciones']['inspeccion']], [self.inspeccion.pk]
        )
        self.assertTrue(datos['puede_agregar']['cita'])

    def test_no_rep_recepcion_que_ya_tiene_otra_orden(self):
        otra = self.OrdenTrabajo.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden='OT-GRA-0002',
        )
        self.recepcion.orden_trabajo = otra
        self.recepcion.save(update_fields=['orden_trabajo'])
        respuesta = self._post(self._url(), 'recepcion', self.recepcion.pk)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.recepcion.refresh_from_db()
        self.assertEqual(self.recepcion.orden_trabajo_id, otra.pk)

    def test_vincula_cita_por_recepcion_derivada(self):
        self.recepcion.orden_trabajo = self.orden
        self.recepcion.save(update_fields=['orden_trabajo'])
        cita = self.Cita.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            fecha_cita=timezone.localdate(),
            hora_cita='11:00',
        )
        respuesta = self._post(self._url(), 'cita', cita.pk)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cita.refresh_from_db()
        self.assertEqual(cita.recepcion_generada_id, self.recepcion.pk)

    def test_no_vincula_cita_si_la_orden_no_tiene_recepcion(self):
        cita = self.Cita.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            fecha_cita=timezone.localdate(),
            hora_cita='11:00',
        )
        respuesta = self._post(self._url(), 'cita', cita.pk)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)

    def test_rechaza_relacion_con_otra_empresa(self):
        from apps.clientes.models import Cliente
        from apps.empresas.models import Empresa

        otra_empresa = Empresa.objects.create(nombre_comercial='Taller Otro', ruc='999999999999')
        otro_cliente = Cliente.objects.create(empresa=otra_empresa, nombre='Cliente Otro')
        cotizacion = self._cotizacion_externa(otra_empresa, otro_cliente)
        respuesta = self._post(self._url(), 'cotizacion', cotizacion.pk)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('empresa actual', respuesta.content.decode())

    def test_rechaza_tipo_y_accion_invalidos(self):
        respuesta = self._post(self._url(), 'vehiculo', self.vehiculo.pk)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        respuesta = self.client.post(
            self._url(),
            {'tipo': 'cotizacion', 'id': self.cotizacion.pk, 'accion': 'borrar'},
            format='json',
        )
        self.assertEqual(respuesta.status_code, 400, respuesta.content)

    def _cotizacion_externa(self, empresa, cliente):
        from apps.cotizaciones.models import Cotizacion

        return Cotizacion.objects.create(
            empresa=empresa, cliente=cliente, numero_cotizacion='COT-EXT-0001'
        )


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


class CotizacionesCandidatasInspeccionTests(TestCase):
    """La pantalla de decisión de la inspección debe ver las cotizaciones
    aceptadas antes de la visita, no solo las ya ligadas a la inspección."""

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
        cls.user = User.objects.create_superuser(username='candidatas_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Candidatas', razon_social='CAND SA', ruc='666666666666'
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre_comercial='Taller Otro', razon_social='OTRO SA', ruc='777777777777'
        )
        cls.taller = Taller.objects.create(
            empresa=cls.empresa, nombre='Central', direccion='Av. Siempre Viva 742'
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa, nombre='Ana Lotus', identificacion='0912345678'
        )
        cls.vehiculo = Vehiculo.objects.create(placa='CAN-001', marca='Toyota', modelo='Corolla')
        cls.vehiculo.empresas.add(cls.empresa)
        cls.Cotizacion = Cotizacion
        cls.DetalleServicioCotizacion = DetalleServicioCotizacion
        cls.DetalleRepuestoCotizacion = DetalleRepuestoCotizacion

    def setUp(self):
        from apps.ordenes.models import InspeccionVehiculo, RecepcionVehiculo

        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.user)
        self.api.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

        self.recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-7001',
            estado='ACEPTADA',
            motivo_ingreso='Mantenimiento',
        )
        self.inspeccion = InspeccionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            numero_inspeccion='INS-7001',
            recepcion=self.recepcion,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            estado='FINALIZADA',
        )

    def _cotizacion(self, estado='ACEPTADA', numero='COT-7001', con_items=True, vehiculo=None):
        cotizacion = self.Cotizacion.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=vehiculo if vehiculo is not None else self.vehiculo,
            numero_cotizacion=numero,
            estado=estado,
            total=Decimal('480.00'),
        )
        if con_items:
            self.DetalleServicioCotizacion.objects.create(
                cotizacion=cotizacion,
                codigo='SRV-001',
                descripcion='Cambio de aceite y filtros',
                horas_estimadas=Decimal('2.00'),
                precio_unitario=Decimal('45.00'),
            )
            self.DetalleRepuestoCotizacion.objects.create(
                cotizacion=cotizacion,
                codigo_repuesto='REP-001',
                descripcion='Filtro de aceite',
                cantidad=2,
                precio_unitario_referencial=Decimal('18.50'),
            )
        return cotizacion

    def _url(self):
        return f'/api/ordenes/inspecciones/{self.inspeccion.id}/cotizaciones-candidatas/'

    # --- Listado ---

    def test_lista_cotizaciones_aceptadas_del_vehiculo(self):
        aceptada = self._cotizacion(estado='ACEPTADA', numero='COT-7001')

        respuesta = self.api.get(self._url())

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(len(respuesta.data['aprobadas']), 1)
        registro = respuesta.data['aprobadas'][0]
        self.assertEqual(registro['id'], aceptada.id)
        self.assertEqual(registro['numero'], 'COT-7001')
        self.assertEqual(registro['estadoDisplay'], 'Aceptada')
        # El total lo recalculan los signals al crear los ítems.
        self.assertGreater(float(registro['total']), 0)
        # Llega el detalle de ítems para poder comparar en pantalla.
        self.assertEqual(len(registro['servicios']), 1)
        self.assertEqual(len(registro['repuestos']), 1)
        self.assertEqual(registro['repuestos'][0]['descripcion'], 'Filtro de aceite')
        # Aún no está vinculada a esta visita.
        self.assertFalse(registro['vinculada'])

    def test_clasifica_por_estado(self):
        self._cotizacion(estado='ACEPTADA', numero='COT-7001')
        self._cotizacion(estado='ENVIADA', numero='COT-7002')
        self._cotizacion(estado='PENDIENTE', numero='COT-7003')
        self._cotizacion(estado='RECHAZADA', numero='COT-7004')
        # "Convertida" = ACEPTADA con orden_trabajo_origen (se mantiene en ACEPTADA)
        convertida = self._cotizacion(estado='ACEPTADA', numero='COT-7005')
        from apps.ordenes.models import OrdenTrabajo, TipoTrabajo
        orden_ficticia = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden='OT-FICTICIA',
            tipo_trabajo=TipoTrabajo.MANTENIMIENTO,
        )
        convertida.orden_trabajo_origen = orden_ficticia
        convertida.save(update_fields=['orden_trabajo_origen', 'updated_at'])

        respuesta = self.api.get(self._url())

        numeros = lambda grupo: sorted(c['numero'] for c in respuesta.data[grupo])  # noqa: E731
        self.assertEqual(numeros('aprobadas'), ['COT-7001', 'COT-7005'])
        self.assertEqual(numeros('enCurso'), ['COT-7002', 'COT-7003'])
        self.assertEqual(numeros('historicas'), ['COT-7004'])

    def test_no_muestra_cotizaciones_de_otros_vehiculos_ni_otros_talleres(self):
        from apps.vehiculos.models import Vehiculo

        otro_vehiculo = Vehiculo.objects.create(placa='CAN-999', marca='Kia', modelo='Rio')
        otro_vehiculo.empresas.add(self.empresa)
        self._cotizacion(estado='ACEPTADA', numero='COT-7001', vehiculo=otro_vehiculo)

        cliente_otro = self.cliente.__class__.objects.create(
            empresa=self.otra_empresa, nombre='Cliente Otro', identificacion='0999999999'
        )
        self.Cotizacion.objects.create(
            empresa=self.otra_empresa,
            cliente=cliente_otro,
            numero_cotizacion='COT-8001',
            estado='ACEPTADA',
        )

        respuesta = self.api.get(self._url())

        self.assertEqual(respuesta.data['aprobadas'], [])
        self.assertEqual(respuesta.data['enCurso'], [])
        self.assertEqual(respuesta.data['historicas'], [])

    def test_marca_como_vinculada_la_cotizacion_de_la_inspeccion(self):
        cotizacion = self._cotizacion(estado='ACEPTADA', numero='COT-7001')
        cotizacion.inspeccion_origen = self.inspeccion
        cotizacion.recepcion_origen = self.recepcion
        cotizacion.save(update_fields=['inspeccion_origen', 'recepcion_origen', 'updated_at'])

        respuesta = self.api.get(self._url())

        self.assertTrue(respuesta.data['aprobadas'][0]['vinculada'])
        self.assertEqual(respuesta.data['cotizacionActivaId'], cotizacion.id)

    # --- Vínculo ---

    def test_vincular_aceptada_a_inspeccion_y_recepcion(self):
        aceptada = self._cotizacion(estado='ACEPTADA', numero='COT-7001')

        respuesta = self.api.post(
            self._url(), {'cotizaciones': [aceptada.id], 'accion': 'vincular'}, format='json'
        )

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.data['resultados'], [{'id': aceptada.id, 'vinculada': True}])
        aceptada.refresh_from_db()
        self.assertEqual(aceptada.inspeccion_origen_id, self.inspeccion.id)
        self.assertEqual(aceptada.recepcion_origen_id, self.recepcion.id)
        # Vincular no reescribe lo aprobado por el cliente.
        self.assertEqual(aceptada.estado, 'ACEPTADA')

    def test_vincular_varias_aceptadas_de_una_vez(self):
        primera = self._cotizacion(estado='ACEPTADA', numero='COT-7001')
        segunda = self._cotizacion(estado='ACEPTADA', numero='COT-7002')

        respuesta = self.api.post(
            self._url(),
            {'cotizaciones': [primera.id, segunda.id], 'accion': 'vincular'},
            format='json',
        )

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(len(respuesta.data['resultados']), 2)
        for cotizacion in (primera, segunda):
            cotizacion.refresh_from_db()
            self.assertEqual(cotizacion.inspeccion_origen_id, self.inspeccion.id)

    def test_no_permite_vincular_una_cotizacion_convertida(self):
        # Una cotización con orden_trabajo_origen se considera "convertida"
        convertida = self._cotizacion(estado='ACEPTADA', numero='COT-7005')
        from apps.ordenes.models import OrdenTrabajo, TipoTrabajo
        orden_ficticia = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden='OT-FICTICIA',
            tipo_trabajo=TipoTrabajo.MANTENIMIENTO,
        )
        convertida.orden_trabajo_origen = orden_ficticia
        convertida.save(update_fields=['orden_trabajo_origen', 'updated_at'])

        respuesta = self.api.post(
            self._url(), {'cotizaciones': [convertida.id], 'accion': 'vincular'}, format='json'
        )

        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('convertida', str(respuesta.data['detail']).lower())
        convertida.refresh_from_db()
        self.assertIsNone(convertida.inspeccion_origen_id)

    def test_no_permite_vincular_cotizacion_de_otra_recepcion(self):
        from apps.ordenes.models import RecepcionVehiculo

        otra_recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-7002',
            estado='ACEPTADA',
            motivo_ingreso='Otro ingreso',
        )
        cotizacion = self._cotizacion(estado='ACEPTADA', numero='COT-7001')
        cotizacion.recepcion_origen = otra_recepcion
        cotizacion.save(update_fields=['recepcion_origen', 'updated_at'])

        respuesta = self.api.post(
            self._url(), {'cotizaciones': [cotizacion.id], 'accion': 'vincular'}, format='json'
        )

        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('otra recepción', str(respuesta.data['detail']).lower())

    def test_no_permite_vincular_si_la_inspeccion_ya_es_orden(self):
        from apps.ordenes.models import OrdenTrabajo

        cotizacion = self._cotizacion(estado='ACEPTADA', numero='COT-7001')
        orden = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden='OT-7001',
            estado='EN_PROCESO',
        )
        self.inspeccion.orden_trabajo = orden
        self.inspeccion.save(update_fields=['orden_trabajo', 'updated_at'])

        respuesta = self.api.post(
            self._url(), {'cotizaciones': [cotizacion.id], 'accion': 'vincular'}, format='json'
        )

        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('orden de trabajo', str(respuesta.data['detail']).lower())

    def test_desvincular_limpia_inspeccion_y_recepcion(self):
        cotizacion = self._cotizacion(estado='ENVIADA', numero='COT-7001')
        cotizacion.inspeccion_origen = self.inspeccion
        cotizacion.recepcion_origen = self.recepcion
        cotizacion.save(update_fields=['inspeccion_origen', 'recepcion_origen', 'updated_at'])

        respuesta = self.api.post(
            self._url(), {'cotizaciones': [cotizacion.id], 'accion': 'desvincular'}, format='json'
        )

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.inspeccion_origen_id)
        self.assertIsNone(cotizacion.recepcion_origen_id)

    def test_exige_lista_de_cotizaciones(self):
        respuesta = self.api.post(self._url(), {'accion': 'vincular'}, format='json')

        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('cotizaciones', respuesta.data)

    def test_rechaza_accion_desconocida(self):
        respuesta = self.api.post(
            self._url(), {'cotizaciones': [1], 'accion': 'fusionar'}, format='json'
        )

        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('accion', respuesta.data)


class CrearInspeccionCotizacionesElegidasTests(TestCase):
    """Al crear la inspección el usuario elige qué cotizaciones se cargan."""

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
        cls.user = User.objects.create_superuser(username='elegir_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Elegir', razon_social='ELEG SA', ruc='888888888888'
        )
        cls.taller = Taller.objects.create(
            empresa=cls.empresa, nombre='Central', direccion='Av. Siempre Viva 742'
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa, nombre='Ana Lotus', identificacion='0912345678'
        )
        cls.vehiculo = Vehiculo.objects.create(placa='ELE-001', marca='Toyota', modelo='Corolla')
        cls.vehiculo.empresas.add(cls.empresa)
        cls.Cotizacion = Cotizacion
        cls.DetalleServicioCotizacion = DetalleServicioCotizacion
        cls.DetalleRepuestoCotizacion = DetalleRepuestoCotizacion

    def setUp(self):
        from apps.ordenes.models import RecepcionVehiculo

        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.user)
        self.api.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)
        self.recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-8001',
            estado='ACEPTADA',
            motivo_ingreso='Mantenimiento',
        )

    def _cotizacion(self, estado='ACEPTADA', numero='COT-8001', servicio='Cambio de aceite', repuesto='Filtro'):
        cotizacion = self.Cotizacion.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_cotizacion=numero,
            estado=estado,
        )
        self.DetalleServicioCotizacion.objects.create(
            cotizacion=cotizacion,
            codigo=f'SRV-{numero[-2:]}',
            descripcion=servicio,
            horas_estimadas=Decimal('2.00'),
            precio_unitario=Decimal('45.00'),
        )
        self.DetalleRepuestoCotizacion.objects.create(
            cotizacion=cotizacion,
            codigo_repuesto=f'REPL-{numero[-2:]}',
            descripcion=repuesto,
            cantidad=1,
            precio_unitario_referencial=Decimal('18.50'),
        )
        return cotizacion

    def _url(self):
        return f'/api/recepciones/{self.recepcion.id}/crear-inspeccion/'

    def _candidatas(self):
        return self.api.get(
            f'/api/recepciones/{self.recepcion.id}/cotizaciones-candidatas/'
        )

    # --- Listado para el modal ---

    def test_lista_las_cotizaciones_del_vehiculo_para_el_modal(self):
        self._cotizacion(estado='ACEPTADA', numero='COT-8001')
        self._cotizacion(estado='ENVIADA', numero='COT-8002', servicio='Balanceo', repuesto='Buje')

        respuesta = self._candidatas()

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertFalse(respuesta.data['tieneInspeccion'])
        self.assertEqual([c['numero'] for c in respuesta.data['aprobadas']], ['COT-8001'])
        self.assertEqual([c['numero'] for c in respuesta.data['enCurso']], ['COT-8002'])
        # El modal necesita el detalle para mostrar qué se va a cargar.
        self.assertEqual(len(respuesta.data['aprobadas'][0]['servicios']), 1)
        self.assertEqual(len(respuesta.data['aprobadas'][0]['repuestos']), 1)

    # --- Creación con selección ---

    def test_sin_seleccion_mantiene_el_comportamiento_anterior(self):
        from apps.ordenes.models import InspeccionVehiculo

        vigente = self._cotizacion(estado='ACEPTADA', numero='COT-8001')

        respuesta = self.api.post(self._url(), {}, format='json')

        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        inspeccion = InspeccionVehiculo.objects.get(pk=respuesta.data['id'])
        vigente.refresh_from_db()
        self.assertEqual(vigente.inspeccion_origen_id, inspeccion.id)
        self.assertEqual(inspeccion.servicios_detectados.count(), 1)

    def test_crea_solo_con_la_cotizacion_elegida(self):
        from apps.ordenes.models import InspeccionVehiculo

        elegida = self._cotizacion(estado='ACEPTADA', numero='COT-8001')
        otra = self._cotizacion(estado='ACEPTADA', numero='COT-8002', servicio='Balanceo', repuesto='Buje')

        respuesta = self.api.post(
            self._url(), {'cotizaciones': [elegida.id]}, format='json'
        )

        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        inspeccion = InspeccionVehiculo.objects.get(pk=respuesta.data['id'])
        elegida.refresh_from_db()
        otra.refresh_from_db()
        self.assertEqual(elegida.inspeccion_origen_id, inspeccion.id)
        self.assertEqual(elegida.recepcion_origen_id, self.recepcion.id)
        self.assertIsNone(otra.inspeccion_origen_id)
        # No se siembra lo que el usuario no marcó.
        self.assertEqual(inspeccion.servicios_detectados.count(), 1)
        self.assertEqual(inspeccion.servicios_detectados.get().descripcion, 'Cambio de aceite')

    def test_crea_con_varias_cotizaciones_y_no_duplica_items(self):
        from apps.ordenes.models import InspeccionVehiculo

        primera = self._cotizacion(estado='ACEPTADA', numero='COT-8001')
        segunda = self._cotizacion(
            estado='ACEPTADA', numero='COT-8002', servicio='Cambio de aceite', repuesto='Buje'
        )

        respuesta = self.api.post(
            self._url(), {'cotizaciones': [primera.id, segunda.id]}, format='json'
        )

        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        inspeccion = InspeccionVehiculo.objects.get(pk=respuesta.data['id'])
        # "Cambio de aceite" viene en las dos cotizaciones y se carga una sola vez.
        servicios = list(inspeccion.servicios_detectados.values_list('descripcion', flat=True))
        repuestos = list(inspeccion.repuestos_sugeridos.values_list('descripcion', flat=True))
        self.assertEqual(servicios.count('Cambio de aceite'), 1)
        self.assertEqual(sorted(repuestos), ['Buje', 'Filtro'])
        for cotizacion in (primera, segunda):
            cotizacion.refresh_from_db()
            self.assertEqual(cotizacion.inspeccion_origen_id, inspeccion.id)

    def test_sin_eleccion_explicita_no_carga_items(self):
        from apps.ordenes.models import InspeccionVehiculo

        self._cotizacion(estado='ACEPTADA', numero='COT-8001')

        respuesta = self.api.post(self._url(), {'cotizaciones': []}, format='json')

        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        inspeccion = InspeccionVehiculo.objects.get(pk=respuesta.data['id'])
        self.assertEqual(inspeccion.servicios_detectados.count(), 0)
        self.assertEqual(inspeccion.repuestos_sugeridos.count(), 0)

    def test_rechaza_cotizacion_convertida(self):
        # Una cotización con orden_trabajo_origen se considera "convertida"
        convertida = self._cotizacion(estado='ACEPTADA', numero='COT-8005')
        from apps.ordenes.models import OrdenTrabajo, TipoTrabajo
        orden_ficticia = OrdenTrabajo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_orden='OT-FICTICIA',
            tipo_trabajo=TipoTrabajo.MANTENIMIENTO,
        )
        convertida.orden_trabajo_origen = orden_ficticia
        convertida.save(update_fields=['orden_trabajo_origen', 'updated_at'])

        respuesta = self.api.post(self._url(), {'cotizaciones': [convertida.id]}, format='json')

        # El id no existe para este vehículo: error de validación, no 500.
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('cotizaciones', respuesta.data)

    def test_rechaza_cotizacion_de_otro_vehiculo(self):
        from apps.vehiculos.models import Vehiculo

        otro = Vehiculo.objects.create(placa='ELE-999', marca='Kia', modelo='Rio')
        otro.empresas.add(self.empresa)
        ajena = self.Cotizacion.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=otro,
            numero_cotizacion='COT-8500',
            estado='ACEPTADA',
        )

        respuesta = self.api.post(self._url(), {'cotizaciones': [ajena.id]}, format='json')

        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('no pertenece al vehículo', str(respuesta.data['cotizaciones']).lower())

    def test_rechaza_cotizacion_ya_ligada_a_otra_recepcion(self):
        from apps.ordenes.models import RecepcionVehiculo

        otra_recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion='REC-8002',
            estado='ACEPTADA',
            motivo_ingreso='Otro',
        )
        cotizacion = self._cotizacion(estado='ACEPTADA', numero='COT-8001')
        cotizacion.recepcion_origen = otra_recepcion
        cotizacion.save(update_fields=['recepcion_origen', 'updated_at'])

        respuesta = self.api.post(self._url(), {'cotizaciones': [cotizacion.id]}, format='json')

        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('otra recepción', str(respuesta.data['cotizaciones']).lower())

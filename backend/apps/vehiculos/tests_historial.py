"""Historial del vehículo (`GET /api/vehiculos/{id}/historial/`) y kilometraje de la orden."""
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.citas.models import Cita
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import (
    DetalleServicioInspeccion,
    DetalleServicioOrdenTrabajo,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)
from apps.vehiculos.models import Vehiculo, vincular_empresa


class HistorialBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.empresa = Empresa.objects.create(nombre_comercial='Historial', ruc='1790000000777')
        cls.taller_a = Taller.objects.create(empresa=cls.empresa, nombre='Norte', codigo_sucursal='001', direccion='x')
        cls.taller_b = Taller.objects.create(empresa=cls.empresa, nombre='Sur', codigo_sucursal='002', direccion='x')
        cls.admin = User.objects.create_superuser(username='historial-admin', password='x')
        cls.asesor = User.objects.create_user(username='historial-asesor', password='x', first_name='Ana')
        relacion = UsuarioEmpresa.objects.create(user=cls.asesor, empresa=cls.empresa, rol='ASESOR')
        relacion.talleres.set([cls.taller_a])
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Juan Perez', identificacion='1712345678')
        cls.vehiculo = Vehiculo.objects.create(placa='GSU544', marca='Toyota', modelo='Corolla', kilometraje_actual=100000)
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        cls.creados = 0

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        fijar_empresa(self.client, self.empresa)

    def url(self, **params):
        base = f'/api/vehiculos/{self.vehiculo.uuid}/historial/'
        if params:
            base += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        return base

    def visita(self, taller=None, km=None, servicio='Cambio de aceite', dias_atras=0, vehiculo=None, empresa=None):
        type(self).creados += 1
        n = self.creados
        empresa = empresa or self.empresa
        vehiculo = vehiculo or self.vehiculo
        cliente = self.cliente if empresa == self.empresa else Cliente.objects.create(
            empresa=empresa, nombre='Otro', identificacion=f'09{n:08d}'
        )
        comun = {'empresa': empresa, 'sucursal': taller or self.taller_a, 'cliente': cliente, 'vehiculo': vehiculo}
        orden = OrdenTrabajo.objects.create(
            **comun, numero_orden=f'OT-H{n}', estado='ENTREGADO', kilometraje=km,
        )
        OrdenTrabajo.objects.filter(pk=orden.pk).update(
            fecha_ingreso=timezone.now() - timedelta(days=dias_atras),
            fecha_entrega=timezone.now() - timedelta(days=dias_atras - 1),
        )
        DetalleServicioOrdenTrabajo.objects.create(
            orden_trabajo=orden, descripcion=servicio, horas_aplicadas=1, precio_unitario=Decimal('50'),
        )
        orden.refresh_from_db()
        orden.calcular_totales()
        orden.save()
        recepcion = RecepcionVehiculo.objects.create(
            **comun, numero_recepcion=f'REC-H{n}', orden_trabajo=orden, kilometraje_ingreso=km,
            motivo_ingreso='Mantenimiento programado',
        )
        RecepcionVehiculo.objects.filter(pk=recepcion.pk).update(
            fecha_ingreso=timezone.now() - timedelta(days=dias_atras)
        )
        inspeccion = InspeccionVehiculo.objects.create(
            **comun, numero_inspeccion=f'INS-H{n}', recepcion=recepcion, orden_trabajo=orden,
            motivo_ingreso='Mantenimiento', diagnostico_tecnico='Pastillas gastadas', estado='FINALIZADA',
        )
        Cotizacion.objects.create(
            **comun, numero_cotizacion=f'COT-H{n}', inspeccion_origen=inspeccion, recepcion_origen=recepcion,
        )
        Cita.objects.create(
            empresa=empresa, taller=taller or self.taller_a, cliente=cliente, vehiculo=vehiculo,
            fecha_cita=date.today(), hora_cita=time(9, 0), recepcion_generada=recepcion,
        )
        return orden, inspeccion


class HistorialTests(HistorialBase):

    def test_agrupa_los_documentos_en_una_visita_y_las_citas_sueltas_como_evento(self):
        self.visita(km=120000)
        Cita.objects.create(
            empresa=self.empresa, taller=self.taller_a, cliente=self.cliente, vehiculo=self.vehiculo,
            fecha_cita=date.today() - timedelta(days=40), hora_cita=time(10, 0), estado='CANCELADA',
        )

        datos = self.client.get(self.url()).json()

        self.assertEqual(datos['count'], 2)
        visita, evento = datos['results']
        self.assertEqual(visita['tipo'], 'visita')
        self.assertEqual(visita['kilometraje'], 120000)
        self.assertEqual(visita['servicios'], ['Cambio de aceite'])
        self.assertEqual(visita['taller']['nombre'], 'Norte')
        self.assertEqual(
            {tipo: len(docs) for tipo, docs in visita['documentos'].items()},
            {'cita': 1, 'recepcion': 1, 'inspeccion': 1, 'cotizacion': 1, 'orden': 1},
        )
        self.assertEqual(evento['tipo'], 'evento')
        self.assertEqual(evento['subtipo'], 'cita')
        self.assertEqual(datos['indicadores']['visitas'], 1)
        self.assertEqual(datos['indicadores']['gasto_total'], visita['total'])

    def test_no_muestra_visitas_de_otra_empresa_con_la_misma_placa(self):
        otra = Empresa.objects.create(nombre_comercial='Otra', ruc='1790000000888')
        taller_otra = Taller.objects.create(empresa=otra, nombre='Ajeno', codigo_sucursal='001', direccion='x')
        vincular_empresa(self.vehiculo, otra.pk)
        self.visita(km=110000, empresa=otra, taller=taller_otra)
        self.visita(km=120000)

        datos = self.client.get(self.url()).json()

        self.assertEqual(datos['count'], 1)
        self.assertEqual(datos['results'][0]['taller']['nombre'], 'Norte')

    def test_muestra_visitas_de_otros_talleres_aunque_el_usuario_no_opere_en_ellos(self):
        self.visita(km=110000, taller=self.taller_b, dias_atras=30)
        self.visita(km=120000, taller=self.taller_a)
        self.client.force_authenticate(user=self.asesor)
        self.client.defaults['HTTP_X_TALLER_ID'] = str(self.taller_a.uuid)

        respuesta = self.client.get(self.url())

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        reciente, antigua = respuesta.json()['results']
        self.assertEqual(antigua['taller']['nombre'], 'Sur')
        self.assertFalse(antigua['documentos']['orden'][0]['accesible'])
        self.assertTrue(reciente['documentos']['orden'][0]['accesible'])

    def test_filtra_por_texto_tipo_y_taller(self):
        self.visita(km=110000, servicio='Alineación', taller=self.taller_b, dias_atras=30)
        self.visita(km=120000, servicio='Cambio de aceite')

        por_texto = self.client.get(self.url(q='alinea')).json()
        por_taller = self.client.get(self.url(taller=self.taller_a.uuid)).json()
        por_tipo = self.client.get(self.url(tipo_trabajo='REPARACION')).json()

        self.assertEqual([v['servicios'] for v in por_texto['results']], [['Alineación']])
        self.assertEqual([v['taller']['nombre'] for v in por_taller['results']], ['Norte'])
        self.assertEqual(por_tipo['count'], 0)
        # Los indicadores no dependen de los filtros.
        self.assertEqual(por_texto['indicadores']['visitas'], 2)

    def test_pendientes_son_lo_diagnosticado_que_ninguna_orden_ejecuto(self):
        _, inspeccion = self.visita(km=120000)
        DetalleServicioInspeccion.objects.create(inspeccion=inspeccion, descripcion='Pastillas delanteras')
        DetalleServicioInspeccion.objects.create(inspeccion=inspeccion, descripcion='Cambio de aceite')

        pendientes = self.client.get(self.url()).json()['pendientes']

        self.assertEqual([p['descripcion'] for p in pendientes], ['Pastillas delanteras'])

    def test_km_por_anio(self):
        self.visita(km=100000, dias_atras=365)
        self.visita(km=118000, dias_atras=0)

        indicadores = self.client.get(self.url()).json()['indicadores']

        self.assertEqual(indicadores['km_por_anio'], 18000)

    def test_el_numero_de_consultas_no_crece_con_las_visitas(self):
        def consultas():
            with CaptureQueriesContext(connection) as capturadas:
                respuesta = self.client.get(self.url())
            self.assertEqual(respuesta.status_code, 200)
            return len(capturadas.captured_queries)

        for i in range(2):
            self.visita(km=100000 + i, dias_atras=10 - i)
        con_dos = consultas()
        for i in range(4):
            self.visita(km=110000 + i, dias_atras=5 - i)
        self.assertEqual(consultas(), con_dos)


class KilometrajeOrdenTests(HistorialBase):

    def payload(self, **extra):
        return {
            'sucursal': str(self.taller_a.uuid),
            'cliente': str(self.cliente.uuid),
            'vehiculo': str(self.vehiculo.uuid),
            'estado': 'PENDIENTE',
            'prioridad': 'MEDIA',
            'tipo_trabajo': 'MANTENIMIENTO',
            'fecha_ingreso': '2026-10-01T08:30:00Z',
            'mecanico_principal': str(self.admin.profile.uuid),
            **extra,
        }

    def test_es_obligatorio_al_crear(self):
        respuesta = self.client.post('/api/ordenes/ordenes-trabajo/', self.payload(), format='json')

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('kilometraje', respuesta.json())

    def test_no_puede_ser_menor_al_del_vehiculo(self):
        respuesta = self.client.post(
            '/api/ordenes/ordenes-trabajo/', self.payload(kilometraje=90000), format='json'
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('kilometraje', respuesta.json())

    def test_una_lectura_mayor_actualiza_el_vehiculo_y_se_puede_conservar(self):
        creada = self.client.post(
            '/api/ordenes/ordenes-trabajo/', self.payload(kilometraje=105000), format='json'
        )
        self.assertEqual(creada.status_code, 201, creada.content)
        self.vehiculo.refresh_from_db()
        self.assertEqual(self.vehiculo.kilometraje_actual, 105000)

        # Otra visita sube el odómetro; la orden conserva su propia lectura.
        Vehiculo.objects.filter(pk=self.vehiculo.pk).update(kilometraje_actual=130000)
        editada = self.client.patch(
            f"/api/ordenes/ordenes-trabajo/{creada.json()['id']}/",
            {'kilometraje': 105000, 'prioridad': 'ALTA'},
            format='json',
        )
        self.assertEqual(editada.status_code, 200, editada.content)

    def test_la_orden_generada_desde_cotizacion_toma_el_km_de_la_visita(self):
        comun = {'empresa': self.empresa, 'sucursal': self.taller_a, 'cliente': self.cliente, 'vehiculo': self.vehiculo}
        recepcion = RecepcionVehiculo.objects.create(**comun, numero_recepcion='REC-K1', kilometraje_ingreso=101000)
        inspeccion = InspeccionVehiculo.objects.create(
            **comun, numero_inspeccion='INS-K1', recepcion=recepcion, motivo_ingreso='x',
            kilometraje_diagnostico=101500,
        )
        cotizacion = Cotizacion.objects.create(
            **comun, numero_cotizacion='COT-K1', inspeccion_origen=inspeccion, recepcion_origen=recepcion,
            estado='ACEPTADA',
        )

        orden = cotizacion._crear_orden_trabajo()

        self.assertEqual(orden.kilometraje, 101500)

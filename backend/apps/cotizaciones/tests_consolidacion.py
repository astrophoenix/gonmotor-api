from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import OrdenTrabajo, InspeccionVehiculo, RecepcionVehiculo
from apps.vehiculos.models import Vehiculo, vincular_empresa

from .models import Cotizacion, DetalleServicioCotizacion, DetalleRepuestoCotizacion
from .services import aplicar_cotizaciones


class DatosConsolidacion:
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre_comercial='Consolidación', ruc='1790000000991')
        self.taller = Taller.objects.create(empresa=self.empresa, nombre='Centro', codigo_sucursal='001', direccion='x')
        self.cliente = Cliente.objects.create(empresa=self.empresa, nombre='Cliente', identificacion='1700000091')
        self.vehiculo = Vehiculo.objects.create(placa='COT9991', marca='Kia', modelo='Rio')
        vincular_empresa(self.vehiculo, self.empresa.pk)
        self.base = dict(empresa=self.empresa, sucursal=self.taller, cliente=self.cliente, vehiculo=self.vehiculo)
        self.usuario = get_user_model().objects.create_superuser(username='consolidacion')
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)
        fijar_empresa(self.client, self.empresa)

    def cotizacion(self, **extra):
        cotizacion = Cotizacion.objects.create(
            **(self.base | dict(numero_cotizacion=uuid4().hex[:12], estado='ACEPTADA', metodo_aceptacion='EMAIL') | extra)
        )
        DetalleServicioCotizacion.objects.create(
            cotizacion=cotizacion, codigo='S1', descripcion='Servicio', horas_estimadas=2,
            precio_unitario=Decimal('20'), descuento=Decimal('5'), iva_porcentaje=Decimal('0.15'),
        )
        DetalleRepuestoCotizacion.objects.create(
            cotizacion=cotizacion, codigo_repuesto='R1', descripcion='Repuesto', cantidad=2,
            precio_unitario_referencial=Decimal('10'), iva_porcentaje=Decimal('0'),
        )
        cotizacion.recalcular_totales()
        return cotizacion

    def orden(self, **extra):
        return OrdenTrabajo.objects.create(**(self.base | dict(numero_orden=uuid4().hex[:12], kilometraje=10) | extra))

    def generar(self, cotizacion, **payload):
        return self.client.post(f'/api/cotizaciones/{cotizacion.uuid}/generar_orden/', payload, format='json')


class ConsolidacionTests(DatosConsolidacion, TestCase):
    def test_metodo_individual_compatible(self):
        cotizacion = self.cotizacion()
        orden = cotizacion.generar_orden(usuario=self.usuario)
        self.assertEqual(cotizacion.orden_trabajo_aplicada_id, orden.pk)
        self.assertEqual(cotizacion.generar_orden().pk, orden.pk)

    def test_opcionales_se_copian_integros_sin_aceptacion_parcial(self):
        cotizacion = self.cotizacion()
        cotizacion.servicios.update(es_opcional=True)
        cotizacion.repuestos.update(es_opcional=True)
        orden = aplicar_cotizaciones([cotizacion, cotizacion])
        self.assertEqual(orden.servicios.count(), 1)
        self.assertEqual(orden.repuestos.count(), 1)
        self.assertEqual(orden.total, cotizacion.total)

    def test_endpoint_individual_y_procedencia(self):
        cotizacion = self.cotizacion()
        respuesta = self.generar(cotizacion)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        orden = OrdenTrabajo.objects.get(uuid=respuesta.json()['id'])
        self.assertEqual(orden.cotizacion_origen, cotizacion)
        self.assertEqual(list(orden.cotizaciones_aplicadas.all()), [cotizacion])
        servicio = orden.servicios.get()
        self.assertEqual(servicio.detalle_cotizacion_origen, cotizacion.servicios.get())
        self.assertEqual(servicio.servicio_codigo, 'S1')
        self.assertEqual(orden.repuestos.get().detalle_cotizacion_origen, cotizacion.repuestos.get())
        self.assertEqual(orden.total, cotizacion.total)
        detalle = self.client.get(f'/api/ordenes/ordenes-trabajo/{orden.uuid}/')
        self.assertEqual(detalle.status_code, 200, detalle.content)
        self.assertEqual(detalle.json()['cotizaciones_aplicadas'], [str(cotizacion.uuid)])
        self.assertEqual(detalle.json()['servicios'][0]['detalle_cotizacion_origen'], str(cotizacion.servicios.get().uuid))

    def test_endpoint_varias_cotizaciones_una_orden_sin_fusionar(self):
        primera, segunda = self.cotizacion(), self.cotizacion()
        respuesta = self.client.post('/api/cotizaciones/generar_orden_consolidada/',
                                    {'cotizaciones': [str(primera.uuid), str(segunda.uuid)]}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        orden = OrdenTrabajo.objects.get()
        self.assertEqual(orden.cotizacion_origen, primera)
        self.assertEqual(orden.cotizaciones_aplicadas.count(), 2)
        self.assertEqual(orden.servicios.count(), 2)
        self.assertEqual(orden.repuestos.count(), 2)
        self.assertEqual(orden.total, primera.total + segunda.total)

    def test_adicional_en_proceso_conserva_operacion(self):
        primera = self.cotizacion()
        orden = aplicar_cotizaciones([primera], usuario=self.usuario)
        orden.estado, orden.observaciones, orden.mecanico_principal = 'EN_PROCESO', 'En ejecución', self.usuario
        orden.save()
        antes = (orden.fecha_ingreso, orden.kilometraje, orden.cotizacion_origen_id)
        adicional = self.cotizacion(orden_trabajo_origen=orden)
        respuesta = self.generar(adicional, orden_trabajo=str(orden.uuid))
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        orden.refresh_from_db()
        self.assertEqual(OrdenTrabajo.objects.count(), 1)
        self.assertEqual((orden.estado, orden.observaciones, orden.mecanico_principal), ('EN_PROCESO', 'En ejecución', self.usuario))
        self.assertEqual((orden.fecha_ingreso, orden.kilometraje, orden.cotizacion_origen_id), antes)
        self.assertEqual(orden.cotizaciones_aplicadas.count(), 2)
        adicional.refresh_from_db()
        self.assertEqual(adicional.orden_trabajo_origen_id, orden.pk)

    def test_idempotencia_incluso_tras_completar(self):
        cotizaciones = [self.cotizacion(), self.cotizacion()]
        orden = aplicar_cotizaciones(cotizaciones)
        self.assertEqual(aplicar_cotizaciones(cotizaciones).pk, orden.pk)
        orden.estado = 'COMPLETADO'
        orden.save()
        aplicar_cotizaciones(cotizaciones, orden=orden)
        self.assertEqual(orden.servicios.count(), 2)
        self.assertEqual(orden.repuestos.count(), 2)
        self.assertEqual(OrdenTrabajo.objects.count(), 1)

    def test_no_aplicar_a_otra_orden(self):
        cotizacion = self.cotizacion()
        aplicar_cotizaciones([cotizacion])
        otra = self.orden()
        with self.assertRaisesMessage(ValueError, 'otra orden'):
            aplicar_cotizaciones([cotizacion], orden=otra)
        self.assertFalse(otra.servicios.exists())

    def test_no_crea_otra_orden_con_cotizacion_historica_sin_backfill(self):
        cotizacion = self.cotizacion()
        self.orden(cotizacion_origen=cotizacion)
        with self.assertRaisesMessage(ValueError, 'ya generó'):
            aplicar_cotizaciones([cotizacion])
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.orden_trabajo_aplicada_id)

    def test_estados_no_admitidos(self):
        for estado in ('COMPLETADO', 'ENTREGADO', 'CANCELADO'):
            with self.subTest(estado=estado), self.assertRaisesMessage(ValueError, 'Solo se pueden'):
                aplicar_cotizaciones([self.cotizacion()], orden=self.orden(estado=estado))

    def test_estados_admitidos(self):
        for estado in ('PENDIENTE', 'EN_ESPERA', 'EN_PROCESO'):
            orden = self.orden(estado=estado)
            aplicar_cotizaciones([self.cotizacion()], orden=orden)
            orden.refresh_from_db()
            self.assertEqual(orden.estado, estado)

    def test_rechaza_no_aceptada_y_revierte_todo(self):
        primera, segunda = self.cotizacion(), self.cotizacion(estado='ENVIADA')
        with self.assertRaisesMessage(ValueError, 'debe estar aceptada'):
            aplicar_cotizaciones([primera, segunda])
        self.assertFalse(OrdenTrabajo.objects.exists())
        primera.refresh_from_db()
        self.assertIsNone(primera.orden_trabajo_aplicada_id)

    def test_rechaza_sin_detalles_y_total_cero(self):
        cotizacion = self.cotizacion()
        cotizacion.servicios.all().delete()
        cotizacion.repuestos.all().delete()
        with self.assertRaisesMessage(ValueError, 'no tiene servicios'):
            aplicar_cotizaciones([cotizacion])
        DetalleServicioCotizacion.objects.create(cotizacion=cotizacion, descripcion='Gratis', precio_unitario=0)
        with self.assertRaisesMessage(ValueError, 'total 0'):
            aplicar_cotizaciones([cotizacion])

    def test_rollback_si_falla_copia(self):
        cotizacion = self.cotizacion()
        with patch('apps.cotizaciones.services._incorporar_detalles', side_effect=ValueError('fallo')):
            with self.assertRaisesMessage(ValueError, 'fallo'):
                aplicar_cotizaciones([cotizacion])
        self.assertFalse(OrdenTrabajo.objects.exists())
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.orden_trabajo_aplicada_id)

    def test_distintas_inspecciones_conservan_principal(self):
        recepcion = RecepcionVehiculo.objects.create(**self.base, numero_recepcion='REC1')
        primera = InspeccionVehiculo.objects.create(**self.base, recepcion=recepcion, numero_inspeccion='INS1')
        segunda = InspeccionVehiculo.objects.create(**self.base, recepcion=recepcion, numero_inspeccion='INS2', deleted_at=timezone.now())
        cot1, cot2 = self.cotizacion(inspeccion_origen=primera), self.cotizacion(inspeccion_origen=segunda)
        orden = aplicar_cotizaciones([cot1, cot2])
        self.assertEqual(orden.inspeccion.pk, primera.pk)
        segunda.refresh_from_db()
        self.assertIsNone(segunda.orden_trabajo_id)
        self.assertEqual(orden.servicios.get(detalle_cotizacion_origen__cotizacion=cot2).detalle_cotizacion_origen.cotizacion.inspeccion_origen_id, segunda.pk)

    def test_origen_vinculado_a_otra_ot_rechazado(self):
        orden = self.orden()
        for campo, origen in (
            ('orden_trabajo_origen', orden),
            ('inspeccion_origen', InspeccionVehiculo.objects.create(**self.base, numero_inspeccion='INS', orden_trabajo=orden)),
            ('recepcion_origen', RecepcionVehiculo.objects.create(**self.base, numero_recepcion='REC', orden_trabajo=orden)),
        ):
            cotizacion = self.cotizacion(**{campo: origen})
            with self.subTest(campo=campo), self.assertRaisesMessage(ValueError, f'pertenece a la orden {orden.numero_orden}'):
                aplicar_cotizaciones([cotizacion])
            with self.subTest(campo=campo), self.assertRaisesMessage(ValueError, 'otra orden'):
                aplicar_cotizaciones([cotizacion], orden=self.orden())

    def test_proteccion_comercial_y_reasignacion_cotizacion(self):
        cotizacion = self.cotizacion()
        aplicar_cotizaciones([cotizacion])
        for payload in ({'estado': 'ENVIADA'}, {'estado': 'ACEPTADA', 'validez_dias': 99}, {'cliente': str(self.cliente.uuid)}):
            respuesta = self.client.patch(f'/api/cotizaciones/{cotizacion.uuid}/', payload, format='json')
            self.assertEqual(respuesta.status_code, 400, respuesta.content)
        respuesta = self.client.delete(f'/api/cotizaciones/{cotizacion.uuid}/')
        self.assertEqual(respuesta.status_code, 400)
        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.estado, 'ACEPTADA')

    def test_proteccion_lineas_cotizacion_incluso_reasignacion_desde_otro_padre(self):
        cotizacion, libre = self.cotizacion(), self.cotizacion(estado='PENDIENTE')
        aplicar_cotizaciones([cotizacion])
        for recurso in ('servicios', 'repuestos'):
            detalle = getattr(cotizacion, recurso).get()
            url = f'/api/cotizaciones/{recurso}/{detalle.uuid}/'
            self.assertEqual(self.client.patch(url, {'descripcion': 'Cambio'}, format='json').status_code, 400)
            self.assertEqual(self.client.delete(url).status_code, 400)
            otro = getattr(libre, recurso).get()
            respuesta = self.client.patch(f'/api/cotizaciones/{recurso}/{otro.uuid}/', {'cotizacion': str(cotizacion.uuid)}, format='json')
            self.assertEqual(respuesta.status_code, 400, respuesta.content)
        with self.assertRaises(ProtectedError):
            cotizacion.servicios.get().delete()

    def test_proteccion_detalles_ot_y_ejecucion_permitida(self):
        orden = aplicar_cotizaciones([self.cotizacion()])
        for recurso, detalle in (('ot-servicios', orden.servicios.get()), ('ot-repuestos', orden.repuestos.get())):
            url = f'/api/ordenes/{recurso}/{detalle.uuid}/'
            self.assertEqual(self.client.patch(url, {'precio_unitario': '99'}, format='json').status_code, 400)
            self.assertEqual(self.client.delete(url).status_code, 400)
            self.assertEqual(self.client.patch(url, {'orden_trabajo': str(self.orden().uuid)}, format='json').status_code, 400)
        respuesta = self.client.patch(f'/api/ordenes/ot-servicios/{orden.servicios.get().uuid}/', {'completado': True}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    def test_no_reasignar_contexto_ot_ni_relaciones_aplicadas(self):
        cotizacion = self.cotizacion()
        orden = aplicar_cotizaciones([cotizacion])
        cliente = Cliente.objects.create(empresa=self.empresa, nombre='Otro', identificacion='1700000088')
        respuesta = self.client.patch(f'/api/ordenes/ordenes-trabajo/{orden.uuid}/', {'cliente': str(cliente.uuid)}, format='json')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        respuesta = self.client.post(f'/api/cotizaciones/{cotizacion.uuid}/relaciones/',
                                    {'tipo': 'orden', 'id': str(orden.uuid), 'accion': 'desvincular'}, format='json')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)

    def test_no_aplicar_ot_inactiva_o_eliminada(self):
        for cambios in ({'is_active': False}, {'deleted_at': timezone.now()}):
            with self.subTest(cambios=cambios), self.assertRaisesMessage(ValueError, 'no está activa'):
                aplicar_cotizaciones([self.cotizacion()], orden=self.orden(**cambios))

    def test_contexto_empresa_taller_cliente_vehiculo(self):
        otra_empresa = Empresa.objects.create(nombre_comercial='Otra', ruc='1790000000992')
        otro_taller = Taller.objects.create(empresa=self.empresa, nombre='Norte', codigo_sucursal='002', direccion='x')
        otro_cliente = Cliente.objects.create(empresa=self.empresa, nombre='Otro', identificacion='1700000087')
        otro_vehiculo = Vehiculo.objects.create(placa='COT9992', marca='Kia', modelo='Rio')
        for cambios in ({'empresa': otra_empresa}, {'sucursal': otro_taller}, {'cliente': otro_cliente}, {'vehiculo': otro_vehiculo}):
            cotizacion = self.cotizacion(**cambios)
            with self.subTest(cambios=cambios), self.assertRaisesMessage(ValueError, 'misma empresa'):
                aplicar_cotizaciones([self.cotizacion(), cotizacion])

    def test_aislamiento_empresa_y_taller_por_api(self):
        usuario = get_user_model().objects.create_user(username='asesor')
        relacion = UsuarioEmpresa.objects.create(user=usuario, empresa=self.empresa, rol='ASESOR')
        relacion.talleres.add(self.taller)
        otro_taller = Taller.objects.create(empresa=self.empresa, nombre='Norte', codigo_sucursal='002', direccion='x')
        otra_empresa = Empresa.objects.create(nombre_comercial='Ajena', ruc='1790000000993')
        taller_ajeno = Taller.objects.create(empresa=otra_empresa, nombre='Ajeno', codigo_sucursal='001', direccion='x')
        cliente_ajeno = Cliente.objects.create(empresa=otra_empresa, nombre='Ajeno', identificacion='1700000086')
        ajenas = [self.cotizacion(sucursal=otro_taller), self.cotizacion(empresa=otra_empresa, sucursal=taller_ajeno, cliente=cliente_ajeno)]
        self.client.force_authenticate(usuario)
        fijar_empresa(self.client, self.empresa)
        propia = self.cotizacion()
        for ajena in ajenas:
            respuesta = self.generar(propia, cotizaciones=[str(ajena.uuid)])
            self.assertEqual(respuesta.status_code, 400, respuesta.content)
        for orden in (self.orden(sucursal=otro_taller), self.orden(empresa=otra_empresa, sucursal=taller_ajeno, cliente=cliente_ajeno)):
            respuesta = self.generar(propia, orden_trabajo=str(orden.uuid))
            self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertFalse(propia.servicios.filter(detalle_orden_trabajo__isnull=False).exists())

    def test_permisos_ambos_recursos(self):
        cotizacion = self.cotizacion()
        from apps.authentication.models import PermisoEmpresa, Rol, Recurso
        usuario = get_user_model().objects.create_user(username='permisos')
        relacion = UsuarioEmpresa.objects.create(user=usuario, empresa=self.empresa, rol='ASESOR')
        relacion.talleres.add(self.taller)
        rol = Rol.objects.get(codigo='ASESOR')
        self.client.force_authenticate(usuario)
        fijar_empresa(self.client, self.empresa)
        for recurso in ('ordenes', 'cotizaciones'):
            permiso = PermisoEmpresa.objects.create(empresa=self.empresa, rol=rol, recurso=Recurso.objects.get(codigo=recurso), ver=True, modificar=False)
            for url, payload in ((f'/api/cotizaciones/{cotizacion.uuid}/generar_orden/', {}),
                                 ('/api/cotizaciones/generar_orden_consolidada/', {'cotizaciones': [str(cotizacion.uuid)]})):
                respuesta = self.client.post(url, payload, format='json')
                self.assertEqual(respuesta.status_code, 403, respuesta.content)
            permiso.delete()
        self.assertFalse(OrdenTrabajo.objects.exists())


class CorreccionesConsolidacionTests(DatosConsolidacion, TestCase):
    def recepcion(self, numero):
        return RecepcionVehiculo.objects.create(**self.base, numero_recepcion=numero)

    # 1. Visitas distintas
    def test_no_consolida_recepciones_distintas(self):
        primera, segunda = self.cotizacion(recepcion_origen=self.recepcion('R1')), self.cotizacion(recepcion_origen=self.recepcion('R2'))
        with self.assertRaisesMessage(ValueError, 'visitas distintas'):
            aplicar_cotizaciones([primera, segunda])
        self.assertFalse(OrdenTrabajo.objects.exists())

    def test_no_consolida_inspeccion_suelta_con_recepcion(self):
        suelta = InspeccionVehiculo.objects.create(**self.base, numero_inspeccion='INS-S')
        with self.assertRaisesMessage(ValueError, 'visitas distintas'):
            aplicar_cotizaciones([self.cotizacion(recepcion_origen=self.recepcion('R1')), self.cotizacion(inspeccion_origen=suelta)])

    def test_no_incorpora_otra_visita_a_ot_existente(self):
        orden = aplicar_cotizaciones([self.cotizacion(recepcion_origen=self.recepcion('R1'))])
        for otra in (self.cotizacion(recepcion_origen=self.recepcion('R2')), self.cotizacion(inspeccion_origen=InspeccionVehiculo.objects.create(**self.base, numero_inspeccion='INS-X'))):
            with self.subTest(otra=otra.pk), self.assertRaisesMessage(ValueError, 'visita distinta'):
                aplicar_cotizaciones([otra], orden=orden)
        sin_visita = self.orden()
        with self.assertRaisesMessage(ValueError, 'visita distinta'):
            aplicar_cotizaciones([self.cotizacion(recepcion_origen=self.recepcion('R3'))], orden=sin_visita)
        self.assertEqual(orden.cotizaciones_aplicadas.count(), 1)

    def test_incorpora_otra_inspeccion_de_la_misma_visita_sin_cambiar_principal(self):
        recepcion = self.recepcion('R1')
        primera = InspeccionVehiculo.objects.create(**self.base, recepcion=recepcion, numero_inspeccion='INS1')
        segunda = InspeccionVehiculo.objects.create(**self.base, recepcion=recepcion, numero_inspeccion='INS2', deleted_at=timezone.now())
        orden = aplicar_cotizaciones([self.cotizacion(inspeccion_origen=primera)])
        adicional = self.cotizacion(inspeccion_origen=segunda)
        respuesta = self.generar(adicional, orden_trabajo=str(orden.uuid))
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        orden.refresh_from_db()
        self.assertEqual(orden.inspeccion.pk, primera.pk)
        self.assertEqual(orden.cotizaciones_aplicadas.count(), 2)

    def test_principal_es_la_cotizacion_con_visita(self):
        recepcion = self.recepcion('R1')
        sin_visita, con_visita = self.cotizacion(), self.cotizacion(recepcion_origen=recepcion)
        orden = aplicar_cotizaciones([sin_visita, con_visita])
        recepcion.refresh_from_db()
        self.assertEqual(recepcion.orden_trabajo_id, orden.pk)
        self.assertEqual(orden.cotizaciones_aplicadas.count(), 2)

    # 2. Eliminación lógica de OT
    def test_no_elimina_ot_con_cotizaciones_aplicadas(self):
        cotizacion = self.cotizacion()
        orden = aplicar_cotizaciones([cotizacion])
        respuesta = self.client.delete(f'/api/ordenes/ordenes-trabajo/{orden.uuid}/')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn(cotizacion.numero_cotizacion, str(respuesta.content.decode()))
        orden.refresh_from_db()
        self.assertIsNone(orden.deleted_at)
        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.orden_trabajo_aplicada_id, orden.pk)

    def test_elimina_ot_pendiente_sin_cotizaciones_aplicadas(self):
        orden = self.orden()
        respuesta = self.client.delete(f'/api/ordenes/ordenes-trabajo/{orden.uuid}/')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    # 3. es_convertible / es_incorporable
    def representacion(self, cotizacion):
        respuesta = self.client.get(f'/api/cotizaciones/{cotizacion.uuid}/')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        datos = respuesta.json()
        return datos['es_convertible'], datos['es_incorporable'], datos['orden_trabajo_destino_id']

    def test_convertible_e_incorporable(self):
        libre = self.cotizacion()
        self.assertEqual(self.representacion(libre), (True, False, None))
        orden = self.orden()
        recepcion = self.recepcion('R1')
        recepcion.orden_trabajo = orden
        recepcion.save()
        for cambios in ({'orden_trabajo_origen': orden}, {'recepcion_origen': recepcion}):
            cotizacion = self.cotizacion(**cambios)
            with self.subTest(cambios=list(cambios)):
                self.assertEqual(self.representacion(cotizacion), (False, True, str(orden.uuid)))
                respuesta = self.generar(cotizacion)
                self.assertEqual(respuesta.status_code, 400, respuesta.content)
                self.assertIn('indica esa OT como destino', respuesta.content.decode())
        aplicar_cotizaciones([libre])
        self.assertEqual(self.representacion(libre), (False, False, None))
        self.assertEqual(self.representacion(self.cotizacion(estado='ENVIADA'))[:2], (False, False))

    # 4. Cotización vinculada manualmente como cotizacion_origen
    def test_vinculada_manualmente_se_incorpora_a_su_ot(self):
        cotizacion = self.cotizacion()
        orden = self.orden(cotizacion_origen=cotizacion)
        self.assertEqual(self.representacion(cotizacion), (False, True, str(orden.uuid)))
        with self.assertRaisesMessage(ValueError, 'ya generó'):
            aplicar_cotizaciones([cotizacion])
        with self.assertRaisesMessage(ValueError, 'no puede aplicarse a otra OT'):
            aplicar_cotizaciones([cotizacion], orden=self.orden())
        respuesta = self.generar(cotizacion, orden_trabajo=str(orden.uuid))
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        orden.refresh_from_db()
        self.assertEqual(list(orden.cotizaciones_aplicadas.all()), [cotizacion])
        self.assertEqual(orden.servicios.get().detalle_cotizacion_origen, cotizacion.servicios.get())
        self.assertEqual(orden.repuestos.get().detalle_cotizacion_origen, cotizacion.repuestos.get())
        self.assertEqual(orden.total, cotizacion.total)
        aplicar_cotizaciones([cotizacion], orden=orden)
        self.assertEqual(orden.servicios.count(), 1)

    def test_vinculada_con_lineas_sin_procedencia_se_rechaza_sin_tocarlas(self):
        from apps.ordenes.models import DetalleServicioOrdenTrabajo

        cotizacion = self.cotizacion()
        orden = self.orden(cotizacion_origen=cotizacion)
        servicio = cotizacion.servicios.get()
        # Línea idéntica a la cotizada pero sin relación explícita: no se infiere su procedencia.
        linea = DetalleServicioOrdenTrabajo.objects.create(
            orden_trabajo=orden, descripcion=servicio.descripcion, horas_aplicadas=servicio.horas_estimadas,
            precio_unitario=servicio.precio_unitario, descuento=servicio.descuento, iva_porcentaje=servicio.iva_porcentaje,
        )
        respuesta = self.generar(cotizacion, orden_trabajo=str(orden.uuid))
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertIn('No se puede confirmar', respuesta.content.decode())
        linea.refresh_from_db()
        self.assertIsNone(linea.detalle_cotizacion_origen_id)
        self.assertEqual(orden.servicios.count(), 1)
        self.assertFalse(orden.repuestos.exists())
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.orden_trabajo_aplicada_id)

    def test_vinculada_admite_lineas_explicitas_de_otras_cotizaciones(self):
        cotizacion = self.cotizacion()
        orden = self.orden(cotizacion_origen=cotizacion)
        aplicar_cotizaciones([self.cotizacion()], orden=orden)
        aplicar_cotizaciones([cotizacion], orden=orden)
        self.assertEqual(orden.servicios.count(), 2)
        self.assertEqual(orden.cotizaciones_aplicadas.count(), 2)

    def test_detalles_ya_enlazados_sin_marca_de_aplicada_se_rechazan(self):
        cotizacion = self.cotizacion()
        orden = aplicar_cotizaciones([cotizacion])
        Cotizacion.objects.filter(pk=cotizacion.pk).update(orden_trabajo_aplicada=None)
        OrdenTrabajo.objects.filter(pk=orden.pk).update(cotizacion_origen=None)
        with self.assertRaisesMessage(ValueError, 'ya figuran en una orden'):
            aplicar_cotizaciones([cotizacion], orden=self.orden())
        self.assertEqual(orden.servicios.count(), 1)


class ConcurrenciaConsolidacionTests(DatosConsolidacion, TransactionTestCase):
    def ejecutar_concurrentes(self, operaciones):
        barrera = Barrier(2)
        def ejecutar(operacion):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                try:
                    return operacion().pk
                except ValueError:
                    return None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = [executor.submit(ejecutar, op) for op in operaciones]
            return [f.result(timeout=20) for f in futuros]

    def test_creaciones_concurrentes_son_idempotentes(self):
        cotizaciones = [self.cotizacion(), self.cotizacion()]
        resultados = self.ejecutar_concurrentes([lambda: aplicar_cotizaciones(cotizaciones), lambda: aplicar_cotizaciones(list(reversed(cotizaciones)))])
        self.assertEqual(resultados[0], resultados[1])
        orden = OrdenTrabajo.objects.get()
        self.assertEqual(orden.servicios.count(), 2)
        self.assertEqual(orden.repuestos.count(), 2)

    def test_competencia_por_cotizacion_entre_dos_ot(self):
        cotizacion = self.cotizacion()
        orden1, orden2 = self.orden(), self.orden()
        resultados = self.ejecutar_concurrentes([lambda: aplicar_cotizaciones([cotizacion], orden=orden1), lambda: aplicar_cotizaciones([cotizacion], orden=orden2)])
        self.assertEqual(resultados.count(None), 1)
        self.assertEqual(orden1.servicios.count() + orden2.servicios.count(), 1)

    def test_incorporaciones_distintas_serializan_totales(self):
        primera, segunda = self.cotizacion(), self.cotizacion()
        orden = self.orden(estado='EN_PROCESO')
        self.ejecutar_concurrentes([lambda: aplicar_cotizaciones([primera], orden=orden), lambda: aplicar_cotizaciones([segunda], orden=orden)])
        orden.refresh_from_db()
        self.assertEqual(orden.total, primera.total + segunda.total)
        self.assertEqual(orden.servicios.count(), 2)

    def test_reapertura_concurrente_no_modifica_cotizacion_aplicada(self):
        from . import services
        from .views import CotizacionViewSet

        cotizacion = self.cotizacion()
        copiando, editando, continuar = Event(), Event(), Event()
        incorporar = services._incorporar_detalles
        actualizar = CotizacionViewSet.perform_update

        def copia_bloqueada(*args, **kwargs):
            copiando.set()
            if not continuar.wait(timeout=10):
                raise RuntimeError('No se liberó la incorporación.')
            return incorporar(*args, **kwargs)

        def editar_bloqueado(view, serializer):
            editando.set()
            return actualizar(view, serializer)

        def generar():
            close_old_connections()
            try:
                return aplicar_cotizaciones([cotizacion]).pk
            finally:
                connections.close_all()

        def reabrir():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.usuario)
                fijar_empresa(client, self.empresa)
                return client.patch(f'/api/cotizaciones/{cotizacion.uuid}/', {'estado': 'ENVIADA'}, format='json').status_code
            finally:
                connections.close_all()

        with patch.object(services, '_incorporar_detalles', side_effect=copia_bloqueada), patch.object(CotizacionViewSet, 'perform_update', editar_bloqueado):
            with ThreadPoolExecutor(max_workers=2) as executor:
                generacion = executor.submit(generar)
                try:
                    self.assertTrue(copiando.wait(timeout=10))
                    reapertura = executor.submit(reabrir)
                    self.assertTrue(editando.wait(timeout=10))
                finally:
                    continuar.set()
                self.assertIsNotNone(generacion.result(timeout=15))
                self.assertEqual(reapertura.result(timeout=15), 400)
        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.estado, 'ACEPTADA')
        self.assertIsNotNone(cotizacion.orden_trabajo_aplicada_id)

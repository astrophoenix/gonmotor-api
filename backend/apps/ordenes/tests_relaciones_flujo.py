from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.core.testing import fijar_empresa
from apps.cotizaciones.models import Cotizacion
from apps.cotizaciones.services import aplicar_cotizaciones
from apps.cotizaciones.tests_consolidacion import DatosConsolidacion
from apps.empresas.models import Empresa, Taller
from .models import InspeccionVehiculo, RecepcionVehiculo


class RelacionesDirigidasTests(DatosConsolidacion, TestCase):
    def recepcion(self, **extra):
        return RecepcionVehiculo.objects.create(**(self.base | {'numero_recepcion': uuid4().hex[:12], 'motivo_ingreso': 'Prueba'} | extra))

    def inspeccion(self, **extra):
        return InspeccionVehiculo.objects.create(**(self.base | {'numero_inspeccion': uuid4().hex[:12]} | extra))

    def url(self, obj):
        if isinstance(obj, Cotizacion):
            return f'/api/cotizaciones/{obj.uuid}/relaciones/'
        if isinstance(obj, RecepcionVehiculo):
            return f'/api/recepciones/{obj.uuid}/relaciones/'
        recurso = 'inspecciones' if isinstance(obj, InspeccionVehiculo) else 'ordenes-trabajo'
        return f'/api/ordenes/{recurso}/{obj.uuid}/relaciones/'

    def relacionar(self, obj, tipo, destino, accion='vincular'):
        return self.client.post(self.url(obj), {'tipo': tipo, 'id': str(destino.uuid), 'accion': accion}, format='json')

    def test_asociacion_operativa_no_incorpora_y_se_puede_quitar(self):
        inicial = self.cotizacion()
        orden = aplicar_cotizaciones([inicial])
        orden.estado = 'EN_PROCESO'
        orden.save(update_fields=['estado'])
        adicional = self.cotizacion(estado='PENDIENTE')
        total, servicios, repuestos = orden.total, orden.servicios.count(), orden.repuestos.count()
        respuesta = self.relacionar(adicional, 'orden', orden)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        adicional.refresh_from_db()
        orden.refresh_from_db()
        self.assertEqual(adicional.orden_trabajo_origen_id, orden.pk)
        self.assertIsNone(adicional.orden_trabajo_aplicada_id)
        self.assertEqual(orden.cotizacion_origen_id, inicial.pk)
        self.assertEqual((orden.total, orden.servicios.count(), orden.repuestos.count()), (total, servicios, repuestos))
        self.assertEqual(list(orden.cotizaciones_aplicadas.all()), [inicial])
        respuesta = self.relacionar(adicional, 'orden', orden, 'desvincular')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        adicional.refresh_from_db()
        self.assertIsNone(adicional.orden_trabajo_origen_id)

    def test_cotizacion_aceptada_no_cambia_referencias(self):
        cotizacion = self.cotizacion()
        for tipo, destino in [('recepcion', self.recepcion()), ('inspeccion', self.inspeccion()), ('orden', self.orden())]:
            respuesta = self.relacionar(cotizacion, tipo, destino)
            self.assertEqual(respuesta.status_code, 400, respuesta.content)
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.recepcion_origen_id)
        self.assertIsNone(cotizacion.inspeccion_origen_id)
        self.assertFalse(any(self.client.get(self.url(cotizacion)).json()['puede_editar'].values()))

    def test_aplicacion_conserva_origenes_y_bloquea_desvinculacion(self):
        recepcion = self.recepcion()
        inspeccion = self.inspeccion(recepcion=recepcion)
        inicial = self.cotizacion(recepcion_origen=recepcion, inspeccion_origen=inspeccion)
        orden = aplicar_cotizaciones([inicial])
        adicional = self.cotizacion(estado='PENDIENTE')
        for tipo, obj in [('recepcion', recepcion), ('inspeccion', inspeccion), ('orden', orden)]:
            respuesta = self.relacionar(adicional, tipo, obj)
            self.assertEqual(respuesta.status_code, 200, respuesta.content)
        adicional.refresh_from_db()
        adicional.estado = 'ACEPTADA'
        adicional.save(update_fields=['estado'])
        aplicar_cotizaciones([adicional], orden=orden)
        for tipo, obj in [('recepcion', recepcion), ('inspeccion', inspeccion), ('orden', orden)]:
            respuesta = self.relacionar(adicional, tipo, obj, 'desvincular')
            self.assertEqual(respuesta.status_code, 400, respuesta.content)
        adicional.refresh_from_db()
        self.assertEqual((adicional.recepcion_origen_id, adicional.inspeccion_origen_id), (recepcion.pk, inspeccion.pk))
        self.assertEqual(orden.cotizaciones_aplicadas.count(), 2)
        self.assertEqual(orden.servicios.filter(detalle_cotizacion_origen__cotizacion=adicional).count(), 1)

    def test_no_cambia_visita_de_inspeccion_con_cotizacion_aceptada(self):
        recepcion = self.recepcion()
        inspeccion = self.inspeccion(recepcion=recepcion)
        self.cotizacion(inspeccion_origen=inspeccion)
        respuesta = self.relacionar(inspeccion, 'recepcion', recepcion, 'desvincular')
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.recepcion_id, recepcion.pk)

    def test_no_reasigna_inspeccion_con_recepcion_explicita_en_cotizacion(self):
        primera, segunda = self.recepcion(), self.recepcion()
        inspeccion = self.inspeccion(recepcion=primera)
        self.cotizacion(estado='PENDIENTE', recepcion_origen=primera, inspeccion_origen=inspeccion)
        respuesta = self.relacionar(inspeccion, 'recepcion', primera, 'desvincular')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        respuesta = self.relacionar(inspeccion, 'recepcion', segunda)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        inspeccion.refresh_from_db()
        self.assertIsNone(inspeccion.recepcion_id)

    def test_inspeccion_finalizada_no_vincula_recepcion(self):
        inspeccion = self.inspeccion()
        InspeccionVehiculo.objects.filter(pk=inspeccion.pk).update(estado='FINALIZADA')
        respuesta = self.relacionar(inspeccion, 'recepcion', self.recepcion())
        self.assertEqual(respuesta.status_code, 400, respuesta.content)

    def test_no_confunde_recepciones_de_distintas_visitas(self):
        primera, segunda = self.recepcion(), self.recepcion()
        inspeccion = self.inspeccion(recepcion=primera)
        cotizacion = self.cotizacion(estado='PENDIENTE', inspeccion_origen=inspeccion)
        respuesta = self.relacionar(cotizacion, 'recepcion', segunda)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.recepcion_origen_id)

    def test_inspeccion_heredada_no_elude_contexto_del_cliente(self):
        from apps.clientes.models import Cliente

        otro_cliente = Cliente.objects.create(empresa=self.empresa, nombre='Otro cliente', identificacion='1700000092')
        recepcion = self.recepcion(cliente=otro_cliente)
        inspeccion = self.inspeccion(recepcion=recepcion, cliente=None)
        cotizacion = self.cotizacion(estado='PENDIENTE')
        respuesta = self.relacionar(cotizacion, 'inspeccion', inspeccion)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.inspeccion_origen_id)

    def test_no_asocia_ot_de_otra_visita_y_revierte_escritura(self):
        primera, segunda = self.recepcion(), self.recepcion()
        orden = self.orden()
        segunda.orden_trabajo = orden
        segunda.save(update_fields=['orden_trabajo'])
        cotizacion = self.cotizacion(estado='PENDIENTE', recepcion_origen=primera)
        respuesta = self.relacionar(cotizacion, 'orden', orden)
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        cotizacion.refresh_from_db()
        self.assertIsNone(cotizacion.orden_trabajo_origen_id)

    def test_no_asocia_ot_cerrada(self):
        respuesta = self.relacionar(self.cotizacion(estado='PENDIENTE'), 'orden', self.orden(estado='ENTREGADO'))
        self.assertEqual(respuesta.status_code, 400, respuesta.content)

    def test_ot_solo_consulta_relaciones(self):
        orden = self.orden()
        for tipo, destino in [('recepcion', self.recepcion()), ('inspeccion', self.inspeccion()), ('cotizacion', self.cotizacion())]:
            for accion in ('vincular', 'desvincular'):
                respuesta = self.relacionar(orden, tipo, destino, accion)
                self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertFalse(any(self.client.get(self.url(orden)).json()['puede_editar'].values()))

    def test_no_vincula_otro_taller_aunque_usuario_tenga_acceso(self):
        taller = Taller.objects.create(empresa=self.empresa, nombre='Otro', codigo_sucursal='002', direccion='x')
        respuesta = self.relacionar(self.cotizacion(estado='PENDIENTE'), 'recepcion', self.recepcion(sucursal=taller))
        self.assertEqual(respuesta.status_code, 400, respuesta.content)

    def test_no_vincula_otra_empresa(self):
        empresa = Empresa.objects.create(nombre_comercial='Otra', ruc='1790000000881')
        respuesta = self.relacionar(self.cotizacion(estado='PENDIENTE'), 'recepcion', self.recepcion(empresa=empresa))
        self.assertEqual(respuesta.status_code, 400, respuesta.content)

    def test_lectura_filtra_ot_de_taller_fuera_del_alcance(self):
        taller = Taller.objects.create(empresa=self.empresa, nombre='Otro', codigo_sucursal='002', direccion='x')
        orden = self.orden(sucursal=taller)
        cotizacion = self.cotizacion(estado='PENDIENTE', orden_trabajo_origen=orden)
        usuario = get_user_model().objects.create_user(username='asesor_relaciones')
        pertenencia = UsuarioEmpresa.objects.create(user=usuario, empresa=self.empresa, rol='ASESOR')
        pertenencia.talleres.set([self.taller])
        self.client.force_authenticate(usuario)
        datos = self.client.get(self.url(cotizacion)).json()
        self.assertEqual(datos['relaciones']['orden'], [])

    def test_lectura_respeta_permiso_del_documento_relacionado(self):
        recepcion = self.recepcion()
        self.cotizacion(recepcion_origen=recepcion)
        with patch('apps.ordenes.relaciones_flujo.permisos_en_request', return_value={'recepciones': ['ver', 'modificar']}):
            datos = self.client.get(self.url(recepcion)).json()
        self.assertEqual(datos['relaciones']['cotizacion'], [])

    def test_no_muestra_documento_eliminado_logicamente(self):
        recepcion = self.recepcion()
        cotizacion = self.cotizacion(estado='PENDIENTE', recepcion_origen=recepcion)
        RecepcionVehiculo.objects.filter(pk=recepcion.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.client.get(self.url(cotizacion)).json()['relaciones']['recepcion'], [])

    def test_exige_permiso_de_modificacion_en_ambos_documentos(self):
        cotizacion, recepcion = self.cotizacion(estado='PENDIENTE'), self.recepcion()
        with patch('apps.ordenes.relaciones_flujo.permisos_en_request', return_value={'cotizaciones': ['ver', 'modificar'], 'recepciones': ['ver']}):
            respuesta = self.relacionar(cotizacion, 'recepcion', recepcion)
        self.assertEqual(respuesta.status_code, 403, respuesta.content)


class RelacionesConcurrentesTests(DatosConsolidacion, TransactionTestCase):
    def test_dos_inspecciones_no_comparten_recepcion(self):
        recepcion = RecepcionVehiculo.objects.create(**self.base, numero_recepcion='REC-CONC', motivo_ingreso='Prueba')
        inspecciones = [InspeccionVehiculo.objects.create(**self.base, numero_inspeccion=f'INS-CONC-{i}') for i in range(2)]
        barrera = Barrier(2)

        def vincular(inspeccion):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.usuario)
                fijar_empresa(client, self.empresa)
                barrera.wait(timeout=10)
                return client.post(f'/api/ordenes/inspecciones/{inspeccion.uuid}/relaciones/',
                                   {'tipo': 'recepcion', 'id': str(recepcion.uuid), 'accion': 'vincular'}, format='json').status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(vincular, inspecciones))
        self.assertEqual(sorted(resultados), [200, 400])
        self.assertEqual(InspeccionVehiculo.objects.filter(recepcion=recepcion).count(), 1)

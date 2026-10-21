"""Historial de cambios de los documentos del flujo (`historial_documentos.py`)."""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import AccionEspecial, PermisoEspecial, Rol, UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import (
    DetalleServicioInspeccion, FotoOrdenTrabajo, FotoRecepcion, InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo,
)
from apps.vehiculos.models import Vehiculo, vincular_empresa
from .models import EventoDocumento
from .testing import fijar_empresa, firma_data_url, imagen_de_prueba

User = get_user_model()
Evento = EventoDocumento.Evento


class BaseHistorialTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Principal', ruc='1790000000011')
        cls.otra = Empresa.objects.create(nombre_comercial='Otra', ruc='1790000000012')
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Centro', codigo_sucursal='001', direccion='x')
        cls.taller_otra = Taller.objects.create(empresa=cls.otra, nombre='Ajeno', codigo_sucursal='001', direccion='x')
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000021')
        cls.cliente_otra = Cliente.objects.create(empresa=cls.otra, nombre='Ajeno', identificacion='1700000022')
        cls.vehiculo = Vehiculo.objects.create(placa='HIS0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        vincular_empresa(cls.vehiculo, cls.otra.pk)
        cls.admin = cls._usuario('hist_admin', cls.empresa, 'ADMIN_EMPRESA', first_name='Ana', last_name='Admin')
        cls.asesor = cls._usuario('hist_asesor', cls.empresa, 'ASESOR', [cls.taller])
        cls.admin_otra = cls._usuario('hist_admin_otra', cls.otra, 'ADMIN_EMPRESA')

    @staticmethod
    def _usuario(username, empresa, rol, talleres=None, **datos):
        user = User.objects.create_user(username=username, password='x', **datos)
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=empresa, rol=rol)
        if talleres:
            relacion.talleres.set(talleres)
        return user

    def setUp(self):
        self.orden = OrdenTrabajo.objects.create(
            empresa=self.empresa, sucursal=self.taller, cliente=self.cliente,
            vehiculo=self.vehiculo, numero_orden='OT-1', kilometraje=1000,
        )
        self.cotizacion = Cotizacion.objects.create(
            empresa=self.empresa, sucursal=self.taller, cliente=self.cliente,
            vehiculo=self.vehiculo, numero_cotizacion='COT-1',
        )
        self.client = self._cliente(self.admin)

    def _cliente(self, user, empresa=None):
        client = APIClient()
        client.force_authenticate(user=user)
        fijar_empresa(client, empresa or self.empresa)
        return client

    def eventos(self, documento):
        return EventoDocumento.objects.filter(documento=documento.uuid).order_by('id')

    def url_orden(self, sufijo=''):
        return f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/{sufijo}'

    def crear_linea(self, **extra):
        respuesta = self.client.post('/api/ordenes/ot-servicios/', {
            'orden_trabajo': str(self.orden.uuid), 'descripcion': 'Cambio de aceite',
            'horas_aplicadas': '1.00', 'precio_unitario': '25.00', 'descuento': '0.00', **extra,
        }, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        return respuesta.json()['id']



class HistorialDocumentosTests(BaseHistorialTests):
    """Órdenes y cotizaciones."""

    # --- Registro ----------------------------------------------------------

    def test_cambio_de_campo_registra_antes_y_despues_con_usuario(self):
        respuesta = self.client.patch(self.url_orden(), {'prioridad': 'ALTA'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        evento = self.eventos(self.orden).get()
        self.assertEqual(evento.evento, Evento.CAMPO)
        self.assertEqual((evento.etiqueta, evento.valor_anterior, evento.valor_nuevo), ('Prioridad', 'Media', 'Alta'))
        self.assertEqual(evento.usuario, self.admin)
        self.assertEqual(evento.usuario_nombre, 'Ana Admin')
        self.assertEqual((evento.empresa, evento.taller), (self.empresa, self.taller))

    def test_guardar_sin_cambios_no_deja_eventos(self):
        linea = self.crear_linea()
        inicial = self.eventos(self.orden).count()
        for _ in range(3):
            self.client.patch(self.url_orden(), {'prioridad': 'MEDIA', 'kilometraje': 1000}, format='json')
            self.client.put(f'/api/ordenes/ot-servicios/{linea}/', {
                'orden_trabajo': str(self.orden.uuid), 'descripcion': 'Cambio de aceite',
                'horas_aplicadas': '1', 'precio_unitario': '25', 'descuento': '0',
            }, format='json')
        self.assertEqual(self.eventos(self.orden).count(), inicial)

    def test_lineas_se_registran_en_su_orden(self):
        linea = self.crear_linea()
        self.client.patch(f'/api/ordenes/ot-servicios/{linea}/', {'precio_unitario': '30.00'}, format='json')
        self.client.delete(f'/api/ordenes/ot-servicios/{linea}/')
        filas = list(self.eventos(self.orden).values_list('evento', 'etiqueta', 'valor_anterior', 'valor_nuevo'))
        self.assertEqual(filas, [
            (Evento.LINEA_AGREGADA, 'Servicio: Cambio de aceite', '', '1.00 × 25.00'),
            (Evento.LINEA_EDITADA, 'Servicio: Cambio de aceite · Precio unitario', '25.00', '30.00'),
            (Evento.LINEA_ELIMINADA, 'Servicio: Cambio de aceite', '1.00 × 30.00', ''),
        ])

    def test_operacion_agrupa_las_peticiones_de_un_guardado(self):
        operacion = str(uuid.uuid4())
        self.client.credentials(HTTP_X_OPERACION_ID=operacion)
        self.client.patch(self.url_orden(), {'prioridad': 'ALTA'}, format='json')
        self.crear_linea()
        self.client.credentials(HTTP_X_OPERACION_ID='no-es-uuid')
        self.client.patch(self.url_orden(), {'prioridad': 'BAJA'}, format='json')
        operaciones = list(self.eventos(self.orden).values_list('operacion', flat=True))
        self.assertEqual([str(o) for o in operaciones[:2]], [operacion, operacion])
        self.assertNotEqual(str(operaciones[2]), operacion)

    def test_estado_de_cotizacion_y_lineas(self):
        respuesta = self.client.post('/api/cotizaciones/servicios/', {
            'cotizacion': str(self.cotizacion.uuid), 'descripcion': 'Alineación', 'precio_unitario': '20.00',
        }, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.client.patch(f'/api/cotizaciones/servicios/{respuesta.json()["id"]}/', {'descuento': '5.00'}, format='json')
        self.client.patch(f'/api/cotizaciones/{self.cotizacion.uuid}/', {'estado': 'ENVIADA'}, format='json')
        filas = list(self.eventos(self.cotizacion).values_list('evento', 'valor_anterior', 'valor_nuevo'))
        self.assertEqual(filas, [
            (Evento.LINEA_AGREGADA, '', '1.00 × 20.00'),
            (Evento.LINEA_EDITADA, '0.00', '5.00'),
            (Evento.ESTADO, 'Pendiente', 'Enviada al Cliente'),
        ])

    def test_fallo_del_historial_revierte_la_operacion(self):
        with patch('apps.core.historial_documentos.EventoDocumento.objects.bulk_create', side_effect=DatabaseError('x')):
            with self.assertRaises(DatabaseError):
                self.client.patch(self.url_orden(), {'prioridad': 'ALTA'}, format='json')
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.prioridad, 'MEDIA')

    # --- Consulta ----------------------------------------------------------

    def test_admin_consulta_el_historial_mas_reciente_primero(self):
        self.client.patch(self.url_orden(), {'prioridad': 'ALTA'}, format='json')
        self.client.patch(self.url_orden(), {'prioridad': 'BAJA'}, format='json')
        respuesta = self.client.get(self.url_orden('historial/'))
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        datos = respuesta.json()
        self.assertEqual(datos['count'], 2)
        self.assertEqual(datos['results'][0]['valor_nuevo'], 'Baja')
        self.assertEqual(set(datos['results'][0]), {
            'id', 'fecha', 'usuario_nombre', 'operacion', 'evento', 'etiqueta', 'valor_anterior', 'valor_nuevo',
        })

    def test_consulta_exige_la_accion_especial(self):
        asesor = self._cliente(self.asesor)
        self.client.patch(self.url_orden(), {'prioridad': 'ALTA'}, format='json')
        self.assertEqual(asesor.get(self.url_orden('historial/')).status_code, 403)
        PermisoEspecial.objects.create(
            rol=Rol.objects.get(codigo='ASESOR'), accion=AccionEspecial.objects.get(codigo='historial_ordenes'),
        )
        self.assertEqual(asesor.get(self.url_orden('historial/')).status_code, 200)
        # La acción de órdenes no abre el historial de cotizaciones.
        self.assertEqual(asesor.get(f'/api/cotizaciones/{self.cotizacion.uuid}/historial/').status_code, 403)

    def test_historial_de_otra_empresa_no_se_ve(self):
        self.client.patch(self.url_orden(), {'prioridad': 'ALTA'}, format='json')
        ajeno = self._cliente(self.admin_otra, self.otra)
        self.assertEqual(ajeno.get(self.url_orden('historial/')).status_code, 404)

    def test_historial_es_solo_lectura(self):
        for metodo in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client, metodo)(self.url_orden('historial/')).status_code, 405)

    def test_me_publica_las_acciones_del_rol(self):
        acciones = self.client.get('/api/auth/me/').json()['acciones']
        self.assertIn('historial_ordenes', acciones)
        self.assertNotIn('historial_ordenes', self._cliente(self.asesor).get('/api/auth/me/').json()['acciones'])


class HistorialRecepcionInspeccionTests(BaseHistorialTests):
    """Recepción e inspección."""

    def setUp(self):
        super().setUp()
        self.recepcion = RecepcionVehiculo.objects.create(
            empresa=self.empresa, sucursal=self.taller, cliente=self.cliente,
            vehiculo=self.vehiculo, numero_recepcion='REC-1', kilometraje_ingreso=1000,
        )
        self.inspeccion = InspeccionVehiculo.objects.create(
            empresa=self.empresa, sucursal=self.taller, recepcion=self.recepcion, cliente=self.cliente,
            vehiculo=self.vehiculo, numero_inspeccion='INS-1', motivo_ingreso='Ruido',
        )
        # Editar una recepción exige sus fotos obligatorias.
        for tipo in FotoRecepcion.VISTAS_OBLIGATORIAS:
            FotoRecepcion.objects.create(recepcion=self.recepcion, tipo_vista=tipo, imagen=f'ordenes/recepcion_fotos/{tipo}.jpg')

    def test_firma_registra_estado_y_firma(self):
        url = f'/api/recepciones/{self.recepcion.uuid}/'
        respuesta = self.client.post(f'{url}firmar/', {'firma_cliente': firma_data_url()}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        filas = {e.etiqueta: (e.evento, e.valor_anterior, e.valor_nuevo) for e in self.eventos(self.recepcion)}
        self.assertEqual(filas['Estado'], (Evento.ESTADO, 'Pendiente', 'Aceptada'))
        self.assertEqual(filas['Firma del cliente'], (Evento.CAMPO, '', 'Registrada'))
        self.assertIn('Fecha de firma', filas)

    def test_cambio_de_pertenencias_y_kilometraje_de_recepcion(self):
        respuesta = self.client.patch(
            f'/api/recepciones/{self.recepcion.uuid}/',
            {'kilometraje_ingreso': 1500, 'pertenencia_dinero': True}, format='json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        filas = set(self.eventos(self.recepcion).values_list('etiqueta', 'valor_anterior', 'valor_nuevo'))
        self.assertEqual(filas, {
            ('Kilometraje de ingreso', '1000', '1500'),
            ('Pertenencia: dinero en efectivo', 'No', 'Sí'),
        })
        self.assertEqual(self.client.get(f'/api/recepciones/{self.recepcion.uuid}/historial/').json()['count'], 2)

    def test_estado_y_lineas_de_inspeccion(self):
        respuesta = self.client.post('/api/ordenes/inspeccion-servicios/', {
            'inspeccion': str(self.inspeccion.uuid), 'descripcion': 'Revisar frenos',
            'horas_estimadas': '2.00', 'precio_referencial': '15.00',
        }, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        respuesta = self.client.patch(
            f'/api/ordenes/inspecciones/{self.inspeccion.uuid}/', {'estado': 'EN_PROCESO'}, format='json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        filas = list(self.eventos(self.inspeccion).values_list('evento', 'valor_anterior', 'valor_nuevo'))
        self.assertEqual(filas, [
            (Evento.LINEA_AGREGADA, '', '2.00 × 15.00'),
            (Evento.ESTADO, 'Pendiente', 'En Proceso'),
        ])

    def test_cotizacion_desde_inspeccion_registra_su_cierre(self):
        DetalleServicioInspeccion.objects.create(
            inspeccion=self.inspeccion, descripcion='Frenos', horas_estimadas=1, precio_referencial=10,
        )
        respuesta = self.client.post('/api/cotizaciones/', {
            'cliente': str(self.cliente.uuid), 'vehiculo': str(self.vehiculo.uuid),
            'inspeccion_origen': str(self.inspeccion.uuid),
        }, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        evento = self.eventos(self.inspeccion).get()
        self.assertEqual((evento.evento, evento.valor_nuevo), (Evento.ESTADO, 'Finalizada'))

    def test_historial_de_recepcion_exige_su_accion(self):
        asesor = self._cliente(self.asesor)
        self.assertEqual(asesor.get(f'/api/recepciones/{self.recepcion.uuid}/historial/').status_code, 403)
        self.assertEqual(asesor.get(f'/api/ordenes/inspecciones/{self.inspeccion.uuid}/historial/').status_code, 403)

    def test_vinculo_se_registra_en_los_dos_documentos(self):
        url = f'/api/cotizaciones/{self.cotizacion.uuid}/relaciones/'
        for accion in ('vincular', 'desvincular'):
            respuesta = self.client.post(url, {'tipo': 'recepcion', 'id': str(self.recepcion.uuid), 'accion': accion}, format='json')
            self.assertEqual(respuesta.status_code, 200, respuesta.content)
        filas = list(self.eventos(self.cotizacion).values_list('evento', 'etiqueta', 'valor_anterior', 'valor_nuevo'))
        self.assertEqual(filas, [
            (Evento.VINCULO, 'Vínculo: Recepción', '', 'REC-1'),
            (Evento.VINCULO, 'Vínculo: Recepción', 'REC-1', ''),
        ])
        self.assertEqual(
            list(self.eventos(self.recepcion).values_list('etiqueta', 'valor_nuevo')),
            [('Vínculo: Cotización', 'COT-1'), ('Vínculo: Cotización', '')],
        )

    def test_reemplazo_de_foto_de_recepcion(self):
        archivo = SimpleUploadedFile('frontal.png', imagen_de_prueba(), content_type='image/png')
        respuesta = self.client.patch(f'/api/recepciones/{self.recepcion.uuid}/', {'foto_FRONTAL': archivo}, format='multipart')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        evento = self.eventos(self.recepcion).get()
        self.assertEqual(evento.evento, Evento.FOTO_REEMPLAZADA)
        self.assertEqual(evento.etiqueta, f'Foto: {FotoRecepcion.TipoVista.FRONTAL.label}')
        self.assertEqual(evento.valor_nuevo, 'Nueva imagen')

    def test_baja_de_foto_de_orden_y_alta_sin_evento(self):
        foto = FotoOrdenTrabajo.objects.create(orden_trabajo=self.orden, imagen='ordenes/orden_fotos/x.jpg', descripcion='Pastillas gastadas')
        respuesta = self.client.delete(f'/api/ordenes/orden-fotos/{foto.uuid}/')
        self.assertIn(respuesta.status_code, (200, 204), respuesta.content)
        evento = self.eventos(self.orden).get()
        self.assertEqual((evento.evento, evento.etiqueta), (Evento.FOTO_ELIMINADA, 'Foto: Pastillas gastadas'))
        self.assertTrue(evento.valor_anterior.startswith('Subida el '))

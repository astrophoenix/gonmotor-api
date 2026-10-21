from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import DatabaseError
from rest_framework.test import APITestCase

from apps.authentication.models import Permiso, Recurso, Rol, UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.empresas.models import Empresa, Taller
from .models import RegistroAuditoria
from .testing import fijar_empresa


class AuditoriaTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Principal', ruc='1790000000001')
        cls.otra = Empresa.objects.create(nombre_comercial='Otra', ruc='1790000000002')
        cls.admin = User.objects.create_user('admin_audit', password='Prueba-auditoria-2026!')
        cls.operador = User.objects.create_user('operador_audit', password='Prueba-auditoria-2026!')
        UsuarioEmpresa.objects.create(user=cls.admin, empresa=cls.empresa, rol='ADMIN_EMPRESA')
        UsuarioEmpresa.objects.create(user=cls.operador, empresa=cls.empresa, rol='ASESOR')
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Centro')
        cls.ajeno = Taller.objects.create(empresa=cls.otra, nombre='Ajeno')

    def setUp(self):
        self.client.force_authenticate(self.admin)
        fijar_empresa(self.client, self.empresa)

    def crear(self, **extra):
        return self.client.post('/api/clientes/', {
            'nombre': 'Cliente de prueba', 'tipo_identificacion': 'C',
            'identificacion': '1234567890', **extra,
        }, format='multipart')

    def test_alta_edicion_borrado_y_lectura_sin_duplicar(self):
        response = self.crear()
        self.assertEqual(response.status_code, 201, response.data)
        registro = RegistroAuditoria.objects.get()
        self.assertEqual(registro.usuario, self.admin)
        self.assertEqual(registro.empresa, self.empresa)
        self.assertEqual(registro.accion, 'create')
        self.assertEqual(registro.registro_id, response.data['id'])
        url = f"/api/clientes/{response.data['id']}/"
        self.assertEqual(self.client.patch(url, {'nombre': 'Editado'}, format='multipart').status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(set(RegistroAuditoria.objects.values_list('accion', flat=True)), {'create', 'partial_update', 'destroy'})
        self.client.get('/api/auditoria/')
        self.client.get('/api/clientes/')
        self.assertEqual(RegistroAuditoria.objects.count(), 3)

    def test_error_de_validacion_no_genera_accion(self):
        response = self.client.post('/api/clientes/', {}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(RegistroAuditoria.objects.exists())

    def test_fallo_del_registro_revierte_la_operacion(self):
        with patch('apps.core.auditoria.RegistroAuditoria.objects.create', side_effect=DatabaseError('bitacora inaccesible')):
            with self.assertRaises(DatabaseError):
                self.crear()
        self.assertFalse(Cliente.objects.exists())
        self.assertFalse(RegistroAuditoria.objects.exists())

    def test_empresa_ajena_no_se_puede_consultar_ni_inyectar(self):
        RegistroAuditoria.objects.create(empresa=self.otra, usuario_nombre='Oculto', modulo='clientes', accion='create', metodo='POST')
        self.crear(empresa=str(self.otra.uuid))
        response = self.client.get('/api/auditoria/', {'empresa': str(self.otra.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertNotEqual(response.data['results'][0]['usuario_nombre'], 'Oculto')
        self.assertEqual(RegistroAuditoria.objects.get(usuario=self.admin).empresa, self.empresa)

    def test_permiso_delegado_y_revocacion(self):
        self.client.force_authenticate(self.operador)
        fijar_empresa(self.client, self.empresa)
        self.assertEqual(self.client.get('/api/auditoria/').status_code, 403)
        permiso = Permiso.objects.create(
            rol=Rol.objects.get(codigo='ASESOR'), recurso=Recurso.objects.get(codigo='auditoria'), ver=True,
        )
        self.assertEqual(self.client.get('/api/auditoria/').status_code, 200)
        permiso.ver = False
        permiso.save()
        self.assertEqual(self.client.get('/api/auditoria/').status_code, 403)

    def test_api_solo_lectura_y_sin_sesion(self):
        for metodo in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client, metodo)('/api/auditoria/').status_code, 405)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get('/api/auditoria/').status_code, 401)

    def test_taller_validado_y_no_cabecera_ajena(self):
        self.client.credentials(HTTP_X_TALLER_ID=str(self.taller.uuid))
        self.assertEqual(self.crear().status_code, 201)
        self.assertEqual(RegistroAuditoria.objects.get().taller, self.taller)
        self.client.credentials(HTTP_X_TALLER_ID=str(self.ajeno.uuid))
        self.assertEqual(self.crear(identificacion='9999999999').status_code, 201)
        self.assertIsNone(RegistroAuditoria.objects.first().taller)

    def test_filtros_fecha_ecuador_y_busqueda(self):
        RegistroAuditoria.objects.create(
            empresa=self.empresa, usuario_nombre='Ana', modulo='clientes', accion='create', metodo='POST',
            fecha=datetime(2026, 10, 21, 0, 30, tzinfo=ZoneInfo('UTC')),
        )
        RegistroAuditoria.objects.create(
            empresa=self.empresa, usuario_nombre='Otro', modulo='ordenes', accion='update', metodo='PATCH',
            fecha=datetime(2026, 10, 21, 5, 30, tzinfo=ZoneInfo('UTC')),
        )
        response = self.client.get('/api/auditoria/', {'desde': '2026-10-20', 'hasta': '2026-10-20', 'modulo': 'clientes', 'accion': 'create', 'search': 'Ana'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['usuario_nombre'], 'Ana')
        for params in ({'desde': 'ayer'}, {'desde': '2026-10-22', 'hasta': '2026-10-20'}, {'hasta': '9999-12-31'}):
            self.assertEqual(self.client.get('/api/auditoria/', params).status_code, 400)

    def test_login_con_jwt_registra_usuario_sin_secretos(self):
        self.client.force_authenticate(user=None)
        response = self.client.post('/api/auth/login/', {'username': self.admin.username, 'password': 'Prueba-auditoria-2026!'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        registro = RegistroAuditoria.objects.get()
        self.assertEqual(registro.accion, 'iniciar_sesion')
        self.assertEqual(registro.usuario, self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        response = self.crear()
        self.assertEqual(response.status_code, 201, response.data)
        listado = self.client.get('/api/auditoria/').json()
        self.assertNotIn('Prueba-auditoria', str(listado))
        self.assertNotIn('access', str(listado))
        self.assertEqual(listado['count'], 2)

    def test_nombre_historico_se_conserva_al_eliminar_usuario(self):
        registro = RegistroAuditoria.objects.create(
            empresa=self.empresa, usuario=self.operador, usuario_nombre='Nombre anterior',
            modulo='clientes', accion='create', metodo='POST',
        )
        self.operador.delete()
        registro.refresh_from_db()
        self.assertIsNone(registro.usuario)
        self.assertEqual(registro.usuario_nombre, 'Nombre anterior')

    def test_paginacion(self):
        RegistroAuditoria.objects.bulk_create([
            RegistroAuditoria(empresa=self.empresa, usuario_nombre='Nombre histórico', modulo='clientes', accion='create', metodo='POST')
            for _ in range(12)
        ])
        response = self.client.get('/api/auditoria/')
        self.assertEqual(response.data['count'], 12)
        self.assertEqual(len(response.data['results']), 10)
        self.assertIsNotNone(response.data['next'])
        self.assertEqual(len(self.client.get('/api/auditoria/?page=2').data['results']), 2)


    def test_exportaciones_filtradas_sin_paginacion_y_aisladas_por_empresa(self):
        from io import BytesIO
        from openpyxl import load_workbook

        RegistroAuditoria.objects.bulk_create([
            RegistroAuditoria(
                empresa=self.empresa, usuario_nombre='Ana exportada', modulo='clientes',
                accion='create', metodo='POST',
                fecha=datetime(2026, 10, 21, 0, 30, tzinfo=ZoneInfo('UTC')),
            ) for _ in range(12)
        ] + [
            RegistroAuditoria(empresa=self.otra, usuario_nombre='Empresa ajena', modulo='clientes', accion='create', metodo='POST'),
            RegistroAuditoria(empresa=self.empresa, usuario_nombre='Excluir por módulo', modulo='ordenes', accion='create', metodo='POST'),
            RegistroAuditoria(empresa=self.empresa, usuario_nombre='Excluir por acción', modulo='clientes', accion='update', metodo='PATCH'),
        ])
        filtros = {'modulo': 'clientes', 'accion': 'create', 'search': 'Ana', 'desde': '2026-10-20', 'hasta': '2026-10-20', 'page': 2}
        for ruta in ('export-excel', 'exportar-pdf'):
            with self.subTest(ruta=ruta):
                # Sin compresión para comprobar el contenido PDF sin dependencias adicionales.
                with patch('reportlab.rl_config.pageCompression', 0):
                    response = self.client.get(f'/api/auditoria/{ruta}/', filtros)
                self.assertEqual(response.status_code, 200)
                contenido = b''.join(response.streaming_content) if response.streaming else response.content
                if ruta == 'export-excel':
                    ws = load_workbook(BytesIO(contenido)).active
                    filas = list(ws.values)
                    self.assertEqual(sum(fila[1] == 'Ana exportada' for fila in filas), 12)
                    self.assertTrue(any(fila[0] == '20/10/2026 19:30:00' for fila in filas))
                    texto = str(filas)
                else:
                    self.assertTrue(contenido.startswith(b'%PDF'))
                    texto = contenido.decode('latin-1')
                    self.assertEqual(texto.count('Ana exportada'), 12)
                    self.assertIn('20/10/2026 19:30:00', texto)
                self.assertNotIn('Empresa ajena', texto)
                self.assertNotIn('Excluir', texto)
                self.assertEqual(self.client.get(f'/api/auditoria/{ruta}/', {'desde': 'ayer'}).status_code, 400)

    def test_exportaciones_requieren_permiso_y_respetan_limites(self):
        from django.test import override_settings

        self.crear()
        for ruta, setting in (('exportar-pdf', 'EXPORT_MAX_FILAS_PDF'), ('export-excel', 'EXPORT_MAX_FILAS_EXCEL')):
            with self.subTest(ruta=ruta):
                with override_settings(**{setting: 0}):
                    self.assertEqual(self.client.get(f'/api/auditoria/{ruta}/').status_code, 400)
                self.client.force_authenticate(self.operador)
                fijar_empresa(self.client, self.empresa)
                self.assertEqual(self.client.get(f'/api/auditoria/{ruta}/').status_code, 403)
                self.client.force_authenticate(user=None)
                self.assertEqual(self.client.get(f'/api/auditoria/{ruta}/').status_code, 401)
                self.client.force_authenticate(self.admin)
                fijar_empresa(self.client, self.empresa)

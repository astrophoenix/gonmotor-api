from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

import importlib
import uuid

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.authentication.matriz_roles import (
    ACCIONES,
    MATRIZ_ROLES,
    RECURSOS,
    ROLES_TODOS_TALLERES,
    puede,
)
from apps.authentication.models import UserProfile, UsuarioEmpresa
from apps.authentication.models import PermisoEmpresa, PermisoEspecialEmpresa, Rol
from apps.authentication.permissions import EsAdminDeEmpresa, TienePermiso
from apps.authentication.permisos_repo import acciones_especiales_de_rol, permisos_de_rol
from apps.authentication.serializers import EmpleadoWriteSerializer
from apps.authentication.utils import (
    contexto_de_usuario,
    get_contexto_desde_request,
    get_empresa_id_desde_request,
    get_rol_desde_request,
    get_taller_desde_request,
    talleres_permitidos,
    validar_talleres_asignables,
)
from apps.empresas.models import Empresa, Taller


class RegistrationTests(APITestCase):
	endpoint = '/api/auth/register/'

	def valid_payload(self, **overrides):
		payload = {
			'email': 'nuevo@ejemplo.com',
			'password': 'UnaClaveSegura123!',
			'password_confirmation': 'UnaClaveSegura123!',
			'accepted_terms': True,
		}
		payload.update(overrides)
		return payload

	def test_registers_user_with_email_as_username(self):
		response = self.client.post(self.endpoint, self.valid_payload(), format='json')

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertTrue(User.objects.filter(username='nuevo@ejemplo.com').exists())

	def test_rejects_mismatched_passwords(self):
		response = self.client.post(
			self.endpoint,
			self.valid_payload(password_confirmation='OtraClave123!'),
			format='json'
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('password_confirmation', response.data)

	def test_rejects_duplicate_email(self):
		User.objects.create_user(
			username='existente@ejemplo.com',
			email='existente@ejemplo.com',
			password='UnaClaveSegura123!'
		)

		response = self.client.post(
			self.endpoint,
			self.valid_payload(email='EXISTENTE@EJEMPLO.COM'),
			format='json'
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('email', response.data)


class EmpleadoExportTests(APITestCase):
	def setUp(self):
		from apps.empresas.models import Empresa

		self.user = User.objects.create_superuser(username='export_tester', password='x')
		self.empresa = Empresa.objects.create(
			nombre_comercial='Taller GON',
			razon_social='GON SA',
			ruc='1750000000004',
		)
		self.usuario = User.objects.create_user(
			username='empleado1',
			email='empleado1@ejemplo.com',
			password='UnaClaveSegura123!',
			first_name='Ana',
			last_name='Gómez',
		)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		self.client.defaults['HTTP_X_EMPRESA_ID'] = self.empresa.pk

	def _crear_empleado(self):
		from apps.authentication.models import UsuarioEmpresa

		ue = UsuarioEmpresa.objects.create(
			user=self.usuario,
			empresa=self.empresa,
			rol='ASESOR',
		)
		return ue

	def test_exportar_pdf_empleados(self):
		self._crear_empleado()
		resp = self.client.get('/api/auth/empleados/exportar-pdf/')
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp['Content-Type'], 'application/pdf')
		contenido = b''.join(resp.streaming_content)
		self.assertTrue(contenido.startswith(b'%PDF'))

	def test_exportar_excel_empleados(self):
		self._crear_empleado()
		resp = self.client.get('/api/auth/empleados/export-excel/')
		self.assertEqual(resp.status_code, 200)
		self.assertTrue(resp.content.startswith(b'PK'))

	def test_excel_contiene_datos_empleado(self):
		from io import BytesIO
		from openpyxl import load_workbook

		self._crear_empleado()
		resp = self.client.get('/api/auth/empleados/export-excel/')
		wb = load_workbook(BytesIO(resp.content))
		valores = [
			celda
			for fila in wb.active.iter_rows(values_only=True)
			for celda in fila
		]
		self.assertIn('empleado1@ejemplo.com', valores)
		self.assertIn('Asesor de Servicio', valores)
		self.assertIn('Activo', valores)

	def test_exportar_sin_empresa_rechazado(self):
		self.client.force_authenticate(user=self.usuario)
		self.client.defaults.pop('HTTP_X_EMPRESA_ID')
		resp = self.client.get('/api/auth/empleados/exportar-pdf/')
		self.assertEqual(resp.status_code, 403)


class AvatarUploadTests(APITestCase):
	def setUp(self):
		from django.core.files.uploadedfile import SimpleUploadedFile
		from io import BytesIO
		from PIL import Image

		self.SimpleUploadedFile = SimpleUploadedFile
		self.user = User.objects.create_user(
			username='avatar_user',
			email='avatar@ejemplo.com',
			password='UnaClaveSegura123!',
			first_name='Luis',
			last_name='Pérez',
		)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

		def _imagen_png():
			buf = BytesIO()
			Image.new('RGB', (80, 80), color='red').save(buf, format='PNG')
			return buf.getvalue()

		self.png_bytes = _imagen_png()

	def test_subir_avatar_validando_formato(self):
		foto = self.SimpleUploadedFile(
			'foto.png', self.png_bytes, content_type='image/png'
		)
		resp = self.client.patch(
			'/api/auth/me/',
			{'avatar': foto},
			format='multipart',
		)
		self.assertEqual(resp.status_code, 200, resp.data)
		self.assertIn('avatar', resp.data['user'])
		self.assertTrue(resp.data['user']['avatar'])

	def test_rechaza_formato_extraño(self):
		foto = self.SimpleUploadedFile(
			'foto.txt', b'no soy una imagen', content_type='text/plain'
		)
		resp = self.client.patch(
			'/api/auth/me/',
			{'avatar': foto},
			format='multipart',
		)
		self.assertEqual(resp.status_code, 400)
		self.assertIn('avatar', resp.data)

	def test_rechaza_imagen_muy_grande(self):
		from django.core.files.uploadedfile import SimpleUploadedFile
		from io import BytesIO
		from PIL import Image
		import os

		buf = BytesIO()
		ruido = Image.frombytes('RGB', (2200, 2200), os.urandom(2200 * 2200 * 3))
		ruido.save(buf, format='PNG')
		foto = SimpleUploadedFile('grande.png', buf.getvalue(), content_type='image/png')
		self.assertGreater(foto.size, 1024 * 1024)
		resp = self.client.patch(
			'/api/auth/me/',
			{'avatar': foto},
			format='multipart',
		)
		self.assertEqual(resp.status_code, 400)
		self.assertIn('avatar', resp.data)

	def test_eliminar_avatar(self):
		foto = self.SimpleUploadedFile(
			'foto.png', self.png_bytes, content_type='image/png'
		)
		self.client.patch('/api/auth/me/', {'avatar': foto}, format='multipart')

		resp = self.client.patch('/api/auth/me/', {'remove_avatar': 'true'}, format='multipart')
		self.assertEqual(resp.status_code, 200, resp.data)
		self.assertIsNone(resp.data['user']['avatar'])


def _crear_escenario():
	"""Dos empresas con sus talleres: (empresa_a, empresa_b, a1, a2, b1)."""
	empresa_a = Empresa.objects.create(
		nombre_comercial='Taller Norte', ruc='1711111111001'
	)
	empresa_b = Empresa.objects.create(
		nombre_comercial='Taller Sur', ruc='1722222222001'
	)
	a1 = Taller.objects.create(
		empresa=empresa_a, nombre='Norte Centro', codigo_sucursal='001',
		direccion='Av. Principal 1',
	)
	a2 = Taller.objects.create(
		empresa=empresa_a, nombre='Norte Norte', codigo_sucursal='002',
		direccion='Av. Principal 2',
	)
	b1 = Taller.objects.create(
		empresa=empresa_b, nombre='Sur Centro', codigo_sucursal='001',
		direccion='Av. Sur 1',
	)
	return empresa_a, empresa_b, a1, a2, b1


def _asignar(user, empresa, rol, talleres=None):
	asignacion = UsuarioEmpresa.objects.create(user=user, empresa=empresa, rol=rol)
	if talleres:
		asignacion.talleres.set(talleres)
	return asignacion


class EscenarioMixin:
	"""Empresa A con dos talleres, empresa B con uno y un usuario de A."""

	def setUp(self):
		super().setUp()
		(
			self.empresa_a,
			self.empresa_b,
			self.a1,
			self.a2,
			self.b1,
		) = _crear_escenario()
		self.usuario = User.objects.create_user(
			username='ana', email='ana@taller.ec', password='x'
		)
		_asignar(self.usuario, self.empresa_a, 'ASESOR', [self.a1])
		self.factory = APIRequestFactory()

	def _request(self, user=None, empresa=None, taller=None, claim=None):
		kwargs = {}
		if empresa is not None:
			kwargs['HTTP_X_EMPRESA_ID'] = str(empresa)
		if taller is not None:
			kwargs['HTTP_X_TALLER_ID'] = str(taller)
		request = self.factory.get('/api/auth/me/', **kwargs)
		request.user = AnonymousUser() if user is None else user
		if claim is not None:
			request.auth = claim
		return request


class ContextoUsuarioTests(EscenarioMixin, APITestCase):
	"""Empresa activa y rol vigente resueltos desde la base."""

	def test_contexto_por_cabecera(self):
		request = self._request(user=self.usuario, empresa=self.empresa_a.pk)
		contexto = get_contexto_desde_request(request)
		self.assertEqual(contexto.empresa_id, self.empresa_a.pk)
		self.assertEqual(contexto.rol, 'ASESOR')
		self.assertFalse(contexto.es_superusuario)

	def test_contexto_por_claim_del_jwt(self):
		request = self._request(user=self.usuario)
		request.auth = {'empresa_id': self.empresa_a.pk, 'rol': 'CAJERO'}
		contexto = get_contexto_desde_request(request)
		self.assertEqual(contexto.empresa_id, self.empresa_a.pk)

	def test_el_rol_vigente_de_la_base_no_lo_pisa_el_jwt(self):
		request = self._request(user=self.usuario)
		request.auth = {'empresa_id': self.empresa_a.pk, 'rol': 'CAJERO'}
		self.assertEqual(get_rol_desde_request(request), 'ASESOR')
		self.assertEqual(get_empresa_id_desde_request(request), self.empresa_a.pk)

	def test_no_da_contexto_si_la_empresa_no_es_suya(self):
		request = self._request(user=self.usuario, empresa=self.empresa_b.pk)
		contexto = get_contexto_desde_request(request)
		self.assertIsNone(contexto.empresa_id)
		self.assertIsNone(contexto.rol)

	def test_sin_cabecera_usa_la_unica_empresa_asignada(self):
		request = self._request(user=self.usuario)
		contexto = get_contexto_desde_request(request)
		self.assertEqual(contexto.empresa_id, self.empresa_a.pk)
		self.assertEqual(contexto.rol, 'ASESOR')

	def test_varias_empresas_sin_cabecera_no_dan_contexto(self):
		_asignar(self.usuario, self.empresa_b, 'MECANICO', [self.b1])
		request = self._request(user=self.usuario)
		contexto = get_contexto_desde_request(request)
		self.assertIsNone(contexto.empresa_id)

	def test_anonimo_no_tiene_contexto(self):
		request = self._request(user=None)
		contexto = get_contexto_desde_request(request)
		self.assertIsNone(contexto.empresa_id)
		self.assertIsNone(contexto.rol)

	def test_superusuario_toma_una_empresa_y_es_admin_sistema(self):
		superuser = User.objects.create_superuser(username='root', password='x')
		request = self._request(user=superuser, empresa=self.empresa_b.pk)
		contexto = get_contexto_desde_request(request)
		self.assertEqual(contexto.empresa_id, self.empresa_b.pk)
		self.assertEqual(contexto.rol, 'ADMIN_SISTEMA')
		self.assertTrue(contexto.es_superusuario)

	def test_el_contexto_se_cachea_por_peticion(self):
		request = self._request(user=self.usuario, empresa=self.empresa_a.pk)
		primero = get_contexto_desde_request(request)
		segundo = get_contexto_desde_request(request)
		self.assertIs(primero, segundo)


class AlcanceTallerTests(EscenarioMixin, APITestCase):
	"""Política: administración ve todo; el resto solo lo asignado."""

	def test_admin_empresa_ve_todos_los_talleres_sin_asignacion(self):
		admin = User.objects.create_user(username='dueno', password='x')
		_asignar(admin, self.empresa_a, 'ADMIN_EMPRESA')
		contexto = contexto_de_usuario(admin, self.empresa_a.pk)
		permitidos = list(talleres_permitidos(admin, contexto).values_list('pk', flat=True))
		self.assertEqual(sorted(permitidos), [self.a1.pk, self.a2.pk])

	def test_admin_taller_solo_ve_los_asignados(self):
		gerente = User.objects.create_user(username='gerente', password='x')
		_asignar(gerente, self.empresa_a, 'ADMIN_TALLER', [self.a2])
		contexto = contexto_de_usuario(gerente, self.empresa_a.pk)
		permitidos = list(talleres_permitidos(gerente, contexto).values_list('pk', flat=True))
		self.assertEqual(permitidos, [self.a2.pk])

	def test_mecanico_sin_asignacion_no_ve_ningun_taller(self):
		mecanico = User.objects.create_user(username='meca', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO')
		contexto = contexto_de_usuario(mecanico, self.empresa_a.pk)
		self.assertFalse(talleres_permitidos(mecanico, contexto).exists())

	def test_los_talleres_de_otra_empresa_no_aparecen(self):
		contexto = contexto_de_usuario(self.usuario, self.empresa_a.pk)
		permitidos = list(talleres_permitidos(self.usuario, contexto).values_list('pk', flat=True))
		self.assertNotIn(self.b1.pk, permitidos)

	def test_superusuario_ve_los_talleres_activos(self):
		superuser = User.objects.create_superuser(username='root2', password='x')
		contexto = contexto_de_usuario(superuser, self.empresa_a.pk)
		permitidos = list(talleres_permitidos(superuser, contexto).values_list('pk', flat=True))
		self.assertEqual(sorted(permitidos), [self.a1.pk, self.a2.pk])

	def test_cabecera_de_un_taller_ajeno_se_descarta(self):
		mecanico = User.objects.create_user(username='meca2', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO', [self.a1])
		request = self._request(
			user=mecanico, empresa=self.empresa_a.pk, taller=self.b1.pk
		)
		self.assertEqual(get_taller_desde_request(request), self.a1.pk)

	def test_cabecera_de_un_taller_no_asignado_se_descarta(self):
		mecanico = User.objects.create_user(username='meca3', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO', [self.a1])
		request = self._request(
			user=mecanico, empresa=self.empresa_a.pk, taller=self.a2.pk
		)
		self.assertEqual(get_taller_desde_request(request), self.a1.pk)

	def test_el_taller_activo_del_perfil_se_valida_contra_el_alcance(self):
		mecanico = User.objects.create_user(username='meca4', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO', [self.a1])
		UserProfile.objects.create(user=mecanico, taller_activo=self.a2)
		request = self._request(user=mecanico, empresa=self.empresa_a.pk)
		self.assertEqual(get_taller_desde_request(request), self.a1.pk)

	def test_el_taller_activo_valido_se_respeta(self):
		mecanico = User.objects.create_user(username='meca5', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO', [self.a1, self.a2])
		UserProfile.objects.create(user=mecanico, taller_activo=self.a2)
		request = self._request(user=mecanico, empresa=self.empresa_a.pk)
		self.assertEqual(get_taller_desde_request(request), self.a2.pk)

	def test_sin_talleres_permitidos_devuelve_none(self):
		mecanico = User.objects.create_user(username='meca6', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO')
		request = self._request(user=mecanico, empresa=self.empresa_a.pk)
		self.assertIsNone(get_taller_desde_request(request))

	def test_taller_de_otra_empresa_no_es_asignable(self):
		admin = User.objects.create_user(username='dueno2', password='x')
		_asignar(admin, self.empresa_a, 'ADMIN_EMPRESA')
		contexto = contexto_de_usuario(admin, self.empresa_a.pk)
		permitidos, rechazados = validar_talleres_asignables(
			admin, contexto, [self.a1.pk, self.b1.pk]
		)
		self.assertEqual(permitidos, [self.a1.pk])
		self.assertEqual(rechazados, [self.b1.pk])

	def test_taller_inactivo_no_es_asignable(self):
		dado_de_baja = Taller.objects.create(
			empresa=self.empresa_a,
			nombre='Norte Cerrado',
			codigo_sucursal='003',
			direccion='Av. 9',
			is_active=False,
		)
		admin = User.objects.create_user(username='dueno3', password='x')
		_asignar(admin, self.empresa_a, 'ADMIN_EMPRESA')
		contexto = contexto_de_usuario(admin, self.empresa_a.pk)
		permitidos, rechazados = validar_talleres_asignables(
			admin, contexto, [self.a1.pk, dado_de_baja.pk]
		)
		self.assertEqual(permitidos, [self.a1.pk])
		self.assertEqual(rechazados, [dado_de_baja.pk])

	def test_me_devuelve_un_taller_dentro_del_alcance(self):
		mecanico = User.objects.create_user(username='meca7', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO', [self.a1])
		cliente = APIClient()
		cliente.force_authenticate(user=mecanico)
		respuesta = cliente.get(
			'/api/auth/me/', HTTP_X_EMPRESA_ID=str(self.empresa_a.pk)
		)
		self.assertEqual(respuesta.status_code, 200, respuesta.data)
		self.assertEqual(respuesta.data['taller_nombre'], self.a1.nombre)


class MatrizRolesTests(TestCase):
	"""La matriz es el único sitio donde se decide qué puede cada rol."""

	def test_cubre_todos_los_roles_del_modelo(self):
		roles_modelo = {clave for clave, _ in UsuarioEmpresa.ROLES}
		self.assertEqual(set(MATRIZ_ROLES), roles_modelo)

	def test_solo_usa_recursos_y_acciones_validos(self):
		for rol, recursos in MATRIZ_ROLES.items():
			for recurso, acciones in recursos.items():
				self.assertIn(recurso, RECURSOS, f'{rol} -> {recurso}')
				for accion in acciones:
					self.assertIn(accion, ACCIONES, f'{rol} -> {recurso}')

	def test_los_roles_de_administracion_tienen_todos_los_recursos(self):
		for rol in ROLES_TODOS_TALLERES:
			self.assertEqual(set(MATRIZ_ROLES[rol]), set(RECURSOS))

	def test_roles_que_no_pueden_gestionar_usuarios(self):
		for rol in ('ADMIN_TALLER', 'ASESOR', 'MECANICO', 'CAJERO'):
			self.assertFalse(puede(rol, 'usuarios', 'modificar'))
		self.assertTrue(puede('ADMIN_EMPRESA', 'usuarios', 'modificar'))
		self.assertTrue(puede('ADMIN_SISTEMA', 'usuarios', 'modificar'))

	def test_roles_que_no_pueden_facturar(self):
		for rol in ('ASESOR', 'MECANICO'):
			self.assertFalse(puede(rol, 'facturacion', 'modificar'))
		self.assertTrue(puede('CAJERO', 'facturacion', 'modificar'))
		self.assertTrue(puede('ADMIN_TALLER', 'facturacion', 'modificar'))

	def test_mecanico_trabaja_ordenes_e_inventario(self):
		self.assertTrue(puede('MECANICO', 'ordenes', 'modificar'))
		self.assertTrue(puede('MECANICO', 'inventario', 'modificar'))
		self.assertTrue(puede('MECANICO', 'ordenes', 'ver'))
		# El mecánico consulta la cotización aprobada que ejecuta (selector en la OT),
		# pero no puede emitir ni modificar cotizaciones.
		self.assertTrue(puede('MECANICO', 'cotizaciones', 'ver'))
		self.assertFalse(puede('MECANICO', 'cotizaciones', 'modificar'))
		self.assertFalse(puede('MECANICO', 'clientes', 'modificar'))

	def test_asesor_cotiza_y_no_toca_la_caja(self):
		self.assertTrue(puede('ASESOR', 'cotizaciones', 'modificar'))
		self.assertTrue(puede('ASESOR', 'clientes', 'modificar'))
		self.assertFalse(puede('ASESOR', 'facturacion', 'ver'))

	def test_cajero_no_modifica_ordenes(self):
		self.assertTrue(puede('CAJERO', 'ordenes', 'ver'))
		self.assertFalse(puede('CAJERO', 'ordenes', 'modificar'))
		self.assertTrue(puede('CAJERO', 'clientes', 'modificar'))

	def test_rol_o_recurso_desconocido_se_niega(self):
		self.assertFalse(puede('ROL_INVENTADO', 'clientes', 'modificar'))
		self.assertFalse(puede(None, 'clientes', 'modificar'))
		self.assertFalse(puede('MECANICO', 'recurso_inventado', 'ver'))

	def test_accion_desconocida_es_error_de_programacion(self):
		with self.assertRaises(ValueError):
			puede('MECANICO', 'ordenes', 'borrar')


class PermisosDrfTests(EscenarioMixin, APITestCase):
	"""TienePermiso lee el rol vigente de la base, no la claim del JWT."""

	def test_cajero_si_puede_facturar(self):
		cajero = User.objects.create_user(username='caja', password='x')
		_asignar(cajero, self.empresa_a, 'CAJERO', [self.a1])
		request = self._request(user=cajero, empresa=self.empresa_a.pk)
		permiso = TienePermiso('facturacion', 'modificar')()
		self.assertTrue(permiso.has_permission(request, None))

	def test_mecanico_no_puede_facturar(self):
		mecanico = User.objects.create_user(username='meca8', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO', [self.a1])
		request = self._request(user=mecanico, empresa=self.empresa_a.pk)
		permiso = TienePermiso('facturacion', 'modificar')()
		self.assertFalse(permiso.has_permission(request, None))

	def test_admin_de_empresa_si_puede_gestionar_usuarios(self):
		admin = User.objects.create_user(username='dueno4', password='x')
		_asignar(admin, self.empresa_a, 'ADMIN_EMPRESA')
		request = self._request(user=admin, empresa=self.empresa_a.pk)
		permiso = TienePermiso('usuarios', 'modificar')()
		self.assertTrue(permiso.has_permission(request, None))

	def test_sin_contexto_de_empresa_se_niega(self):
		_asignar(self.usuario, self.empresa_b, 'ADMIN_EMPRESA')
		request = self._request(user=self.usuario)
		permiso = TienePermiso('clientes', 'modificar')()
		self.assertFalse(permiso.has_permission(request, None))

	def test_es_admin_de_empresa_permite_lectura_a_cualquiera(self):
		request = self._request(user=self.usuario, empresa=self.empresa_a.pk)
		permiso = EsAdminDeEmpresa()
		self.assertTrue(permiso.has_permission(request, None))

	def test_es_admin_de_empresa_bloquea_escritura_a_un_asesor(self):
		request = self._request(user=self.usuario, empresa=self.empresa_a.pk)
		request.method = 'POST'
		permiso = EsAdminDeEmpresa()
		self.assertFalse(permiso.has_permission(request, None))

	def test_el_nombre_de_la_clase_refleja_el_recurso(self):
		clase = TienePermiso('ordenes')
		self.assertEqual(clase.__name__, 'TienePermiso_ordenes')


class SerializadorEmpleadosTests(EscenarioMixin, APITestCase):
	"""Alta de empleados: no se puede asignar un taller de otra empresa."""

	def _serializer(self, talleres):
		request = self._request(user=self.usuario, empresa=self.empresa_a.pk)
		return EmpleadoWriteSerializer(
			data={
				'first_name': 'Ana',
				'last_name': 'Gómez',
				'email': 'ana@taller.ec',
				'rol': 'MECANICO',
				'talleres': talleres,
			},
			context={'request': request},
		)

	def test_rechaza_taller_de_otra_empresa(self):
		serializer = self._serializer([self.b1.pk])
		self.assertFalse(serializer.is_valid())
		self.assertIn('talleres', serializer.errors)

	def test_acepta_talleres_de_la_empresa(self):
		serializer = self._serializer([self.a1.pk, self.a2.pk])
		self.assertTrue(serializer.is_valid(), serializer.errors)

	def test_acepta_lista_vacia(self):
		serializer = self._serializer([])
		self.assertTrue(serializer.is_valid(), serializer.errors)


class BackfillTalleresTests(TestCase):
	"""La migración 0006 conserva el alcance anterior de los datos existentes."""

	def _backfill(self):
		modulo = importlib.import_module(
			'apps.authentication.migrations.0006_backfill_talleres_asignados'
		)
		return modulo.backfill_talleres(UsuarioEmpresa, Taller)

	def test_asigna_los_talleres_activos_de_su_empresa(self):
		empresa, _, a1, a2, _ = _crear_escenario()
		usuario = User.objects.create_user(username='viejo', password='x')
		asignacion = _asignar(usuario, empresa, 'MECANICO')

		completadas = self._backfill()

		self.assertEqual(completadas, 1)
		self.assertEqual(
			sorted(asignacion.talleres.values_list('pk', flat=True)),
			[a1.pk, a2.pk],
		)

	def test_no_asigna_a_los_roles_que_ya_ven_todo(self):
		empresa, _, _, _, _ = _crear_escenario()
		usuario = User.objects.create_user(username='dueno5', password='x')
		asignacion = _asignar(usuario, empresa, 'ADMIN_EMPRESA')

		completadas = self._backfill()

		self.assertEqual(completadas, 0)
		self.assertFalse(asignacion.talleres.exists())

	def test_no_toca_asignaciones_previas(self):
		empresa, _, a1, _, b1 = _crear_escenario()
		usuario = User.objects.create_user(username='yaasignado', password='x')
		asignacion = _asignar(usuario, empresa, 'MECANICO', [b1])

		completadas = self._backfill()

		self.assertEqual(completadas, 0)
		self.assertEqual(list(asignacion.talleres.values_list('pk', flat=True)), [b1.pk])
		self.assertNotIn(a1.pk, asignacion.talleres.values_list('pk', flat=True))


class PoderOtorgarTests(TestCase):
	"""Reglas de niveles: nadie otorga roles iguales o superiores al suyo."""

	def test_niveles_por_jerarquia(self):
		from apps.authentication.matriz_roles import NIVELES_ROLES, puede_otorgar

		self.assertEqual(NIVELES_ROLES['ADMIN_SISTEMA'], 4)
		self.assertEqual(NIVELES_ROLES['ADMIN_EMPRESA'], 3)
		self.assertEqual(NIVELES_ROLES['ADMIN_TALLER'], 2)
		self.assertEqual(NIVELES_ROLES['ASESOR'], 1)
		self.assertEqual(NIVELES_ROLES['MECANICO'], 1)
		self.assertEqual(NIVELES_ROLES['CAJERO'], 1)

		self.assertTrue(puede_otorgar('ADMIN_SISTEMA', 'CAJERO'))
		self.assertTrue(puede_otorgar('ADMIN_TALLER', 'MECANICO'))
		self.assertTrue(puede_otorgar('ADMIN_TALLER', 'ADMIN_TALLER'))
		self.assertTrue(puede_otorgar('MECANICO', 'CAJERO'))
		self.assertFalse(puede_otorgar('ADMIN_TALLER', 'ADMIN_EMPRESA'))
		self.assertFalse(puede_otorgar('MECANICO', 'ADMIN_TALLER'))
		self.assertFalse(puede_otorgar('CAJERO', 'ADMIN_SISTEMA'))
		self.assertFalse(puede_otorgar('ADMIN_EMPRESA', 'ADMIN_SISTEMA'))
		self.assertFalse(puede_otorgar('MECANICO', 'ROL_INVENTADO'))


class MiPerfilRolesTests(EscenarioMixin, APITestCase):
	"""`/api/auth/me/` expone el rol y los roles que el usuario puede otorgar."""

	def _me(self, usuario):
		cliente = APIClient()
		cliente.force_authenticate(usuario)
		cliente.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa_a.pk)
		resp = cliente.get('/api/auth/me/')
		return resp

	def test_dueno_ve_todos_los_roles_como_otorgables(self):
		dueno = User.objects.create_user(username='dueno-me', password='x')
		_asignar(dueno, self.empresa_a, 'ADMIN_EMPRESA', [self.a1])
		resp = self._me(dueno)
		self.assertEqual(resp.status_code, 200, resp.content)
		data = resp.json()
		self.assertEqual(data['rol'], 'ADMIN_EMPRESA')
		self.assertFalse(data['es_superusuario'])
		self.assertEqual(
			set(data['roles_otorgables']),
			{c for c, _ in UsuarioEmpresa.ROLES if c != 'ADMIN_SISTEMA'},
		)

	def test_asesor_solo_otorga_roles_operativos(self):
		asesor = User.objects.create_user(username='asesor-me', password='x')
		_asignar(asesor, self.empresa_a, 'ASESOR', [self.a1])
		data = self._me(asesor).json()
		self.assertEqual(data['rol'], 'ASESOR')
		self.assertNotIn('ADMIN_EMPRESA', data['roles_otorgables'])
		self.assertNotIn('ADMIN_TALLER', data['roles_otorgables'])
		self.assertIn('MECANICO', data['roles_otorgables'])
		self.assertIn('CAJERO', data['roles_otorgables'])


class AccesoEmpresaTests(EscenarioMixin, APITestCase):
	"""Flag `tiene_acceso`: personas sin acceso existen sin poder loguearse."""

	def _cliente(self, usuario):
		cliente = APIClient()
		cliente.force_authenticate(usuario)
		cliente.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa_a.pk)
		return cliente

	def _con_rol(self, rol, talleres=None):
		usuario = User.objects.create_user(
			username=f'rol-{rol.lower()}-{uuid.uuid4().hex[:6]}', password='x'
		)
		_asignar(usuario, self.empresa_a, rol, talleres or [self.a1])
		return usuario

	def test_crea_empleado_sin_acceso(self):
		cliente = self._cliente(self._con_rol('ADMIN_EMPRESA'))
		resp = cliente.post(
			'/api/auth/empleados/',
			{
				'first_name': 'Aseo',
				'last_name': 'Limpieza',
				'email': 'aseo@taller.ec',
				'tiene_acceso': False,
				'rol': None,
			},
			format='json',
		)
		self.assertEqual(resp.status_code, 201, resp.content)
		data = resp.json()
		self.assertFalse(data['tiene_acceso'])
		self.assertIsNone(data['rol'])
		relacion = UsuarioEmpresa.objects.get(user__email='aseo@taller.ec')
		self.assertFalse(relacion.tiene_acceso)

	def test_empleado_sin_acceso_no_puede_loguearse(self):
		usuario = User.objects.create_user(username='noseqa', email='nose@taller.ec', password='x')
		_asignar(usuario, self.empresa_a, 'MECANICO', [self.a1])
		UsuarioEmpresa.objects.filter(user=usuario).update(tiene_acceso=False)

		resp = self.client.post(
			'/api/auth/login/',
			{'username': 'nose@taller.ec', 'password': 'x'},
			format='json',
		)
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_empleado_con_acceso_si_puede_loguearse(self):
		usuario = User.objects.create_user(username='si-qa', email='si@taller.ec', password='x')
		_asignar(usuario, self.empresa_a, 'MECANICO', [self.a1])
		resp = self.client.post(
			'/api/auth/login/',
			{'username': 'si@taller.ec', 'password': 'x'},
			format='json',
		)
		self.assertEqual(resp.status_code, 200, resp.content)

	def test_no_puedes_quitarte_tu_propio_acceso(self):
		mi = self._con_rol('ADMIN_EMPRESA')
		cliente = self._cliente(mi)
		resp = cliente.patch(
			f'/api/auth/empleados/{mi.pk}/',
			{'tiene_acceso': False},
			format='json',
		)
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_filtro_acceso_en_listado(self):
		con = User.objects.create_user(username='con-qa', email='con@taller.ec', password='x')
		_asignar(con, self.empresa_a, 'MECANICO', [self.a1])
		sin = User.objects.create_user(username='sin-qa', email='sin@taller.ec', password='x')
		_asignar(sin, self.empresa_a, None, [])
		UsuarioEmpresa.objects.filter(user=sin).update(tiene_acceso=False)

		cliente = self._cliente(self._con_rol('ADMIN_EMPRESA'))
		resp = cliente.get('/api/auth/empleados/?acceso=con')
		emails = [u['user']['email'] for u in resp.json()['results']]
		self.assertIn('con@taller.ec', emails)
		self.assertNotIn('sin@taller.ec', emails)

		resp = cliente.get('/api/auth/empleados/?acceso=sin')
		emails = [u['user']['email'] for u in resp.json()['results']]
		self.assertIn('sin@taller.ec', emails)
		self.assertNotIn('con@taller.ec', emails)


class PermisosEndpointTests(EscenarioMixin, APITestCase):
	"""Cableado de F2: los recursos de la API se protegen con la matriz."""

	def _cliente(self, usuario):
		cliente = APIClient()
		cliente.force_authenticate(usuario)
		cliente.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa_a.pk)
		return cliente

	def _con_rol(self, rol, talleres=None):
		usuario = User.objects.create_user(
			username=f'rol-{rol.lower()}-{uuid.uuid4().hex[:6]}', password='x'
		)
		_asignar(usuario, self.empresa_a, rol, talleres or [self.a1])
		return usuario

	def _assert_nunca_aprueba(self, endpoint, rol):
		cliente = self._cliente(self._con_rol(rol))
		resp = cliente.get(endpoint)
		self.assertEqual(resp.status_code, 403, resp.content)

	def test_mecanico_lista_pero_no_emite_cotizaciones(self):
		cliente = self._cliente(self._con_rol('MECANICO'))
		self.assertEqual(cliente.post('/api/cotizaciones/', {}, format='json').status_code, 403)
		self.assertEqual(cliente.get('/api/cotizaciones/').status_code, 200)

	def test_mecanico_no_accede_a_usuarios_ni_reportes(self):
		self._assert_nunca_aprueba('/api/auth/usuarios/', 'MECANICO')
		self._assert_nunca_aprueba('/api/auth/roles/', 'MECANICO')
		self._assert_nunca_aprueba('/api/core/dashboard-v2/', 'MECANICO')

	def test_cajero_lee_inventario_no_lo_edita(self):
		cliente = self._cliente(self._con_rol('CAJERO'))
		self.assertEqual(cliente.post('/api/repuestos/', {}, format='json').status_code, 403)
		self.assertEqual(cliente.get('/api/repuestos/').status_code, 200)

	def test_cajero_no_gestiona_personal(self):
		cliente = self._cliente(self._con_rol('CAJERO'))
		resp = cliente.post(
			'/api/auth/empleados/',
			{'first_name': 'X', 'last_name': 'Y', 'email': 'caja@taller.ec', 'rol': 'CAJERO'},
			format='json',
		)
		self.assertEqual(resp.status_code, 403, resp.content)

	def test_asesora_lista_al_personal_pero_no_lo_modifica(self):
		cliente = self._cliente(self._con_rol('ASESOR'))
		self.assertEqual(cliente.get('/api/auth/empleados/').status_code, 200)
		self.assertEqual(cliente.post('/api/auth/empleados/', {}, format='json').status_code, 403)

	def test_admin_empresa_gestiona_usuarios_y_roles(self):
		cliente = self._cliente(self._con_rol('ADMIN_EMPRESA'))
		self.assertEqual(cliente.get('/api/auth/usuarios/').status_code, 200)
		resp = cliente.get('/api/auth/roles/')
		self.assertEqual(resp.status_code, 200, resp.content)
		esperados = [c for c, _ in UsuarioEmpresa.ROLES if c != 'ADMIN_SISTEMA']
		self.assertEqual(len(resp.json()), len(esperados))
		self.assertNotIn('ADMIN_SISTEMA', [r['codigo'] for r in resp.json()])
		self.assertTrue(all('codigo' in rol and 'es_sistema' in rol for rol in resp.json()))

	def test_roles_expone_nivel_y_alcance_de_talleres(self):
		cliente = self._cliente(self._con_rol('ADMIN_EMPRESA'))
		resp = cliente.get('/api/auth/roles/')
		por_rol = {r['codigo']: r for r in resp.json()}
		self.assertEqual(por_rol['ADMIN_EMPRESA']['nivel'], 3)
		self.assertTrue(por_rol['ADMIN_EMPRESA']['ver_todos_talleres'])
		self.assertEqual(por_rol['MECANICO']['nivel'], 1)
		self.assertFalse(por_rol['MECANICO']['ver_todos_talleres'])
		self.assertTrue(por_rol['MECANICO']['es_sistema'])

	def test_admin_empresa_da_de_alta_un_cliente(self):
		cliente = self._cliente(self._con_rol('ADMIN_EMPRESA'))
		resp = cliente.post(
			'/api/clientes/',
			{'tipo_identificacion': 'R', 'identificacion': '1790000000001', 'nombre': 'Cliente F2'},
			format='multipart',
		)
		self.assertEqual(resp.status_code, 201, resp.content)

	def test_usuario_sin_talleres_no_tiene_taller_de_sesion(self):
		usuario = User.objects.create_user(username='sin-taller-2', password='x')
		asignacion = _asignar(usuario, self.empresa_a, 'ADMIN_TALLER', [])
		request = self._request(user=usuario, empresa=self.empresa_a.pk)
		self.assertEqual(list(talleres_permitidos(usuario, get_contexto_desde_request(request))), [])
		self.assertIsNone(get_taller_desde_request(request))
		self.assertEqual(asignacion.talleres.count(), 0)


class RolesPersonalizadosTests(EscenarioMixin, APITestCase):
	"""Roles del sistema: solo lectura. Personalizados: la empresa los crea,
	edita sus permisos y los asigna a empleados."""

	def setUp(self):
		super().setUp()
		self.superadmin = User.objects.create_superuser(username='sys-root', password='x')
		self.dueno = User.objects.create_user(username='dueno-perm', password='x')
		_asignar(self.dueno, self.empresa_a, 'ADMIN_EMPRESA', [self.a1])

	def _cliente(self, usuario):
		cliente = APIClient()
		cliente.force_authenticate(usuario)
		cliente.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa_a.pk)
		return cliente

	def _crear_rol(self, cliente, nombre='Supervisor de taller'):
		return cliente.post('/api/auth/roles/', {'nombre': nombre}, format='json')

	def test_solo_roles_de_sistema_en_lista(self):
		resp = self._cliente(self.dueno).get('/api/auth/roles/')
		codigos = [r['codigo'] for r in resp.json()]
		esperados = sorted(c for c, _ in UsuarioEmpresa.ROLES if c != 'ADMIN_SISTEMA')
		self.assertEqual(sorted(codigos), esperados)
		self.assertTrue(all(r['editable'] for r in resp.json()))
		self.assertTrue(all(r['personalizado'] is False for r in resp.json()))

	def test_crea_rol_personalizado_de_la_empresa(self):
		resp = self._crear_rol(self._cliente(self.dueno))
		self.assertEqual(resp.status_code, 201, resp.content)
		rol = resp.json()
		self.assertTrue(rol['codigo'])
		self.assertFalse(rol['es_sistema'])
		self.assertTrue(rol['editable'])
		self.assertEqual(rol['nombre'], 'Supervisor de taller')
		en_bd = Rol.objects.get(pk=rol['id'])
		self.assertEqual(en_bd.empresa_id, self.empresa_a.pk)
		self.assertTrue(en_bd.is_active)

	def test_rol_personalizado_aparece_en_la_lista_de_su_empresa(self):
		creado = self._crear_rol(self._cliente(self.dueno)).json()
		resp = self._cliente(self.dueno).get('/api/auth/roles/')
		self.assertTrue(any(r['id'] == creado['id'] for r in resp.json()))

	def test_nombre_vacio_rechazado(self):
		resp = self._cliente(self.dueno).post(
			'/api/auth/roles/', {'nombre': '   '}, format='json'
		)
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_detalle_excluye_roles_de_otra_empresa(self):
		otro_empresa = Empresa.objects.create(nombre_comercial='Otra S.A.', ruc='1799999999002')
		otro_rol = Rol.objects.create(
			codigo='rol-otra', nombre='Rol ajeno',
			empresa=otro_empresa, es_sistema=False, nivel=1,
		)
		resp = self._cliente(self.dueno).get(f'/api/auth/roles/{otro_rol.pk}/')
		self.assertEqual(resp.status_code, 404, resp.content)

	def test_asigna_permisos_generales(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		resp = cliente.put(
			f"/api/auth/roles/{rol['id']}/permisos/",
			{'permisos': [
				{'recurso': 'clientes', 'ver': True, 'modificar': True},
				{'recurso': 'facturacion', 'ver': True, 'modificar': False},
			]},
			format='json',
		)
		self.assertEqual(resp.status_code, 200, resp.content)
		esperado = {'clientes': ['ver', 'modificar'], 'facturacion': ['ver']}
		self.assertEqual(permisos_de_rol(rol['codigo']), esperado)

	def test_modificar_implica_ver(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		resp = cliente.put(
			f"/api/auth/roles/{rol['id']}/permisos/",
			{'permisos': [{'recurso': 'inventario', 'ver': False, 'modificar': True}]},
			format='json',
		)
		self.assertEqual(resp.status_code, 200, resp.content)
		self.assertEqual(permisos_de_rol(rol['codigo']), {'inventario': ['ver', 'modificar']})

	def test_recurso_desconocido_rechazado(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		resp = cliente.put(
			f"/api/auth/roles/{rol['id']}/permisos/",
			{'permisos': [{'recurso': 'frutas', 'ver': True, 'modificar': False}]},
			format='json',
		)
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_asigna_acciones_especiales_y_verifica_detalle(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		resp = cliente.put(
			f"/api/auth/roles/{rol['id']}/acciones/",
			{'acciones': ['aprobar_descuentos']},
			format='json',
		)
		self.assertEqual(resp.status_code, 200, resp.content)
		detalle = cliente.get(f"/api/auth/roles/{rol['id']}/").json()
		self.assertEqual(detalle['acciones'], ['aprobar_descuentos'])

	def test_accion_desconocida_rechazada(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		resp = cliente.put(
			f"/api/auth/roles/{rol['id']}/acciones/",
			{'acciones': ['apagar_el_motor']},
			format='json',
		)
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_edita_nombre_y_desactiva(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		resp = cliente.patch(
			f"/api/auth/roles/{rol['id']}/",
			{'nombre': 'Auxiliar administrativo', 'descripcion': 'Cubre caja y clientes'},
			format='json',
		)
		self.assertEqual(resp.status_code, 200, resp.content)
		self.assertEqual(Rol.objects.get(pk=rol['id']).nombre, 'Auxiliar administrativo')
		resp = cliente.patch(
			f"/api/auth/roles/{rol['id']}/",
			{'is_active': False},
			format='json',
		)
		self.assertEqual(resp.status_code, 200, resp.content)
		self.assertFalse(Rol.objects.get(pk=rol['id']).is_active)

	def test_metadata_de_roles_de_sistema_no_editable(self):
		cliente = self._cliente(self.superadmin)
		sistema = Rol.objects.get(codigo='MECANICO')
		resp = cliente.patch(
			f'/api/auth/roles/{sistema.pk}/',
			{'nombre': 'Mecanizado'},
			format='json',
		)
		self.assertEqual(resp.status_code, 403, resp.content)

	def test_superadmin_edita_matriz_global_de_sistema(self):
		cliente = self._cliente(self.superadmin)
		sistema = Rol.objects.get(codigo='MECANICO')
		resp = cliente.put(
			f'/api/auth/roles/{sistema.pk}/permisos/',
			{'permisos': [
				{'recurso': 'clientes', 'ver': True, 'modificar': False},
				{'recurso': 'facturacion', 'ver': True, 'modificar': True},
			]},
			format='json',
		)
		self.assertEqual(resp.status_code, 200, resp.content)
		globales = permisos_de_rol('MECANICO')
		self.assertEqual(globales['clientes'], ['ver'])
		self.assertEqual(globales['facturacion'], ['ver', 'modificar'])
		self.assertFalse(PermisoEmpresa.objects.filter(rol=sistema).exists())

	def test_empresa_ajusta_rol_de_sistema_y_aisla_de_otras(self):
		cliente = self._cliente(self.dueno)
		mecanico = Rol.objects.get(codigo='MECANICO')
		globales = permisos_de_rol('MECANICO')
		self.assertEqual(globales['clientes'], ['ver'])
		self.assertNotIn('facturacion', globales)
		resp = cliente.put(
			f'/api/auth/roles/{mecanico.pk}/permisos/',
			{'permisos': [
				{'recurso': 'clientes', 'ver': True, 'modificar': True},
				{'recurso': 'facturacion', 'ver': True, 'modificar': False},
			]},
			format='json',
		)
		self.assertEqual(resp.status_code, 200, resp.content)
		efectivo = permisos_de_rol('MECANICO', self.empresa_a.pk)
		self.assertEqual(efectivo['clientes'], ['ver', 'modificar'])
		self.assertEqual(efectivo['facturacion'], ['ver'])
		self.assertEqual(permisos_de_rol('MECANICO'), globales)
		otra = Empresa.objects.create(nombre_comercial='Lejana S.A.', ruc='1799999999007')
		self.assertEqual(permisos_de_rol('MECANICO', otra.pk), globales)
		lista = cliente.get('/api/auth/roles/').json()
		self.assertTrue(next(r for r in lista if r['codigo'] == 'MECANICO')['personalizado'])
		detalle = cliente.get(f'/api/auth/roles/{mecanico.pk}/').json()
		self.assertTrue(detalle['personalizado'])

	def test_restablecer_rol_de_sistema_a_matriz_global(self):
		cliente = self._cliente(self.dueno)
		mecanico = Rol.objects.get(codigo='MECANICO')
		globales = permisos_de_rol('MECANICO')
		cliente.put(
			f'/api/auth/roles/{mecanico.pk}/permisos/',
			{'permisos': [{'recurso': 'clientes', 'ver': True, 'modificar': True}]},
			format='json',
		)
		resp = cliente.delete(f'/api/auth/roles/{mecanico.pk}/permisos/')
		self.assertEqual(resp.status_code, 200, resp.content)
		self.assertEqual(permisos_de_rol('MECANICO', self.empresa_a.pk), globales)
		self.assertFalse(
			PermisoEmpresa.objects.filter(rol=mecanico, empresa_id=self.empresa_a.pk).exists()
		)
		lista = cliente.get('/api/auth/roles/').json()
		self.assertFalse(next(r for r in lista if r['codigo'] == 'MECANICO')['personalizado'])

	def test_override_empresa_afecta_permisos_drf(self):
		cliente = self._cliente(self.dueno)
		mecanico = Rol.objects.get(codigo='MECANICO')
		cliente.put(
			f'/api/auth/roles/{mecanico.pk}/permisos/',
			{'permisos': [{'recurso': 'facturacion', 'ver': True, 'modificar': False}]},
			format='json',
		)
		usuario = self._con_rol('MECANICO')
		request = self._request(user=usuario, empresa=self.empresa_a.pk)
		self.assertTrue(TienePermiso('facturacion', 'ver')().has_permission(request, None))

	def test_override_acciones_especiales_por_empresa(self):
		cliente = self._cliente(self.dueno)
		asesor = Rol.objects.get(codigo='ASESOR')
		cliente.put(
			f'/api/auth/roles/{asesor.pk}/acciones/',
			{'acciones': ['aprobar_descuentos']},
			format='json',
		)
		self.assertEqual(
			acciones_especiales_de_rol('ASESOR', self.empresa_a.pk),
			['aprobar_descuentos'],
		)
		self.assertEqual(acciones_especiales_de_rol('ASESOR'), [])
		self.assertTrue(
			PermisoEspecialEmpresa.objects.filter(
				rol=asesor, empresa_id=self.empresa_a.pk, permitido=True
			).exists()
		)

	def test_rol_personalizado_permisos_afectan_permisos_drf(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		cliente.put(
			f"/api/auth/roles/{rol['id']}/permisos/",
			{'permisos': [
				{'recurso': 'clientes', 'ver': True, 'modificar': False},
				{'recurso': 'facturacion', 'ver': True, 'modificar': True},
			]},
			format='json',
		)
		usuario = self._con_rol_empresa(rol['codigo'])
		request = self._request(user=usuario, empresa=self.empresa_a.pk)
		self.assertTrue(
			TienePermiso('facturacion', 'modificar')().has_permission(request, None)
		)
		self.assertTrue(
			TienePermiso('clientes', 'ver')().has_permission(request, None)
		)
		self.assertFalse(
			TienePermiso('clientes', 'modificar')().has_permission(request, None)
		)

	def test_asigna_rol_personalizado_a_un_empleado(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		resp = cliente.post(
			'/api/auth/empleados/',
			{'first_name': 'Ana', 'last_name': 'Supervisa',
			 'email': 'ana-super@taller.ec', 'rol': rol['codigo']},
			format='json',
		)
		self.assertEqual(resp.status_code, 201, resp.content)
		self.assertEqual(resp.json()['rol'], rol['codigo'])

	def test_rol_inactivo_no_asignable(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		cliente.patch(f"/api/auth/roles/{rol['id']}/", {'is_active': False}, format='json')
		resp = cliente.post(
			'/api/auth/empleados/',
			{'first_name': 'No', 'last_name': 'Aplica',
			 'email': 'no-aplica@taller.ec', 'rol': rol['codigo']},
			format='json',
		)
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_cuenta_usuarios_asignados(self):
		cliente = self._cliente(self.dueno)
		rol = self._crear_rol(cliente).json()
		usuario = self._con_rol_empresa(rol['codigo'])
		resp = cliente.get('/api/auth/roles/')
		por_id = {r['id']: r for r in resp.json()}
		self.assertEqual(por_id[rol['id']]['usuarios_count'], 1)
		detalle = cliente.get(f"/api/auth/roles/{rol['id']}/").json()
		self.assertEqual(len(detalle['usuarios']), 1)
		self.assertEqual(detalle['usuarios'][0]['email'], usuario.email)

	def test_mecanico_no_gestiona_roles(self):
		for ruta in (
			'/api/auth/roles/',
			'/api/auth/recursos/',
		):
			self.assertEqual(
				self._cliente(self._con_rol('MECANICO')).get(ruta).status_code, 403
			)

	def _con_rol(self, rol, talleres=None):
		usuario = User.objects.create_user(
			username=f'rol-custom-{rol.lower()}-{uuid.uuid4().hex[:6]}', password='x'
		)
		_asignar(usuario, self.empresa_a, rol, talleres or [self.a1])
		return usuario

	def _con_rol_empresa(self, codigo):
		usuario = User.objects.create_user(
			username=f'custom-{uuid.uuid4().hex[:6]}', password='x'
		)
		_asignar(usuario, self.empresa_a, codigo, [self.a1])
		return usuario


class EscalacionRolesTests(EscenarioMixin, APITestCase):
	"""A través del API solo se otorgan roles de nivel igual o inferior."""

	def setUp(self):
		super().setUp()
		self.gerente = User.objects.create_user(username='roberto', password='x')
		_asignar(self.gerente, self.empresa_a, 'ADMIN_TALLER', [self.a1])

	def _cliente(self, usuario):
		cliente = APIClient()
		cliente.force_authenticate(usuario)
		cliente.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa_a.pk)
		return cliente

	def _postea(self, rol):
		return self._cliente(self.gerente).post(
			'/api/auth/empleados/',
			{'first_name': 'Nuevo', 'last_name': 'Arriba',
			 'email': f'nuevo-{rol.lower()}@taller.ec', 'rol': rol},
			format='json',
		)

	def test_gerente_contrata_un_mecanico(self):
		resp = self._postea('MECANICO')
		self.assertEqual(resp.status_code, 201, resp.content)

	def test_gerente_crea_otro_gerente_del_mismo_nivel(self):
		resp = self._postea('ADMIN_TALLER')
		self.assertEqual(resp.status_code, 201, resp.content)

	def test_gerente_no_puede_crear_un_dueno(self):
		resp = self._postea('ADMIN_EMPRESA')
		self.assertEqual(resp.status_code, 400, resp.content)
		self.assertIn('rol', resp.json())

	def test_mecanico_no_contrata(self):
		mecanico = User.objects.create_user(username='tecnico', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO', [self.a1])
		resp = self._cliente(mecanico).post(
			'/api/auth/empleados/',
			{'first_name': 'N', 'last_name': 'N', 'email': 'nuevo-m@taller.ec', 'rol': 'CAJERO'},
			format='json',
		)
		self.assertEqual(resp.status_code, 403, resp.content)

	def test_no_desactivo_mi_propio_acceso(self):
		cliente = self._cliente(self.gerente)
		mi = UsuarioEmpresa.objects.get(user=self.gerente)
		resp = cliente.patch(f'/api/auth/empleados/{mi.pk}/', {'is_active': False}, format='json')
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_no_cambio_mi_propio_rol(self):
		cliente = self._cliente(self.gerente)
		mi = UsuarioEmpresa.objects.get(user=self.gerente)
		resp = cliente.patch(f'/api/auth/empleados/{mi.pk}/', {'rol': 'ASESOR'}, format='json')
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_no_me_doy_de_baja_a_mi_mismo(self):
		cliente = self._cliente(self.gerente)
		mi = UsuarioEmpresa.objects.get(user=self.gerente)
		resp = cliente.delete(f'/api/auth/empleados/{mi.pk}/')
		self.assertEqual(resp.status_code, 400, resp.content)

	def test_si_doy_de_baja_a_otro(self):
		cliente = self._cliente(self.gerente)
		mecanico = User.objects.create_user(username='baja-1', email='baja@taller.ec', password='x')
		_asignar(mecanico, self.empresa_a, 'MECANICO', [self.a1])
		ue = UsuarioEmpresa.objects.get(user=mecanico)
		resp = cliente.delete(f'/api/auth/empleados/{ue.pk}/')
		self.assertEqual(resp.status_code, 200, resp.content)
		ue.refresh_from_db()
		self.assertFalse(ue.is_active)


class EmpresaConfigPermisosTests(EscenarioMixin, APITestCase):
	"""Decisión F2: el gerente de taller pierde la edición de datos de empresa."""

	def _cliente(self, usuario):
		cliente = APIClient()
		cliente.force_authenticate(usuario)
		cliente.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa_a.pk)
		return cliente

	def test_gerente_de_taller_no_toca_datos_de_empresa(self):
		gerente = User.objects.create_user(username='gerente-conf', password='x')
		_asignar(gerente, self.empresa_a, 'ADMIN_TALLER', [self.a1])
		cliente = self._cliente(gerente)
		lectura = cliente.get('/api/configuracion/empresa/')
		self.assertEqual(lectura.status_code, 403, lectura.content)
		grabado = cliente.patch(f'/api/configuracion/empresa/{self.empresa_a.pk}/', {}, format='json')
		self.assertEqual(grabado.status_code, 403, grabado.content)

	def test_dueno_edita_datos_de_empresa(self):
		dueno = User.objects.create_user(username='dueno-conf', password='x')
		_asignar(dueno, self.empresa_a, 'ADMIN_EMPRESA', [self.a1])
		cliente = self._cliente(dueno)
		lectura = cliente.get('/api/configuracion/empresa/')
		self.assertEqual(lectura.status_code, 200, lectura.content)
		grabado = cliente.patch(
			f'/api/configuracion/empresa/{self.empresa_a.pk}/',
			{'nombre_comercial': 'Taller GON Config'},
			format='json',
		)
		self.assertEqual(grabado.status_code, 200, grabado.content)

	def test_gerente_sigue_gestionando_sucursales(self):
		gerente = User.objects.create_user(username='gerente-suc', password='x')
		_asignar(gerente, self.empresa_a, 'ADMIN_TALLER', [self.a1])
		cliente = self._cliente(gerente)
		resp = cliente.post(
			'/api/configuracion/sucursales/',
			{'nombre': 'Sucursal F2', 'direccion': 'Av. Norte', 'codigo_sucursal': 'SUC-F2'},
			format='json',
		)
		self.assertEqual(resp.status_code, 201, resp.content)

	def test_solo_admin_sistema_crea_empresas(self):
		dueno = User.objects.create_user(username='dueno-create', password='x')
		_asignar(dueno, self.empresa_a, 'ADMIN_EMPRESA', [self.a1])
		cliente = self._cliente(dueno)
		resp = cliente.post(
			'/api/empresas/',
			{'nombre_comercial': 'Taller Nuevo', 'razon_social': 'NUEVA SA', 'ruc': '9999999999001'},
			format='json',
		)
		self.assertEqual(resp.status_code, 403, resp.content)

		sistema = User.objects.create_superuser(username='sistema-root', password='x')
		resp2 = self._cliente(sistema).post(
			'/api/empresas/',
			{'nombre_comercial': 'Taller Nuevo', 'razon_social': 'NUEVA SA', 'ruc': '9999999999001'},
			format='json',
		)
		self.assertEqual(resp2.status_code, 201, resp2.content)

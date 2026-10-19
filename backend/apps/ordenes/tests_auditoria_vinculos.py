"""Auditoría de vínculos entre documentos del flujo escritos por la API.

Inspección, cotización y orden guardan FKs a otros documentos del flujo
(recepción, inspección, cotización, orden) y a la empresa. Cada test intenta,
como un ASESOR del taller A1, escribir uno de esos campos apuntando a un
documento de otra empresa o de un taller fuera de su alcance, y exige que el
documento quede sin cambios.
"""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo
from apps.vehiculos.models import Vehiculo, vincular_empresa

User = get_user_model()


def _ruc():
    return str(uuid4().int % 10_000_000_000_000).zfill(13)


class AuditoriaVinculosFlujoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa_a = Empresa.objects.create(nombre_comercial='Empresa A', ruc=_ruc())
        cls.empresa_b = Empresa.objects.create(nombre_comercial='Empresa B', ruc=_ruc())
        cls.taller_a1 = Taller.objects.create(empresa=cls.empresa_a, nombre='A1', codigo_sucursal='001', direccion='x')
        cls.taller_a2 = Taller.objects.create(empresa=cls.empresa_a, nombre='A2', codigo_sucursal='002', direccion='x')
        cls.taller_b = Taller.objects.create(empresa=cls.empresa_b, nombre='B1', codigo_sucursal='001', direccion='x')

        cls.cliente_a = Cliente.objects.create(empresa=cls.empresa_a, nombre='Cliente A', identificacion='1700000021')
        cls.cliente_b = Cliente.objects.create(empresa=cls.empresa_b, nombre='Cliente B', identificacion='1700000022')
        cls.vehiculo = Vehiculo.objects.create(placa='VIN0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa_a.pk)
        vincular_empresa(cls.vehiculo, cls.empresa_b.pk)

        # Documentos ajenos: otra empresa (B) y otro taller de la misma empresa (A2).
        cls.ajenos = {}
        for clave, empresa, taller, cliente in (
            ('b', cls.empresa_b, cls.taller_b, cls.cliente_b),
            ('a2', cls.empresa_a, cls.taller_a2, cls.cliente_a),
        ):
            base = dict(empresa=empresa, sucursal=taller, cliente=cliente, vehiculo=cls.vehiculo)
            cls.ajenos[clave] = {
                'recepcion': RecepcionVehiculo.objects.create(
                    **base, numero_recepcion=f'{clave}-REC', motivo_ingreso='x',
                ),
                'inspeccion': InspeccionVehiculo.objects.create(**base, numero_inspeccion=f'{clave}-INS'),
                'cotizacion': Cotizacion.objects.create(**base, numero_cotizacion=f'{clave}-COT'),
                'orden': OrdenTrabajo.objects.create(**base, numero_orden=f'{clave}-OT'),
            }

        user = User.objects.create_user(username='vin_asesor_a1', password='x', email='vin@test.ec')
        relacion = UsuarioEmpresa.objects.create(user=user, empresa=cls.empresa_a, rol='ASESOR')
        relacion.talleres.set([cls.taller_a1])
        cls.asesor_a1 = user

    def setUp(self):
        base = dict(empresa=self.empresa_a, sucursal=self.taller_a1, cliente=self.cliente_a, vehiculo=self.vehiculo)
        sufijo = uuid4().hex[:6]
        self.inspeccion = InspeccionVehiculo.objects.create(**base, numero_inspeccion=f'A1-INS-{sufijo}')
        self.cotizacion = Cotizacion.objects.create(**base, numero_cotizacion=f'A1-COT-{sufijo}')
        self.orden = OrdenTrabajo.objects.create(**base, numero_orden=f'A1-OT-{sufijo}', kilometraje=0)
        self.client = APIClient()
        self.client.force_authenticate(user=self.asesor_a1)
        fijar_empresa(self.client, str(self.empresa_a.uuid))

    def _intentar(self, url, documento, campo, destino, valor=None):
        """PATCH de `campo` hacia `destino`; el documento no debe cambiar."""
        valor = valor if valor is not None else str(destino.uuid)
        antes = getattr(documento, f'{campo}_id')
        respuesta = self.client.patch(url, {campo: valor}, format='json')
        documento.refresh_from_db()
        self.assertEqual(
            getattr(documento, f'{campo}_id'), antes,
            f'{campo} cambió (HTTP {respuesta.status_code}): {respuesta.content[:200]!r}',
        )
        return respuesta

    # --- Inspección -----------------------------------------------------------

    def _url_inspeccion(self):
        return f'/api/ordenes/inspecciones/{self.inspeccion.uuid}/'

    def test_inspeccion_no_cambia_de_empresa(self):
        self._intentar(self._url_inspeccion(), self.inspeccion, 'empresa', self.empresa_b)

    def test_inspeccion_no_vincula_recepcion_ajena(self):
        for clave in ('b', 'a2'):
            with self.subTest(origen=clave):
                self._intentar(self._url_inspeccion(), self.inspeccion, 'recepcion', self.ajenos[clave]['recepcion'])

    def test_inspeccion_no_vincula_orden_ajena(self):
        for clave in ('b', 'a2'):
            with self.subTest(origen=clave):
                self._intentar(self._url_inspeccion(), self.inspeccion, 'orden_trabajo', self.ajenos[clave]['orden'])

    def test_inspeccion_nueva_no_cuelga_de_recepcion_ajena(self):
        for clave in ('b', 'a2'):
            with self.subTest(origen=clave):
                recepcion = self.ajenos[clave]['recepcion']
                self.client.post('/api/ordenes/inspecciones/', {
                    'recepcion': str(recepcion.uuid),
                    'tipo_inspeccion': 'MANTENIMIENTO',
                    'motivo_ingreso': 'x',
                }, format='json')
                self.assertFalse(InspeccionVehiculo.objects.filter(recepcion=recepcion).exists())

    # --- Cotización -----------------------------------------------------------

    def _url_cotizacion(self):
        return f'/api/cotizaciones/{self.cotizacion.uuid}/'

    def test_cotizacion_no_vincula_documentos_ajenos(self):
        for campo, tipo in (
            ('recepcion_origen', 'recepcion'),
            ('inspeccion_origen', 'inspeccion'),
            ('orden_trabajo_origen', 'orden'),
        ):
            for clave in ('b', 'a2'):
                with self.subTest(campo=campo, origen=clave):
                    self._intentar(self._url_cotizacion(), self.cotizacion, campo, self.ajenos[clave][tipo])

    # --- Orden de trabajo -----------------------------------------------------

    def test_orden_no_vincula_cotizacion_ajena(self):
        for clave in ('b', 'a2'):
            with self.subTest(origen=clave):
                self._intentar(
                    f'/api/ordenes/ordenes-trabajo/{self.orden.uuid}/', self.orden,
                    'cotizacion_origen', self.ajenos[clave]['cotizacion'],
                )

    def test_cotizacion_nueva_no_nace_de_documentos_ajenos(self):
        for campo, tipo in (
            ('recepcion_origen', 'recepcion'),
            ('inspeccion_origen', 'inspeccion'),
            ('orden_trabajo_origen', 'orden'),
        ):
            for clave in ('b', 'a2'):
                with self.subTest(campo=campo, origen=clave):
                    destino = self.ajenos[clave][tipo]
                    respuesta = self.client.post('/api/cotizaciones/', {
                        'cliente': str(self.cliente_a.uuid),
                        'vehiculo': str(self.vehiculo.uuid),
                        campo: str(destino.uuid),
                    }, format='json')
                    self.assertFalse(
                        Cotizacion.objects.filter(**{campo: destino}).exists(),
                        f'Se creó con {campo} ajeno (HTTP {respuesta.status_code}): {respuesta.content[:200]!r}',
                    )

    def test_orden_nueva_no_nace_de_cotizacion_ajena(self):
        for clave in ('b', 'a2'):
            with self.subTest(origen=clave):
                destino = self.ajenos[clave]['cotizacion']
                respuesta = self.client.post('/api/ordenes/ordenes-trabajo/', {
                    'cliente': str(self.cliente_a.uuid),
                    'vehiculo': str(self.vehiculo.uuid),
                    'cotizacion_origen': str(destino.uuid),
                    'fecha_ingreso': '2026-10-19T09:00:00-05:00',
                    'kilometraje': 0,
                }, format='json')
                self.assertFalse(
                    OrdenTrabajo.objects.filter(cotizacion_origen=destino).exclude(pk=destino.pk).exists(),
                    f'Se creó con cotización ajena (HTTP {respuesta.status_code}): {respuesta.content[:200]!r}',
                )

    # --- Acciones que crean otro documento ------------------------------------

    def test_generar_orden_exige_modificar_ordenes(self):
        from apps.authentication.models import Rol
        from apps.authentication.permisos_repo import guardar_permisos_empresa

        url = f'/api/cotizaciones/{self.cotizacion.uuid}/generar_orden/'
        # Con permisos completos la acción se evalúa (falla por el estado, no por permiso).
        self.assertNotEqual(self.client.post(url, {}, format='json').status_code, 403)

        guardar_permisos_empresa(
            Rol.objects.get(codigo='ASESOR'),
            self.empresa_a.id,
            [{'recurso': 'ordenes', 'ver': True, 'modificar': False}],
        )
        respuesta = self.client.post(url, {}, format='json')
        self.assertEqual(respuesta.status_code, 403, respuesta.content)


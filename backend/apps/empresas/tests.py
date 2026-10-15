"""Tests de la numeración de documentos (apps/empresas/services.py).

Cada taller tiene su propia secuencia (prefijo, contador y dígitos) y el número
es único **dentro del taller**: los modelos lo garantizan con
`UNIQUE (empresa, sucursal, numero_*)`. Estos tests fijan que dos sucursales
pueden emitir REC-00001 sin colisionar y que, dentro de una misma sucursal, el
generador siempre entrega el siguiente número **disponible**, aunque el contador
esté atrasado.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion
from apps.ordenes.models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo
from apps.vehiculos.models import Vehiculo

from .models import Empresa, Taller
from .services import generar_codigo_secuencial, ultimo_numero_emitido


class NumeracionDocumentosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Taller Numeracion', ruc='999999999999')
        cls.otra_empresa = Empresa.objects.create(nombre_comercial='Otra Numeracion', ruc='888888888888')
        cls.central = Taller.objects.create(
            empresa=cls.empresa, nombre='Central', direccion='Av. Central 100', codigo_sucursal='001',
        )
        cls.sucursal = Taller.objects.create(
            empresa=cls.empresa, nombre='Sucursal Norte', direccion='Av. Norte 200', codigo_sucursal='002',
        )
        cls.ajeno = Taller.objects.create(
            empresa=cls.otra_empresa, nombre='Ajeno', direccion='Av. Ajena 300', codigo_sucursal='001',
        )
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa, nombre='Cliente Numeración', identificacion='1711111111',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='NUM-001', marca='Kia', modelo='Rio')
        cls.vehiculo.empresas.add(cls.empresa)

    def _recepcion(self, numero, sucursal, **extra):
        return RecepcionVehiculo.objects.create(
            empresa=self.empresa,
            sucursal=sucursal,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            numero_recepcion=numero,
            **extra,
        )

    # --- Secuencia básica ---

    def test_primer_codigo_del_taller(self):
        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-00001')

    def test_el_codigo_respeta_prefijo_y_digitos_del_taller(self):
        self.central.prefijo_recepcion = 'REC-'
        self.central.digitos_recepcion = 3
        self.central.save(update_fields=['prefijo_recepcion', 'digitos_recepcion'])

        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-001')

    def test_incrementa_el_contador_del_taller(self):
        generar_codigo_secuencial(self.central, 'recepcion')
        generar_codigo_secuencial(self.central, 'recepcion')

        self.central.refresh_from_db()
        self.assertEqual(self.central.siguiente_recepcion, 3)

    def test_cada_tipo_de_documento_usa_su_propia_secuencia(self):
        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-00001')
        self.assertEqual(generar_codigo_secuencial(self.central, 'inspeccion'), 'INS-00001')
        self.assertEqual(generar_codigo_secuencial(self.central, 'cotizacion'), 'COT-00001')
        self.assertEqual(generar_codigo_secuencial(self.central, 'ot'), 'OT-00001')

    # --- Secuencia independiente por taller (el bug reportado) ---

    def test_cada_taller_mantiene_su_propia_secuencia(self):
        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-00001')
        self.assertEqual(generar_codigo_secuencial(self.sucursal, 'recepcion'), 'REC-00001')

        self._recepcion('REC-00001', self.central)
        self._recepcion('REC-00001', self.sucursal)
        self.assertEqual(RecepcionVehiculo.objects.filter(empresa=self.empresa).count(), 2)

    def test_el_contador_de_una_sucursal_no_consume_la_otra(self):
        self._recepcion(generar_codigo_secuencial(self.central, 'recepcion'), self.central)
        self._recepcion(generar_codigo_secuencial(self.central, 'recepcion'), self.central)

        self.assertEqual(generar_codigo_secuencial(self.sucursal, 'recepcion'), 'REC-00001')

    def test_la_restriccion_bloquea_repetidos_en_la_misma_sucursal(self):
        self._recepcion('REC-00001', self.central)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._recepcion('REC-00001', self.central)

    def test_avanza_si_el_numero_ya_fue_emitido_en_la_misma_sucursal(self):
        # Central ya emitió hasta REC-00003 pero su contador quedó en 1.
        for numero in ('REC-00001', 'REC-00002', 'REC-00003'):
            self._recepcion(numero, self.central)
        self.central.siguiente_recepcion = 1
        self.central.save(update_fields=['siguiente_recepcion'])

        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-00004')

    def test_salta_numeros_huecos_ya_usados(self):
        self._recepcion('REC-00001', self.central)
        self.central.siguiente_recepcion = 1
        self.central.save(update_fields=['siguiente_recepcion'])

        codigo = generar_codigo_secuencial(self.central, 'recepcion')

        self.assertEqual(codigo, 'REC-00002')

    def test_el_otro_taller_no_consume_la_secuencia_del_primer_taller(self):
        # Cada taller mantiene su propia secuencia, aunque comparta prefijo.
        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-00001')
        self.assertEqual(generar_codigo_secuencial(self.ajeno, 'recepcion'), 'REC-00001')

    def test_prefijos_distintos_mantienen_secuencias_independientes(self):
        Taller.objects.filter(pk=self.sucursal.pk).update(prefijo_recepcion='PREF-')

        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-00001')
        self.assertEqual(generar_codigo_secuencial(self.sucursal, 'recepcion'), 'PREF-00001')

    # --- Helper de auditoría ---

    def test_ultimo_numero_emitido_ignora_otros_tipos(self):
        self._recepcion('REC-00007', self.central)

        self.assertEqual(ultimo_numero_emitido(self.central, 'recepcion'), 7)
        self.assertEqual(ultimo_numero_emitido(self.central, 'inspeccion'), 0)

    def test_ultimo_numero_emitido_ignora_documentos_de_otra_sucursal(self):
        self._recepcion('REC-00009', self.central)
        self._recepcion('REC-00001', self.sucursal)

        self.assertEqual(ultimo_numero_emitido(self.central, 'recepcion'), 9)
        self.assertEqual(ultimo_numero_emitido(self.sucursal, 'recepcion'), 1)

    def test_ultimo_numero_emitido_cuenta_documentos_dados_de_baja(self):
        self._recepcion('REC-00004', self.central, is_active=False)

        self.assertEqual(ultimo_numero_emitido(self.central, 'recepcion'), 4)
        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-00005')

    def test_modelos_de_documento_comparten_la_misma_numeracion(self):
        # La restricción se aplica igual a los cuatro documentos.
        Cotizacion.objects.create(
            empresa=self.empresa, sucursal=self.central, cliente=self.cliente,
            numero_cotizacion='COT-00001',
        )
        InspeccionVehiculo.objects.create(
            empresa=self.empresa, sucursal=self.central, cliente=self.cliente,
            vehiculo=self.vehiculo, motivo_ingreso='Revisión', numero_inspeccion='INS-00001',
        )
        OrdenTrabajo.objects.create(
            empresa=self.empresa, sucursal=self.central, cliente=self.cliente,
            vehiculo=self.vehiculo, numero_orden='OT-00001',
        )

        self.assertEqual(generar_codigo_secuencial(self.central, 'recepcion'), 'REC-00001')
        self.assertEqual(generar_codigo_secuencial(self.central, 'cotizacion'), 'COT-00002')
        self.assertEqual(generar_codigo_secuencial(self.sucursal, 'cotizacion'), 'COT-00001')

class EmpresaCrudAdminSaaSTests(TestCase):
    """`/api/empresas/`: CRUD exclusivo del administrador del SaaS."""

    endpoint = '/api/empresas/'

    def setUp(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        from apps.authentication.models import UsuarioEmpresa

        self.activa = Empresa.objects.create(
            nombre_comercial='Alfa Motors', ruc='1790000000001', email_contacto='alfa@ejemplo.com'
        )
        self.inactiva = Empresa.objects.create(
            nombre_comercial='Beta Autos', ruc='1790000000002',
            email_contacto='beta@ejemplo.com', is_active=False,
        )
        self.saas = APIClient()
        self.saas.force_authenticate(
            User.objects.create_superuser(username='saas', password='x')
        )
        dueno = User.objects.create_user(username='dueno', password='x')
        UsuarioEmpresa.objects.create(user=dueno, empresa=self.activa, rol='ADMIN_EMPRESA')
        self.dueno = APIClient()
        self.dueno.force_authenticate(dueno)
        self.dueno.defaults['HTTP_X_EMPRESA_ID'] = str(self.activa.pk)

    def _payload(self, **extra):
        return {
            'nombre_comercial': 'Gamma Taller',
            'razon_social': 'GAMMA S.A.',
            'ruc': '1790000000003',
            'telefono': '0991234567',
            'email_contacto': 'Gamma@Ejemplo.com',
            'is_active': True,
            **extra,
        }

    def test_solo_admin_saas_accede(self):
        self.assertEqual(self.dueno.get(self.endpoint).status_code, 403)
        self.assertEqual(self.dueno.post(self.endpoint, self._payload(), format='json').status_code, 403)
        self.assertEqual(
            self.dueno.patch(f'{self.endpoint}{self.activa.pk}/', {'nombre_comercial': 'X'}, format='json').status_code,
            403,
        )

    def test_lista_activas_e_inactivas_con_filtros(self):
        resp = self.saas.get(self.endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 2)
        resp = self.saas.get(self.endpoint, {'estado': 'inactivo'})
        self.assertEqual([e['id'] for e in resp.data['results']], [self.inactiva.pk])
        resp = self.saas.get(self.endpoint, {'search': '0000000001'})
        self.assertEqual([e['id'] for e in resp.data['results']], [self.activa.pk])

    def test_crea_y_edita(self):
        resp = self.saas.post(self.endpoint, self._payload(), format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.data['email_contacto'], 'gamma@ejemplo.com')
        resp = self.saas.patch(
            f"{self.endpoint}{resp.data['id']}/", {'is_active': False, 'telefono': '022345678'}, format='json'
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.data['is_active'])

    def test_valida_ruc(self):
        resp = self.saas.post(self.endpoint, self._payload(ruc='123'), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('ruc', resp.data)
        resp = self.saas.post(self.endpoint, self._payload(ruc=self.inactiva.ruc), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Ya existe', str(resp.data['ruc']))
        # Editar sin cambiar el RUC no choca consigo misma.
        resp = self.saas.patch(
            f'{self.endpoint}{self.activa.pk}/', {'ruc': self.activa.ruc}, format='json'
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_eliminar_es_logico_y_libera_el_ruc(self):
        resp = self.saas.delete(f'{self.endpoint}{self.activa.pk}/')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['action'], 'soft_delete')
        self.assertTrue(Empresa.all_objects.filter(pk=self.activa.pk, deleted_at__isnull=False).exists())
        self.assertEqual(self.saas.get(self.endpoint).data['count'], 1)
        resp = self.saas.post(self.endpoint, self._payload(ruc=self.activa.ruc), format='json')
        self.assertEqual(resp.status_code, 201, resp.content)

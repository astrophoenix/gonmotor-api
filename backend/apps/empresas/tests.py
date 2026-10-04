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
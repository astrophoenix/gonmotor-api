from django.test import SimpleTestCase
from unittest.mock import MagicMock

from rest_framework import serializers

from .models import Cotizacion
from .serializers import transicion_estado_valida
from .views import _validar_origen_inspeccion


class TransicionEstadoTests(SimpleTestCase):
    def test_transiciones_validas_del_flujo(self):
        casos = [
            (Cotizacion.EstadoCotizacion.BORRADOR, Cotizacion.EstadoCotizacion.ENVIADA, True),
            (Cotizacion.EstadoCotizacion.ENVIADA, Cotizacion.EstadoCotizacion.ACEPTADA, True),
            (Cotizacion.EstadoCotizacion.ENVIADA, Cotizacion.EstadoCotizacion.RECHAZADA, True),
            (Cotizacion.EstadoCotizacion.ACEPTADA, Cotizacion.EstadoCotizacion.CONVERTIDA, False),
            (Cotizacion.EstadoCotizacion.RECHAZADA, Cotizacion.EstadoCotizacion.ENVIADA, True),
            (Cotizacion.EstadoCotizacion.CONVERTIDA, Cotizacion.EstadoCotizacion.ENVIADA, False),
        ]
        for actual, nuevo, esperado in casos:
            with self.subTest(actual=actual, nuevo=nuevo):
                self.assertEqual(transicion_estado_valida(actual, nuevo), esperado)

    def test_mismo_estado_siempre_valido(self):
        for estado in Cotizacion.EstadoCotizacion:
            if estado.value != Cotizacion.EstadoCotizacion.ACEPTADA:
                self.assertTrue(transicion_estado_valida(estado, estado))

    def test_no_hay_salto_de_borrador_a_aceptada(self):
        self.assertFalse(
            transicion_estado_valida(Cotizacion.EstadoCotizacion.BORRADOR, Cotizacion.EstadoCotizacion.ACEPTADA)
        )


class ValidarOrigenInspeccionTests(SimpleTestCase):
    def _inspeccion(self, orden_trabajo_id=None, hay_activa=False):
        inspeccion = MagicMock()
        inspeccion.orden_trabajo_id = orden_trabajo_id
        filtrar = inspeccion.cotizaciones_generadas.filter
        filtrar.return_value.exists.return_value = hay_activa
        return inspeccion

    def test_sin_inspeccion_no_valida(self):
        self.assertIsNone(_validar_origen_inspeccion(None))

    def test_inspeccion_con_orden_trabajo_rechazada(self):
        inspeccion = self._inspeccion(orden_trabajo_id=10)
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_inspeccion(inspeccion)

    def test_inspeccion_con_cotizacion_activa_rechazada(self):
        inspeccion = self._inspeccion(hay_activa=True)
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_inspeccion(inspeccion)

    def test_inspeccion_libre_aprueba(self):
        inspeccion = self._inspeccion()
        self.assertIsNone(_validar_origen_inspeccion(inspeccion))
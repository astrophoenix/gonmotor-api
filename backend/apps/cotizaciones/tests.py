from decimal import Decimal
from unittest.mock import MagicMock

from django.db import IntegrityError
from django.test import SimpleTestCase

from rest_framework import serializers

from .models import Cotizacion
from .serializers import transicion_estado_valida
from .views import _traducir_integridad, _validar_origen_cotizacion, _validar_origen_inspeccion


class TransicionEstadoTests(SimpleTestCase):
    def test_transiciones_validas_del_flujo(self):
        casos = [
            (Cotizacion.EstadoCotizacion.BORRADOR, Cotizacion.EstadoCotizacion.ENVIADA, True),
            (Cotizacion.EstadoCotizacion.ENVIADA, Cotizacion.EstadoCotizacion.ACEPTADA, True),
            (Cotizacion.EstadoCotizacion.ENVIADA, Cotizacion.EstadoCotizacion.RECHAZADA, True),
            (Cotizacion.EstadoCotizacion.ACEPTADA, Cotizacion.EstadoCotizacion.ENVIADA, True),
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


class ValidarOrigenCotizacionTests(SimpleTestCase):
    def _origen(self, **atributos):
        origen = MagicMock()
        origen.orden_trabajo_id = None
        origen.numero_inspeccion = 'INS-00001'
        origen.numero_recepcion = 'REC-00001'
        cotizacion_vigente = atributos.pop('vigente', None)
        origen.cotizaciones_generadas.filter.return_value.first.return_value = cotizacion_vigente
        for clave, valor in atributos.items():
            setattr(origen, clave, valor)
        return origen

    def _vigente(self, numero='COT-00001'):
        cotizacion = MagicMock()
        cotizacion.numero_cotizacion = numero
        return cotizacion

    def test_sin_origenes_no_valida(self):
        self.assertIsNone(_validar_origen_cotizacion())

    def test_cotizacion_independiente_sin_inspeccion_aprueba(self):
        # Cliente que llama por teléfono: no hay recepción ni inspección.
        self.assertIsNone(_validar_origen_cotizacion())

    def test_inspeccion_con_orden_trabajo_rechazada(self):
        inspeccion = self._origen(orden_trabajo_id=10)
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_cotizacion(inspeccion=inspeccion)

    def test_inspeccion_con_cotizacion_aceptada_rechazada(self):
        inspeccion = self._origen(vigente=self._vigente('COT-00004'))
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_cotizacion(inspeccion=inspeccion)

    def test_inspeccion_con_cotizacion_borrador_rechazada(self):
        inspeccion = self._origen(vigente=self._vigente())
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_cotizacion(inspeccion=inspeccion)

    def test_inspeccion_libre_aprueba(self):
        self.assertIsNone(_validar_origen_cotizacion(inspeccion=self._origen()))

    def test_recepcion_con_cotizacion_vigente_rechazada(self):
        recepcion = self._origen(vigente=self._vigente('COT-00007'))
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_cotizacion(recepcion=recepcion)

    def test_recepcion_libre_aprueba(self):
        self.assertIsNone(_validar_origen_cotizacion(recepcion=self._origen()))

    def test_mensaje_incluye_numero_de_cotizacion_vigente(self):
        recepcion = self._origen(vigente=self._vigente('COT-00007'))
        with self.assertRaises(serializers.ValidationError) as contexto:
            _validar_origen_cotizacion(recepcion=recepcion)
        self.assertIn('COT-00007', str(contexto.exception))

    def test_validar_origen_inspeccion_mantiene_compatibilidad(self):
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_inspeccion(self._origen(vigente=self._vigente()))
        self.assertIsNone(_validar_origen_inspeccion(None))


class TraducirIntegridadTests(SimpleTestCase):
    def _origen(self, numero_cotizacion='COT-00007', estado_display='Aceptada'):
        origen = MagicMock()
        cotizacion = MagicMock()
        cotizacion.numero_cotizacion = numero_cotizacion
        cotizacion.get_estado_display.return_value = estado_display
        filtrado = origen.cotizaciones_generadas.filter.return_value
        filtrado.exclude.return_value = filtrado
        filtrado.first.return_value = cotizacion
        return origen

    def test_constraint_de_recepcion_da_error_de_validacion(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_recepcion_vigente_unica"')
        with self.assertRaises(serializers.ValidationError):
            _traducir_integridad(error)

    def test_constraint_de_inspeccion_da_error_de_validacion(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_inspeccion_vigente_unica"')
        with self.assertRaises(serializers.ValidationError):
            _traducir_integridad(error)

    def test_mensaje_identifica_la_cotizacion_en_conflicto(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_recepcion_vigente_unica"')
        with self.assertRaises(serializers.ValidationError) as contexto:
            _traducir_integridad(error, recepcion=self._origen())
        mensaje = str(contexto.exception)
        self.assertIn('COT-00007', mensaje)
        self.assertIn('Aceptada', mensaje)

    def test_mensaje_sin_origen_no_rompe(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_inspeccion_vigente_unica"')
        with self.assertRaises(serializers.ValidationError) as contexto:
            _traducir_integridad(error)
        self.assertIn('inspección', str(contexto.exception))

    def test_otros_errores_se_dejan_pasar(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_empresa_numero_unico"')
        self.assertIs(_traducir_integridad(error), error)


class ActualizacionOrigenInmutableTests(SimpleTestCase):
    def test_reapertura_choca_con_la_maquina_de_estados(self):
        # Reabrir es una transición válida, pero el constraint de la BD impide
        # que dos cotizaciones del mismo origen queden vigentes a la vez.
        self.assertTrue(
            transicion_estado_valida(Cotizacion.EstadoCotizacion.RECHAZADA, Cotizacion.EstadoCotizacion.ENVIADA)
        )
        self.assertTrue(
            transicion_estado_valida(Cotizacion.EstadoCotizacion.ACEPTADA, Cotizacion.EstadoCotizacion.ENVIADA)
        )
        self.assertIn(
            Cotizacion.EstadoCotizacion.ACEPTADA,
            Cotizacion.ESTADOS_VIGENTES,
        )
        self.assertNotIn(
            Cotizacion.EstadoCotizacion.RECHAZADA,
            Cotizacion.ESTADOS_VIGENTES,
        )


class GenerarOrdenTests(SimpleTestCase):
    def _cotizacion(self, estado, orden_origen_id=None, inspeccion=None, recepcion=None, total='50.00', con_detalles=True):
        cotizacion = MagicMock()
        cotizacion.orden_trabajo_origen_id = orden_origen_id
        cotizacion.estado = estado
        cotizacion.total = Decimal(total)
        cotizacion.EstadoCotizacion = Cotizacion.EstadoCotizacion
        cotizacion.inspeccion_origen_id = inspeccion.id if inspeccion is not None else None
        cotizacion.inspeccion_origen = inspeccion
        cotizacion.recepcion_origen = recepcion
        if con_detalles:
            cotizacion.servicios.exists.return_value = True
        else:
            cotizacion.servicios.exists.return_value = False
            cotizacion.repuestos.exists.return_value = False
        cotizacion.generar_orden = Cotizacion.generar_orden.__get__(cotizacion, Cotizacion)
        return cotizacion

    def _origen(self, orden_trabajo_id=None):
        origen = MagicMock()
        origen.orden_trabajo_id = orden_trabajo_id
        origen.numero_inspeccion = 'INS-00001'
        origen.numero_recepcion = 'REC-00001'
        origen.recepcion_id = None
        return origen

    def test_con_orden_trabajo_origen_rechazada(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ENVIADA, orden_origen_id=7)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()

    def test_ya_convertida_rechazada(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.CONVERTIDA)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()

    def test_no_llama_creacion_cuando_hay_orden_origen(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ENVIADA, orden_origen_id=1)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_inspeccion_ya_convertida_en_otra_ot_rechazada(self):
        inspeccion = self._origen(orden_trabajo_id=5)
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA, inspeccion=inspeccion)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_recepcion_ya_con_ot_rechazada(self):
        recepcion = self._origen(orden_trabajo_id=5)
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA, recepcion=recepcion)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_sin_detalles_rechazada(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA, con_detalles=False)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_total_cero_rechazada(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA, total='0.00')
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_cotizacion_valida_llega_a_crear_la_orden(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA)
        cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_called_once()

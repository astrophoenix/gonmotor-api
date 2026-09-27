from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .models import InspeccionVehiculo


class OrdenesCrudTests(SimpleTestCase):
    def test_imports_crud_components(self):
        from apps.ordenes.views import OrdenTrabajoViewSet
        from apps.ordenes.serializers import OrdenTrabajoSerializer

        self.assertIsNotNone(OrdenTrabajoViewSet)
        self.assertIsNotNone(OrdenTrabajoSerializer)


class InspeccionFechasTests(TestCase):
    def _inspeccion(self, **atributos):
        return InspeccionVehiculo.objects.create(
            numero_inspeccion=atributos.pop('numero_inspeccion', 'INS-TEST-0001'),
            **atributos,
        )

    def test_fecha_inspeccion_por_defecto(self):
        inspeccion = self._inspeccion()

        self.assertIsNotNone(inspeccion.fecha_inspeccion)

    def test_fecha_finalizacion_nula_mientras_no_este_finalizada(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')

        self.assertIsNone(inspeccion.fecha_finalizacion)

    def test_finalizar_sella_fecha_finalizacion(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')

        inspeccion.estado = 'FINALIZADA'
        inspeccion.save(update_fields=['estado', 'updated_at'])

        inspeccion.refresh_from_db()
        self.assertIsNotNone(inspeccion.fecha_finalizacion)
        self.assertEqual(inspeccion.estado, 'FINALIZADA')

    def test_finalizar_respeta_update_fields(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')

        inspeccion.estado = 'FINALIZADA'
        inspeccion.save(update_fields=['estado', 'updated_at'])

        # La fecha se escribe aunque no esté en update_fields.
        inspeccion.refresh_from_db()
        self.assertIsNotNone(inspeccion.fecha_finalizacion)

    def test_reabrir_limpia_fecha_finalizacion(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')
        inspeccion.estado = 'FINALIZADA'
        inspeccion.save()
        self.assertIsNotNone(inspeccion.fecha_finalizacion)

        inspeccion.estado = 'EN_PROCESO'
        inspeccion.save()

        inspeccion.refresh_from_db()
        self.assertIsNone(inspeccion.fecha_finalizacion)

    def test_no_sobrescribe_fecha_finalizacion_al_editar_una_finalizada(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')
        inspeccion.estado = 'FINALIZADA'
        inspeccion.save()
        cerrada = inspeccion.fecha_finalizacion

        inspeccion.diagnostico_tecnico = 'Ajuste posterior'
        inspeccion.save()

        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.fecha_finalizacion, cerrada)

    def test_duracion_inspeccion_en_minutos(self):
        inicio = timezone.now() - timedelta(hours=2, minutes=15)
        inspeccion = self._inspeccion(estado='EN_PROCESO', fecha_inspeccion=inicio)
        inspeccion.estado = 'FINALIZADA'
        inspeccion.save()

        self.assertEqual(inspeccion.duracion_inspeccion, 135)

    def test_duracion_calculada_entre_fechas(self):
        inicio = timezone.now() - timedelta(minutes=95)
        inspeccion = InspeccionVehiculo(
            numero_inspeccion='INS-TEST-0002',
            estado='FINALIZADA',
            fecha_inspeccion=inicio,
        )
        inspeccion.save()
        inspeccion.refresh_from_db()
        inspeccion.fecha_finalizacion = inicio + timedelta(minutes=95)
        inspeccion.save()

        self.assertEqual(inspeccion.duracion_inspeccion, 95)

    def test_duracion_es_none_sin_finalizacion(self):
        inspeccion = self._inspeccion(estado='EN_PROCESO')

        self.assertIsNone(inspeccion.duracion_inspeccion)

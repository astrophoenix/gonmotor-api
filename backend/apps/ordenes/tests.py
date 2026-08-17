from django.test import SimpleTestCase


class OrdenesCrudTests(SimpleTestCase):
    def test_imports_crud_components(self):
        from apps.ordenes.views import OrdenTrabajoViewSet
        from apps.ordenes.serializers import OrdenTrabajoSerializer

        self.assertIsNotNone(OrdenTrabajoViewSet)
        self.assertIsNotNone(OrdenTrabajoSerializer)

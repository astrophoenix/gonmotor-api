from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status


class HealthCheckView(APIView):
    """
    Endpoint para verificar que el servidor/backend y la API están funcionando correctamente.
    Útil para monitoreo, despliegues o validación inicial del frontend.
    """
    permission_classes = []  # Opcional: permite que sea público para herramientas de monitoreo

    def get(self, request):
        return Response(
            {
                "status": "ok",
                "message": "API de Gestión de Taller Automotriz operativa",
                "version": "1.0.0"
            },
            status=status.HTTP_200_OK
        )
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.renderers import JSONRenderer

from apps.core.services.text_improver import TextImproverError, mejorar_texto


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


class MejorarTextoView(APIView):
    """
    Endpoint genérico y reutilizable: mejora el texto de cualquier campo de
    texto libre (motivo, observaciones, etc.) usando un LLM.

    Body esperado:
        - texto: cadena con el texto a mejorar (obligatorio, máx. 500 chars).
        - contexto: pista opcional del tipo de campo (ej. "motivo de ingreso",
          "observaciones del tablero") para afinar la reescritura.

    Respuesta:
        - { "mejorado": "<texto mejorado>" }
    """
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        texto = data.get('texto') or ''
        contexto = data.get('contexto') or ''
        try:
            mejorado = mejorar_texto(texto, contexto)
        except TextImproverError as exc:
            return Response({'detail': str(exc)}, status=exc.status)
        return Response({'mejorado': mejorado})
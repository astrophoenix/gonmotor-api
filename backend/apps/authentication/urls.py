from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    CustomTokenObtainPairView, 
    UserProfileView, 
    UserManagementViewSet,
    PasswordResetRequestView,
    PasswordResetConfirmView
)

router = DefaultRouter()
router.register(r'usuarios', UserManagementViewSet, basename='usuarios-gestion')

urlpatterns = [
    # Endpoint de Login (Obtener Token)
    path('login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    
    # Endpoint para refrescar el Access Token cuando expira
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # Endpoint para obtener datos del usuario autenticado
    path('me/', UserProfileView.as_view(), name='user_profile'),

    # 🔑 Recuperación de contraseña
    path('password-reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    
    path('', include(router.urls)),
]
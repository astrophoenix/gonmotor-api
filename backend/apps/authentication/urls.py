# apps/authentication/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    CustomTokenObtainPairView, 
    SelectCompanyView,
    RegistrationView,
    UserProfileView, 
    UserManagementViewSet,
    EmpleadoViewSet,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    ChangePasswordView
)

router = DefaultRouter()
router.register(r'usuarios', UserManagementViewSet, basename='usuarios-gestion')
router.register(r'empleados', EmpleadoViewSet, basename='empleados')

urlpatterns = [
    # Endpoint de Login (Obtener Token o lista de empresas)
    path('login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),

    # 🏢 Endpoint para seleccionar empresa cuando hay múltiples RUCs
    path('select-company/', SelectCompanyView.as_view(), name='select_company'),

    # Registro público de nuevas cuentas
    path('register/', RegistrationView.as_view(), name='user_register'),
    
    # Endpoint para refrescar el Access Token cuando expira
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # Endpoint para obtener datos del usuario autenticado
    path('me/', UserProfileView.as_view(), name='user_profile'),

    # Endpoint para cambiar contraseña del usuario autenticado
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),

    # 🔑 Recuperación de contraseña
    path('password-reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    
    path('', include(router.urls)),
]

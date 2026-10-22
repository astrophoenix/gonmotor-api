# Correo de invitación de empleados

El alta con acceso al sistema envía un enlace para establecer la contraseña.
La API responde 201 cuando el backend de correo acepta la invitación. Si el
envío falla, responde 503 con un mensaje en español y revierte el alta completa
(usuario, perfil, relación con la empresa y talleres). El formulario conserva
los datos y se puede reintentar con el mismo correo tras resolver el servicio.
Las altas sin acceso no envían invitación.

Antes de crear los registros, el alta comprueba que SMTP tenga servidor y
remitente válido, que las credenciales no estén incompletas y que TLS/SSL no
estén activados a la vez. Los backends de desarrollo (consola y memoria) siguen
funcionando sin configuración SMTP. Un servidor que permita enviar sin
autenticación puede dejar vacíos tanto usuario como contraseña.

Los errores devuelven JSON con `detail` (mensaje para el formulario) y `code`:

| Código | Motivo |
| --- | --- |
| `correo_no_configurado` | Configuración SMTP ausente o incompatible |
| `correo_autenticacion_fallida` | El proveedor rechazó las credenciales |
| `correo_destinatario_rechazado` | El proveedor rechazó el destinatario |
| `correo_remitente_rechazado` | El proveedor rechazó el remitente |
| `correo_seguridad_fallida` | Falló la conexión segura o no se admite STARTTLS |
| `correo_tiempo_agotado` | El proveedor no respondió a tiempo |
| `correo_conexion_fallida` | No se pudo conectar o se perdió la conexión |
| `invitacion_no_enviada` | Otro fallo de envío o invitación no aceptada |

Todos mantienen HTTP 503 y revierten el alta. Las respuestas no incluyen el
texto técnico del proveedor ni sus credenciales. Los detalles de la excepción
de envío se registran en los logs del servidor para el administrador.

## Configuración de producción

`config.settings.production` usa SMTP. Configurar en el entorno del servicio de
Render (un archivo `.env` local no configura el servicio desplegado):

- `EMAIL_HOST`: hostname SMTP del proveedor, no vacío.
- `EMAIL_PORT`: puerto de envío, por defecto 587.
- `EMAIL_USE_TLS`: STARTTLS, por defecto `True`.
- `EMAIL_HOST_USER` y `EMAIL_HOST_PASSWORD`: credenciales del proveedor.
- `DEFAULT_FROM_EMAIL`: remitente autorizado por el proveedor.
- `EMAIL_TIMEOUT`: espera de red en segundos, por defecto 10.
- `FRONTEND_URL`: `https://gonmotor-ui.vercel.app`, para el enlace de contraseña.

El repositorio declara `plan: free` en `render.yaml`. Si el servicio sigue en
ese plan, Render bloquea SMTP saliente en los puertos 25, 465 y 587:
https://render.com/docs/free#other-limitations
En ese caso, configurar credenciales SMTP no basta: se necesita integrar un
proveedor de correo mediante HTTPS o usar un servicio que permita el puerto
SMTP del proveedor. Cambiar las variables SMTP no activa una integración HTTP.

## Diagnóstico del incidente

El log del 22 de octubre de 2026 registra `SMTPServerDisconnected: please run
connect() first` en `email_msg.send()`, antes de devolver HTTP 500. Revisar
primero que `EMAIL_HOST` exista y no esté vacío: `smtplib.SMTP` no conecta al
construirse con un host vacío. El log por sí solo no prueba qué variables están
configuradas en Render ni que el bloqueo de puertos haya causado esa excepción.

Antes de este cambio, los datos quedaban guardados aunque fallara la invitación.
Revisar los empleados creados durante el incidente; el cambio no elimina esos
registros ni les reenvía el correo automáticamente. Tras reparar el correo, se
puede usar el flujo de recuperación de contraseña para las cuentas existentes.

## Verificación local

Con PostgreSQL local disponible:

```bash
cd backend
source venv/bin/activate
DEBUG=False python manage.py test apps.authentication.tests_invitacion_empleado --noinput
```

Las pruebas simulan desconexión, timeout y envío no aceptado; verifican rollback,
reintento correcto con el mismo correo y alta sin acceso independiente del SMTP.
No envían correos reales.

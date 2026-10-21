# Historial de acciones

`GET /api/auditoria/` devuelve un listado paginado de operaciones exitosas de
la API en la empresa de la sesión. Requiere el permiso `auditoria.ver`.
Los administradores lo reciben inicialmente; se puede conceder a otros roles
desde Roles. El permiso permite consultar la bitácora de toda la empresa,
incluidos los eventos sin taller y con independencia del taller seleccionado.
No concede acceso a registros de otras empresas.

Filtros: `search` (nombre del usuario o UUID del registro), `modulo`, `accion`,
`desde` y `hasta` (AAAA-MM-DD, ambas fechas inclusive, zona horaria del negocio).
Orden: fecha descendente y clave interna descendente para desempatar.
Los identificadores expuestos son UUID públicos.

Cada evento conserva fecha, usuario y nombre al momento de la acción, módulo,
acción de la vista, UUID del registro cuando está disponible, método HTTP y
taller validado si la solicitud lo seleccionó explícitamente. Una operación
masiva genera un evento de la operación, no uno por cada fila procesada.

El middleware registra POST, PUT, PATCH y DELETE exitosos de usuarios
autenticados con empresa válida. También registra el inicio de sesión que
entra directamente a una empresa y la selección de empresa tras un login
multiempresa. La elección pendiente de empresa, las solicitudes rechazadas,
la renovación de tokens, las consultas GET y las prevalidaciones de
importación (`?validar=1`) no generan eventos.

La operación y el evento se confirman en la misma transacción: si falla la
escritura del historial, se revierte la operación. No se almacenan cuerpos,
contraseñas, tokens, cookies ni cabeceras de autenticación. La API del historial
solo admite lectura. Los nombres históricos se conservan si se elimina el
usuario o el taller. Esto no es un mecanismo de detección de manipulación por
un administrador de la base de datos.

## Despliegue y alcance

Ejecutar `python manage.py migrate` antes de desplegar los procesos con el nuevo
middleware. Las migraciones crean la tabla y agregan el permiso sin reemplazar
los permisos ya configurados. En una base nueva se usa la semilla del sistema.

La interfaz está en `/configuracion/auditoria/`, accesible desde Auditoría en
el menú lateral. No ofrece edición ni eliminación de eventos.

El registro comienza al desplegar el módulo. No reconstruye acciones antiguas,
no guarda los valores anteriores y nuevos de cada campo y no captura cambios
por SQL directo, Django admin, comandos ni tareas en segundo plano. Los eventos
describen solicitudes exitosas; una petición idempotente repetida puede generar
otro evento aunque reutilice un registro existente. La política de retención
debe definirse antes de implantar limpieza periódica; el módulo no elimina
eventos automáticamente.

Validación: `python manage.py test apps.core.tests_auditoria`. Cubre operaciones,
rollback, JWT real, permisos delegados y revocados, aislamiento por empresa,
cabeceras de taller, fechas y paginación.

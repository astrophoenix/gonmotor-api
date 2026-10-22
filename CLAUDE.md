# CLAUDE.md — gonmotor-api

## 1. Propósito

API REST multi-tenant del SaaS **GonMotor**, un sistema de gestión para talleres mecánicos. Una **Empresa** (tenant) tiene uno o varios **Talleres** (sucursales; en el código y en la BD el campo suele llamarse `sucursal`). La API cubre todo el flujo operativo del taller:

**Cita → Recepción del vehículo → Inspección → Cotización → Orden de trabajo**

Además gestiona clientes, vehículos, proveedores, inventario (repuestos y servicios), gastos, reportes de rentabilidad, usuarios/roles/permisos, recordatorios de mantenimiento por WhatsApp y mejora de texto con IA. El cliente principal es el frontend `../gonmotor-ui/` (repo aparte).

Despliegue: Render (`render.yaml`, `rootDir: backend`, gunicorn, health check `GET /api/core/health/`).

## 2. Mapa del repositorio

```
gonmotor-api/
├── render.yaml                 # Servicio web en Render (build: pip + migrate + collectstatic)
├── comandos                    # Notas personales de comandos (contiene credenciales locales: no copiar)
└── backend/                    # rootDir del proyecto Django
    ├── manage.py               # DJANGO_SETTINGS_MODULE por defecto: config.settings.local
    ├── requirements.txt
    ├── .env                    # OBLIGATORIO (SECRET_KEY, DB_*); no versionado
    ├── venv/                   # entorno virtual local
    ├── logs/whatsapp_mock.log  # salida del modo mock de WhatsApp
    ├── media/                  # uploads (fotos de recepción/inspección/orden, logos, repuestos)
    ├── templates/  static/
    ├── config/
    │   ├── settings/{base,local,production}.py
    │   ├── urls.py             # enrutado raíz de todas las apps
    │   └── wsgi.py / asgi.py
    └── apps/
        ├── core/               # BaseModel, mixins, health, dashboard, búsqueda global, IA de texto, exportes
        ├── authentication/     # login JWT, perfil, usuarios, empleados, roles y permisos, contexto tenant
        ├── empresas/           # Empresa (tenant) y Taller (sucursal)
        ├── configuracion/      # pantallas de config: empresa y sucursales (sin modelos propios)
        ├── clientes/           # Cliente + importación/exportación Excel
        ├── vehiculos/          # Vehiculo + VehiculoPropietario (historial de dueños)
        ├── proveedores/
        ├── citas/              # Cita + export .ics compartible
        ├── ordenes/            # RecepcionVehiculo, InspeccionVehiculo, OrdenTrabajo, detalles y fotos
        ├── cotizaciones/       # Cotizacion + detalles de servicio/repuesto + PDF
        ├── inventario/         # Repuesto, RepuestoImagen, Servicio
        ├── gastos/             # Gasto
        ├── pagos/              # PagoOrden: pagos, abonos y devoluciones de la OT (recurso `facturacion`)
        ├── reportes/           # rentabilidad por taller / empresa (sin modelos)
        └── notificaciones/     # recordatorios WhatsApp de mantenimiento
```

### Piezas transversales clave

| Archivo | Rol |
|---|---|
| `apps/core/models.py` | `BaseModel` (auditoría: `is_active`, `created_at`, `updated_at`; catálogos y acceso) y `SoftDeleteModel` → `BaseEmpresaModel` → `BaseTallerModel` (borrado lógico con `deleted_at`, managers `objects`/`all_objects`, QuerySet `para_request`/`de_empresa`/`de_talleres`). |
| `apps/core/mixins.py` | `FiltroTallerMixin` (recorte por taller activo), `SoftDeleteDestroyMixin` (DELETE siempre lógico) y `ValidarTenantMixin` (serializers: FKs cliente/vehículo/taller del payload deben ser del tenant), `FiltroDocumentoPadreMixin` + `ValidarDocumentoPadreMixin` (líneas y fotos de un documento: mismo alcance empresa/taller que el documento, al leer y al escribir). `ValidarUsuariosEmpresaMixin` (`campos_usuario`: asesor/responsable/técnico asignados deben pertenecer a la empresa activa). Regresión de aislamiento: `apps/core/tests_auditoria_tenant.py`. |
| `apps/authentication/utils.py` | **Fuente única del contexto tenant**: empresa, rol y taller de la petición. |
| `apps/authentication/permissions.py` | `TienePermiso(recurso, accion)`, `TieneRecurso(recurso)`, `EsAdminDeEmpresa`. |
| `apps/authentication/permisos_repo.py` | `MATRIZ_ROLES` (semilla), jerarquía de roles, lectura/escritura de permisos en BD. |
| `apps/ordenes/relaciones_flujo.py` | Grafo de vínculos entre cita, recepción, inspección, cotización y orden (FKs existentes), compartido por los cuatro editables. |
| `apps/core/services/text_improver.py` | Mejora de texto vía LLM compatible con OpenAI (por defecto Groq). Descubre el modelo disponible si `AI_TEXT_MODEL` está vacío. |
| `apps/core/utils/{pdf_export,excel_export}.py` | Exportación PDF (reportlab) y Excel (openpyxl). |
| `apps/core/utils/images.py` + `apps/core/archivos.py` | Servicio único de imágenes (ver «Imágenes») y borrado de archivos reemplazados/borrados. |
| `apps/notificaciones/services/` | `whatsapp.py` (proveedores mock/Twilio/Meta), `correo.py` (backend de correo de Django), `despacho.py` (elige canal con respaldo), `cola.py` (envía `NotificacionProgramada` vencidas con reintentos), `mantenimiento.py` (próximo mantenimiento al entregar la OT), `avisos.py` (retiro y recordatorios de cita), `campanas.py` (audiencia y lanzamiento), `recordatorios.py`, `mensajes.py`, `telefonos.py`. Disparadores automáticos en `notificaciones/signals.py`. |

### Rutas (`config/urls.py`)

| Prefijo | App |
|---|---|
| `api/core/` | health, `limites-imagenes/`, `mejorar-texto/`, `buscar/`, `dashboard/`, `dashboard-v2/` |
| `api/auth/` | `login/`, `select-company/`, `register/`, `refresh/`, `me/`, `talleres/`, password reset/change, `roles/`, `recursos/`, `usuarios/`, `empleados/` |
| `api/token/refresh/` | refresco JWT (alias) |
| `api/clientes/`, `api/proveedores/`, `api/vehiculos/`, `api/empresas/`, `api/citas/`, `api/gastos/` | CRUD de cada app |
| `api/cotizaciones/` | cotizaciones + `servicios/` + `repuestos/` |
| `api/ordenes/` | `ordenes-trabajo/`, `inspecciones/`, `inspeccion-*`, `orden-fotos/`, `ot-servicios/`, `ot-repuestos/` |
| `api/recepciones/` | **vive en `apps.ordenes`** (`apps/ordenes/recepciones_urls.py`) |
| `api/repuestos/`, `api/repuestos-imagenes/`, `api/servicios/` | `apps.inventario.urls` montado directamente en `api/` |
| `api/reportes/` | `rentabilidad-taller/`, `rentabilidad-empresa/` |
| `api/pagos/` | pagos y devoluciones de órdenes (`?orden=`, `?cliente=`, alta, `{id}/anular/`; sin edición ni borrado), `{id}/recibo/` (PDF), `{id}/enviar-recibo/`, `cuentas-por-cobrar/` y `cuentas-por-cobrar/{orden}/recordar/`. Comprobante externo: `api/ordenes/ordenes-trabajo/{id}/factura/` |
| `api/notificaciones/` | estado, preferencias, recordatorios (listar/enviar por vehículo/probar), `programadas/` (cola: listar, alta manual de mantenimiento, editar, `cancelar/`, `enviar-ahora/`), `campanas/` (CRUD en borrador, `audiencia/`, `lanzar/`, `cancelar/`), `cron/procesar/` (cola, con `X-Cron-Token`). Aviso de retiro: `api/ordenes/ordenes-trabajo/{id}/notificar-retiro/`; aviso manual de cita: `api/citas/{id}/notificar/` |
| `api/configuracion/` | `empresa/`, `sucursales/` |

La mayoría de los módulos de listado exponen además `exportar-pdf/` y `export-excel/`.

## 3. Tecnologías y dependencias

- **Python / Django 6.1** + **Django REST Framework 3.18**
- **djangorestframework-simplejwt** (access 60 min, refresh 30 días, rotación + blacklist)
- **PostgreSQL** vía `psycopg2-binary` (también en local; el `db.sqlite3` del directorio es residual)
- `python-decouple` (config por `.env`), `django-cors-headers`, `django-countries`
- `reportlab` (PDF), `openpyxl` (Excel), `Pillow` + `pillow-heif` (imágenes, HEIC), `requests` (LLM, WhatsApp)
- Producción: `gunicorn` (2 workers × 2 hilos: una subida lenta o un PDF no bloquea al resto), `whitenoise` (estáticos comprimidos), SMTP para correo
- Idioma `es-ec`, zona horaria `America/Guayaquil`

## 4. Reglas de arquitectura y flujos

### Configuración y entorno
- Activar el venv antes de cualquier comando: `cd backend && source venv/bin/activate`.
- `backend/.env` es obligatorio: `SECRET_KEY` y `DB_NAME/USER/PASSWORD/HOST/PORT` no tienen valor por defecto. Opcionales: `DB_SSLMODE`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`, `WHATSAPP_*`, `AI_TEXT_*`, `THROTTLE_*`, `EXPORT_MAX_FILAS_PDF` (default 5000) y `EXPORT_MAX_FILAS_EXCEL` (default 20000): máximo de filas de los exportes de listados; por encima responden 400 pidiendo filtrar (`apps/core/utils/export_limits.py`). `IMAGEN_MAX_MB` (default 15) e `IMAGEN_MAX_MEGAPIXELES` (default 60): límites de las imágenes subidas.
- Los tests escriben los archivos subidos en un `MEDIA_ROOT` temporal (`base.py` lo cambia al correr `manage.py test`).
- `local.py`: `DEBUG=True`, `ALLOWED_HOSTS=['*']`, email por consola. `production.py`: HTTPS/HSTS, whitenoise, SMTP, logging a consola.
- CORS permite la cabecera `x-taller-id` (la empresa viaja en el JWT, no hay cabecera de empresa); si se añade una cabecera nueva desde el frontend hay que agregarla a `CORS_ALLOW_HEADERS`.

### Multi-tenant (empresa → taller)
Todo el filtrado por tenant pasa por `apps/authentication/utils.py`; **no reimplementarlo en otro archivo**.

1. **Empresa activa**: sale **solo** de la claim `empresa_id` del JWT (UUID; un entero de tokens previos se acepta porque va firmado). No hay cabecera de empresa: cambiar de empresa exige un token nuevo (`select-company`). En tests: `apps/core/testing.fijar_empresa(client, empresa)` tras `force_authenticate`. Se valida contra las relaciones activas `UsuarioEmpresa` del usuario. Si pide una empresa a la que no pertenece, el contexto es vacío (sin fallback). Con una sola empresa asignada entra directo. El superusuario puede usar cualquier empresa activa y actúa como `ADMIN_SISTEMA`.
2. **Rol**: se lee SIEMPRE de la BD (`UsuarioEmpresa.rol`), nunca de la claim `rol` del token (que es solo informativa).
3. **Taller activo**: precedencia cabecera `X-Taller-ID` → `UserProfile.taller_activo` → primer taller permitido, siempre validado contra la empresa y el alcance del rol.
4. **Alcance de talleres**: `ADMIN_SISTEMA` y `ADMIN_EMPRESA` (`ROLES_TODOS_TALLERES`) ven todos los talleres activos y pueden operar en «Todos» (sin cabecera = sin recorte). El resto solo ve los asignados en `UsuarioEmpresa.talleres` (vacío = ningún dato) y nunca queda sin recorte.
5. El resultado se cachea por petición (`request._contexto_gonmotor`).

Funciones a usar: `get_empresa_id_desde_request`, `get_rol_desde_request`, `get_contexto_desde_request`, `get_taller_desde_request`, `talleres_filtro_de_request`, `talleres_permitidos`, `validar_talleres_asignables` (antes de cualquier `M2M.set()` de talleres).

### Patrón de un ViewSet
```python
class XViewSet(SoftDeleteDestroyMixin, FiltroTallerMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('<recurso>')]
    taller_filter_field = 'sucursal'          # solo si el modelo cuelga de un taller

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return X.objects.none()
        qs = X.objects.filter(empresa_id=empresa_id)
        return self.filtrar_por_taller(qs)
```
Equivalente para modelos `Base*Model`: `X.objects.para_request(request, explicito=...)` (empresa + talleres validados, sin eliminados; ver `OrdenTrabajoViewSet`).
- Sin empresa en contexto → `queryset.none()`; nunca devolver datos sin filtrar.
- **Reglas automáticas de `ModelSerializerPublico`** (base obligatoria): `empresa` es siempre de solo lectura, y toda FK escrita hacia un modelo con `empresa_lookup` (cliente, documentos del flujo, repuestos…) debe estar en `para_request` del usuario (empresa + talleres permitidos); corre después de las validaciones propias del serializer como red de seguridad. `apps/core/tests_guardian_serializers.py` falla si un serializer no hereda de esa base o deja `empresa` escribible.
- **Acciones que crean o enlazan otro documento** exigen también `modificar` sobre el recurso de ese documento (`TienePermiso`): `citas/convertir_a_recepcion` (recepciones), `recepciones/crear-inspeccion` (inspecciones), `cotizaciones/generar_orden` (ordenes) y `relaciones` (recurso del documento enlazado, buscado con `para_request`). Regresión: `apps/ordenes/tests_auditoria_recepciones.py` y `tests_auditoria_vinculos.py`.
- Al crear, `empresa` (y el taller activo) se asignan desde el contexto del request, normalmente en el `create()` del serializer, con `empresa` en `read_only_fields`; nunca desde el payload.
- `FiltroTallerMixin` lo usan hoy los viewsets del flujo: citas, recepciones, inspecciones, cotizaciones y órdenes.
- **Excepción deliberada**: `GET /api/vehiculos/{id}/historial/` (`apps/vehiculos/historial.py`) lee los documentos del flujo de **todos** los talleres de la empresa, sin recorte por taller ni por `X-Taller-ID` (decisión de producto: el técnico debe ver qué se hizo en otra sede). Cada documento lleva `accesible=False` si su taller está fuera del alcance del usuario; su ficha sigue protegida por taller. No copiar este patrón a otros endpoints.
- Defaults DRF: `IsAuthenticated`, `PageNumberPagination` con `PAGE_SIZE=10`, throttling `300/min` usuario y `60/min` anónimo.
- Mensajes de error y respuestas en español.

### ID público (UUIDv7)
- El `id` entero es solo interno (FKs, joins). Los modelos con `UuidPublicoModel` (`apps/core/ids_publicos.py`) exponen su `uuid` como único identificador en la API. Aplicado a todos los modelos expuestos: clientes, vehículos, citas, documentos del flujo (con líneas y fotos), proveedores, inventario, gastos, talleres, empresas, roles, empleados (`UsuarioEmpresa`), usuarios y notificaciones. Única excepción: el catálogo global `Recurso` (igual para todos los tenants).
- **Empresa**: el `empresa_id` de `select-company` y la claim `empresa_id` de los JWT llevan el UUID (`_empresa_pk`); la claim es la única fuente de la empresa de la sesión.
- **Taller**: la cabecera `X-Taller-ID` y los filtros explícitos (`?sucursal=`, `?taller_id=`…) llevan el UUID; `authentication/utils._taller_pk` lo convierte a pk (caché por petición) y el filtrado interno sigue en pks. Un taller declarado que no se resuelve no devuelve datos (no amplía el alcance).
- **Usuario**: el `User` de Django no tiene columna propia; su ID público es `UserProfile.uuid` (todo usuario tiene perfil: señal `post_save` + migración `0014_perfiles_faltantes`). `id_publico(user)` / `uuid_de_usuario(pk, request)` lo resuelven con caché por petición; los lookups usan `campo_uuid(modelo)` (`profile__uuid`).
- Todos los serializers heredan `ModelSerializerPublico` (`apps/core/serializers.py`): `id` sale del `uuid` y toda FK automática se lee/escribe por UUID si el modelo relacionado lo tiene (si no, por pk, igual que DRF). Nuevos serializers: heredar de esa clase, nunca de `serializers.ModelSerializer`.
- Para pasar un modelo a UUID: heredar `UuidPublicoModel`, migración en tres pasos (columna nullable → `rellenar_uuid` → única; ver `clientes/migrations/0007_cliente_uuid.py`), `LookupPublicoMixin` en su ViewSet, filtros por query param con `filtrar_por_id_publico`, y `select_related` de las FKs que lo serializan.
- Serializar una FK por UUID no exige traer el objeto: `RelacionPublicaField` usa el objeto si ya está precargado y, si no, resuelve el UUID por pk con caché por petición (`uuid_por_pk`). Aun así, todo lo que un serializer lee del relacionado (nombre, placa, perfil…) debe venir con `select_related`/`Prefetch`.
- Listados con documentos anidados (recepción dentro de la orden, inspección dentro de la recepción): usar `_recepciones_con_relaciones()` / `_inspecciones_con_relaciones()` de `apps/ordenes/views.py`. Nada de `.filter()`/`.order_by()`/`.last()` sobre relaciones precargadas en `to_representation` (ignoran la precarga): resolver en memoria sobre `.all()`. Rol de un usuario en la empresa: `rol_de_usuario_en_empresa(user_id, empresa_id, request)` (2 consultas por petición). Regresión: `apps/ordenes/tests_consultas_listados.py` exige que las consultas no crezcan con las filas.
- Respuestas armadas a mano: `id_publico(obj)`, nunca `obj.id`/`obj.pk` (tampoco dentro de textos o URLs: `referencia_corta(obj)` para un `#ABC123` legible). Entradas fuera de serializers (query params, cuerpos de acciones): `por_id_publico(qs, valor)`. Tests: enviar `str(obj.uuid)`.

### Imágenes
- **Toda imagen subida pasa por `procesar_imagen(archivo, perfil)`** (`apps/core/utils/images.py`): verifica que sea una imagen real (JPG, PNG, WebP, HEIC), aplica los límites de bytes y megapíxeles, endereza según el EXIF, borra los metadatos (GPS, modelo del teléfono), limita el lado mayor y guarda con nombre UUID. Perfiles: `evidencia` 1920 px (fotos de recepción, inspección y orden), `vehiculo` 1600, `repuesto` 1200, `avatar` 512, `logo` 1024 sin pérdida (conserva transparencia), `firma` PNG ≤ 300 KB. Fotos en WebP calidad 80; las de evidencia, vehículo y repuesto generan `miniatura` (400/300 px).
- En serializers: `ImagenProcesadaField(perfil=..., campo_miniatura='miniatura')` (`apps/core/serializers.py`); `ModelSerializerPublico` guarda la miniatura y, al leer, la sustituye por la imagen si falta. Firma: `FirmaField` (archivo o data URL del canvas). Fuera de serializers, llamar a `procesar_imagen` directamente (fotos de recepción, acción `vehiculos/{id}/imagen/`). Nunca asignar un archivo subido a un `ImageField` sin pasar por el servicio.
- `GET /api/core/limites-imagenes/` publica perfiles y límites para que la web y la app compriman antes de subir; los límites viven en `settings`.
- Altas de fotos reintentables: `CreacionIdempotenteMixin` (`apps/core/mixins.py`) acepta un `id` (UUID) generado por el cliente; si ya existe responde 200 con la misma foto.
- Archivos: `apps/core/archivos.py` borra el archivo reemplazado o el de una fila borrada físicamente **al confirmar la transacción** (señales para todo modelo con `FileField`). El borrado lógico conserva los archivos. Restos antiguos: `python manage.py limpiar_media_huerfana [--aplicar]`.
- Tests: `apps/core/tests_imagenes.py`; helpers `imagen_de_prueba()` y `firma_data_url()` en `apps/core/testing.py`.

### Roles y permisos
- Un permiso es `(recurso, acción)` con acciones `ver` / `modificar`. Los recursos están en `permisos_repo.RECURSOS` (empresa, talleres, usuarios, empleados, clientes, vehiculos, proveedores, citas, recepciones, inspecciones, cotizaciones, ordenes, inventario, facturacion, reportes, gastos, notificaciones). Configurar WhatsApp (preferencias, envío masivo, prueba) exige además `empresa`/`modificar`.
- Roles de sistema: `ADMIN_SISTEMA`, `ADMIN_EMPRESA`, `ADMIN_TALLER`, `ASESOR`, `MECANICO`, `CAJERO`. Los roles personalizados son posibles y pueden tener el flag `ver_todos_talleres`.
- En tiempo de ejecución la fuente de verdad son las tablas `Rol`/`Recurso`/`Permiso`/`PermisoEspecial`, con overrides por empresa (`PermisoEmpresa`, `PermisoEspecialEmpresa`). `MATRIZ_ROLES` es la semilla: se siembra con una señal `post_migrate` (`authentication/signals.py`) o con `python manage.py seed_permisos`.
- **El flujo del taller se consulta como bloque**: `citas`, `recepciones`, `inspecciones`, `cotizaciones` y `ordenes` tienen `ver` todos o ninguno (sus documentos se serializan anidados). `validar_bloque_flujo` lo exige al guardar un rol personalizado o un override de empresa (sobre el resultado efectivo); `modificar` es independiente por documento. La pantalla de roles sincroniza «Ver» en los cinco.
- **Acciones especiales** (`AccionEspecial`, concedidas por rol y ajustables por empresa): catálogo en `permisos_repo.ACCIONES_CATALOGO` y semilla en `MATRIZ_ACCIONES` (para bases existentes, migración de datos como `0017_acciones_dashboard`). Hoy: `dashboard_tendencia_financiera`, `dashboard_facturacion`, `dashboard_cotizaciones_por_aprobar` y `historial_{recepciones,inspecciones,cotizaciones,ordenes}` (solo roles de administración). Lectura en vistas: `acciones_en_request(request, rol, empresa_id)` o la clase `TieneAccion(codigo)`. `GET /api/auth/me/` las publica en `acciones`.
- **Panel de inicio** (`GET /api/core/dashboard-v2/`, el que usa la web): no es un recurso; cualquier usuario de la empresa entra. `secciones_dashboard()` decide cada bloque (los tres de arriba por acción especial, el resto por `ver` de su recurso) y `recortar_dashboard()` quita de la respuesta lo no permitido; la respuesta trae `secciones` para que la UI acomode la grilla. `DashboardView` (v1) no lo usa la web y sigue exigiendo `reportes`.
- Jerarquía `NIVELES_ROLES`: un usuario solo otorga roles de nivel igual o inferior al suyo (`puede_otorgar`).
- **Sin escalada al editar roles** (`_escalada_de_permisos` en `authentication/views.py`): nadie cambia los permisos/acciones de su propio rol, y solo se concede lo que el editor ya tiene (comparado con lo que el rol ya tenía). Superusuario exento. Regresión: `EscalacionPermisosRolTests`.
- Si añades un recurso nuevo: agrégalo a `RECURSOS`, a `MATRIZ_ROLES`, a `NOMBRES_RECURSOS` y protege el viewset con `TieneRecurso`. La semilla solo corre sobre una base vacía: para las bases existentes añade una migración de datos idempotente (ver `authentication/migrations/0013_recursos_gastos_notificaciones.py`).

### Borrado lógico híbrido (`is_active` + `deleted_at`)
| Estado | `is_active` | `deleted_at` | Visible | Bloquea duplicados |
|---|---|---|---|---|
| Operativo | True | NULL | sí | sí |
| Suspendido | False | NULL | con `?estado=inactivo` | sí |
| Eliminado | False | timestamp | no | no |

- Bases: `SoftDeleteModel` (global: `Empresa`, `Vehiculo`), `BaseEmpresaModel` (`Cliente`, `Proveedor`, `Repuesto`, `Servicio`, `Taller`, `VehiculoEmpresa`), `BaseTallerModel` (`OrdenTrabajo`, `RecepcionVehiculo`, `InspeccionVehiculo`, `Cotizacion`; `Cita` y `Gasto` con `taller_field='taller'`). Catálogos de roles/permisos, perfiles y líneas de detalle siguen en `BaseModel` (borrado físico).
- `objects` oculta eliminados y conserva suspendidos; `all_objects` lo incluye todo (numeración de documentos, admin). `delete()` de instancia o queryset es lógico; el físico solo con `hard_delete()`.
- Documentos con cliente de por medio: solo se eliminan la **cotización** y la **orden** en estado `PENDIENTE` (`destroy` responde 400 en otro estado); el resto se conserva como historial.
- `SoftDeleteDestroyMixin`: DELETE siempre lógico. Responde 200 con `{status, action: 'soft_delete', entity, identifier, message}`.
- Uniques de negocio: parciales con `condition=Q(deleted_at__isnull=True)`. Los números de documento (`numero_orden`, etc.) mantienen la unique completa: nunca se reutilizan.
- No hay cascada lógica entre agregados: los documentos conservan su cliente/vehículo eliminado (el acceso por FK no filtra). Para cascada dentro de un agregado: `soft_delete_cascade`; efectos laterales: señal `core.models.post_soft_delete`.
- **Vehículo**: es global (placa única); cada empresa activa/suspende/elimina su vínculo `VehiculoEmpresa`. **El dueño actual es por empresa** (cada cliente es de una sola): leerlo con `propietario_actual(vehiculo, empresa_id)` / `propietarios_actuales_en(empresa_id)` y asignarlo con `asignar_propietario_actual(vehiculo, cliente)`, que solo cierra al anterior de la misma empresa. Nunca `propietarios.filter(es_actual=True)` sin empresa: mostraría o usaría (p. ej. en recordatorios de WhatsApp) el cliente de otra empresa. Regresión: `apps/vehiculos/tests_propietario_empresa.py`. Usar `Vehiculo.objects.de_empresa/operativos_en/por_estado_en(empresa_id)` y `vincular_empresa()`; nunca `filter(empresas=...)` ni `empresas.add/set`.
- Los listados suelen aceptar `?estado=activo|inactivo`; los selectores de búsqueda envían `estado=activo`.

### Flujo del taller
- Recepción, inspección, cotización y orden están enlazadas por FKs; `relaciones_flujo.py` centraliza su lectura y edición. Cualquier cambio de vínculos entre estas entidades va ahí, no en cada serializer.
- **Kilometraje de la orden**: `OrdenTrabajo.kilometraje` es obligatorio en la API (no menor al `kilometraje_actual` del vehículo, salvo conservar la lectura guardada); si es mayor, sube el odómetro del vehículo. Al generar la orden desde una cotización se toma con `OrdenTrabajo.kilometraje_de_visita()` (lectura más alta de la recepción/inspección, o la del vehículo).
- **Historial del vehículo**: agrupa en memoria los documentos enlazados en visitas (union-find sobre las mismas FKs) en un número fijo de consultas; regresión en `apps/vehiculos/tests_historial.py`.
- `cotizaciones/signals.py` recalcula los totales de la cotización al guardar/borrar sus detalles de servicio o repuesto; `recepciones` tiene la acción `firmar` (firma del cliente → estado `ACEPTADA`). La firma es un archivo PNG (`firma_cliente`, `FirmaField`): se envía como archivo o como data URL y la API devuelve su URL.
- Los documentos sin taller se rellenaron con un taller por defecto mediante migraciones de backfill; el filtro por taller es estricto (los registros con `sucursal` NULL quedan fuera).

### Pagos de la orden (`apps/pagos/`)
- `PagoOrden` cuelga de la OT (`FiltroDocumentoPadreMixin`: mismo alcance empresa/taller) y se protege con `TieneRecurso('facturacion')`. Tipos `PAGO` y `DEVOLUCION`; la `fecha` la fija el servidor (el cierre de caja cuenta los movimientos del día) y `es_anticipo` marca los pagos recibidos antes de `COMPLETADO`. Un movimiento no se edita ni se borra: se anula con motivo (`POST {id}/anular/`).
- **No hay saldos a favor** (`services.py`): un pago no supera el saldo pendiente; una devolución no supera lo pagado neto; anular una devolución no puede dejar lo pagado por encima del total. `OrdenTrabajo.calcular_totales()` llama a `validar_total_cubre_pagos`: un cambio de líneas que deje el total por debajo de lo pagado responde 400 pidiendo registrar antes la devolución. Para anular la OT (`CANCELADO`) lo pagado neto debe ser 0, y una OT con pagos (aunque anulados) no se elimina.
- La OT serializada trae `total_pagado`, `total_devuelto`, `pagado_neto`, `saldo`, `estado_pago` (`PENDIENTE`/`PARCIAL`/`PAGADO`) y `estado_pago_display`, calculados en memoria sobre `pagos` precargado (`resumen_pagos`). Los ve quien vea la orden; la lista de movimientos exige `facturacion`/`ver`.
- Una OT anulada tiene `estado_pago` `SIN_COBRO` y saldo 0. El listado filtra con `?estado_pago=PENDIENTE|PARCIAL|PAGADO|SIN_COBRO` (`filtrar_por_estado_pago`, subconsultas sobre los pagos vigentes).
- **Entrega a crédito**: pasar la OT a `ENTREGADO` con saldo pendiente responde 400 (`entrega_a_credito` + `saldo`) salvo que llegue `entrega_a_credito: true`; entonces se guardan `saldo_al_entregar` y `entrega_credito_autorizada_por` (el usuario de la petición), y el saldo queda en el historial.
- **Cuentas por cobrar** (`CuentasPorCobrarView`, `facturacion`/`ver`): solo OT `ENTREGADO` con saldo > 0 (`services.cuentas_por_cobrar`); una OT en proceso con anticipo no es deuda. Filtros `search`, `cliente`, `sucursal`; orden por defecto `fecha_entrega` (la más antigua primero). La respuesta paginada trae `resumen` (`total_por_cobrar`, `ordenes`, `clientes`, `por_taller`). `recordar/` (`facturacion`/`modificar`) envía en el acto una `NotificacionProgramada` tipo `SALDO` por la cola, con respaldo de canal como el aviso de retiro; la fila trae `ultimo_recordatorio`.
- **Recibo**: `GET {id}/recibo/` genera el PDF del movimiento (`pdf_recibo.py`, misma cabecera e identidad que el PDF de la orden; marca «ANULADO» si aplica). Pagado neto y saldo son los de la orden justo después del movimiento (`estado_tras_movimiento`, sobre el total actual). `POST {id}/enviar-recibo/` (`facturacion`/`modificar`) confirma el movimiento al cliente con una `NotificacionProgramada` tipo `RECIBO`: **texto sin adjunto**, por WhatsApp o correo con el respaldo de la cola (el PDF se descarga aparte); un movimiento anulado no se envía.
- **Comprobante externo** (factura del facturador del SRI o nota de venta de talonario): `tipo_comprobante`, `numero_factura` (`^\d{3}-\d{3}-\d{9}$`) y `fecha_factura` en la OT, solo lectura en el serializer. Se escriben únicamente con `POST ordenes-trabajo/{id}/factura/` (`ordenes`/`ver` + `facturacion`/`modificar`; número vacío lo quita) y quedan en el historial. Único por empresa + tipo entre OT no eliminadas (`ot_comprobante_unico_vivo`; factura y nota de venta llevan secuencias distintas). El listado filtra «cobradas sin factura» con `?sin_factura=1` (`filtrar_sin_factura`: algún pago vigente, sin número, no anulada).
- Cada pago, devolución y anulación deja un evento en el historial de la OT (`Evento.PAGO`, `DEVOLUCION`, `PAGO_ANULADO`). Tests: `apps/pagos/tests.py` y `tests_factura.py`.

### Historial de cambios de documentos
- `apps/core/historial_documentos.py` + modelo `EventoDocumento`: valor anterior → nuevo de los campos sensibles declarados en `CAMPOS`, solo cuando cambian (comparados ya formateados; FKs por id). Complementa `RegistroAuditoria` (una fila por petición, sin valores), no lo reemplaza.
- Las líneas registran en su documento padre (`registrar_linea`); operaciones masivas como `sincronizar_inspeccion` o `aplicar_cotizaciones` dejan **un** evento resumido, nunca bajas y altas por línea.
- `X-Operacion-ID` (UUID, en `CORS_ALLOW_HEADERS`) agrupa los eventos de un mismo guardado; sin cabecera, uno por petición. El frontend lo envía con `enOperacion()`.
- Se escribe en la transacción de la operación (si falla, la operación también). **El registro nunca se apaga**; `HistorialDocumentoMixin` expone `GET <doc>/{id}/historial/` con `ver` del recurso + acción `historial_<recurso>`.
- Cubre los cuatro documentos (cabecera y líneas), la firma de la recepción y las cascadas que cambian otro documento (cotización desde inspección → `FINALIZADA`, generar OT → vincula recepción/inspección, cita → recepción). Los vínculos editados por `relaciones/` quedan en los dos documentos (`relaciones_flujo._registrar_vinculo`); de las fotos solo se registran reemplazos y bajas (`registrar_foto`), no las altas. Regresión: `apps/core/tests_historial_documentos.py`.

### Importación masiva (Excel)
- **Núcleo reutilizable** `apps/core/importacion.py`: un módulo define un `Importador` (lista de `Columna` con alias y `opciones` para listas desplegables, `procesar(filas)` y `resumen_fila`) y expone las rutas con las vistas genéricas `ImportarView`, `ErroresImportacionView` y `PlantillaImportacionView` (solo `permission_classes`, `importador_class` y `nombre_archivo`). El núcleo aporta lectura por encabezados, límites `IMPORTACION_MAX_FILAS` (2000) e `IMPORTACION_MAX_MB` (5), vista previa con rollback, contrato de respuesta, motivos seguros (`FilaInvalida`; lo demás va al log), plantilla con hoja de instrucciones y reporte de errores sin fórmulas. Referencia: `apps/clientes/excel_import.py` (`ImportadorClientes`). Tests del núcleo: `apps/core/tests_importacion.py`. Registros con clave única por empresa: `ImportadorPorClave` del núcleo (`campo_clave` en mayúsculas define el duplicado, clave repetida dentro del archivo = error, `tipos` decimal/entero/email/choices o propio con `convertir`); lo usan `ImportadorProveedores` (`apps/proveedores/excel_import.py`, `/api/proveedores/importar/`) y los catálogos de inventario (`apps/inventario/excel_import.py`): `ImportadorCatalogo` (código en mayúsculas define el duplicado, código repetido dentro del archivo = error) con `ImportadorRepuestos` y `ImportadorServicios` en `/api/repuestos/importar/` y `/api/servicios/importar/` (rutas antes del router). **El stock de un repuesto solo se toma al crearlo**: en uno existente no se pisa y la fila lleva un aviso (`estado` `actualiza` + `motivo`, que la web muestra en ámbar). IVA admitido: 15 o 0. Tests: `apps/inventario/tests_importacion.py`.
- `POST /api/clientes/importar/` recibe `archivo` (.xlsx de la plantilla `importar/plantilla/`). Con `?validar=1` ejecuta la misma lógica dentro de una transacción revertida: no guarda nada y devuelve `filas` con el estado de cada una (`nuevo` / `actualiza` / `error` + `motivo`) para la vista previa de la web. Ambas respuestas traen `total`, `exitosos`, `nuevos`, `actualizados`, `vehiculos` y `errores`; si faltan columnas responde 400 con `columnas_faltantes`. Cada escritura va en su savepoint para que un fallo no rompa la transacción. Un importador nuevo para otro módulo debe seguir este contrato (lo consume `ImportWizardModal` del frontend).
- **Reglas de integridad** (las mismas de la pantalla): una celda vacía nunca borra un dato guardado (solo se actualiza lo que trae valor); un cliente o vehículo desactivado no se reactiva (la fila sale con su motivo); el kilometraje no retrocede; el dueño anterior del vehículo se cierra (`vehiculos.models.asignar_propietario_actual`); las filas con vehículo exigen `vehiculos/modificar` además de `clientes`; los motivos de error son para el usuario (un error inesperado va al log, no a la respuesta). La plantilla trae solo encabezados y listas desplegables en la hoja de datos; los ejemplos van en la hoja «Instrucciones».
- Todo Excel generado con datos de usuarios pasa sus celdas por `core.utils.excel_export.neutralizar_formula` (un texto que empieza con «=» se guarda como texto, no como fórmula).
- Regresión: `apps/clientes/tests_importacion.py` y `tests_importacion_integridad.py`.

### Integraciones externas
- **WhatsApp**: `WHATSAPP_MODE=mock` en local (sin llamadas externas, escribe en `logs/whatsapp_mock.log`). Proveedores Twilio/Meta documentados en `apps/notificaciones/services/whatsapp.py`. Con `meta_api`, los avisos que inicia el taller salen con plantillas aprobadas (`WHATSAPP_META_PLANTILLA_AVISO`, `WHATSAPP_META_PLANTILLA_PROMOCION` para campañas, `WHATSAPP_META_IDIOMA`); sin ellas se envía texto libre, que Meta solo acepta dentro de las 24 h desde el último mensaje del cliente. El barrido periódico de vehículos se eliminó: todo aviso automático pasa por la cola.
- **Cola de notificaciones** (`NotificacionProgramada`): los avisos al cliente (retiro, mantenimiento, citas, masivos) se encolan con `fecha_programada` y los envía `python manage.py procesar_notificaciones` o `POST /api/notificaciones/cron/procesar/` (sin JWT, cabecera `X-Cron-Token` = `NOTIFICACIONES_CRON_TOKEN`; vacío = 503). En Render lo dispara cada 15 min `.github/workflows/procesar-notificaciones.yml`. Canal preferido con respaldo al otro (WhatsApp ↔ correo); hasta 3 intentos con espera creciente. Cada envío queda en `RegistroMensajeWhatsApp` (guarda ambos canales, ver `canal`). Tests: `apps/notificaciones/tests_cola.py`.
- **Disparadores** (`apps/notificaciones/signals.py`, en `on_commit`; un fallo va al log y no rompe el guardado):
  - OT que **pasa** a `ENTREGADO` → próximo mantenimiento (`services/mantenimiento.py`): km objetivo = odómetro + intervalo; fecha = la menor entre el tope por meses y la estimada por el ritmo de km entre las dos últimas entregas del vehículo en la empresa; aviso `dias_antelacion` antes a las 09:00. Intervalos: el menor de los `Servicio.intervalo_km/intervalo_meses` de la OT, si no los de `PreferenciaMantenimiento`. Sin alerta para GARANTIA/ESTETICA/SINIESTRO. Reemplaza el pendiente del vehículo (único por empresa + vehículo) y actualiza `proximo_mantenimiento_km/fecha` del vehículo.
  - Cita guardada o eliminada → recordatorios `CITA_24H` y `CITA_2H` (se crean, mueven o cancelan; `recordatorios_cita_activos` en la preferencia). La cola vuelve a comprobar el estado de la cita antes de enviar.
  - Retiro: manual, solo con la OT `COMPLETADO`; 409 si ya se avisó salvo `reenviar: true`.
  - Campañas: solo a clientes con `acepta_promociones` y algún contacto; plantillas con `{cliente}`, `{empresa}` (`renderizar_plantilla` ignora llaves desconocidas). Lanzar exige además `empresa`/`modificar`.
  - Tests: `apps/notificaciones/tests_disparadores.py`.
- **IA de texto**: `POST /api/core/mejorar-texto/` `{texto, contexto?}` → `{mejorado}`. Máx. 500 caracteres. Variables `AI_TEXT_API_KEY`, `AI_TEXT_BASE_URL`, `AI_TEXT_MODEL` (opcional). No enviar datos personales.
- **Correo**: reset de contraseña con token de 1 hora y de un solo uso; enlaces hacia `FRONTEND_URL` (obligatoria en producción: su valor por defecto es de desarrollo).

### Sesiones y límites de autenticación
- JWT: access de 15 min (el cliente lo renueva solo) y refresh de 30 días con rotación + lista negra.
- **Restablecer o cambiar la contraseña revoca todas las sesiones** (`apps/authentication/sesiones.py`); el cambio de contraseña devuelve un par nuevo para el dispositivo actual con la misma empresa.
- Límites propios (`apps/authentication/throttles.py`, tasas en `LIMITES_AUTENTICACION`, ajustables por `.env` con `LIMITE_LOGIN_IP`, `LIMITE_LOGIN_CUENTA`, `LIMITE_RECUPERACION_IP`, `LIMITE_RECUPERACION_CORREO`): login 10/min por IP y 20/h por cuenta; recuperación 5/h por IP y 3/h por correo. En `manage.py test` se desactivan salvo en sus tests. La caché por defecto es local a cada worker: con Redis el límite sería exacto.
- Contraseñas nuevas: mínimo 12 caracteres (`PASSWORD_MIN_LENGTH` en el frontend debe coincidir).
- El enlace público del `.ics` caduca `DIAS_VIGENCIA_ENLACE_CITA` (7) días después de la cita.
- Regresión: `apps/authentication/tests_seguridad_sesiones.py`.

## Comandos habituales

```bash
cd backend && source venv/bin/activate
python manage.py runserver 0.0.0.0:8000
python manage.py makemigrations && python manage.py migrate
python manage.py test apps.<app>          # por app; requiere Postgres levantado
python manage.py seed_permisos | seed_clientes | seed_vehiculos
python manage.py limpiar_media_huerfana   # vista previa; --aplicar para borrar
```

Hay tests en casi todas las apps (los más extensos: `authentication`, `ordenes`, `citas`, `cotizaciones`, `notificaciones`, `core`). Al tocar el contexto tenant o los permisos, corre `apps.authentication` y `apps.core`.

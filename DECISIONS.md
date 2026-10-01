# Architecture Decision Records (ADR)

## 2026-09-29 — Ingesta: qué significa "falló", y por qué los reintentos son un multiplicador de filas

### Context

La Fase 0 del `ingestion-service` (saneamiento previo) fue donde se descubrió que **el plan partía de una premisa falsa**: daba por hecho que un fallo de `ffprobe` en `procesar_clip` agotaba `max_retries` y moría. El `except` existía, pero **se comía la excepción**: no había reintento, no había carta muerta, y el mensaje de error desaparecía sin dejar rastro. El paso estaba verificado solo en su premisa, no en su realidad.

Al arreglarlo apareció lo contrario de lo que se suponía: `procesar_lote` y `procesar_video` **sí** reintentan de verdad (tienen rutas sin capturar), y sin idempotencia eso no era robustez sino **corrupción**: un reintento con Redis caído creaba un segundo juego de filas `Video` para los mismos archivos, y `procesar_video` en su reentrada salía sin encolar los clips, colgando el lote en `procesando` para siempre.

### Decision

**1. Un actor de Dramatiq solo tiene reintentos si es idempotente. Sin idempotencia, `max_retries` es un multiplicador de filas.** `procesar_lote` lleva `if lote.estado != "pendiente": return` y `procesar_video` reapunta en la reentrada los clips que sigan en `pendiente` en vez de salir. Cubierto con 11 tests y validado por mutación: quitar la guarda, invertirla, o volver a poner el `return` seco **hacen fallar la suite**.

**2. `max_retries=0` en `procesar_clip`, con `raise`.** El trabajo es medir con ffprobe un fichero que **acabamos de escribir nosotros**, así que es determinista: si no se puede leer, reintentar solo retrasa el aviso. Y el `raise` no es decorativo — sin él, `after_process_message` sale por `if exception is None: return` y Dramatiq hace **ACK**: el mensaje se pierde en silencio. El `raise` es lo que lo manda a la carta muerta, que es donde queda el rastro. Registrado en el código que ese `0` **no es el valor bueno ni la tabla final** (la fija el Paso 7.5): significa "determinista sobre fichero propio", y la transcripción de la Fase 3 lee de disco un fichero que sube el usuario, que es otro caso.

**3. `raise` en el `finally` que cierra el lote, siempre.** `_cerrar_si_termino` es lo único que cierra un lote. Mientras exista un clip en `pendiente`, el lote no se cierra, así que la garantía tiene que ser "pase lo que pase". Lo acompaña el segundo `count` de clips en `error`: sin él, un vídeo que se corta bien pero tiene un clip corrupto cerraba el lote como `listo` y el fallo quedaba invisible desde la API.

**4. Un `.docx` corrupto avisa y se sigue sin él; NO tumba el lote.** El contexto es auxiliar y los vídeos son el trabajo de verdad, así que abortar convertiría un éxito parcial en pérdida total. El `except` se dejó **deliberadamente ancho**, contra la intuición: se comprobó contra `python-docx` que un `.docx` malformado lanza **cuatro clases sin relación** (`PackageNotFoundError`, `KeyError`, `ValueError`, `ParseError`), y un `except` estrecho que se equivoque en una reinstala el bug original (lote colgado en silencio). Lo que sí se movió a nivel de módulo fue el `from docx import Document`: dentro de la función, un `ImportError` se habría tragado una vez por documento y el lote habría perdido todo el contexto de forma indistinguible de "documentos corruptos".

### Reason

El principio que ordena las cuatro: **un fallo tiene que ser visible y no puede costar más que el trabajo que sustituía**. Tragar la excepción parecía robustez y era un agujero: el trabajo se perdía sin log y sin estado. Y un reintento sin idempotencia no es robustez, es una fuente de datos duplicados: el sistema parecía más fiable y era menos fiable.

### Trade-offs y decisiones discutidas

- **Un id inexistente reintentaba tres veces un trabajo imposible** (lote borrado a mano, fila que nunca existió). Ahora los tres actores comprueban `None` y avisan. El `strict=True` del `xfail` avisó del momento exacto de quitar la marca.
- **Un `.docx` ilegible y "no había documentos" dejan el mismo rastro en la BD** (ambos → `contexto_texto is None`). Hoy el log es el único registro. Cuando el LLM del Paso 7 empiece a usar ese campo, habrá que distinguirlo, porque es el punto donde un fallo de lectura se convertiría en una transcripción silenciosamente peor.
- **El `except` ancho es un riesgo aceptado a propósito**, y está justificado con los nombres de las cuatro excepciones, no con "es lo fácil". Si `python-docx` cambia su comportamiento, hay que revisarlo.
- **La ventana entre el `commit` de los `Video` y el `procesar_video.send` sigue abierta.** La guarda evita la duplicación, no la pérdida: si el worker muere en ese hueco, el lote queda en `procesando` con vídeos creados y nadie encolado. Cerrarlo exige un outbox transaccional o un barrido de huérfanos, y es trabajo de la Fase 7 (documentado en el Paso 7.4), no de esta fase.
- **La carta muerta expira a los 7 días** (`DEFAULT_DEAD_MESSAGE_TTL`). Con la Fase 0, `procesar_lote` (3) y `procesar_video` (2) empiezan a generar mensajes ahí. El consumidor de la Fase 8 tiene que existir antes de que la primera semana de producción se lleve los mensajes.
- **Comparar la API key con `!=` sigue siendo vulnerable a timing.** `secrets.compare_digest` quedó fuera por no ser parte de este paso, y anotado como tal: es un cambio de una línea, pero de una línea que toca autenticación.
- **`HF_HOME` en `app/config.py` es decorativo.** `faster-whisper` y `transformers` leen la variable de entorno por su cuenta; lo que la fija de verdad es el bloque `environment:` del compose, que pisa al `env_file`. Declarar la constante da la falsa impresión de que el código la controla, así que el comentario lo dice explícitamente en vez de dejar que se suponga.

## 2026-09-29 — El límite de reenvíos pasa a decir el tiempo que queda, y el estado de la verificación lo cuenta el servidor

### Context

La barra de verificación del frontend tenía que pintar tres cosas que **el cliente no puede saber**: cuánto le queda al código (el cliente solo conoce la hora en la que *pidió* el reenvío, no la de emisión), cuántos códigos se han pedido esta hora (las filas están en `verification_codes`) y cuánto falta para poder pedir otro (depende del cubo en memoria del limitador, que es del proceso del servidor). Calcularlo en el cliente obligaba a duplicar reglas de negocio —el TTL, el tope por hora, la ventana del cubo— en dos sitios, y cualquier divergencia se paga como un contador que miente.

El `InMemoryRateLimiter.allow()` devolvía un `bool`: quien recibía el 429 solo podía mannedown la ventana completa (`Retry-After: 3600` aunque quedaran 12 segundos) y el `detail` era un texto fijo que no contenía ningún número.

### Decision

**1. `allow(key, max, window) -> bool` pasa a `check(key, max, window) -> RateLimitDecision`** (`app/api/rate_limit.py`), con `allowed` y `retry_after_seconds`. El tiempo sale del cubo: cuando está lleno, la próxima plaza se libera cuando salga de la ventana la petición **más antigua** (`bucket[0]`), no la más nueva. Un par `retry_after()` aparte resuelve la **misma pregunta sin efectos** (no anota, no poda, no crea la clave), que es lo que permite al endpoint de estado *mirar* el cubo sin gastar un reenvío. La petición denegada sigue sin anotarse, como antes: si se anotara, un cliente insistiendo en el 429 iría empujando su propia ventana.

**2. El 429 lleva el tiempo real en dos sitios y con el texto en palabras.** Excepción propia `RateLimitExceeded` + handler registrado en `create_app`, cuerpo plano `{"detail": ..., "retry_after_seconds": N}` y cabecera `Retry-After: N`, ambos del mismo cálculo. No es un `HTTPException` porque su handler anida el `detail` bajo otra clave `detail` y el cuerpo quedaría `{"detail": {"detail": ..., "retry_after_seconds": ...}}`. `humanize_wait()` dice "47 minutos" o "30 segundos", redondeando **hacia arriba** en minutos y horas: decir "en 1 minuto" con 119 segundos invita a reintentar y a encadenar otro 429. Hay un texto propio para `resend-verification` y `forgot-password`; el resto usa el genérico anterior con el tiempo sustituido por "un momento".

**3. `GET /api/v1/auth/verification-status`** (autenticado, umbral propio 60/60s). `origin_guard` **no** le aplica: el middleware de `app/main.py` sale temprano para métodos que no son `POST/PUT/PATCH/DELETE`, así que un GET queda fuera por diseño. No es un agujero — no muta nada y CORS impide leer la respuesta desde otro origen — pero conviene decirlo aquí porque el guard da la falsa impresión de que cubre el router entero. Contrato congelado: `pending`, `expires_in_seconds`, `resend_available_in_seconds`, `codes_used_last_hour`, `codes_limit_per_hour`. Lectura pura: no emite, no consume códigos y no toca ningún cubo. `resend_available_in_seconds` indexa **el cubo de `resend-verification` con la misma clave** que usaría esa petición, construida desde las mismas variables que el `prefix` del router para que no puedan separarse; el test lo comprueba contra el 429 de verdad, no contra una fórmula.

### Reason

El número que se le enseña al usuario tiene que ser el mismo que va a encontrar en el 429, y eso solo se garantiza si **lo lee el mismo sitio** que lo va a denegar. Por eso el endpoint no estima nada: consulta el cubo. Y el 429 informa del tiempo que **queda**, no del que dura la ventana, porque es lo que el usuario necesita para decidir cuándo volver.

### Trade-offs y decisiones discutidas

- **Un código CADUCADO sigue contando como `pending`, con `expires_in_seconds = 0`.** Marcarlo `pending=false` haría desaparecer la barra de verificación y dejaría al usuario sin ninguna señal de que debe pedir un código nuevo. La UI lo pinta como "caducado, pide otro".
- **Con la cuenta ya verificada los cinco campos van a 0, incluido `codes_limit_per_hour`.** Es lo que dice el contrato congelado ("`false` y el resto en 0") y evita que un cliente que pinte "0/5 códigos" se lie. Es el único caso en que el tope devuelve 0 en vez de `settings.verification_code_max_per_hour`, y por eso todos los campos admiten `ge=0`.
- **Carrera conocida en el registro.** `register` emite el código en una `BackgroundTask`, que se ejecuta *después* de enviar la respuesta: una llamada inmediata a `/verification-status` puede devolver `pending=false, codes_used_last_hour=0` y, 1,5 s después, `pending=true, codes_used_last_hour=1`. Verificado contra uvicorn real. No se arregla aquí (mover la emisión a la ruta reintroduciría el oráculo de enumeración de tiempo documentado en el ADR anterior); el frontend debe tolerarlo, igual que ya tolera un correo que aún no ha llegado.
- **`Retry-After` en minúscula sobre el socket.** Uvicorn normaliza los nombres de cabecera; son case-insensitive por RFC y httpx/los proxies no distinguen. Los tests los leen con httpx.
- **El limitador sigue siendo por proceso.** Con varios workers, `resend_available_in_seconds` sería el del worker que atendió la petición y no el de otro. Preexistente y ya documentado en el módulo: la solución es un backend compartido (Redis).

### Verification

212 tests backend verdes (175 previos + 37 nuevos). La suite se validó por **mutación**: devolver la ventana entera en vez de lo que queda, quitar el suelo 0 de `expires_in_seconds`, indexar el cubo del reenvío con la ruta equivocada, omitir la clave `verification-status` del dict `limits`, contar sin filtrar por propósito, ageing de 2 h en vez de 1 h, tratar un código caducado como no pendiente, hacer que `check()` lea `bucket[0]` sin blindar o quitar `retry_after_seconds`/`Retry-After` del 429 **hacen fallar la suite**. Comprobación manual contra uvicorn real con dos instancias (ventanas de 1 h y de 30 s) y usuarios de verdad: registro → estado → verificación → reenvíos → 429, con el `Retry-After` medido en 26 s sobre una ventana de 30 s.

---

## 2026-09-29 — Verificación de email y reseteo de contraseña por código de un solo uso (Resend)

### Context

El panel de usuario tenía tres secciones —cambiar contraseña, cambiar email y zona de peligro— bloqueadas con un chip "Próximamente" porque dependían de una capacidad inexistente: **envío de correos transaccionales**. El reseteo de contraseña ya tenía endpoint (`forgot-password` / `reset-password`), pero era un deadlock funcional: generaba un token que **nunca llegaba a nadie** (en dev se logueaba y en prod no había proveedor), así que el flujo era inejecutable de punta a punta.

### Decision

Sustituir el token largo por un **código numérico de 6 dígitos de un solo uso**, enviado con la API de Resend, sobre una **tabla única discriminada por `purpose`**.

**1. Tabla única `verification_codes`, no dos.** `purpose` separa `"email_verification"` de `"password_reset"` (y deja hueco para `"email_change"`). Los dos flujos son idénticos en mecánica —emitir, enviar, verificar, consumir, caducar, contar intentos—, así que dos tablas habrían duplicado el código y la mitad de los casos límite. La tabla `password_reset_tokens` se elimina: su endpoint ya no la usa.

**2. El hash va salado por usuario y propósito**: `sha256(f"{purpose}:{user_id}:{código}")`. No es decorativo. Un código de 6 dígitos tiene 1.000.000 de valores posibles, así que con un `UNIQUE` global sobre `sha256(código)` **dos usuarios distintos recibirían el mismo código con frecuencia no despreciable** (paradoja del cumpleaños) y el `INSERT` fallaría, dejando al usuario sin código. Salar con el `user_id` elimina la colisión en la raíz en lugar de posponerla, y deja el `UNIQUE` como una invariante real que además detecta reuso de hash. El código en claro nunca se persiste.

**3. Endurecimiento del fuerza bruta.** Un código de 6 dígitos es débil por entropía, así que la seguridad no depende de la longitud:
- TTL de 10 minutos · máximo **5 intentos** por código (agotados, se invalida).
- Tope de **5 códigos por hora** por `(usuario, propósito)`: es la pieza crítica, porque el rate limit existente es **por IP** y un atacante desde una IP distinta no lo tocaría. Con el tope por email el atacante hace ~25 intentos/hora contra 10⁶.
- Al emitir un código nuevo se revocan los anteriores sin usar del mismo propósito, y **el tope se comprueba antes de revocar**: si se revocara primero, agotar el tope dejaría al usuario sin código nuevo *y* sin el viejo.
- Respuesta genérica y única para los cinco casos de fallo (inexistente, consumido, caducado, sin intentos, email desconocido).

**4. La capa de email es agnóstica.** `EmailSender` como `Protocol` con dos implementaciones: `ResendSender` (SDK oficial, `send_async`, `idempotency_key`) y `ConsoleSender`. Vive en `app.state.email_sender`, no en el `Settings`: así los tests inyectan un espía y **ningún test puede enviar un correo real**, sin depender de la configuración. El fallo de envío nunca rompe un flujo de auth (best-effort), salvo en `resend-verification`, que es autenticado y donde no hay riesgo de enumeración.

**5. La verificación NO bloquea el uso.** El registro deja `is_email_verified=false` e inicia sesión igual; un banner en el dashboard invita a verificar. Se descartó deliberadamente una puerta dura (403 en `login`) por acuerdo explícito. El flag `require_verified_email` se implementó y **se eliminó**: un setting que no hace nada es peor que no tenerlo, porque un operador que lo active esperará una protección que no existe.

### Reason

El sistema tenía que ser auditable por alguien que no escribiera el código, así que cada garantía de seguridad está sujetada por un test que **muerde**: quitar la sal del hash, anular el bloqueo de intentos, deshacer el escapado HTML o reintroducir la enumeración hacen fallar la suite. La verificación por mutación es lo que convierte "está endurecido" en "lo está".

### Trade-offs y hallazgos de la revisión

- **6 dígitos frente a 8 caracteres alfanuméricos.** Se eligió 6 dígitos por fricción de uso; el coste es que la entropía es baja y la seguridad depende de los límites, no de la clave. Un código `XXXX-XXXX` haría la fuerza bruta inviable por sí solo, a cambio de más caracteres que teclear. **Revisar si el producto crece.**
- **Enumeración de cuentas por tiempo (corregido).** La primera implementación solo llamaba al proveedor si el usuario existía, con el `await` en la ruta de la petición: 154 ms de mediana con cuenta frente a 2,9 ms sin ella, es decir, **100 % de acierto enumerando con una sola petición y un umbral de 50 ms**. El status y el body ya eran idénticos, y el test existente solo miraba el body, así que pasaba. La emisión completa se movió a `BackgroundTasks`; la tarea abre **su propia `SessionLocal()`** porque desde FastAPI 0.106 las dependencias con `yield` se cierran antes de que se envíe la respuesta y la sesión de `Depends(get_db)` ya está cerrada. Re-medido sobre socket real: separación de −0,21 a +0,07 ms, frente a los 151 ms del control positivo del mismo arnés.
- **Códigos OTP en el log de producción (corregido).** `EMAIL_PROVIDER=resend` + `EMAIL_ENABLED=false` + sin key pasaba la validación y terminaba en `ConsoleSender`, que escribe el cuerpo del mensaje. Tres barreras independientes: la validación exige las tres variables, `build_email_sender` **nunca** devuelve `ConsoleSender` en producción, y el propio `ConsoleSender` omite el cuerpo cuando `is_prod`.
- **TOCTOU en el tope horario (aceptado).** El `count` se lee y se inserta sin lock: 8 peticiones simultáneas emiten 9 códigos. Mitigado por el rate limit por IP y porque solo permite auto-spam. Documentado en el código para que no se re-descubra como bug.
- **`is_prod` falla abierto ante un typo (preexistente).** `ENVIRONMENT=production` no se reconoce como producción y se saltarían las validaciones de `SECRET_KEY`, `COOKIE_SECURE` y de email. Heredado de la configuración original, no introducido aquí.

### Verification

175 tests backend verdes. Quality gate en dos rondas: `Needs changes` con los dos exploits anteriores, y `Approved` tras los fixes, con medición de timing sobre uvicorn real, barrido de 28 combinaciones de configuración de producción, y comprobación de que el envío sigue ocurriendo de verdad (120 envíos reales, ninguno a direcciones inexistentes) y de que no hay fuga de conexiones (200 entradas / 200 salidas del pool). Frontend con `npm run lint` y `npm run build` limpios y recorrido E2E de los dos flujos en navegador real.

---

## 2026-09-29 — Panel de usuario: qué se implementa ya y qué queda como plantilla

### Context

El usuario recién registrado debe tener acceso a su panel de cuenta. Parte de las opciones de seguridad que queríamos ofrecer (cambiar contraseña, cambiar email, eliminar cuenta) dependen de una capacidad que todavía no existe: **envío de correos transaccionales**. Implementarlas "a medias" o esconderlas habría dejado al usuario sin visibilidad de lo que viene, y prometer un flujo que no puede completarse.

### Decision

Dividir el panel de usuario (`/settings`) en dos capas:

1. **Funcional ahora, sin depender de correo**: edición de `full_name`, listado de sesiones activas y cierre de sesión en otros dispositivos, y cierre de sesión local.
2. **Plantilla visible pero deshabilitada**: cambiar contraseña, preferencias (notificaciones, idioma, zona horaria, tema) y zona de peligro (exportar datos, eliminar cuenta). Se renderizan con `disabled` + `aria-disabled`, chip "Próximamente" y una explicación de qué falta, **nunca ocultos con `display: none`**. "Cambiar contraseña" enlaza además al flujo de recuperación ya existente (`/auth`) para que el usuario tenga una salida real.

El endpoint de cambio de contraseña **no** se implementó todavía. Cuando llegue el envío de correos se añadirá junto con el resto de la capa diferida.

### Reason

La plantilla deja la arquitectura de información y el diseño visual cerrados y revisados, de modo que activar cada función más adelante es una tarea mecanizada (endpoint + conectar el handler) en lugar de una fase de diseño. Y la sección de Seguridad no queda como un placeholder vacío: la gestión de sesiones es una función de seguridad real, útil hoy, que además cubre el caso legítimo de "cerré sesión en un dispositivo compartido" sin necesidad de correo.

### Trade-offs y detalles de diseño

- **`PATCH /me` solo toca `full_name`.** El modelo de request es cerrado (Pydantic 2 ignora campos extra), así que `email`, `is_active` o `password_hash` en el body se descartan. Cambiar el email exige verificación por correo, luego queda en la capa diferida.
- **`GET /sessions` expone metadatos, nunca credenciales.** `SessionPublic` declara 5 campos y se construye campo a campo, sin `from_attributes`: si alguien devolviera filas ORM crudas, FastAPI daría 500 en vez de filtrar. La cookie `httpOnly` sigue siendo la única credencial. Trade-off aceptado: `id` es el autoincrement global de la tabla, así que revela el número total de sesiones del sistema (la UI no lo muestra, solo lo usa como clave de React). Documentado en el docstring del schema.
- **`POST /sessions/revoke-others` tiene un guard previo obligatorio.** Antes del `UPDATE` se comprueba que la cookie corresponde a una sesión **viva** (no revocada, no caducada) del usuario autenticado. Sin ese guard, una cookie caducada o manipulada hace que `token_hash != :hash` sea cierto para todas las filas y el endpoint revoca todas las sesiones, dejando al usuario sin ninguna — un fallo silencioso y destructivo. Con cookie inválida responde 204 sin tocar nada. Hay un test que falla explícitamente si se quita el `expires_at` del guard.
- **El panel es un layout propio, no el shell `cockpit` del dashboard.** Los ajustes son ortogonales al cockpit operativo; meterlos en la sidebar de KPIs sería incoherente. Se mantiene la Navbar global y se reutilizan clases compartidas de `dashboard.css` (`.panel`, `.queue-*`, `.side-link`, `.btn-secondary`, `.status-chip`) sin duplicarlas.
- **Rate limiting con claves compartidas.** `GET /sessions` y `POST /sessions/revoke-others` usan la misma clave `sessions` (30/60s); `PATCH /me` usa `profile` (20/60s). Ojo al añadir rutas: el dict de `make_rate_limit_dependency` se indexa **en tiempo de petición**, así que una dependencia `rate("x")` sin su clave en el dict produce un `KeyError` → HTTP 500, no un fallo visible al importar.
- **La cobertura anti-CSRF depende del prefijo del router.** `origin_guard` en `app/main.py` solo cubre rutas bajo `/api/v1/auth`. Mover estos endpoints a `/api/v1/account` desactivaría el guard en silencio; hay tests de Origin que fijan el 403 en `PATCH /me` y `POST /sessions/revoke-others` para que ese refactor no pase inadvertido.

---

## 2026-09-17 — Unified Command System Design Architecture

### Context

The application needs a consistent visual language that supports high data density and fast operational scanning for social media management.

### Decision

Adopt the "Unified Command System" visual language:

- **Primary Accent**: Purple `#511877`
- **Secondary Accent**: Coral `#FF5722`
- **Typography**: Plus Jakarta Sans

### Reason

Provides high legibility for dense analytics dashboards while maintaining a modern, agency-grade aesthetic.

---

## 2026-09-17 — Frontend Stack Selection (React 19 + Vite 8 + Oxlint + React Compiler)

### Context

Need a fast, type-safe, and future-proof frontend build setup.

### Decision

- Use **React 19** with **Vite 8**.
- Enable **React Compiler** via `@rolldown/plugin-babel` to eliminate manual memoization (`useMemo`/`useCallback`).
- Use **Oxlint** for high-speed JavaScript/TypeScript linting.

### Trade-offs

Oxlint and React Compiler are modern tools; build configurations must be explicitly locked and verified.

---

## 2026-09-16 — Strict Three-Agent Execution Workflow

**Status**: Superseded on 2026-09-20 by the OpenCode agent configuration in `.opencode/opencode.json` (`primary` orchestrator + specialized subagents: `backend-architect`, `frontend-developer`, `database-admin`, `tester-senior`, `code-reviewer`).

### Context

To prevent context drift, hallucinated changes, and broken builds when using LLM agents.

### Decision

Mandate a 3-agent cyclic workflow: `planner` → `executor` → `reviewer`.

### Rules

- No code execution without an explicit plan.
- No code commit/approval without `reviewer` sign-off.
- Every approved step must be recorded in project state logs.

---

## 2026-09-20 — Autenticación JWT: access token en memoria + refresh token en cookie httpOnly

### Context

NexusManager es una SPA (Vite/React) con API FastAPI. Necesitábamos auth por usuario/contraseña sin vulnerar el balance seguridad/UX: proteger contra XSS (robo de token), CSRF (abuso de cookie) y escalada de sesión.

### Decision

Doble token con separación de responsabilidades:

- **Access token (JWT, HS256, 15 min)**: vive **solo en memoria** dentro del módulo `frontend/src/lib/api.ts` (variable de módulo → nunca `localStorage`/`sessionStorage`). Inyectado por la app en el header `Authorization`, por tanto invisible a XSS persistente y no persiste tras recargar.
- **Refresh token (opaco, 256-bit aleatorio)**: viaja en **cookie `httpOnly` + `SameSite=Lax` + `Secure` (en prod)**, con `Path=/api/v1/auth` (solo la envía el router de auth) y **rotación en cada refresh** + detección de reuso (un refresh usado dos veces revoca **todas** las sesiones del usuario → mitigación de robo de cookie). `remember me` controla `SameSite/expiración` (sesión de navegador vs 30 días).

### Consequences

- + Mitiga sustancialmente el vector más común de robo de JWT (XSS → localStorage).
- + Mitiga CSRF: la cookie es `SameSite=Lax` y el cliente no puede leer el refresh (double-submit nativo vía SameSite). Mutaciones de `/auth` protegidas además por guard de Origin.
- + Rate limiting propio en memoria por ruta (`register/login/refresh/logout/forgot/reset`) evita fuerza bruta; se descartó `slowapi` por incompatibilidad con Starlette 1.6.
- − El access token se pierde al recargar → el frontend reintenta un `refresh` silencioso antes de declarar al usuario como invitado (flujo `bootstrap` ya verificado E2E).
- − Sustituir el `secret_key` por defecto de `app/config.py` es **obligatorio** en despliegue (fail-fast en prod valida `CLAVE_INVALIDA` de entorno).

### Verification

24 tests backend (pytest), `build`+`lint` frontend limpios, y **E2E de navegador real (Playwright)**: registro→dashboard(vía `/me`)→logout→login→login-fallido(no redirige+banner) — todo PASS sin errores JS.

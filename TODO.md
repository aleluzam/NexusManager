# TODO — Ingestion Service: corte por frases con Whisper

> **Este archivo es el punto de reanudación del proyecto.** Si una sesión nueva retoma
> este trabajo, lee primero `## Contexto` y `## Cómo retomar el trabajo`, y continúa
> desde la primera casilla sin marcar de la fase activa.
>
> **Regla:** las fases se hacen **en orden**. No se empieza la fase N+1 con la N a medias.
> Cada paso tiene su línea de **Verificación**; un paso no se marca hecho hasta que esa
> verificación pasa.
>
> **Cómo se marca un paso hecho:** añadir bajo el paso un bloque
> `**Estado: hecho (AAAA-MM-DD)**` con **qué se ejecutó** y **cómo se verificó**, incluyendo
> lo que quedó pendiente de confirmar. La verificación ejecutada a medias se dice a medias.

---

## Cómo retomar el trabajo

Esta sección es el punto de entrada. El orden es **obligatorio**: las fases van en serie y
un paso no se marca hecho hasta que su línea de **Verificación** pasa.

### Dónde está el proyecto

- **Fase 0 — CERRADA (2026-09-29).** Los 8 pasos hechos, verificados y documentados bajo
  cada uno, con 75 tests verdes. **Empieza por la Fase 1.**
  - **El 0.7 está hecho a medias y no se debe dar por cerrado:** `HF_HOME` está
    configurado, pero su verificación literal (que no se vuelva a descargar el modelo) no
    se puede hacer hasta que haya un modelo que descargar, que es la Fase 3.
  - **El 0.1 nunca se ha probado en un puerto libre.** El `8000` siempre estuvo ocupado.
    La coexistencia de puertos está probada de sobra y no bloquea nada, pero la línea de
    verificación del paso no se cumple al 100 %.
- **Fase 1 — sin empezar.** Es lo siguiente: modelo de datos y migraciones.
- Fases 2 a 9 — planificadas, sin tocar.
- Fase 10 — declarada fuera de alcance, no olvidada.
- **Trampas 3, 9 y 10** de "Trampas conocidas": resueltas en la Fase 0 y marcadas como
  tales. No las releas como si siguieran vigentes.

### Antes de tocar nada, cinco comprobaciones que cuestan un disgusto si se saltan

1. **`docker compose build` antes de probar.** El `Dockerfile` hace `COPY app`, así que
   los tests y el servicio corren contra el `app/` congelado en el build. Editar y probar
   sin reconstruir da un **verde falso**. Ya pasó una vez en esta fase.
2. **Los tests corren dentro del contenedor**, no en el host:
   `docker compose run --rm -v ./tests:/srv/tests -v ./requirements-dev.txt:/srv/requirements-dev.txt:ro api sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"`.
   Con `python -m pytest` a secas no encuentra el paquete `app`.
3. **`API_KEY` es obligatoria** en `.env` (permisos `600`, gitignored). Sin ella el
   servicio **no arranca**, a propósito. Para generarla:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
4. **Postgres con pgvector, no SQLite.** La columna `Vector(1024)` impide usar SQLite ni
   para los tests.
5. **No tocar el backend raíz ni el frontend.** `ingestion-service` habla por su puerto
   `8001` y no necesita ni molesta al `8000` (backend) ni al `5173` (Vite).

### Las tres reglas de este código

- **Todo actor de Dramatiq que reintente tiene que ser idempotente.** Sin idempotencia,
  `max_retries` duplica filas. No es robustez, es corrupción de datos.
- **`_cerrar_si_termino` va siempre en el `finally`.** Es lo único que cierra un lote.
- **Un fallo se registra y se propaga; no se traga.** Tragar la excepción hace que Dramatiq
  haga ACK y el mensaje desaparezca sin log y sin estado. Ya costó el 0.6 entero.

### Antes de cerrar un paso

Su **Verificación** tiene que pasar **por ejecución**, no por lectura. Y un test que pasa
sin comprobar lo que su nombre afirma es peor que no tener test: pásalo por **mutación**
(introduce el bug y comprueba que la suite falla). Los dos casos que topographic esta fase
fueron justo eso: un test que no ejercitaba el recorte que nombraba, y una suite entera
verde contra el código viejo.

---

## Contexto

`ingestion-service/` es un microservicio independiente que hoy hace: `POST /lotes` → guarda
originales → worker Dramatiq → corta cada vídeo en clips fijos de 10 s normalizados
(720p/30fps) → `GET /lotes/{id}` para seguir el progreso. Los 4 TODO de `procesar_clip`
(transcripción, visión, ficha JSON, embedding) nunca se implementaron.

**Recuento tras la Fase 0 (2026-09-29):** `app/` son 8 `.py` y ~586 líneas; `tests/` son 6
`.py` y ~2250 líneas, que es más código de test que de aplicación. Todo bajo un único
commit (`c01342c`) y sin migraciones: el esquema se gestiona con `create_all`, así que
cualquier cambio de modelo de la Fase 1 necesita `ALTER TABLE` manual en producción.

**Este plan implementa solo el primer TODO: transcripción con marcas de tiempo y corte por
frases.** Visión, ficha JSON y embeddings quedan explícitamente fuera (Fase 10).

### Objetivo funcional

```
Subir un vídeo
  → transcribirlo con Whisper
  → obtener las frases con su marca de inicio y de fin
  → cortar el vídeo usando esas marcas
  → resultado: un lote de minivideos, cada uno empieza en el inicio de una frase
    del vídeo original y termina en su final.
```

### Contexto de despliegue (decidido por el usuario)

- **Todo en una sola VPS**: `backend/`, `frontend/` e `ingestion-service/` conviven en el
  mismo servidor.
- Por tanto **el bind mount `./storage:/storage` es válido y se mantiene**: no hace falta
  migrar a S3 ni a NFS. Se registra como decisión en `DECISIONS.md`.

**Especificaciones de la VPS:**

| Recurso | Valor | Lectura para este plan |
|---|---|---|
| CPU | 6 vCPU | **Es el recurso escaso.** Condiciona el modelo de Whisper y el número de hilos del worker. |
| RAM | 12 GB | Sobrada para `large-v3` (~3 GB) + Postgres + backend. No es el limitante. |
| Disco | 200 GB SSD | **Segundo punto de tensión.** El pipeline guarda originales + clips y hoy no limpia nada. |
| Red | 300 Mbit/s (~37 MB/s) | No es un problema: un vídeo de 1 GB sube en ~35 s. El cuello es la transcripción, no la subida. |

- **Puertos decididos:** `backend 8000`, `ingestion 8001`, `frontend 5173`. Hoy
  `ingestion-service` publica `8000:8000` y el backend arranca `uvicorn main:app` **sin
  puerto explícito** (o sea, 8000): los dos chocan.
- **El worker procesa un vídeo a la vez** (`--processes 1`). Si se suben dos lotes, el
  segundo espera. No hay nada que arreglar, pero la UI tiene que reflejar esa cola o
  parecerá que se quedó colgado.
- **Presupuesto de disco:** un vídeo de 10 min en 1080p ≈ 1 GB, y los clips suman otro
  tanto. 200 GB ≈ 80–150 vídeos. hace falta una política de retención (Fase 10), y entre
  tanto conviene avisar al usuario cuando el disco pasa del 80%.

---

## Decisiones ya tomadas (no re-litigar)

| Decisión | Motivo |
|---|---|
| `faster-whisper` en vez de `openai-whisper` | Mucho más rápido en CPU (CTranslate2), cuantización int8, marcas por palabra, VAD integrado. |
| **Modelo por defecto `medium`**, no `large-v3` | En 6 vCPU, `large-v3` int8 va a ~0.4–0.8x tiempo real: un vídeo de 10 min tardaría 12–25 min. `medium` baja a ~5–7 min. La RAM no es el problema; la CPU sí. `large-v3` queda configurable para pasadas de calidad. |
| La transcripción baja a `procesar_video`, no a `procesar_clip` | Transcribir es una operación **por vídeo**, no por clip. `procesar_clip` queda casi vacío. |
| `Clip` pasa a significar "frase" | Se reutiliza la tabla y la cadena Dramatiq existentes en vez de crear un camino paralelo. |
| Se **conserva** la normalización 720p/30fps/h264+aac | Es lo que hoy permite que el `concat` de FFmpeg funcione sin re-encodear. No se toca. |
| Un `ffmpeg` por frase (v1) | Simple y depurable. La alternativa de un solo paso con `select` queda anotada en 5.3. |
| El storage sigue en bind mount local | Todo vive en la misma VPS. |
| **El padding se persiste por lote**, no es una constante global | El fallo que vamos a detectar es sistémico (una constante mal puesta), no un error por clip. Persistirlo hace que corregirlo sea cambiar un valor y relanzar el lote. |
| **QA: reportar + 1 auto-corrección acotada solo en la etapa 1** | La etapa 1 cuesta ~1 ms (numpy) y su fallo siempre es de configuración → auto-corregir 1 vez. La etapa 2 cuesta ~0.5–2 s (ASR) y su fallo requiere juicio → solo reportar. Ver Fase 6. |
| **Nada de agente LLM para el QA de bordes** | Un modelo de visión no puede oír una sílaba truncada. Y una pipeline no puede depender de un "me parece bien" no determinista: hace falta veredicto numérico. El LLM entra en la Fase 10, para otra pregunta. |
| **Una "frase" = segmento de Whisper partido por puntuación** | Tope **20 s**, mínimo **1.5 s**. Corte en `.` `?` `!` y, si aún queda largo, en `,` `;` `:`. Sobre un vídeo de 10 min en español da ~40–60 clips de 10–20 s. Configurable, con estos defaults. |
| **`CALIDAD_QA = "rapida"` por defecto** (etapa 2 apagada) | La etapa 2 es un ASR por clip. Medirla antes de calibrar el padding es medir contra una verdad sin calibrar. Se enciende cuando un lote salga limpio en la etapa 1. |
| **Vídeo sin voz → `sin_voz`, no `error`** | Un B-roll con música es una subida legítima de una agencia; no debe tumbar el lote. El vídeo queda en `sin_voz` con 0 clips, se loguea un aviso, y el lote se cierra igual. La UI muestra "0 clips generados". |
| **Puertos**: `backend 8000`, `ingestion 8001`, `frontend 5173` | Los dos servicios de FastAPI están hoy los dos en 8000. |

---

## Fase 0 — Saneamiento previo (sin tocar el modelo de negocio)

> Nada de esto es "el objetivo", pero bloquea o vuelve invisible la Fase 3. Se hace
> primero porque es barato y porque depurar Whisper a ciegas, sin logs, es imposible.

### Paso 0.1 — Repartir puertos (BLOQUEANTE)
- Mover `ingestion-service` a `8001:8001` en `docker-compose.yml` y en su `README.md`.
- Fijar `8000` de forma explícita en el arranque del backend principal y en el `README.md`
  raíz, para que no dependa del default de uvicorn.
- **Verificación:** `docker compose up` con los dos servicios de FastAPI arriba, y `curl` a
  `localhost:8000/hello` y `localhost:8001/docs` sin interferirse.

**Estado: hecho (2026-09-29), verificado en caliente.**
- Ejecutado: `docker-compose.yml` publica `8001:8000` (el contenedor sigue en 8000);
  `ingestion-service/README.md` actualizado a 8001; `README.md` raíz con tabla de puertos
  y sección del ingestion-service; arranque del backend fijado a `--port 8000`.
- Verificado por ejecución, con los dos servicios **a la vez**:
  - `:8000/hello` → `200 {"message":"Hello, World!"}` (backend).
  - `:8001/docs` → `200`, `:8001/openapi.json` → `200`, con las 3 rutas (`/lotes`,
    `/lotes/{lote_id}`, `/lotes/{lote_id}/clips`).
  - Auth: sin cabecera → `422` (validación de FastAPI por header obligatorio), clave
    incorrecta → `401`, clave correcta → `404 "Lote no encontrado"`, o sea que la petición
    **llegó a Postgres y consultó la tabla**.
  - `docker compose ps`: `api`, `worker`, `postgres`, `redis` los cuatro arriba, con
    `0.0.0.0:8001->8000/tcp`.
  - Esquema creado: tablas `lotes`, `archivos`, `videos`, `clips` + extensión `vector`.
- **Extras que hizo falta añadir** (no estaban en el paso original): `ingestion-service/`
  no tenía `.gitignore`, así que el `.env` con `API_KEY` habría sido commiteable. Se creó
  uno con `.env`, `__pycache__/` y `storage/`. Y se creó el `.env` que faltaba (copia de
  `.env.example`, permisos 600), sin el cual `env_file: .env` hace fallar `docker compose`.
  **Ojo:** en aquel momento esa copia traía `API_KEY=cambia-esta-clave`; el Paso 0.2 ya
  la sustituyó por una clave real de 43 caracteres.
- **Lo que NO se verificó:** el arranque propio del backend. En el momento de la prueba el
  puerto 8000 ya estaba ocupado por un uvicorn huérfano (PID 31376, del 2026-09-20, con un
  Python de Homebrew por fuera del venv), así que aquel `200` vino de **ese** proceso y no
  del comando `uvicorn main:app --port 8000` que documenta el README.
- **Resuelto por el paso del tiempo, 2026-09-29:** el PID 31376 ya no existe. En su lugar
  hay un uvicorn en `:8000` (PID 91408, arrancado a las 13:46) con `cwd=backend/` y
  `.venv/bin/uvicorn main:app`, o sea **usando el venv**, y responde `200` en `/hello`.
  El frontend sigue en `:5173` con Vite y responde `200`. Ninguno de los dos lo ha tocado
  esta fase: `ingestion-service` habla por su propio puerto 8001 y no toca el 8000.
  - El huérfano era exactamente lo que parecía: un `--reload` olvidado del 2026-09-20 que
    alguien relevó. El comando del README (`uvicorn main:app --port 8000`) queda respaldado
    por un proceso vivo que lo ejecuta con el venv correcto.
  - Sigue **sin** verificarse en un puerto libre, que es lo que el paso pedía. Es la
    única parte de la Fase 0 que no puede darse por verificada al 100 %, y no bloquea
    nada: la coexistencia de puertos está probada de sobra.

### Paso 0.2 — `API_KEY` con fallo abierto
- `app/config.py:3` usa `os.environ.get("API_KEY", "cambia-esta-clave")`: si falta la
  variable el servicio **arranca con una clave publicada en el repo**.
- Igualar el comportamiento del backend principal: si `API_KEY` falta o es el valor por
  defecto, abortar al arrancar con mensaje claro.
- **Verificación:** arrancar sin `API_KEY` en el entorno → el proceso muere con error
  explícito, no con un warning.

**Estado: hecho (2026-09-29), verificado por ejecución.**
- `app/config.py` → `_api_key()` aborta el arranque con `RuntimeError` si falta, está
  vacía, son solo espacios o vale el placeholder `cambia-esta-clave`. No queda ninguna
  clave por defecto en el código, que era el punto: una clave por defecto versionada
  es una clave pública.
- Ejecutado los cuatro casos contra el build real: los cuatro matan el proceso con
  mensaje explícito; con clave válida el módulo importa.
- `.env` local creado con clave real de 43 caracteres, `chmod 600` y gitignored.
  `.env.example` documenta que la clave es obligatoria.
- `ingestion-service/README.md` ya no muestra la clave de ejemplo: genera una con
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- 8 tests en `tests/test_config_api_key.py`. Mutation testing: dejar el fallback o quitar
  el `.strip()` hacen fallar la suite.

### Paso 0.3 — Colisión de nombres en `storage.nombre_seguro`
- `mi video.mp4` y `mi-video.mp4` colisionan → dos filas `archivos` apuntan al mismo
  archivo y el segundo upload pisa al primero.
- Añadir un sufijo único al guardar (hash corto o contador), conservando el
  `nombre_original` en la BD para poder mostrarlo.
- **Verificación:** subir un lote con esos dos nombres → 2 filas `archivos`, 2 rutas
  distintas, y el contenido de cada una intacto.

**Estado: hecho (2026-09-29), verificado por ejecución y con 40 tests.**
- `nombre_seguro` añade un token aleatorio de 12 hex conservando la extensión, y
  `nombre_original` sigue guardándose en la BD para poder mostrarlo.
- 40 tests en `tests/test_storage_nombres.py`, incluidos los tres casos que el
  `TODO` no mencionaba y que también colisionaban: stem vacío (`....mp4`), separador de
  ruta (subida de directorios) y `.`/`..`.
- **Trampa encontrada al escribir los tests:** el caso `"ñ"*300` **nunca ejercitaba el
  recorte de 200** que su nombre afirmaba. `ñ` se normaliza a `_`, `strip("._")` se come
  las 300 y salta el fallback `or "archivo"`. Sustituido por casos ASCII, y añadida la
  invariante que hace que ese recorte sea seguro: **la salida es siempre ASCII**. Con
  cirílico o CJK, 200 caracteres son 400–600 bytes y los uploads romperían con
  `ENAMETOOLONG` con toda la suite en verde.
- **Consecuencia de producto a confirmar:** con un nombre cirílico o CJK el prefijo
  legible se pierde entero y solo queda el token (para unicidad da igual, porque
  `nombre_original` lo conserva). Es correcto por diseño, pero alguien debería
  confirmarlo.
- Además: `main.py` recorta `nombre_original` a 255. `VARCHAR(255)` de Postgres cuenta
  caracteres, así que `str[:255]` es exacto. Sin eso, un nombre largo daba un 500 sin
  manejar en mitad del bucle, con el lote ya commiteado y ficheros huérfanos en disco.

### Paso 0.4 — Logging real
- `tasks.py:14` define `log` y **nunca lo usa**. Cero visibilidad del pipeline.
- Configurar logging en `db.py`/`main.py`, y loguear en cada transición de estado con
  `lote_id` / `video_id` / `clip_id` como contexto.
- **Verificación:** un lote fallido produce líneas de log que dicen **por qué** y en qué
  paso, sin consultar la BD.

**Estado: hecho (2026-09-29), verificado en caliente.**
- `configurar_logging()` se llama desde los **dos** puntos de entrada: la API y el
  worker son procesos distintos y si solo lo configurara uno, el otro salía sin logs.
  `force=True` evita líneas duplicadas.
- Cada transición loguea con los tres ids en el mensaje: lote, vídeo y clip. Convención
  deliberada (el id va en el texto, no en `extra={}`) — lo que necesitará un consumidor
  de logs indexado llega en la Fase 8.4.
- **Verificación en caliente, que es la del paso:** lote con un mp4 corrupto →
  `vídeo 1 (lote 1): corte fallido, queda en error` + traceback de ffprobe + `lote 1:
  cerrado como 'error' (1 vídeo(s) y 0 clip(s) en error)`. Se lee sin abrir la BD.
  Lote con mp4 válido → `3.00s -> 1 clip(s)`, `clip 2 (vídeo 3, lote 3): listo`, `lote 3:
  cerrado como 'listo'`.
- Hay test de esto (`test_lote_fallido_se_explica_en_el_log_sin_consultar_la_bd`), porque
  el criterio de verificación del paso sí es automatizable.
- `log.exception` en los fallos: incluye el traceback, que es la mitad del valor.

### Paso 0.5 — Healthchecks en `docker-compose.yml`
- Hoy `depends_on` sin `condition: service_healthy` solo espera "contenedor iniciado", y
  `init_db()` hace `CREATE EXTENSION vector` al arrancar → carrera contra Postgres.
- Añadir healthcheck a `postgres` y `redis`, y `restart: unless-stopped` a `api`/`worker`.
- **Verificación:** `docker compose down && docker compose up` en frío 3 veces seguidas,
  sin que `api` falle por `CREATE EXTENSION`.

**Estado: hecho (2026-09-29), verificado con 4 arranques en frío.**
- Healthcheck en postgres y redis, `depends_on` con `condition: service_healthy`, y
  `restart: unless-stopped` en `api` y `worker`.
- Ejecutado 4 veces (1 con volumen nuevo + 3 conservando volumen): la API siempre con
  `RestartCount=0`, sin fallos de `CREATE EXTENSION`. Antes del primer `down -v` se
  confirmó que el volumen tenía 0 filas, para no destruir datos por inercia.
- **Pendiente, no bloqueante:** postgres y redis siguen **sin `restart`**, así que no se
  recuperan solos si mueren. El healthcheck los detecta, pero nadie los relanza. Y el
  `worker` no tiene healthcheck propio: su salud es "no se ha caído", que es otra cosa.

### Paso 0.6 — `procesar_clip` puede dejar el lote colgado
- `ffmpeg_utils.duracion()` usa `check=True` **fuera de try/except**. Si falla, Dramatiq
  agota `max_retries` y la tarea muere **sin llamar a `_cerrar_si_termino`** → el clip
  queda `pendiente` y el lote `procesando` para siempre.
- Envolver en try/except, marcar `clip.estado = "error"`, y **siempre** llamar a
  `_cerrar_si_termino` en el `finally`.
- **Verificación:** un test que fuerza un clip corrupto deja el lote en `error`, no
  colgado en `procesando`.

**Estado: hecho (2026-09-29). Fue el bug más serio de la fase, y no era el del plan.**
- **La premisa del paso era falsa:** el `except` se comía la excepción, así que
  `max_retries=3` **nunca se activó**. Nunca hubo reintentos ni cartas muertas; el fallo
  se perdía en silencio. La verificación del paso ("Dramatiq agota max_retries") daba
  por hecho un mecanismo inexistente.
- `procesar_clip` ahora marca `error`, loguea con traceback, **relanza** y cierra el lote
  en el `finally`. Sin el `raise`, `after_process_message` sale por `if exception is None:
  return` y Dramatiq hace ACK: el mensaje desaparece sin dejar rastro. El `raise` es lo
  que activa la carta muerta.
- `max_retries` baja a **0**, con justificación: medir con ffprobe un fichero que
  acabamos de escribir nosotros es determinista; si no se puede leer, no se va a poder
  leer nunca más, y reintentar solo retrasa el aviso. **Este 0 no es "el valor bueno" ni
  la tabla final**, la fija el Paso 7.5. Avisado en el código, junto con que la carta
  muerta expira a los **7 días** (`DEFAULT_DEAD_MESSAGE_TTL`).
- **Dos cuelgues más del mismo tipo, encontrados al arreglar este:**
  1. El mismo bug un nivel arriba, en `procesar_lote`: un `.docx` corrupto hacía escapar
     la excepción y dejaba el lote en `pendiente` **para siempre, sin error ni log**.
     Ahora avisa y sigue sin ese documento: el contexto es auxiliar y los vídeos son el
     trabajo de verdad. El `except` se dejó **ancho a propósito**, porque un `.docx`
     malformado lanza cuatro clases sin relación (`PackageNotFoundError`, `KeyError`,
     `ValueError`, `ParseError`) y un `except` estrecho que se equivoque reinstala el bug.
  2. `procesar_lote` y `procesar_video` **sí reintentan de verdad** (tienen rutas sin
     capturar), y eso destapó corrupción de datos: `procesar_lote` no era idempotente, así
     que un reintento con Redis caído **duplicaba las filas `Video`**; y `procesar_video`
     se colgaba a sí mismo en la reentrada (encontraba `cortado` y salía sin encolar
     nunca los clips, dejando el lote en `procesando` para siempre). Ambos corregidos:
     guarda de estado en el lote, y la reentrada reapunta los clips pendientes.
- **Corrección de una afirmación mía que era falsa:** documenté que "los otros dos actores
  tampoco reintentan, mismo `except`". Era falso, y peligroso: habría conducido al Paso
  7.5 a subir unos reintentos que hoy son corrupción de datos. Lo cazó el
  `code-reviewer` leyendo el fuente de Dramatiq. Corregido en el código **y** en el
  docstring del test que lo repetía.
- Verificación: 14 tests en `tests/test_cercar_lote.py` + 11 de idempotencia en
  `tests/test_idempotencia.py`. Mutation testing sin supervivientes: `raise`→`return`,
  `max_retries` 0→3, quitar el `finally`, quitar la guarda de idempotencia, invertirla, y
  poner la reentrada de `procesar_video` en `return` seco — **todos** hacen fallar la
  suite. El `return` seco es el mutante que reproduce el cuelgue original.
- **Para el Paso 7.5:** aquí está la tabla de reintentos, con el `fallback` ya resuelto pero sin el consumidor. `procesar_lote` 3 y `procesar_video` 2 reintentan de verdad y son idempotentes ahora; `procesar_clip` 0 y va a la carta muerta. Falta decidir quién consume esa cola y qué se hace con los ficheros huérfanos de un upload a medias.

### Paso 0.7 — Cache del modelo en volumen
- `faster-whisper` descarga el modelo en el primer uso dentro del contenedor. Sin volumen,
  **cada `docker compose up --build` vuelve a descargar 1–3 GB**.
- Fijar `HF_HOME` (o el path equivalente) a un directorio montado en el volumen de storage.
- **Verificación:** tras un segundo `docker compose down` + `up`, no se vuelve a descargar
  (se comprueba con `ls` del directorio de cache antes y después).

**Estado: hecho (2026-09-29), salvo la verificación literal.**
- `HF_HOME=/storage/modelos` en el bloque `environment:` de api y worker, sobre el
  volumen de storage, que es persistente.
- **Ojo, y es un detalle que confunde:** `HF_HOME` en `app/config.py` es **decorativo**.
  `faster-whisper` y `transformers` leen la variable de entorno por su cuenta, no este
  código. Lo que la fija de verdad es el bloque `environment:` del compose, que pisa al
  `env_file`. Está anotado en el propio código para que nadie lo lea como una garantía
  que no da.
- **La verificación del paso sigue pendiente de forma literal:** no se puede comprobar
  hasta que haya un modelo que descargar, y el modelo llega en la Fase 3. La fecha en que
  se complete de verdad es la del primer `faster-whisper` en el contenedor, no hoy.

### Paso 0.8 — Fijar versiones de `requirements.txt`
- Hoy no hay ni un pin (salvo `sqlalchemy>=2.0`) → builds no reproducibles.
- Fijar versiones exactas de todo, incluidas las de `faster-whisper` y `ctranslate2`
  cuando se añadan en la Fase 3.
- **Verificación:** el fichero no contiene ninguna dependencia sin versión.

**Estado: hecho (2026-09-29), verificado.**
- Las 9 dependencias directas con versión exacta, incluidas las transitivas que cambian
  entre builds.
- Verificado por rebuild limpio: todas las versiones resueltas reproducen las capturadas.
- `requirements-dev.txt` aparte para las de test (`pytest==9.1.1`, `pytest-asyncio==1.4.0`),
  para que las de desarrollo no entren en la imagen de producción.
- **Pendiente, con fecha conocida:** `faster-whisper` y `ctranslate2` se pinean en la
  Fase 3, que es cuando se añadan. Es el propio paso quien lo dice.

---

## Fase 1 — Modelo de datos y migraciones

> Hoy solo hay `Base.metadata.create_all` en el arranque: **no hay Alembic**. Cualquier
> cambio de columna es hoy un "drop table y rezar". Esta fase lo arregla antes de tocar el
> esquema.

### Paso 1.1 — Introducir Alembic
- Configurar Alembic contra `DATABASE_URL`, con el arranque actual como revisión inicial
  (`alembic stamp head`) para no recrear tablas existentes.
- **Verificación:** `alembic upgrade head` sobre una BD ya inicializada por `init_db` no
  altera nada, y `alembic current` coincide con `head`.

### Paso 1.2 — Columnas nuevas en `videos`
```python
transcripcion_texto : Text | None        # transcripción completa legible
transcripcion_json  : JSON | None        # salida cruda de Whisper (segments + words)
modelo_whisper      : String(50) | None  # "medium", para saber con qué se generó
```
- Y ampliar el estado: `pendiente | transcribiendo | cortando | cortado | error`.
- **Verificación:** migración aplicada y reversible; `alembic downgrade` deja la tabla como
  estaba.

### Paso 1.3 — Columnas nuevas en `clips`
```python
inicio_seg : Float    # marca de inicio de la frase EN EL VÍDEO ORIGINAL
fin_seg    : Float    # marca de fin de la frase EN EL VÍDEO ORIGINAL
texto      : Text     # la frase transcrita
```
- `inicio_seg`/`fin_seg` son el contrato nuevo: es lo que permite verificar que el
  minivideo empieza y termina donde debe.
- **Verificación:** los clips de un vídeo de ejemplo tienen marcas ascendentes y sin
  solapes.

### Paso 1.4 — Padding persistido y columnas de QA
En `lotes`:
```python
frase_pad_ini : Float  # aplicado a este lote
frase_pad_fin : Float
frase_pad_def : Float  # el valor por defecto de configuración, para ver si el lote lo alteró
```
En `clips`:
```python
qa_estado         : String(20) | None  # null | ok | sospechoso | indeterminado
qa_desv_ini_ms    : Integer | None     # cuánto nos pasamos de tarde al inicio
qa_desv_fin_ms    : Integer | None     # idem al final
qa_similitud      : Float | None       # similitud de la etapa 2
qa_intentos       : Integer            # nº de auto-correcciones (tope 1)
```
- **Verificación:** un lote recién creado hereda el padding de configuración y registra
  también el valor por defecto, aunque no se haya tocado nada.

### Paso 1.5 — Índice de `pgvector` (decidir ahora, aplicar después)
- La columna `embedding Vector(1024)` existe y **no tiene índice ANN**. Cuando empiece el
  trabajo con embeddings será un full scan.
- **Decidir y documentar** HNSW vs IVFFlat aunque el índice se aplique más adelante.
- **Verificación:** la decisión está escrita en `DECISIONS.md` con su porqué.

---

## Fase 2 — Extracción de audio

### Paso 2.1 — `ffmpeg_utils.extraer_audio_16k(ruta) -> str`
- Extraer a **wav PCM mono 16 kHz** (lo que Whisper espera) a un fichero temporal.
- Escritura atómica: escribir en `.tmp` y renombrar, para que un corte de ejecución no
  deje un wav truncado que luego se lea como válido.
- Limpiar el temporal al terminar.
- **Verificación:** `ffprobe` sobre el wav devuelve `16000 Hz`, 1 canal, y una duración
  igual (±0.1 s) a la del vídeo original.

### Paso 2.2 — Fixture de vídeo de prueba
- Vídeo corto con voz real, en `ingestion-service/tests/fixtures/`.
- Es la base de las fases 3, 5, 6 y 9.
- **Verificación:** el fixture se regenera con un comando documentado (no hay que buscarlo
  a mano).

---

## Fase 3 — Transcripción con faster-whisper

### Paso 3.1 — Dependencia y configuración
- Añadir `faster-whisper` a `requirements.txt` **con versión fijada** (Paso 0.8).
- Nuevas variables en `config.py` y `.env.example`:
  - `WHISPER_MODEL` (por defecto **`medium`**; `large-v3` disponible para calidad)
  - `WHISPER_DEVICE` (`cpu`)
  - `WHISPER_COMPUTE_TYPE` (`int8`)
  - `WHISPER_CPU_THREADS` (vacío = todos los núcleos)
- **Verificación:** `.env.example` documenta las cuatro y el servicio arranca sin ninguna.

### Paso 3.2 — `app/transcriber.py` con modelo singleton
- Carga **perezosa y única** del modelo a nivel de módulo: cargar el modelo cuesta 10–30 s
  y bastante RAM, y con `--processes 1` solo se paga una vez.
- API: `transcribir(wav_path) -> Segmentos`.
- **Verificación:** dos llamadas consecutivas en el mismo proceso no vuelven a cargar el
  modelo (el log de "modelo cargado" aparece una sola vez).

### Paso 3.3 — Parámetros que importan
- `word_timestamps=True` — sin esto no hay marcas por palabra.
- `vad_filter=True` (Silero VAD) — **el mayor divisor de calidad**: recorta los silencios
  para que Whisper reciba audio que empieza en un inicio de voz, y las marcas caen mucho
  más cerca de la realidad. Sin VAD, los clips empiezan a media palabra.
- `condition_on_previous_text=False` — reduce los bucles de alucinación sobre silencio o
  música.
- `language` fijada a `es` por defecto, configurable.
- **Verificación:** con el fixture, medir (no suponer) la desviación real entre la marca
  de Whisper y el inicio de voz. Ese número es el que dimensiona el padding.

### Paso 3.4 — Persistencia e idempotencia
- Guardar `transcripcion_texto` + `transcripcion_json` en `videos`.
- **Guarda de idempotencia:** si `transcripcion_texto` ya existe, **no volver a
  transcribir**. Es lo que evita pagar minutos de CPU en cada reintento de Dramatiq.
- **Verificación:** lanzar dos veces la misma tarea → la segunda es un no-op, y se ve en el
  log.

### Paso 3.5 — Estados y errores
- `videos.estado = "transcribiendo"` al empezar; `"error"` + columna `error` si falla.
- `max_retries` para esta fase: **0 o 1** (reintentar una transcripción de 10 min cuesta
  minutos de CPU; con la guarda del 3.4 el reintento solo se paga si falló *antes* de
  persistir).
- **Verificación:** un vídeo con audio corrupto deja el vídeo en `error` con mensaje
  legible, y el lote se cierra como `error` en vez de quedarse colgado.

### Paso 3.6 — Script de prueba sin cola
- Un `python -m app.transcriber <ruta>` que transcriba un fichero local e imprima los
  segmentos con sus marcas y la desviación de onset medida.
- **Motivo:** da feedback en segundos sin levantar Postgres, Redis ni la cola. Es la
  diferencia entre depurar en minutos o en horas.
- **Verificación:** `python -m app.transcriber tests/fixtures/x.mp4` imprime las frases con
  `inicio`/`fin` y la desviación, sin tocar ningún servicio.

---

## Fase 4 — Derivar las frases

> Aquí se fija la decisión de producto pendiente: **qué es una "frase"**. La función es
> pura y sin dependencias, para poder testearla en milisegundos y poder iterar sin
> transcribir un vídeo de 10 minutos cada vez.

### Paso 4.1 — `segmentar_frases(segmentos, ...) -> list[Frase]`
- Función pura, sin BD ni FFmpeg, en un módulo nuevo (p. ej. `app/frases.py`).
- `Frase = {inicio_seg, fin_seg, texto}`.
- **Verificación:** se puede testear en <1 s importando el módulo, sin modelo ni Docker.

### Paso 4.2 — Regla de corte (defaults ya decididos)
- Partir de los `segments` que devuelve Whisper y **dividir los largos** por puntuación de
  final de frase (`.` `?` `!`) y, si aún son largos, por `,` `;` `:`.
- Tope de duración `MAX_FRASE_SEG = 20` s y mínimo `MIN_FRASE_SEG = 1.5` s. Por debajo del
  mínimo se fusiona con la frase vecina, porque un clip de 0.4 s no es un minivideo
  publicable.
- Cambiar estos defaults más adelante es cambiar dos números, no reescribir la función.
- **Verificación:** sobre el fixture, ninguna frase supera el tope y ninguna queda por
  debajo del mínimo.

### Paso 4.3 — Normalización de bordes
- Las marcas de Whisper **no son exactas al frame**: el segmento suele empezar tarde (se
  pierde la primera sílaba) y terminar tarde (incluye silencio).
- Aplicar el padding **leído de `lotes.frase_pad_ini` / `frase_pad_fin`**, no de una
  constante, y acotado siempre a la duración real del vídeo.
- No dejar dos frases solapadas ni huecos.
- **Verificación:** test que comprueba que `inicio[i+1] >= fin[i] - epsilon` y que ningún
  `inicio` es negativo ni ningún `fin` excede la duración del vídeo.

### Paso 4.4 — Tests unitarios
- Casos: segmentación normal, segmento largo con comas, silencio al principio/fin del
  vídeo, frase más corta que el mínimo, vídeo de un solo segmento.
- **Verificación:** todos verdes sin Docker ni modelo.

---

## Fase 5 — Corte por marcas de frase

### Paso 5.1 — `ffmpeg_utils.cortar_por_frases(origen, carpeta, [(ini, fin)])`
- Sustituir el `cortar_y_normalizar` actual, que calcula los cortes desde `CLIP_SECONDS`.
- **Reutilizar el mismo filtro de normalización** (`scale`+`pad`+`fps=30`+`setsar=1`,
  `libx264`, `yuv420p`, `aac`). Ese filtro es lo que garantiza que todos los clips puedan
  concatenarse después; no se toca.
- Un `ffmpeg` por frase, con `-ss INI -to FIN`.
- **Verificación:** la duración de cada clip coincide con `fin_seg - inicio_seg` (±0.3 s,
  tolerancia de un frame a 30 fps).

### Paso 5.2 — Numeración y nombres
- Nombres `clip_%05d.mp4` ordenados por `indice` (que ya ordena por posición en la frase).
- **Verificación:** `indice` de la BD coincide con el orden de las marcas de tiempo.

### Paso 5.3 — Anotar la optimización (no es v1)
- Con muchas frases son muchos procesos ffmpeg. La alternativa es **un solo paso** con
  `-vf "select='between(t,A,B)+between(t,C,D)',setpts=N/FRAME_RATE/TB"`.
- Se documenta aquí, **no se implementa en v1**: a cambio de complicar el manejo de
  errores, ahorra procesos. La decisión se revisa si un lote de 10 min se hace lento de
  verdad.
- **Verificación:** anotado en `DECISIONS.md` con la razón de posponerlo.

### Paso 5.4 — Casos límite
- **Vídeo sin voz** (solo música, o silencio): **ya decidido** → `videos.estado =
  "sin_voz"`, 0 clips, log de aviso, y **el lote se cierra igual** (no como `error`). Hay
  que añadir `sin_voz` a los estados de `videos` y a la lista de "ya cerrados" de
  `_cerrar_si_termino` (Paso 7.3).
- **Frases que solapan** tras el padding: recortarlas para no repetir contenido.
- **Clips de duración ~0**: descartarlos con log, no crear filas huérfanas.
- **Verificación:** cada caso límite tiene un test o una decisión escrita.

---

## Fase 6 — QA automático de bordes (sin nadie en el bucle)

> **Por qué esta fase existe.** Todos los tests que se mantienen son tautológicos: comprueban
> que el clip se cortó donde dicen los metadatos, y si la marca de Whisper va 200 ms tarde
> los metadatos van tarde y el test pasa igual. El error está aguas arriba. Por eso hace
> falta medir la geometría real del corte, y por eso el único test que de verdad valida
> esto es un número, no una aserción de igualdad.

**El agente LLM no entra aquí.** Un modelo de visión no puede oír una sílaba truncada, y
una pipeline no puede depender de un "me parece bien" no determinista. Un agente de IA sí es
la herramienta adecuada en la Fase 10, para otra pregunta ("¿este clip dice lo que el
guion pedía?").

### Paso 6.1 — Etapa 1: medir la geometría del corte (`app/qa.py`)
- Función pura, **numpy, sin modelo ni FFmpeg**: leer el audio 16 kHz del vídeo original,
  calcular el RMS en tramas de ~10 ms, y localizar el **onset real** de la frase dentro de
  una ventana (±1.5 s) alrededor de `inicio_seg`.
- Comparar con el corte realmente aplicado (`inicio_corte = inicio_seg - pad_ini`):

| Condición | Significado | Veredicto |
|---|---|---|
| `inicio_corte <= inicio_real` | cortamos antes de que empezara la voz | `ok` |
| `inicio_corte > inicio_real` | **cortamos dentro de la palabra** | `sospechoso` + `desv = inicio_corte - inicio_real` |
| la energía nunca cruza el umbral en la ventana | no se puede medir (ruido, música) | `indeterminado` (nunca auto-corregir) |
| `inicio_corte` más de ~1 s antes de `inicio_real` | mucho aire muerto: mala detección de borde | `sospechoso` |

- Simétrico al final: si el audio sigue por encima del umbral en el último frame del
  clip, la frase quedó truncada.
- **Coste:** ~1 ms por clip. Cero GPU, cero dependencias nuevas.
- **Verificación:** un test sintético genera una señal con un onset conocido en t=3.00 y
  comprueba que la medición lo encuentra con ±30 ms. Un test de ruido/música da
  `indeterminado`, no un veredicto inventado.

### Paso 6.2 — La etapa 1 informa del valor de la constante
- El `desv` máximo del lote es exactamente **cuánto padding falta**. La corrección real es
  `frase_pad_ini += desv_max + margen`, no un parche por clip.
- **Verificación:** el informe del lote propone un `pad_ini` y ese valor, aplicado a un
  lote nuevo, deja 0 clips sospechosos.

### Paso 6.3 — Política: reportar, con 1 auto-corrección acotada en la etapa 1
- Etapa 1 sospechosa y `qa_intentos == 0` → **re-cortar ese clip con** `pad + desv + 0.05`,
  incrementar `qa_intentos`, y volver a medir. **Tope 1 intento.**
- Etapa 1 sospechosa y `qa_intentos == 1` → marcar y **no volver a intentar**.
- `indeterminado` → marcar, nunca auto-corregir.
- Actor nuevo `recortar_clip(clip_id)`, reutilizable también para el recálculo manual de un
  lote completo.
- **Verificación:** un lote con padding claramente insuficiente produce clips marcados con
  `qa_intentos = 1` y **ninguna** tarea entra en bucle; un lote con padding correcto no
  re-corta nada.

### Paso 6.4 — Etapa 2: round-trip ASR, solo sobre sospechosos
- Transcribir **el clip ya generado** con un modelo pequeño (`small`) y comparar con la
  frase esperada (`rapidfuzz.fuzz.ratio` o `difflib`). Umbral por defecto `0.85`.
- Solo se ejecuta si `CALIDAD_QA = "alta"`. Por defecto `"rapida"` → la etapa 2 no corre,
  y un lote sale en el tiempo de la transcripción más el corte.
- **Aviso que hay que documentar en el código:** Whisper reconstruye texto plausible, así
  que una sílaba truncada **se la tapa** y la comparación puede no notarla. Por eso la
  etapa 2 va *detrás* de la 1 y no al revés: la 1 mide geometría, la 2 detecta pérdida de
  contenido. Ambas numéricas.
- **Verificación:** un clip al que se le borra la primera palabra da similitud < 0.85.

### Paso 6.5 — Informe de QA en el estado del lote
- `GET /lotes/{lote_id}` añade:
```json
"qa": {
  "total": 58, "ok": 55, "sospechosos": 3, "indeterminados": 0,
  "desviacion_ini_p50_ms": 40, "desviacion_ini_p95_ms": 120,
  "pad_ini_sugerido": 0.19
}
```
- Ese `pad_ini_sugerido` es el número que hace innecesario revisar clips a ojo: dice
  exactamente qué constante cambiar.
- **Verificación:** el informe de un lote con desviación alta propone un padding mayor que
  el aplicado, y al aplicarlo el siguiente lote sale limpio.

### Paso 6.6 — Calibrar los umbrales una vez (el único paso humano que queda)
- Con 3–5 clips y audio conocido, comprobar que los umbrales marcan lo que deben. **Un QA
  que miente es peor que no tener QA**, y eso no se detecta sin mirarlo una vez.
- **Verificación:** umbrales finales escritos en el `README.md` con la justificación.

---

## Fase 7 — Reencadenar la máquina de estados

### Paso 7.1 — `procesar_video` absorbe la transcripción
```
pendiente → transcribiendo → cortando → cortado | error
   ffprobe → extraer audio → transcribir → derivar frases → cortar → crear clips
```
- `_cerrar_si_termino` debe considerar también `transcribiendo` como "pendiente".
- **Verificación:** `GET /lotes/{id}` muestra el vídeo pasando por `transcribiendo`.

### Paso 7.2 — `procesar_clip` se simplifica
- Ya no transcribe: solo mide la duración real del clip y marca `listo`.
- **Sobre su `max_retries` (2026-09-29): este paso estaba mal planteado.** Decía "bajarlo de
  3 a 1 porque ahora es trabajo trivial", cuando el valor real ya era **0** y está en 0 por
  un motivo mejor: medir con ffprobe un fichero que el propio servicio acaba de escribir es
  determinista, así que si falla no va a funcionar nunca más.
- El razonamiento correcto para el resto de actores **no es "trabajo trivial"**, es
  "trabajo determinista sobre algo que controlamos" (→ 0) frente a "trabajo que depende
  del mundo exterior" (→ reintentos útiles). Copiar el 0 por analogía al resto sería el
  error. El Paso 7.5 es quien fija la tabla, y la Fase 0 ya le dejó el razonamiento.
- **Lo que este paso sí tiene que decidir:** si `procesar_clip` conserva el `raise` a la
  carta muerta. Al no transcribir, su único fallo posible es el fichero corrupto, y eso
  quizá sea trabajo de reintento cero **y** de no reencolar nunca.
- **Verificación:** el log muestra que `procesar_clip` no invoca a Whisper.

### Paso 7.3 — `_cerrar_si_termino` con los estados nuevos
- Contar vídeos en `[pendiente, transcribiendo, cortando]`. `sin_voz` cuenta como **ya
  cerrado** (no bloquea el cierre del lote) pero como aviso en el resumen.
- **Verificación:** un lote con 2 vídeos, uno con voz y otro mudo, cierra en `listo` con
  0 clips del segundo y un aviso explícito.

### Paso 7.4 — Idempotencia de `procesar_lote`
- ~~Hoy **no la tiene**~~ **RESUELTO por adelantado en la Fase 0 (2026-09-29).** La premisa
  era correcta, pero el módulo docstring prometía "cada paso es una función pequeña e
  idempotente" cuando solo `procesar_video` la respetaba. Al arreglar el bug del 0.6 se
  descubrió que `procesar_lote` **no la tenía** y que sus reintentos eran reales: un
  reintento creaba un **segundo juego de filas `videos`** para los mismos archivos. Ya
  lleva `if lote.estado != "pendiente": return`, con 4 tests que lo cubren.
- **Lo que queda aquí, y no es la guarda:** la ventana entre el `commit` de los `Video` y el
  `procesar_video.send` sigue sin poder ser atómica. La guarda evita la **duplicación**, no
  la **pérdida**: si el worker muere justo en ese hueco, el lote queda en `procesando` con
  vídeos ya creados y **nadie encolado**. Cerrarlo de verdad es un outbox transaccional o un
  barrido de lotes huérfanos, y es trabajo de esta fase, no de la Fase 0.
- **Verificación:** ya cubierta (`test_procesar_lote_dos_veces_no_duplica_los_videos`, y el
  mutante que quita la guarda hace fallar 4 tests). Pendiente en este paso: decidir y
  aplicar el mecanismo para la ventana commit/send.

### Paso 7.5 — `max_retries` diferenciados
- Transcripción: 0–1. Corte: 2. Lote: 3. QA/recorte: 1.
- **Verificación:** la tabla de reintentos está escrita y es intencionada.

**Lo que la Fase 0 aporta a esta decisión (2026-09-29), para no repetir el error:**
- Los tres actores tienen hoy `max_retries` reales y **distintos** (3, 2, 0), y los tres
  reintentan de verdad **por rutas distintas**. La tabla de arriba no es arbitraria: sale de
  qué parte del trabajo es determinista y qué parte depende del mundo exterior.
- **Un actor con reintentos DEBE ser idempotente.** Es la lección de la Fase 0: un
  `max_retries` alto sin guarda es un duplicador de filas, no un mecanismo de robustez.
- El `0` del clip **no es un valor bueno que deba copiarse**: significa "trabajo
  determinista sobre un fichero que acabamos de escribir nosotros". La transcripción de la
  Fase 3 lee de disco un fichero que sube el usuario, así que **su caso es otro** y necesita
  su propio razonamiento, no heredar el 0 por analogía.
- **La carta muerta expira a los 7 días** (`DEFAULT_DEAD_MESSAGE_TTL` de Dramatiq). Si esta
  tabla va a tener un consumidor, ese consumidor tiene que existir antes de que la primera
  semana de producción se le escapen los mensajes.
- La Fase 0 dejó también la infraestructura: `raise` en `procesar_clip` es lo que hace que
  el mensaje llegue a la cola de cartas muertas en vez de desaparecer con un ACK.

---

## Fase 8 — API y observabilidad

### Paso 8.1 — `GET /lotes/{id}/clips` más rico
- Añadir `inicio_seg`, `fin_seg`, `texto` y las columnas de `qa_*` a cada clip. Es lo que
  hace **verificable** el objetivo.
- Añadir paginación: hoy devuelve miles de filas en un solo JSON.
- **Verificación:** la respuesta de un vídeo de ejemplo permite reconstruir la frase, su
  posición y su veredicto de QA.

### Paso 8.2 — `GET /lotes/{id}/transcripcion`
- Devolver la transcripción completa con las marcas por frase.
- **Verificación:** incluye el texto de los documentos (`.txt`/`.docx`) que se subió como
  contexto, ahora que por fin hay algo que lo consuma.

### Paso 8.3 — `POST /lotes/{id}/reintentar`
- Hoy un vídeo en `error` es **terminal**: no hay forma de relanzarlo sin tocar la BD a
  mano.
- Poner en cola solo los vídeos/clips en `error`. Un parámetro `pad_ini` opcional permite
  relanzar el lote con otro padding (usa `recortar_clip` de la Fase 6).
- **Verificación:** reintentar un vídeo fallido lo devuelve a `listo` sin tocar el resto.

### Paso 8.4 — Logs estructurados
- Cada línea con `lote_id` / `video_id` / `clip_id` y la fase. Incluir el resumen de QA al
  cerrar el lote.
- **Verificación:** un `grep` por `lote_id` reconstruye la historia completa del lote.

### Paso 8.5 — Progreso real
- Tras el corte se conoce el **total** de clips; antes no. Ajustar `GET /lotes/{id}` para
  que el total sea fiable desde que existe.
- **Verificación:** el progreso nunca retrocede, ni queda en 0 clips con el lote `listo`.

---

## Fase 9 — Verificación de extremo a extremo

### Paso 9.1 — Recorrido manual completo
```
docker compose up --build
curl -X POST localhost:8001/lotes -F "files=@entrevista.mp4"
curl localhost:8001/lotes/1            → poll hasta listo
curl localhost:8001/lotes/1            → bloque "qa"
ls storage/clips/lote_1/video_1/
curl localhost:8001/lotes/1/clips
```
- **Verificación:** los pasos documentados en el `README.md` del servicio funcionan tal
  cual, escritos.

### Paso 9.2 — La validación humana pasa a ser un solo paso de calibración
- Abrir 3–5 clips **una vez**, para validar que los umbrales de la Fase 6 dicen la verdad
  (ver Paso 6.6). Después, la validación es la métrica, no el oído.
- El criterio, escrito en el `README.md`: *"un clip es correcto si la voz empieza en su
  primer fotograma útil y la frase se completa en el último"*.
- **Verificación:** los umbrales de QA de la Fase 6 dicen `ok` en esos clips, y dicen
  `sospechoso` en un clip al que se le recorta media palabra a propósito.

### Paso 9.3 — Rendimiento medido en la VPS
- Cronometrar: subida, transcripción de 10 min, corte, QA. Comparar `medium` contra
  `large-v3` con el fixture.
- **Salida:** los números reales escritos en el `README.md`, para que la decisión de
  modelo no se base en estimaciones.
- **Verificación:** una tabla con los tiempos medidos, no estimados.

### Paso 9.4 — Configuración de hilos del worker
- Probar `--threads 2` con `WHISPER_CPU_THREADS=3` frente a `--threads 1` con 6.
- **Motivo:** `faster-whisper` usa por defecto todos los núcleos, así que 4 hilos × 6
  cores se pelean. Hay que medir, no suponer.
- **Verificación:** la configuración elegida está en `docker-compose.yml` con un
  comentario explicando el porqué de los números.

### Paso 9.5 — Suite de tests
- Unitarios de `frases` y `qa` (rápidos, sin Docker, sin modelo) y de `storage`/`main`.
- Integración marcada como lenta, con el fixture.
- **Verificación:** un comando documentado los ejecuta todos.

### Paso 9.6 — Documentación
- `notas.txt` describe el flujo **antiguo** (clips de 10 s fijos): queda obsoleto y hay que
  reescribirlo o borrarlo.
- `README.md` del servicio: flujo nuevo, puertos, modelos, calidad de QA, reintentos.
- `PROGRESS.md` y `DECISIONS.md` al día.
- **Verificación:** quien lea `notas.txt` entiende el flujo que hay hoy.

---

## Fase 10 — Fuera de alcance (declarado, no olvidado)

- [ ] Embeddings `bge-m3` (la columna `Vector(1024)` ya está lista; faltan el modelo y el índice)
- [ ] Descripción visual con modelo de visión
- [ ] **Agente LLM para control de calidad semántico** ("¿este clip dice lo que el guion pedía?") — aquí sí es la herramienta adecuada
- [ ] **Usar el documento de contexto para corregir nombres propios** con la transcripción (ver decisión abierta nº1)
- [ ] Ficha JSON estructurada por clip (`clips.ficha`)
- [ ] Descarga/streaming de los clips por la API
- [ ] Multi-tenancy (`usuario_id`): hoy **ninguna tabla lo tiene**
- [ ] Política de retención de `storage` y aviso de disco > 80 %
- [ ] Limpieza de huérfanos (un 413 a mitad de subida deja archivos y un lote colgado)
- [ ] `GET /lotes/{id}/clips` con descarga en ZIP
- [ ] Integración con el `backend` principal (usuario, auth, panel)

---

## Trampas conocidas (leer antes de escribir código)

1. **Las marcas de Whisper no son exactas al frame.** El segmento suele empezar
   normalmente tarde y se pierde la primera sílaba. Con VAD + padding baja mucho, pero
   **hay que medirlo (Fase 6), no suponerlo.**
2. **Los tests de igualdad no detectan un borde malo.** `duración ≈ fin − inicio` pasa
   igual con el borde bien que mal, porque la marca es lo que se comparó consigo misma.
   Por eso la Fase 6 mide la geometría real.
3. ~~**La cola y la BD pueden discrepar.**~~ **RESUELTO en la Fase 0 (2026-09-29).** Si el
   worker moría entre el `commit` de los `Video` y el `send`, Dramatiq reintentaba y
   duplicaba. `procesar_lote` ya lleva guarda de idempotencia y `procesar_video` reapunta
   los clips pendientes en la reentrada. Lo que se ***mantiene*** como riesgo real es la
   ventana entre el `commit` y el `send`, que sigue sin poder ser atómica sin un outbox;
   el Paso 7.4 la revisa.
4. **`_cerrar_si_termino` es lo único que cierra el lote.** Cualquier tarea nueva que no la
   llame deja el lote colgado. La regla es: **siempre en el `finally`**.
5. **Cargar el modelo cuesta 10–30 s y RAM.** Con `--processes 1` se paga una vez. **No
   subir `--processes`** o se paga N veces.
6. **Cuidado con el oversubscription de hilos.** `faster-whisper` usa por defecto todos
   los núcleos; varios hilos de Dramatiq transcribiendo a la vez se pelean por la CPU. Se
   mide en el Paso 9.4.
7. **El `--processes 1` del worker es deliberado**, no un descuido: es un límite de
   concurrencia para una VPS. La transcripción multiplica el consumo de CPU.
8. **La normalización 720p/30fps es lo que hace funcionar el `concat` de FFmpeg.** Si se
   cambia un parámetro de `cortar_por_frases`, hay que rehacer el paso de validación de la
   Fase 9 entera.
9. ~~**`API_KEY` con default público**~~ **RESUELTO en la Fase 0 (Paso 0.2, 2026-09-29).**
   El servicio ya aborta al arrancar si falta, está vacía o vale el placeholder. No hay
   ninguna clave por defecto en el código, que es lo que importa. Ahora bien: **comparar
   la clave con `!=` es vulnerable a timing**, y lo pendiente es
   `secrets.compare_digest`. No expongas el servicio a internet sin eso.
10. ~~**El puerto 8000 está duplicado**~~ **RESUELTO en la Fase 0 (Paso 0.1, 2026-09-29).**
    El host reparte los puertos: `8000` backend raíz, `8001` ingestion, `5173` frontend. Los
    contenedores pueden seguir ambos en 8000 porque cada uno publica en un puerto distinto.
11. **Duraciones:** en 6 vCPU, un vídeo de 10 min tarda unos 5–7 min con `medium` y
    12–25 min con `large-v3`. El servicio es asíncrono y lo tolera, pero **no es
    instantáneo** y la UI tiene que reflejarlo.
12. **El disco es el segundo cuello de botella** (200 GB, originales + clips, sin
    limpieza). La transcripción es lenta pero finita; el disco se llena para siempre.
13. **Auto-corregir esconde bugs de configuración.** Si el 30 % de los clips necesita
    corrección, el problema es `frase_pad_ini`, no cada clip. Por eso el tope es 1 intento
    y el dato se reporta en el informe del lote.

---

## Decisiones abiertas (ninguna bloquea la Fase 0)

Las cuatro decisiones de producto ya están resueltas y constan en la tabla de arriba. Quedan
estas, que se pueden tomar en su momento:

1. **¿Para qué sirve el documento de contexto?** El `.txt`/`.docx` se sube, su texto se
   extrae a `lotes.contexto_texto` y **nadie lo consume**: con la transcripción real sigue
   siendo código muerto. El uso obvio sería **corregir los nombres propios con el
   documento** (marcas, nombres de producto), que es justo donde Whisper más falla en
   español. Está fuera de este plan porque amplía el alcance, pero conviene decidirlo antes
   de dar el servicio por cerrado.
2. **¿Política de retención de storage?** ¿Cuánto tiempo se guardan originales y clips?
   Afecta al diseño del aviso de disco. (Fase 10)
3. **¿Los clips deben llevar audio siempre?** Hoy `-c:a aac` no garantiza pista de audio en
   los vídeos mudos, y el `concat` exige flujos idénticos (ver análisis previo).
4. **¿Dónde se queda el `TODO.md`?** Hoy está en la raíz, junto a `PROGRESS.md` y
   `DECISIONS.md`. Se puede mover a `ingestion-service/TODO.md`.

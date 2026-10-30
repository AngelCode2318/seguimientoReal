# Old Classic Club

Aplicación web en español para encontrar a los autos del club en un mapa común durante rodadas y reuniones. Entrada con los datos del participante, ubicación voluntaria y actualizaciones sin recargar la página. Preparada para una prueba con hasta **30 autos simultáneos**.

## Ejecutar en Windows

Requiere Python 3.14. Desde PowerShell, dentro de esta carpeta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe app.py
```

Abre **http://127.0.0.1:5000**. Escribe tu alias y auto, opcionalmente año y color, y pulsa **Entrar al mapa**. No hay registro, correo ni contraseña.

Para personalizar la configuración, copia `.env.example` a `.env`. La ejecución local genera una clave temporal si no hay `SECRET_KEY`; para conservar las sesiones entre arranques locales, define una clave propia. Puedes generarla con:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

En Linux/macOS, los comandos equivalentes usan `.venv/bin/python`. `app.py` es únicamente el servidor de desarrollo; Render utiliza Gunicorn.

## Uso

1. Escribe tu **alias** y **auto**, y si quieres **año** y **color**. Pulsa **Entrar al mapa**.
2. Verás los autos que comparten su ubicación. Entrar no publica tu posición.
3. Pulsa **Compartir mi ubicación** y acepta el permiso del navegador. Todos los participantes podrán ver tu auto. No hay validación de membresía del club.
4. Mantén la web visible y la pantalla encendida. La app intentará mantenerla encendida si el navegador y la batería lo permiten.
5. Pulsa **Dejar de compartir** para retirar tu marcador. También se retira al **Salir de la rodada**. Si no hay red, el envío local se detiene y el marcador remoto caduca en un máximo de 60 segundos desde el último dato recibido.

### Reconocer a quien vuelve

- La combinación de **alias, auto, año y color** identifica al participante. Se ignoran mayúsculas y espacios sobrantes; se conservan los acentos. Los opcionales vacíos coinciden entre sí.
- Si la combinación es nueva, se crea un participante. Si ya existe y no está compartiendo desde una conexión válida, se recupera su mismo identificador, se revocan los accesos anteriores y se desconectan sus pestañas anteriores. Esto también ocurre si la persona anterior seguía mirando el mapa sin compartir.
- Si la combinación sigue compartiendo ubicación, la entrada se bloquea y pide cambiar el alias si se trata de otra persona. Tener compartir activado cuenta como activo incluso mientras se espera el primer dato del GPS.
- Recuperar un participante siempre comienza con la ubicación desactivada. Para volver a compartir, pulsa el botón correspondiente.
- Con un acceso vigente en el mismo navegador se vuelve directamente al mapa. Editar el perfil no permite duplicar la combinación de otro participante.

Se trata de identificación por datos del auto, sin comprobación de identidad personal.

**Localizarme** centra el mapa en tu posición sin publicarla. **Ver todo el grupo** encuadra los autos visibles. Seleccionar un participante centra su marcador. El botón del perfil permite actualizar alias y auto.

El botón de **pantalla completa**, en la esquina superior derecha del mapa, muestra únicamente el mapa y sus controles. Pulsa de nuevo para volver o utiliza **Esc**. El seguimiento continúa durante esta vista. Cuando el navegador no permite pantalla completa nativa, el mapa ocupa el área visible de la página y sus barras pueden seguir presentes. La [API de pantalla completa](https://developer.mozilla.org/en-US/docs/Web/API/Fullscreen_API/Guide) depende de la compatibilidad y los permisos del navegador.

La app permite una sola pestaña emisora por participante. Dentro de un acceso vigente, activar compartir desde otra pestaña sustituye a la anterior. La reconexión de una pestaña que seguía compartiendo solicita muestras nuevas; recargar la página deja compartir desactivado.

## Desplegar en Render

1. Sube el código a un repositorio de GitHub, sin `.env`, `.venv` ni archivos SQLite.
2. En Render crea un **Blueprint** conectado a ese repositorio y utiliza el archivo `render.yaml` incluido.
3. Confirma el servicio de tipo **Web Service**, plan **Free**. El Blueprint genera `SECRET_KEY` y configura Python, el puerto, la base temporal y `/healthz`.
4. Cuando termine, abre la URL HTTPS de Render y entra con los datos de los participantes de prueba.

Si creas el servicio manualmente:

| Ajuste | Valor |
| --- | --- |
| Runtime | Python |
| Python | `3.14.0` mediante `PYTHON_VERSION` |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn --workers 1 --threads 100 --bind 0.0.0.0:$PORT --access-logfile - --error-logfile - app:app` |
| Health check | `/healthz` |
| `APP_ENV` | `production` |
| `SECRET_KEY` | Una clave aleatoria larga, diferente del ejemplo |
| `DATABASE_PATH` | `/tmp/oldclassic/club.sqlite3` |

**Mantén un solo proceso y una instancia.** La coordinación de los emisores está en memoria. Aumentar procesos o instancias requeriría almacenamiento y coordinación compartidos.

Render gratuito utiliza almacenamiento efímero: **los participantes, sesiones y posiciones de SQLite pueden perderse al suspenderse, reiniciarse o redesplegarse el servicio**. La base se recrea automáticamente si falta. Al arrancar siempre se eliminan las posiciones anteriores, aunque los participantes sigan en el archivo. No es una solución de persistencia para producción. Consulta las [limitaciones oficiales de Render](https://render.com/docs/free).

### Actualizar desde la versión con cuentas

La primera ejecución migra automáticamente SQLite al esquema versión 2 en una transacción. Conserva los perfiles y sus identificadores; si varias cuentas tenían los cuatro campos equivalentes, conserva el identificador más antiguo. Elimina las columnas de correo y contraseña e invalida los accesos anteriores. Todos vuelven a entrar con el formulario simplificado.

La migración es idempotente: los arranques siguientes conservan las nuevas sesiones vigentes. Si la copia falla, se revierte la transacción y permanece el esquema anterior. No hace falta borrar manualmente el archivo SQLite. Las direcciones antiguas `/login` y `/register` redirigen a la entrada principal.

En celulares, el GPS requiere **HTTPS**. Acceder por una IP local con HTTP no equivale a `localhost` y normalmente no habilita la geolocalización. Utiliza la URL de Render para probar en teléfonos.

## Comportamiento del seguimiento

- El navegador utiliza `watchPosition` y solicita además una muestra nueva cada 10 segundos para autos estacionados. Solo se envían muestras nuevas, como máximo una cada cinco segundos.
- El servidor verifica coordenadas, precisión, antigüedad y orden de las muestras. Ignora la identidad enviada por el cliente: cada posición pertenece a la sesión autenticada.
- Una muestra de más de 15 segundos no se acepta como nueva. El reloj del celular debe estar correctamente configurado.
- A los 30 segundos sin una posición nueva se muestra **Sin actualizar**. Al desconectarse el emisor se muestra **Sin señal**. A los 60 segundos se retira el marcador.
- No hay garantía de GPS en segundo plano ni con la pantalla bloqueada. No se guarda historial de recorridos.
- Los mapas usan Leaflet y OpenStreetMap, con atribución visible y caché normal del navegador. No hay navegación, mapas sin conexión ni descargas masivas. El mapa depende de la disponibilidad del [servicio de teselas de OpenStreetMap](https://operations.osmfoundation.org/policies/tiles/).

## Arquitectura e interfaces

Flask sirve HTML, CSS, JavaScript y API. Flask-SocketIO transmite las posiciones; SQLite usa WAL, conexiones por contexto y transacciones breves. Las bibliotecas de navegador están incluidas en `occ/static/vendor`, con versiones, procedencia y hashes en `manifest.json`; no se descargan durante el arranque ni requieren Node.

| Interfaz | Uso |
| --- | --- |
| `POST /api/enter` | `{alias,car,year?,color?}`; crea o recupera un participante inactivo |
| `POST /api/logout` | Sale de la rodada, revoca el acceso y detiene el envío del auto |
| `GET /api/me` | Perfil actual y token CSRF |
| `PATCH /api/me` | `{alias,car,year?,color?}`; reemplaza los datos editables |
| `GET /api/participants` | Últimas posiciones para participantes con un acceso vigente |
| `GET /healthz` | Disponibilidad del servicio y conexión con SQLite |

`POST /api/enter` devuelve `{user,csrf_token}` con estado `201` para un participante nuevo o `200` al recuperar uno existente. Si la combinación está activa devuelve `409` sin sustituir su conexión. `PATCH /api/me` también devuelve `409` si sus nuevos datos ya pertenecen a otro participante. Los endpoints antiguos `POST /api/login` y `POST /api/register` se retiraron.

Las mutaciones HTTP requieren JSON y `X-CSRF-Token`, obtenido del HTML o del perfil. Al abrir un nuevo acceso cambia el token. Las cookies de sesión son opacas, revocables, HttpOnly y SameSite=Lax; en producción también son Secure. Las sesiones duran siete días y se verifican en cada evento y antes de difundir coordenadas. Hay límites de intentos de entrada.

Socket.IO utiliza la ruta habitual `/socket.io` y requiere `{csrf_token}` en `auth`. El cliente envía `share:start`, `position:update` y `share:stop`, con respuestas de confirmación `{ok,...}`. Una posición incluye `latitude`, `longitude`, `accuracy` en metros y `captured_at` en segundos Unix. El servidor emite `participants:snapshot`, `participant:updated`, `participant:removed`, `share:replaced` y `session:expired`. Las claves de identidad y los tokens de sesión no se incluyen en los participantes.

Esta prueba no incluye aprobación de miembros, salas privadas ni administración.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\load_test.py --report artifacts\load-test.json
.\.venv\Scripts\python.exe tools\load_test.py --https-proxy --clients 2 --rounds 1
.\.venv\Scripts\python.exe -m pip check
```

La prueba de carga crea una instancia temporal propia y 30 participantes sintéticos mediante `/api/enter`. Usa **WebSockets reales**, envía tres rondas separadas por 5,1 segundos y comprueba que cada observador recibe las 90 actualizaciones y conserva 30 marcadores únicos. No modifica la base usada por `app.py`.

La variante `--https-proxy` simula en loopback el encabezado HTTPS del proxy y comprueba que las sesiones con cookies Secure se autentiquen también en Socket.IO. Utiliza exclusivamente participantes sintéticos; no reemplaza una prueba TLS en Render.

Las pruebas automatizadas cubren la entrada de cuatro campos, normalización, recuperación de inactivos, bloqueo de activos, sesiones revocadas y vencidas, CSRF, solicitudes simultáneas, edición del perfil, migración y reversión ante errores, validación de posiciones, frecuencia, múltiples pestañas, reconexión, caducidad y reinicio. El resultado local de la carga no garantiza la capacidad de una instancia gratuita bajo todas las condiciones de red.

Los reportes de carga generados se guardan en `artifacts/`, fuera del repositorio. La verificación visual y del GPS en teléfonos sigue requiriendo dispositivos reales.

### Comprobación pendiente en dispositivos reales

La revisión visual automatizada no se pudo completar: Computer Use detuvo el acceso porque no pudo confirmar la URL del navegador. Tampoco se dispone de Android/iPhone físicos ni de un despliegue Render conectado. Antes de la rodada:

- Abrir la URL HTTPS en Chrome para Android y Safari para iPhone, con dos participantes diferentes.
- Comprobar la entrada, perfil, mapa, lista y controles sin desbordamientos en pantallas pequeñas; verificar que la atribución del mapa siga visible.
- Activar compartir en ambos, moverse y comprobar la actualización en el otro teléfono. Verificar también un auto estacionado.
- Denegar el permiso, apagar temporalmente los datos, reconectar, detener el envío y salir de la rodada. Verificar los estados de señal y la retirada del marcador.
- Intentar entrar con los mismos datos mientras comparte y comprobar el bloqueo. Después de detener el envío, recuperarlos desde el otro teléfono y verificar que el anterior salga del mapa.
- Abrir otra pestaña del mismo participante y comprobar que solo una emite. Recargar y verificar que compartir queda apagado. Verificar también el botón de pantalla completa y la salida con Esc.
- Confirmar que mantener la pantalla encendida funciona cuando está disponible y que no se promete seguimiento al bloquearla.

No se ha publicado ningún servicio ni creado un repositorio remoto desde este proyecto.

# Old Classic Club

Aplicación web en español para encontrar a los autos del club en un mapa común durante rodadas y reuniones. Registro abierto, ubicación voluntaria y actualizaciones sin recargar la página. Preparada para una prueba con hasta **30 autos simultáneos**.

## Ejecutar en Windows

Requiere Python 3.14. Desde PowerShell, dentro de esta carpeta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe app.py
```

Abre **http://127.0.0.1:5000**. Crea tu cuenta y entra al mapa. No hay cuentas ni contraseñas predeterminadas.

Para personalizar la configuración, copia `.env.example` a `.env`. La ejecución local genera una clave temporal si no hay `SECRET_KEY`; para conservar las sesiones entre arranques locales, define una clave propia. Puedes generarla con:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

En Linux/macOS, los comandos equivalentes usan `.venv/bin/python`. `app.py` es únicamente el servidor de desarrollo; Render utiliza Gunicorn.

## Uso

1. Regístrate con correo, contraseña de al menos 10 caracteres, alias y auto. Año y color son opcionales.
2. Verás los autos que comparten su ubicación. Tu correo solo aparece en tu perfil.
3. Pulsa **Compartir mi ubicación** y acepta el permiso del navegador. Todos los registrados podrán ver tu auto. No hay validación de membresía del club.
4. Mantén la web visible y la pantalla encendida. La app intentará mantenerla encendida si el navegador y la batería lo permiten.
5. Pulsa **Dejar de compartir** para retirar tu marcador. También se retira al cerrar sesión. Si no hay red, el envío local se detiene y el marcador remoto caduca en un máximo de 60 segundos desde el último dato recibido.

**Localizarme** centra el mapa en tu posición sin publicarla. **Ver todo el grupo** encuadra los autos visibles. Seleccionar un participante centra su marcador. El botón del perfil permite actualizar alias y auto.

La app permite una sola pestaña o dispositivo emisor por cuenta. Activar compartir desde otro sustituye al anterior. La reconexión de una pestaña que seguía compartiendo solicita muestras nuevas; recargar la página deja compartir desactivado.

## Desplegar en Render

1. Sube el código a un repositorio de GitHub, sin `.env`, `.venv` ni archivos SQLite.
2. En Render crea un **Blueprint** conectado a ese repositorio y utiliza el archivo `render.yaml` incluido.
3. Confirma el servicio de tipo **Web Service**, plan **Free**. El Blueprint genera `SECRET_KEY` y configura Python, el puerto, la base temporal y `/healthz`.
4. Cuando termine, abre la URL HTTPS de Render y registra las cuentas de prueba.

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

Render gratuito utiliza almacenamiento efímero: **las cuentas, sesiones y posiciones de SQLite pueden perderse al suspenderse, reiniciarse o redesplegarse el servicio**. La base se recrea automáticamente si falta. Al arrancar siempre se eliminan las posiciones anteriores, aunque las cuentas sigan en el archivo. No es una solución de persistencia para producción. Consulta las [limitaciones oficiales de Render](https://render.com/docs/free).

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
| `POST /api/register` | `{email,password,alias,car,year?,color?}`; abre sesión |
| `POST /api/login` | `{email,password}`; abre sesión |
| `POST /api/logout` | Revoca la sesión y detiene el envío del auto |
| `GET /api/me` | Perfil privado y token CSRF |
| `PATCH /api/me` | `{alias,car,year?,color?}`; reemplaza los datos editables |
| `GET /api/participants` | Últimas posiciones públicas para usuarios autenticados |
| `GET /healthz` | Disponibilidad del servicio y conexión con SQLite |

Las mutaciones HTTP requieren JSON y `X-CSRF-Token`, obtenido del HTML o del perfil. Al iniciar sesión cambia el token. Las cookies de sesión son opacas, revocables, HttpOnly y SameSite=Lax; en producción también son Secure. Las contraseñas se guardan con scrypt. Las sesiones duran siete días y se verifican en cada evento y antes de difundir coordenadas. Hay límites de intentos para registro y acceso.

Socket.IO utiliza la ruta habitual `/socket.io` y requiere `{csrf_token}` en `auth`. El cliente envía `share:start`, `position:update` y `share:stop`, con respuestas de confirmación `{ok,...}`. Una posición incluye `latitude`, `longitude`, `accuracy` en metros y `captured_at` en segundos Unix. El servidor emite `participants:snapshot`, `participant:updated`, `participant:removed`, `share:replaced` y `session:expired`. Correo, credenciales y sesiones no se incluyen en los participantes.

Esta prueba no incluye aprobación de miembros, salas privadas, administración, verificación de correo ni recuperación de contraseña. Si se pierde la contraseña habrá que usar otra cuenta o reiniciar la base de prueba.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\load_test.py --report artifacts\load-test.json
.\.venv\Scripts\python.exe tools\load_test.py --https-proxy --clients 2 --rounds 1
.\.venv\Scripts\python.exe -m pip check
```

La prueba de carga crea una instancia temporal propia y 30 cuentas sintéticas. Usa **WebSockets reales**, envía tres rondas separadas por 5,1 segundos y comprueba que cada observador recibe las 90 actualizaciones y conserva 30 marcadores únicos. No modifica la base usada por `app.py`.

La variante `--https-proxy` simula en loopback el encabezado HTTPS del proxy y comprueba que las sesiones con cookies Secure se autentiquen también en Socket.IO. Utiliza exclusivamente cuentas sintéticas; no reemplaza una prueba TLS en Render.

Las pruebas automatizadas cubren registro duplicado, acceso incorrecto, CSRF, sesiones revocadas y vencidas, privacidad, validación de posiciones, frecuencia, múltiples pestañas, reconexión, caducidad y reinicio. El resultado local de la carga no garantiza la capacidad de una instancia gratuita bajo todas las condiciones de red.

Verificación local completada: **36 pruebas aprobadas**, dependencias sin conflictos, sintaxis JavaScript válida y 30 conexiones WebSocket con 90 posiciones aceptadas y 90 actualizaciones por observador, sin marcadores duplicados. La confirmación de envío tuvo una mediana de 154 ms y un máximo de 304 ms en esa ejecución local. También pasó la prueba de dos conexiones con proxy HTTPS simulado. Los reportes generados se guardan en `artifacts/`, fuera del repositorio.

### Comprobación pendiente en dispositivos reales

La revisión visual automatizada no se pudo completar: Computer Use detuvo el acceso porque no pudo confirmar la URL del navegador. Tampoco se dispone de Android/iPhone físicos ni de un despliegue Render conectado. Antes de la rodada:

- Abrir la URL HTTPS en Chrome para Android y Safari para iPhone, con dos cuentas diferentes.
- Comprobar el registro, perfil, mapa, lista y controles sin desbordamientos en pantallas pequeñas; verificar que la atribución del mapa siga visible.
- Activar compartir en ambos, moverse y comprobar la actualización en el otro teléfono. Verificar también un auto estacionado.
- Denegar el permiso, apagar temporalmente los datos, reconectar, detener el envío y cerrar sesión. Verificar los estados de señal y la retirada del marcador.
- Abrir otra pestaña de la misma cuenta y comprobar que solo una emite. Recargar y verificar que compartir queda apagado.
- Confirmar que mantener la pantalla encendida funciona cuando está disponible y que no se promete seguimiento al bloquearla.

No se ha publicado ningún servicio ni creado un repositorio remoto desde este proyecto.

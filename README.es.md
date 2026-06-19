# Telegram to Matrix Backfill

[Read in English](README.md)

Importa exportaciones JSON de Telegram Desktop a Matrix/Synapse preservando el
remitente original y la marca temporal de cada mensaje. El progreso se guarda
en SQLite para que las reejecuciones sean idempotentes para los eventos ya
enviados.

## Qué Hace Este Proyecto

- Parsea exportaciones de Telegram Desktop (`result.json`).
- Crea o reutiliza salas Matrix por chat de Telegram.
- Crea o actualiza usuarios Matrix locales para los remitentes de Telegram.
- Envía mensajes usando un token de Matrix Application Service con el MXID y
  timestamp originales.
- Guarda el progreso por mensaje en una base SQLite local.

## Alcance Actual

Tipos de chat soportados:

- `private_group`
- `private_supergroup`
- `personal_chat`
- `saved_messages`

Tipos ignorados:

- `broadcast_channel`
- `public_channel`
- `channel`

## Requisitos

- Python `3.10+`
- `ffmpeg` disponible en `PATH`
- Un homeserver Synapse
- Un registro de Matrix Application Service operativo
- Un token de administrador de Synapse

La CLI espera:

- `--as-token`: token del Application Service usado para llamar a las APIs de
  cliente/media de Matrix.
- `--hs-token`: hoy se acepta por compatibilidad, pero el código no lo usa.
- `--synapse-admin-token`: necesario para crear/actualizar usuarios locales y
  ejecutar operaciones administrativas de salas.

## Instalación

### Instalación de ejecución

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install .
```

### Instalación de desarrollo

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .[dev]
```

## Requisitos Previos de Synapse / AppService

Este proyecto no implementa un servidor Application Service. Asume que ya
existe un AppService registrado y reutiliza su token para hacerse pasar por
usuarios locales mediante peticiones con `user_id=...`.

Documentación oficial de Synapse:

- Documentación actual de Element Synapse:
  `https://element-hq.github.io/synapse/latest/application_services.html`
- Documentación Matrix.org para ramas antiguas de Synapse:
  `https://matrix-org.github.io/synapse/latest/application_services.html`
- API de administración de usuarios de Synapse:
  `https://matrix-org.github.io/synapse/latest/admin_api/user_admin_api.html`

Como mínimo, Synapse necesita un fichero de registro de Application Service y
ese fichero debe figurar en `app_service_config_files` dentro de
`homeserver.yaml`.

Ejemplo mínimo:

```yaml
id: telegram-backfill
url: http://127.0.0.1:9999
as_token: CHANGE_ME_AS_TOKEN
hs_token: CHANGE_ME_HS_TOKEN
sender_localpart: telegram-backfill-bot
namespaces:
  users:
    - exclusive: true
      regex: '@tg_.*:example.com'
    - exclusive: true
      regex: '@telegram-backfill-bot:example.com'
  aliases: []
  rooms: []
```

Y en `homeserver.yaml`:

```yaml
app_service_config_files:
  - /etc/matrix-synapse/appservice-telegram-backfill.yaml
```

Notas importantes:

- `--domain` debe coincidir con el `server_name` local de Synapse, no con la
  URL del homeserver.
- Los MXID generados deben pertenecer a un namespace reservado por el
  AppService.
- `--bot-mxid` debe ser el usuario `sender_localpart` del AppService o cualquier
  otro usuario local dentro del namespace del AppService.
- El código crea usuarios locales usando la API de administración de Synapse
  `PUT /_synapse/admin/v2/users/<user_id>`, así que los MXID remotos/federados
  no sirven como usuarios generados.

## Ficheros de Configuración

Crea copias locales de trabajo a partir de las plantillas canónicas:

```bash
cp templates/user_map.yaml ./user_map.yaml
cp templates/room_map.yaml ./room_map.yaml
```

El repositorio deja intencionadamente solo los ficheros de `templates/`. Tus
`user_map.yaml` y `room_map.yaml` de trabajo deben ser ficheros locales creados
a partir de esas plantillas.

### `user_map.yaml`

Mapeo manual de usuario de Telegram a usuario de Matrix.

Ejemplo:

```yaml
"123456789":
  mxid: "@alice:example.com"
  displayname: "Alice Example"
```

Funcionamiento:

- Clave: ID de usuario de Telegram presente en la exportación (`from_id` /
  `actor_id`).
- `mxid`: usuario Matrix que se usará para ese remitente de Telegram.
- `displayname`: nombre visible que se fijará al asegurar el usuario en Matrix.

Si un usuario de Telegram no aparece en `user_map.yaml`, la herramienta genera
un MXID local con este patrón:

```text
@tg_<display_name_normalizado>_<telegram_user_id>:<domain>
```

Ejemplo:

```text
@tg_alice_example_123456789:example.com
```

Comportamiento importante:

- Los mappings generados son deterministas para el nombre visible actual y el
  ID de Telegram.
- `user_map.yaml` no se autoguarda al finalizar. Se mantiene manual a propósito.
- Si cambia el nombre visible de un usuario entre exportaciones y dependes del
  MXID autogenerado, puedes terminar con un MXID distinto. Para identidades
  estables conviene fijar entradas explícitas en `user_map.yaml`.

### `room_map.yaml`

Mapeo de chat de Telegram a sala Matrix.

Ejemplo:

```yaml
"987654321": "!abcdefg:example.com"
```

Funcionamiento:

- Clave: ID del chat de Telegram.
- Valor: ID de una sala Matrix existente que se quiere reutilizar.
- Si un chat no está mapeado, la herramienta crea una sala Matrix privada.
- Las salas creadas correctamente se persisten en `room_map.yaml`.
- Si una sala mapeada ya no existe en Synapse, el mapping se elimina y la sala
  se vuelve a crear.

## Cómo Se Crean Usuarios y Salas

### Flujo de creación de usuarios

Para cada remitente de Telegram detectado en la exportación:

1. Resuelve el MXID destino desde `user_map.yaml`, o lo genera.
2. Llama a la API de administración de Synapse para crear o actualizar ese
   usuario local.
3. Fija el `displayname` configurado o el nombre de Telegram.
4. Une ese usuario a la sala destino antes de enviar sus mensajes.

El primer autor detectado en cada chat pasa a ser el creador/admin inicial de
la sala desde el punto de vista de esta herramienta.

### Flujo de creación de salas

Para cada chat de Telegram:

1. Consulta `room_map.yaml`.
2. Si no existe mapping, crea una sala Matrix privada con el título del chat.
3. Asegura que `--bot-mxid` esté unido.
4. Asegura que el usuario creador del chat esté unido.
5. Promociona al creador a admin de la sala.
6. Fija la visibilidad del historial a `shared`.
7. Une al resto de remitentes encontrados en ese chat.

## Flujo Completo del Backfill

Para cada mensaje de Telegram soportado:

1. Parsea metadatos del chat, remitente, timestamp, texto y media opcional.
2. Agrupa mensajes por chat de Telegram.
3. Resuelve la sala Matrix destino.
4. Resuelve el MXID del remitente.
5. Prepara el media si hace falta.
6. Envía un evento Matrix usando:
   - `user_id=<sender_mxid>`
   - `ts=<timestamp_original_de_telegram_en_ms>`
7. Guarda el resultado en la base SQLite de checkpoint.

La tabla de checkpoint se indexa por `(telegram_chat_id, telegram_message_id)`.
Los mensajes ya marcados como `sent` se omiten en nuevas ejecuciones.

## Media y Ficheros

Comportamiento real actual:

- Las imágenes se convierten a JPEG.
- El audio se reutiliza tal cual si ya es `.ogg`; si no, se convierte a
  OGG/Opus con `ffmpeg`.
- Los vídeos se convierten a MP4/H.264/AAC con `ffmpeg`.
- Los documentos/ficheros genéricos se suben como `m.file` con el nombre
  original y el MIME detectado.
- Los ficheros temporales convertidos se escriben en un directorio temporal y
  se eliminan automáticamente al terminar el proceso.

Limitaciones importantes:

- Si Telegram exportó el marcador
  `(File not included. Change data exporting settings to download.)`, la
  herramienta no puede subir ese fichero y el mensaje termina sin adjunto.
- Si un mensaje tiene media/documento, la herramienta solo envía el payload del
  fichero. Los captions o textos asociados al mismo mensaje de Telegram no se
  emiten como un segundo mensaje Matrix.
- Los documentos/ficheros no reciben un tratamiento especial aparte de detectar
  el MIME y subirlos como `m.file`. No hay preview, thumbnail ni enriquecido.

## Problemas Conocidos

- La conversión de vídeo es poco fiable en uso real. El código intenta
  transcodificar a MP4/H.264/AAC usando `ffmpeg`, pero cuando falla el mensaje
  se marca como `skipped` y no se envía.
- Si ya sabes que la conversión de vídeo no te aporta nada en tu dataset, usa
  `--skip-videos` para que los mensajes con vídeo se omitan directamente.
- Los documentos/ficheros genéricos solo se soportan como subidas simples
  `m.file`. No hay tratamiento más rico como previews, thumbnails ni extracción
  de metadatos específica por tipo de documento.
- Si Telegram no exportó el fichero real y en el JSON aparece el marcador
  `(File not included...)`, esta herramienta no puede recuperarlo. El mensaje
  se procesa sin ese adjunto.
- Los captions de media no se conservan si el mismo mensaje de Telegram contiene
  texto y adjunto. La implementación actual envía solo el payload del
  media/fichero.
- Los stickers se detectan en el parser, pero la tubería de envío no los
  soporta y por tanto se saltan como media no soportado.
- `user_map.yaml` no se persiste automáticamente, así que conviene curar
  manualmente los usuarios estables si no quieres que los MXID generados
  dependan del nombre visible de Telegram.

## Uso

### Ejecución de prueba

Úsalo primero para validar parseo y configuración:

```bash
python -m telegram_to_matrix.cli \
  --telegram-export /path/to/export/result.json \
  --media-dir /path/to/export \
  --homeserver https://matrix.example.com \
  --domain example.com \
  --as-token CHANGE_ME_AS_TOKEN \
  --hs-token CHANGE_ME_HS_TOKEN \
  --synapse-admin-token CHANGE_ME_ADMIN_TOKEN \
  --bot-mxid @telegram-backfill-bot:example.com \
  --state-db ./checkpoint.sqlite \
  --room-map ./room_map.yaml \
  --user-map ./user_map.yaml \
  --dry-run \
  --verbose
```

### Importación real

```bash
python -m telegram_to_matrix.cli \
  --telegram-export /path/to/export/result.json \
  --media-dir /path/to/export \
  --homeserver https://matrix.example.com \
  --domain example.com \
  --as-token CHANGE_ME_AS_TOKEN \
  --hs-token CHANGE_ME_HS_TOKEN \
  --synapse-admin-token CHANGE_ME_ADMIN_TOKEN \
  --bot-mxid @telegram-backfill-bot:example.com \
  --state-db ./checkpoint.sqlite \
  --room-map ./room_map.yaml \
  --user-map ./user_map.yaml \
  --verbose
```

### Flags importantes

- `--dry-run`: parsea e inspecciona mensajes sin enviar eventos.
- `--bot-mxid`: bot/usuario que actúa al crear salas e invitar miembros.
- `--since-message-id N`: empieza desde un ID concreto de mensaje Telegram.
- `--max-messages N`: limita el total de mensajes procesados.
- `--retry-max-attempts N`: límite de reintentos ante fallos transitorios.
- `--retry-base-delay S`: base del backoff exponencial.
- `--fail-fast`: corta en el primer fallo no recuperable.
- `--skip-videos`: omite mensajes con vídeo sin intentar convertirlos.
- `--verbose`: activa logs detallados.

## Tests

```bash
pytest -q
```

## Utilidades de Mantenimiento

### Purgar chats parcialmente importados del checkpoint

Listar el estado actual del checkpoint:

```bash
python -m telegram_to_matrix.purge --state-db ./checkpoint.sqlite --list
```

Previsualizar chats parciales auto-seleccionados (`sent > 0` y `failed > 0`):

```bash
python -m telegram_to_matrix.purge --state-db ./checkpoint.sqlite --auto-partial
```

Aplicar la purga y eliminar también los mappings de sala:

```bash
python -m telegram_to_matrix.purge \
  --state-db ./checkpoint.sqlite \
  --room-map ./room_map.yaml \
  --auto-partial \
  --remove-room-map \
  --apply
```

### Borrar salas Matrix para una reimportación limpia

Previsualizar el reseteo de una sala:

```bash
python -m telegram_to_matrix.reset_matrix_rooms \
  --homeserver https://matrix.example.com \
  --synapse-admin-token CHANGE_ME_ADMIN_TOKEN \
  --room-map ./room_map.yaml \
  --state-db ./checkpoint.sqlite \
  --chat-id 987654321 \
  --dry-run
```

Aplicar el borrado y esperar a que termine:

```bash
python -m telegram_to_matrix.reset_matrix_rooms \
  --homeserver https://matrix.example.com \
  --synapse-admin-token CHANGE_ME_ADMIN_TOKEN \
  --room-map ./room_map.yaml \
  --state-db ./checkpoint.sqlite \
  --chat-id 987654321 \
  --wait-delete \
  --apply
```

Eliminar también los usuarios `@tg_...` generados por importaciones previas:

```bash
python -m telegram_to_matrix.reset_matrix_rooms \
  --homeserver https://matrix.example.com \
  --synapse-admin-token CHANGE_ME_ADMIN_TOKEN \
  --room-map ./room_map.yaml \
  --state-db ./checkpoint.sqlite \
  --chat-id 987654321 \
  --wait-delete \
  --cleanup-tg-users \
  --apply
```

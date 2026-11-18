# Guía para montar la infraestructura AWS del Receptor AIS

**Para:** el compañero que va a crear la infraestructura.
**Resultado:** una página pública `https://NOMBRE.duckdns.org` con los barcos en el mapa y el receptor en su ubicación exacta. Una API en la misma dirección recibe los datos del PC del receptor y los guarda en una base PostgreSQL en RDS.

El PC ya está listo para enviar los datos: versión 8 del sistema, módulo `nube.py`. Tú solo tienes que montar el lado de AWS siguiendo esta guía en orden. Todo el código del servidor está en el **Anexo B** y ya se probó contra PostgreSQL 16 con el cliente real del PC:

- historial completo: 2526 registros en 0,28 s;
- reenvío completo sin duplicados y sin ninguna diferencia, columna por columna;
- retraso normal de 0,24 s (máximo 0,7 s);
- caída de la base de datos con recuperación automática y sin pérdidas;
- clave incorrecta rechazada (401);
- web pública probada, con protección contra nombres de barco con HTML.

> Tiempo estimado: 1,5 a 2 horas la primera vez.
> Necesitas: la cuenta de AWS, una terminal con `ssh` (en Windows sirve PowerShell) y una cuenta en duckdns.org (entras con Google o GitHub).

---

## 0. Arquitectura

```
 PC del receptor (WSL)                         AWS (región us-east-1)
 ┌───────────────────────┐   HTTPS + clave    ┌──────────────────────────────────────────┐
 │ RTL-SDR → receptor    │ ─────────────────▶ │ EC2 (Ubuntu 24.04)                       │
 │ SQLite local (ais.db) │  POST /api/v1/lote │  Caddy :443 (certificado Let's Encrypt)  │
 │ nube.py: envía en <1 s│                    │    └▶ app.py (FastAPI) 127.0.0.1:8000    │
 │ y reintenta si falla  │                    │          │ SQL (TLS), puerto 5432        │
 └───────────────────────┘                    │          ▼                               │
                                              │  RDS PostgreSQL 16 (privada, sin IP      │
 Cualquier persona ──── https://NOMBRE ─────▶ │  pública; solo la EC2 puede entrar)      │
 (mapa público, sin contraseña)               └──────────────────────────────────────────┘
        NOMBRE.duckdns.org → IP elástica de la EC2
```

- **El PC manda.** Su SQLite es la base principal. Si la EC2 o RDS se caen, el PC guarda todo y reenvía al volver; no se pierde nada. La nube es una réplica.
- **Idempotente.** Un mismo registro puede llegar varias veces (reintentos o actualizaciones). El servidor hace *upsert* por `(estacion, id)` y nunca duplica.
- **Público.** La web y las rutas `/api/v1/publico/*` no tienen contraseña y solo leen datos. Escribir exige la clave `X-API-Key`.
- **Ubicación del receptor.** Se muestra **exacta**, por decisión del dueño del sistema.

## 1. Lo que vas a crear

| Recurso | Nombre sugerido | Notas |
|---|---|---|
| Alerta de presupuesto | `ais-presupuesto` | Avisa por correo si el gasto pasa de 5 USD |
| Grupo de seguridad (web) | `ais-web` | 80 y 443 abiertos; 22 solo desde tu IP |
| Grupo de seguridad (BD) | `ais-rds` | 5432 solo desde `ais-web` |
| Base de datos RDS | `ais-db` | PostgreSQL 16, `db.t4g.micro`, 20 GB, **sin acceso público** |
| Instancia EC2 | `ais-servidor` | Ubuntu 24.04, `t4g.micro` o `t3.micro`, 20 GB gp3 |
| Par de claves SSH | `ais-ssh` | Archivo `.pem`; guárdalo bien |
| IP elástica | – | IP fija para que el dominio no cambie |
| Subdominio DuckDNS | `NOMBRE.duckdns.org` | Apunta a la IP elástica |

### Valores que vas a ir anotando (en un lugar privado)

| Valor | Ejemplo | Quién lo necesita |
|---|---|---|
| `DOMINIO` | `ais-barranquilla.duckdns.org` | Tú y el dueño del PC |
| `IP_ELASTICA` | `3.215.10.20` | Tú (DuckDNS, SSH) |
| `ENDPOINT_RDS` | `ais-db.abc123xyz.us-east-1.rds.amazonaws.com` | Tú |
| Contraseña maestra de RDS (`ais_admin`) | `openssl rand -hex 20` | Solo tú |
| Contraseña de la app (`ais_app`) | `openssl rand -hex 24` | Solo la EC2 |
| `AIS_CLAVE` (clave de la API) | 43 caracteres aleatorios | La EC2 **y** el PC (misma clave) |

> Usa contraseñas **solo con letras y números** (como las que da `openssl rand -hex`). Así no hay que escaparlas dentro de la URL de conexión.

---

## 2. Cuenta, región y costos (antes de crear nada)

1. **Región.** Arriba a la derecha de la consola elige **US East (N. Virginia) `us-east-1`**. Es barata y tiene buena latencia desde Colombia. Crea **todo** en esa misma región.
2. **Seguridad de la cuenta.** Activa MFA en el usuario raíz (*IAM → Security recommendations*). Si puedes, trabaja con un usuario IAM administrador y no con el raíz.
3. **Alerta de gasto.** *Billing and Cost Management → Budgets → Create budget → Use a template → Zero spend budget* o *Monthly cost budget* de 5 USD, con tu correo.
4. **Plan de la cuenta.** Desde julio de 2025, las cuentas nuevas reciben créditos (100 USD, más hasta 100 USD por completar actividades como lanzar una EC2 o crear una base RDS). Hay dos planes:
   - **Free plan:** no cobra nada, pero **la cuenta se cierra a los 6 meses** o cuando se acaban los créditos. Después hay 90 días para pasar a pago antes de que AWS borre todo.
   - **Paid plan:** usa los mismos créditos y luego cobra por uso.

   Si el sistema debe durar más que el semestre, pasa a *Paid plan* antes de que se acabe el plazo. Revisa tu saldo en *Billing → Credits*.

**Costo aproximado si se pagara** (us-east-1, 24 h al día; confírmalo en calculator.aws):

| Recurso | USD/mes aprox. |
|---|---|
| EC2 `t4g.micro` | 6 |
| Disco EC2 20 GB gp3 | 1,6 |
| IP pública IPv4 (0,005 USD/h) | 3,6 |
| RDS `db.t4g.micro` PostgreSQL | 12 |
| Disco RDS 20 GB | 2,3 |
| **Total** | **≈ 25** (los créditos cubren varios meses) |

---

## 3. Grupos de seguridad

*EC2 → Network & Security → Security Groups → Create security group*, en la **VPC por defecto**:

**`ais-web`**, reglas de entrada (*Inbound rules*):

| Tipo | Puerto | Origen | Para qué |
|---|---|---|---|
| SSH | 22 | **My IP** | Administrar la EC2 desde tu PC |
| HTTP | 80 | 0.0.0.0/0 | Let's Encrypt y redirección a HTTPS |
| HTTPS | 443 | 0.0.0.0/0 | Web pública y API |

**`ais-rds`**, reglas de entrada:

| Tipo | Puerto | Origen |
|---|---|---|
| PostgreSQL | 5432 | El grupo **`ais-web`** (escribe `sg-` y elígelo de la lista) |

Las reglas de salida quedan como vienen (todo permitido).

> Si cambia la IP de tu casa y SSH deja de conectar, edita la regla 22 de `ais-web` y vuelve a elegir *My IP*.

---

## 4. Base de datos RDS

*RDS → Databases → Create database*:

| Opción | Valor |
|---|---|
| Creation method | **Standard create** |
| Engine | **PostgreSQL**, versión **16.x** (la más reciente de la 16) |
| Templates | **Free tier** (o *Sandbox*, si es lo que aparece) |
| DB instance identifier | `ais-db` |
| Master username | `ais_admin` |
| Credentials management | **Self managed**; escribe la contraseña maestra y anótala |
| Instance class | `db.t4g.micro` |
| Storage | gp2 o gp3, **20 GiB**; desactiva *Storage autoscaling* (o pon un máximo de 30) |
| Compute resource | *Don't connect to an EC2 compute resource* (lo hacemos con los grupos) |
| VPC | La VPC por defecto (**la misma de la EC2**) |
| Public access | **No** |
| VPC security group | *Choose existing* → **`ais-rds`** (quita `default`) |
| Database authentication | Password authentication |
| Additional configuration → **Initial database name** | **`ais`** ⚠️ si lo dejas vacío no se crea la base |
| Backup retention | El máximo que permita la plantilla (1 a 7 días) |
| Deletion protection | **Activada** |
| Performance Insights / Enhanced monitoring | Desactivados (no hacen falta) |

Pulsa *Create database* y espera unos 10 minutos a que el estado sea **Available**. Entra en `ais-db` → *Connectivity & security* y copia el **Endpoint**: ese es tu `ENDPOINT_RDS`.

---

## 5. Instancia EC2 e IP elástica

*EC2 → Instances → Launch instances*:

| Opción | Valor |
|---|---|
| Name | `ais-servidor` |
| AMI | **Ubuntu Server 24.04 LTS** |
| Architecture / Instance type | 64-bit (Arm) + **`t4g.micro`**, o 64-bit (x86) + **`t3.micro`**; elige la que diga *Free tier eligible* |
| Key pair | *Create new key pair* → `ais-ssh`, RSA, `.pem`. Se descarga una sola vez: **guárdalo** |
| Network settings → Edit | VPC por defecto, *Auto-assign public IP*: Enable, *Select existing security group* → **`ais-web`** |
| Storage | **20 GiB gp3** |

Lánzala. Después:

1. *EC2 → Elastic IPs → Allocate Elastic IP address → Allocate*.
2. Selecciónala → *Actions → Associate Elastic IP address* → instancia `ais-servidor` → *Associate*.
3. Anota esa IP: es tu `IP_ELASTICA`.

> Una IP elástica **sin asociar** también cobra. Si borras la EC2, libera la IP (ver sección 15).

**Conectarte por SSH** (PowerShell en Windows, o una terminal en Linux/Mac):

```bash
ssh -i C:\ruta\a\ais-ssh.pem ubuntu@IP_ELASTICA
```

Si Linux o Mac se quejan de los permisos del `.pem`, ejecuta `chmod 400 ais-ssh.pem`. En Windows, si dice *UNPROTECTED PRIVATE KEY FILE*: clic derecho en el archivo → *Propiedades → Seguridad → Opciones avanzadas* → deshabilita la herencia y deja solo a tu usuario con permiso de lectura.

---

## 6. Dominio DuckDNS

1. Entra en <https://www.duckdns.org> con Google o GitHub.
2. En *sub domain* escribe el nombre (por ejemplo `ais-barranquilla`) → *add domain*.
3. En el campo *current ip* de ese dominio pon la **`IP_ELASTICA`** → *update ip*.
4. Comprueba desde tu PC, unos minutos después: `nslookup ais-barranquilla.duckdns.org`. Debe responder la IP elástica.

Como la IP elástica no cambia, **no** hace falta instalar el actualizador de DuckDNS en la EC2. No compartas el *token* de DuckDNS: con él se puede cambiar a dónde apunta el dominio.

---

## 7. Preparar la EC2

Todo esto va **dentro de la EC2**, conectado por SSH:

```bash
# Sistema al día y herramientas
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3-venv postgresql-client

# 1 GB de swap (la micro tiene 1 GB de RAM)
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Zona horaria de Colombia (solo afecta a los registros del servidor)
sudo timedatectl set-timezone America/Bogota

# Usuario sin privilegios que ejecutará la aplicación, y carpetas
sudo useradd --system --home /opt/ais --shell /usr/sbin/nologin ais
sudo mkdir -p /opt/ais/web /etc/ais
```

Si `apt upgrade` pide reiniciar: `sudo reboot`, espera un minuto y vuelve a conectarte.

---

## 8. Crear las tablas y el usuario de la aplicación en RDS

1. Crea el archivo del esquema con `sudo nano /opt/ais/schema.sql`, pega el contenido del **Anexo B.1** y guarda (Ctrl+O, Enter, Ctrl+X).
2. Prueba la conexión con el usuario maestro (pedirá la contraseña maestra):

```bash
export ENDPOINT_RDS=ais-db.xxxxxxxx.us-east-1.rds.amazonaws.com   # el tuyo
psql "host=$ENDPOINT_RDS port=5432 dbname=ais user=ais_admin sslmode=require" -c "SELECT version();"
```

3. Crea las tablas:

```bash
psql "host=$ENDPOINT_RDS port=5432 dbname=ais user=ais_admin sslmode=require" -v ON_ERROR_STOP=1 -f /opt/ais/schema.sql
```

4. Crea el usuario de la aplicación. Genera su contraseña con `openssl rand -hex 24`, anótala y entra a `psql` con el usuario maestro:

```bash
psql "host=$ENDPOINT_RDS port=5432 dbname=ais user=ais_admin sslmode=require"
```

```sql
CREATE ROLE ais_app LOGIN PASSWORD 'PEGA_AQUI_LA_CONTRASEÑA_DE_AIS_APP';
GRANT CONNECT ON DATABASE ais TO ais_app;
GRANT USAGE ON SCHEMA public TO ais_app;
-- La aplicación puede leer, insertar y actualizar, pero NO borrar.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO ais_app;
\q
```

> Si más adelante creas otra tabla, repite el último `GRANT`: solo se aplica a las tablas que ya existen.

---

## 9. Instalar la aplicación

```bash
# Entorno de Python con las versiones probadas
sudo python3 -m venv /opt/ais/venv
sudo /opt/ais/venv/bin/pip install "fastapi==0.162.0" "uvicorn==0.54.0" "psycopg[binary]==3.3.6" "psycopg-pool==3.3.3"
```

Crea los dos archivos y pega el contenido de los anexos:

```bash
sudo nano /opt/ais/app.py           # Anexo B.2
sudo nano /opt/ais/web/index.html   # Anexo B.3
```

**Genera la clave de la API** (es la que pondrá el PC). Anótala:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

> Es el mismo formato que da `python nube.py --generar-clave` en el PC. Si el dueño del PC ya generó una, usa esa: lo único que importa es que **sea la misma en los dos lados**.

Archivo de configuración con los secretos, legible solo por root y el usuario `ais`:

```bash
sudo nano /etc/ais/ais.env
```

```ini
AIS_CLAVE=PEGA_AQUI_LA_CLAVE_DE_LA_API
DATABASE_URL=postgresql://ais_app:CONTRASEÑA_DE_AIS_APP@ENDPOINT_RDS:5432/ais?sslmode=require
```

```bash
sudo chown root:ais /etc/ais/ais.env && sudo chmod 640 /etc/ais/ais.env
sudo chown -R root:root /opt/ais && sudo chmod -R a+rX /opt/ais
```

---

## 10. Servicio permanente (systemd)

```bash
sudo nano /etc/systemd/system/ais.service
```

```ini
[Unit]
Description=Servidor AIS (API de ingesta + web pública)
After=network-online.target
Wants=network-online.target

[Service]
User=ais
Group=ais
WorkingDirectory=/opt/ais
EnvironmentFile=/etc/ais/ais.env
ExecStart=/opt/ais/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ais
systemctl status ais --no-pager        # debe decir "active (running)"
curl -s http://127.0.0.1:8000/api/v1/publico/resumen
```

Si `curl` responde algo como `{"barcos":0,"mensajes_24h":0,...}`, la aplicación ya llega a RDS. Si no, mira el error con `journalctl -u ais -n 50 --no-pager` y la sección 15.

La aplicación escucha **solo** en 127.0.0.1. Desde internet se entra únicamente a través de Caddy.

---

## 11. HTTPS con Caddy

Instalación desde el repositorio oficial de Caddy:

```bash
sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt -y install caddy
```

Reemplaza **todo** el contenido de `/etc/caddy/Caddyfile` (`sudo nano /etc/caddy/Caddyfile`), cambiando el dominio por el tuyo:

```
ais-barranquilla.duckdns.org {
	encode zstd gzip
	request_body {
		max_size 25MB
	}
	reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl reload caddy
sudo journalctl -u caddy -n 30 --no-pager     # busca "certificate obtained successfully"
```

Caddy pide y renueva solo el certificado de Let's Encrypt. Para eso necesita que el dominio apunte a la IP elástica (sección 6) y que los puertos 80 y 443 estén abiertos (sección 3).

---

## 12. Pruebas antes de entregar

Desde la EC2 por SSH, pasando por el dominio público igual que lo hará el PC. Cambia el dominio y la clave:

```bash
D=https://ais-barranquilla.duckdns.org
K=LA_CLAVE_DE_LA_API

# 1. Salud con la clave correcta → {"ok":true,"version":1,...}
curl -s $D/api/v1/salud -H "X-API-Key: $K"; echo

# 2. Con una clave incorrecta → HTTP/2 401
curl -s -o /dev/null -w "%{http_code}\n" $D/api/v1/salud -H "X-API-Key: otra"

# 3. Un lote de prueba → {"ok":true,"recibidos":{"barcos":1}}
T=$(date +%s)
curl -s -X POST $D/api/v1/lote -H "X-API-Key: $K" -H "Content-Type: application/json" \
  -d "{\"version\":1,\"estacion\":\"prueba\",\"tablas\":{\"barcos\":[{\"mmsi\":999000001,\"nombre\":\"PRUEBA\",\"lat\":11.05,\"lon\":-74.85,\"ultima_vez\":$T,\"ultima_posicion\":$T,\"mod_nube\":$T}]}}"; echo
```

4. Abre `https://ais-barranquilla.duckdns.org` en el navegador. Debe verse el mapa con el barco "PRUEBA" en la lista (el candado indica HTTPS correcto).
5. **Borra el dato de prueba** desde la EC2 (el usuario de la app no puede borrar, así que entra con el maestro):

```bash
psql "host=$ENDPOINT_RDS port=5432 dbname=ais user=ais_admin sslmode=require" -c "DELETE FROM barcos WHERE estacion='prueba';"
```

---

## 13. Entrega al dueño del PC y primera sincronización

Envíale **por un canal privado** (nunca en un grupo ni en un repositorio):

1. La URL: `https://ais-barranquilla.duckdns.org`
2. La clave de la API (`AIS_CLAVE`).
3. El nombre de estación acordado: **`barranquilla-1`**.

En el PC, el dueño hace doble clic en `C:\Users\USER\Receptor_AIS\Configurar_Nube.bat`. El programa le pide esos tres datos y prueba la conexión. Debe decir *Conexión correcta*. Después, para subir todo el historial de una vez, ejecuta en WSL:

```bash
cd ~/sistema_ais/funcionalidad && python nube.py --enviar-todo
```

(O simplemente abre el receptor: el historial se sube solo y el indicador **Nube** pasa a verde, "Al día".)

**Verificación en RDS.** Los conteos deben ser iguales a los del PC:

```sql
SELECT 'sesiones' t, count(*) FROM sesiones WHERE estacion='barranquilla-1'
UNION ALL SELECT 'barcos', count(*) FROM barcos WHERE estacion='barranquilla-1'
UNION ALL SELECT 'mensajes', count(*) FROM mensajes WHERE estacion='barranquilla-1'
UNION ALL SELECT 'metricas', count(*) FROM metricas WHERE estacion='barranquilla-1'
UNION ALL SELECT 'posiciones_receptor', count(*) FROM posiciones_receptor WHERE estacion='barranquilla-1';
```

---

## 14. Operación diaria

| Tarea | Comando (en la EC2) |
|---|---|
| Ver lo que llega en vivo | `journalctl -u ais -f` (cada lote es un `POST /api/v1/lote ... 200`) |
| Registros de Caddy | `journalctl -u caddy -f` |
| Reiniciar la aplicación | `sudo systemctl restart ais` |
| Cambiar el código | Edita `/opt/ais/app.py` o `web/index.html` y luego `sudo systemctl restart ais` |
| Actualizar Ubuntu (una vez al mes) | `sudo apt update && sudo apt -y upgrade && sudo reboot` |
| Último dato de cada estación | `SELECT estacion, to_timestamp(max(ts)) FROM mensajes GROUP BY 1;` |
| Copia manual antes de cambios grandes | *RDS → ais-db → Actions → Take snapshot* |

**Cambiar la clave de la API.** Edita `/etc/ais/ais.env`, ejecuta `sudo systemctl restart ais` y dale la clave nueva al dueño del PC para que vuelva a correr `Configurar_Nube.bat`. Mientras las claves no coincidan, el PC muestra "Rechazado" y **guarda todo en local**. Al configurarlo se pone al día solo.

**Reinicios de mantenimiento de RDS.** El PC ve "Sin conexión" unos minutos y luego reenvía lo pendiente. La aplicación descarta sola las conexiones caídas. No hay que hacer nada.

### Cómo se ven los problemas en el indicador **Nube** del PC

| El PC muestra | Significa | Revisa |
|---|---|---|
| **Al día** (verde) | Todo llegó a RDS | – |
| **N pend.** / **Enviando** (naranja) | Hay datos en cola, se están enviando | Normal unos segundos; si dura, mira la fila siguiente |
| **Sin conexión** (rojo), error de red o timeout | No llega a la EC2 | Que la EC2 esté encendida, `systemctl status caddy ais`, puerto 443 en `ais-web`, que DuckDNS apunte a la IP elástica |
| **Sin conexión**, "HTTP 500/502" | Llega a Caddy pero falla la app o RDS | `journalctl -u ais -n 50`; que RDS esté *Available* |
| **Rechazado**, "HTTP 401" | Clave distinta | Que `AIS_CLAVE` sea igual a la del PC |
| **Rechazado**, "HTTP 400/413" | Lote inválido o enorme | `journalctl -u ais -n 50` y avisa al dueño del PC |

---

## 15. Problemas frecuentes al montar

| Síntoma | Causa | Solución |
|---|---|---|
| `psql` se queda esperando y da *timeout* | El grupo `ais-rds` no deja entrar a `ais-web`, o RDS está en otra VPC | Regla 5432 con origen `ais-web`; misma VPC |
| `password authentication failed` | Contraseña mal copiada o con símbolos en la URL | Usa contraseñas `openssl rand -hex` y revisa `/etc/ais/ais.env` |
| `database "ais" does not exist` | No se puso *Initial database name* | `psql "host=$ENDPOINT_RDS dbname=postgres user=ais_admin sslmode=require" -c "CREATE DATABASE ais;"` y repite la sección 8 |
| `permission denied for table ...` | Falta el `GRANT` o se hizo antes de crear las tablas | Repite el `GRANT SELECT, INSERT, UPDATE ...` |
| `ais.service` no arranca, `KeyError: 'AIS_CLAVE'` | Falta o está mal `/etc/ais/ais.env` | Revisa el archivo y sus permisos (`root:ais 640`) |
| Caddy: *challenge failed* o no obtiene certificado | DNS sin propagar, puerto 80 cerrado o muchos intentos | `nslookup` del dominio, regla 80 en `ais-web`; espera 1 hora si hubo muchos intentos |
| El navegador avisa *not secure* | Caddy aún no tiene certificado | Mira `journalctl -u caddy` |
| El mapa sale gris | Los mapas de OpenStreetMap no cargan (red del visitante) | Los barcos se dibujan igual; no es un error del servidor |
| SSH ya no conecta | Cambió la IP de tu casa | Edita la regla 22 de `ais-web` → *My IP* |

---

## 16. Apagar o borrar todo (para no pagar)

En este orden:

1. *EC2 → Instances →* `ais-servidor` *→ Terminate*.
2. *EC2 → Elastic IPs →* selecciónala *→ Release* (**importante**: una IP sin usar sigue cobrando).
3. *RDS →* `ais-db` *→ Modify →* desactiva *Deletion protection → Apply immediately*. Después *Actions → Delete*; si quieres conservar los datos, crea un *final snapshot* (los snapshots también cobran almacenamiento).
4. Borra los grupos `ais-web` y `ais-rds` y el par de claves `ais-ssh`.
5. En DuckDNS, borra el subdominio.

> Los datos no se pierden: el PC conserva su SQLite completo y puede volver a subir todo a una infraestructura nueva con `python nube.py --reiniciar` y luego `--enviar-todo`.

---

## Anexo A: qué recibe la API

La especificación completa está en `NUBE_API.md` (carpeta `sistema_ais`). En resumen:

**`GET /api/v1/salud`** (requiere `X-API-Key`): devuelve `{"ok": true, "version": 1, ...}`. El PC lo usa para "Probar conexión".

**`POST /api/v1/lote`** (requiere `X-API-Key`):
- Cuerpo JSON comprimido con gzip (`Content-Encoding: gzip`), con hasta 500 filas por tabla:
  ```json
  {"version": 1, "estacion": "barranquilla-1", "enviado": 1793403268.17,
   "tablas": {"sesiones": [...], "barcos": [...], "mensajes": [...], "metricas": [...], "posiciones_receptor": [...]}}
  ```
- Cada fila trae todas las columnas de su tabla en el SQLite del PC, más `mod_nube` (cuándo cambió por última vez). `mensajes.campos` es un texto JSON con los campos decodificados (lat, lon, sog_kn, nombre…) y se guarda como `JSONB`.
- Upsert por `(estacion, id)` (en `barcos`, por `(estacion, mmsi)`), todo en una sola transacción. Responde 200 **solo después del COMMIT**: el PC marca el lote como enviado al recibir ese 200.
- Las columnas desconocidas se ignoran. Si el PC agrega columnas en el futuro, no se rompe nada.
- Respuestas: 200 OK · 401 clave · 400 lote mal formado · 413 demasiado grande · 5xx el PC reintenta a los 5, 10, 20… s (máx. 5 min).
- Las horas (`ts`, `inicio`, `fin`, `ultima_vez`…) son **segundos Unix UTC** con decimales. En SQL: `to_timestamp(ts)`.

**Rutas públicas** (sin clave, solo lectura, las usa la web):

| Ruta | Devuelve |
|---|---|
| `/api/v1/publico/receptor` | Ubicación exacta más reciente del receptor de cada estación |
| `/api/v1/publico/barcos?horas=24` | Última posición e información de los barcos vistos en esas horas (máx. 2000) |
| `/api/v1/publico/barco/{mmsi}?horas=72` | Trayectoria de un barco (máx. 5000 puntos) |
| `/api/v1/publico/resumen` | Barcos totales, mensajes de las últimas 24 h, hora del último mensaje |

**Seguridad aplicada:**
- La clave se compara en tiempo constante.
- HTTPS es obligatorio y RDS no tiene IP pública.
- El usuario de la aplicación no puede borrar.
- Las rutas públicas tienen límites.
- La web escapa todo el texto que viene de la radio: cualquiera puede transmitir un nombre de barco con HTML.

---

## Anexo B: código del servidor

### B.1 `/opt/ais/schema.sql`

```sql
-- Esquema de la réplica en RDS (PostgreSQL 16). Ejecutar UNA vez con el usuario maestro.
-- Mismas columnas que el SQLite del receptor, más "estacion". Las horas (inicio, fin, ts, ...) son
-- segundos Unix en UTC con decimales. Sin claves foráneas a propósito: los lotes son idempotentes y
-- pueden llegar en cualquier orden tras un corte.

CREATE TABLE IF NOT EXISTS sesiones (
  estacion TEXT NOT NULL, id BIGINT NOT NULL,
  inicio DOUBLE PRECISION, fin DOUBLE PRECISION, fuente TEXT, detalle TEXT,
  ganancia REAL, ppm INTEGER, umbral REAL, rx_lat DOUBLE PRECISION, rx_lon DOUBLE PRECISION,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, id));

CREATE TABLE IF NOT EXISTS barcos (
  estacion TEXT NOT NULL, mmsi BIGINT NOT NULL,
  nombre TEXT, indicativo TEXT, destino TEXT, tipo_nave INTEGER,
  lat DOUBLE PRECISION, lon DOUBLE PRECISION, sog_kn REAL, cog REAL, rumbo INTEGER, estado_nav INTEGER,
  canal TEXT, primera_vez DOUBLE PRECISION, ultima_vez DOUBLE PRECISION, ultima_posicion DOUBLE PRECISION,
  n_mensajes INTEGER, pais TEXT, pais_iso TEXT, categoria TEXT, mmsi_valido INTEGER, tipo_nave_desc TEXT,
  rx_lat DOUBLE PRECISION, rx_lon DOUBLE PRECISION, dist_km DOUBLE PRECISION, directo INTEGER,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, mmsi));

CREATE TABLE IF NOT EXISTS mensajes (
  estacion TEXT NOT NULL, id BIGINT NOT NULL,
  sesion_id BIGINT, ts DOUBLE PRECISION, t_rel DOUBLE PRECISION, canal TEXT, snr_db REAL, desvio_hz REAL,
  tipo INTEGER, mmsi BIGINT, nmea TEXT, campos JSONB,
  rx_lat DOUBLE PRECISION, rx_lon DOUBLE PRECISION, dist_km DOUBLE PRECISION, repeticion INTEGER,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, id));

CREATE TABLE IF NOT EXISTS metricas (
  estacion TEXT NOT NULL, id BIGINT NOT NULL,
  sesion_id BIGINT, ts DOUBLE PRECISION, mensajes INTEGER, rafagas INTEGER, sin_crc INTEGER,
  invalidos INTEGER, piso_a_db REAL, piso_b_db REAL, snr_db REAL,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, id));

CREATE TABLE IF NOT EXISTS posiciones_receptor (
  estacion TEXT NOT NULL, id BIGINT NOT NULL,
  sesion_id BIGINT, ts DOUBLE PRECISION, lat DOUBLE PRECISION, lon DOUBLE PRECISION,
  precision_m REAL, fuente TEXT,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, id));

CREATE INDEX IF NOT EXISTS mensajes_mmsi_ts ON mensajes (mmsi, ts);
CREATE INDEX IF NOT EXISTS mensajes_ts      ON mensajes (ts);
CREATE INDEX IF NOT EXISTS barcos_ultima    ON barcos (ultima_vez);
CREATE INDEX IF NOT EXISTS posrx_ts         ON posiciones_receptor (estacion, ts);
CREATE INDEX IF NOT EXISTS metricas_ts      ON metricas (ts);
```

### B.2 `/opt/ais/app.py`

```python
"""Servidor AIS en la EC2: recibe los lotes del receptor (NUBE_API.md), los guarda en RDS y sirve la web pública.

Variables de entorno (archivo /etc/ais/ais.env):
  AIS_CLAVE      clave compartida con el PC (X-API-Key)
  DATABASE_URL   postgresql://ais_app:CONTRASEÑA@<endpoint-rds>:5432/ais?sslmode=require
"""
import hmac
import json
import os
import time
import zlib
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

CLAVE = os.environ["AIS_CLAVE"]
MAX_CUERPO = 20 * 1024 * 1024          # comprimido
MAX_DESCOMPRIMIDO = 200 * 1024 * 1024
WEB = Path(__file__).with_name("web")

# Orden de inserción, clave única y columnas aceptadas (las desconocidas se ignoran).
TABLAS = {
    "sesiones": ("id", "inicio fin fuente detalle ganancia ppm umbral rx_lat rx_lon mod_nube"),
    "barcos": ("mmsi", "nombre indicativo destino tipo_nave lat lon sog_kn cog rumbo estado_nav canal primera_vez "
                       "ultima_vez ultima_posicion n_mensajes pais pais_iso categoria mmsi_valido tipo_nave_desc "
                       "rx_lat rx_lon dist_km directo mod_nube"),
    "mensajes": ("id", "sesion_id ts t_rel canal snr_db desvio_hz tipo mmsi nmea campos rx_lat rx_lon dist_km "
                       "repeticion mod_nube"),
    "metricas": ("id", "sesion_id ts mensajes rafagas sin_crc invalidos piso_a_db piso_b_db snr_db mod_nube"),
    "posiciones_receptor": ("id", "sesion_id ts lat lon precision_m fuente mod_nube"),
}
TABLAS = {t: (k, c.split()) for t, (k, c) in TABLAS.items()}


def _sql_upsert(tabla):
    clave, cols = TABLAS[tabla]
    todas = ["estacion", clave] + cols
    marcas = ", ".join(["%s"] * len(todas))
    act = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols) + ", recibido = now()"
    # Siempre gana la versión más reciente (mod_nube); un reenvío viejo no pisa uno nuevo.
    return (f"INSERT INTO {tabla} ({', '.join(todas)}) VALUES ({marcas}) "
            f"ON CONFLICT (estacion, {clave}) DO UPDATE SET {act} "
            f"WHERE {tabla}.mod_nube IS NULL OR EXCLUDED.mod_nube >= {tabla}.mod_nube")


SQL = {t: _sql_upsert(t) for t in TABLAS}
# check: descarta conexiones muertas (p. ej. tras un reinicio de mantenimiento de RDS).
pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=4, open=True, timeout=10,
                      check=ConnectionPool.check_connection, kwargs={"row_factory": dict_row})
app = FastAPI(title="AIS", docs_url=None, redoc_url=None)


class LoteInvalido(Exception):
    pass


def _json_campos(v):
    if v is None or isinstance(v, (dict, list)):
        return Jsonb(v) if v is not None else None
    try:
        return Jsonb(json.loads(v))
    except (TypeError, ValueError):
        return None


def _filas(tabla, estacion, filas):
    clave, cols = TABLAS[tabla]
    salida = []
    for f in filas:
        if not isinstance(f, dict) or f.get(clave) is None:
            raise LoteInvalido(f"{tabla}: fila sin '{clave}'")
        valores = [f.get(c) for c in cols]
        if tabla == "mensajes":
            i = cols.index("campos")
            valores[i] = _json_campos(valores[i])
        salida.append([estacion, int(f[clave])] + valores)
    return salida


def guardar_lote(estacion, tablas):
    preparadas = {t: _filas(t, estacion, tablas.get(t) or []) for t in TABLAS}
    recibidos = {}
    with pool.connection() as con:          # una transacción: se guarda todo o nada
        with con.cursor() as cur:
            for tabla, filas in preparadas.items():
                if filas:
                    cur.executemany(SQL[tabla], filas)
                    recibidos[tabla] = len(filas)
    return recibidos                         # al salir del bloque ya hizo COMMIT


def _autorizado(request):
    return hmac.compare_digest(request.headers.get("x-api-key", "").encode(), CLAVE.encode())


def _error(codigo, texto):
    return JSONResponse({"ok": False, "error": texto}, status_code=codigo)


# ------------------------------------------------------------------ API de ingesta (con clave)
@app.get("/api/v1/salud")
def salud(request: Request):
    if not _autorizado(request):
        return _error(401, "clave no válida")
    with pool.connection() as con:
        con.execute("SELECT 1")
    return {"ok": True, "version": 1, "servidor": "ec2", "hora": time.time()}


@app.post("/api/v1/lote")
async def lote(request: Request):
    if not _autorizado(request):
        return _error(401, "clave no válida")
    if int(request.headers.get("content-length") or 0) > MAX_CUERPO:
        return _error(413, "lote demasiado grande")
    cuerpo = await request.body()
    if len(cuerpo) > MAX_CUERPO:
        return _error(413, "lote demasiado grande")
    try:
        if request.headers.get("content-encoding", "").lower() == "gzip":
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            cuerpo = d.decompress(cuerpo, MAX_DESCOMPRIMIDO)
            if d.unconsumed_tail:
                return _error(413, "lote descomprimido demasiado grande")
        datos = json.loads(cuerpo)
        estacion = str(datos.get("estacion") or "").strip()[:64]
        if datos.get("version") != 1 or not estacion or not isinstance(datos.get("tablas"), dict):
            raise LoteInvalido("faltan version/estacion/tablas")
        recibidos = await run_in_threadpool(guardar_lote, estacion, datos["tablas"])
    except (zlib.error, ValueError, LoteInvalido) as e:
        return _error(400, f"lote mal formado: {e}")
    return {"ok": True, "recibidos": recibidos}


# ------------------------------------------------------------------ API pública (sin clave, solo lectura)
def _consulta(sql, params=()):
    with pool.connection() as con:
        return con.execute(sql, params).fetchall()


@app.get("/api/v1/publico/receptor")
def receptor():
    """Ubicación EXACTA del receptor de cada estación (decisión del dueño del sistema)."""
    filas = _consulta("""
        SELECT DISTINCT ON (estacion) estacion, lat, lon, ts, fuente FROM (
            SELECT estacion, lat, lon, ts, fuente FROM posiciones_receptor WHERE lat IS NOT NULL
            UNION ALL
            SELECT estacion, rx_lat, rx_lon, inicio, 'sesion' FROM sesiones WHERE rx_lat IS NOT NULL
        ) p ORDER BY estacion, ts DESC""")
    return {"receptores": filas}


@app.get("/api/v1/publico/barcos")
def barcos(horas: float = Query(24, gt=0, le=24 * 365)):
    filas = _consulta("""
        SELECT estacion, mmsi, nombre, indicativo, destino, tipo_nave_desc, categoria, pais, pais_iso,
               lat, lon, sog_kn, cog, rumbo, estado_nav, primera_vez, ultima_vez, ultima_posicion,
               n_mensajes, dist_km, directo
        FROM barcos WHERE ultima_vez >= %s ORDER BY ultima_vez DESC LIMIT 2000""",
                      (time.time() - horas * 3600,))
    return {"ahora": time.time(), "barcos": filas}


@app.get("/api/v1/publico/barco/{mmsi}")
def barco(mmsi: int, horas: float = Query(72, gt=0, le=24 * 365)):
    pista = _consulta("""
        SELECT ts, (campos->>'lat')::float AS lat, (campos->>'lon')::float AS lon,
               (campos->>'sog_kn')::float AS sog_kn
        FROM mensajes
        WHERE mmsi = %s AND ts >= %s AND jsonb_typeof(campos->'lat') = 'number'
          AND abs((campos->>'lat')::float) <= 90 AND abs((campos->>'lon')::float) <= 180
        ORDER BY ts LIMIT 5000""", (mmsi, time.time() - horas * 3600))
    return {"mmsi": mmsi, "pista": pista}


@app.get("/api/v1/publico/resumen")
def resumen():
    f = _consulta("""
        SELECT (SELECT count(*) FROM barcos) AS barcos,
               (SELECT count(*) FROM mensajes WHERE ts >= %s) AS mensajes_24h,
               (SELECT max(ts) FROM mensajes) AS ultimo_mensaje,
               (SELECT count(DISTINCT estacion) FROM sesiones) AS estaciones""", (time.time() - 86400,))[0]
    return f


@app.get("/")
def inicio():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})
```

### B.3 `/opt/ais/web/index.html`

```html
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Barcos AIS en vivo</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --fondo:#0f1720; --panel:#16212d; --texto:#e6edf3; --tenue:#8aa0b4; --acento:#3fb6ff; }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:var(--fondo); color:var(--texto); font:14px system-ui, sans-serif; }
  header { display:flex; flex-wrap:wrap; gap:8px 18px; align-items:center; padding:10px 16px; background:var(--panel); }
  header h1 { font-size:16px; margin:0 auto 0 0; }
  .dato span { color:var(--tenue); margin-right:4px; }
  select { background:var(--fondo); color:var(--texto); border:1px solid #2b3b4c; border-radius:6px; padding:3px 6px; }
  main { display:flex; height:calc(100% - 48px); }
  #mapa { flex:1; }
  #lista { width:300px; overflow:auto; background:var(--panel); border-left:1px solid #22313f; }
  #lista div { padding:7px 12px; border-bottom:1px solid #1f2c39; cursor:pointer; }
  #lista div:hover { background:#1c2a38; }
  #lista small { color:var(--tenue); display:block; }
  .pt { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
  .leaflet-popup-content { font-size:13px; line-height:1.45; }
  .leaflet-popup-content button { margin-top:6px; }
  .rx { font-size:20px; text-shadow:0 0 3px #fff; }
  @media (max-width: 760px) { main { flex-direction:column; } #lista { width:auto; height:35%; border-left:0; } }
</style>
</head>
<body>
<header>
  <h1>Barcos AIS en vivo</h1>
  <div class="dato"><span>Barcos</span><b id="nBarcos">–</b></div>
  <div class="dato"><span>Mensajes 24 h</span><b id="nMsg">–</b></div>
  <div class="dato"><span>Último mensaje</span><b id="ultimo">–</b></div>
  <label class="dato"><span>Ventana</span>
    <select id="horas"><option value="1">1 h</option><option value="6">6 h</option>
      <option value="24" selected>24 h</option><option value="72">3 días</option><option value="168">7 días</option></select>
  </label>
</header>
<main><div id="mapa"></div><div id="lista"></div></main>
<script>
"use strict";
// Todo texto que viene de la radio (nombres, destinos) puede traer cualquier cosa: siempre escapar.
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const hora = ts => ts ? new Date(ts * 1000).toLocaleString("es-CO", {timeZone: "America/Bogota"}) : "–";
const hace = ts => { if (!ts) return "–"; const s = Date.now() / 1000 - ts;
  return s < 90 ? `hace ${Math.round(s)} s` : s < 5400 ? `hace ${Math.round(s / 60)} min` :
         s < 172800 ? `hace ${Math.round(s / 3600)} h` : `hace ${Math.round(s / 86400)} d`; };
const color = ts => { const s = Date.now() / 1000 - ts; return s < 600 ? "#3ddc84" : s < 3600 ? "#ffb020" : "#8aa0b4"; };

const mapa = L.map("mapa").setView([10.99, -74.79], 9);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  {maxZoom: 18, attribution: "&copy; OpenStreetMap"}).addTo(mapa);
const capaBarcos = L.layerGroup().addTo(mapa), capaRx = L.layerGroup().addTo(mapa), capaPista = L.layerGroup().addTo(mapa);
let primeraVez = true, rxPos = null;

async function api(ruta) { const r = await fetch(ruta, {cache: "no-store"}); if (!r.ok) throw new Error(r.status); return r.json(); }

function popupBarco(b) {
  const filas = [["MMSI", b.mmsi], ["País", b.pais], ["Tipo", b.tipo_nave_desc || b.categoria], ["Indicativo", b.indicativo],
    ["Destino", b.destino], ["Velocidad", b.sog_kn != null ? `${b.sog_kn} nudos` : null], ["Rumbo", b.cog != null ? `${b.cog}°` : null],
    ["Distancia al receptor", b.dist_km != null ? `${b.dist_km.toFixed(1)} km` : null],
    ["Última posición", hora(b.ultima_posicion)], ["Mensajes", b.n_mensajes]]
    .filter(([, v]) => v != null && v !== "").map(([k, v]) => `${esc(k)}: <b>${esc(v)}</b>`).join("<br>");
  return `<b>${esc(b.nombre || "(sin nombre)")}</b><br>${filas}<br><button data-mmsi="${esc(b.mmsi)}">Ver trayectoria</button>`;
}

async function pista(mmsi) {
  capaPista.clearLayers();
  const d = await api(`/api/v1/publico/barco/${encodeURIComponent(mmsi)}?horas=${document.getElementById("horas").value}`);
  const pts = d.pista.map(p => [p.lat, p.lon]);
  if (pts.length > 1) L.polyline(pts, {color: "#3fb6ff", weight: 3}).addTo(capaPista);
  pts.forEach(p => L.circleMarker(p, {radius: 2, color: "#3fb6ff"}).addTo(capaPista));
}
mapa.on("popupopen", e => { const btn = e.popup.getElement().querySelector("button[data-mmsi]");
  if (btn) btn.onclick = () => pista(btn.dataset.mmsi)
    .catch(() => { btn.textContent = "No se pudo cargar la trayectoria"; }); });

async function actualizar() {
  const horas = document.getElementById("horas").value;
  const [rx, bs, res] = await Promise.all([api("/api/v1/publico/receptor"), api(`/api/v1/publico/barcos?horas=${horas}`),
                                           api("/api/v1/publico/resumen")]);
  capaRx.clearLayers();
  rx.receptores.forEach(r => {
    rxPos = [r.lat, r.lon];
    L.marker(rxPos, {icon: L.divIcon({className: "rx", html: "📡", iconSize: [24, 24]}), zIndexOffset: 1000})
      .bindPopup(`<b>Receptor ${esc(r.estacion)}</b><br>Lat ${esc(r.lat.toFixed(6))}, Lon ${esc(r.lon.toFixed(6))}<br>` +
                 `Desde: ${esc(hora(r.ts))}`).addTo(capaRx);
  });
  capaBarcos.clearLayers();
  const lista = document.getElementById("lista"); lista.innerHTML = "";
  const conPos = [];
  bs.barcos.forEach(b => {
    let m = null;
    if (b.lat != null && b.lon != null && Math.abs(b.lat) <= 90 && Math.abs(b.lon) <= 180) {
      m = L.circleMarker([b.lat, b.lon], {radius: 7, weight: 2, color: "#fff", fillColor: color(b.ultima_vez), fillOpacity: .9})
        .bindPopup(popupBarco(b)).addTo(capaBarcos);
      conPos.push([b.lat, b.lon]);
    }
    const it = document.createElement("div");
    it.innerHTML = `<span class="pt" style="background:${color(b.ultima_vez)}"></span><b>${esc(b.nombre || b.mmsi)}</b>` +
      `<small>${esc([b.pais, b.tipo_nave_desc || b.categoria, hace(b.ultima_vez)].filter(Boolean).join(" · "))}</small>`;
    it.onclick = () => { if (m) { mapa.setView(m.getLatLng(), Math.max(mapa.getZoom(), 11)); m.openPopup(); } };
    lista.appendChild(it);
  });
  document.getElementById("nBarcos").textContent = bs.barcos.length;
  document.getElementById("nMsg").textContent = res.mensajes_24h;
  document.getElementById("ultimo").textContent = hace(res.ultimo_mensaje);
  if (primeraVez) {
    primeraVez = false;
    const todos = rxPos ? conPos.concat([rxPos]) : conPos;
    if (todos.length > 1) mapa.fitBounds(todos, {padding: [30, 30], maxZoom: 12});
    else if (rxPos) mapa.setView(rxPos, 10);
  }
}
function ciclo() { actualizar().catch(e => { document.getElementById("ultimo").textContent = "sin conexión (" + e.message + ")"; }); }
document.getElementById("horas").onchange = () => { primeraVez = true; capaPista.clearLayers(); ciclo(); };
ciclo(); setInterval(ciclo, 15000);
</script>
</body>
</html>
```

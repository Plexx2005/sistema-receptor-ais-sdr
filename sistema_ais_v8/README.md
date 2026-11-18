# Sistema Receptor AIS (SDR) con Replicación en Tiempo Real en AWS — Versión 8

Sistema completo de recepción, demodulación y decodificación de señales marítimas **AIS (Automatic Identification System, ITU-R M.1371)** implementado en Python mediante **Radio Definida por Software (RTL-SDR)**, con persistencia local resiliente en SQLite y sincronización en tiempo real hacia **Amazon Web Services (EC2 + RDS PostgreSQL 16 + Mapa Web Público)**.

🌐 **Mapa Público en Vivo:** [https://ais-barranquilla.duckdns.org](https://ais-barranquilla.duckdns.org)

---

## 1. Arquitectura General del Sistema

```text
 PC del Receptor (Edge / WSL)                   AWS Cloud (Región us-east-1)
 ┌───────────────────────────────┐   HTTPS      ┌────────────────────────────────────────────┐
 │ Antena VHF (162 MHz)          │   GZIP +     │ Amazon EC2 (ais-servidor, Ubuntu 24.04)    │
 │   └▶ RTL-SDR (1.536 MS/s I/Q) │  X-API-Key   │   └▶ Caddy :443 (TLS/HTTPS Let's Encrypt)  │
 │   └▶ DSP Multitasa (A y B)    │ ───────────▶ │        └▶ FastAPI + Uvicorn (127.0.0.1)    │
 │   └▶ Demod GMSK + HDLC/CRC16  │  POST /lote  │             │ SQL + TLS (:5432)            │
 │   └▶ SQLite Local (ais.db)    │   (< 1 s)    │             ▼                              │
 │   └▶ Sincronizador (nube.py)  │              │ Amazon RDS PostgreSQL 16 (ais-db, Privada) │
 └───────────────────────────────┘              └────────────────────────────────────────────┘
```

---

## 2. Cadena de Procesamiento Digital de Señales (DSP) y Protocolo

1. **Captura RF (`sdr.py`, `config.py`)**:
   - Sintonización central en **162.000 MHz** con tasa de muestreo de **1.536 MS/s**, permitiendo capturar simultáneamente los dos canales internacionales AIS: **Canal A (161.975 MHz, $-25\text{ kHz}$)** y **Canal B (162.025 MHz, $+25\text{ kHz}$)**.
2. **Banco de Canales Multitasa (`canal.py`)**:
   - Filtros FIR pasabajos diseñados con ventana de Kaiser ($60\text{ dB}$ de atenuación).
   - Decimación en cascada ($\div 4 \to \div 4 \to \div 2$, total $\div 32$) y mezclador digital complejo ($e^{-j\omega_0 n}$) para entregar cada canal en banda base a **48 kS/s** (**5 muestras exactas por símbolo** a 9600 baudios).
3. **Detección de Ráfagas TDMA y SNR (`receptor.py`)**:
   - Estimación adaptativa continua del piso de ruido térmico por canal y recorte de ráfagas activas por umbral de potencia.
4. **Demodulación GMSK / FM (`demod.py`, `receptor_sensible.py`)**:
   - **Modo estándar:** Discriminador polar de fase en cuadratura, corrección de desvío de portadora (CFO) y banco polifásico de 5 fases.
   - **Modo sensible:** Estimación espectral de portadora por no-linealidad cuadrática ($x^2$, FFT de 8192 puntos), sobremuestreo $\times 2$ (10 fases) y reparación combinatoria de hasta 2 bits erróneos guiada por confianza blanda (*soft-decision*) y síndrome CRC-16.
5. **Capa de Enlace HDLC y Traducción (`ais_bits.py`, `ais_msg.py`, `nmea.py`, `mmsi_info.py`)**:
   - Decodificación diferencial **NRZI**, detección de banderas HDLC (`0x7E`), remoción de *bit-stuffing*, comprobación **CRC-16-CCITT** (`0xF0B8`) e inversión de octetos LSB $\to$ MSB.
   - Decodificación de mensajes **Tipos 1, 2, 3, 4, 5, 11, 18, 19, 21 y 24**, posicionamiento geodésico en complemento a 2 ($\div 600,000$), distancia ortodrómica receptor–barco (**Haversine**), codificación **NMEA 0183 (`!AIVDM`)** e identificación de país por prefijo **MID**.

---

## 3. Estructura del Repositorio

```text
├── README.md                         # Documentación general del proyecto
├── LEEME.txt                         # Guía rápida de instalación y uso en el PC receptor
├── GUIA_AWS.md                       # Manual paso a paso de despliegue de infraestructura AWS
├── instalar_v8.sh                    # Instalador en entorno WSL
├── Instalar_v8_en_WSL.bat            # Lanzador automático desde Windows
├── servidor_aws/                     # Código del servidor desplegado en Amazon EC2
│   ├── app.py                        # API de ingesta (FastAPI) y endpoints públicos con conexión a RDS
│   ├── schema.sql                    # Esquema relacional PostgreSQL 16 (índices y JSONB)
│   ├── ais.service                   # Unidad systemd de ejecución aislada
│   └── web/
│       └── index.html                # Mapa público en vivo (Leaflet + OpenStreetMap)
└── sistema_ais/
    ├── NUBE_API.md                   # Especificación técnica del contrato HTTP/JSON/GZIP
    ├── funcionalidad/                # Módulos principales de RF, DSP, decodificación, GUI y nube
    └── pruebas/                      # Suite de pruebas unitarias, sintéticas y de sensibilidad
```

---

## 4. Comandos Rápidos

### En el PC del Receptor (WSL)
```bash
cd ~/sistema_ais/funcionalidad

# Ver estado de sincronización y registros pendientes por tabla
python nube.py --estado

# Probar conectividad HTTPS y validez de la API Key contra AWS
python nube.py --probar

# Subir todo el historial pendiente de forma inmediata
python nube.py --enviar-todo
```

### Ejecutar Suite de Pruebas
```bash
cd sistema_ais/pruebas
python prueba_bits.py
python prueba_canal.py
python prueba_demod.py
python prueba_msg.py
python prueba_sensibilidad.py
python prueba_db.py
```

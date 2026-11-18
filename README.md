# RikoPi — adquisición OKE en Python

Sustituye a **Riko v5.1** (LabVIEW): barrido pump-probe con la stage
**Newport XPS** (TCP/IP) y el lock-in **SR810** (RS232).
No hace falta ningún driver de National Instruments.

## Instalación (PC del laboratorio)

1. Python ≥ 3.11.
2. SR810 por RS232:
   * Adaptador USB-RS232 (mejor con chip FTDI) y cable **recto** (no null-modem) DB9 → DB25:
     el SR810 tiene un conector DB25 configurado como DCE.
   * En el SR810: tecla **SETUP** → interfaz **RS232**, y elige los baudios (9600 o 19200).
   * Mira qué puerto ha creado el adaptador (Windows: *Administrador de dispositivos* → `COMx`)
     y ponlo en `config.toml` (`[lockin] port`, `baudrate`).
   * Prueba la conexión: `python -m rikopi.instruments.sr810 COM3 9600` (sin argumentos lista los puertos).
3. Red: el PC tiene que estar en la misma subred que el XPS (p.ej. IP fija `192.168.0.100`,
   máscara `255.255.255.0`; el XPS está en `192.168.0.254`).
4. Desde esta carpeta:
   ```
   python -m venv .venv
   .venv\Scripts\activate          (Windows)   |   source .venv/bin/activate   (Linux/Mac)
   pip install -e .
   ```

## Uso

```
rikopi           # con hardware (según config.toml)
rikopi --sim      # simulación, sin hardware
```

Si al arrancar no encuentra los instrumentos, ofrece abrir en simulación (como Riko).

* **Barrido**: zonas de retardo (inicio, fin, paso; *Log* = paso creciente geométricamente, el paso
  indicado es el primero), tabla de sensibilidades por tramos de retardo, repeticiones, espera
  por punto, jitter. Se guarda el fichero completo al final de cada repetición (nunca se sobrescribe:
  si existe, se añade `_2`, `_3`…). *Ajustar autocorrelación* (sech² o gauss) da t0 y la duración
  del pulso, y *Usar como t0* corrige el cero de tiempos.
* **Stage / Scope**: posición en mm y ps, ir a una posición, fijar t0, sensibilidad del lock-in,
  lectura continua de X y movimiento adelante/atrás para alinear.

Al cerrar se guardan los ajustes y el t0 en `state.json`.

## Configuración (`config.toml`)

Equivalencias con `Riko v5.ini`: `XPSaddr → stage.host`, `Axis = Group1/- → group = "Group1"` +
`reversed = true`, `/x2 → passes = 2`, y el SR810, que antes iba por GPIB (`SR810addr = 12`), ahora va por RS232 (`lockin.port`).

**Antes de medir, comprueba:**
- `positioner`: nombre del posicionador en el XPS (en la web del XPS). Suele ser `Group1.Pos`.
- `t0_mm`: **hay que recalibrarlo** (el `t00` del .ini no se puede traducir con seguridad porque no
  está claro cómo combinaba LabVIEW el signo con el `/-`). Lo más fácil: barrido corto alrededor de
  la autocorrelación → *Ajustar* → *Usar como t0*.
- Las sensibilidades del .ini (`Sens0=8`…) eran índices de un menú de LabVIEW; en RikoPi se eligen
  directamente por valor (`50 mV`, etc.).

## Formato de datos

Texto con cabecera `#` y columnas separadas por tabuladores: `t_fs rep1 … repN avg` (mV).
```python
import numpy as np
d = np.loadtxt("fichero.dat")         # d[:,0] = t (fs), d[:,-1] = media
from rikopi import datafile
t_ps, reps, meta = datafile.read("fichero.dat")   # meta = todos los ajustes del barrido
```

## Estructura

| Módulo | Sustituye a |
|---|---|
| `rikopi/instruments/sr810.py` | SR810startup/readX/sens/timeconst/freq.vi |
| `rikopi/instruments/xps.py` | XPSstartup/init/home/getPosn, MotoMove, TCPwrtrd.vi |
| `rikopi/delay.py` | conversión mm ↔ ps de MotoMove/CheckRange.vi |
| `rikopi/scan.py` | bucle de Riko + Smpl, Jitr, deJitr, zonas |
| `rikopi/datafile.py` | FilePrep, xXYYheader, MakexXlgYYYarray.vi |
| `rikopi/fit.py` | CorrFit.vi |
| `rikopi/gui.py` | panel frontal de Riko |

Tests: `pip install -e ".[test]" && pytest`.

## Chopper

`[chopper]` en `config.toml`, como SR810slowChop.vi: 10 min después de terminar un barrido, la
referencia del SR810 baja a 120 Hz. Al empezar el siguiente barrido (o el scope) se restaura la
frecuencia anterior y se espera `spinup_s` a que el chopper recupere la velocidad. Se puede
desactivar con la casilla de la pestaña Barrido.

## No portado

OKEdoke (análisis). Tampoco el Nanomover MGn2, el boxcar + NI-DAQ, las cámaras (SXVR-H9, IMAQdx),
el ITC503 ni el ASG, que ya no se usan en el laboratorio.

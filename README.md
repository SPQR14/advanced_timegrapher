# Advanced Timegrapher

**[English](#english) · [Español](#español)**

---

# English

A software **timegrapher** for mechanical and quartz watches. It listens to the tick-tock
through a microphone and shows, in real time:

- **Rate** (s/d): how many seconds a day the watch gains or loses.
- **Beat error** (ms): how much the tick and the tock differ.
- **Amplitude** (°): how far the balance wheel swings.

> **This is a beta.** The app already measures, but we need your results and your bug
> reports to fine-tune it. See [Help us improve it](#help-us-improve-it).
>
> The interface is available in **English and Spanish**: use the **EN / ES** selector at the
> right end of the top bar.

## Contents

1. [What you need](#what-you-need)
2. [Installation](#installation)
3. [Your first measurement](#your-first-measurement)
4. [Reading the screen](#reading-the-screen)
5. [Tips for a good measurement](#tips-for-a-good-measurement)
6. [Quartz watches](#quartz-watches)
7. [Sound card clock calibration](#sound-card-clock-calibration)
8. [Saving results](#saving-results)
9. [Analyzing a recording](#analyzing-a-recording)
10. [Help us improve it](#help-us-improve-it)
11. [Troubleshooting](#troubleshooting)
12. [Beta limitations](#beta-limitations)
13. [Support the project](#support-the-project) · [Licence](#licence-and-technical-documentation)

## What you need

- A computer with **macOS 11 or later, Windows 10/11 or Linux**.
- A **microphone**. The best choice is a **contact microphone** with a clamp or a stand that
  holds the watch (for example a USB piezo microphone such as the CM18). A regular microphone
  also works if the watch is very close to it and the room is quiet.

## Installation

### Option A: download the app (recommended)

Go to the [Releases page](https://github.com/SPQR14/advanced_timegrapher/releases) and, under
the latest version, download the file for your system. Beta builds are **not code-signed**, so
the first time your system will warn you. This is expected; follow the steps below.

**macOS**

1. Download the `.dmg` file and double-click it.
2. Drag **Advanced Timegrapher** into the **Applications** folder.
3. Open **Applications**, **right-click** (or Control-click) **Advanced Timegrapher** and choose
   **Open**. In the warning, click **Open** again. (Double-clicking does not work the first
   time because the app is not signed.)
   - On recent macOS versions you may have to go to **System Settings → Privacy & Security**,
     scroll down and click **Open Anyway**.
4. When the app asks for **microphone access**, click **OK**.
   - If you clicked *Don't Allow*, go to **System Settings → Privacy & Security → Microphone**,
     turn on **Advanced Timegrapher** and restart the app.

**Windows**

1. Download the `.zip` file, right-click it → **Extract All…**, and choose a folder (for example
   `Documents\Advanced Timegrapher`).
2. Open that folder and double-click **adv-tg.exe**.
3. If a blue **"Windows protected your PC"** window appears, click **More info** → **Run
   anyway**.
4. If no sound arrives, go to **Settings → Privacy & security → Microphone** and make sure that
   **Let desktop apps access your microphone** is on.

**Linux**

1. Install PortAudio if you do not have it: `sudo apt install libportaudio2` (Debian/Ubuntu) or
   your distribution's equivalent.
2. Download the Linux file, make it executable (`chmod +x <file>`) and run it.

### Option B: using pip (wheel or from source)

**1. From the release wheel (recommended):**
The project is pure Python (no compiler needed). Download and install the wheel from the [Releases](https://github.com/SPQR14/advanced_timegrapher/releases/latest) page, or point pip directly to the release:
```bash
pip install --find-links https://github.com/SPQR14/advanced_timegrapher/releases/latest advanced-timegrapher
```

**2. From the source code (git checkout):**
Requires **Python 3.11 or later** and Git:
```bash
git clone https://github.com/SPQR14/advanced_timegrapher.git
cd advanced_timegrapher
python -m venv .venv
# macOS / Linux
.venv/bin/python -m pip install -e .
.venv/bin/adv-tg
# Windows (PowerShell)
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\adv-tg.exe
```
No compiler is needed: the whole analysis (filters, envelope, beat detection) is pure Python
on top of numpy; SciPy is not used.

On Linux you may also need `sudo apt install libportaudio2`. On macOS, the first time, the
Terminal app (or your IDE) will ask for microphone access.

## Your first measurement

1. **Plug in the microphone**, then open the app. It uses your system's default input; you can
   choose another one in **IN Device** (top bar). Leave **Sample Rate** at 48 kHz; choose
   **44.1 kHz** only if the microphone refuses 48 kHz (some USB microphones on Linux/PipeWire
   run at 44.1 kHz).
2. **Put the watch on the microphone**, in the position you want to measure. Dial up is the
   most common. Choose the same position in **Position**:
   - **DU** dial up · **DD** dial down · **CU** crown up · **CD** crown down · **CL** crown left
     · **CR** crown right.
3. **Choose the caliber** in **Caliber**. This fills in the BPH and the lift angle. If your
   caliber is not in the list, leave *Custom* and set:
   - **BPH** (beats per hour): 18000, 19800, 21600, 25200, 28800 or 36000. It is usually printed
     in the movement's specifications (e.g. 28800 = 8 beats per second).
   - **Lift Angle**: from the manufacturer's data. If you do not know it, leave 52°; the rate and
     beat error are not affected, but the **amplitude will only be indicative**.
4. Choose the **Stabilization** time (10, 15 or 20 s): the app listens but does not record
   figures yet, so the watch can settle after you put it on the microphone.
5. Choose the **Test Duration** (12 s to 24 h). 60 s is a good start; for comparing positions use
   2 minutes or more.
6. Optionally type a **Test Name** (it is used in the exported files).
7. Press **START**. The status bar shows *STABILIZING…* and then *MEASURING*. The first figures
   appear after 5–10 seconds and become more reliable over time (they are computed over the
   last 30 seconds). The test stops by itself at the end of the duration, or press **STOP**.

## Reading the screen

| Element | Meaning |
|---|---|
| **Rate, Beat error, Amplitude** cards (left) | `---.-` = not enough data yet, or confidence too low to show a figure. **Grey** figure = doubtful, use with caution. |
| **Signal Quality / SNR** bar | Before the watch is detected it shows the microphone level (dBFS). Then it shows the detection quality: signal-to-noise ratio and % of usable beats. **Green** = good; **amber** = could be better; **red with ⚠** = the signal is clipping: lower the microphone gain. Below it, **Normalize audio** (see [Tips](#tips-for-a-good-measurement)). |
| Statistics panel | Maximum, minimum, spread and mean rate of the test, elapsed time and **Clock Drift** (see [calibration](#sound-card-clock-calibration)). |
| **Paper Tape** (centre) | Like the paper strip of a mechanical timegrapher: **one dot for every tic (cyan) and every tac (orange)**, showing how early or late it arrives (ms) against the nominal beat. A healthy watch draws **two straight rows**: rising = gaining, falling = losing; the steeper, the larger the rate (the green dashed lines are the COSC limits, −4 and +6 s/d). The rows are timed at the drop (P3), so their distance is the tic/tac offset at that sound, not exactly the beat error card (measured at P1). Scattered dots = noise or an irregular escapement; a row that jumps up or down = the sound of that beat changed. It rescales by itself; if you pan or zoom it, press **Center Trace** to go back. |
| **Escapement Oscilloscope** (bottom left) | The last beat, with its three sounds: **P1** unlocking, **P2** impulse and **P3** drop. |
| **Audio Waveform** (bottom right) | The microphone signal in real time. |
| Status bar (bottom) | Session state, percentage of audio received, clock drift and warnings (e.g. *"tic and tac amplitude differ"*). |

## Tips for a good measurement

- Find a **quiet place**. Engines (motorbikes, cars), voices and knocks on the table spoil the
  measurement, especially the beat error.
- Put the microphone stand on **foam or a folded cloth**: a contact microphone mostly picks up
  the vibration of the table, not the air.
- If the bar turns **red with ⚠**, the signal is clipping: lower the microphone volume in your
  system's sound settings, or move the watch slightly.
- If it still clips with the volume at its minimum (loud watches such as many Timex quartz, or
  USB adapters with a hardware **auto gain control**), tick **Normalize audio** under the
  signal quality bar. It brings the level down by software before the analysis so the clipped
  beats are still measured. Use it **only** when the gain cannot be lowered: clipping cannot be
  undone. It applies from the next test and cannot be changed during a test.
  - On Linux, first try to turn the adapter's auto gain off, e.g.
    `amixer -c <card> sset 'Auto Gain Control' off` (`cat /proc/asound/cards` lists the cards).
- If figures never appear, check the BPH (a wrong BPH is the most common cause; the status bar
  may warn about it) and that the watch is firmly held.
- Wind the watch fully and let it run for a few minutes before measuring.
- Inexpensive movements can vary several s/d from one minute to the next: compare positions
  with longer tests and look at the **mean** in the statistics.

## Quartz watches

1. Tick **Quartz** next to the BPH. **Caliber**, **BPH** (shown as **Steps**, fixed at **1/s**,
   the step rate of almost every quartz watch) and **Lift Angle** are disabled; only the
   **rate** is measured.
2. If the watch is so loud that the signal clips even at the minimum gain, see
   **Normalize audio** in [Tips](#tips-for-a-good-measurement).
3. Many quartz movements correct their rate in jumps every few seconds or minutes
   (inhibition), so use long tests (5 minutes or more).

## Sound card clock calibration

The sound card has its own clock, and its error adds to the measured rate: each ppm (part per
million) is 0.0864 s/d; a typical USB sound card is off by 1–4 s/d. The app estimates it
automatically during each test using your computer's clock (synchronised over the Internet)
and corrects the rate when the estimate is reliable. The **Clock Drift** line shows the
correction applied and its confidence level (1–3).

For a more precise correction, press **Calibrate clock…** (under the statistics):

- After a **long test (15 minutes or more)**, press **Use live estimate** and then **Save**.
- **With a quartz watch of known rate** (checked against a time signal over several days):
  measure it, open **Calibrate clock…** and, in **From a reference watch of known rate**, type
  its **known rate**. The **measured rate** is already filled in with the last reading,
  without any clock correction. The window computes `ppm = (known − measured) / 0.0864`;
  press **Use this value** and then **Save**.
- With another reference (e.g. a GPS with PPS output), type the clock error directly in
  **Clock error** and its **Uncertainty**, add a note and press **Save**. A positive value
  means the card's clock runs fast (watches look slow).

The calibration is stored per microphone and is applied from the **next** test. The same window
shows the history and lets you delete it.

## Saving results

- **Record Audio**: saves the test's sound as a FLAC file, with the microphone, date and clock
  calibration stored inside the file. Press it during the test and it will ask where to save
  when the test ends; press it after the test to save the last one. Only available for tests
  of up to **240 s** (longer recordings would take hundreds of MB).
- **Save Position**: adds the current result to the positions table; **Global Statistics
  (Positional Delta)** shows the differences between positions.
- **Export Results to CSV**, **Export Test Results**, **Export Traces**, **Print Traces**: the
  test data as CSV, and the plots as an image or on paper.

## Analyzing a recording

**Analyze Recording** (above **Record Audio**) looks for defects that the live figures do not
show. It analyses the last test's recording (tests of up to 240 s are recorded); if there is
none, or after you saved it, it asks for a WAV/FLAC file. The BPH and lift angle stored in the
app's own recordings are used; for other files, those of the top bar. Mechanical watches only.
Beat detection takes about 1 s per 40 s of audio.

The window has three tabs:

- **Gear train**: periodic errors once per turn of a wheel.
  - **Escape wheel**: one turn every `2 × teeth × 3600 / BPH` seconds (3.75 s at 28800,
    5 s at 21600 with the usual 15 teeth; change the teeth if your caliber differs).
  - **Fourth wheel** (seconds wheel): one turn per minute. It needs **at least 180 s of
    recording** (three turns): use the **240 s** test. Shorter recordings show "Record ≥ 180 s".
  - The plots show the timing error of every beat, the loudness of the drop (P3) and their
    spectrum; the table gives, for each wheel, the amplitude, the equivalent rate ripple and
    whether it is present (false-alarm probability below 1 %).
- **Features**: the feature vector of every 60 s window and of the whole recording: escape
  and fourth wheel spectral energy, mean, variance, skewness and kurtosis of the P1→P2 and
  P2→P3 times, P1/P2 and P3/P2 energy ratios, rate, beat error, amplitude, sounds before P1
  and abrupt timing jumps.
- **Diagnosis**: indications of **rebanking** (amplitude above 310° with sounds before P1 and
  timing jumps), **lack of pallet lubrication** (weak impulse P2 against the drop P3 and an
  unstable P1→P2), **magnetism / off-centre hairspring** (rate above +30 s/d with a beat error
  that changes with the position) and the gear-train components. Save positions (**Save
  Position**, at least one horizontal and one vertical) before opening the window to get the
  horizontal-vertical rate and amplitude differences.

The thresholds of these indications are **provisional** (not yet calibrated with real watches
of known faults): treat them as something to check on the bench, never as a diagnosis, and
repeat the test in another position and after winding before opening the watch.
**Export features…** saves everything as JSON.

## Help us improve it

Reports go to [GitHub Issues](https://github.com/SPQR14/advanced_timegrapher/issues). You need a
free GitHub account.

1. After a test, press **Report** (top bar).
2. Optionally describe what happened. Leave **Include the last session recording** ticked: the
   recording is the most useful thing you can send us.
3. Press **Create report…** and choose where to save the `.zip` file. (It contains the log
   files, which include file paths with your user name and your audio device names, your
   settings and your clock calibrations. You can open it and check it before sharing.)
4. Press **Open GitHub**, choose a form, fill it in and **drag the `.zip` file** into the last
   field:
   - **Measurement result**: watch and caliber, BPH, lift angle, position, the app's figures
     and, if you have them, those of another timegrapher on the same watch and position (a
     commercial timegrapher, *tg*, *Watch Accuracy Meter*…). These comparisons are the most
     valuable reports.
   - **Bug report**: what you did, what you expected and what happened.

If the app does not open at all, attach the files of the log folder instead:

| System | Log folder |
|---|---|
| macOS | `~/Library/Logs/adv_tg` (in Finder: **Go → Go to Folder…** and paste the path) |
| Windows | `%LOCALAPPDATA%\adv_tg\logs` (paste it into the File Explorer address bar) |
| Linux | `~/.local/state/adv_tg/logs` |

**About** (top bar) shows the version and these folders.

## Troubleshooting

| Problem | What to do |
|---|---|
| The app does not open (macOS: "cannot be opened", "unidentified developer") | Right-click → **Open**, or **System Settings → Privacy & Security → Open Anyway**. See [Installation](#installation). |
| Windows: "Windows protected your PC" | **More info → Run anyway**. |
| The status bar says *"No sound from the microphone"* | The system is not giving the microphone to the app. macOS: **System Settings → Privacy & Security → Microphone**; Windows: **Settings → Privacy & security → Microphone**. Then restart the app. Also check **IN Device** and the input volume. |
| The level is fine but figures never appear | Check the **BPH** and that the watch is firmly on the microphone. Try another position or a quieter place. |
| The bar is red with ⚠ | The signal clips: lower the input volume. If it clips even at the minimum, tick **Normalize audio** (see [Tips](#tips-for-a-good-measurement)). |
| *"Invalid sample rate"* when starting | The microphone does not accept that rate: choose **44.1 kHz** (or 48 kHz) in **Sample Rate**. |
| **Analyze Recording** asks for a file | There is no recording of the last test (longer than 240 s, already saved, or no test yet): open the saved FLAC or any WAV/FLAC. |
| The fourth wheel says "Record ≥ 180 s" | The recording is too short to see one turn per minute three times: repeat with the **240 s** test. |
| Figures jump a lot | Noise (engines, voices, the table) or a watch whose rate really varies. Use foam under the stand and longer tests. |
| Amplitude in grey and a *"tic and tac amplitude differ"* warning | The amplitude is only indicative for this beat; check the lift angle and the microphone contact. |
| "Calibrate clock…" says it is not available | Choose a real microphone in **IN Device** (not the simulator). |
| **MOCK** badge on screen | The simulator is on: its figures are **not** measurements. Press **MOCK** to turn it off. |

## Beta limitations

- The beat error is sensitive to ambient noise.
- No support for beat rates above 36000 BPH.
- Builds are not code-signed (see [Installation](#installation)).
- Quartz mode measures watches with **1 step per second** only.
- The indications of **Analyze Recording** use provisional thresholds (see
  [Analyzing a recording](#analyzing-a-recording)).
- Some measurement warnings in the status bar (e.g. *"sin enganche… ¿BPH mal configurado?"*,
  no lock: wrong BPH?) are only in Spanish for now.

## Support the project

If you find it useful, you can buy me a coffee: <https://buymeacoffee.com/sqpr14> (also from
the ☕ button in the app).

## Licence and technical documentation

Free software under the **GNU GPL v3.0 or later** (see [`LICENSE`](LICENSE)). Technical
documentation, in Spanish: what is new in v0.8.0 (capture, audio normalization, recording
analysis, features and diagnosis rules) in
[`docs/documentation_v0.8.0-beta.md`](docs/documentation_v0.8.0-beta.md); architecture,
algorithms, validation and command-line tools in
[`docs/documentation_v0.6.3-beta3.md`](docs/documentation_v0.6.3-beta3.md).

---

# Español

Cronocomparador (*timegrapher*) por software para relojes mecánicos y de cuarzo. Escucha el
tic-tac con un micrófono y muestra en tiempo real:

- **Marcha** (s/d): cuántos segundos adelanta o atrasa el reloj al día.
- **Error de batimiento** (ms): cuánto difieren el tic y el tac.
- **Amplitud** (°): cuánto gira el volante.

> **Versión beta.** La aplicación ya mide, pero necesitamos tus resultados y tus reportes de
> fallos para afinarla. Ver [Ayúdanos a mejorarla](#ayúdanos-a-mejorarla).
>
> La interfaz está en **inglés y español**: usa el selector **EN / ES** del extremo derecho de
> la barra superior. Esta guía usa los nombres en español.

## Contenido

1. [Qué necesitas](#qué-necesitas)
2. [Instalación](#instalación)
3. [Tu primera medida](#tu-primera-medida)
4. [Cómo leer la pantalla](#cómo-leer-la-pantalla)
5. [Consejos para una buena medida](#consejos-para-una-buena-medida)
6. [Relojes de cuarzo](#relojes-de-cuarzo)
7. [Calibración del reloj de la tarjeta](#calibración-del-reloj-de-la-tarjeta)
8. [Guardar resultados](#guardar-resultados)
9. [Analizar una grabación](#analizar-una-grabación)
10. [Ayúdanos a mejorarla](#ayúdanos-a-mejorarla)
11. [Solución de problemas](#solución-de-problemas)
12. [Limitaciones de la beta](#limitaciones-de-la-beta)
13. [Apoya el proyecto](#apoya-el-proyecto) · [Licencia](#licencia-y-documentación-técnica)

## Qué necesitas

- Un ordenador con **macOS 11 o posterior, Windows 10/11 o Linux**.
- Un **micrófono**. Lo mejor es un **micrófono de contacto** con pinza o soporte que sujete el
  reloj (por ejemplo, un micrófono piezoeléctrico USB como el CM18). Un micrófono normal también
  sirve si el reloj está muy cerca y el entorno es silencioso.

## Instalación

### Opción A: descargar la aplicación (recomendado)

Entra en la [página de Releases](https://github.com/SPQR14/advanced_timegrapher/releases) y, en
la última versión, descarga el archivo de tu sistema. Las versiones beta **no están firmadas**,
así que la primera vez el sistema avisa. Es normal; sigue estos pasos.

**macOS**

1. Descarga el archivo `.dmg` y haz doble clic en él.
2. Arrastra **Advanced Timegrapher** a la carpeta **Aplicaciones**.
3. Abre **Aplicaciones**, haz **clic derecho** (o Control-clic) sobre **Advanced Timegrapher** y
   elige **Abrir**. En el aviso, pulsa **Abrir** otra vez. (El doble clic no funciona la primera
   vez porque la aplicación no está firmada.)
   - En las versiones recientes de macOS puede que tengas que ir a **Ajustes del Sistema →
     Privacidad y seguridad**, bajar hasta el final y pulsar **Abrir igualmente**.
4. Cuando la aplicación pida **acceso al micrófono**, pulsa **OK**.
   - Si pulsaste *No permitir*, ve a **Ajustes del Sistema → Privacidad y seguridad →
     Micrófono**, activa **Advanced Timegrapher** y reinicia la aplicación.

**Windows**

1. Descarga el archivo `.zip`, haz clic derecho → **Extraer todo…** y elige una carpeta (por
   ejemplo `Documentos\Advanced Timegrapher`).
2. Abre esa carpeta y haz doble clic en **adv-tg.exe**.
3. Si aparece la ventana azul **"Windows protegió su PC"**, pulsa **Más información** →
   **Ejecutar de todas formas**.
4. Si no llega sonido, ve a **Configuración → Privacidad y seguridad → Micrófono** y comprueba
   que **Permitir que las aplicaciones de escritorio accedan al micrófono** esté activado.

**Linux**

1. Instala PortAudio si no lo tienes: `sudo apt install libportaudio2` (Debian/Ubuntu) o el
   equivalente de tu distribución.
2. Descarga el archivo de Linux, dale permiso de ejecución (`chmod +x <archivo>`) y ábrelo.

### Opción B: mediante pip (wheel o desde código fuente)

**1. Desde el wheel del release (recomendado):**
El proyecto es Python puro (no hace falta compilador). Descarga e instala el wheel desde la página de [Releases](https://github.com/SPQR14/advanced_timegrapher/releases/latest), o instala directamente apuntando pip al release:
```bash
pip install --find-links https://github.com/SPQR14/advanced_timegrapher/releases/latest advanced-timegrapher
```

**2. Desde el código fuente (git):**
Requiere **Python 3.11 o posterior** y Git:
```bash
git clone https://github.com/SPQR14/advanced_timegrapher.git
cd advanced_timegrapher
python -m venv .venv
# macOS / Linux
.venv/bin/python -m pip install -e .
.venv/bin/adv-tg
# Windows (PowerShell)
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\adv-tg.exe
```
No hace falta compilador: todo el análisis (filtros, envolvente, detección de batimientos) es
Python puro sobre numpy; no se usa SciPy.

En Linux puede hacer falta `sudo apt install libportaudio2`. En macOS, la primera vez, la
aplicación Terminal (o tu editor) pedirá acceso al micrófono.

## Tu primera medida

1. **Conecta el micrófono** y después abre la aplicación. Usa la entrada predeterminada del
   sistema; puedes elegir otra en **Dispositivo IN** (barra superior). Deja **Frec. Muestreo**
   en 48 kHz; elige **44.1 kHz** solo si el micrófono no acepta 48 kHz (algunos micrófonos USB
   en Linux/PipeWire funcionan a 44.1 kHz).
2. **Coloca el reloj en el micrófono**, en la posición que quieras medir. La más habitual es
   esfera arriba. Elige la misma en **Posición**:
   - **DU** esfera arriba · **DD** esfera abajo · **CU** corona arriba · **CD** corona abajo ·
     **CL** corona a la izquierda · **CR** corona a la derecha.
3. **Elige el calibre** en **Calibre**. Así se rellenan los A/h y el ángulo. Si tu calibre no
   está, deja *Custom* y ajusta:
   - **A/h** (alternancias por hora): 18000, 19800, 21600, 25200, 28800 o 36000. Suele venir en
     las especificaciones del movimiento (por ejemplo, 28800 = 8 batimientos por segundo).
   - **Ángulo de Alzamiento** (ángulo de levantamiento): de los datos del fabricante. Si no lo
     sabes, deja 52°; la marcha y el error de batimiento no cambian, pero la **amplitud solo es
     orientativa**.
4. Elige la **Estabilización** (10, 15 o 20 s): la aplicación escucha pero aún no registra
   cifras, para que el reloj se asiente después de colocarlo.
5. Elige la **Duración Test** (de 12 s a 24 h). 60 s está bien para empezar; para comparar
   posiciones usa 2 minutos o más.
6. Si quieres, escribe un **Nombre de la prueba** (se usa en los archivos exportados).
7. Pulsa **INICIAR**. La barra de estado muestra *ESTABILIZANDO…* y después *MIDIENDO*. Las
   primeras cifras salen a los 5–10 segundos y se vuelven más fiables con el tiempo (se calculan
   sobre los últimos 30 segundos). La prueba termina sola al acabar la duración, o pulsa
   **DETENER**.

## Cómo leer la pantalla

| Elemento | Qué significa |
|---|---|
| Tarjetas de **marcha, error de batimiento y amplitud** (izquierda) | `---.-` = todavía no hay datos suficientes o la confianza es demasiado baja para mostrar una cifra. Cifra **en gris** = dudosa, tómala con cautela. |
| Barra **Calidad de Señal / SNR** | Antes de detectar el reloj muestra el nivel del micrófono (dBFS). Después, la calidad de la detección: relación señal/ruido y % de batimientos útiles. **Verde** = bien; **ámbar** = mejorable; **rojo con ⚠** = la señal satura: baja la ganancia del micrófono. Debajo, **Normalizar audio** (ver [Consejos](#consejos-para-una-buena-medida)). |
| Panel de estadísticas | Marcha máxima, mínima, diferencia y media de la prueba, tiempo transcurrido y **Deriva de Reloj** (ver [calibración](#calibración-del-reloj-de-la-tarjeta)). |
| **Cinta de papel** (centro) | Como la tira de papel de un cronocomparador mecánico: **un punto por cada tic (cian) y cada tac (naranja)**, que indica cuánto se adelanta o atrasa (ms) respecto al batimiento nominal. Un reloj sano dibuja **dos hileras rectas**: si suben, adelanta; si bajan, atrasa; cuanto más inclinadas, mayor la marcha (las líneas verdes discontinuas son los límites COSC, −4 y +6 s/d). Las hileras se miden en la caída (P3), así que su separación es el desfase tic/tac en ese sonido, no exactamente la tarjeta de error de batimiento (medido en P1). Puntos dispersos = ruido o un escape irregular; una hilera que salta hacia arriba o abajo = cambió el sonido de ese batimiento. Se reajusta sola; si la mueves o amplías, pulsa **Centrar Trazo** para volver. |
| **Osciloscopio de Escape** (abajo a la izquierda) | El último batimiento con sus tres sonidos: **P1** desbloqueo, **P2** impulso y **P3** caída. |
| **Onda de Audio** (abajo a la derecha) | La señal del micrófono en tiempo real. |
| Barra de estado (abajo) | Estado de la sesión, porcentaje de audio recibido, deriva del reloj y avisos (p. ej. *"amplitud de tic y tac distinta"*). |

## Consejos para una buena medida

- Busca un **sitio silencioso**. Los motores (motos, coches), las voces y los golpes en la mesa
  estropean la medida, sobre todo el error de batimiento.
- Apoya el soporte del micrófono sobre **espuma o un trapo doblado**: un micrófono de contacto
  capta sobre todo la vibración de la mesa, no el aire.
- Si la barra se pone **roja con ⚠**, la señal satura: baja el volumen del micrófono en los
  ajustes de sonido del sistema o mueve un poco el reloj.
- Si sigue saturando con el volumen al mínimo (relojes muy ruidosos, como muchos cuarzos Timex,
  o adaptadores USB con **control automático de ganancia** por hardware), marca **Normalizar
  audio** bajo la barra de calidad de señal. Baja el nivel por software antes del análisis para
  que los batimientos saturados se sigan midiendo. Úsalo **solo** cuando no se pueda bajar la
  ganancia: el recorte no se puede deshacer. Se aplica desde la siguiente prueba y no se puede
  cambiar durante una prueba.
  - En Linux, prueba antes a desactivar la ganancia automática del adaptador, p. ej.
    `amixer -c <tarjeta> sset 'Auto Gain Control' off` (`cat /proc/asound/cards` lista las
    tarjetas).
- Si nunca aparecen cifras, revisa los A/h (unos A/h equivocados son la causa más frecuente; la
  barra de estado puede avisarlo) y que el reloj esté bien sujeto.
- Da cuerda completa al reloj y déjalo funcionar unos minutos antes de medir.
- Los movimientos económicos pueden variar varios s/d de un minuto a otro: compara posiciones
  con pruebas más largas y fíjate en la **media** de las estadísticas.

## Relojes de cuarzo

1. Marca **Cuarzo** junto a los A/h. Se desactivan **Calibre**, **A/h** (que se muestra como
   **Pasos**, fijo en **1/s**, el paso de casi todos los relojes de cuarzo) y el **Ángulo**; solo
   se mide la **marcha**.
2. Si el reloj es tan ruidoso que la señal satura incluso con la ganancia al mínimo, ver
   **Normalizar audio** en [Consejos](#consejos-para-una-buena-medida).
3. Muchos cuarzos corrigen su marcha a saltos cada pocos segundos o minutos (inhibición), así
   que usa pruebas largas (5 minutos o más).

## Calibración del reloj de la tarjeta

La tarjeta de sonido tiene su propio reloj, y su error se suma a la marcha medida: cada ppm
(parte por millón) son 0.0864 s/d; una tarjeta USB típica se desvía de 1 a 4 s/d. La
aplicación lo estima sola durante cada prueba usando el reloj del ordenador (sincronizado por
Internet) y corrige la marcha cuando la estimación es fiable. La línea **Deriva de Reloj**
muestra la corrección aplicada y su nivel de confianza (1–3).

Para una corrección más precisa, pulsa **Calibrar reloj…** (bajo las estadísticas):

- Tras una **prueba larga (15 minutos o más)**, pulsa **Usar la estimación en vivo** y después
  **Guardar**.
- **Con un cuarzo de marcha conocida** (comprobada contra una señal horaria durante varios
  días): mídelo, abre **Calibrar reloj…** y, en **Desde un reloj de referencia de marcha
  conocida**, escribe su **marcha conocida**. La **marcha medida** ya viene rellenada con la
  última lectura, sin corrección de reloj. La ventana calcula
  `ppm = (conocida − medida) / 0.0864`; pulsa **Usar este valor** y después **Guardar**.
- Con otra referencia (p. ej. un GPS con salida PPS), escribe directamente el error en
  **Error del reloj** y su **Incertidumbre**, añade una nota y pulsa **Guardar**. Un valor
  positivo significa que el reloj de la tarjeta adelanta (los relojes parecen atrasar).

La calibración se guarda por micrófono y se aplica desde la **siguiente** prueba. La misma
ventana muestra el historial y permite borrarlo.

## Guardar resultados

- **Grabar Sonido**: guarda el sonido de la prueba en un archivo FLAC, con el micrófono, la
  fecha y la calibración del reloj dentro del archivo. Pulsado durante la prueba, pregunta dónde
  guardarlo al terminar; pulsado después, guarda la última prueba. Solo está disponible en
  pruebas de hasta **240 s** (las grabaciones más largas ocuparían cientos de MB).
- **Guardar Posición**: añade el resultado actual a la tabla de posiciones; **Estadísticas
  Globales (Delta Posicional)** muestra las diferencias entre posiciones.
- **Exportar resultados a CSV**, **Exportar resultados de la prueba**, **Exportar Trazas** e
  **Imprimir Trazas**: los datos de la prueba en CSV y las gráficas como imagen o en papel.

## Analizar una grabación

**Analizar Grabación** (encima de **Grabar Sonido**) busca defectos que las cifras en vivo no
muestran. Analiza la grabación de la última prueba (se graban las pruebas de hasta 240 s); si
no hay ninguna, o ya la guardaste, pide un archivo WAV/FLAC. Usa los A/h y el ángulo guardados
en las grabaciones de la propia aplicación; en otros archivos, los de la barra superior. Solo
relojes mecánicos. La detección de batimientos tarda aprox. 1 s por cada 40 s de audio.

La ventana tiene tres pestañas:

- **Tren de rodaje**: errores periódicos una vez por vuelta de una rueda.
  - **Rueda de escape**: una vuelta cada `2 × dientes × 3600 / A/h` segundos (3.75 s a 28800,
    5 s a 21600 con los 15 dientes habituales; cambia los dientes si tu calibre es distinto).
  - **Cuarta rueda** (rueda de segundos): una vuelta por minuto. Necesita **al menos 180 s de
    grabación** (tres vueltas): usa la prueba de **240 s**. Con grabaciones más cortas aparece
    "Graba ≥ 180 s".
  - Las gráficas muestran el error de tiempo de cada batimiento, la fuerza de la caída (P3) y
    su espectro; la tabla da, para cada rueda, la amplitud, el rizado de marcha equivalente y
    si está presente (probabilidad de falsa alarma menor del 1 %).
- **Características**: el vector de características de cada ventana de 60 s y de la grabación
  completa: energía espectral de la rueda de escape y de la cuarta rueda; media, varianza,
  asimetría y curtosis de los tiempos P1→P2 y P2→P3; relaciones de energía P1/P2 y P3/P2;
  marcha, error de batimiento, amplitud, sonidos antes de P1 y saltos bruscos de tiempo.
- **Diagnóstico**: indicios de **rebanking** (amplitud por encima de 310° con sonidos antes de
  P1 y saltos de tiempo), **falta de lubricación en las paletas** (impulso P2 débil frente a la
  caída P3 y P1→P2 inestable), **magnetismo / espiral descentrada** (marcha por encima de
  +30 s/d con un error de batimiento que cambia con la posición) y las componentes del tren de
  rodaje. Guarda posiciones (**Guardar Posición**, al menos una horizontal y una vertical) antes
  de abrir la ventana para obtener las diferencias de marcha y amplitud horizontal-vertical.

Los umbrales de estos indicios son **provisionales** (aún no están calibrados con relojes
reales de averías conocidas): tómalos como algo a comprobar en el banco, nunca como un
diagnóstico, y repite la prueba en otra posición y tras dar cuerda antes de abrir el reloj.
**Exportar características…** lo guarda todo en JSON.

## Ayúdanos a mejorarla

Los reportes van a [GitHub Issues](https://github.com/SPQR14/advanced_timegrapher/issues).
Necesitas una cuenta gratuita de GitHub.

1. Después de una prueba, pulsa **Reportar** (barra superior).
2. Si quieres, describe qué pasó. Deja marcada **Incluir la grabación de la última sesión**: la
   grabación es lo más útil que nos puedes mandar.
3. Pulsa **Crear reporte…** y elige dónde guardar el archivo `.zip`. (Contiene los registros,
   que incluyen rutas de archivos con tu nombre de usuario y los nombres de tus dispositivos de
   audio, tus ajustes y las calibraciones del reloj. Puedes abrirlo y revisarlo antes de
   compartirlo.)
4. Pulsa **Abrir GitHub**, elige un formulario, rellénalo y **arrastra el `.zip`** al último
   campo:
   - **Measurement result** (resultado de medida): reloj y calibre, A/h, ángulo, posición, las
     cifras de la aplicación y, si las tienes, las de otro cronocomparador en el mismo reloj y
     posición (un aparato comercial, *tg*, *Watch Accuracy Meter*…). Estas comparaciones son
     los reportes más valiosos.
   - **Bug report** (fallo): qué hiciste, qué esperabas y qué pasó.

Los formularios están en inglés y en español. Si la aplicación no llega a abrirse, adjunta en
su lugar los archivos de la carpeta de registros:

| Sistema | Carpeta de registros |
|---|---|
| macOS | `~/Library/Logs/adv_tg` (en el Finder: **Ir → Ir a la carpeta…** y pega la ruta) |
| Windows | `%LOCALAPPDATA%\adv_tg\logs` (pégala en la barra de direcciones del Explorador) |
| Linux | `~/.local/state/adv_tg/logs` |

**Acerca de** (barra superior) muestra la versión y estas carpetas.

## Solución de problemas

| Problema | Qué hacer |
|---|---|
| La aplicación no abre (macOS: "no se puede abrir", "desarrollador no identificado") | Clic derecho → **Abrir**, o **Ajustes del Sistema → Privacidad y seguridad → Abrir igualmente**. Ver [Instalación](#instalación). |
| Windows: "Windows protegió su PC" | **Más información → Ejecutar de todas formas**. |
| La barra de estado dice *"No llega sonido del micrófono"* | El sistema no le da el micrófono a la aplicación. macOS: **Ajustes del Sistema → Privacidad y seguridad → Micrófono**; Windows: **Configuración → Privacidad y seguridad → Micrófono**. Después reinicia la aplicación. Revisa también **Dispositivo IN** y el volumen de entrada. |
| El nivel es bueno pero nunca salen cifras | Revisa los **A/h** y que el reloj esté bien apoyado en el micrófono. Prueba otra posición o un sitio más silencioso. |
| La barra está roja con ⚠ | La señal satura: baja el volumen de entrada. Si satura incluso al mínimo, marca **Normalizar audio** (ver [Consejos](#consejos-para-una-buena-medida)). |
| *"Invalid sample rate"* al iniciar | El micrófono no acepta esa frecuencia: elige **44.1 kHz** (o 48 kHz) en **Frec. Muestreo**. |
| **Analizar Grabación** pide un archivo | No hay grabación de la última prueba (duró más de 240 s, ya la guardaste o aún no hay prueba): abre el FLAC guardado o cualquier WAV/FLAC. |
| La cuarta rueda dice "Graba ≥ 180 s" | La grabación es demasiado corta para ver tres veces la vuelta por minuto: repite con la prueba de **240 s**. |
| Las cifras saltan mucho | Ruido (motores, voces, la mesa) o un reloj cuya marcha varía de verdad. Usa espuma bajo el soporte y pruebas más largas. |
| Amplitud en gris y aviso *"amplitud de tic y tac distinta"* | La amplitud es orientativa en esa medida; revisa el ángulo y el contacto con el micrófono. |
| "Calibrar reloj…" dice que no está disponible | Elige un micrófono real en **Dispositivo IN** (no el simulador). |
| Insignia **MOCK** en pantalla | El simulador está activo: sus cifras **no** son medidas. Pulsa **MOCK** para desactivarlo. |

## Limitaciones de la beta

- El error de batimiento es sensible al ruido ambiental.
- No admite frecuencias por encima de 36000 A/h.
- Las aplicaciones no están firmadas (ver [Instalación](#instalación)).
- El modo cuarzo solo mide relojes de **1 paso por segundo**.
- Los indicios de **Analizar Grabación** usan umbrales provisionales (ver
  [Analizar una grabación](#analizar-una-grabación)).
- Algunos avisos de la medida en la barra de estado solo están en español, aunque la interfaz
  esté en inglés.

## Apoya el proyecto

Si te resulta útil, puedes invitarme a un café: <https://buymeacoffee.com/sqpr14> (también
desde el botón ☕ de la aplicación).

## Licencia y documentación técnica

Software libre bajo la licencia **GNU GPL v3.0 o posterior** (ver [`LICENSE`](LICENSE)). La
documentación técnica: lo nuevo de la v0.8.0 (captura, normalización de audio, análisis de
grabaciones, características y reglas de diagnóstico) en
[`docs/documentation_v0.8.0-beta.md`](docs/documentation_v0.8.0-beta.md); arquitectura,
algoritmos, validación y herramientas de consola en
[`docs/documentation_v0.6.3-beta3.md`](docs/documentation_v0.6.3-beta3.md).

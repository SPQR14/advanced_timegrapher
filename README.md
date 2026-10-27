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
9. [Help us improve it](#help-us-improve-it)
10. [Troubleshooting](#troubleshooting)
11. [Beta limitations](#beta-limitations)
12. [Support the project](#support-the-project) · [Licence](#licence-and-technical-documentation)

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

### Option B: from the source code (for technical users)

Requires **Python 3.11 or later**, Git and a **C++ compiler** (the analysis core has a C++
part that is built during the installation):

- **macOS:** Xcode Command Line Tools: run `xcode-select --install` in Terminal.
- **Windows:** [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  with the **Desktop development with C++** workload.
- **Linux:** `sudo apt install build-essential python3-dev` (Debian/Ubuntu) or your
  distribution's equivalent.

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

On Linux you may also need `sudo apt install libportaudio2`. On macOS, the first time, the
Terminal app (or your IDE) will ask for microphone access.

## Your first measurement

1. **Plug in the microphone**, then open the app. It uses your system's default input; you can
   choose another one in **IN Device** (top bar). Leave **Sample Rate** at 48 kHz.
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
5. Choose the **Test Duration** (12 s to 1 h). 60 s is a good start; for comparing positions use
   2 minutes or more.
6. Optionally type a **Test Name** (it is used in the exported files).
7. Press **START**. The status bar shows *STABILIZING…* and then *MEASURING*. The first figures
   appear after 5–10 seconds and become more reliable over time (they are computed over the
   last 30 seconds). The test stops by itself at the end of the duration, or press **STOP**.

## Reading the screen

| Element | Meaning |
|---|---|
| **Rate, Beat error, Amplitude** cards (left) | `---.-` = not enough data yet, or confidence too low to show a figure. **Grey** figure = doubtful, use with caution. |
| **Signal Quality / SNR** bar | Before the watch is detected it shows the microphone level (dBFS). Then it shows the detection quality: signal-to-noise ratio and % of usable beats. **Green** = good; **amber** = could be better; **red with ⚠** = the signal is clipping: lower the microphone gain. |
| Statistics panel | Maximum, minimum, spread and mean rate of the test, elapsed time and **Clock Drift** (see [calibration](#sound-card-clock-calibration)). |
| **Paper Tape Trace** (centre) | The rate along the test, one line for the tick and one for the tock (they are apart by the beat error). The green dashed lines are the COSC limits (−4 / +6 s/d). It rescales by itself; if you pan or zoom it, press **Center Trace** to go back. |
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
- If figures never appear, check the BPH (a wrong BPH is the most common cause; the status bar
  may warn about it) and that the watch is firmly held.
- Wind the watch fully and let it run for a few minutes before measuring.
- Inexpensive movements can vary several s/d from one minute to the next: compare positions
  with longer tests and look at the **mean** in the statistics.

## Quartz watches

1. Tick **Quartz** next to the BPH. The **BPH** list becomes **Steps**: the number of steps per
   second of the seconds hand. Most quartz watches: **1/s**. "Sweeping" seconds hands such as
   the Bulova Precisionist: **16/s**.
2. The lift angle and the caliber list are disabled; only the **rate** is measured.
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
- If you have a reference (a quartz watch of known error, a GPS with PPS output), type the
  measured clock error in **Clock error** and its **Uncertainty**, add a note and press
  **Save**. A positive value means the card's clock runs fast (watches look slow).

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
| The bar is red with ⚠ | The signal clips: lower the input volume. |
| Figures jump a lot | Noise (engines, voices, the table) or a watch whose rate really varies. Use foam under the stand and longer tests. |
| Amplitude in grey and a *"tic and tac amplitude differ"* warning | The amplitude is only indicative for this beat; check the lift angle and the microphone contact. |
| "Calibrate clock…" says it is not available | Choose a real microphone in **IN Device** (not the simulator). |
| **MOCK** badge on screen | The simulator is on: its figures are **not** measurements. Press **MOCK** to turn it off. |

## Beta limitations

- The beat error is sensitive to ambient noise.
- No support for beat rates above 36000 BPH.
- Builds are not code-signed (see [Installation](#installation)).
- Some measurement warnings in the status bar (e.g. *"sin enganche… ¿BPH mal configurado?"*,
  no lock: wrong BPH?) are only in Spanish for now.

## Support the project

If you find it useful, you can buy me a coffee: <https://buymeacoffee.com/sqpr14> (also from
the ☕ button in the app).

## Licence and technical documentation

Free software under the **GNU GPL v3.0 or later** (see [`LICENSE`](LICENSE)). Technical
documentation (architecture, algorithms, validation and command-line tools, in Spanish):
[`docs/resumen_beta1.md`](docs/resumen_beta1.md) and the result documents of each phase in
[`docs/`](docs/).

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
9. [Ayúdanos a mejorarla](#ayúdanos-a-mejorarla)
10. [Solución de problemas](#solución-de-problemas)
11. [Limitaciones de la beta](#limitaciones-de-la-beta)
12. [Apoya el proyecto](#apoya-el-proyecto) · [Licencia](#licencia-y-documentación-técnica)

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

### Opción B: desde el código fuente (usuarios técnicos)

Requiere **Python 3.11 o posterior**, Git y un **compilador de C++** (el núcleo del análisis
tiene una parte en C++ que se compila durante la instalación):

- **macOS:** Xcode Command Line Tools: ejecuta `xcode-select --install` en Terminal.
- **Windows:** [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  con la carga de trabajo **Desarrollo para el escritorio con C++**.
- **Linux:** `sudo apt install build-essential python3-dev` (Debian/Ubuntu) o el equivalente
  de tu distribución.

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

En Linux puede hacer falta `sudo apt install libportaudio2`. En macOS, la primera vez, la
aplicación Terminal (o tu editor) pedirá acceso al micrófono.

## Tu primera medida

1. **Conecta el micrófono** y después abre la aplicación. Usa la entrada predeterminada del
   sistema; puedes elegir otra en **Dispositivo IN** (barra superior). Deja **Frec. Muestreo**
   en 48 kHz.
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
5. Elige la **Duración Test** (de 12 s a 1 h). 60 s está bien para empezar; para comparar
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
| Barra **Calidad de Señal / SNR** | Antes de detectar el reloj muestra el nivel del micrófono (dBFS). Después, la calidad de la detección: relación señal/ruido y % de batimientos útiles. **Verde** = bien; **ámbar** = mejorable; **rojo con ⚠** = la señal satura: baja la ganancia del micrófono. |
| Panel de estadísticas | Marcha máxima, mínima, diferencia y media de la prueba, tiempo transcurrido y **Deriva de Reloj** (ver [calibración](#calibración-del-reloj-de-la-tarjeta)). |
| **Trazo de Cinta de Papel** (centro) | La marcha a lo largo de la prueba, una línea para el tic y otra para el tac (separadas por el error de batimiento). Las líneas verdes discontinuas son los límites COSC (−4 / +6 s/d). Se reajusta sola; si la mueves o amplías, pulsa **Centrar Trazo** para volver. |
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
- Si nunca aparecen cifras, revisa los A/h (unos A/h equivocados son la causa más frecuente; la
  barra de estado puede avisarlo) y que el reloj esté bien sujeto.
- Da cuerda completa al reloj y déjalo funcionar unos minutos antes de medir.
- Los movimientos económicos pueden variar varios s/d de un minuto a otro: compara posiciones
  con pruebas más largas y fíjate en la **media** de las estadísticas.

## Relojes de cuarzo

1. Marca **Cuarzo** junto a los A/h. La lista **A/h** pasa a ser **Pasos**: los pasos por
   segundo del segundero. La mayoría de cuarzos: **1/s**. Segunderos "de barrido" como los
   Bulova Precisionist: **16/s**.
2. El ángulo y la lista de calibres se desactivan; solo se mide la **marcha**.
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
- Si tienes una referencia (un cuarzo de error conocido, un GPS con salida PPS), escribe el
  error medido en **Error del reloj** y su **Incertidumbre**, añade una nota y pulsa
  **Guardar**. Un valor positivo significa que el reloj de la tarjeta adelanta (los relojes
  parecen atrasar).

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
| La barra está roja con ⚠ | La señal satura: baja el volumen de entrada. |
| Las cifras saltan mucho | Ruido (motores, voces, la mesa) o un reloj cuya marcha varía de verdad. Usa espuma bajo el soporte y pruebas más largas. |
| Amplitud en gris y aviso *"amplitud de tic y tac distinta"* | La amplitud es orientativa en esa medida; revisa el ángulo y el contacto con el micrófono. |
| "Calibrar reloj…" dice que no está disponible | Elige un micrófono real en **Dispositivo IN** (no el simulador). |
| Insignia **MOCK** en pantalla | El simulador está activo: sus cifras **no** son medidas. Pulsa **MOCK** para desactivarlo. |

## Limitaciones de la beta

- El error de batimiento es sensible al ruido ambiental.
- No admite frecuencias por encima de 36000 A/h.
- Las aplicaciones no están firmadas (ver [Instalación](#instalación)).
- Algunos avisos de la medida en la barra de estado solo están en español, aunque la interfaz
  esté en inglés.

## Apoya el proyecto

Si te resulta útil, puedes invitarme a un café: <https://buymeacoffee.com/sqpr14> (también
desde el botón ☕ de la aplicación).

## Licencia y documentación técnica

Software libre bajo la licencia **GNU GPL v3.0 o posterior** (ver [`LICENSE`](LICENSE)). La
documentación técnica (arquitectura, algoritmos, validación y herramientas de consola) está en
[`docs/resumen_beta1.md`](docs/resumen_beta1.md) y en los documentos de resultados de cada fase
de [`docs/`](docs/).

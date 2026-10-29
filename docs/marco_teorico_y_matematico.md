# Marco Teórico y Fundamentos Matemáticos: Watch Escape Analyzer EVO

Este documento constituye la especificación científica y matemática unificada para **Watch Escape Analyzer EVO**. Compila los basamentos de arquitectura de hardware, metrología de tiempo de alta precisión, teoría del escape de áncora suizo, modelos de regresión estocástica y la bibliografía de referencia asociada al proyecto.

---

## 1. Infraestructura de Medición Temporal de Ultra-Baja Latencia

### 1.1 Contadores Hardware y Aislamiento de Pipeline
Para alcanzar una resolución de lectura temporal a nivel de sub-microsegundo ($\pm 0.1\text{ ms}$ en Beat Error y $\pm 0.01\text{ s/d}$ en resolución de marcha), el software abstrae la captura de tiempo del sistema operativo utilizando contadores monotónicos de hardware [18, 28]:

* **x86_64 (Time Stamp Counter - Invariant TSC):**
  Uso del registro de 64 bits `TSC`, presente en procesadores x86 [18]. Se verifica el soporte de `constant_tsc` y `nonstop_tsc` en el kernel para garantizar un incremento a frecuencia nominal fija e inmune a las transiciones de estados de energía (P-states/C-states) y escalado DVFS [18, 28].
  Para evitar que la ejecución fuera de orden (*out-of-order execution*) reordene las lecturas del contador en la CPU, se aplican barreras de serialización [18]:
  * `RDTSCP`: Lee el contador esperando a que se retiren las instrucciones previas e inyecta el ID del núcleo ejecutor para detectar migraciones entre sockets [18].
  * `LFENCE + RDTSC`: La instrucción *Load Fence* purga la tubería de cargas especulativas previa al muestreo del registro [18].

* **ARM64 (Generic Timer / CNTPCT_EL0 / CNTVCTSS_EL0):**
  En arquitecturas ARM64 (Apple Silicon, Raspberry Pi, Android), la lectura del tiempo se realiza sobre el registro `CNTPCT_EL0` (contador físico) o `CNTVCT_EL0` (contador virtual) [2, 15].
  * Se aplica la instrucción de barrera `ISB` (*Instruction Synchronization Barrier*) para sincronizar las lecturas especulativas [2].
  * Bajo la extensión **Armv8.6-A (FEAT_ECV)**, se utiliza `CNTVCTSS_EL0`, un registro de conteo auto-sincronizado que elimina el costo de la instrucción `ISB` [8].

### 1.2 Métodos de Extracción en Espacio de Usuario (vDSO) y APIs de Sistema
Para evitar el costo computacional de las llamadas al sistema (*sys-calls* / transiciones Ring 3 a Ring 0), el software se integra con las APIs de baja latencia del sistema operativo [18, 28]:

| Sistema Operativo | API Recomendada | Mecanismo Subyacente | Latencia típica | Resolución |
| :--- | :--- | :--- | :--- | :--- |
| **Linux / POSIX** | `clock_gettime(CLOCK_MONOTONIC_RAW)` | vDSO + Invariant TSC / Generic Timer [18] | $\sim 15\text{ ns}$ [18] | $1\text{ ns}$ [18] |
| **Windows** | `QueryPerformanceCounter` (QPC) | HAL Invariant TSC [3, 18] | $\sim 20\text{ ns}$ [18] | Sub-microsegundo [3, 18] |
| **Windows UTC** | `GetSystemTimePreciseAsFileTime` | QPC + Matrix `KUSER_SHARED_DATA` [17, 18] | $\sim 40\text{ ns}$ [18] | $100\text{ ns}$ [17, 18] |
| **macOS / iOS** | `clock_gettime_nsec_np` / `mach_absolute_time` | Invariant TSC / ARM System Counter [21, 37] | $\sim 10\text{ ns}$ [18] | $1\text{ ns}$ [21] |

### 1.3 Conversión de Ciclos Hardware a Nanosegundos
Los kernels modernos traducen los ciclos del contador hardware $C$ a nanosegundos $T_{\text{ns}}$ mediante operaciones de enteros (*multiply and shift*) en el búfer vDSO sin divisiones flotantes en el camino crítico [11, 14, 28]:
$$T_{\text{ns}} = \frac{C \cdot \text{mult}}{2^{\text{shift}}}$$

---

## 2. Deriva Temporal (*Clock Drift*), Auto-Calibración y Disciplina Frecuencial

### 2.1 Física de la Deriva del Cristal del ADC
Los convertidores analógico-digitales (ADC) de tarjetas de sonido USB o micrófonos piezoeléctricos se gobiernan por osciladores de cristal de cuarzo comerciales (XO) con tolerancias de $\pm 30$ a $\pm 100\text{ PPM}$ (partes por millón) [6, 10, 28].
En metrología de relojería mecánica:
$$1\text{ PPM} = 86400\text{ s/día} \times 10^{-6} = 0.0864\text{ segundos al día (s/d)}$$

Una desviación típica de $+35\text{ PPM}$ en el oscilador de la tarjeta de sonido induce un error sistemático de $+3.024\text{ s/d}$ en la medición de un calibre perfecto.

### 2.2 Auto-Calibración con Estándar Atómico GPS 1PPS
Para calibrar la frecuencia de muestreo real ($f_{s,\text{real}}$) del ADC, se utiliza un módulo GPS comercial con salida **1PPS (1 Pulse Per Second)** acoplado al sensor piezoeléctrico [6].
* **Flanco de subida:** El pulso emite un flanco con un *jitter* $< 30\text{ ns}$ sincronizado con la red de relojes atómicos GPS [6].
* **Regresión Lineal OLS:** Se detecta el instante fraccionario del ataque mediante interpolación parabólica sub-muestra y se ajusta la recta sobre los tiempos de arribo atómicos $t_k = k / f_{\text{pps}}$ [6]:
  $$\text{Onsets}_k = f_{s,\text{real}} \cdot t_k + \text{offset}$$
* La pendiente de la regresión entrega $f_{s,\text{real}}$ con una precisión de $\pm 0.1\text{ PPM}$ [6].

### 2.3 Filtro PLL/FLL para Disciplina Frecuencial Continua
Inspirado en el algoritmo de disciplina de reloj de NTP, se implementa un bucle de enganche de fase/frecuencia (PLL/FLL) impulsado por un controlador Proporcional-Integrador (PI) [9, 28].
En cada bloque de audio de $N$ muestras capturado a las marcas de tiempo monotónicas $T_{\text{mono}}[k]$:

1. **Error de fase:**
   $$e[k] = T_{\text{mono}}[k] - T_{\text{estimado}}[k]$$
2. **Controlador PI (Ajuste de Frecuencia e Integración):**
   $$\text{drift\_ppm}[k] = \text{drift\_ppm}[k-1] + K_i \cdot e[k] \cdot 10^6$$
   $$\phi_{\text{corr}}[k] = K_p \cdot e[k]$$
3. **Proyección de tiempo esperado:**
   $$T_{\text{estimado}}[k+1] = T_{\text{estimado}}[k] + \frac{N}{f_{s,\text{nominal}}} \cdot \left(1 + \frac{\text{drift\_ppm}[k]}{10^6}\right) + \phi_{\text{corr}}[k]$$

Este filtro absorbe el *jitter* del bus USB sin realizar un costoso remuestreo del audio en tiempo real, inyectando $\text{drift\_ppm}$ directamente en el motor de métricas [6, 9].

---

## 3. Física del Escape de Áncora Suizo y Análisis Tri-fásico (P1, P2, P3)

### 3.1 Mecánica de la Oscilación y Fases Acústicas
En cada alternancia (medio ciclo de oscilación del volante), la interacción del conjunto volante-áncora-rueda de escape produce tres impulsos acústicos característicos [22]:

```text
[ Volante Girando ] ──>  P1 (Desbloqueo)  ──>  P2 (Impulso)  ──>  P3 (Caída/Bloqueo)
                         │                     │                  │
                         └────── t_lift (Tiempo de Alzado) ───────┘
```

1. **P1 (Desbloqueo / *Unlocking*):**
   La elipse del platillo del volante entra en la horquilla del áncora y desplaza la paleta, liberando el diente de la rueda de escape [22].
   * *Firma:* Transitorio tenue de baja amplitud y alta frecuencia [22].
   * *Importancia:* Es el **único evento provocado directamente por el volante de inercia** al cruzar por su posición de reposo [22].

2. **P2 (Impulso / *Impulse*):**
   El diente de la rueda de escape impulsa el plano de inclinación de la paleta del áncora, transmitiendo energía al volante [22].

3. **P3 (Caída y Bloqueo / *Drop and Lock*):**
   El diente de la rueda de escape cae libremente y choca contra la superficie de bloqueo de la otra paleta; el áncora impacta contra la clavija de tope (*banking pin*) [22].
   * *Firma:* Impacto acústico de **máxima energía y amplitud dominante** [22].

### 3.2 Ángulo de Alzado (*Lift Angle*) y Amplitud del Volante
El intervalo transcurrido entre **P1** y **P3** es el **tiempo de alzado** ($t_{\text{lift}}$) [22]. Basándose en el modelo de oscilación armónica simple del volante, la amplitud en grados ($\theta$) se calcula mediante la ecuación horológica fundamental [22]:

$$\theta = \frac{\text{LA}}{2 \cdot \sin(\pi \cdot f \cdot t_{\text{lift}})}$$

Donde:
* $\text{LA}$: Ángulo de alzado (*Lift Angle*) del calibre (ej. $52^\circ$ por defecto) [22].
* $f$: Frecuencia del reloj en Hz ($f = \text{BPH} / 7200$, ej. $4\text{ Hz}$ para $28,800\text{ A/h}$) [22].
* $t_{\text{lift}}$: Tiempo de alzado en segundos ($t_{\text{P3}} - t_{\text{P1}}$) obtenido de las plantillas [22].

### 3.3 Origen Físico del Beat Error: P1 vs. P3
* **Causa Mecánica:** El *Beat Error* (error de punto muerto) ocurre cuando el rubí elipse no coincide con el punto muerto neutro de la horquilla del áncora en posición de reposo. Esto hace que el volante recorra ángulos distintos para desenganchar el escape en el *tic* que en el *tac*.
* **Medición en P1 (Puro del Volante):** Al medir la asimetría de tiempo sobre los instantes **P1**, el software extrae el *Beat Error* puro del órgano regulador, libre de aberraciones geométricas de las paletas.
* **Medición en P3 (Comercial / Weishi):** Dispositivos comerciales miden en **P3** por su alta amplitud (SNR) [22]. Sin embargo, si existe una asimetría de lubricación o penetración entre paletas ($\Delta t_{\text{lift}} = t_{\text{lift, tic}} - t_{\text{lift, toc}}$), la medición en P3 reflejará una combinación de ambos efectos [22]:
  $$\text{Beat Error}_{\text{P3}} = \text{Beat Error}_{\text{P1}} + \Delta t_{\text{lift}}$$

---

## 4. Modelos Matemáticos de Métricas Horológicas (`metrics.py`)

### 4.1 Modelo de Regresión Lineal Robusta Ponderada por Paridad
Para aislar la marcha de la asimetría de batido, el motor de métricas ajusta los instantes finos de llegada de los batimientos ($t_{\text{fine}}$) en función del índice entero acumulado $k$ mediante el modelo de regresión [22]:

$$t = a + P \cdot k + c \cdot (-1)^k$$

Donde:
* $k$: Índice entero de alternancia ($k = 0, 1, 2, \dots$).
* $P$: Pendiente real del periodo de batimiento en segundos.
* $c$: Coeficiente de asimetría de paridad (*tic/tac*).
* $(-1)^k$: Término de paridad (+1 para *tic*, -1 para *tac*).

### 4.2 Filtrado Robusto por Desviación Absoluta respecto a la Mediana (MAD)
Antes de resolver el sistema matricial, se descartan atípicos en cada paridad aplicando un filtro estricto a $5\sigma$ basado en la estimación robusta MAD [22]:
$$\sigma_{\text{MAD}} = 1.4826 \cdot \text{median}(|x_i - \text{median}(x)|)$$

### 4.3 Ponderación por Inversa de Varianza
Cada batimiento válido recibe un peso $w_i = 1 / \sigma_{\text{paridad}}^2$ derivado de la dispersión de residuos de su propia paridad, garantizando que el *jitter* asimétrico de un canal no sesgue la pendiente global $P$ [22].

### 4.4 Formulación de Salida de Métricas
* **Marcha (`rate_s_per_day`):**
  $$\text{rate\_s\_per\_day} = \left( \frac{P_{\text{nominal}}}{P} - 1 \right) \times 86400$$
  donde $P_{\text{nominal}} = 3600 / \text{BPH}$.

* **Error de Batimiento (`beat_error_ms`):**
  Se proyecta $t_{\text{P1}, k} = t_{\text{fine}, k} - (\text{onset\_ref} - \text{onset\_P1})_{\text{paridad}}$ y se calcula [22]:
  $$\text{Beat Error (ms)} = 2 \cdot |c| \times 1000$$

---

## 5. Estrategia Multimodal y Procesamiento Adaptativo

### 5.1 Modo Diagnóstico de Largo Plazo (5+ min)
* **Operador de Energía de Teager-Kaiser (TKEO):** Algoritmo de complejidad $O(N)$ para extracción instantánea de la envolvente de señal [22, 40]:
  $$\Psi[x[n]] = x^2[n] - x[n-1]x[n+1]$$
* **Análisis Espectral de Periodicidad:** Se aplica una Transformada Rápida de Fourier (FFT) o Periodograma de Lomb-Scargle sobre la serie temporal de variaciones de intervalo ($\Delta t$) para identificar fallos cíclicos en el tren de rodaje [22, 40]:
  * Rueda de escape: impulsos periódicos cada $\sim 7.5\text{ s}$.
  * Rueda cuarta / segundero: anomalías cíclicas cada $60\text{ s}$.

### 5.2 Modo Regulación en Vivo (Wavelets + Ventana Adaptativa)
* **Transformada Wavelet Discreta/Continua (DWT/CWT Morlet):** Aísla los transitorios agudos del escape ($8\text{--}16\text{ kHz}$) eliminando los ruidos metálicos graves provocados por herramientas de regulación [22, 40].
* **Detector de Cambio Abrupto (*Change-Point Detection*):** Si la varianza inter-tic supera un umbral de $3\sigma$, se purga el buffer histórico y se contrae la ventana a $4\text{--}8$ tics, ofreciendo una latencia de respuesta $< 0.5\text{ s}$ para ajustes activos [22, 40].

### 5.3 Módulos de Inspección Óptica y Láser (240 FPS / RP2040)
* **Computer Vision (240 FPS):** Rastreos por flujo óptico en los puntos de retorno angular ($\omega = 0^\circ/\text{s}$) miden la amplitud real directa y despejan el *Lift Angle* real en conjunto con el tiempo de alzado acústico [22, 40]:
  $$\text{Lift Angle} = \theta_{\text{óptica}} \cdot \sin\left(\frac{\pi \cdot t_{\text{lift acoust}}}{T}\right)$$
* **Lector Láser DIY:** Microcontroladores dedicados (RP2040 con máquinas de estado PIO / STM32) miden las interrupciones de un haz láser sobre las palas del volante con resolución de nanosegundos [40].

---

## 6. Referencias Documentales y Fuentes del Proyecto

1. **Arm Limited (2019).** *AArch64 Programmer's Guide: Generic Timer*. Reference ARM062-1010708621-30. [1, 2]
2. **Arm Limited (2020).** *Arm Architecture Reference Manual for A-profile architecture: D12.2.2 The virtual counter*. [8, 10]
3. **Microsoft Learn (2024).** *Acquiring high-resolution time stamps*. Win32 Apps System Information Guide. [3, 17, 18, 19]
4. **Microsoft Learn (2024).** *GetSystemTimePreciseAsFileTime function (sysinfoapi.h)*. [17, 18]
5. **Red Hat Enterprise Linux (2023).** *Chapter 15. Timestamping - Reference Guide: Real Time Linux 7*. [13]
6. **Linux Kernel Documentation.** *Clock sources, Clock events, sched_clock() and delay timers*. Kernel.org. [11, 14]
7. **Mills, D. L. (2022).** *Clock Discipline Algorithm*. Network Time Protocol (NTP) Specification & Architecture. [9]
8. **Gregg, B.** *The Speed of Time*. Systems Performance & Microbenchmarking Infrastructure. [28]
9. **Kvolkovich, K. / 0xAX.** *Clocksource framework and Linux Timers Internals*. GitBook / GitHub. [12, 14]
10. **Lucini, M. et al. (2026).** *High-Precision Software Time Measurement: Architectural Foundations, Subsystem Mechanics, and Low-Latency Implementation Strategies*. Documento Markdown interno del cuaderno. [18]
11. **Gemini Notebook Sessions (2026).** *Calibración de Audio con GPS*, *Optimización de Software para Cronocomparador*, *Preparación para Proyecto Timegrapher*. [6, 22, 40]
12. **Watch Escape Analyzer EVO.** *hoja_de_ruta_y_arquitectura_futura_watch_escape_analyzer_evo.md*. [40]

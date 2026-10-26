# Advanced Timegrapher

Cronocomparador (*timegrapher*) por software para relojes mecánicos y de cuarzo. Escucha el
tic-tac con un micrófono y muestra en tiempo real:

- **Marcha** (s/d): cuántos segundos adelanta o atrasa el reloj al día.
- **Error de batimiento** (ms): cuánto difieren el tic y el tac.
- **Amplitud** (°): cuánto gira el volante.

> **Versión beta.** La aplicación ya mide, pero necesitamos tus resultados y tus reportes de
> fallos para afinarla. Ver [Ayúdanos a mejorarla](#ayúdanos-a-mejorarla).

*English: software timegrapher for mechanical and quartz watches (rate, beat error, amplitude).
The interface is available in English and Spanish (EN/ES selector in the top bar). Bug reports
and measurement results are welcome in English on the
[issue tracker](https://github.com/SPQR14/advanced_timegrapher/issues).*

---

## Qué necesitas

- Un ordenador con **macOS, Windows o Linux**.
- Un **micrófono**. Lo mejor es un micrófono de contacto con pinza o soporte (por ejemplo, un
  micrófono piezoeléctrico USB como el CM18). Un micrófono normal también sirve si el reloj
  está muy cerca y el entorno es silencioso.

## Instalación

### Con el instalador (recomendado)

Descarga la versión para tu sistema en
[Releases](https://github.com/SPQR14/advanced_timegrapher/releases).

Las versiones beta **no están firmadas**, así que el sistema avisa la primera vez:

- **macOS:** clic derecho sobre la aplicación → **Abrir** → **Abrir**. Al pedir acceso al
  micrófono, acepta (si lo rechazaste: Ajustes del Sistema → Privacidad y seguridad →
  Micrófono).
- **Windows:** en el aviso de SmartScreen, **Más información** → **Ejecutar de todas formas**.
- **Linux:** da permiso de ejecución al archivo (`chmod +x`) y ábrelo.

### Desde el código fuente

Requiere Python 3.11 o superior.

```bash
git clone https://github.com/SPQR14/advanced_timegrapher.git
cd advanced_timegrapher
python -m venv .venv
.venv/bin/python -m pip install -e .        # Windows: .venv\Scripts\python.exe -m pip install -e .
.venv/bin/adv-tg                            # Windows: .venv\Scripts\adv-tg
```

En Linux puede hacer falta PortAudio: `sudo apt install libportaudio2`.

## Tu primera medida

1. **Conecta el micrófono** y abre la aplicación. Usa la entrada de audio predeterminada del
   sistema; puedes elegir otra en **Dispositivo IN**.
2. **Coloca el reloj** en el micrófono, en la posición que quieras medir (esfera arriba es la
   más habitual), y elige esa posición en la lista (DU, DD, CU, CD, CL, CR).
3. **Elige el calibre** en la lista. Si no está, deja *Custom* y pon a mano los **A/h**
   (alternancias por hora: 21600, 28800…) y el **ángulo de alzamiento** o de levantamiento
   (52° si no lo sabes; entonces la amplitud es orientativa).
4. Elige la **estabilización** (10–20 s: tiempo que se deja asentar al reloj antes de
   registrar) y la **duración** de la prueba (de 30 s a 1 h; 60 s está bien para empezar).
5. Pulsa **INICIAR**. Las primeras cifras aparecen a los pocos segundos y se vuelven más fiables
   a medida que pasa el tiempo (se calculan sobre los últimos 30 s).

### Cómo leer la pantalla

| Elemento | Qué significa |
|---|---|
| Tarjetas de marcha, error de batimiento y amplitud | `---.-`: todavía no hay datos suficientes o la confianza es baja. **En gris**: cifra dudosa, tómala con cautela. |
| Barra de calidad | Antes de detectar el reloj muestra el nivel del micrófono; después, la calidad de la detección (SNR y % de batimientos útiles). Verde: bien; ámbar: mejorable; rojo con ⚠: saturación, baja la ganancia del micrófono. |
| Cinta de papel (centro) | Marcha a lo largo de la prueba, una línea para el tic y otra para el tac (separadas por el error de batimiento). Las líneas verdes son los límites COSC (−4/+6 s/d). |
| Osciloscopio (abajo a la izquierda) | El último batimiento con sus tres sonidos: **P1** desbloqueo, **P2** impulso, **P3** caída. |
| Forma de onda (abajo a la derecha) | La señal del micrófono en tiempo real. |
| Deriva del reloj (estadísticas) | Error del reloj interno de tu tarjeta de sonido, que ya se corrige en la marcha (ver [Calibración](#calibración-del-reloj-de-la-tarjeta)). |

### Consejos para una buena medida

- Busca un sitio **silencioso**. Los motores (motos, coches) y los golpes en la mesa estropean
  sobre todo el error de batimiento.
- Apoya el soporte del micrófono sobre **espuma** o un trapo doblado: un micrófono de contacto
  capta sobre todo la vibración de la mesa.
- Si la barra se pone **roja con ⚠**, la señal satura: baja la ganancia del micrófono en el
  sistema o aleja un poco el reloj.
- Los relojes económicos varían varios s/d de un minuto a otro; para comparar posiciones usa
  pruebas de 2 minutos o más.

## Relojes de cuarzo

Marca **Cuarzo** junto a los A/h y elige los **pasos por segundo** del segundero (1/s en la
mayoría; 16/s en los de segundero "barrido" como los Bulova Precisionist). Solo se mide la
marcha. Muchos cuarzos corrigen su marcha a saltos cada pocos segundos o minutos, así que usa
pruebas largas (5 min o más).

## Calibración del reloj de la tarjeta

La tarjeta de sonido tiene su propio reloj, y su error se suma a la marcha medida (cada ppm
equivale a 0.0864 s/d; en una tarjeta USB típica, de 1 a 4 s/d). La aplicación lo estima
sola durante cada prueba usando la hora del sistema, y lo corrige si la estimación es fiable.

Para una corrección más precisa, pulsa **Calibrar reloj…** (bajo las estadísticas):

- Tras una prueba larga (15 min o más), **Usar la estimación en vivo** la guarda para ese
  micrófono.
- Si tienes un reloj de cuarzo de error conocido o un GPS con salida PPS, introduce el valor
  medido a mano.

La calibración se guarda por micrófono y se aplica desde la siguiente prueba.

## Guardar resultados

- **Grabar Sonido**: guarda la grabación de la prueba en FLAC, con el micrófono, la fecha y la
  calibración dentro del archivo. Pulsado durante la prueba, la guarda al terminar.
- **Guardar Posición**: añade la medida actual a la tabla de posiciones; **Estadísticas
  Globales** muestra la diferencia entre posiciones.
- **Exportar resultados a CSV**, **Exportar resultados de la prueba**, **Exportar Trazas** e
  **Imprimir Trazas**: los datos de la prueba en CSV y las gráficas como imagen o en papel.

## Ayúdanos a mejorarla

Abre un reporte en
[GitHub Issues](https://github.com/SPQR14/advanced_timegrapher/issues) (necesitas una cuenta
gratuita de GitHub):

- **Resultado de medida:** reloj y calibre, A/h, ángulo, posición, las cifras de la aplicación
  y, si los tienes, las de otro cronocomparador (tg, Watch Accuracy Meter, un aparato
  comercial…). Adjuntar la grabación (Grabar Sonido) ayuda muchísimo.
- **Fallo:** qué hiciste, qué esperabas y qué pasó. Incluye la versión y la carpeta de
  registros, que aparecen en **Acerca de**; adjunta los archivos `adv_tg*.log` de esa carpeta.

## Limitaciones de la beta

- El error de batimiento es sensible al ruido ambiental.
- No hay modo de medida para relojes de alta frecuencia por encima de 36000 A/h.
- La aplicación no está firmada (ver [Instalación](#instalación)).

## Apoya el proyecto

Si te resulta útil, puedes invitarme a un café: <https://buymeacoffee.com/sqpr14> (también
desde el botón ☕ de la aplicación).

## Licencia y documentación técnica

Software libre bajo la licencia **GNU GPL v3.0 o posterior** (ver [`LICENSE`](LICENSE)).

La documentación técnica (arquitectura, algoritmos, validación y herramientas de consola) está
en [`docs/resumen_beta1.md`](docs/resumen_beta1.md) y en los documentos de resultados de cada
fase de [`docs/`](docs/).

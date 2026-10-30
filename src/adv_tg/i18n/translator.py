"""Lightweight bilingual translator (EN/ES) for horological terminology and UI labels."""

from __future__ import annotations

from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup

logger = LoggerSetup.setup_logger(__name__)
_dbg = Debugger.get(__name__)


class Translator:
    """Provides localized strings in English ('en') and Spanish ('es') for horological metrics."""

    DICTIONARIES: dict[str, dict[str, str]] = {
        "en": {
            "app_title": "Advanced Timegrapher — Horological DSP Test Bench",
            "rate": "Rate",
            "beat_error": "Beat Error",
            "amplitude": "Amplitude",
            "lift_angle": "Lift Angle",
            "bph": "BPH",
            "quartz": "Quartz",
            "quartz_steps": "Steps",
            "quartz_tooltip": "Quartz watch (1 step per second): caliber, BPH and lift angle are disabled; only the rate is measured",
            "confidence": "Confidence",
            "test_name": "Test Name",
            "test_name_placeholder": "e.g. Movement #01 - Service",
            "caliber": "Caliber",
            "position": "Position",
            "in_device": "IN Device",
            "sample_rate": "Sample Rate",
            "stab_time": "Stabilization",
            "test_duration": "Test Duration",
            "language": "Lang",
            "signal_quality": "Signal Quality / SNR",
            "statistics": "Telemetry Statistics",
            "stat_max": "Max",
            "stat_min": "Min",
            "stat_delta": "Delta",
            "stat_mean": "Mean",
            "stat_elapsed": "Elapsed Time",
            "btn_start": "START",
            "btn_stop": "STOP",
            "btn_center_trace": "Center Trace",
            "btn_tape_zoom": "Zoom {factor}×",
            "btn_save_position": "Save Position",
            "position_no_measurement": "No measured reading yet in this session: start a test and wait for the figures.",
            "btn_save_csv": "Export Results to CSV",
            "btn_export_test_results": "Export Test Results",
            "btn_buy_coffee": "☕ Buy me a coffee",
            "no_signal_macos": (
                "No sound from the microphone. Allow it in System Settings → Privacy & Security → "
                "Microphone, then restart the app. Also check the input device and its volume."
            ),
            "no_signal_windows": (
                "No sound from the microphone. Allow it in Settings → Privacy & security → "
                "Microphone (desktop apps), and check the input device and its volume."
            ),
            "no_signal_other": "No sound from the microphone: check the input device, its volume and that it is not muted.",
            "recording_too_long": "Recording is only available for tests up to {limit} s",
            "btn_report": "Report",
            "btn_report_tooltip": "Create a zip with logs and settings to attach to a GitHub issue",
            "report_title": "Report a result or a problem",
            "report_intro": (
                "This creates a .zip file with the information we need. Then open "
                "<a href='{url}'>GitHub Issues</a>, choose <b>Measurement result</b> or "
                "<b>Bug report</b>, fill in the form and drag the .zip into it."
            ),
            "report_description": "What happened? (optional, also goes into the zip)",
            "report_placeholder": "Watch, calibre, what you did, what you expected…",
            "report_include_recording": "Include the last session recording (FLAC)",
            "report_no_recording": "No recording available: run a session with a microphone first",
            "report_privacy": (
                "The zip contains the log files of {logs} (they include file paths with your "
                "user name and your audio device names), your settings and clock calibrations. "
                "You can open it and check it before sharing."
            ),
            "report_create": "Create report…",
            "report_done": "Report saved:\n{path}\n\nNow open GitHub and attach this file.",
            "report_open_github": "Open GitHub",
            "report_show_file": "Show file",
            "open_logs_folder": "Open Logs Folder",
            "open_calibration_folder": "Open Calibration Folder",
            "menu_help": "Help",
            "menu_tools": "Tools",
            "btn_about": "About",
            "about_title": "About Advanced Timegrapher",
            "about_body": (
                "Software timegrapher for mechanical and quartz watches.<br><br>"
                "Licence: {license}. Source code and bug reports: "
                "<a href='{repo}'>{repo}</a><br><br>"
                "Python {python} · Qt {qt} · PyQt6 {pyqt}<br>{system}<br><br>"
                "Settings and calibrations: {config}<br>Logs: {logs}"
            ),
            "btn_calibrate": "Calibrate clock…",
            "btn_calibrate_tooltip": "Enter the sound card's clock error (ppm) for this device",
            "cal_unavailable": "Select a hardware input device to calibrate its clock",
            "cal_title": "Sound card clock calibration",
            "cal_device": "Device: {device} at {fs} Hz",
            "cal_current": "Applied now: {ppm:+.3f} ± {sigma:.3f} ppm (level {level}, {date})",
            "cal_none": "No calibration stored: the rate is not corrected for the card's clock.",
            "cal_help": (
                "ppm > 0 means the card's clock runs fast, which makes watches look slow by "
                "0.0864 s/d per ppm. Get the value from a quartz watch of known error, a GPS PPS "
                "or the live estimate of a long session. It applies from the next session."
            ),
            "cal_ppm": "Clock error",
            "cal_sigma": "Uncertainty (±)",
            "cal_note": "Note",
            "cal_note_placeholder": "method, reference, temperature…",
            "cal_bias": "An uncorrected watch would read {bias:+.3f} s/d",
            "cal_use_live": "Use live estimate",
            "cal_live_tooltip": "Last session: {ppm:+.3f} ± {sigma:.3f} ppm (level {level})",
            "cal_live_note": "live estimate (level {level})",
            "cal_save": "Save",
            "cal_forget": "Delete all",
            "cal_forget_confirm": "Delete every calibration of {device} at {fs} Hz?",
            "cal_col_date": "Date",
            "cal_col_level": "Level",
            "cal_col_note": "Note",
            "cal_ref_title": "From a reference watch of known rate",
            "cal_ref_help": (
                "Measure a quartz watch whose rate you know (checked against a time signal over "
                "several days), then enter both rates: ppm = (known − measured) / 0.0864. The "
                "measured rate is prefilled with the last session's reading without clock correction."
            ),
            "cal_ref_measured": "Rate measured here (uncorrected)",
            "cal_ref_known": "Known rate of the watch",
            "cal_ref_result": "Card clock error: {ppm:+.3f} ppm",
            "cal_ref_apply": "Use this value",
            "cal_ref_apply_tooltip": "Copy the result to “Clock error”; press Save to store it",
            "cal_ref_note": "reference watch: known {known:+.3f} s/d, measured {measured:+.3f} s/d",
            "normalize_audio": "Normalize audio",
            "normalize_audio_tooltip": (
                "Enable it ONLY when the microphone gain cannot be adjusted (the input saturates "
                "even at minimum gain). It brings the signal level to -6 dBFS by software before "
                "the analysis, so saturated beats are still measured. Clipping cannot be undone: "
                "lower the gain whenever possible. Applies from the next session."
            ),
            "error_invalid_sample_rate": (
                "the device does not accept this sample rate: choose the one it runs at "
                "(44.1 or 48 kHz)"
            ),
            "btn_export_traces": "Export Traces",
            "btn_print_traces": "Print Traces",
            "btn_record_audio": "Record Audio",
            "btn_analyze_recording": "Analyze Recording",
            "btn_analyze_recording_tooltip": (
                "Look for cyclical gear-train defects (escape wheel, fourth wheel) in the last "
                "session's recording, or in a WAV/FLAC file"
            ),
            "an_title": "Recording analysis — gear train",
            "an_source": "Recording: {name}",
            "an_last_session": "last session",
            "an_summary": (
                "{duration:.0f} s · {bph} A/h · {used} beats used ({missing} missing) · "
                "mean rate {rate:+.1f} s/d (no clock correction)"
            ),
            "an_teeth": "Escape wheel teeth:",
            "an_teeth_tooltip": "15 in almost every Swiss and Japanese calibre; count them if in doubt",
            "an_escape_period": "→ one turn every {period:.2f} s at {bph} A/h (2 × teeth × 3600 / BPH)",
            "an_open_file": "Open file…",
            "an_running": "Detecting beats… (about 1 s per 40 s of audio)",
            "an_failed": "The analysis failed: {error}",
            "an_too_few_beats": "Only {found} beats could be timed: check the BPH and the signal level.",
            "an_quartz": "Not applicable to quartz watches: the gear train analysis needs an escapement.",
            "an_busy": "Stop the session before analysing its recording.",
            "an_found": "Periodic components found: {n}.",
            "an_none_found": "No periodic gear-train component found.",
            "an_warn_short_for_escape": "Too short for the escape wheel: record at least {min_s:.0f} s.",
            "an_warn_short_for_fourth": "Too short for the fourth wheel: record at least {min_s:.0f} s (240 s test).",
            "an_warn_many_missing": "Many beats were missed: the result is less reliable.",
            "an_plot_timing": "Timing residual per beat (rate, drift and beat error removed)",
            "an_plot_energy": "P3 drop energy (Teager-Kaiser) relative to its median",
            "an_plot_spectrum": "Lomb-Scargle periodogram (normalised)",
            "an_axis_time": "Time (s)",
            "an_axis_period": "Period (s)",
            "an_col_component": "Wheel",
            "an_col_series": "Series",
            "an_col_expected": "Expected period",
            "an_col_measured": "Measured period",
            "an_col_amplitude": "Amplitude",
            "an_col_ripple": "Rate ripple",
            "an_col_fap": "False-alarm prob.",
            "an_col_verdict": "Verdict",
            "an_escape_wheel": "Escape wheel",
            "an_fourth_wheel": "Fourth wheel",
            "an_series_timing": "Timing",
            "an_series_energy": "P3 energy",
            "an_verdict_present": "Present",
            "an_verdict_absent": "Not detected",
            "an_verdict_unresolvable": "Record ≥ {min_s:.0f} s",
            "an_tab_gear": "Gear train",
            "an_tab_features": "Features",
            "an_tab_diagnosis": "Diagnosis",
            "an_export": "Export features…",
            "an_export_tooltip": "Save the feature vectors, the gear-train components and the findings as JSON (a record for the ML dataset)",
            "an_whole": "Whole recording",
            "an_features_help": (
                "One column per 60 s window and one for the whole recording. Escape-wheel energy at "
                "f_beat / (2 × teeth) (f_beat / 30 with 15 teeth); fourth-wheel energy at 1/60 Hz, "
                "only for the whole recording and from 180 s of audio (one 60 s window holds a "
                "single turn, which cannot be told from the drift of the rate)."
            ),
            "an_f_n_beats": "Beats",
            "an_f_rate": "Rate (s/d)",
            "an_f_be": "Beat error (ms)",
            "an_f_amp": "Amplitude (°)",
            "an_f_escape_power": "Escape wheel: LS power",
            "an_f_escape_amp": "Escape wheel: amplitude (µs)",
            "an_f_fourth_power": "Fourth wheel: LS power",
            "an_f_fourth_amp": "Fourth wheel: amplitude (µs)",
            "an_f_d12_mean": "t_P2 − t_P1: mean (ms)",
            "an_f_d12_var": "t_P2 − t_P1: variance (ms²)",
            "an_f_d12_skew": "t_P2 − t_P1: skewness",
            "an_f_d12_kurt": "t_P2 − t_P1: excess kurtosis",
            "an_f_d23_mean": "t_P3 − t_P2: mean (ms)",
            "an_f_d23_var": "t_P3 − t_P2: variance (ms²)",
            "an_f_d23_skew": "t_P3 − t_P2: skewness",
            "an_f_d23_kurt": "t_P3 − t_P2: excess kurtosis",
            "an_f_e1_e2": "E_P1 / E_P2 (median)",
            "an_f_e3_e2": "E_P3 / E_P2 (median)",
            "an_f_spurious": "Beats with a sound before P1",
            "an_f_jumps": "Abrupt timing excursions",
            "an_col_finding": "Indication",
            "an_col_level": "Level",
            "an_col_evidence": "Evidence",
            "an_no_findings": "No indication of a defect.",
            "an_level_probable": "Probable",
            "an_level_possible": "Possible",
            "an_finding_rebanking": "Rebanking / knocking (ellipse hitting the fork)",
            "an_finding_pallet_lubrication": "Lack of lubrication on the pallet stones",
            "an_finding_magnetism": "Magnetism / off-centre hairspring",
            "an_finding_escape_wheel": "Escape wheel: eccentricity or damaged tooth",
            "an_finding_fourth_wheel": "Fourth wheel: bent pivot or dirty tooth",
            "an_positional": (
                "Positions {positions}: ΔRate H−V {rate:+.1f} s/d · ΔAmp H−V {amp:+.0f}° · "
                "beat error spread {be:.2f} ms"
            ),
            "an_positional_none": (
                "No horizontal-vertical differentials: save at least one horizontal (DU/DD) and "
                "one vertical (CU/CD/CL/CR) position."
            ),
            "an_diag_help": (
                "Rules: rebanking = amplitude > 310° with a sound before P1 and abrupt timing "
                "excursions; lubrication = P2 more than 10 dB below P3 and an unstable P1→P2 "
                "(variation > 5 %); magnetism = rate > +30 s/d with a beat error that changes with "
                "the position (> 0.5 ms). The thresholds are provisional, not calibrated with "
                "labelled watches: an indication to check on the bench, never a diagnosis."
            ),
            "an_help": (
                "A component is present when its false-alarm probability is below 1 %. In timing "
                "it is a rate ripple once per turn of the wheel; in P3 energy, a drop that gets "
                "louder and softer. Escape wheel: eccentric wheel or pinion, damaged or dirty "
                "tooth, bent pivot. Fourth wheel: eccentric wheel, damaged tooth, seconds hand "
                "rubbing. It is a symptom, not a diagnosis: repeat it in another position and "
                "after winding before opening the watch."
            ),
            "dialog_save_csv": "Export Telemetry Results to CSV",
            "dialog_export_test_results": "Export Global Statistics & Positional Delta",
            "dialog_export_traces": "Export Audio Traces",
            "dialog_save_audio": "Save Recorded Audio",
            "btn_global_stats": "Global Statistics (Positional Delta)",
            "col_pos": "Pos",
            "col_rate": "Rate (s/d)",
            "col_be": "BE (ms)",
            "col_amp": "Amp (°)",
            "global_mean": "Global Mean",
            "global_max_delta": "Positional Delta",
            "paper_tape_title": "Paper Tape (timing of every tic and tac; slope = rate)",
            "paper_tape_x": "Elapsed Time (s)",
            "paper_tape_y": "Beat offset (ms)",
            "oscilloscope_title": "Real-Time Escapement Oscilloscope (P1 / P2 / P3)",
            "oscilloscope_x": "Time Window (ms)",
            "oscilloscope_y": "Normalized Acoustic Amplitude",
            "waveform_title": "Real-Time Audio Waveform",
            "waveform_x": "Time (ms)",
            "waveform_y": "Acoustic Amplitude",
            "synthetic_device": "Synthetic Ground-Truth Generator",
            "status_idle": "READY",
            "status_stabilizing": "STABILIZING...",
            "status_running": "MEASURING",
            "clock_drift": "Clock Drift",
            "effective_fs": "Effective Sample Rate",
            "status_drift_format": (
                "Clock Drift: {ppm:+.2f} ppm | Effective Fs: {fs:.2f} Hz | Bias: {bias:+.2f} s/d"
            ),
            "status_capture_format": "Audio received: {pct:.1f} % | Overflows: {ovf}",
            "status_stopping": "STOPPING...",
            "mock_mode": "MOCK",
            "mock_tooltip": "Simulated data to test the GUI (no real measurement)",
            "coffee_tooltip": "Support the project: {url}",
            "beat_error_p3": "at the drop (P3): {value}",
            "real_bph": "Real BPH: {value}",
            "beat_error_tooltip": (
                "Measured at P1 (unlocking), as Witschi does. The P3 (drop) figure is for "
                "comparison with timegraphers that time the loudest sound; the two differ by "
                "the tic/tac lift-time difference."
            ),
            "coffee_open_failed": "Could not open the browser. Visit {url}",
            "mock_badge": "MOCK · SIMULATED DATA",
            "mock_no_hardware": "No hardware input device available: MOCK stays on",
            "no_recording_available": "No recording available: run a session first",
            "recording_replay": "File replay mode: the session is not recorded",
            "recording_saved": "Recording saved: {path}",
            "drift_level_format": "{ppm:+.2f} ± {sigma:.2f} ppm (level {level})",
            "drift_uncalibrated_format": "{ppm:+.2f} ppm (uncalibrated)",
            "capturing_no_measure": "CAPTURING (no measurement)",
        },
        "es": {
            "app_title": "Advanced Timegrapher — Banco de Pruebas DSP Horológico",
            "rate": "Marcha",
            "beat_error": "Error de Batido",
            "amplitude": "Amplitud",
            "lift_angle": "Ángulo de Alzamiento",
            "bph": "A/h",
            "quartz": "Cuarzo",
            "quartz_steps": "Pasos",
            "quartz_tooltip": "Reloj de cuarzo (1 paso por segundo): calibre, BPH y ángulo de levantamiento se deshabilitan; solo se mide la marcha",
            "confidence": "Confianza",
            "test_name": "Nombre de la prueba",
            "test_name_placeholder": "ej. Calibre #01 - Servicio",
            "caliber": "Calibre",
            "position": "Posición",
            "in_device": "Dispositivo IN",
            "sample_rate": "Frec. Muestreo",
            "stab_time": "Estabilización",
            "test_duration": "Duración Test",
            "language": "Idioma",
            "signal_quality": "Calidad de Señal / SNR",
            "statistics": "Estadísticas de Telemetría",
            "stat_max": "Máx",
            "stat_min": "Mín",
            "stat_delta": "Delta",
            "stat_mean": "Media",
            "stat_elapsed": "Tiempo Transcurrido",
            "btn_start": "INICIAR",
            "btn_stop": "DETENER",
            "btn_center_trace": "Centrar Trazo",
            "btn_tape_zoom": "Zoom {factor}×",
            "btn_save_position": "Guardar Posición",
            "position_no_measurement": "Todavía no hay ninguna lectura medida en esta sesión: inicia una prueba y espera a las cifras.",
            "btn_save_csv": "Exportar resultados a CSV",
            "btn_export_test_results": "Exportar resultados de la prueba",
            "btn_buy_coffee": "☕ Buy me a coffee",
            "no_signal_macos": (
                "No llega sonido del micrófono. Permítelo en Ajustes del Sistema → Privacidad y "
                "seguridad → Micrófono y reinicia la aplicación. Revisa también el dispositivo y su volumen."
            ),
            "no_signal_windows": (
                "No llega sonido del micrófono. Permítelo en Configuración → Privacidad y seguridad → "
                "Micrófono (aplicaciones de escritorio) y revisa el dispositivo y su volumen."
            ),
            "no_signal_other": "No llega sonido del micrófono: revisa el dispositivo, su volumen y que no esté silenciado.",
            "recording_too_long": "La grabación solo está disponible en pruebas de hasta {limit} s",
            "btn_report": "Reportar",
            "btn_report_tooltip": "Crea un zip con registros y ajustes para adjuntar en GitHub",
            "report_title": "Reportar un resultado o un problema",
            "report_intro": (
                "Esto crea un archivo .zip con la información que necesitamos. Después abre "
                "<a href='{url}'>GitHub Issues</a>, elige <b>Measurement result</b> (resultado) o "
                "<b>Bug report</b> (fallo), rellena el formulario y arrastra el .zip dentro."
            ),
            "report_description": "¿Qué pasó? (opcional, también va dentro del zip)",
            "report_placeholder": "Reloj, calibre, qué hiciste, qué esperabas…",
            "report_include_recording": "Incluir la grabación de la última sesión (FLAC)",
            "report_no_recording": "No hay grabación: haz primero una sesión con micrófono",
            "report_privacy": (
                "El zip contiene los registros de {logs} (incluyen rutas de archivos con tu "
                "nombre de usuario y los nombres de tus dispositivos de audio), tus ajustes y "
                "las calibraciones del reloj. Puedes abrirlo y revisarlo antes de compartirlo."
            ),
            "report_create": "Crear reporte…",
            "report_done": "Reporte guardado:\n{path}\n\nAhora abre GitHub y adjunta este archivo.",
            "report_open_github": "Abrir GitHub",
            "report_show_file": "Mostrar archivo",
            "open_logs_folder": "Abrir carpeta de registros",
            "open_calibration_folder": "Abrir carpeta de calibración",
            "menu_help": "Ayuda",
            "menu_tools": "Herramientas",
            "btn_about": "Acerca de",
            "about_title": "Acerca de Advanced Timegrapher",
            "about_body": (
                "Cronocomparador por software para relojes mecánicos y de cuarzo.<br><br>"
                "Licencia: {license}. Código fuente y reporte de fallos: "
                "<a href='{repo}'>{repo}</a><br><br>"
                "Python {python} · Qt {qt} · PyQt6 {pyqt}<br>{system}<br><br>"
                "Ajustes y calibraciones: {config}<br>Registros: {logs}"
            ),
            "btn_calibrate": "Calibrar reloj…",
            "btn_calibrate_tooltip": "Introduce el error del reloj de la tarjeta (ppm) de este dispositivo",
            "cal_unavailable": "Elige un dispositivo de entrada de hardware para calibrar su reloj",
            "cal_title": "Calibración del reloj de la tarjeta",
            "cal_device": "Dispositivo: {device} a {fs} Hz",
            "cal_current": "Se aplica ahora: {ppm:+.3f} ± {sigma:.3f} ppm (nivel {level}, {date})",
            "cal_none": "No hay calibración guardada: la marcha no se corrige por el reloj de la tarjeta.",
            "cal_help": (
                "ppm > 0 significa que el reloj de la tarjeta adelanta, lo que hace que los relojes "
                "parezcan atrasar 0.0864 s/d por cada ppm. Obtén el valor con un cuarzo de error "
                "conocido, un PPS de GPS o la estimación en vivo de una sesión larga. Se aplica "
                "desde la próxima sesión."
            ),
            "cal_ppm": "Error del reloj",
            "cal_sigma": "Incertidumbre (±)",
            "cal_note": "Nota",
            "cal_note_placeholder": "método, referencia, temperatura…",
            "cal_bias": "Un reloj sin corregir marcaría {bias:+.3f} s/d",
            "cal_use_live": "Usar la estimación en vivo",
            "cal_live_tooltip": "Última sesión: {ppm:+.3f} ± {sigma:.3f} ppm (nivel {level})",
            "cal_live_note": "estimación en vivo (nivel {level})",
            "cal_save": "Guardar",
            "cal_forget": "Borrar todas",
            "cal_forget_confirm": "¿Borrar todas las calibraciones de {device} a {fs} Hz?",
            "cal_col_date": "Fecha",
            "cal_col_level": "Nivel",
            "cal_col_note": "Nota",
            "cal_ref_title": "Desde un reloj de referencia de marcha conocida",
            "cal_ref_help": (
                "Mide un reloj de cuarzo cuya marcha conozcas (comprobada contra una señal horaria "
                "durante varios días) e introduce ambas marchas: ppm = (conocida − medida) / 0.0864. "
                "La marcha medida se rellena con la última lectura de la sesión sin corrección de reloj."
            ),
            "cal_ref_measured": "Marcha medida aquí (sin corregir)",
            "cal_ref_known": "Marcha conocida del reloj",
            "cal_ref_result": "Error del reloj de la tarjeta: {ppm:+.3f} ppm",
            "cal_ref_apply": "Usar este valor",
            "cal_ref_apply_tooltip": "Copia el resultado a «Error del reloj»; pulsa Guardar para almacenarlo",
            "cal_ref_note": "reloj de referencia: conocida {known:+.3f} s/d, medida {measured:+.3f} s/d",
            "normalize_audio": "Normalizar audio",
            "normalize_audio_tooltip": (
                "Actívalo SOLO cuando no sea posible ajustar la ganancia del micrófono (la entrada "
                "se satura incluso con la ganancia al mínimo). Lleva el nivel de la señal a -6 dBFS "
                "por software antes del análisis, de modo que los batimientos saturados se sigan "
                "midiendo. El recorte no se puede deshacer: baja la ganancia siempre que puedas. "
                "Se aplica desde la próxima sesión."
            ),
            "error_invalid_sample_rate": (
                "el dispositivo no acepta esta frecuencia de muestreo: elige la que usa "
                "(44.1 o 48 kHz)"
            ),
            "btn_export_traces": "Exportar Trazas",
            "btn_print_traces": "Imprimir Trazas",
            "btn_record_audio": "Grabar Sonido",
            "btn_analyze_recording": "Analizar Grabación",
            "btn_analyze_recording_tooltip": (
                "Busca defectos cíclicos del tren de rodaje (rueda de escape, cuarta rueda) en la "
                "grabación de la última sesión o en un archivo WAV/FLAC"
            ),
            "an_title": "Análisis de la grabación — tren de rodaje",
            "an_source": "Grabación: {name}",
            "an_last_session": "última sesión",
            "an_summary": (
                "{duration:.0f} s · {bph} A/h · {used} batimientos usados ({missing} perdidos) · "
                "marcha media {rate:+.1f} s/d (sin corrección de reloj)"
            ),
            "an_teeth": "Dientes de la rueda de escape:",
            "an_teeth_tooltip": "15 en casi todos los calibres suizos y japoneses; cuéntalos si dudas",
            "an_escape_period": "→ una vuelta cada {period:.2f} s a {bph} A/h (2 × dientes × 3600 / BPH)",
            "an_open_file": "Abrir archivo…",
            "an_running": "Detectando batimientos… (aprox. 1 s por cada 40 s de audio)",
            "an_failed": "El análisis falló: {error}",
            "an_too_few_beats": "Solo se pudieron medir {found} batimientos: revisa el BPH y el nivel de señal.",
            "an_quartz": "No aplicable a relojes de cuarzo: el análisis del rodaje necesita un escape.",
            "an_busy": "Detén la sesión antes de analizar su grabación.",
            "an_found": "Componentes periódicas encontradas: {n}.",
            "an_none_found": "No se encontró ninguna componente periódica del rodaje.",
            "an_warn_short_for_escape": "Demasiado corta para la rueda de escape: graba al menos {min_s:.0f} s.",
            "an_warn_short_for_fourth": "Demasiado corta para la cuarta rueda: graba al menos {min_s:.0f} s (prueba de 240 s).",
            "an_warn_many_missing": "Se perdieron muchos batimientos: el resultado es menos fiable.",
            "an_plot_timing": "Residuo de tiempo por batimiento (sin marcha, deriva ni error de batimiento)",
            "an_plot_energy": "Energía de la caída P3 (Teager-Kaiser) relativa a su mediana",
            "an_plot_spectrum": "Periodograma de Lomb-Scargle (normalizado)",
            "an_axis_time": "Tiempo (s)",
            "an_axis_period": "Periodo (s)",
            "an_col_component": "Rueda",
            "an_col_series": "Serie",
            "an_col_expected": "Periodo esperado",
            "an_col_measured": "Periodo medido",
            "an_col_amplitude": "Amplitud",
            "an_col_ripple": "Rizado de marcha",
            "an_col_fap": "Prob. falsa alarma",
            "an_col_verdict": "Veredicto",
            "an_escape_wheel": "Rueda de escape",
            "an_fourth_wheel": "Cuarta rueda",
            "an_series_timing": "Tiempo",
            "an_series_energy": "Energía P3",
            "an_verdict_present": "Presente",
            "an_verdict_absent": "No detectada",
            "an_verdict_unresolvable": "Graba ≥ {min_s:.0f} s",
            "an_tab_gear": "Tren de rodaje",
            "an_tab_features": "Características",
            "an_tab_diagnosis": "Diagnóstico",
            "an_export": "Exportar características…",
            "an_export_tooltip": "Guarda los vectores de características, las componentes del rodaje y los indicios en JSON (un registro para el dataset de ML)",
            "an_whole": "Grabación completa",
            "an_features_help": (
                "Una columna por ventana de 60 s y otra para la grabación completa. Energía de la "
                "rueda de escape en f_beat / (2 × dientes) (f_beat / 30 con 15 dientes); de la "
                "cuarta rueda en 1/60 Hz, solo para la grabación completa y a partir de 180 s de "
                "audio (una ventana de 60 s contiene una sola vuelta, indistinguible de la deriva "
                "de la marcha)."
            ),
            "an_f_n_beats": "Batimientos",
            "an_f_rate": "Marcha (s/d)",
            "an_f_be": "Error de batimiento (ms)",
            "an_f_amp": "Amplitud (°)",
            "an_f_escape_power": "Rueda de escape: potencia LS",
            "an_f_escape_amp": "Rueda de escape: amplitud (µs)",
            "an_f_fourth_power": "Cuarta rueda: potencia LS",
            "an_f_fourth_amp": "Cuarta rueda: amplitud (µs)",
            "an_f_d12_mean": "t_P2 − t_P1: media (ms)",
            "an_f_d12_var": "t_P2 − t_P1: varianza (ms²)",
            "an_f_d12_skew": "t_P2 − t_P1: asimetría",
            "an_f_d12_kurt": "t_P2 − t_P1: exceso de curtosis",
            "an_f_d23_mean": "t_P3 − t_P2: media (ms)",
            "an_f_d23_var": "t_P3 − t_P2: varianza (ms²)",
            "an_f_d23_skew": "t_P3 − t_P2: asimetría",
            "an_f_d23_kurt": "t_P3 − t_P2: exceso de curtosis",
            "an_f_e1_e2": "E_P1 / E_P2 (mediana)",
            "an_f_e3_e2": "E_P3 / E_P2 (mediana)",
            "an_f_spurious": "Batimientos con sonido antes de P1",
            "an_f_jumps": "Excursiones bruscas de tiempo",
            "an_col_finding": "Indicio",
            "an_col_level": "Nivel",
            "an_col_evidence": "Evidencia",
            "an_no_findings": "Ningún indicio de defecto.",
            "an_level_probable": "Probable",
            "an_level_possible": "Posible",
            "an_finding_rebanking": "Rebanking / knocking (rebote de la elipse en la horquilla)",
            "an_finding_pallet_lubrication": "Falta de lubricación en las paletas del áncora",
            "an_finding_magnetism": "Magnetismo / espiral descentrada",
            "an_finding_escape_wheel": "Rueda de escape: excentricidad o diente dañado",
            "an_finding_fourth_wheel": "Cuarta rueda: pivote doblado o diente sucio",
            "an_positional": (
                "Posiciones {positions}: ΔMarcha H−V {rate:+.1f} s/d · ΔAmp H−V {amp:+.0f}° · "
                "dispersión del error de batimiento {be:.2f} ms"
            ),
            "an_positional_none": (
                "Sin diferenciales horizontal-vertical: guarda al menos una posición horizontal "
                "(DU/DD) y una vertical (CU/CD/CL/CR)."
            ),
            "an_diag_help": (
                "Reglas: rebanking = amplitud > 310° con sonido antes de P1 y excursiones bruscas "
                "de tiempo; lubricación = P2 más de 10 dB por debajo de P3 y P1→P2 inestable "
                "(variación > 5 %); magnetismo = marcha > +30 s/d con un error de batimiento que "
                "cambia con la posición (> 0.5 ms). Los umbrales son provisionales, no calibrados "
                "con relojes etiquetados: un indicio a comprobar en el banco, nunca un diagnóstico."
            ),
            "an_help": (
                "Una componente está presente cuando su probabilidad de falsa alarma es menor del "
                "1 %. En el tiempo es un rizado de la marcha una vez por vuelta de la rueda; en la "
                "energía P3, una caída que suena más fuerte y más débil. Rueda de escape: rueda o "
                "piñón excéntricos, diente dañado o sucio, pivote doblado. Cuarta rueda: rueda "
                "excéntrica, diente dañado, segundero que roza. Es un síntoma, no un diagnóstico: "
                "repítelo en otra posición y tras dar cuerda antes de abrir el reloj."
            ),
            "dialog_save_csv": "Exportar Resultados de Telemetría a CSV",
            "dialog_export_test_results": "Exportar Estadísticas Globales y Delta Posicional",
            "dialog_export_traces": "Exportar Trazas de Audio",
            "dialog_save_audio": "Guardar Grabación de Audio",
            "btn_global_stats": "Estadísticas Globales (Delta Posicional)",
            "col_pos": "Pos",
            "col_rate": "Marcha (s/d)",
            "col_be": "EB (ms)",
            "col_amp": "Amp (°)",
            "global_mean": "Media Global",
            "global_max_delta": "Delta Posicional",
            "paper_tape_title": "Cinta de papel (cada tic y tac; la pendiente es la marcha)",
            "paper_tape_x": "Tiempo Transcurrido (s)",
            "paper_tape_y": "Desfase del batimiento (ms)",
            "oscilloscope_title": "Osciloscopio de Escape en Tiempo Real (P1 / P2 / P3)",
            "oscilloscope_x": "Ventana Temporal (ms)",
            "oscilloscope_y": "Amplitud Acústica Normalizada",
            "waveform_title": "Onda de Audio en Tiempo Real",
            "waveform_x": "Tiempo (ms)",
            "waveform_y": "Amplitud Acústica",
            "synthetic_device": "Generador Sintético Ground-Truth",
            "status_idle": "LISTO",
            "status_stabilizing": "ESTABILIZANDO...",
            "status_running": "MIDIENDO",
            "clock_drift": "Deriva de Reloj",
            "effective_fs": "Frec. Muestreo Efectiva",
            "status_drift_format": (
                "Deriva de Reloj: {ppm:+.2f} ppm | Fs Efectiva: {fs:.2f} Hz | Sesgo: {bias:+.2f} s/d"
            ),
            "status_capture_format": "Audio recibido: {pct:.1f} % | Desbordes: {ovf}",
            "status_stopping": "DETENIENDO...",
            "mock_mode": "MOCK",
            "mock_tooltip": "Datos simulados para probar la GUI (sin medición real)",
            "coffee_tooltip": "Apoya el proyecto: {url}",
            "beat_error_p3": "en la caída (P3): {value}",
            "real_bph": "A/h reales: {value}",
            "beat_error_tooltip": (
                "Medido en P1 (desbloqueo), como Witschi. La cifra en P3 (caída) sirve para "
                "comparar con cronocomparadores que miden el golpe más fuerte; las dos difieren "
                "en la diferencia de alzado entre tic y tac."
            ),
            "coffee_open_failed": "No se pudo abrir el navegador. Visita {url}",
            "mock_badge": "MOCK · DATOS SIMULADOS",
            "mock_no_hardware": "No hay dispositivos de entrada de hardware: el MOCK sigue activo",
            "no_recording_available": "No hay grabación: haz primero una sesión",
            "recording_replay": "Modo de repetición de archivo: la sesión no se graba",
            "recording_saved": "Grabación guardada: {path}",
            "drift_level_format": "{ppm:+.2f} ± {sigma:.2f} ppm (nivel {level})",
            "drift_uncalibrated_format": "{ppm:+.2f} ppm (sin calibrar)",
            "capturing_no_measure": "CAPTURANDO (sin medición)",
        },
    }

    def __init__(self, locale: str = "en") -> None:
        """Initialize the translator with the desired language code ('en' or 'es')."""
        self._locale = "en"
        self.set_locale(locale)

    @property
    def locale(self) -> str:
        """Return the active language code."""
        return self._locale

    def set_locale(self, locale: str) -> None:
        """Change the active language code ('en' or 'es')."""
        normalized = locale.strip().lower()
        if normalized not in self.DICTIONARIES:
            logger.error("Unsupported locale '%s' requested", locale)
            raise ValueError(f"Unsupported locale '{locale}'. Supported: {list(self.DICTIONARIES)}")
        old_locale = self._locale
        self._locale = normalized
        _dbg.state_change("Translator.locale", old_locale, self._locale)

    def tr(self, key: str, default: str | None = None) -> str:
        """Translate a horological key into the current locale."""
        table = self.DICTIONARIES[self._locale]
        if key in table:
            return table[key]
        if default is not None:
            return default
        return key

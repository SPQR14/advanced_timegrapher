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
            "quartz_tooltip": "Quartz watch: choose the second hand's steps per second; only the rate is measured",
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
            "btn_save_position": "Save Position",
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
            "btn_export_traces": "Export Traces",
            "btn_print_traces": "Print Traces",
            "btn_record_audio": "Record Audio",
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
            "paper_tape_title": "Paper Tape Trace (Horizontal Rate / Deviation)",
            "paper_tape_x": "Elapsed Time (s)",
            "paper_tape_y": "Deviation / Rate (s/d)",
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
            "quartz_tooltip": "Reloj de cuarzo: elige los pasos por segundo del segundero; solo se mide la marcha",
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
            "btn_save_position": "Guardar Posición",
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
            "btn_export_traces": "Exportar Trazas",
            "btn_print_traces": "Imprimir Trazas",
            "btn_record_audio": "Grabar Sonido",
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
            "paper_tape_title": "Trazo de Cinta de Papel (Desviación / Marcha Horizontal)",
            "paper_tape_x": "Tiempo Transcurrido (s)",
            "paper_tape_y": "Desviación / Marcha (s/d)",
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

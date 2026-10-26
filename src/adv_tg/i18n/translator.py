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
            "confidence": "Confidence",
            "unlock_impulse": "P1 Unlock",
            "impulse": "P2 Impulse",
            "drop_impulse": "P3 Drop",
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
            "btn_export_traces": "Export Traces",
            "btn_print_traces": "Print Traces",
            "btn_record_audio": "Record Audio",
            "dialog_save_csv": "Export Telemetry Results to CSV",
            "dialog_export_test_results": "Export Global Statistics & Positional Delta",
            "dialog_export_traces": "Export Audio Traces",
            "dialog_save_audio": "Save Recorded Audio",
            "btn_global_stats": "Global Statistics (Positional Delta)",
            "global_stats_summary": "Positional Summary (2–6 Pos)",
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
            "status_completed": "COMPLETED",
            "clock_drift": "Clock Drift",
            "effective_fs": "Effective Sample Rate",
            "drift_ppm": "Drift (ppm)",
            "rate_bias": "Drift Bias",
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
            "no_recording_available": "No recording available: measurement engine not implemented",
            "capturing_no_measure": "CAPTURING (no measurement)",
            "recording_hw_unavailable": "Recording from a hardware device is not available yet",
        },
        "es": {
            "app_title": "Advanced Timegrapher — Banco de Pruebas DSP Horológico",
            "rate": "Marcha",
            "beat_error": "Error de Batido",
            "amplitude": "Amplitud",
            "lift_angle": "Ángulo de Alzamiento",
            "bph": "A/h",
            "confidence": "Confianza",
            "unlock_impulse": "P1 Desbloqueo",
            "impulse": "P2 Impulso",
            "drop_impulse": "P3 Caída",
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
            "btn_save_csv": "Export Results to CSV",
            "btn_export_test_results": "Export Test Results",
            "btn_buy_coffee": "☕ Buy me a coffee",
            "btn_export_traces": "Exportar Trazas",
            "btn_print_traces": "Imprimir Trazas",
            "btn_record_audio": "Grabar Sonido",
            "dialog_save_csv": "Exportar Resultados de Telemetría a CSV",
            "dialog_export_test_results": "Exportar Estadísticas Globales y Delta Posicional",
            "dialog_export_traces": "Exportar Trazas de Audio",
            "dialog_save_audio": "Guardar Grabación de Audio",
            "btn_global_stats": "Estadísticas Globales (Delta Posicional)",
            "global_stats_summary": "Resumen Posicional (2–6 Pos)",
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
            "status_completed": "COMPLETADO",
            "clock_drift": "Deriva de Reloj",
            "effective_fs": "Frec. Muestreo Efectiva",
            "drift_ppm": "Deriva (ppm)",
            "rate_bias": "Sesgo de Marcha",
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
            "no_recording_available": "No hay grabación: el motor de medición aún no está implementado",
            "capturing_no_measure": "CAPTURANDO (sin medición)",
            "recording_hw_unavailable": "Grabación desde dispositivo de hardware no disponible aún",
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

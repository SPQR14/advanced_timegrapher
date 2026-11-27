// Bilingual EN/ES formatting for native detector, metrics, and session warnings.
#include "advtg_warnings.hpp"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <string>

#include "advtg.h"

namespace advtg::warn {
namespace {

bool is_english(const char* lang) {
    if (!lang || !*lang) return false;
    return (std::tolower(static_cast<unsigned char>(lang[0])) == 'e' &&
            std::tolower(static_cast<unsigned char>(lang[1])) == 'n');
}

}  // namespace

std::string format_warning_str(const advtg_warning& w, const char* lang) {
    const bool en = is_english(lang);
    char buf[512];
    buf[0] = '\0';
    switch (w.code) {
        case ADVTG_WARN_INTERFERENCE:
            if (en) {
                std::snprintf(
                    buf, sizeof(buf),
                    "interference at %.1f kHz, %.0f dB above noise floor (filtered): "
                    "move the microphone away from chargers, screens, or dimmers",
                    w.args[0], w.args[1]);
            } else {
                std::snprintf(
                    buf, sizeof(buf),
                    "interferencia a %.1f kHz, %.0f dB sobre el ruido de fondo (filtrada): "
                    "aleja el micrófono de cargadores, pantallas o reguladores de luz",
                    w.args[0], w.args[1]);
            }
            break;
        case ADVTG_WARN_NO_LOCK:
            if (en) {
                std::snprintf(
                    buf, sizeof(buf),
                    "no lock after %.1f s: wrong BPH setting (%g) or signal without beats?",
                    w.args[0], w.args[1]);
            } else {
                std::snprintf(
                    buf, sizeof(buf),
                    "sin enganche tras %.1f s: ¿BPH mal configurado (%g) o señal sin batimientos?",
                    w.args[0], w.args[1]);
            }
            break;
        case ADVTG_WARN_WRONG_BPH:
            if (en) {
                std::snprintf(
                    buf, sizeof(buf),
                    "observed period %+.2f %% relative to nominal (%g BPH): wrong BPH setting?",
                    w.args[0], w.args[1]);
            } else {
                std::snprintf(
                    buf, sizeof(buf),
                    "periodo observado %+.2f %% respecto al nominal (%g BPH): "
                    "¿BPH mal configurado?",
                    w.args[0], w.args[1]);
            }
            break;
        case ADVTG_WARN_RATE_UNCALIBRATED:
            return en ? "uncalibrated rate: sound card clock drift has not been corrected (phase 4)"
                      : "marcha sin calibrar: no se ha corregido la deriva del reloj de la tarjeta (fase 4)";
        case ADVTG_WARN_FEW_BEATS:
            if (en) {
                std::snprintf(buf, sizeof(buf),
                              "fewer than %d valid beats per parity: no figures", w.param_int);
            } else {
                std::snprintf(buf, sizeof(buf),
                              "menos de %d batimientos válidos por paridad: sin cifras",
                              w.param_int);
            }
            break;
        case ADVTG_WARN_RATE_TREND:
            if (en) {
                std::snprintf(
                    buf, sizeof(buf),
                    "rate is changing during measurement: %+.1f s/d over %.0f s (%+.1f ± %.1f "
                    "s/d per minute); watch is still settling",
                    w.args[0], w.args[1], w.args[2], w.args[3]);
            } else {
                std::snprintf(
                    buf, sizeof(buf),
                    "la marcha cambia durante la medida: %+.1f s/d en %.0f s (%+.1f ± %.1f "
                    "s/d por minuto); el reloj aún se está asentando",
                    w.args[0], w.args[1], w.args[2], w.args[3]);
            }
            break;
        case ADVTG_WARN_BE_ON_T_FINE:
            return en ? "beat error measured on t_fine (P3 onset): no valid P1 instants"
                      : "error de batimiento medido en t_fine (inicio de P3): sin instantes de P1 válidos";
        case ADVTG_WARN_UNKNOWN_LIFT_ANGLE:
            if (en) {
                std::snprintf(buf, sizeof(buf),
                              "unknown lift angle: using %g°; amplitude is indicative", w.args[0]);
            } else {
                std::snprintf(
                    buf, sizeof(buf),
                    "ángulo de levantamiento desconocido: se usan %g°; la amplitud es orientativa",
                    w.args[0]);
            }
            break;
        case ADVTG_WARN_NO_AMPLITUDE:
            if (en) {
                std::snprintf(buf, sizeof(buf),
                              "amplitude unavailable (no t_lift in templates or outside %g-%g°)",
                              w.args[0], w.args[1]);
            } else {
                std::snprintf(
                    buf, sizeof(buf),
                    "amplitud no disponible (sin t_lift en las plantillas o fuera de %g-%g°)",
                    w.args[0], w.args[1]);
            }
            break;
        case ADVTG_WARN_AMP_MISMATCH:
            if (en) {
                std::snprintf(buf, sizeof(buf),
                              "tic and tac amplitudes differ (%.0f° / %.0f°): check P1/P3 "
                              "identification",
                              w.args[0], w.args[1]);
            } else {
                std::snprintf(buf, sizeof(buf),
                              "amplitud de tic y tac distinta (%.0f° / %.0f°): revisar la "
                              "identificación de P1/P3",
                              w.args[0], w.args[1]);
            }
            break;
        case ADVTG_WARN_AMP_ONE_PARITY: {
            const char* names[2] = {"tic", "tac"};
            const int p = (w.param_int == 1) ? 1 : 0;
            if (en) {
                std::snprintf(buf, sizeof(buf),
                              "amplitude taken from %s (%.0f°): %s drop is not sharp "
                              "(would give %.0f°)",
                              names[p], w.args[0], names[1 - p], w.args[1]);
            } else {
                std::snprintf(buf, sizeof(buf),
                              "amplitud tomada del %s (%.0f°): la caída del %s no es nítida "
                              "(daría %.0f°)",
                              names[p], w.args[0], names[1 - p], w.args[1]);
            }
            break;
        }
        case ADVTG_WARN_AMP_MISMATCH_INDICATIVE:
            if (en) {
                std::snprintf(buf, sizeof(buf),
                              "tic and tac amplitudes differ (%.0f° / %.0f°): amplitude is indicative",
                              w.args[0], w.args[1]);
            } else {
                std::snprintf(buf, sizeof(buf),
                              "amplitud de tic y tac distinta (%.0f° / %.0f°): la amplitud es orientativa",
                              w.args[0], w.args[1]);
            }
            break;
        case ADVTG_WARN_SATURATED_BEATS:
            if (en) {
                if (w.args[0] > 0.5) {
                    std::snprintf(
                        buf, sizeof(buf),
                        "%d saturated beats: lower microphone gain or enable 'Normalize audio'",
                        w.param_int);
                } else {
                    std::snprintf(buf, sizeof(buf),
                                  "%d saturated beats: lower microphone gain", w.param_int);
                }
            } else {
                if (w.args[0] > 0.5) {
                    std::snprintf(
                        buf, sizeof(buf),
                        "%d batimientos saturados: baja la ganancia del micrófono "
                        "o activa 'Normalizar audio'",
                        w.param_int);
                } else {
                    std::snprintf(buf, sizeof(buf),
                                  "%d batimientos saturados: baja la ganancia del micrófono",
                                  w.param_int);
                }
            }
            break;
        default:
            return "";
    }
    return std::string(buf);
}

}  // namespace advtg::warn

extern "C" {

ADVTG_C_API int32_t advtg_format_warning(const advtg_warning* w, const char* lang, char* buf,
                                         int32_t cap) {
    if (!w) return -1;
    const std::string s = advtg::warn::format_warning_str(*w, lang);
    if (buf && cap > 0) {
        const std::size_t n =
            std::min<std::size_t>(s.size(), static_cast<std::size_t>(cap - 1));
        if (n) std::memcpy(buf, s.data(), n);
        buf[n] = '\0';
    }
    return static_cast<int32_t>(s.size());
}

}  // extern "C"

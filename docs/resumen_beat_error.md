# Beat Error: Análisis Técnico de Fases del Escape

## Resumen Ejecutivo

| Parámetro | P1 (Desbloqueo) | P2 (Impulso) | P3 (Caída) |
|-----------|-----------------|--------------|------------|
| **Usable para Beat Error** | ❌ No (sesgado) | ❌ No (irregular) | ✅ **Sí** (correcto) |
| **Usable para Rate** | ⚠️ Con reservas | ❌ No | ✅ Sí |
| **Usable para Amplitud** | ✅ Sí (con P3) | ❌ No | ✅ Sí (con P1) |
| **Potencia acústica** | Débil | Irregular | Potente |
| **Jitter de detección** | 2-5 ms | Variable | <0,5 ms |
| **Dependencia velocidad volante** | Alta | Media | Baja/Nula |

---

## Las Tres Fases del Escape Suizo

### Ciclo Completo de Batido
┌──────────────────────────────────────────────────────────────────────┐ │ CICLO DEL ESCAPE │ ├──────────────────────────────────────────────────────────────────────┤ │ │ │ ┌─────────┐ ┌─────────┐ ┌─────────┐ │ │ │ VOLANTE│ ───> │ ÁNCORA │ ───> │RUEDA │ │ │ │ + ESPI │ │ │ │ESCAPE │ │ │ └─────────┘ └─────────┘ └─────────┘ │ │ │ │ TAP: 18.000 - 36.000 vib/h │ │ PERÍODO: 125 ms a 62,5 ms (medio ciclo) │ │ │ └──────────────────────────────────────────────────────────────────────┘


### Desglose de Eventos Acústicos

| Evento | Fase Física | Origen del Sonido | Momento Temporal |
|--------|-------------|-------------------|------------------|
| **P1** | Desbloqueo | Elipse golpea horquilla; rueda retrocede | Inicio de media oscilación |
| **P2** | Impulso | Diente entra plano impulso paleta; horquilla toca elipse | Centro de media oscilación |
| **P3** | Caída | Diente sobre plano reposo; áncora golpea tope | Fin de media oscilación |

---

## Fórmula del Beat Error

### Definición Matemática

$$\text{Rep} = \frac{t_{TIC} - t_{TAC}}{2} \times 1000 \quad \text{[ms]}$$

Donde:
- $t_{TIC}$ = tiempo entre dos pulsos consecutivos en un sentido
- $t_{TAC}$ = tiempo entre dos pulsos consecutivos en sentido opuesto
- Factor 1000 = conversión a milisegundos

### Error Real vs. Medición

**Medición en P3 (correcta):**
$$\text{Beat Error}_{(P3)} = \frac{(t_{TIC}^{P3} - t_{TAC}^{P3})}{2}$$

**Medición en P1 (sesgada):**
$$\text{Beat Error}_{(P1)} = \frac{(t_{TIC}^{P1} - t_{TAC}^{P1})}{2} + \delta_{velocidad}(TIC, TAC)$$

$\delta_{velocidad}$ = sesgo introducido por asimetría de velocidad del volante

---

## Tabla de Tolerancias del Beat Error

| Rango (ms) | Clasificación | Acción Recomendada |
|------------|---------------|---------------------|
| **0,0 - 0,3** | Excelente | Mejor que fábrica, sin acción |
| **0,3 - 0,6** | Aceptable | Perfecto, sin acción |
| **0,6 - 1,2** | Visible | Ajustar en próximo servicio |
| **1,2 - 2,0** | Alto | Ajuste necesario, estabilidad afectada |
| **> 2,0** | Crítico | Reparación urgente, posible daño |

---

## Comparativa: P1 vs. P3

### Por Qué P1 Introduce Sesgo

| Mecanismo | Descripción | Impacto en Medición |
|-----------|-------------|---------------------|
| **Umbral de fuerza** | Volante debe vencer draw + drop | Retraso dependiente de velocidad |
| **Pulso débil** | Menor SNR, ataque gradual | Jitter de detección +2-5 ms |
| **Sensibilidad amplitud** | Menor amplitud → mayor retraso | Sesgo asimétrico TIC/TAC |

### Por Qué P3 Es Correcto

| Mecanismo | Descripción | Ventaja |
|-----------|-------------|---------|
| **Evento cinemático** | Rueda empuja horquilla hasta tope | Independiente del volante |
| **Pulso potente** | Ataque abrupto, alto SNR | Detección precisa <0,5 ms |
| **Reproducibilidad** | Fin de carrera mecánica | Determinístico, estable |

---

## Ejemplo Numérico de Discrepancia

### Datos de Caso Real

| Parámetro | Valor |
|-----------|-------|
| Beat error real del reloj | 0,5 ms |
| Sesgo de velocidad P1 (TIC vs TAC) | +0,4 ms |
| **Tu medición en P1** | **~0,9 ms** |
| **Medición comercial en P3** | **~0,5 ms** ✓ |

### Error Relativo

$$\text{Error relativo} = \frac{0,9 - 0,5}{0,5} \times 100 = 80\%$$

---

## Diagrama de Flujo para Selección de Pulso

┌──────────────────────────────────────────────────────────────┐ │ SELECCIÓN DE EVENTO ACÚSTICO │ └──────────────────────────────────────────────────────────────┘ │ ┌─────────▼─────────┐ │ ¿Qué parámetro │ │ necesitas medir? │ └─────────┬─────────┘ │ ┌────────────────────┼────────────────────┐ │ │ │ ▼ ▼ ▼ ┌──────────┐ ┌──────────┐ ┌──────────┐ │ RATE │ │BEAT ERROR│ │ AMPLITUD │ └────┬─────┘ └────┬─────┘ └────┬─────┘ │ │ │ │ ┌────▼────┐ │ │ │USAR P3 │ │ │ └────┬────┘ │ │ │ │ ┌────▼────┐ ┌──────┴──────┐ ┌──────▼──────┐ │USAR P3 o│ │VERIFICAR │ │MEDIR Δt │ │P1* │ │CON P3 │ │(P1→P3) │ └─────────┘ └─────────────┘ │LIFT ANGLE │ │ └─────────────┘ │ │ ┌────▼────┐ ┌────▼────┐ │Si P3 no │ │SIEMPRE │ │es usable│ │PRIORIZA │ │(débil) │ │P3 │ └─────────┘ └─────────┘


---

## Tabla de Características de Pulso

| Característica | P1 | P2 | P3 |
|----------------|----|----|----|
| Nombre fase | Desbloqueo | Impulso | Caída |
| Intensidad | ★☆☆ | ★★☆ | ★★★ |
| Precisión temporal | Baja | Baja | Alta |
| SNR (dB) | -10 a -5 | -5 a 0 | +5 a +15 |
| Duración del pulso | 1-3 ms | 2-5 ms | 0,5-1 ms |
| Dependencia amplitud | Alta | Media | Baja |
| Dependencia velocidad | Alta | Media | Mínima |
| Usable rate | ⚠️ Condicional | ❌ No | ✅ Sí |
| Usable beat error | ❌ No | ❌ No | ✅ Sí |
| Usable amplitud | ✅ Sí (con P3) | ❌ No | ✅ Sí (con P1) |

---

## Fuentes Técnicas

| Fuente | Documento | Observación |
|--------|-----------|-------------|
| Witschi | Testing Methods for Mechanical Watches | P1 para rate/beat en teoría |
| Witschi | Manual Técnico | P3 para amplitud (P1→P3) |
| MicroSet | Andrew Mummery | P3 viable, P1 con reservas |
| NAWCC | Foros Técnicos | Reportes de usuarios: P3 más robusto |
| Escapement.watch | Journal | P3 ancla cinemáticamente |

---

## Chequeos de Validación

### Lista de Verificación para Software

- [ ] Anclar en **P3** para beat error y rate
- [ ] Calcular amplitud con **Δt(P1→P3) + lift angle**
- [ ] Implementar **interpolación submuestral** (<0,1 ms)
- [ ] **Filtrar outliers** (±2σ antes de promediar)
- [ ] Validar con **reloj referencia conocido** en 3 posiciones
- [ ] Configuración de **lift angle correcta** (52° estándar, 38° Co-Axial)

### Posiciones de Prueba

| Posición | Uso | Notas |
|----------|-----|-------|
| Dial Up (DU) | Estado general | Punto de partida |
| Dial Down (DD) | Verificar gravedad | Comparar con DU |
| Crown Up (CU) | Eje horizontal | Influencia colgando |
| Crown Left (CL) | Eje horizontal | Rotación 90° |
| Crown Down (CD) | Eje vertical | Posición uso normal |

---

## Notas Adicionales

1. **Beat error tradicional** se reporta sin signo, pero dispositivos Witschi nuevos proporcionan dirección al cambiar polaridad.

2. **Lift angle** es crítico solo para amplitud. En rate y beat error, el lift angle no afecta el cálculo.

3. **P2 (impulso)** nunca debe usarse: es irregular según Witschi ("cannot be used for an evaluation").

4. **Diferencia entrada/salida**: los pulsos de entrada y salida no son geométricamente idénticos; considerar esta asimetría en análisis avanzado.

5. **Amplitud calculada ≠ medida**: ninguna timegrapher mide amplitud directamente; todas la calculan desde intervalos acústicos y lift angle.

---

*Basado en investigación Witschi, MicroSet, NAWCC, y análisis acústico horológico*

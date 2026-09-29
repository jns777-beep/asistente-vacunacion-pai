import streamlit as st
from datetime import date
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

# ==========================================
# 1. MODELOS DE DATOS (SCHEMA)
# ==========================================

class EstadoDictamen(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_WARNINGS = "APPROVED_WITH_WARNINGS"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"

class EventosAdversosPrevios(BaseModel):
    anafilaxia_dosis_previa: bool = False
    encefalopatia_7d_previa: bool = False

class AntecedentesClinicos(BaseModel):
    eventos_previos: EventosAdversosPrevios = Field(default_factory=EventosAdversosPrevios)
    edad_gestacional_semanas: Optional[int] = None
    encefalopatia_7d_previa: bool = False

class LoteBiológico(BaseModel):
    nombre_comercial: str = "PENTAXIM"
    numero_lote: str
    fecha_vencimiento: date
    es_reconstituido: bool = False

class PacienteContext(BaseModel):
    id_paciente: str
    fecha_nacimiento: date
    numero_dosis_a_evaluar: int = 1
    fecha_ultima_dosis: Optional[date] = None
    enfermedad_aguda_o_fiebre_hoy: bool = False
    antecedentes: AntecedentesClinicos = Field(default_factory=AntecedentesClinicos)

class ReglaInformativa(BaseModel):
    codigo_regla: str
    mensaje: str

class EvaluacionDictamen(BaseModel):
    id_paciente: str
    producto_evaluado: str = "PENTAXIM"
    estado: EstadoDictamen
    puede_administrarse: bool
    antigenos_cubiertos: List[str] = []
    bloqueos_seguridad: List[ReglaInformativa] = []
    advertencias: List[ReglaInformativa] = []

# ==========================================
# 2. MOTOR DE EVALUACIÓN (ENGINE)
# ==========================================

ANTIGENOS_PENTAXIM = [
    "Difteria",
    "Tétanos",
    "Pertussis Acelular",
    "Poliomielitis_IPV",
    "Haemophilus_Influenzae_B"
]

def evaluar_pentaxim(paciente: PacienteContext, lote: LoteBiológico, fecha_evaluacion: date) -> EvaluacionDictamen:
    bloqueos = []
    advertencias = []

    # 1. Reglas de Vencimiento y Lote
    if lote.fecha_vencimiento < fecha_evaluacion:
        bloqueos.append(ReglaInformativa(
            codigo_regla="INV_SHELF_LIFE_EXPIRED",
            mensaje=f"El lote {lote.numero_lote} está vencido desde {lote.fecha_vencimiento}."
        ))

# 2. Reglas Cronológicas
    dias_vida = (fecha_evaluacion - paciente.fecha_nacimiento).days

    # Validar edad mínima (42 días / 6 semanas)
    if paciente.numero_dosis_a_evaluar == 1 and dias_vida < 42:
        bloqueos.append(ReglaInformativa(
            codigo_regla="ELIG_AGE_BELOW_MINIMUM",
            mensaje=f"Edad insuficiente ({dias_vida} días). Edad mínima requerida: 42 días (6 semanas)."
        ))

    # Validar edad máxima tope para Pentaxim (7 años / 2555 días)
    if dias_vida > 2555:
        bloqueos.append(ReglaInformativa(
            codigo_regla="ELIG_AGE_EXCEEDS_MAXIMUM",
            mensaje=f"Paciente fuera del rango de edad pediátrica para Pentaxim ({dias_vida // 365} años). Biológico indicado únicamente para menores de 7 años."
        ))

    # Validar intervalo entre dosis
    if paciente.numero_dosis_a_evaluar > 1:
        if not paciente.fecha_ultima_dosis:
            bloqueos.append(ReglaInformativa(
                codigo_regla="ELIG_MISSING_PREVIOUS_DOSE_DATE",
                mensaje="No se registró la fecha de la última dosis para validar el intervalo."
            ))
        else:
            dias_intervalo = (fecha_evaluacion - paciente.fecha_ultima_dosis).days
            if dias_intervalo < 28:
                bloqueos.append(ReglaInformativa(
                    codigo_regla="ELIG_INTERVAL_TOO_SHORT",
                    mensaje=f"Intervalo insuficiente ({dias_intervalo} días). Mínimo requerido: 28 días."
                ))
                
    # 3. Reglas de Seguridad
    if paciente.antecedentes.encefalopatia_7d_previa:
        bloqueos.append(ReglaInformativa(
            codigo_regla="SAFE_CONTRAINDICATION_ENCEPHALOPATHY_7D",
            mensaje="Contraindicación absoluta: Antecedente de encefalopatía dentro de los 7 días posteriores a una dosis previa."
        ))

    if paciente.enfermedad_aguda_o_fiebre_hoy:
        advertencias.append(ReglaInformativa(
            codigo_regla="SAFE_DEFERRAL_ACUTE_FEVER",
            mensaje="Se debe posponer la vacunación por cuadro febril o enfermedad aguda en el momento."
        ))

    if paciente.antecedentes.edad_gestacional_semanas and paciente.antecedentes.edad_gestacional_semanas <= 28:
        advertencias.append(ReglaInformativa(
            codigo_regla="SPECIAL_PREMATURE_APNEA_MONITORING",
            mensaje="Paciente prematuro (<=28 sem). Requiere monitoreo respiratorio de 48-72h por riesgo de apnea."
        ))

    # Determinación del Estado Final
    if any(b.codigo_regla.startswith("ELIG_") for b in bloqueos):
        estado_final = EstadoDictamen.NOT_ELIGIBLE
        puede_admin = False
    elif any(b.codigo_regla.startswith(("INV_", "SAFE_")) for b in bloqueos):
        estado_final = EstadoDictamen.BLOCKED
        puede_admin = False
    elif any(a.codigo_regla == "SAFE_DEFERRAL_ACUTE_FEVER" for a in advertencias):
        estado_final = EstadoDictamen.DEFERRED
        puede_admin = False
    elif len(advertencias) > 0:
        estado_final = EstadoDictamen.APPROVED_WITH_WARNINGS
        puede_admin = True
    else:
        estado_final = EstadoDictamen.APPROVED
        puede_admin = True

    return EvaluacionDictamen(
        id_paciente=paciente.id_paciente,
        estado=estado_final,
        puede_administrarse=puede_admin,
        antigenos_cubiertos=ANTIGENOS_PENTAXIM if puede_admin else [],
        bloqueos_seguridad=bloqueos,
        advertencias=advertencias
    )

# ==========================================
# 3. INTERFAZ GRÁFICA INTERACTIVA (STREAMLIT)
# ==========================================

st.set_page_config(page_title="Asistente PAI - Pentaxim", page_icon="💉", layout="centered")

st.title("💉 Asistente Clínico de Vacunación")
st.subheader("Evaluación de Elegibilidad: PENTAXIM")
st.caption("Programa Ampliado de Inmunizaciones (PAI)")

st.divider()

# 1. Entrada de datos del paciente
col1, col2 = st.columns(2)

with col1:
    st.markdown("### 👤 Datos del Paciente")
    id_pacientes = st.text_input("ID o Documento del Paciente", value="PED-2026-01")
    fecha_nac = st.date_input("Fecha de Nacimiento", value=date(2026, 7, 28))
    dosis = st.selectbox("Número de Dosis a Evaluar", [1, 2, 3])
    
    fecha_ultima = None
    if dosis > 1:
        fecha_ultima = st.date_input("Fecha de Última Dosis Aplicada", value=date(2026, 8, 1))

with col2:
    st.markdown("### 📋 Condición Clínica Hoy")
    tiene_fiebre = st.checkbox("¿Presenta fiebre o enfermedad aguda hoy?")
    es_prematuro = st.checkbox("¿Antecedente de Prematurez (<= 28 sem)?")
    encefalopatia = st.checkbox("¿Antecedente de encefalopatía post-vacuna (7d)?")

    st.markdown("### 📦 Datos del Lote")
    num_lote = st.text_input("Número de Lote", value="LOT-2026-X")
    fecha_venc = st.date_input("Fecha de Vencimiento del Lote", value=date(2027, 12, 31))

st.divider()

# 2. Botón de evaluación
if st.button("🚀 Evaluar Elegibilidad Clínica", type="primary", use_container_width=True):
    
    lote = LoteBiológico(
        nombre_comercial="PENTAXIM",
        numero_lote=num_lote,
        fecha_vencimiento=fecha_venc
    )
    
    antecedentes = AntecedentesClinicos(
        edad_gestacional_semanas=27 if es_prematuro else None,
        encefalopatia_7d_previa=encefalopatia
    )
    
    paciente = PacienteContext(
        id_paciente=id_pacientes,
        fecha_nacimiento=fecha_nac,
        numero_dosis_a_evaluar=dosis,
        fecha_ultima_dosis=fecha_ultima,
        enfermedad_aguda_o_fiebre_hoy=tiene_fiebre,
        antecedentes=antecedentes
    )
    
    dictamen = evaluar_pentaxim(paciente, lote, date.today())
    
    st.markdown("## 📊 Dictamen Clínico")
    
    if dictamen.estado.value == "APPROVED":
        st.success("✅ **APROBADO**: El paciente cumple con todos los criterios para la aplicación.")
    elif dictamen.estado.value == "APPROVED_WITH_WARNINGS":
        st.warning("⚠️ **APROBADO CON ADVERTENCIAS**: Se puede aplicar la dosis, pero requiere precauciones especiales.")
    elif dictamen.estado.value == "DEFERRED":
        st.warning("🟡 **APLAZADO**: No aplicar hoy. Reprogramar al ceder el Cuadro Febril.")
    elif dictamen.estado.value in ["BLOCKED", "NOT_ELIGIBLE"]:
        st.error("❌ **NO ELEGIBLE / BLOQUEADO**: Prohibida la administración del biológico.")

    if dictamen.advertencias:
        st.markdown("#### ⚠️ Advertencias / Instrucciones de Seguridad:")
        for adv in dictamen.advertencias:
            st.info(f"**[{adv.codigo_regla}]**: {adv.mensaje}")
            
    if dictamen.bloqueos_seguridad:
        st.markdown("#### 🚫 Motivos de Rechazo / Bloqueo:")
        for bloq in dictamen.bloqueos_seguridad:
            st.error(f"**[{bloq.codigo_regla}]**: {bloq.mensaje}")
            
    if dictamen.antigenos_cubiertos:
        st.markdown("#### 🛡️ Antígenos Protegidos:")
        st.write(", ".join(dictamen.antigenos_cubiertos))

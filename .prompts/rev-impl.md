# Revisión adversarial e implementación en paralelo

Realiza una revisión adversarial del alcance indicado en esta sesión siguiendo
AGENTS.md y las skills aplicables. Si el alcance no está definido, solicítalo
antes de activar el goal. Busca fallos reales, regresiones y riesgos de seguridad;
prioriza los hallazgos y evita ampliar el trabajo con mejoras ajenas al objetivo.

Antes de activar el goal, comprueba que Git y los worktrees de trabajo estén
limpios. Si hay cambios previos, presérvalos y resuelve su tratamiento conmigo;
no los descartes ni los incluyas en commits sin autorización.

Usa `set goal` (o la herramienta equivalente disponible) para registrar el
objetivo. Tú orquestas la revisión, la integración y la validación. Delega la
implementación en subagentes GPT-6.1 Sol con razonamiento medium, utilizando
worktrees paralelos y tareas con límites claros para evitar conflictos. Si ese
modelo no está disponible, avísame antes de sustituirlo.

Implementa los cambios necesarios en pequeñas unidades verificables. No
sobreoptimices con tests: usa las comprobaciones mínimas que aporten evidencia
real, con regresiones focalizadas cuando corresponda y verificación fail-closed
para cambios de seguridad. Integra y revisa los resultados de los subagentes.

Si es necesario reconstruir Hermes o Camofox, tienes autorización para eliminar
primero sus imágenes actuales y reconstruirlas. Identifica las imágenes exactas
y limita la eliminación a ellas; conserva volúmenes, perfiles y datos. Haz los
builds uno a uno, solo cuando la revisión y las comprobaciones previas den la
máxima confianza razonable en los cambios. Respeta los requisitos de espacio y
las demás restricciones operativas de AGENTS.md.

Al terminar, deja los cambios completados en commits locales, Git limpio y los
worktrees utilizados limpios; retira los worktrees temporales creados para esta
tarea una vez integrado su trabajo. No hagas push. Marca el goal como completado
solo cuando no quede trabajo requerido pendiente.

Avísame cuando todo esté acabado con un resumen breve de los hallazgos, los
cambios, las validaciones y builds realizados, el estado de Git y los worktrees,
y cualquier limitación concreta que quede pendiente.

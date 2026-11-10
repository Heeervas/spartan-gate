# Browser MCP propio: fuente preparada, activación pendiente

Estado: implementación de fuente y pruebas locales/aisladas; no registrado ni
activado en producción. No se ha recreado Hermes, Camofox, tool-access o MCPHub,
ni se han copiado documentos reales o tocado perfiles. La revisión final y el
cierre de fuente se registran en el task propietario, no se presuponen aquí.

El contrato público está en `docs/browser-mcp.md`. El nuevo backend comparte
sesiones explícitas `session_id`, no IDs internos de conversaciones. No requiere
editar Pi, Hermes o MCPHub ni instalar un plugin Hermes. Conserva el backend
search/Reader y las herramientas nativas existentes.

Uploads generales: copiar explícitamente un archivo regular terminado a
`incoming`, publicándolo por rename atómico; stage devuelve un `file_id` propio
con nombre, tamaño y SHA-256. Upload adjunta esos IDs a un input exacto con un
solo intento; no significa formulario enviado ni recepción remota confirmada.
No hay restricción a PDF o un directorio de trabajo concreto, ni permiso para
buscar documentos/credenciales o acceder a rutas arbitrarias del host.

Los montajes opcionales se limitan a la carpeta de transferencia: incoming RO en
tool-access y outgoing RW en tool-access / RO en Camofox. El montaje adicional de
incoming para Hermes requiere su propia recreación autorizada; no se aplica por
añadir archivos de Compose. El principal nuevo necesita browser+upload; no
ampliar permisos de los tokens existentes. Cookies compartidas dentro del nuevo
principal; tabs separados por sesión, sin adoptar tabs o perfiles actuales.

Operaciones ambiguas no se repiten. No borrar el journal ni la carpeta outgoing
para recuperar un error: spool no vacío al arrancar o archivos uncertain necesitan
reconciliación explícita. Cancelar un wrapper asíncrono no prueba la terminación
del hilo de copia; reservas/archivos permanecen retenidos si no hay certeza.

Pruebas SDK/REST/archivos y handler nativo con recursos inertes no prueban Firefox,
clientes activos o un sitio real. Construcción de imágenes revisadas, ventana sin
trabajos afectados, montajes, registro/grants y readback son una fase operativa
separada. No ampliar whitelist, reiniciar servicios o exponer un documento real
como consecuencia implícita de esta nota. Este archivo es archivo operatorio,
no una nueva guía publicada dentro del runtime.

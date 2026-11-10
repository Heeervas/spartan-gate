# Browser MCP: activación aplicada, pruebas del usuario pendientes

Estado: aplicado en la instalación seleccionada, sin push ni publicación de una
nueva guía de runtime. El usuario ha reservado para sí la recarga de MCP y las
pruebas funcionales. No se ha navegado, creado una sesión/tab MCP ni subido un
archivo real durante esta activación.

- Backend `spartan-browser-tools` conectado;23 herramientas nuevas seleccionadas
  y autorizadas con grants exactos. Los tres clientes seleccionados ven126 tools
  en MCPHub: los103 anteriores más23. Se conservan backends, claves de clientes,
  permisos previos y closed sentinel; sin cambios de core Pi/Hermes/MCPHub.
- Imágenes Camofox/tool-access derivadas de las bases instaladas, con las capas
  originales conservadas y únicamente patch/módulos revisados. Sin descargas de
  dependencias ni rebuild Hermes. Camofox anuncia strict single-attempt upload.
- Recreados solo Camofox, tool-access y Hermes por sus nuevos montajes. Hermes
  conserva exactamente su imagen; su supervisor y healthcheck están OK. Los11
  vecinos, incluido MCPHub, conservan identidad/start time/restart count.
- Incoming es RO para tool-access y accesible al usuario1000 en Hermes en
  `/opt/data/browser-transfer/incoming`; outgoing es RW solo para tool-access y
  RO en Camofox. La ubicación host queda en el registro operativo privado.
- Lectura de catálogo, permisos/montajes y denegación sin workflow comprobadas;
  no sustituyen las pruebas reales de Firefox, snapshots/viewport y adjuntos.

Recargar los clientes MCP antes de probar `browser_session_create`. Guardar el
`session_id` devuelto y usar un `operation_id` nuevo para cada acción nueva; nunca
inventar/reasignar IDs para repetir una operación ambigua. Upload usa file IDs y
un input de archivo exacto, y no significa formulario enviado o recepción remota.

Para publicar archivos, preparar un temporal y renombrarlo al nombre final dentro
del mismo filesystem/montaje de incoming. Los consumidores deben stagear solo
nombres finales completos, nunca temporales. No borrar journals ni spools para
recuperar fallos. El task de activación conserva rollback y pendientes del usuario;
la aceptación de fuente archivada no se reescribe como prueba funcional live.

# Variables compartidas para execute_code

Fecha: 12/10/2026.
Estado: recomendación pendiente de aplicar; esta nota no cambia la configuración.

## Qué ocurre

Una variable puede estar disponible en Terminal y no aparecer en el Python de
`execute_code`. Hermes filtra el entorno de ese proceso: los nombres que
contienen `KEY`, `TOKEN`, `SECRET` y otros indicadores de credenciales se eliminan
salvo autorización explícita mediante su passthrough nativo.

Las claves añadidas con `sg-hermes env set` se guardan en el dotenv administrado
compartido. No tienen por qué aparecer en los `.env` del perfil raíz o de un
perfil secundario. Esa ubicación compartida no autoriza por sí sola su paso a
`execute_code`.

## Recomendación

Añadir el nombre de la variable a `terminal.env_passthrough` en el `config.yaml`
de cada perfil autorizado. Por ejemplo:

```yaml
terminal:
  env_passthrough:
    - PLANE_ARTEFACT_KEY
```

Aunque la sección se llame `terminal`, esta lista también controla las variables
que puede recibir `execute_code`. Conservar las entradas existentes al añadir
la nueva. La lista contiene solo nombres: el valor permanece en el dotenv
compartido, sin duplicarlo en configuraciones de perfiles.

Para compartir esta capacidad entre todos los perfiles, aplicar la configuración
a los perfiles existentes y al flujo que crea perfiles nuevos. Ampliar el helper
para gestionar esta autorización es una mejora pendiente; actualmente guarda
el valor compartido, pero no añade este passthrough.

La allowlist se cachea por perfil y un kernel Python ya iniciado conserva su
entorno. Tras aplicar la configuración, reiniciar de forma controlada los
gateways afectados y usar un kernel o sesión Python nuevos para verificarla.
Si también cambia el dotenv administrado compartido, recrear Hermes para
regenerar esa capa.

## Alternativa por skill

Una skill de Plane puede declarar `PLANE_ARTEFACT_KEY` en
`required_environment_variables`. Al cargarla mediante `skill_view`, Hermes
registra las variables disponibles para el sandbox. Esta autorización depende
de cargar la skill; la configuración por perfil es preferible cuando se quiere
acceso persistente sin ese paso.

## Verificación y límites

Comprobar únicamente la presencia de la variable desde `execute_code`:

```python
import os
print(bool(os.environ.get("PLANE_ARTEFACT_KEY")))
```

No imprimir el valor, cabeceras de autenticación ni volcados del entorno. Los
filtros nativos de credenciales de modelos, mensajería y políticas internas
siguen vigentes; el passthrough no debe usarse para eludirlos. Esta autorización
permite recibir la variable en Python y no concede permisos adicionales de
ejecución, archivos o red.

## Registro de próximas notas

Guardar las nuevas recomendaciones, actualizaciones y descubrimientos en esta
carpeta, con nombres `<tema>_<ddmmyyyy>.md`. Indicar la fecha, el estado de
aplicación y la comprobación relevante. No guardar secretos ni datos privados
de la instalación.

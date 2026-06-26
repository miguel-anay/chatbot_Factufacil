"""
Capa de APLICACIÓN — casos de uso del sistema.

Acá viven los servicios de aplicación que ORQUESTAN el dominio:
  - presales_service.py : caso de uso del chatbot de preventa (imperativo).
  - agents/             : co-piloto ERP — loop ReAct por especialista.
  - orchestration/      : supervisor + grafo LangGraph (caso de uso agéntico).

Regla de capa (a diferencia de core/domain.py y core/ports.py, que son
PUROS y NUNCA importan frameworks): este paquete SÍ puede importar
LangChain/LangGraph — es application-services, no dominio puro. El dominio
y los puertos siguen siendo agnósticos de infraestructura.
"""

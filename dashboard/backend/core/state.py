"""
Estado mutável compartilhado entre main.py e routers.

Usa módulo-singleton: todos os imports apontam para os MESMOS objetos.
Nunca faça re-atribuição (instances = {}) — apenas mute (instances[k] = v).
"""
import threading

instances: dict = {}
conversations: dict = {}
instances_lock = threading.RLock()
conversations_lock = threading.RLock()

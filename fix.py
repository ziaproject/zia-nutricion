import re

with open('whatsapp.py', 'r') as f:
    code = f.read()

# Fix: pasar phone a cargar_memoria y guardar_memoria
code = code.replace(
    'memoria = main.cargar_memoria()\n    perfil = memoria.get("perfil", {})',
    'memoria = main.cargar_memoria(phone)\n    perfil = memoria.get("perfil", {})'
)
code = code.replace('main.guardar_memoria(memoria)', 'main.guardar_memoria(memoria, phone)')

with open('whatsapp.py', 'w') as f:
    f.write(code)
print("OK")

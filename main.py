import os
import time
import subprocess
import pymongo
import threading
import json
from flask import Flask, render_template_string
import aider

##############################
# Configuración de MongoDB
##############################
def setup_mongo():
    """Configura la conexión a MongoDB y devuelve la colección de logs."""
    client = pymongo.MongoClient("mongodb://localhost:27017/")
    db = client["git_ai_assistant"]
    return db["changes"]

db_collection = setup_mongo()

##############################
# Funciones de Git
##############################
def get_current_branch():
    """Obtiene el branch actual del repositorio Git."""
    return subprocess.check_output(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD']
    ).decode().strip()

def switch_or_create_branch(branch_name):
    """Cambia al branch especificado o lo crea si no existe."""
    branches = subprocess.check_output(['git', 'branch']).decode()
    if branch_name not in branches:
        subprocess.run(['git', 'checkout', '-b', branch_name], check=True)
    else:
        subprocess.run(['git', 'checkout', branch_name], check=True)

def rollback_last_commit():
    """Revierte el último commit y restaura los archivos a su estado anterior."""
    subprocess.run(["git", "reset", "--hard", "HEAD~1"])
    print("Últimos cambios revertidos.")

##############################
# Función para obtener contexto del repositorio
##############################
def get_repository_context():
    """
    Recorre el repositorio (ignorando la carpeta .git) y concatena el contenido
    de los ficheros de interés (por ejemplo, .py, .txt, .md, .json) para ofrecer
    contexto al rol del Programador.
    """
    context = ""
    for root, dirs, files in os.walk('.'):
        if '.git' in dirs:
            dirs.remove('.git')
        for file in files:
            if file.endswith(('.py', '.txt', '.md', '.json')):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r', encoding="utf-8") as f:
                        content = f.read()
                    context += f"Archivo: {filepath}\nContenido:\n{content}\n{'-'*40}\n"
                except Exception as e:
                    continue
    return context

##############################
# Funciones de comunicación con Aider
##############################
def chat_with_role(role, prompt):
    """
    Envía un mensaje al modelo Aider y devuelve la respuesta.
    """
    mensaje = f"[{role}] {prompt}"
    respuesta = aider.chat(
        model="qwen2.5-coder:1.5b",
        messages=[{"role": "user", "content": mensaje}]
    )
    return respuesta["message"]["content"]

##############################
# Flujo de trabajo completo
##############################
def apply_workflow(request_text):
    """
    Ejecuta el flujo de trabajo con los 4 roles:
      - Analista Funcional
      - Programador (con contexto completo del repositorio)
      - Arquitecto Técnico
      - Tester

    Además, consulta a Aider para determinar si se debe modificar un fichero existente o crear uno nuevo.
    Se realizan los cambios en el repositorio y se registra el proceso en MongoDB.
    """
    print("\nIniciando flujo de trabajo con Aider...\n")
    
    # Rol: Analista Funcional
    analysis = chat_with_role("Analista Funcional", 
        f"Analiza la siguiente solicitud y define los requerimientos: {request_text}")
    print("-> Análisis completado.")
    
    # Rol: Programador (incluyendo todo el contexto del repositorio)
    repo_context = get_repository_context()
    programming = chat_with_role("Programador", 
        f"Implementa la solución basada en el siguiente análisis: {analysis}.\n\n"
        f"Contexto completo del repositorio:\n{repo_context}")
    print("-> Programación completada.")
    
    # Rol: Arquitecto Técnico
    architecture = chat_with_role("Arquitecto Técnico", 
        f"Revisa y mejora la arquitectura del siguiente código:\n{programming}")
    print("-> Optimización de arquitectura completada.")
    
    # Rol: Tester
    testing = chat_with_role("Tester", 
        f"Genera pruebas para validar el siguiente código:\n{architecture}")
    print("-> Generación de pruebas completada.")
    
    # Decisión sobre el fichero: modificar uno existente o crear uno nuevo.
    file_decision_prompt = (
        f"Basándote en la siguiente solución de código:\n{architecture}\n\n"
        "Determina si es mejor modificar un fichero existente o crear uno nuevo. "
        "En caso de crear uno nuevo, sugiere un nombre de fichero relevante para la aplicación. "
        "Devuelve un JSON con dos claves: 'accion' (valores 'modificar' o 'crear') y 'nombre' (nombre del fichero)."
    )
    file_decision_response = aider.chat(
        model="qwen2.5-coder:1.5b",
        messages=[{"role": "user", "content": file_decision_prompt}]
    )
    
    try:
        decision = json.loads(file_decision_response["message"]["content"])
        accion = decision.get("accion")
        nombre = decision.get("nombre")
        if not accion or not nombre:
            raise ValueError("Información incompleta en la respuesta JSON.")
    except Exception as e:
        print("Error al parsear la respuesta JSON para la decisión de fichero. Se usará 'generated_code.py' por defecto. Error:", e)
        accion = "crear"
        nombre = "generated_code.py"
        decision = {"accion": accion, "nombre": nombre}
    
    # Definir la ruta completa del fichero en el repositorio
    file_path = os.path.join(os.getcwd(), nombre)
    if accion == "modificar":
        if not os.path.exists(file_path):
            print(f"El fichero {nombre} no existe. Se creará un nuevo fichero en su lugar.")
            accion = "crear"
    if accion == "crear":
        print(f"Se creará un nuevo fichero: {nombre}")
    else:
        print(f"Se modificará el fichero existente: {nombre}")
    
    # Escribir (o sobrescribir) el código propuesto (resultado de Arquitecto Técnico) en el fichero
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(architecture)
        
    # Agregar y hacer commit de los cambios en Git
    subprocess.run(["git", "add", file_path])
    subprocess.run(["git", "commit", "-m", f"Auto-generated (Aider): {request_text} en fichero {nombre}"])
    print(f"\nCódigo generado y guardado en {file_path}\n")
    
    # Registrar el proceso en MongoDB
    log_entry = {
        "branch": get_current_branch(),
        "request": request_text,
        "analysis": analysis,
        "programming": programming,
        "architecture": architecture,
        "testing": testing,
        "file_decision": decision,
        "timestamp": time.time()
    }
    db_collection.insert_one(log_entry)
    print("Registro almacenado en MongoDB.\n")
    
    return file_path, architecture

##############################
# Interfaz Web para visualizar logs
##############################
def start_web_interface():
    """Levanta un servidor Flask para visualizar el historial de cambios."""
    app = Flask(__name__)
    
    log_template = """
    <!doctype html>
    <html lang="es">
    <head>
      <meta charset="utf-8">
      <title>Historial de Cambios</title>
      <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        tr:nth-child(even) { background-color: #fafafa; }
      </style>
    </head>
    <body>
      <h1>Historial de Cambios</h1>
      <table>
        <tr>
          <th>Timestamp</th>
          <th>Branch</th>
          <th>Solicitud</th>
          <th>Análisis</th>
          <th>Programación</th>
          <th>Arquitectura</th>
          <th>Testing</th>
          <th>Decisión de Fichero</th>
        </tr>
        {% for log in logs %}
        <tr>
          <td>{{ log.timestamp }}</td>
          <td>{{ log.branch }}</td>
          <td>{{ log.request }}</td>
          <td>{{ log.analysis }}</td>
          <td>{{ log.programming }}</td>
          <td>{{ log.architecture }}</td>
          <td>{{ log.testing }}</td>
          <td>{{ log.file_decision }}</td>
        </tr>
        {% endfor %}
      </table>
    </body>
    </html>
    """
    
    @app.route("/logs")
    def logs_view():
        logs = list(db_collection.find().sort("timestamp", -1))
        for log in logs:
            log["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log["timestamp"]))
        return render_template_string(log_template, logs=logs)
    
    print("Iniciando interfaz web en http://localhost:5000/logs ...")
    app.run(host="0.0.0.0", port=5000)

##############################
# Función Principal
##############################
def main():
    # Iniciar la interfaz web en un hilo paralelo
    web_thread = threading.Thread(target=start_web_interface, daemon=True)
    web_thread.start()
    
    branch_name = input("Ingrese el nombre del branch de trabajo: ").strip()
    switch_or_create_branch(branch_name)
    print(f"Trabajando en el branch {branch_name} en {os.getcwd()}\n")
    
    try:
        while True:
            command = input("Ingrese un comando ('rollback' para deshacer últimos cambios, 'exit' para salir): ").strip()
            if command.lower() == "rollback":
                rollback_last_commit()
            elif command.lower() == "exit":
                break
            else:
                apply_workflow(command)
    except KeyboardInterrupt:
        print("\nEjecución interrumpida por el usuario.")

if __name__ == "__main__":
    main()


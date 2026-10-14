"""App de consulta: CMg de una barra y riesgo nodal, desde la base cmg_cen.

La usa cualquier persona con acceso a la carpeta compartida: un ejecutable con
una ventana, que lee PostgreSQL con el usuario de solo lectura y entrega Excel.
A proposito no importa nada del resto del programa (DuckDB, Silver, la ingesta):
el ejecutable queda chico y no puede escribir en ningun lado salvo el Excel.
"""

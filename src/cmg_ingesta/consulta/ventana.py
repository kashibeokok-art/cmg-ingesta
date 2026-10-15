"""La ventana de la app (tkinter: viene con Python, no agrega nada al ejecutable).

Toda la logica esta en `informes`, `datos` y `buscar`; aqui solo se arma la
ventana, se leen las elecciones del usuario y se muestran los resultados. Las
funciones puras de arriba (fechas, rangos rapidos) se prueban sin abrir nada.

La consulta corre en un hilo aparte para que la ventana no se congele; el hilo
abre su propia conexion (una conexion de psycopg no se comparte entre hilos) y
avisa el resultado por una cola que la ventana revisa cada 100 ms.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cmg_ingesta.consulta import buscar, conexion, datos, informes

TITULO = "CMg por barra - SEN"
FORMATOS_FECHA = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d")


# ------------------------------------------------------- funciones puras


def parsear_fecha(texto: str) -> date:
    """'05-03-2025', '05/03/2025' o '2025-03-05'. ValueError con un mensaje claro."""
    limpio = texto.strip()
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(limpio, formato).date()
        except ValueError:
            continue
    raise ValueError(
        f"Fecha no valida: '{texto}'. Escribela como dd-mm-aaaa, por ejemplo 05-03-2025."
    )


def rangos_rapidos(hoy: date, disponible: tuple[date, date]) -> dict[str, tuple[date, date]]:
    """Los botones de rango, recortados a lo que tiene la base."""
    d1, d2 = disponible
    primero_mes = hoy.replace(day=1)
    fin_mes_pasado = primero_mes - timedelta(days=1)
    rangos = {
        "Mes pasado": (fin_mes_pasado.replace(day=1), fin_mes_pasado),
        "Este año": (date(hoy.year, 1, 1), hoy),
        "Ultimos 12 meses": (date(hoy.year - 1, hoy.month, 1), hoy),
        "Todo": (d1, d2),
    }
    recortados = {k: (max(a, d1), min(b, d2)) for k, (a, b) in rangos.items()}
    # si la base termina antes (o empieza despues), el rango recortado queda al reves:
    # entonces, el ultimo mes con datos
    ultimo_mes = (max(d1, d2.replace(day=1)), d2)
    return {k: (a, b) if a <= b else ultimo_mes for k, (a, b) in recortados.items()}


def _miles(n: int) -> str:
    """12345 -> '12.345', como se escribe en Chile."""
    return f"{n:,}".replace(",", ".")


def _todos_hijos(w: tk.Misc) -> list[tk.Misc]:
    hijos = list(w.winfo_children())
    return hijos + [n for h in hijos for n in _todos_hijos(h)]


def carpeta_descargas() -> Path:
    descargas = Path.home() / "Downloads"
    return descargas if descargas.is_dir() else Path.home()


def abrir(ruta: Path) -> None:
    """Abre un archivo o carpeta con el programa de Windows que corresponda."""
    if os.name == "nt":
        os.startfile(ruta)  # solo existe en Windows
    else:  # pragma: no cover
        subprocess.run(["xdg-open", str(ruta)], check=False)


# ------------------------------------------------------- piezas de la ventana


def selector_barra(
    padre: tk.Misc, titulo: str, barras: list[str]
) -> tuple[ttk.LabelFrame, Callable[[], str | None]]:
    """Buscador: se escribe, aparecen las 10 mas parecidas con su %, se elige con clic.

    Nunca elige solo (pedido del usuario): aunque haya una sola parecida, hay que
    hacer clic. Devuelve el marco y una funcion que entrega la barra elegida.
    """
    marco = ttk.LabelFrame(padre, text=titulo, padding=8)
    texto = tk.StringVar()
    elegida: dict[str, str | None] = {"barra": None}
    etiqueta = tk.StringVar(value="Elegida: (ninguna)")

    ttk.Label(marco, text="Escribe parte del nombre (ej. santa elvira 13):").pack(anchor="w")
    entrada = ttk.Entry(marco, textvariable=texto, width=40)
    entrada.pack(fill="x", pady=(2, 4))
    lista = tk.Listbox(
        marco, height=10, font=("Consolas", 10), activestyle="dotbox", exportselection=False
    )
    lista.pack(fill="both", expand=True)
    ttk.Label(marco, textvariable=etiqueta, font=("Segoe UI", 9, "bold")).pack(
        anchor="w", pady=(4, 0)
    )
    opciones: list[str] = []

    def actualizar(*_: object) -> None:
        lista.delete(0, tk.END)
        opciones.clear()
        for c in buscar.buscar(texto.get(), barras):
            opciones.append(c.barra)
            sufijo = f"{c.porcentaje:>4}%" if texto.get().strip() else ""
            lista.insert(tk.END, f"{c.barra:<20}{sufijo}")

    def elegir(_: object = None) -> None:
        sel: tuple[int, ...] = lista.curselection()  # type: ignore[no-untyped-call]
        if sel:
            elegida["barra"] = opciones[sel[0]]
            etiqueta.set(f"Elegida: {elegida['barra']}")

    texto.trace_add("write", actualizar)
    lista.bind("<<ListboxSelect>>", elegir)
    actualizar()
    return marco, lambda: elegida["barra"]


def selector_varias(
    padre: tk.Misc, barras: list[str]
) -> tuple[ttk.LabelFrame, Callable[[], list[str] | None]]:
    """Una, varias o todas las barras. Devuelve el marco y una funcion que entrega
    la lista de elegidas, o `None` si se marco "Todas las barras".

    Igual que `selector_barra`, nunca elige sola: la busqueda muestra las 10 mas
    parecidas con su %, y cada barra se AGREGA a mano (doble clic o boton).
    """
    marco = ttk.LabelFrame(padre, text="Barras", padding=8)
    todas = tk.BooleanVar(value=False)
    texto = tk.StringVar()
    contador = tk.StringVar(value="Elegidas: 0")
    opciones: list[str] = []
    elegidas: list[str] = []

    ttk.Checkbutton(
        marco,
        text="Todas las barras (CSV con una columna por barra + Excel con los bloques)",
        variable=todas,
    ).pack(anchor="w", pady=(0, 6))
    columnas = ttk.Frame(marco)
    columnas.pack(fill="both", expand=True)

    izquierda = ttk.Frame(columnas)
    izquierda.pack(side="left", fill="both", expand=True)
    ttk.Label(izquierda, text="Buscar (ej. santa elvira 13):").pack(anchor="w")
    entrada = ttk.Entry(izquierda, textvariable=texto, width=34)
    entrada.pack(fill="x", pady=(2, 4))
    resultados = tk.Listbox(
        izquierda, height=10, font=("Consolas", 10), activestyle="dotbox", exportselection=False
    )
    resultados.pack(fill="both", expand=True)

    medio = ttk.Frame(columnas, padding=(6, 40))
    medio.pack(side="left", fill="y")
    agregar_b = ttk.Button(medio, text="Agregar →")
    agregar_b.pack(pady=(0, 6))
    quitar_b = ttk.Button(medio, text="← Quitar")
    quitar_b.pack(pady=(0, 6))
    vaciar_b = ttk.Button(medio, text="Quitar todas")
    vaciar_b.pack()

    derecha = ttk.Frame(columnas)
    derecha.pack(side="left", fill="both", expand=True)
    ttk.Label(derecha, textvariable=contador, font=("Segoe UI", 9, "bold")).pack(anchor="w")
    lista_elegidas = tk.Listbox(
        derecha, height=11, font=("Consolas", 10), activestyle="dotbox", exportselection=False
    )
    lista_elegidas.pack(fill="both", expand=True, pady=(4, 0))

    def mostrar_resultados(*_: object) -> None:
        resultados.delete(0, tk.END)
        opciones.clear()
        for c in buscar.buscar(texto.get(), barras):
            opciones.append(c.barra)
            sufijo = f"{c.porcentaje:>4}%" if texto.get().strip() else ""
            marca = "  ya elegida" if c.barra in elegidas else ""
            resultados.insert(tk.END, f"{c.barra:<20}{sufijo}{marca}")

    def mostrar_elegidas() -> None:
        lista_elegidas.delete(0, tk.END)
        for b in elegidas:
            lista_elegidas.insert(tk.END, b)
        contador.set(f"Elegidas: {len(elegidas)}")
        mostrar_resultados()

    def agregar(_: object = None) -> None:
        sel: tuple[int, ...] = resultados.curselection()  # type: ignore[no-untyped-call]
        if sel and opciones[sel[0]] not in elegidas:
            elegidas.append(opciones[sel[0]])
            mostrar_elegidas()

    def quitar(_: object = None) -> None:
        sel: tuple[int, ...] = lista_elegidas.curselection()  # type: ignore[no-untyped-call]
        if sel:
            elegidas.pop(sel[0])
            mostrar_elegidas()

    def vaciar() -> None:
        elegidas.clear()
        mostrar_elegidas()

    def cambiar_todas(*_: object) -> None:
        estado = ["disabled"] if todas.get() else ["!disabled"]
        for w in (entrada, agregar_b, quitar_b, vaciar_b):
            w.state(estado)
        resultados.configure(state="disabled" if todas.get() else "normal")
        lista_elegidas.configure(state="disabled" if todas.get() else "normal")

    texto.trace_add("write", mostrar_resultados)
    todas.trace_add("write", cambiar_todas)
    resultados.bind("<Double-Button-1>", agregar)
    resultados.bind("<Return>", agregar)
    lista_elegidas.bind("<Double-Button-1>", quitar)
    agregar_b.configure(command=agregar)
    quitar_b.configure(command=quitar)
    vaciar_b.configure(command=vaciar)
    mostrar_resultados()
    return marco, lambda: None if todas.get() else list(elegidas)


def selector_fechas(
    padre: tk.Misc, disponible: tuple[date, date]
) -> tuple[ttk.LabelFrame, Callable[[], tuple[date, date]]]:
    """Desde/hasta escritos (dd-mm-aaaa) y botones de rango rapido."""
    marco = ttk.LabelFrame(padre, text="Rango de fechas (dd-mm-aaaa)", padding=8)
    rapidos = rangos_rapidos(date.today(), disponible)
    d1, d2 = rapidos["Mes pasado"]
    desde = tk.StringVar(value=f"{d1:%d-%m-%Y}")
    hasta = tk.StringVar(value=f"{d2:%d-%m-%Y}")

    fila = ttk.Frame(marco)
    fila.pack(fill="x")
    ttk.Label(fila, text="Desde").pack(side="left")
    ttk.Entry(fila, textvariable=desde, width=12).pack(side="left", padx=(4, 12))
    ttk.Label(fila, text="Hasta").pack(side="left")
    ttk.Entry(fila, textvariable=hasta, width=12).pack(side="left", padx=4)

    botones = ttk.Frame(marco)
    botones.pack(fill="x", pady=(6, 0))
    for nombre, (a, b) in rapidos.items():

        def poner(a: date = a, b: date = b) -> None:
            desde.set(f"{a:%d-%m-%Y}")
            hasta.set(f"{b:%d-%m-%Y}")

        ttk.Button(botones, text=nombre, command=poner).pack(side="left", padx=(0, 4))

    return marco, lambda: (parsear_fecha(desde.get()), parsear_fecha(hasta.get()))


# --------------------------------------------------------------- la ventana


def construir(datos_conexion: conexion.DatosConexion) -> tk.Tk:
    """Arma la ventana y empieza a conectarse. `principal` la deja abierta; un test, no."""
    raiz = tk.Tk()
    raiz.title(TITULO)
    raiz.geometry("900x740")
    raiz.minsize(800, 640)
    ttk.Style().theme_use("vista" if "vista" in ttk.Style().theme_names() else "clam")

    estado = tk.StringVar(value="Conectando a la base...")
    ttk.Label(raiz, textvariable=estado, padding=(10, 8)).pack(anchor="w")
    cuerpo = ttk.Frame(raiz, padding=(10, 0))
    cuerpo.pack(fill="both", expand=True)

    pie = ttk.Frame(raiz, padding=10)
    pie.pack(fill="x")
    progreso = ttk.Progressbar(pie, mode="indeterminate", length=160)
    mensaje = tk.StringVar()
    ttk.Label(pie, textvariable=mensaje, wraplength=520).pack(side="left", fill="x", expand=True)
    abrir_archivo = ttk.Button(pie, text="Abrir archivo")
    abrir_carpeta = ttk.Button(pie, text="Abrir carpeta")

    resultados: queue.Queue[tuple[str, object]] = queue.Queue()
    botones_accion: list[ttk.Button] = []

    def en_segundo_plano(
        tarea: Callable[[datos.Conexion], object], al_terminar: Callable[[object], None]
    ) -> None:
        def correr() -> None:
            try:
                with conexion.conectar(datos_conexion) as conn:
                    resultados.put(("ok", (tarea(conn), al_terminar)))
            except Exception as e:  # se muestra al usuario, no se pierde
                resultados.put(("error", e))

        threading.Thread(target=correr, daemon=True).start()

    def avance(texto: str) -> None:
        """Lo llama el hilo de la consulta: el texto llega a la ventana por la cola."""
        resultados.put(("avance", texto))

    def revisar_cola() -> None:
        try:
            tipo, valor = resultados.get_nowait()
        except queue.Empty:
            raiz.after(100, revisar_cola)
            return
        if tipo == "avance":  # la consulta sigue: solo cambia el texto
            mensaje.set(str(valor))
            raiz.after(100, revisar_cola)
            return
        progreso.stop()
        progreso.pack_forget()
        for b in botones_accion:
            b.state(["!disabled"])
        if tipo == "ok":
            assert isinstance(valor, tuple)
            resultado, al_terminar = valor
            al_terminar(resultado)
        else:
            texto = (
                str(valor) if isinstance(valor, ValueError) else f"{type(valor).__name__}: {valor}"
            )
            mensaje.set("")
            if not botones_accion:  # fallo al conectarse: la ventana no tiene pestanas
                estado.set(f"No se pudo conectar a la base: {texto}")
            messagebox.showerror(TITULO, texto)
        raiz.after(100, revisar_cola)

    def ocupado(texto: str) -> None:
        mensaje.set(texto)
        abrir_archivo.pack_forget()
        abrir_carpeta.pack_forget()
        for b in botones_accion:
            b.state(["disabled"])
        progreso.pack(side="right")
        progreso.start(12)

    def listo(resultado: object) -> None:
        """Un archivo (Path) o, con todas las barras, (CSV, Excel de bloques)."""
        if isinstance(resultado, tuple):
            csv_, excel_ = resultado
            mensaje.set(f"Listo: {csv_.name} y {excel_.name}, en {csv_.parent}")
            principal_ = excel_  # el CSV puede pesar cientos de MB: se abre el Excel
        else:
            assert isinstance(resultado, Path)
            mensaje.set(f"Listo: {resultado}")
            principal_ = resultado
        abrir_archivo.configure(command=lambda: abrir(principal_))
        abrir_carpeta.configure(command=lambda: abrir(principal_.parent))
        abrir_carpeta.pack(side="right", padx=(4, 0))
        abrir_archivo.pack(side="right")

    def guardar_como(sugerido: str, csv: bool = False) -> Path | None:
        ruta = filedialog.asksaveasfilename(
            title="Guardar el CSV" if csv else "Guardar el Excel",
            initialdir=carpeta_descargas(),
            initialfile=sugerido,
            defaultextension=".csv" if csv else ".xlsx",
            filetypes=[("CSV", "*.csv")] if csv else [("Excel", "*.xlsx")],
        )
        return Path(ruta) if ruta else None

    def armar(inicio: object) -> None:
        assert isinstance(inicio, tuple)
        barras, disponible = inicio
        if disponible is None:
            estado.set("La base no tiene datos todavia.")
            return
        d1, d2 = disponible
        estado.set(
            f"Conectado a {datos_conexion['dbname']} ({datos_conexion['host']}). "
            f"Datos del {d1:%d-%m-%Y} al {d2:%d-%m-%Y}, {_miles(len(barras))} barras."
        )
        pestanas = ttk.Notebook(cuerpo)
        pestanas.pack(fill="both", expand=True)

        # --- CMg de una, varias o todas las barras
        p1 = ttk.Frame(pestanas, padding=8)
        pestanas.add(p1, text="CMg por barra")
        sel, barras_elegidas = selector_varias(p1, barras)
        sel.pack(fill="both", expand=True)
        # el cursor queda en el buscador: se abre y se escribe
        next(w for w in _todos_hijos(sel) if isinstance(w, ttk.Entry)).focus_set()
        fechas1, rango1 = selector_fechas(p1, disponible)
        fechas1.pack(fill="x", pady=8)
        ttk.Label(
            p1,
            text="Una barra: Excel con el CMg cada 15 minutos y los bloques (A, B, C, Solar, "
            "NoSolar) por mes y del rango. Varias: el CMg en una columna por barra. Todas: "
            "CSV con una columna por barra + Excel con los bloques.",
            wraplength=760,
        ).pack(anchor="w")

        def descargar_cmg() -> None:
            elegidas = barras_elegidas()
            if elegidas is not None and not elegidas:
                messagebox.showwarning(
                    TITULO, "Agrega al menos una barra (doble clic en la lista), o marca Todas."
                )
                return
            try:
                desde, hasta = rango1()
                informes.validar_rango(desde, hasta, disponible)
            except ValueError as e:
                messagebox.showwarning(TITULO, str(e))
                return
            periodo = (f"{desde:%Y%m%d}", f"{hasta:%Y%m%d}")
            if elegidas is None:
                sugerido = informes.nombre_sugerido("CMg_todas", *periodo)[:-5] + ".csv"
                ruta_csv = guardar_como(sugerido, csv=True)
                if ruta_csv is None:
                    return
                csv_: Path = ruta_csv  # ya no es None: el hilo lo usa tal cual
                ocupado(f"Todas las barras, del {desde:%d-%m-%Y} al {hasta:%d-%m-%Y}...")
                en_segundo_plano(
                    lambda c: informes.cmg_todas(c, desde, hasta, csv_, avisar=avance), listo
                )
                return
            if len(elegidas) == 1:
                barra = elegidas[0]
                ruta_una = guardar_como(informes.nombre_sugerido("CMg", barra, *periodo))
                if ruta_una is None:
                    return
                una: Path = ruta_una
                ocupado(f"Consultando {barra} del {desde:%d-%m-%Y} al {hasta:%d-%m-%Y}...")
                en_segundo_plano(lambda c: informes.cmg_barra(c, barra, desde, hasta, una), listo)
                return
            ruta = guardar_como(
                informes.nombre_sugerido("CMg", f"{len(elegidas)}_barras", *periodo)
            )
            if ruta is None:
                return
            ocupado(
                f"Consultando {len(elegidas)} barras del {desde:%d-%m-%Y} al {hasta:%d-%m-%Y}..."
            )
            en_segundo_plano(lambda c: informes.cmg_varias(c, elegidas, desde, hasta, ruta), listo)

        b1 = ttk.Button(p1, text="Descargar", command=descargar_cmg)
        b1.pack(anchor="e", pady=(8, 0))
        botones_accion.append(b1)

        # --- Riesgo nodal
        p2 = ttk.Frame(pestanas, padding=8)
        pestanas.add(p2, text="Riesgo nodal")
        dos = ttk.Frame(p2)
        dos.pack(fill="both", expand=True)
        sel_ref, ref_elegida = selector_barra(dos, "Referencia", barras)
        sel_ref.pack(side="left", fill="both", expand=True, padx=(0, 4))
        sel_comp, comp_elegida = selector_barra(dos, "Comparada", barras)
        sel_comp.pack(side="left", fill="both", expand=True, padx=(4, 0))
        fechas2, rango2 = selector_fechas(p2, disponible)
        fechas2.pack(fill="x", pady=8)
        ttk.Label(
            p2,
            text="Riesgo = CMg(comparada) - CMg(referencia). Positivo: la comparada es mas cara. "
            "El Excel trae cada 15 minutos y el resumen por mes y bloque.",
            wraplength=700,
        ).pack(anchor="w")

        def descargar_riesgo() -> None:
            ref, comp = ref_elegida(), comp_elegida()
            if ref is None or comp is None:
                messagebox.showwarning(TITULO, "Elige la barra de referencia y la comparada.")
                return
            try:
                desde, hasta = rango2()
                informes.validar_rango(desde, hasta, disponible)
            except ValueError as e:
                messagebox.showwarning(TITULO, str(e))
                return
            if ref == comp:
                messagebox.showwarning(TITULO, "La referencia y la comparada son la misma barra.")
                return
            sugerido = informes.nombre_sugerido(
                "Riesgo", ref, "vs", comp, f"{desde:%Y%m%d}", f"{hasta:%Y%m%d}"
            )
            ruta = guardar_como(sugerido)
            if ruta is None:
                return
            ocupado(f"Calculando riesgo {ref} vs {comp}...")
            en_segundo_plano(
                lambda c: informes.riesgo_nodal(c, ref, comp, desde, hasta, ruta), listo
            )

        b2 = ttk.Button(p2, text="Descargar Excel", command=descargar_riesgo)
        b2.pack(anchor="e", pady=(8, 0))
        botones_accion.append(b2)
        mensaje.set("")

    ocupado("Conectando a la base...")
    en_segundo_plano(lambda c: (datos.barras(c), datos.rango_disponible(c)), armar)
    raiz.after(100, revisar_cola)
    return raiz


def principal(datos_conexion: conexion.DatosConexion) -> None:
    construir(datos_conexion).mainloop()


def prueba(args: list[str]) -> int:
    """`CMG_Consulta.exe --prueba BARRA REFERENCIA AAAA-MM-DD AAAA-MM-DD CARPETA`.

    Sin ventana: escribe en CARPETA los Excel de una barra, de las dos barras juntas
    y del riesgo, y el CSV de todas las barras del PRIMER dia (para que sea rapido),
    y deja `prueba.log` con el resultado. Sirve para comprobar el ejecutable ya
    armado, y para diagnosticar en el equipo de un usuario si la ventana no abre.
    """
    carpeta = Path(args[4])
    carpeta.mkdir(parents=True, exist_ok=True)
    log = carpeta / "prueba.log"
    try:
        barra, referencia = args[0], args[1]
        desde, hasta = parsear_fecha(args[2]), parsear_fecha(args[3])
        with conexion.conectar(conexion.leer_ini(conexion.ruta_ini())) as conn:
            a = informes.cmg_barra(conn, barra, desde, hasta, carpeta / "prueba_cmg.xlsx")
            b = informes.riesgo_nodal(
                conn, referencia, barra, desde, hasta, carpeta / "prueba_riesgo.xlsx"
            )
            c = informes.cmg_varias(
                conn, [barra, referencia], desde, hasta, carpeta / "prueba_varias.xlsx"
            )
            d, e = informes.cmg_todas(conn, desde, desde, carpeta / "prueba_todas.csv")
        log.write_text(f"ok\n{a}\n{b}\n{c}\n{d}\n{e}\n", encoding="utf-8")
        return 0
    except Exception as e:
        log.write_text(f"error\n{type(e).__name__}: {e}\n", encoding="utf-8")
        return 1


def main() -> None:
    """Punto de entrada del ejecutable: lee conexion.ini y abre la ventana."""
    if len(sys.argv) == 7 and sys.argv[1] == "--prueba":
        sys.exit(prueba(sys.argv[2:]))
    try:
        datos_conexion = conexion.leer_ini(conexion.ruta_ini())
    except ValueError as e:
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showerror(TITULO, str(e))
        raiz.destroy()
        return
    principal(datos_conexion)


if __name__ == "__main__":
    main()

"""Un "guion" de respuestas para probar el menu sin teclado.

Es un modulo de ayuda y no un `conftest.py`: los tests lo importan con
`from guion import Guion` (pytest agrega la carpeta del test a `sys.path`), y un
segundo `conftest.py` chocaria en mypy con el de `tests/` (mismo nombre de modulo).

`Guion` cumple el papel del usuario: entrega las respuestas en orden y junta todo
lo que el programa pregunta e imprime. Cuando se le acaban las respuestas lanza
`EOFError`, igual que `input()` con la entrada cerrada: un test que espera menos
preguntas de las que hace el programa termina limpio en vez de colgarse.
"""

from cmg_ingesta.menu.consola import Consola


class Guion:
    def __init__(self, *respuestas: str) -> None:
        self.pendientes = list(respuestas)
        self.salida: list[str] = []

    def leer(self, texto: str) -> str:
        self.salida.append(texto)
        if not self.pendientes:
            raise EOFError
        return self.pendientes.pop(0)

    def escribir(self, texto: str) -> None:
        self.salida.append(texto)

    @property
    def texto(self) -> str:
        return "\n".join(self.salida)

    def consola(self) -> Consola:
        return Consola(leer=self.leer, escribir=self.escribir)

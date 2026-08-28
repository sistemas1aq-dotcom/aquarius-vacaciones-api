"""Sincronización del maestro de personal desde el sistema de Planillas.

Consume el API de exposición que ya corre en el servidor de Planillas (el mismo
que usa Assistime) y concilia el padrón contra Employees / Departments.

DECISIONES DE DISEÑO (ver también integracion-planillas en la memoria del proyecto):

1. NEXO = DNI. `Employees.Dni` es UNIQUE y `NUM_DOC_IDENTIDAD` es obligatorio en
   el contrato del API. No se empareja por nombre ni por código.

2. ALCANCE = las filas de `Departments` que tienen (CodEmpresa, CodTipoPlanilla).
   Los tipos de planilla no mapeados NO se sincronizan, pero SÍ se informan en el
   resultado de la corrida: así se detecta que apareció un tipo nuevo en el
   origen sin ensuciar la tabla que alimenta desplegables y reportes.

3. Se empareja el departamento por CÓDIGO, nunca por nombre. Los nombres ya
   divergen entre ambos sistemas ('ADMINISTRACION' vs 'ADMINISTRACIÓN',
   'VALUACION' vs 'VALUACIONES') y volverían a divergir con cualquier renombrado.

4. ÁMBITO DE CESES: solo se consideran para cese los empleados cuyo `CodEmpresa`
   coincide con la empresa sincronizada. Esto tiene una consecuencia deliberada:
   en la PRIMERA corrida nadie tiene `CodEmpresa`, así que no se cesa a nadie y
   la corrida se limita a rellenar ese campo. De la segunda en adelante la
   detección funciona, y los empleados creados a mano en Vacaciones —que nunca
   tendrán `CodEmpresa`— jamás son cesados por el sync.

5. Un empleado que en Planillas pasa a un tipo FUERA del alcance no se cesa: se
   deja intacto y se informa. Cesarlo sería falso (sigue trabajando) y además
   alteraría los saldos de vacaciones de la empresa entera.

6. Con `limite > 0` el padrón que llega es PARCIAL, así que NO se calculan ceses
   bajo ninguna circunstancia. Es una salvaguarda, no una optimización.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date, datetime
from typing import Any, Optional

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.models import Department, Employee, SyncCorrida

settings = get_settings()


# Alias que este sync consume del payload. Se validan al recibir la respuesta:
# si el personal.sql del servidor de Planillas cambia y deja de traer alguno,
# la corrida falla con un mensaje claro en vez de un KeyError a las 3 am.
ALIAS_REQUERIDOS = (
    "NUM_DOC_IDENTIDAD",
    "COD_EMPRESA",
    "COD_TIPO_PLANILLA",
    "COD_PERSONAL",
    "APE_PATERNO",
    "APE_MATERNO",
    "NOM_TRABAJADOR",
    "FEC_INGRESO",
    "FEC_CESADO",
    "TIP_ESTADO",
)


class SyncError(Exception):
    """Fallo que impide completar la corrida. No se escribe nada."""


# ───────────────────────────────────────────────────────────────────
# Utilidades
# ───────────────────────────────────────────────────────────────────

def _texto(valor: Any) -> Optional[str]:
    """Normaliza a texto limpio, o None si viene vacío."""
    if valor is None:
        return None
    s = str(valor).strip()
    return s or None


def _nombre_completo(fila: dict) -> str:
    """APE_PATERNO + APE_MATERNO + NOM_TRABAJADOR, con espacios colapsados.

    Es el mismo orden que ya tienen los registros cargados en Vacaciones
    ('CORTEZ ROJAS JOSE YOBERLI' = paterno materno nombres).
    """
    partes = [_texto(fila.get(c)) for c in ("APE_PATERNO", "APE_MATERNO", "NOM_TRABAJADOR")]
    return " ".join(p for p in partes if p)


def _fecha(valor: Any) -> Optional[date]:
    """Convierte las fechas del API (texto dd/mm/yyyy) a date.

    El contrato del API dice dd/mm/yyyy (CONVERT ... 103). Se aceptan también
    ISO y datetime por si una instalación devuelve otro formato: es preferible
    tolerar a fallar la corrida entera por un formato de fecha.
    """
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    s = str(valor).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], formato).date()
        except ValueError:
            continue
    return None


def _dni(fila: dict) -> Optional[str]:
    return _texto(fila.get("NUM_DOC_IDENTIDAD"))


def _clave_tipo(fila: dict) -> tuple[Optional[str], Optional[str]]:
    return (_texto(fila.get("COD_EMPRESA")), _texto(fila.get("COD_TIPO_PLANILLA")))


def _activo_en_origen(fila: dict) -> bool:
    """El estado real en Planillas es TIP_ESTADO; 'AC' = activo."""
    return (_texto(fila.get("TIP_ESTADO")) or "").upper() == "AC"


def _clave_nombre(texto: Optional[str]) -> str:
    """Normaliza un nombre para comparar: sin tildes, mayúsculas, espacios simples.

    Sirve para detectar que un 'alta' es en realidad un empleado que ya existe
    con el documento en otro formato. No se usa para emparejar de verdad -- el
    nexo sigue siendo el DNI -- solo para avisar.
    """
    if not texto:
        return ""
    t = unicodedata.normalize("NFD", texto.upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    # La Ñ pierde la tilde con lo anterior y queda como N; da igual, lo que
    # importa es que ambos lados se normalicen igual.
    return " ".join(t.split())


def _reparar_codificacion(texto: Optional[str]) -> Optional[str]:
    """Deshace el doble-encodeado UTF-8→Latin-1 si lo detecta.

    Los datos actuales de Vacaciones traen nombres como 'OROÃ±A' en lugar de
    'OROÑA' (defecto anterior a esta integración). Si el payload de Planillas
    viniera con el mismo problema, esto lo corrige antes de escribir en vez de
    propagarlo. Si el texto está sano, lo devuelve intacto.
    """
    if not texto or not any(s in texto for s in ("Ã", "Â", "â€")):
        return texto
    try:
        arreglado = texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto
    # Solo se acepta si el resultado es texto razonable (sin caracteres de control).
    if any(unicodedata.category(c) == "Cc" for c in arreglado):
        return texto
    return arreglado


# ───────────────────────────────────────────────────────────────────
# Origen de datos
# ───────────────────────────────────────────────────────────────────

def obtener_padron(cod_empresa: str, limite: int = 0) -> list[dict]:
    """Llama al API de exposición de Planillas y devuelve las filas del padrón."""
    if not settings.planillas_api_url:
        raise SyncError("Falta configurar PLANILLAS_API_URL")
    if not settings.planillas_api_token:
        raise SyncError("Falta configurar PLANILLAS_API_TOKEN")

    url = f"{settings.planillas_api_url.rstrip('/')}/api/personal"
    params: dict[str, Any] = {"cod_empresa": cod_empresa}
    if limite and limite > 0:
        params["limit"] = limite

    try:
        respuesta = httpx.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {settings.planillas_api_token}"},
            timeout=settings.planillas_timeout_seg,
        )
    except httpx.RequestError as exc:
        raise SyncError(f"No se pudo contactar el API de Planillas ({url}): {exc}") from exc

    if respuesta.status_code == 401:
        raise SyncError("El API de Planillas rechazó el token (401). Revisa PLANILLAS_API_TOKEN.")
    if respuesta.status_code != 200:
        raise SyncError(
            f"El API de Planillas respondió {respuesta.status_code}: {respuesta.text[:300]}"
        )

    try:
        cuerpo = respuesta.json()
    except ValueError as exc:
        raise SyncError(f"El API de Planillas no devolvió JSON válido: {exc}") from exc

    filas = cuerpo.get("personal")
    if not isinstance(filas, list):
        raise SyncError("La respuesta del API no trae la lista 'personal'.")
    if not filas:
        return []

    faltantes = [a for a in ALIAS_REQUERIDOS if a not in filas[0]]
    if faltantes:
        raise SyncError(
            "El padrón de Planillas no trae las columnas que este sync necesita: "
            + ", ".join(faltantes)
            + ". Revisa el personal.sql del servidor de Planillas."
        )

    return filas


# ───────────────────────────────────────────────────────────────────
# Conciliación
# ───────────────────────────────────────────────────────────────────

def _mapa_departamentos(db: Session) -> dict[tuple[str, str], Department]:
    """(CodEmpresa, CodTipoPlanilla) -> Department, para los que estén mapeados.

    Se incluyen también los departamentos inactivos: IsActive controla si el
    departamento se ofrece en los desplegables, no si su gente se sincroniza.
    """
    filas = (
        db.query(Department)
        .filter(Department.CodEmpresa.isnot(None), Department.CodTipoPlanilla.isnot(None))
        .all()
    )
    return {(d.CodEmpresa, d.CodTipoPlanilla): d for d in filas}


def _campos_desde_origen(fila: dict, departamento: Department) -> dict:
    """Traduce una fila del padrón a los campos de Employees."""
    return {
        "FullName": _reparar_codificacion(_nombre_completo(fila)) or "",
        "Email": _texto(fila.get("NUM_EMAIL")),
        "DepartmentId": departamento.Id,
        "Position": _reparar_codificacion(_texto(fila.get("DES_CARGO"))),
        "HireDate": _fecha(fila.get("FEC_INGRESO")),
        "CeaseDate": _fecha(fila.get("FEC_CESADO")),
        "IsActive": _activo_en_origen(fila),
        "CodPersonal": _texto(fila.get("COD_PERSONAL")),
        "CodEmpresa": _texto(fila.get("COD_EMPRESA")),
    }


def sincronizar(
    db: Session,
    cod_empresa: Optional[str] = None,
    limite: int = 0,
    dry_run: bool = False,
) -> dict:
    """Ejecuta una corrida de sincronización y devuelve su resumen.

    Con dry_run=True no se escribe NADA en Employees: se calcula todo y se
    informa qué habría pasado. La corrida sí queda registrada en SyncCorrida,
    marcada como dry run.
    """
    empresa = (cod_empresa or settings.planillas_cod_empresa or "0").strip() or "0"
    limite = max(0, int(limite or 0))

    corrida = SyncCorrida(CodEmpresa=empresa, DryRun=dry_run, Limite=limite)
    db.add(corrida)
    db.commit()
    db.refresh(corrida)

    detalle: dict[str, Any] = {}
    errores: list[str] = []

    try:
        filas = obtener_padron(empresa, limite)
        departamentos = _mapa_departamentos(db)
        if not departamentos:
            raise SyncError(
                "Ningún departamento tiene (CodEmpresa, CodTipoPlanilla). "
                "Ejecuta primero 06_preparar_integracion.sql."
            )

        # --- Clasificación del padrón -------------------------------------
        en_alcance: dict[str, dict] = {}      # dni -> fila
        dnis_del_padron: set[str] = set()     # todos, dentro y fuera de alcance
        ignorados: dict[str, int] = {}        # "0001/06" -> conteo
        sin_dni = 0

        for fila in filas:
            dni = _dni(fila)
            if not dni:
                sin_dni += 1
                continue
            dnis_del_padron.add(dni)
            clave = _clave_tipo(fila)
            if clave in departamentos:
                # Si el mismo DNI viniera repetido, gana el último: el padrón
                # viene ordenado y no debería haber duplicados, pero no se
                # revienta la corrida por ello.
                en_alcance[dni] = fila
            else:
                etiqueta = f"{clave[0] or '?'}/{clave[1] or '?'}"
                ignorados[etiqueta] = ignorados.get(etiqueta, 0) + 1

        if sin_dni:
            errores.append(f"{sin_dni} filas del padrón sin NUM_DOC_IDENTIDAD; se omitieron.")

        # --- Empleados de Vacaciones que el sync considera suyos ----------
        consulta = db.query(Employee)
        if empresa == "0":
            consulta = consulta.filter(Employee.CodEmpresa.isnot(None))
        else:
            consulta = consulta.filter(Employee.CodEmpresa == empresa)
        propios = {e.Dni: e for e in consulta.all() if e.Dni}

        # Todos los de Vacaciones, por DNI: hace falta para reconocer a los que
        # aún no tienen CodEmpresa (primera corrida) y no darlos de alta dos veces.
        todos = {e.Dni: e for e in db.query(Employee).all() if e.Dni}

        # --- Cálculo de acciones ------------------------------------------
        altas: list[dict] = []
        actualizaciones: list[tuple[Employee, dict]] = []
        reactivaciones = 0
        fuera_de_alcance: list[dict] = []
        omitidos_cesados: list[str] = []

        for dni, fila in en_alcance.items():
            departamento = departamentos[_clave_tipo(fila)]
            campos = _campos_desde_origen(fila, departamento)
            existente = todos.get(dni)

            if existente is None:
                # Solo se dan de alta trabajadores ACTIVOS. El padrón incluye a
                # los cesados de este año (FEC_CESADO >= 1 de enero), y crear en
                # Vacaciones a alguien que ya cesó y que nunca estuvo aquí no
                # aporta nada: no hay historial de vacaciones que gestionar y
                # solo engorda el maestro.
                # A los que YA existen sí se les actualiza el cese: ese caso cae
                # por la rama de abajo.
                if not _activo_en_origen(fila):
                    omitidos_cesados.append(campos["FullName"])
                    continue
                altas.append({"dni": dni, "campos": campos, "nombre": campos["FullName"]})
                continue

            cambios = {
                k: v for k, v in campos.items()
                if getattr(existente, k, None) != v
            }
            if cambios:
                # Comparación por veracidad, NO por identidad: `is False` falla
                # si el driver devuelve el BIT como entero (0/1) en vez de bool,
                # y entonces las reactivaciones se cuentan como 0 aunque el dato
                # sí se actualice. El contador es lo que se usa para revisar la
                # corrida, así que un contador mudo es peor que inútil.
                if "IsActive" in cambios and cambios["IsActive"] and not existente.IsActive:
                    reactivaciones += 1
                actualizaciones.append((existente, cambios))

        # --- Posibles duplicados -------------------------------------------
        # Un "alta" cuyo nombre YA existe en Vacaciones casi siempre significa
        # que la misma persona está en los dos sistemas con el documento en
        # distinto formato (ceros a la izquierda, carné de extranjería, un
        # dígito mal tecleado). Si se deja pasar, el sync crea un duplicado y
        # además cesa el registro original por "ausente del padrón".
        # No se bloquea la corrida: se avisa, porque la decisión de qué
        # documento es el bueno es humana.
        # Se comprueba por DOS vías, porque el nombre solo no basta:
        #
        #   a) COD_PERSONAL -- el código de Planillas. Es la señal FUERTE: si un
        #      empleado de Vacaciones ya lo tiene, es literalmente la misma
        #      persona, sin lugar a interpretación.
        #   b) Nombre normalizado -- la señal DÉBIL, de respaldo, para cuando el
        #      empleado existente no tiene CodPersonal cargado. Falla si el
        #      nombre trae basura de codificación ('CASTAÃEDA' no normaliza
        #      igual que 'CASTAÑEDA'), y por eso no puede ser la única.
        por_codpersonal: dict[tuple[str, str], Employee] = {}
        por_nombre: dict[str, Employee] = {}
        for emp in todos.values():
            if emp.CodEmpresa and emp.CodPersonal:
                por_codpersonal.setdefault((emp.CodEmpresa, emp.CodPersonal), emp)
            clave = _clave_nombre(emp.FullName)
            if clave:
                por_nombre.setdefault(clave, emp)

        posibles_duplicados = []
        for alta in altas:
            campos = alta["campos"]
            existente = None
            motivo = None
            if campos.get("CodEmpresa") and campos.get("CodPersonal"):
                existente = por_codpersonal.get(
                    (campos["CodEmpresa"], campos["CodPersonal"])
                )
                if existente is not None:
                    motivo = "mismo COD_PERSONAL"
            if existente is None:
                existente = por_nombre.get(_clave_nombre(alta["nombre"]))
                if existente is not None:
                    motivo = "mismo nombre"
            if existente is not None:
                posibles_duplicados.append({
                    "motivo": motivo,
                    "nombre": alta["nombre"],
                    "cod_personal": campos.get("CodPersonal"),
                    "dni_en_planillas": alta["dni"],
                    "dni_en_vacaciones": existente.Dni,
                    "employee_id": existente.Id,
                    "activo_en_vacaciones": bool(existente.IsActive),
                })

        # Empleados propios y activos que en el padrón aparecen bajo un tipo NO
        # mapeado: no se tocan (decisión 5 de la cabecera).
        for dni, emp in propios.items():
            if emp.IsActive and dni in dnis_del_padron and dni not in en_alcance:
                fuera_de_alcance.append({"dni": dni, "nombre": emp.FullName, "id": emp.Id})

        # --- Ceses ---------------------------------------------------------
        ceses: list[Employee] = []
        if limite > 0:
            detalle["nota_ceses"] = (
                "No se calcularon ceses: con limite>0 el padrón es parcial y la "
                "ausencia de un empleado no significa nada."
            )
        else:
            fuera = {f["dni"] for f in fuera_de_alcance}
            for dni, emp in propios.items():
                if not emp.IsActive or dni in fuera:
                    continue
                if dni not in dnis_del_padron:
                    # Desapareció del padrón: ya no está activo ni cesó este año.
                    ceses.append(emp)
                elif dni in en_alcance and not _activo_en_origen(en_alcance[dni]):
                    # Vino, pero marcado como cesado en el origen. Ya está
                    # contemplado en `actualizaciones` (IsActive pasa a False);
                    # se cuenta aparte para el freno.
                    ceses.append(emp)

        # --- Freno de ceses -------------------------------------------------
        # En dry_run NO se aborta: una previsualización debe mostrar el cuadro
        # completo -- altas, actualizaciones Y el aviso de que una corrida real
        # se detendría. Abortar aquí escondería justo la información que hace
        # falta para decidir si la cifra de ceses es legítima.
        maximo = settings.planillas_max_ceses
        freno_superado = len(ceses) > maximo
        detalle["freno_ceses"] = {
            "detectados": len(ceses),
            "maximo_permitido": maximo,
            "superado": freno_superado,
            "muestra": [{"dni": e.Dni, "nombre": e.FullName} for e in ceses[:50]],
        }

        if freno_superado and not dry_run:
            detalle["ignorados"] = ignorados
            mensaje = (
                f"Abortado: se cesarían {len(ceses)} empleados y el máximo permitido "
                f"es {maximo}. No se escribió nada. Revisa el origen; si la cifra es "
                f"correcta, sube PLANILLAS_MAX_CESES para esta corrida."
            )
            _cerrar(db, corrida, "abortado", mensaje, detalle,
                    total=len(filas), en_alcance=len(en_alcance),
                    ignorados=sum(ignorados.values()), fuera=len(fuera_de_alcance),
                    errores=len(errores))
            return _resumen(corrida, detalle)

        # --- Escritura -------------------------------------------------------
        n_altas = n_updates = 0
        if not dry_run:
            siguiente_num = (db.query(func.max(Employee.Num)).scalar() or 0) + 1

            for alta in altas:
                emp = Employee(
                    Num=siguiente_num,
                    Dni=alta["dni"],
                    DaysPerYear=settings.planillas_dias_por_anio,
                    **alta["campos"],
                )
                db.add(emp)
                siguiente_num += 1
                n_altas += 1

            for emp, cambios in actualizaciones:
                for campo, valor in cambios.items():
                    setattr(emp, campo, valor)
                n_updates += 1

            for emp in ceses:
                emp.IsActive = False
                if not emp.CeaseDate:
                    emp.CeaseDate = date.today()

            db.commit()
        else:
            n_altas = len(altas)
            n_updates = len(actualizaciones)

        detalle.update({
            "ignorados": ignorados,
            "fuera_de_alcance": fuera_de_alcance[:50],
            "posibles_duplicados": posibles_duplicados[:100],
            "omitidos_cesados": {
                "total": len(omitidos_cesados),
                "muestra": omitidos_cesados[:50],
            },
            "errores": errores,
        })
        if dry_run:
            detalle["altas_previstas"] = [a["nombre"] for a in altas[:50]]

        avisos = []
        if dry_run and freno_superado:
            avisos.append(
                f"Una corrida real ABORTARÍA: se cesarían {len(ceses)} empleados "
                f"y el máximo permitido es {maximo}."
            )
        if posibles_duplicados:
            avisos.append(
                f"{len(posibles_duplicados)} de las altas coinciden por nombre con "
                f"empleados que YA existen: probable diferencia de formato en el "
                f"documento. Si se ejecuta así, se crearán duplicados."
            )
        aviso = " | ".join(avisos) or None

        _cerrar(db, corrida, "ok", aviso, detalle,
                total=len(filas), en_alcance=len(en_alcance),
                altas=n_altas, actualizaciones=n_updates, ceses=len(ceses),
                reactivaciones=reactivaciones, ignorados=sum(ignorados.values()),
                fuera=len(fuera_de_alcance), errores=len(errores))
        return _resumen(corrida, detalle)

    except SyncError as exc:
        db.rollback()
        _cerrar(db, corrida, "error", str(exc), detalle)
        return _resumen(corrida, detalle)
    except Exception as exc:  # noqa: BLE001 - la corrida nunca debe tumbar el scheduler
        db.rollback()
        _cerrar(db, corrida, "error", f"Error inesperado: {exc}", detalle)
        return _resumen(corrida, detalle)


def _cerrar(db: Session, corrida: SyncCorrida, estado: str, mensaje: Optional[str],
            detalle: dict, total: int = 0, en_alcance: int = 0, altas: int = 0,
            actualizaciones: int = 0, ceses: int = 0, reactivaciones: int = 0,
            ignorados: int = 0, fuera: int = 0, errores: int = 0) -> None:
    corrida.Fin = datetime.now()
    corrida.Estado = estado
    corrida.Mensaje = (mensaje or "")[:1000] or None
    corrida.TotalOrigen = total
    corrida.EnAlcance = en_alcance
    corrida.Altas = altas
    corrida.Actualizaciones = actualizaciones
    corrida.Ceses = ceses
    corrida.Reactivaciones = reactivaciones
    corrida.Ignorados = ignorados
    corrida.FueraDeAlcance = fuera
    corrida.Errores = errores
    corrida.Detalle = json.dumps(detalle, ensure_ascii=False, default=str)[:100000]
    db.commit()


def _resumen(corrida: SyncCorrida, detalle: dict) -> dict:
    return {
        "corrida_id": corrida.Id,
        "estado": corrida.Estado,
        "mensaje": corrida.Mensaje,
        "dry_run": bool(corrida.DryRun),
        "cod_empresa": corrida.CodEmpresa,
        "limite": corrida.Limite,
        "total_origen": corrida.TotalOrigen,
        "en_alcance": corrida.EnAlcance,
        "altas": corrida.Altas,
        "actualizaciones": corrida.Actualizaciones,
        "ceses": corrida.Ceses,
        "reactivaciones": corrida.Reactivaciones,
        "ignorados": corrida.Ignorados,
        "fuera_de_alcance": corrida.FueraDeAlcance,
        "errores": corrida.Errores,
        "detalle": detalle,
    }


def estado_integracion(db: Session) -> dict:
    """Semáforo de la integración, para un indicador en el frontend.

    verde  = última corrida ok y reciente
    ámbar  = sin corridas recientes (el scheduler no está corriendo)
    rojo   = la última corrida abortó o falló
    """
    ultima = db.query(SyncCorrida).order_by(SyncCorrida.Inicio.desc()).first()
    if not ultima:
        return {"semaforo": "ambar", "mensaje": "Nunca se ha ejecutado una sincronización.",
                "ultima": None}

    minutos = (datetime.now() - ultima.Inicio).total_seconds() / 60
    umbral = settings.planillas_intervalo_min * 2

    if ultima.Estado in ("abortado", "error"):
        semaforo = "rojo"
    elif minutos > umbral:
        semaforo = "ambar"
    else:
        semaforo = "verde"

    return {
        "semaforo": semaforo,
        "minutos_desde_ultima": round(minutos, 1),
        "umbral_minutos": umbral,
        "ultima": {
            "id": ultima.Id,
            "inicio": ultima.Inicio,
            "estado": ultima.Estado,
            "mensaje": ultima.Mensaje,
            "dry_run": bool(ultima.DryRun),
            "altas": ultima.Altas,
            "actualizaciones": ultima.Actualizaciones,
            "ceses": ultima.Ceses,
            "ignorados": ultima.Ignorados,
            "fuera_de_alcance": ultima.FueraDeAlcance,
            "errores": ultima.Errores,
        },
    }

# app/db.py — fábrica central de conexão Postgres (psycopg2) com pool
import logging
import threading

import psycopg2
import psycopg2.extensions
import psycopg2.extras
import psycopg2.pool

logger = logging.getLogger("italo.db")

_DSN: str | None = None
_POOL: psycopg2.pool.ThreadedConnectionPool | None = None
_POOL_LOCK = threading.RLock()
_SLOTS: threading.Semaphore | None = None
_POOL_MAX: int = 0

# Espera máxima (segundos) por um slot livre no pool antes de desistir. Sem
# isso, um vazamento de conexão travaria o request para sempre em vez de
# estourar um erro visível no log.
_ACQUIRE_TIMEOUT = 30.0



class PooledConnection:
    """Proxy sobre a conexão psycopg2 cujo `.close()` DEVOLVE ao pool.

    Existe para que os ~40 call sites espalhados pelos `*_store.py` /
    `*_log.py` continuem escrevendo `conn.close()` no `finally` sem saber que
    agora existe um pool por baixo. Qualquer outro atributo é delegado à
    conexão real.
    """

    def __init__(self, conn, pool, slots):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_slots", slots)
        object.__setattr__(self, "_returned", False)

    # ── delegação ────────────────────────────────────────────────────────────
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def __enter__(self):
        # `with conn:` no psycopg2 é escopo de TRANSAÇÃO, não de conexão —
        # mantemos essa semântica (commit/rollback no exit, sem devolver ao
        # pool), igual ao comportamento pré-pool.
        object.__getattribute__(self, "_conn").__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return object.__getattribute__(self, "_conn").__exit__(exc_type, exc, tb)

    # ── devolução ────────────────────────────────────────────────────────────
    def close(self) -> None:
        if object.__getattribute__(self, "_returned"):
            return
        object.__setattr__(self, "_returned", True)

        conn = object.__getattribute__(self, "_conn")
        pool = object.__getattribute__(self, "_pool")
        slots = object.__getattribute__(self, "_slots")
        broken = False
        try:
            # Uma conexão devolvida com transação aberta ficaria "idle in
            # transaction" e seguraria locks para o próximo tomador. Desfaz.
            if not conn.closed and conn.get_transaction_status() != (
                psycopg2.extensions.TRANSACTION_STATUS_IDLE
            ):
                conn.rollback()
        except Exception as exc:  # conexão morta — não volta para o pool
            logger.warning("rollback ao devolver conexão falhou: %s", exc)
            broken = True
        try:
            pool.putconn(conn, close=broken or bool(conn.closed))
        except psycopg2.pool.PoolError as exc:
            # pool já fechado (troca de DSN / shutdown) — a conexão morre junto
            logger.debug("putconn em pool fechado: %s", exc)
        finally:
            slots.release()


def init(dsn: str, minconn: int | None = None, maxconn: int | None = None) -> None:
    """(Re)inicializa o pool de conexões.

    Idempotente: chamado de novo com o MESMO DSN (a fixture autouse dos testes
    faz isso a cada teste) mantém o pool existente. Com DSN diferente, fecha o
    anterior antes de criar o novo — nada de pool órfão segurando conexões.
    """
    global _DSN, _POOL, _SLOTS, _POOL_MAX

    resize_requested = maxconn is not None
    if minconn is None or maxconn is None:
        cfg_min, cfg_max = _pool_size_from_config()
        minconn = cfg_min if minconn is None else minconn
        maxconn = cfg_max if maxconn is None else maxconn
    minconn = max(0, int(minconn))
    maxconn = max(1, int(maxconn), minconn)

    with _POOL_LOCK:
        if _POOL is not None and not _POOL.closed and dsn == _DSN:
            # Mesmo DSN → mantém o pool. Só um pedido EXPLÍCITO de tamanho
            # diferente justifica derrubar conexões vivas.
            if not resize_requested or maxconn == _POOL_MAX:
                return

        if _POOL is not None:
            try:
                _POOL.closeall()
            except Exception as exc:
                logger.warning("falha ao fechar o pool anterior: %s", exc)

        _DSN = dsn
        _POOL = psycopg2.pool.ThreadedConnectionPool(minconn, maxconn, dsn)
        _POOL_MAX = maxconn
        _SLOTS = threading.Semaphore(maxconn)


def _pool_size_from_config() -> tuple[int, int]:
    """Tamanho do pool via `app.config` (DB_POOL_MIN / DB_POOL_MAX)."""
    try:
        import app.config as cfg

        return int(getattr(cfg, "DB_POOL_MIN", 1)), int(getattr(cfg, "DB_POOL_MAX", 10))
    except Exception:
        return 1, 10


def _reset_for_tests() -> None:
    """Derruba o pool e volta ao estado 'não inicializado'."""
    global _DSN, _POOL, _SLOTS, _POOL_MAX
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                _POOL.closeall()
            except Exception:
                # Teardown de teste: pool já fechado/inválido não importa — o objetivo aqui é só zerar o estado.
                pass
        _DSN = None
        _POOL = None
        _SLOTS = None
        _POOL_MAX = 0


def _checkout():
    with _POOL_LOCK:
        pool, slots = _POOL, _SLOTS
    if pool is None or slots is None or pool.closed:
        raise RuntimeError("app.db não foi inicializado — chame db.init(dsn) no startup")

    if not slots.acquire(timeout=_ACQUIRE_TIMEOUT):
        raise RuntimeError(
            f"pool Postgres esgotado ({_POOL_MAX} conexões) — nenhuma liberada "
            f"em {_ACQUIRE_TIMEOUT:.0f}s; provável conexão não fechada"
        )
    try:
        conn = pool.getconn()
    except Exception:
        slots.release()
        raise
    return PooledConnection(conn, pool, slots)


def get_conn():
    """Conexão do pool com cursor padrão (tuplas). Caller deve chamar conn.close()."""
    conn = _checkout()
    # Reset explícito: a conexão pode voltar do pool marcada com o
    # RealDictCursor de um `get_dict_conn()` anterior.
    conn.cursor_factory = None
    return conn


def get_dict_conn():
    """Conexão do pool com RealDictCursor — rows se comportam como dict."""
    conn = _checkout()
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn

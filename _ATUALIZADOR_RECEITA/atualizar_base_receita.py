"""
ProspectaX - Atualizador da base publica CNPJ/RFB

Baixa a base mais recente da Receita Federal, converte para o formato
dados_parquet usado pelo ProspectaX e gera prospectax_index.duckdb.

Fonte oficial:
  https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin
from urllib.request import Request, urlopen


OFFICIAL_BASE_URL = "https://arquivos.receitafederal.gov.br/public.php/dav/files/YggdBLfdninEJX9/"
OFFICIAL_BASE_URLS = [
    OFFICIAL_BASE_URL,
    "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/",
    "https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/",
    "https://dadosabertos.rfb.gov.br/CNPJ/",
]
CHUNK_SIZE = 200_000
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ProspectaX-Atualizador/1.0"

EST_COLS = ["cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "nome_fantasia", "situacao_cadastral", "data_situacao_cadastral", "motivo_situacao_cadastral", "nome_cidade_exterior", "pais", "data_inicio_atividade", "cnae_principal", "cnae_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento", "bairro", "cep", "uf", "municipio", "ddd_1", "telefone_1", "ddd_2", "telefone_2", "ddd_fax", "fax", "correio_eletronico", "situacao_especial", "data_situacao_especial"]
EMP_COLS = ["cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel", "capital_social", "porte_empresa", "ente_federativo_responsavel"]
SOC_COLS = ["cnpj_basico", "identificador_socio", "nome_socio", "cpf_cnpj_socio", "qualificacao_socio", "data_entrada_sociedade", "pais", "representante_legal", "nome_representante", "qualificacao_representante", "faixa_etaria"]
SIMPLES_COLS = ["cnpj_basico", "opcao_simples", "data_opcao_simples", "data_exclusao_simples", "opcao_mei", "data_opcao_mei", "data_exclusao_mei"]
MUNI_COLS = ["codigo", "descricao"]
CNAE_COLS = ["codigo", "descricao"]
MOTI_COLS = ["codigo", "descricao"]
NATJ_COLS = ["codigo", "descricao"]
PAIS_COLS = ["codigo", "descricao"]
QUAL_COLS = ["codigo", "descricao"]


@dataclass(frozen=True)
class Group:
    label: str
    file_prefixes: tuple[str, ...]
    cols: tuple[str, ...]
    output_prefix: str = ""
    output_file: str = ""
    required: bool = False
    keep_download: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'cols', tuple(self.cols))


GROUPS = [
    Group("Estabelecimentos", ("estabelecimentos",), EST_COLS, output_prefix="estabelecimentos_part", required=True),
    Group("Empresas", ("empresas",), EMP_COLS, output_prefix="empresas_part", required=True),
    Group("Socios", ("socios",), SOC_COLS, output_prefix="socios_part", required=False),
    Group("Municipios", ("municipios",), MUNI_COLS, output_file="municipios.parquet", required=True),
    Group("CNAEs", ("cnaes",), CNAE_COLS, output_file="cnaes.parquet", required=False),
    Group("Motivos", ("motivos",), MOTI_COLS, output_file="motivos.parquet", required=False),
    Group("Naturezas", ("naturezas", "naturezas_juridicas"), NATJ_COLS, output_file="naturezas_juridicas.parquet", required=False),
    Group("Paises", ("paises",), PAIS_COLS, output_file="paises.parquet", required=False),
    Group("Qualificacoes", ("qualificacoes",), QUAL_COLS, output_file="qualificacoes.parquet", required=False),
]


def log(msg: str = "") -> None:
    print(msg, flush=True)


def fmt_bytes(value: int | float) -> str:
    n = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_num(value: int | float) -> str:
    return f"{int(value):,}".replace(",", ".")


def ensure_package(import_name: str, pip_name: str | None = None):
    try:
        return __import__(import_name)
    except Exception:
        pkg = pip_name or import_name
        log(f"Instalando dependencia: {pkg}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return __import__(import_name)


def import_deps():
    pd = ensure_package("pandas")
    pa = ensure_package("pyarrow")
    pq = __import__("pyarrow.parquet", fromlist=["parquet"])
    duckdb = ensure_package("duckdb")
    return pd, pa, pq, duckdb


def read_url(url: str, timeout: int = 60) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_hrefs(html: str) -> list[str]:
    return [unquote(x) for x in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)]


_REMOTE_SIZES = {}

def read_dav_listing(url: str) -> list[dict]:
    """Lista somente o compartilhamento publico indicado pelo catalogo da RFB.

    PROPFIND/Depth 1 e leitura: nao cria nem modifica arquivos remotos.
    https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj
    """
    body = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/></d:prop></d:propfind>'
    request = Request(url, data=body, method='PROPFIND', headers={
        'Depth': '1', 'Content-Type': 'application/xml', 'User-Agent': USER_AGENT})
    with urlopen(request, timeout=40) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise RuntimeError('Listagem oficial inesperadamente grande.')
    entries = []
    prefix = url.rstrip('/') + '/'
    for response in ET.fromstring(raw).findall('{DAV:}response'):
        href = response.findtext('{DAV:}href') or ''
        item_url = urljoin(url, href)
        if not item_url.startswith(prefix) or item_url.rstrip('/') == url.rstrip('/'):
            continue
        name = unquote(item_url.rstrip('/').rsplit('/', 1)[-1])
        if not name or '/' in name or '\\' in name or name in ('.', '..'):
            continue
        for propstat in response.findall('{DAV:}propstat'):
            if ' 200 ' not in (propstat.findtext('{DAV:}status') or ''):
                continue
            prop = propstat.find('{DAV:}prop')
            if prop is None:
                continue
            size = int(prop.findtext('{DAV:}getcontentlength') or 0)
            folder = prop.find('{DAV:}resourcetype/{DAV:}collection') is not None
            entries.append({'name': name, 'url': item_url, 'folder': folder, 'size': size})
            if not folder:
                _REMOTE_SIZES[item_url] = size
            break
    if not entries:
        raise RuntimeError('O repositorio publico nao retornou arquivos ou pastas.')
    return entries


def discover_latest_month(base_url: str) -> tuple[str, str]:
    if '/public.php/dav/files/' in base_url:
        months = [item for item in read_dav_listing(base_url) if item['folder'] and re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', item['name'])]
        if not months:
            raise RuntimeError('Nao encontrei competencias no repositorio oficial.')
        item = max(months, key=lambda value: value['name'])
        return item['name'], item['url'].rstrip('/') + '/'
    html = read_url(base_url).decode("utf-8", errors="ignore")
    months = []
    for href in parse_hrefs(html):
        clean = href.strip().strip("/")
        if re.fullmatch(r"\d{4}-\d{2}", clean):
            months.append(clean)
    if not months:
        zips = [href for href in parse_hrefs(html) if Path(href).name.lower().endswith(".zip")]
        if zips:
            return "raiz", base_url.rstrip("/") + "/"
        raise RuntimeError(f"Nao encontrei pastas YYYY-MM nem ZIPs em {base_url}")
    month = sorted(set(months))[-1]
    return month, urljoin(base_url, month + "/")


def discover_source(base_urls: Iterable[str], requested_month: str = "") -> tuple[str, str, str]:
    errors = []
    for base_url in base_urls:
        try:
            if requested_month:
                month_url = urljoin(base_url.rstrip("/") + "/", requested_month + "/")
                choose_manifest(list_zip_files(month_url))
                return requested_month, month_url, base_url
            month, month_url = discover_latest_month(base_url)
            choose_manifest(list_zip_files(month_url))
            return month, month_url, base_url
        except Exception as exc:
            errors.append(f"{base_url} -> {exc}")
    raise RuntimeError("Nenhuma fonte respondeu corretamente:\n" + "\n".join(errors))


def list_zip_files(month_url: str) -> dict[str, str]:
    if '/public.php/dav/files/' in month_url:
        files = {item['name']: item['url'] for item in read_dav_listing(month_url)
                 if not item['folder'] and item['name'].lower().endswith('.zip') and item['size'] > 0}
        if not files:
            raise RuntimeError('Nenhum ZIP completo na competencia selecionada.')
        return dict(sorted(files.items(), key=lambda kv: kv[0].lower()))
    html = read_url(month_url).decode("utf-8", errors="ignore")
    out = {}
    for href in parse_hrefs(html):
        name = Path(href).name
        if name.lower().endswith(".zip"):
            out[name] = urljoin(month_url, href)
    if not out:
        raise RuntimeError(f"Nenhum ZIP encontrado em {month_url}")
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def remote_size(url: str) -> int:
    if url in _REMOTE_SIZES:
        return _REMOTE_SIZES[url]
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        with urlopen(req, timeout=30) as resp:
            raw = resp.headers.get("Content-Length") or ""
        return int(raw) if raw.isdigit() else 0
    except Exception:
        return 0


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = remote_size(url)
    if dest.is_file() and expected and dest.stat().st_size == expected and zipfile.is_zipfile(dest):
        log(f"      Download ja existe: {dest.name} ({fmt_bytes(dest.stat().st_size)})")
        return dest

    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        if tmp.is_dir():
            shutil.rmtree(tmp)
        else:
            tmp.unlink()
    log(f"      Baixando {dest.name}")
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    downloaded = 0
    last_print = 0.0
    with urlopen(req, timeout=120) as resp, tmp.open("wb") as f:
        total = expected or int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_print > 2:
                if total:
                    pct = downloaded * 100 / total
                    log(f"        {pct:5.1f}%  {fmt_bytes(downloaded)} / {fmt_bytes(total)}")
                else:
                    log(f"        {fmt_bytes(downloaded)}")
                last_print = now
    if expected and downloaded != expected:
        raise RuntimeError(f'Download incompleto de {dest.name}: {downloaded} de {expected} bytes. A base anterior foi preservada.')
    if not zipfile.is_zipfile(tmp):
        raise RuntimeError(f'O servidor nao retornou um ZIP valido: {dest.name}')
    tmp.replace(dest)
    log(f"      OK: {dest.name} ({fmt_bytes(dest.stat().st_size)})")
    return dest


def file_matches_group(name: str, group: Group) -> bool:
    low = name.lower()
    return any(low.startswith(prefix.lower()) for prefix in group.file_prefixes)


def choose_manifest(all_zips: dict[str, str], include_simples: bool = False) -> dict[Group, list[tuple[str, str]]]:
    groups = list(GROUPS)
    if include_simples:
        groups.append(Group("Simples Nacional", ("simples",), SIMPLES_COLS, output_file="simples.parquet", required=False))
    manifest: dict[Group, list[tuple[str, str]]] = {}
    for group in groups:
        files = [(name, url) for name, url in all_zips.items() if file_matches_group(name, group)]
        files.sort(key=lambda x: x[0].lower())
        if group.required and not files:
            raise RuntimeError(f"Arquivo obrigatorio nao encontrado na Receita: {group.label}")
        if files and group.label in ('Empresas', 'Estabelecimentos', 'Socios'):
            expected = {group.file_prefixes[0] + str(i) + '.zip' for i in range(10)}
            found = {name.lower() for name, _ in files}
            if found != expected:
                raise RuntimeError(f'Competencia incompleta ou formato alterado em {group.label}: esperados os blocos 0 a 9. Verifique a fonte oficial antes de continuar.')
        manifest[group] = files
    return manifest


def first_zip_member(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist() if not n.endswith("/") and not Path(n).name.startswith(".")]
    if not names:
        raise RuntimeError("ZIP vazio")
    return names[0]


def convert_zip_to_parquet(zip_path: Path, output_path: Path, cols: list[str], pd, pa, pq) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    writer = None
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            member = first_zip_member(zf)
            with zf.open(member) as raw:
                reader = pd.read_csv(
                    raw,
                    sep=";",
                    header=None,
                    names=cols,
                    dtype=str,
                    encoding="latin-1",
                    on_bad_lines="error",
                    keep_default_na=False,
                    chunksize=CHUNK_SIZE,
                    low_memory=False,
                )
                for chunk in reader:
                    chunk = chunk.fillna("")
                    table = pa.Table.from_pandas(chunk, preserve_index=False)
                    if writer is None:
                        writer = pq.ParquetWriter(output_path, table.schema, compression="zstd")
                    writer.write_table(table)
                    total += len(chunk)
    finally:
        if writer is not None:
            writer.close()
    return total


def convert_manifest(manifest: dict[Group, list[tuple[str, str]]], downloads_dir: Path, output_dir: Path, keep_zips: bool, pd, pa, pq) -> dict[str, int]:
    summary: dict[str, int] = {}
    for group, files in manifest.items():
        if not files:
            log(f"[CONVERTER] {group.label}: nao encontrado, pulando.")
            summary[group.label] = 0
            continue
        log()
        log(f"[CONVERTER] {group.label}: {len(files)} arquivo(s)")
        group_total = 0
        for idx, (name, url) in enumerate(files, 1):
            zip_path = download_file(url, downloads_dir / name)
            if group.output_prefix:
                out_path = output_dir / f"{group.output_prefix}_{idx:02d}.parquet"
            else:
                out_path = output_dir / group.output_file
            t0 = time.time()
            log(f"      Convertendo para {out_path.name}")
            rows = convert_zip_to_parquet(zip_path, out_path, group.cols, pd, pa, pq)
            group_total += rows
            log(f"      OK: {fmt_num(rows)} registros, {fmt_bytes(out_path.stat().st_size)} em {time.time() - t0:.0f}s")
            if not keep_zips and not group.keep_download:
                try:
                    zip_path.unlink()
                except Exception:
                    pass
        summary[group.label] = group_total
    return summary


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def parquet_pattern(data_dir: Path, prefix: str, single_name: str) -> str:
    parts = sorted(data_dir.glob(f"{prefix}_*.parquet"))
    return sql_path(data_dir / f"{prefix}_*.parquet") if parts else sql_path(data_dir / single_name)


def duck_scalar(conn, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0] if row else 0)


def generate_fast_index(data_dir: Path, duckdb, threads: int, memory_limit: str) -> Path:
    output = data_dir / "prospectax_index.duckdb"
    tmp = data_dir / "prospectax_index.duckdb.tmp"
    if tmp.exists():
        if tmp.is_dir():
            shutil.rmtree(tmp)
        else:
            tmp.unlink()

    est_pattern = parquet_pattern(data_dir, "estabelecimentos_part", "estabelecimentos.parquet")
    emp_pattern = parquet_pattern(data_dir, "empresas_part", "empresas.parquet")
    muni_path = data_dir / "municipios.parquet"
    has_muni = muni_path.exists()
    muni_cte = f", muni AS (SELECT * FROM read_parquet('{sql_path(muni_path)}'))" if has_muni else ""
    muni_join = "LEFT JOIN muni m ON trim(e.municipio) = trim(m.codigo)" if has_muni else ""
    cidade_expr = "coalesce(nullif(trim(m.descricao), ''), trim(e.municipio), '')" if has_muni else "trim(e.municipio)"

    log()
    log("[INDICE] Gerando prospectax_index.duckdb")
    conn = duckdb.connect(str(tmp))
    try:
        conn.execute(f"PRAGMA threads={int(threads)}")
        conn.execute("PRAGMA enable_progress_bar=false")
        clean_memory_limit = str(memory_limit).replace("'", "")
        conn.execute(f"PRAGMA memory_limit='{clean_memory_limit}'")

        t0 = time.time()
        log("      [1/5] Criando prospects_idx...")
        conn.execute(f"""
            CREATE TABLE prospects_idx AS
            WITH est AS (SELECT * FROM read_parquet('{est_pattern}')),
                 emp AS (SELECT * FROM read_parquet('{emp_pattern}'))
                 {muni_cte}
            SELECT
                trim(e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) AS cnpj,
                coalesce(nullif(trim(e.nome_fantasia), ''), nullif(trim(emp.razao_social), ''), '-') AS nome,
                CASE trim(e.situacao_cadastral)
                    WHEN '01' THEN 'NULA'
                    WHEN '02' THEN 'ATIVA'
                    WHEN '03' THEN 'SUSPENSA'
                    WHEN '04' THEN 'INAPTA'
                    WHEN '08' THEN 'BAIXADA'
                    ELSE trim(e.situacao_cadastral)
                END AS situacao,
                regexp_replace(e.cnae_principal, '[^0-9]', '', 'g') AS cnae,
                trim(upper(e.uf)) AS uf,
                {cidade_expr} AS cidade,
                trim(e.municipio) AS municipio_cod,
                regexp_replace(coalesce(e.cep, ''), '[^0-9]', '', 'g') AS cep,
                CASE
                    WHEN nullif(trim(e.ddd_1), '') IS NOT NULL AND nullif(trim(e.telefone_1), '') IS NOT NULL
                        THEN '(' || trim(e.ddd_1) || ') ' || trim(e.telefone_1)
                    WHEN nullif(trim(e.telefone_1), '') IS NOT NULL THEN trim(e.telefone_1)
                    ELSE ''
                END AS telefone,
                lower(trim(e.correio_eletronico)) AS email,
                trim(coalesce(e.tipo_logradouro, '') || ' ' || coalesce(e.logradouro, '') || ', ' || coalesce(e.numero, '')) AS end,
                trim(e.data_inicio_atividade) AS abertura,
                trim(e.bairro) AS bairro,
                trim(e.matriz_filial) AS matriz_filial,
                trim(coalesce(e.ddd_2, '') || coalesce(e.telefone_2, '')) AS telefone_2,
                trim(emp.natureza_juridica) AS natureza_juridica,
                trim(emp.porte_empresa) AS porte_empresa,
                CASE WHEN trim(emp.natureza_juridica) IN ('2135') THEN 'SIM' ELSE 'NAO' END AS mei
            FROM est e
            LEFT JOIN emp ON trim(e.cnpj_basico) = trim(emp.cnpj_basico)
            {muni_join}
        """)
        log(f"            {fmt_num(duck_scalar(conn, 'SELECT count(*) FROM prospects_idx'))} registros")

        log("      [2/5] Criando cnae_empresa_idx...")
        conn.execute(f"""
            CREATE TABLE cnae_empresa_idx AS
            WITH est AS (
                SELECT
                    trim(cnpj_basico || cnpj_ordem || cnpj_dv) AS cnpj,
                    regexp_replace(cnae_principal, '[^0-9]', '', 'g') AS cnae_principal,
                    coalesce(cnae_secundaria, '') AS cnae_secundaria
                FROM read_parquet('{est_pattern}')
            ),
            idx AS (
                SELECT cnpj, cnae_principal AS cnae
                FROM est
                WHERE cnae_principal <> ''
                UNION ALL
                SELECT cnpj, unnest(regexp_extract_all(cnae_secundaria, '[0-9]{{7}}')) AS cnae
                FROM est
                WHERE cnae_secundaria <> ''
            )
            SELECT cnpj, cnae
            FROM idx
            WHERE cnae <> ''
        """)
        log(f"            {fmt_num(duck_scalar(conn, 'SELECT count(*) FROM cnae_empresa_idx'))} registros")

        log("      [3/5] Ordenando tabelas...")
        conn.execute("CREATE TABLE prospects_idx_sorted AS SELECT * FROM prospects_idx ORDER BY cnae, uf, situacao, cidade, nome")
        conn.execute("DROP TABLE prospects_idx")
        conn.execute("ALTER TABLE prospects_idx_sorted RENAME TO prospects_idx")
        conn.execute("CREATE TABLE cnae_empresa_idx_sorted AS SELECT * FROM cnae_empresa_idx ORDER BY cnae, cnpj")
        conn.execute("DROP TABLE cnae_empresa_idx")
        conn.execute("ALTER TABLE cnae_empresa_idx_sorted RENAME TO cnae_empresa_idx")

        log("      [4/5] Criando indices auxiliares...")
        for sql in [
            "CREATE INDEX idx_prospects_cnae ON prospects_idx(cnae)",
            "CREATE INDEX idx_prospects_uf ON prospects_idx(uf)",
            "CREATE INDEX idx_prospects_situacao ON prospects_idx(situacao)",
            "CREATE INDEX idx_prospects_cnpj ON prospects_idx(cnpj)",
            "CREATE INDEX idx_prospects_cep ON prospects_idx(cep)",
            "CREATE INDEX idx_cnae_empresa_cnae ON cnae_empresa_idx(cnae)",
            "CREATE INDEX idx_cnae_empresa_cnpj ON cnae_empresa_idx(cnpj)",
        ]:
            try:
                conn.execute(sql)
            except Exception as exc:
                log(f"            Aviso: {exc}")

        log("      [5/5] Gravando banco...")
        conn.execute("CHECKPOINT")
        log(f"      OK em {(time.time() - t0) / 60:.1f} min")
    finally:
        conn.close()

    tmp.replace(output)
    return output


def validate_output(data_dir: Path, duckdb) -> None:
    required = [
        data_dir / "municipios.parquet",
        data_dir / "prospectax_index.duckdb",
    ]
    for prefix, single_name in [("empresas_part", "empresas.parquet"), ("estabelecimentos_part", "estabelecimentos.parquet")]:
        parts = sorted(data_dir.glob(prefix + "_*.parquet"))
        required += parts[:1] or [data_dir / single_name]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError("Saida incompleta: " + "; ".join(missing))
    conn = duckdb.connect(str(data_dir / "prospectax_index.duckdb"), read_only=True)
    try:
        prospects = duck_scalar(conn, "SELECT count(*) FROM prospects_idx")
        cnaes = duck_scalar(conn, "SELECT count(*) FROM cnae_empresa_idx")
        est_pattern = parquet_pattern(data_dir, "estabelecimentos_part", "estabelecimentos.parquet")
        establishments = duck_scalar(conn, f"SELECT count(*) FROM read_parquet('{est_pattern}')")
    finally:
        conn.close()
    if prospects <= 0 or cnaes <= 0:
        raise RuntimeError("Indice rapido gerado sem registros.")
    if prospects != establishments:
        raise RuntimeError(f"Indice inconsistente: {prospects} linhas para {establishments} estabelecimentos. A base anterior foi preservada.")
    log(f"[VALIDACAO] prospects_idx={fmt_num(prospects)} | cnae_empresa_idx={fmt_num(cnaes)}")


def replace_data_dir(current_data_dir: Path, new_data_dir: Path, backup_root: Path, yes: bool) -> Path:
    if not current_data_dir.exists():
        new_data_dir.replace(current_data_dir)
        return current_data_dir

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / f"dados_parquet_BACKUP_{stamp}"
    log()
    log("[TROCA] Tudo pronto para substituir a base atual.")
    log(f"        Atual : {current_data_dir}")
    log(f"        Nova  : {new_data_dir}")
    log(f"        Backup: {backup_dir}")
    if not yes:
        ans = input("Digite SIM para trocar a base agora: ").strip().upper()
        if ans != "SIM":
            raise RuntimeError("Troca cancelada pelo usuario. A base nova ficou preservada em: " + str(new_data_dir))

    current_data_dir.replace(backup_dir)
    try:
        new_data_dir.replace(current_data_dir)
    except Exception:
        if not current_data_dir.exists() and backup_dir.exists():
            backup_dir.replace(current_data_dir)
        raise
    return backup_dir


def write_manifest(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atualiza a base local do ProspectaX com dados publicos da Receita Federal.")
    parser.add_argument("--base-url", default="", help="URL base da Receita. Vazio = tenta as fontes conhecidas.")
    parser.add_argument("--month", default="", help="Mes da Receita no formato YYYY-MM. Vazio = ultimo disponivel.")
    parser.add_argument("--data-dir", default="dados_parquet")
    parser.add_argument("--work-dir", default="_atualizacao_receita")
    parser.add_argument("--replace", action="store_true", help="Troca dados_parquet pela base nova ao final.")
    parser.add_argument("--yes", action="store_true", help="Nao perguntar confirmacao na troca final.")
    parser.add_argument("--keep-zips", action="store_true", help="Nao apagar ZIPs depois de converter.")
    parser.add_argument("--include-simples", action="store_true", help="Tambem baixa/converte Simples Nacional.")
    parser.add_argument("--dry-run", action="store_true", help="So lista o mes e os ZIPs encontrados, sem baixar.")
    parser.add_argument("--threads", type=int, default=max(1, min(4, os.cpu_count() or 4)))
    parser.add_argument("--memory-limit", default="6GB")
    args = parser.parse_args(argv)
    if args.month and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args.month.strip()):
        parser.error("--month precisa estar no formato YYYY-MM, com mes de 01 a 12.")

    root = Path(__file__).resolve().parent.parent
    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = root / data_dir
    data_dir = data_dir.resolve()
    work_dir = Path(args.work_dir)
    if not work_dir.is_absolute():
        work_dir = root / work_dir
    work_dir = work_dir.resolve()

    started = time.time()
    log("=" * 70)
    log("ProspectaX - Atualizador da base publica CNPJ/RFB")
    log("=" * 70)
    log(f"Pasta do sistema : {root}")
    log(f"Base atual       : {data_dir}")
    log(f"Pasta trabalho   : {work_dir}")
    base_urls = [args.base_url] if args.base_url else OFFICIAL_BASE_URLS
    log("Fontes oficiais  :")
    for u in base_urls:
        log(f"  - {u}")

    month = args.month.strip()
    month, month_url, source_url = discover_source(base_urls, requested_month=month)
    log(f"Mes selecionado  : {month}")
    log(f"URL do mes       : {month_url}")
    log(f"Fonte usada      : {source_url}")

    all_zips = list_zip_files(month_url)
    manifest = choose_manifest(all_zips, include_simples=args.include_simples)
    selected_count = sum(len(v) for v in manifest.values())
    log(f"ZIPs selecionados: {selected_count}")
    for group, files in manifest.items():
        if files:
            log(f"  - {group.label}: " + ", ".join(name for name, _ in files))
    if args.dry_run:
        known_size = sum(_REMOTE_SIZES.get(url, 0) for files in manifest.values() for _, url in files)
        if known_size:
            log(f"Tamanho compactado selecionado: {fmt_bytes(known_size)}")
        log("[DRY-RUN] Fonte e manifestos conferidos. Nada foi baixado.")
        return 0

    pd, pa, pq, duckdb = import_deps()

    run_dir = work_dir / month
    downloads_dir = run_dir / "downloads"
    new_data_dir = run_dir / "dados_parquet_novo"
    if new_data_dir.exists():
        shutil.rmtree(new_data_dir)
    new_data_dir.mkdir(parents=True, exist_ok=True)

    write_manifest(run_dir / "manifest.json", {
        "month": month,
        "month_url": month_url,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "zips": {g.label: [name for name, _ in files] for g, files in manifest.items()},
    })

    usage = shutil.disk_usage(work_dir.parent if work_dir.parent.exists() else root)
    log(f"Espaco livre     : {fmt_bytes(usage.free)}")
    # Conversao, tabelas ordenadas, indices e temporarios coexistem no pico.
    # E uma margem preventiva, nao uma garantia de consumo maximo.
    if usage.free < 60 * 1024**3:
        raise RuntimeError("Espaco livre menor que 60 GB. Para a carga nacional, reserve preferencialmente 80 GB ou mais em SSD; o consumo depende da competencia e dos temporarios.")

    summary = convert_manifest(manifest, downloads_dir, new_data_dir, args.keep_zips, pd, pa, pq)
    write_manifest(new_data_dir / "_prospectax_base_info.json", {
        "fonte": source_url,
        "mes_receita": month,
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "resumo_registros": summary,
    })

    index_path = generate_fast_index(new_data_dir, duckdb, args.threads, args.memory_limit)
    log(f"[INDICE] Arquivo final: {index_path} ({fmt_bytes(index_path.stat().st_size)})")
    validate_output(new_data_dir, duckdb)

    if args.replace:
        backup_dir = replace_data_dir(data_dir, new_data_dir, root, args.yes)
        log(f"[OK] Base atualizada. Backup anterior: {backup_dir}")
    else:
        log(f"[OK] Base nova pronta em: {new_data_dir}")
        log("     Use --replace para trocar a pasta dados_parquet automaticamente.")

    log()
    log(f"Tempo total: {(time.time() - started) / 60:.1f} minutos")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("\nCancelado pelo usuario.")
        raise SystemExit(130)
    except Exception as exc:
        log()
        log("ERRO: " + str(exc))
        raise SystemExit(1)

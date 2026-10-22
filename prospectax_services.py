"""Servicos locais de consulta. Sem dependencias novas ou chamadas ao importar."""
from prospectax_discovery import normalize_cnpj
import copy
import json
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def digits(value):
    return re.sub(r"\D", "", str(value or ""))


def flag(value):
    if isinstance(value, dict):
        value = value.get("optant", value.get("optante"))
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"s", "sim", "true", "1", "yes"}


def phone(area="", number=""):
    number, area = digits(number), digits(area)
    # BrasilAPI fornece ddd_telefone_1 contendo DDD E telefone.
    raw = (area + number) if number else area
    if len(raw) in (12, 13) and raw.startswith("55"):
        raw = raw[2:]
    if len(raw) in (10, 11):
        return f"({raw[:2]}) {raw[2:]}"
    return raw


def money(value):
    if value is None or value == "":
        return ""
    try:
        text = str(value).replace("R$", "").strip()
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        return (f"R$ {float(text):,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return ""


def _text(value):
    if isinstance(value, dict):
        value = value.get("text") or value.get("descricao") or value.get("nome") or ""
    return str(value).strip() if value is not None else ""


def normalize_company(provider, raw, cnpj):
    """Adapta cada contrato publico; nunca infere MEI por natureza juridica."""
    if not isinstance(raw, dict) or raw.get("error") or raw.get("status") == "ERROR":
        raise ValueError("Resposta sem cadastro valido")
    d = {"cnpj": cnpj, "_fonte": provider, "_consultado_em": datetime.now(timezone.utc).isoformat(),
         "_detalhes_parciais": False, "_api_combo_v1": True, "_socios_v2": True}
    if provider == "cnpja":
        co, ad = raw.get("company") or {}, raw.get("address") or {}
        tels, mails = raw.get("phones") or [], raw.get("emails") or []
        main = raw.get("mainActivity") or {}
        members = co.get("members") or raw.get("members") or []
        socios = [{"nome": _text((m.get("person") or {}).get("name") or m.get("name")),
                   "qualificacao": _text(m.get("role")), "entrada": _text(m.get("since"))} for m in members]
        activities = [{"codigo": str(a.get("id") or ""), "descricao": _text(a.get("text"))} for a in raw.get("sideActivities") or []]
        d.update(razao_social=_text(co.get("name")), nome_fantasia=_text(raw.get("alias")),
                 abertura=_text(raw.get("founded")), situacao=_text(raw.get("status")),
                 capital_social=money(co.get("equity")), porte=_text(co.get("size") or raw.get("size")),
                 natureza_juridica=_text(co.get("nature") or raw.get("nature")),
                 cnae_principal_codigo=_text(main.get("id")), cnae_principal_descricao=_text(main.get("text")),
                 telefone_1=phone(tels[0].get("area"), tels[0].get("number")) if tels else "",
                 telefone_2=phone(tels[1].get("area"), tels[1].get("number")) if len(tels) > 1 else "",
                 email=_text(mails[0].get("address")) if mails else "",
                 logradouro=" ".join(filter(None, [_text(ad.get("street")), _text(ad.get("number"))])),
                 complemento=_text(ad.get("details")), bairro=_text(ad.get("district")), cep=digits(ad.get("zip")),
                 municipio=_text(ad.get("city")), uf=_text(ad.get("state")),
                 mei=flag(co.get("simei", raw.get("simei"))), simples=flag(co.get("simples", raw.get("simples"))),
                 _atualizado_em=_text(raw.get("updated")))
    elif provider == "cnpjws":
        est = raw.get("estabelecimento") or {}
        main = est.get("atividade_principal") or {}
        socios = [{"nome": _text(m.get("nome")), "qualificacao": _text(m.get("qualificacao_socio")),
                   "entrada": _text(m.get("data_entrada"))} for m in raw.get("socios") or []]
        activities = [{"codigo": digits(a.get("id")), "descricao": _text(a.get("descricao"))} for a in est.get("atividades_secundarias") or []]
        sn = raw.get("simples") or {}
        d.update(razao_social=_text(raw.get("razao_social")), nome_fantasia=_text(est.get("nome_fantasia")),
                 abertura=_text(est.get("data_inicio_atividade")), situacao=_text(est.get("situacao_cadastral")),
                 capital_social=money(raw.get("capital_social")), porte=_text(raw.get("porte")),
                 natureza_juridica=_text(raw.get("natureza_juridica")),
                 cnae_principal_codigo=digits(main.get("id")), cnae_principal_descricao=_text(main.get("descricao")),
                 telefone_1=phone(est.get("ddd1"), est.get("telefone1")), telefone_2=phone(est.get("ddd2"), est.get("telefone2")),
                 email=_text(est.get("email")), logradouro=" ".join(filter(None, [_text(est.get(k)) for k in ("tipo_logradouro", "logradouro", "numero")])),
                 complemento=_text(est.get("complemento")), bairro=_text(est.get("bairro")), cep=digits(est.get("cep")),
                 municipio=_text(est.get("cidade")), uf=_text((est.get("estado") or {}).get("sigla")),
                 mei=flag(sn.get("mei")), simples=flag(sn.get("simples")), _atualizado_em=_text(est.get("atualizado_em")))
    elif provider == "brasilapi":
        socios = [{"nome": _text(m.get("nome_socio")), "qualificacao": _text(m.get("qualificacao_socio")),
                   "entrada": _text(m.get("data_entrada_sociedade"))} for m in raw.get("qsa") or []]
        activities = [{"codigo": digits(a.get("codigo")), "descricao": _text(a.get("descricao"))} for a in raw.get("cnaes_secundarios") or []]
        d.update(razao_social=_text(raw.get("razao_social")), nome_fantasia=_text(raw.get("nome_fantasia")),
                 abertura=_text(raw.get("data_inicio_atividade")), situacao=_text(raw.get("descricao_situacao_cadastral")),
                 capital_social=money(raw.get("capital_social")), porte=_text(raw.get("porte")),
                 natureza_juridica=_text(raw.get("natureza_juridica")), cnae_principal_codigo=digits(raw.get("cnae_fiscal")),
                 cnae_principal_descricao=_text(raw.get("cnae_fiscal_descricao")),
                 telefone_1=phone(raw.get("ddd_telefone_1"), raw.get("telefone_1")), telefone_2=phone(raw.get("ddd_telefone_2"), raw.get("telefone_2")),
                 email=_text(raw.get("email")), logradouro=" ".join(filter(None, [_text(raw.get(k)) for k in ("descricao_tipo_de_logradouro", "logradouro", "numero")])),
                 complemento=_text(raw.get("complemento")), bairro=_text(raw.get("bairro")), cep=digits(raw.get("cep")),
                 municipio=_text(raw.get("municipio")), uf=_text(raw.get("uf")),
                 mei=flag(raw.get("opcao_pelo_mei")), simples=flag(raw.get("opcao_pelo_simples")), _atualizado_em="")
    elif provider == "receitaws":
        socios = [{"nome": _text(m.get("nome")), "qualificacao": _text(m.get("qual"))} for m in raw.get("qsa") or []]
        activities = [{"codigo": digits(a.get("code")), "descricao": _text(a.get("text"))} for a in raw.get("atividades_secundarias") or []]
        main = (raw.get("atividade_principal") or [{}])[0]
        tels = str(raw.get("telefone") or "").split("/")
        d.update(razao_social=_text(raw.get("nome")), nome_fantasia=_text(raw.get("fantasia")),
                 abertura=_text(raw.get("abertura")), situacao=_text(raw.get("situacao")), capital_social=money(raw.get("capital_social")),
                 porte=_text(raw.get("porte")), natureza_juridica=_text(raw.get("natureza_juridica")),
                 cnae_principal_codigo=digits(main.get("code")), cnae_principal_descricao=_text(main.get("text")),
                 telefone_1=phone(tels[0]), telefone_2=phone(tels[1]) if len(tels) > 1 else "", email=_text(raw.get("email")),
                 logradouro=" ".join(filter(None, [_text(raw.get("logradouro")), _text(raw.get("numero"))])),
                 complemento=_text(raw.get("complemento")), bairro=_text(raw.get("bairro")), cep=digits(raw.get("cep")),
                 municipio=_text(raw.get("municipio")), uf=_text(raw.get("uf")),
                 mei=flag(raw.get("simei")), simples=flag(raw.get("simples")), _atualizado_em=_text(raw.get("ultima_atualizacao")))
    else:
        raise ValueError("Fonte desconhecida")
    if not d.get("razao_social"):
        raise ValueError("Fonte nao retornou razao social")
    d["socios"] = [s for s in socios if s.get("nome")]
    d["qsa"] = d["socios"]
    d["donos"] = ", ".join(s["nome"] for s in d["socios"])
    d["cnaes_secundarios"] = [a for a in activities if a.get("codigo") and a["codigo"] != "0"]
    return d


class FreeCompanyAPIs:
    # Conservador: nao satura servicos comunitarios; limites por processo/IP.
    PROVIDERS = (("brasilapi", "https://brasilapi.com.br/api/cnpj/v1/{}", 2.0),
                 ("cnpja", "https://open.cnpja.com/office/{}", 12.1),
                 ("cnpjws", "https://publica.cnpj.ws/cnpj/{}", 20.1),
                 ("receitaws", "https://receitaws.com.br/v1/cnpj/{}", 20.1))

    def __init__(self, opener=urlopen, clock=time.monotonic):
        self.opener, self.clock = opener, clock
        self.lock = threading.Lock()
        self.next_call, self.health = {}, {}

    def _reserve(self, name, interval):
        with self.lock:
            now = self.clock()
            if self.next_call.get(name, 0) > now:
                return False
            self.next_call[name] = now + interval
            return True

    def _record(self, name, state, detail="", cooldown=0):
        with self.lock:
            self.health[name] = {"status": state, "detalhe": detail, "verificado_em": datetime.now(timezone.utc).isoformat()}
            self.next_call[name] = max(self.next_call.get(name, 0), self.clock() + cooldown)

    def status(self):
        with self.lock:
            return [{"fonte": name, **self.health.get(name, {"status": "nao_consultada"}),
                     "aguardar_segundos": max(0, round(self.next_call.get(name, 0) - self.clock(), 1))}
                    for name, _, _ in self.PROVIDERS]

    def lookup(self, cnpj, only_provider=None):
        cnpj = normalize_cnpj(cnpj)
        if len(cnpj) != 14:
            return {"error": "CNPJ deve ter 14 digitos", "_api_errors": []}
        errors = []
        for name, endpoint, interval in self.PROVIDERS:
            if only_provider and name != only_provider:
                continue
            if not self._reserve(name, interval):
                errors.append(f"{name}: intervalo entre consultas; tente novamente em instantes")
                continue
            try:
                req = Request(endpoint.format(cnpj), headers={"User-Agent": "ProspectaX/2.0", "Accept": "application/json"})
                with self.opener(req, timeout=5) as response:
                    raw = json.loads(response.read(2_000_000).decode("utf-8-sig"))
                result = normalize_company(name, raw, cnpj)
                self._record(name, "disponivel")
                result["_api_errors"] = errors
                return result
            except HTTPError as exc:
                if exc.code == 429:
                    retry = exc.headers.get("Retry-After", "60") if exc.headers else "60"
                    try:
                        cooldown = float(retry)
                    except ValueError:
                        try:
                            cooldown = parsedate_to_datetime(retry).timestamp() - time.time()
                        except (ValueError, TypeError):
                            cooldown = 60
                    self._record(name, "limite_temporario", "Limite gratuito atingido", max(60, min(cooldown, 3600)))
                else:
                    self._record(name, "indisponivel", f"HTTP {exc.code}", 60 if exc.code >= 500 else 0)
                errors.append(f"{name}: HTTP {exc.code}")
            except (URLError, TimeoutError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                self._record(name, "indisponivel", "Falha de conexao ou resposta invalida", 30)
                errors.append(f"{name}: {type(exc).__name__}")
        return {"error": "Fontes gratuitas indisponiveis ou em intervalo de consultas. Os dados locais continuam disponiveis.", "_api_errors": errors}


class QueryCache:
    """LRU com coalescencia por chave: duas buscas iguais nao duplicam o SQL."""
    def __init__(self, limit=128):
        self.limit = limit
        self.values = OrderedDict()
        self.lock = threading.RLock()
        self.stripes = [threading.Lock() for _ in range(32)]

    def get(self, key, compute, refresh=False):
        with self.stripes[hash(key) % len(self.stripes)]:
            with self.lock:
                if not refresh and key in self.values:
                    self.values.move_to_end(key)
                    return copy.deepcopy(self.values[key]), True
            value = compute()
            with self.lock:
                self.values[key] = copy.deepcopy(value)
                self.values.move_to_end(key)
                while len(self.values) > self.limit:
                    self.values.popitem(last=False)
            return value, False

    def clear(self):
        with self.lock:
            self.values.clear()


def database_revision(path):
    stat = path.stat()
    return str(path.resolve()), stat.st_size, stat.st_mtime_ns

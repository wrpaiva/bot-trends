# src/tests/test_product_match.py
"""
Regra de casamento vídeo do TikTok → produto do catálogo do ML (TIE-18).
Falso positivo é pior que falso negativo: na dúvida, não vincula.
"""

import pytest

from src.domain.product_match import Candidato, cobertura, escolhe_candidato, tokens


def test_tokens_ignora_acento_caixa_plural_e_palavra_vazia():
    assert tokens("Potes de Condimentos em AÇO inox") == {"pote", "condimento", "aco", "inox"}


def test_tokens_mantem_numero_que_descreve_o_produto():
    # "3 em 1" e "20w" diferenciam carregadores
    assert {"3", "1", "20w"} <= tokens("Carregador 3 em 1 20W")


def test_cobertura_conta_as_palavras_da_consulta_presentes_no_nome():
    assert cobertura("kit brocas corte lateral", "Kit Brocas Corte Lateral E Escalonadas") == 1.0
    assert cobertura("kit brocas corte lateral", "Kit Brocas Escalonadas") == 0.5


def test_cobertura_de_consulta_vazia_eh_zero():
    assert cobertura("", "Qualquer coisa") == 0.0


def _c(nome: str, dominio: str | None = "MLB-DRILL_BITS", pid: str = "MLB1") -> Candidato:
    return Candidato(product_id=pid, nome=nome, domain_id=dominio)


def test_escolhe_o_candidato_de_maior_cobertura():
    escolha = escolhe_candidato(
        "kit brocas corte lateral",
        [
            _c("Kit Brocas Escalonadas", pid="MLB1"),
            _c("Kit Brocas Corte Lateral E Escalonadas 9 Peças", pid="MLB2"),
        ],
        minimo=0.8,
    )
    assert escolha is not None
    assert (escolha.candidato.product_id, escolha.score) == ("MLB2", 1.0)


def test_sem_candidato_acima_do_minimo_nao_vincula():
    # O caso real que motivou a regra: "cara milionaria" → livro
    assert escolhe_candidato("cara milionaria", [_c("Livro - A Milionária")], minimo=0.8) is None


def test_consulta_de_uma_palavra_nao_vincula():
    # "carregador" sozinho casa com qualquer carregador do catálogo
    assert escolhe_candidato("carregador", [_c("Carregador Turbo")], minimo=0.8) is None


def test_candidato_de_outro_dominio_fica_de_fora():
    escolha = escolhe_candidato(
        "porta temperos giratorio",
        [
            _c("Porta Temperos Giratório Livro de Receitas", dominio="MLB-BOOKS", pid="MLB1"),
            _c("Porta Temperos Giratório Preto", dominio="MLB-SPICE_RACKS", pid="MLB2"),
        ],
        dominio="MLB-SPICE_RACKS",
        minimo=0.8,
    )
    assert escolha is not None and escolha.candidato.product_id == "MLB2"


def test_candidato_sem_dominio_nao_eh_descartado_pelo_filtro():
    escolha = escolhe_candidato(
        "porta temperos giratorio",
        [_c("Porta Temperos Giratório", dominio=None)],
        dominio="MLB-SPICE_RACKS",
        minimo=0.8,
    )
    assert escolha is not None


@pytest.mark.parametrize("minimo", [0.0, 1.5])
def test_minimo_fora_da_faixa_eh_erro(minimo):
    with pytest.raises(ValueError):
        escolhe_candidato("kit brocas", [], minimo=minimo)


def test_candidato_de_outro_publico_fica_de_fora():
    # Caso real (2026-10-16): cinta com colchetes e barbatanas → cinta masculina
    candidatos = [
        _c("Cinta Modeladora Masculina Abdominal Dupla Compressão", pid="MLB1"),
        _c("Cinta Modeladora Dupla Compressão Colchetes", pid="MLB2"),
    ]
    escolha = escolhe_candidato("cinta modeladora dupla compressao", candidatos, minimo=0.8)
    assert escolha is not None and escolha.candidato.product_id == "MLB2"


def test_publico_so_no_candidato_sem_alternativa_nao_vincula():
    candidatos = [_c("Tênis Corrida Infantil Leve")]
    assert escolhe_candidato("tenis corrida leve", candidatos, minimo=0.8) is None


def test_publico_na_consulta_aceita_candidato_do_mesmo_publico():
    candidatos = [_c("Tênis Corrida Feminino Leve")]
    assert escolhe_candidato("tenis corrida feminino", candidatos, minimo=0.8) is not None

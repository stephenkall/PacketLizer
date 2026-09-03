# PacketLizer

Monitor discreto de estabilidade da conexao para **juntar provas de perda de
pacotes** e apresentar ao provedor (ISP). Fica na bandeja do sistema (ao lado do
relogio), sonda um alvo configuravel de forma continua, guarda tudo num SQLite
compacto e gera **relatorios sob demanda em HTML + PDF + CSV**.

## Por que existe

O `ping -t alvo > log.txt` funciona, mas o resultado e um paredao de texto.
O PacketLizer faz a mesma coisa de forma automatica e transforma os dados num
dashboard com % de perda, frequencia/horario das quedas, duracao media, MTBF,
grafico de latencia x tempo (timeout marcado na linha do timeout configurado) e
um CSV verbose com cada pacote.

## Instalacao

Precisa de Python 3.11+ no Windows. Nao precisa instalar dependencias a mao: o
programa instala sozinho a partir de `requirements.txt` no primeiro arranque.

```powershell
git clone https://github.com/stephenkall/PacketLizer.git
cd PacketLizer
pythonw main.py            # inicia em segundo plano, so o icone na bandeja
```

Se o ambiente bloquear scripts `.py`, gere um executavel unico:

```powershell
python build_exe.py        # cria dist\PacketLizer.exe
dist\PacketLizer.exe
```

## Uso

| Comando | O que faz |
|---|---|
| `pythonw main.py` | Monitor + icone na bandeja (modo normal) |
| `python main.py --monitor` | So o monitor, em primeiro plano, com logs (Ctrl+C encerra salvando) |
| `python main.py --monitor --duration 3600` | Monitor headless com prazo de execucao (encerra apos 1 h) |
| `python main.py --monitor --target 1.1.1.1 --interval 2` | Sobrescreve alvo/intervalo so nesta execucao |
| `python main.py --report --format both` | Gera relatorio HTML + PDF (+ CSV) na pasta atual |
| `python main.py --report --days 7` | Relatorio so dos ultimos 7 dias |
| `python main.py --export-csv --out saida.csv` | Exporta todas as amostras para CSV |
| `python main.py --install-autostart` | Liga o inicio automatico (registro HKCU, sem admin) |
| `python main.py --install-autostart --startup-folder` | Idem, via pasta Inicializar |
| `python main.py --uninstall-autostart` | Desliga o inicio automatico |
| `python main.py --config` | Mostra a configuracao e os caminhos efetivos |

### Janela principal

O icone fica no tray e a janela **nao aparece na barra de tarefas** (janela do
tipo *tool window*). Na primeira execucao a janela abre para voce definir o
alvo; depois disso ela **inicia escondida** — clique no icone do tray para
abri-la. A janela mostra:

* o **estado atual** (Em execucao / Instavel / QUEDA em andamento / Em pausa),
  com um indicador colorido;
* **Configuracao editavel**: campo de texto para o **alvo (dominio ou IP)**,
  intervalo entre pings, timeout por ping, nº de perdas seguidas para contar
  uma queda e dias de retencao do historico. **Salvar e aplicar** grava o
  `config.json`; se o alvo/intervalo/timeout mudou, o monitor reinicia sozinho;
* alvo, metodo de sondagem, ultima amostra, % de perda, nº de quedas e ha quanto
  tempo esta monitorando;
* **Pausar / Retomar** o monitoramento (standby);
* **Encerrar programa** (com confirmacao);
* **Gerar relatorio** informando **data inicial** e **data final** opcionais:
  sem data inicial traz desde o inicio dos dados, sem data final vai ate a
  amostra mais recente. O HTML abre automaticamente ao terminar.

Fechar a janela no `X` apenas a esconde de volta para o tray; o monitoramento
continua. Sem ambiente grafico (`tkinter` ausente), o programa cai para um menu
simples no proprio icone do tray.

> **Modo `--monitor`** e proposital: roda *headless* (sem janela e sem icone),
> so escrevendo logs no console — inclusive uma linha `[status]` a cada 15 s
> para voce ver que esta vivo. Use o modo normal (sem argumentos) para ter a
> janela e o tray.

## Metodo de sondagem

No arranque o programa decide sozinho:

* **ICMP raw** (via `icmplib`) quando o processo tem privilegio de
  administrador — timestamps mais precisos;
* **`ping` do sistema operacional** (parse da saida, funciona em qualquer
  locale) quando **nao** ha privilegio. Sem exigir nada do usuario.

Se o ICMP raw perder permissao em execucao, o monitor troca para o `ping`
automaticamente.

## Onde ficam os dados

`%LOCALAPPDATA%\PacketLizer\` (fora do repositorio):

```
config.json              parametros (alvo, intervalo, timeout, retencao, ...)
packetlizer.db           SQLite: samples(ts, rtt_ms, status) + meta
packetlizer.log          log da aplicacao
reports\                 relatorios gerados pelo menu da bandeja
```

`config.json`:

```json
{
  "target": "www.vivo.com.br",
  "interval_seconds": 1.0,
  "timeout_ms": 2000,
  "outage_min_consecutive": 3,
  "retention_days": 60,
  "db_path": "",
  "prefer_raw_icmp": true
}
```

As alteracoes feitas na janela sao gravadas neste arquivo (ao clicar em **Salvar
e aplicar** e tambem ao **Encerrar programa**) e recarregadas no proximo
arranque — nada se perde entre execucoes.

Uma **queda (outage)** e uma sequencia de `outage_min_consecutive` ou mais
perdas seguidas. `timeout_ms` e o timeout de cada ping **e** o valor de latencia
usado no grafico para marcar um pacote perdido. `retention_days` apaga o
historico mais antigo no arranque e compacta o banco (VACUUM); **`0` = retencao
ilimitada** (nada e apagado).

## O relatorio

1. **Dashboard**: % de perda, disponibilidade, nº de quedas, quedas/dia, tempo
   total fora do ar, duracao media/mediana/maxima da queda, intervalo medio
   entre quedas, MTBF, horario e dia da semana mais criticos, latencia p50/p95
   e jitter.
2. **Grafico** latencia x tempo, com pacotes perdidos plotados na linha de
   timeout (`timeout_ms`, ex.: 2000 ms) e as janelas de queda sombreadas;
   abaixo, perda % por hora de calendario.
3. **Tabelas**: cada queda, resumo diario, distribuicao por status.
4. **CSV verbose**: `timestamp_iso, timestamp_epoch, rtt_ms, status_code, status`
   para cada pacote — sempre gerado junto do HTML/PDF.

## Desenvolvimento

```powershell
pip install -r requirements.txt pytest
pytest -q
```

CI (GitHub Actions): testes em Python 3.11/3.12 (Ubuntu) e build do
`PacketLizer.exe` (Windows) publicado como artefato a cada push na `main`.

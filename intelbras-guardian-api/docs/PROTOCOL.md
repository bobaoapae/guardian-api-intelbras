# ISECNet Protocol Documentation

Este documento descreve o protocolo ISECNet utilizado pelas centrais de alarme Intelbras para comunicação via nuvem e receptor IP. A documentação foi criada através de engenharia reversa do aplicativo oficial Guardian Android.

## Sumário

1. [Visão Geral](#visão-geral)
2. [Modos de Conexão](#modos-de-conexão)
3. [Servidores e Portas](#servidores-e-portas)
4. [Protocolo V2 (ISECNet V2)](#protocolo-v2-isecnet-v2)
5. [Protocolo V1 (ISECNet V1)](#protocolo-v1-isecnet-v1)
6. [Comandos](#comandos)
7. [Códigos de Resposta](#códigos-de-resposta)
8. [Parsing de Status](#parsing-de-status)
9. [Modelos Suportados](#modelos-suportados)
10. [Checksums](#checksums)

---

## Visão Geral

O ISECNet é um protocolo binário proprietário da Intelbras usado para comunicação com centrais de alarme. Existem duas versões principais:

- **ISECNet V2**: Usado por modelos mais novos (AMT_8000, AMT_9000, Eletrificadores)
- **ISECNet V1**: Usado por modelos mais antigos (AMT_2018_E_SMART, AMT_4010, etc.)

### Arquitetura

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Cliente   │────>│  Servidor Relay  │────>│ Central Alarme  │
│  (API/App)  │<────│    (Cloud)       │<────│   (Intelbras)   │
└─────────────┘     └──────────────────┘     └─────────────────┘
                            │
                    Cloud V2: amt8000.intelbras.com.br:9009
                    Cloud V1: amt.intelbras.com.br:9015
```

---

## Modos de Conexão

### 1. Cloud V2 (ISECNet V2)
- **Servidor**: `amt8000.intelbras.com.br`
- **Porta**: 9009 (ou 80 como fallback)
- **Modelos**: AMT_8000, AMT_8000_LITE, AMT_8000_PRO, AMT_9000, ELC_6012_NET
- **Fluxo**: GET_BYTE → APP_CONNECT → AUTH → Comandos

### 2. Cloud V1 (ISECNet V1)
- **Servidor**: `amt.intelbras.com.br`
- **Porta**: 9015
- **Modelos**: AMT_2018_E_SMART, AMT_4010, AMT_2018_E_EG, AMT_2118_EG, etc.
- **Fluxo**: GET_BYTE → CONNECT → Comandos (senha embutida em cada comando)

### 3. IP Receiver (ISECNet V1)
- **Servidor**: Endereço do receptor IP configurado
- **Porta**: Configurável (normalmente 9009)
- **Fluxo**: GET_BYTE → APP_CONNECT → Comandos V1 (senha embutida)

---

## Servidores e Portas

| Modo | Servidor | Porta | Protocolo |
|------|----------|-------|-----------|
| Cloud V2 | amt8000.intelbras.com.br | 9009, 80 | ISECNet V2 |
| Cloud V1 | amt.intelbras.com.br | 9015 | ISECNet V1 |
| IP Receiver | Configurável | 9009 | ISECNet V1 |

---

## Protocolo V2 (ISECNet V2)

### Estrutura de Pacote V2

```
[destination:2][source:2][size:2][command:2][payload:N][checksum:1]
```

| Campo | Tamanho | Descrição |
|-------|---------|-----------|
| destination | 2 bytes | Sempre `0x00 0x00` |
| source | 2 bytes | Source ID (obtido na conexão) |
| size | 2 bytes | Tamanho de command + payload (big-endian) |
| command | 2 bytes | Código do comando (big-endian) |
| payload | N bytes | Dados do comando |
| checksum | 1 byte | XOR de todos os bytes ^ 0xFF |

### Comandos V2

| Comando | Código (hex) | Código (dec) | Descrição |
|---------|--------------|--------------|-----------|
| CONNECT | 0x30F6 | 12534 | Conexão inicial com servidor |
| APP_CONNECT | 0xFFF1 | 65521 | Conexão do app com central |
| AUTHORIZE | 0xF0F0 | 61680 | Autenticação com senha |
| KEEP_ALIVE | 0xF0F7 | 61687 | Keep-alive |
| DISCONNECT | 0xF0F1 | 61681 | Desconexão |
| SYSTEM_ARM_DISARM | 0x401E | 16414 | Armar/Desarmar |
| ALARM_PANEL_STATUS | 0x0B4A | 2890 | Obter status |
| PANIC_ALARM | 0x401A | 16410 | Pânico |
| TURN_OFF_SIREN | 0x4019 | 16409 | Desligar sirene |
| BYPASS_ZONE | 0x401F | 16415 | Bypass de zona |
| GET_MAC | 0x3FAA | 16298 | Obter MAC |
| PGM_ON_OFF | 0x45AF | 17839 | Controle PGM |

### Códigos de Resposta V2

| Código | Hex | Descrição |
|--------|-----|-----------|
| ACK | 0xF0FE (61694) | Comando aceito |
| NACK | 0xF0FD (61693) | Comando rejeitado |

### Fluxo de Conexão V2

```
1. Cliente → Servidor: CONNECT packet
   Resposta: byte_value para XOR encryption

2. Cliente → Servidor: APP_CONNECT (nome do alarme, ex: "AMT8000-AABBCCDDEEFF")
   Pacote criptografado com byte_value
   Resposta: source_id + connection_result

3. Cliente → Servidor: AUTHORIZE (senha 6 dígitos)
   Resposta: auth_result (0=aceito, 1=senha inválida, 2=bloqueado, 3=sem permissão)

4. Cliente → Servidor: Comandos (status, arm, disarm, etc.)
```

### Operações de Armar/Desarmar V2

| Operação | Código | Descrição |
|----------|--------|-----------|
| SYSTEM_DISARM | 0 | Desarmar |
| SYSTEM_ARM | 1 | Armar Total (Away) |
| ARM_STAY | 2 | Armar Parcial (Stay) |
| FORCE_ARM | 3 | Armar forçado |

Payload: `[partition_index][operation]`
- `partition_index`: 0xFF = todas, ou índice+1
- `operation`: código da operação

---

## Protocolo V1 (ISECNet V1)

### Estrutura de Pacote V1

```
[size:1][command:1][0x21][password:N][command_data:N][0x21][checksum:1]
```

| Campo | Tamanho | Descrição |
|-------|---------|-----------|
| size | 1 byte | Tamanho do payload (excluindo size e checksum) |
| command | 1 byte | Código do comando |
| 0x21 | 1 byte | Delimitador ('!') |
| password | N bytes | Senha como ASCII |
| command_data | N bytes | Dados específicos do comando |
| 0x21 | 1 byte | Delimitador final ('!') |
| checksum | 1 byte | XOR de todos os bytes ^ 0xFF |

### Comandos V1

| Comando | Código (hex) | Código (dec) | ASCII | Descrição |
|---------|--------------|--------------|-------|-----------|
| ISEC_PROGRAM | 0xE9 | 233 | - | Wrapper para comandos |
| GET_PARTIAL_STATUS | 0x5A | 90 | 'Z' | Status parcial (46 bytes) |
| GET_COMPLETE_STATUS | 0x53 | 83 | 'S' | Status completo (96 bytes) |
| GET_COMPLETE_INFO | 0x49 | 73 | 'I' | Informações completas |
| ACTIVATE_CENTRAL | 0x41 | 65 | 'A' | Armar |
| DEACTIVATE_CENTRAL | 0x44 | 68 | 'D' | Desarmar |
| PANIC | 0x50 | 80 | 'P' | Pânico |
| SIREN_OFF | 0x4F | 79 | 'O' | Desligar sirene |
| PGM | 0x47 | 71 | 'G' | Controle PGM |

### Comandos do Servidor V1

| Comando | Código (hex) | Descrição |
|---------|--------------|-----------|
| GET_BYTE | 0xFB (251) | Obter byte de criptografia |
| CONNECT | 0xE5 (229) | Conectar à central |
| TOKEN | 0xE6 (230) | Token de autenticação |
| IP_RECEIVER_GET_BYTE | 0xE0 (224) | GET_BYTE para IP Receiver |
| IP_RECEIVER_CONNECT | 0xE4 (228) | CONNECT para IP Receiver |

### Fluxo de Conexão V1 Cloud

```
1. Cliente → amt.intelbras.com.br:9015
   Pacote: [0x01][0xFB][checksum]
   Resposta: [status][byte_value][checksum]

2. Cliente → Servidor: CONNECT com client_id + MAC
   Pacote: [18][0xE5][5(GUARDIAN)][client_id:8][mac:6][0][connection_type][checksum]
   XOR com byte_value

   Resposta:
   - 254 (0xFE): SUCCESS
   - 69 (0x45): SUCCESS_ETHERNET
   - 71 (0x47): SUCCESS_GPRS
   - 230 (0xE6): SUCCESS com firmware GPRS (response[1:4] = versão)
   - 228 (0xE4): Central não conectada
   - 232 (0xE8): Central ocupada

3. Comandos V1 com senha embutida
   Exemplo status: [size][0xE9][0x21][senha][0x5A][0x21][checksum]
```

### Partições V1

Para comandos de armar/desarmar com partição:

| Partição | Código |
|----------|--------|
| A | 0x41 ('A') |
| B | 0x42 ('B') |
| C | 0x43 ('C') |
| D | 0x44 ('D') |

Exemplo armar partição A em modo stay:
```
[size][0xE9][0x21][senha][0x41][0x41][0x50][0x21][checksum]
                         ^     ^     ^
                         |     |     +-- 'P' = Stay mode
                         |     +-------- 'A' = Partition A
                         +-------------- 'A' = Activate command
```

---

## Códigos de Resposta

### ISECNetServerResponse (Conexão V1)

| Código | Hex | Nome | Descrição |
|--------|-----|------|-----------|
| 254 | 0xFE | SUCCESS | Conexão bem sucedida |
| 69 | 0x45 | SUCCESS_ETHERNET | Sucesso via Ethernet |
| 71 | 0x47 | SUCCESS_GPRS | Sucesso via GPRS |
| 230 | 0xE6 | DIFFERENT_CHECKSUM | **Sucesso** com versão GPRS |
| 228 | 0xE4 | CENTRAL_NOT_CONNECTED | Central offline |
| 232 | 0xE8 | CONNECTED_TO_OTHER_DEVICE | Central ocupada |
| 0 | 0x00 | UNKNOWN_ERROR | Erro desconhecido |

> **Importante**: O código 230 (DIFFERENT_CHECKSUM) indica SUCESSO, não erro. A resposta contém a versão do firmware GPRS nos bytes 1-3.

### ISECNetResponse (Comandos V1)

| Código | Hex | Nome | Descrição |
|--------|-----|------|-----------|
| 254 | 0xFE | SUCCESS | Comando executado |
| 224 | 0xE0 | INVALID_PACKAGE | Pacote inválido |
| 225 | 0xE1 | INCORRECT_PASSWORD | Senha incorreta |
| 226 | 0xE2 | INVALID_COMMAND | Comando inválido |
| 227 | 0xE3 | CENTRAL_DOES_NOT_HAVE_PARTITIONS | Central sem partições |
| 228 | 0xE4 | OPEN_ZONES | Zonas abertas |
| 229 | 0xE5 | COMMAND_DEPRECATED | Comando obsoleto |
| 230 | 0xE6 | BYPASS_DENIED | Bypass negado |
| 231 | 0xE7 | DEACTIVATION_DENIED | Desativação negada |
| 232 | 0xE8 | BYPASS_CENTRAL_ACTIVATED | Bypass com central armada |
| 255 | 0xFF | INVALID_MODEL | Modelo inválido |
| 0 | 0x00 | UNKNOWN_ERROR | Erro desconhecido |

### AuthResponse (Autenticação V2)

| Código | Nome | Descrição |
|--------|------|-----------|
| 0 | ACCEPTED | Autenticação aceita |
| 1 | INVALID_PASSWORD | Senha inválida |
| 2 | BLOCKED_USER | Usuário bloqueado |
| 3 | NO_PERMISSION | Sem permissão |

### AppConnectionResponse

| Código | Nome | Descrição |
|--------|------|-----------|
| 0 | SUCCESS | Conexão estabelecida |
| 1 | NOT_CONNECTED | Central desconectada |
| 2 | CENTRAL_NOT_FOUND | Central não encontrada |
| 3 | CENTRAL_BUSY | Central ocupada |
| 4 | CENTRAL_OFFLINE | Central offline |

---

## Parsing de Status

### Status Parcial V1 (46 bytes)

Resposta de GET_PARTIAL_STATUS (0x5A):

| Índice (data) | Índice (APK) | Descrição |
|---------------|--------------|-----------|
| 0 | 1 | 0xE9 (echo do comando) |
| 1 | 2 | Código de resposta |
| 2-7 | 3-8 | Status das zonas (48 zonas, 1 bit cada) |
| 19 | 20 | Código do modelo |
| 20 | 21 | Versão do firmware |
| 21 | 22 | Partições habilitadas (0=não, 1=sim) |
| 22 | 23 | Status das partições (1 bit por partição) |
| 31 | 32 | Nível da bateria |
| 38 | 39 | Status da sirene/PGM |

### Status das Partições

O byte de status das partições (data[22]) contém 1 bit por partição:

```
bit 0 = Partição A armada
bit 1 = Partição B armada
bit 2 = Partição C armada
bit 3 = Partição D armada
...
```

Exemplo:
- `0x01` (0b00000001) = Apenas A armada
- `0x03` (0b00000011) = A e B armadas
- `0x05` (0b00000101) = A e C armadas

> **Nota**: O status parcial (46 bytes) não contém informação de modo STAY/AWAY. Esta informação está apenas no status completo (96 bytes) no byte 94.

### Status Completo V1 (96 bytes)

O status completo inclui informações adicionais:

| Índice | Descrição |
|--------|-----------|
| 94 | Modo Stay (1 bit por partição) |

### Eletrificador (ELC_6012)

Para eletrificadores, os bytes têm significado diferente:

| Índice (data) | Descrição |
|---------------|-----------|
| 21 | Estado do choque (bit 0=ligado, bit 2=disparado) |
| 22 | Estado do alarme (bit 0=armado, bit 1=stay, bit 2=disparado) |
| 38 | Byte de pânico |

---

## Modelos Suportados

| Código | Hex | Modelo | Protocolo | Partições |
|--------|-----|--------|-----------|-----------|
| 1 | 0x01 | AMT_8000 | V2 | 16 |
| 2 | 0x02 | AMT_8000_LITE | V2 | 16 |
| 3 | 0x03 | AMT_8000_PRO | V2 | 16 |
| 30 | 0x1E | AMT_2018_E_EG | V1 | 2 |
| 36 | 0x24 | ANM_24_NET | V1 | 0 |
| 37 | 0x25 | ANM_24_NET_G2 | V1 | 0 |
| 46 | 0x2E | AMT_2118_EG | V1 | 2 |
| 49 | 0x31 | AMT_2016_E3G | V1 | 2 |
| 50 | 0x32 | AMT_2018_E3G | V1 | 2 |
| 52 | 0x34 | AMT_2018_E_SMART | V1 | 2 |
| 53 | 0x35 | ELC_6012_NET | V2 | 0 |
| 54 | 0x36 | AMT_1000_SMART | V1 | 0 |
| 57 | 0x39 | ELC_6012_IND | V2 | 0 |
| 65 | 0x41 | AMT_4010 | V1 | 4 |
| 97 | 0x61 | AMT_1016_NET | V1 | 2 |
| 144 | 0x90 | AMT_9000 | V2 | 8 |

### Seleção de Protocolo

```
Se modelo in [AMT_8000, AMT_8000_LITE, AMT_8000_PRO, AMT_9000, ELC_6012_NET]:
    usar V2 (amt8000.intelbras.com.br:9009)
Senão:
    usar V1 (amt.intelbras.com.br:9015)
```

---

## Checksums

### Checksum XOR com inversão (V1 e V2)

```python
def checksum_xor_inverted(data: List[int]) -> int:
    """Usado em pacotes V2 e V1."""
    result = 0
    for byte in data:
        result ^= byte
    return result ^ 0xFF
```

### Checksum XOR puro (V1 interno)

```python
def checksum_xor(data: List[int]) -> int:
    """Usado internamente em alguns comandos V1."""
    result = 0
    for byte in data:
        result ^= byte
    return result & 0xFF
```

### Checksum SUM (IP Receiver handshake)

```python
def checksum_sum(data: List[int]) -> int:
    """Usado em handshake do IP Receiver."""
    result = 0
    for byte in data:
        result += byte
    return result & 0xFF
```

---

## Exemplos de Pacotes

### V1 GET_BYTE
```
Envio: 01 FB 05
       ^  ^  ^
       |  |  +-- checksum (0x01 ^ 0xFB ^ 0xFF = 0x05)
       |  +-- GET_BYTE command
       +-- size
```

### V1 CONNECT (antes de XOR)
```
12 E5 05 A1 B2 C3 D4 E5 F6 00 01 AA BB CC DD EE FF 00 45 XX
^  ^  ^  ^                    ^                       ^  ^
|  |  |  |                    |                       |  +-- checksum
|  |  |  +-- client_id (8 bytes hex)                  +-- ETHERNET (69)
|  |  +-- GUARDIAN type (5)
|  +-- CONNECT command (229)
+-- size (18)
```

### V1 GET_PARTIAL_STATUS
```
08 E9 21 31 32 33 34 35 36 5A 21 XX
^  ^  ^  ^                 ^  ^  ^
|  |  |  |                 |  |  +-- checksum
|  |  |  |                 |  +-- delimiter
|  |  |  |                 +-- GET_PARTIAL_STATUS (0x5A)
|  |  |  +-- password "123456" as ASCII
|  |  +-- delimiter
|  +-- ISEC_PROGRAM command
+-- size
```

### V2 STATUS Command
```
00 00 XX XX 00 02 0B 4A YY
^     ^     ^     ^     ^
|     |     |     |     +-- checksum
|     |     |     +-- ALARM_PANEL_STATUS (0x0B4A)
|     |     +-- size (2)
|     +-- source_id
+-- destination (always 0x0000)
```

---

## Referências

- APK Guardian Intelbras (versão analisada: app_release)
- Classes analisadas:
  - `ISECNetServerProtocol.kt`
  - `ISECNetProtocol.kt`
  - `ISECNetV2Protocol.java`
  - `ISECNetV2SDK.java`
  - `ISECNetParserHelper.java`
  - `AlarmModel.java`
  - `ISECNetResponse.java`
  - `ISECNetServerResponse.java`
